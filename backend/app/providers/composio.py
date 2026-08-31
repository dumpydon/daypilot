from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.messages import ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient

from backend.app.config import Settings
from backend.app.domain.errors import ProviderUnavailableError
from backend.app.providers.managed_state import ManagedStateStore

try:  # Keep demo/direct mode importable when the optional managed extra is absent.
    from composio import SESSION_PRESET_DIRECT_TOOLS, Composio
except ImportError:  # pragma: no cover - exercised by deployment without the extra.
    Composio = None  # type: ignore[assignment,misc]
    SESSION_PRESET_DIRECT_TOOLS = "direct_tools"


GOOGLE_TOOLKIT = "googlesuper"
X_TOOLKIT = "twitter"
MANAGED_TOOLKITS = (GOOGLE_TOOLKIT, X_TOOLKIT)
# Composio's current Twitter toolkit has no managed app. Keep it in the
# semantic routing allowlist for future/custom configurations, but never let
# its missing auth config participate in Google's normal managed flow.
MANAGED_AUTH_UNAVAILABLE = frozenset({X_TOOLKIT})
logger = logging.getLogger(__name__)

# These are the current Composio action slugs selected for the narrow DayPilot
# contract. They are never exposed to LangGraph or the frontend.
MANAGED_TOOL_SLUGS = {
    GOOGLE_TOOLKIT: (
        "GOOGLESUPER_FETCH_EMAILS",
        "GOOGLESUPER_FETCH_MESSAGE_BY_THREAD_ID",
        "GOOGLESUPER_FETCH_MESSAGE_BY_MESSAGE_ID",
        "GOOGLESUPER_CREATE_EMAIL_DRAFT",
        "GOOGLESUPER_GET_PROFILE",
        "GOOGLESUPER_EVENTS_LIST",
        "GOOGLESUPER_FIND_FREE_SLOTS",
        "GOOGLESUPER_CREATE_EVENT",
        "GOOGLESUPER_EVENTS_GET",
        "GOOGLESUPER_LIST_TASKS",
        "GOOGLESUPER_INSERT_TASK",
        "GOOGLESUPER_PATCH_TASK",
        "GOOGLESUPER_GET_TASK",
    ),
    X_TOOLKIT: (
        "TWITTER_RECENT_SEARCH",
        "TWITTER_POST_LOOKUP_BY_POST_ID",
        "TWITTER_USER_LOOKUP_BY_USERNAME",
        "TWITTER_USER_HOME_TIMELINE_BY_USER_ID",
        "TWITTER_CREATION_OF_A_POST",
        "TWITTER_USER_LOOKUP_ME",
    ),
}


@dataclass(frozen=True)
class ManagedAuthorization:
    toolkit: str
    account_id: str
    redirect_url: str


class ComposioManagedClient:
    """Composio session/auth management plus execution over its hosted MCP URL.

    The SDK is intentionally isolated here. DayPilot's MCP servers call this
    boundary, while LangGraph only sees the existing semantic MCP tools.
    Composio API keys and MCP headers stay in this backend process and are never
    returned as tool data or connection metadata.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        state: ManagedStateStore | None = None,
        composio_factory: Callable[..., Any] | None = None,
        mcp_client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.settings = settings
        self.state = state or ManagedStateStore(settings.database_path)
        self._composio_factory = composio_factory
        self._mcp_client_factory = mcp_client_factory or MultiServerMCPClient
        self._client_instance: Any | None = None
        self._lock = threading.RLock()

    def configured(self) -> bool:
        return bool(self.settings.composio_api_key)

    def authorize(self, toolkit: str, callback_url: str) -> ManagedAuthorization:
        self._validate_toolkit(toolkit)
        if toolkit in MANAGED_AUTH_UNAVAILABLE:
            raise ProviderUnavailableError("Managed connection is currently unavailable for X.")
        session = self._session(toolkit)
        try:
            request = session.authorize(toolkit, callback_url=callback_url)
        except Exception as exc:
            raise self._error("Composio could not create a managed connection link", exc) from exc
        account_id = str(getattr(request, "id", "") or "")
        redirect_url = str(getattr(request, "redirect_url", "") or "")
        if not account_id or not redirect_url:
            raise ProviderUnavailableError(
                "Composio returned an incomplete managed connection link. Try again shortly."
            )
        self.state.set_account(toolkit, account_id, "INITIATED")
        return ManagedAuthorization(toolkit, account_id, redirect_url)

    def complete(
        self,
        toolkit: str,
        account_id: str | None,
        status: str | None,
        error: str | None = None,
    ) -> dict[str, Any]:
        self._validate_toolkit(toolkit)
        if str(status or "").lower() != "success" or not account_id:
            message = "Managed authorization was not completed."
            if error:
                message = f"Managed authorization failed: {_safe_error(error)}"
            existing = self.state.account(toolkit)
            self.state.set_account(
                toolkit,
                str(account_id or (existing or {}).get("account_id") or "pending"),
                "FAILED",
                (existing or {}).get("account_label"),
                message,
            )
            raise ProviderUnavailableError(message)
        client = self._client()
        try:
            account = client.connected_accounts.get(str(account_id))
        except Exception as exc:
            provider_error = self._error("Composio could not verify the managed connection", exc)
            self.state.set_account(
                toolkit,
                str(account_id),
                "FAILED",
                None,
                str(provider_error),
            )
            raise provider_error from exc
        actual_status = str(getattr(account, "status", "") or "").upper()
        label = _account_label(account, toolkit)
        if actual_status != "ACTIVE":
            self.state.set_account(
                toolkit,
                str(account_id),
                actual_status or "FAILED",
                label,
                f"The managed {toolkit} connection is {actual_status.lower() or 'unavailable'}.",
            )
            raise ProviderUnavailableError(
                f"The managed {toolkit} connection is {actual_status.lower() or 'unavailable'}."
            )
        self.state.set_account(toolkit, str(account_id), "ACTIVE", label)
        # A connection link may have been created from an unauthenticated
        # session. Never reuse that session after OAuth changes account state;
        # the next call creates a fresh MCP session bound to this account.
        self.state.delete_session(toolkit)
        return {
            "toolkit": toolkit,
            "account_id": str(account_id),
            "status": actual_status,
            "account_label": label,
        }

    def disconnect(self, toolkit: str) -> None:
        self._validate_toolkit(toolkit)
        record = self.state.account(toolkit)
        if record is None:
            return
        account_id = str(record.get("account_id") or "")
        if account_id and account_id != "pending":
            try:
                try:
                    self._client().connected_accounts.delete(account_id, revoke_on_delete=True)
                except TypeError:  # Compatibility with older SDK fakes/versions.
                    self._client().connected_accounts.delete(account_id)
            except Exception as exc:
                raise self._error("Composio could not disconnect this account", exc) from exc
        self.state.delete_account(toolkit)
        self.state.delete_session(toolkit)

    def account(self, toolkit: str) -> dict[str, str | None] | None:
        self._validate_toolkit(toolkit)
        return self.state.account(toolkit)

    def execute(self, toolkit: str, tool_slug: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._validate_toolkit(toolkit)
        if tool_slug not in MANAGED_TOOL_SLUGS[toolkit]:
            raise ProviderUnavailableError("The requested managed capability is not allowlisted.")
        session = self._session(toolkit)
        mcp = getattr(session, "mcp", None)
        url = str(getattr(mcp, "url", "") or "")
        if not url:
            raise ProviderUnavailableError("Composio did not return a managed MCP endpoint.")
        headers = {
            str(key): str(value)
            for key, value in (getattr(mcp, "headers", None) or {}).items()
            if value is not None
        }
        transport = "sse" if str(getattr(mcp, "type", "")).lower() == "sse" else "http"
        connection = {
            "transport": transport,
            "url": url,
            "headers": headers,
            "timeout": self.settings.provider_http_timeout_seconds,
        }
        try:
            result = _run_async(
                self._invoke_mcp(connection, tool_slug, arguments),
            )
        except ProviderUnavailableError as exc:
            message = str(exc)
            if (
                tool_slug == "GOOGLESUPER_GET_PROFILE"
                and "did not expose the curated capability" in message
            ):
                # Existing sessions may predate the profile read capability.
                # Refresh only this harmless read; never retry a mutation.
                self.state.delete_session(toolkit)
                fresh = self._session(toolkit)
                fresh_mcp = getattr(fresh, "mcp", None)
                fresh_connection = {
                    "transport": "sse"
                    if str(getattr(fresh_mcp, "type", "")).lower() == "sse"
                    else "http",
                    "url": str(getattr(fresh_mcp, "url", "") or ""),
                    "headers": {
                        str(key): str(value)
                        for key, value in (getattr(fresh_mcp, "headers", None) or {}).items()
                        if value is not None
                    },
                    "timeout": self.settings.provider_http_timeout_seconds,
                }
                result = _run_async(
                    self._invoke_mcp(fresh_connection, tool_slug, arguments),
                )
                return _as_dict(result)
            if _looks_like_reauth_error(message):
                account = self.state.account(toolkit)
                if account:
                    self.state.set_account(
                        toolkit,
                        str(account.get("account_id") or "pending"),
                        "EXPIRED",
                        account.get("account_label"),
                        _safe_error(message),
                    )
            raise
        except Exception as exc:
            raise self._error("Composio managed capability failed", exc) from exc
        if isinstance(result, dict) and result.get("error"):
            raise ProviderUnavailableError(_safe_error(str(result["error"])))
        return _as_dict(result)

    async def _invoke_mcp(
        self,
        connection: dict[str, Any],
        tool_slug: str,
        arguments: dict[str, Any],
    ) -> Any:
        client = self._mcp_client_factory(
            {"composio": connection},
            handle_tool_errors=False,
        )
        tools = await client.get_tools(server_name="composio")
        target = next((tool for tool in tools if tool.name == tool_slug), None)
        if target is None:
            raise ProviderUnavailableError(
                f"Composio did not expose the curated capability {tool_slug}."
            )
        return _unwrap_result(await target.ainvoke(arguments))

    def _client(self) -> Any:
        if self._client_instance is not None:
            return self._client_instance
        if not self.settings.composio_api_key:
            raise ProviderUnavailableError(
                "Managed connections require COMPOSIO_API_KEY on the backend."
            )
        if Composio is None and self._composio_factory is None:
            raise ProviderUnavailableError(
                "Managed connections are unavailable because the Composio SDK is not installed."
            )
        factory = self._composio_factory or Composio
        try:
            kwargs: dict[str, Any] = {
                "api_key": self.settings.composio_api_key,
                "allow_tracking": False,
                "toolkit_versions": {toolkit: "latest" for toolkit in MANAGED_TOOLKITS},
            }
            if self.settings.composio_base_url:
                kwargs["base_url"] = self.settings.composio_base_url
            self._client_instance = factory(**kwargs)
        except Exception as exc:
            raise self._error("Composio could not be initialized", exc) from exc
        return self._client_instance

    def _session(self, toolkit: str) -> Any:
        self._validate_toolkit(toolkit)
        client = self._client()
        existing = self.state.session(toolkit)
        if existing:
            try:
                return client.sessions.use(existing["session_id"], mcp=True)
            except Exception:
                # A deleted/expired Composio session is safe to replace; no
                # provider fallback is attempted.
                pass
        user_id = self.state.ensure_user_id()
        active_account = self.state.account(toolkit)
        session_options: dict[str, Any] = {
            "user_id": user_id,
            "toolkits": [toolkit],
            "tools": {toolkit: {"enable": list(MANAGED_TOOL_SLUGS[toolkit])}},
            "session_preset": SESSION_PRESET_DIRECT_TOOLS,
            "manage_connections": False,
            "mcp": True,
        }
        if (
            active_account
            and str(active_account.get("status") or "").upper() == "ACTIVE"
            and active_account.get("account_id")
            and active_account.get("account_id") != "pending"
        ):
            # manage_connections=False disables Composio's in-session auth
            # helper, so pin the already-authorized account explicitly.
            session_options["connected_accounts"] = {toolkit: [str(active_account["account_id"])]}
        try:
            session = client.sessions.create(**session_options)
        except Exception as exc:
            raise self._error("Composio could not create a managed MCP session", exc) from exc
        session_id = str(getattr(session, "session_id", "") or "")
        if not session_id:
            raise ProviderUnavailableError("Composio returned a session without an ID.")
        self.state.set_session(toolkit, session_id, user_id)
        return session

    @staticmethod
    def _validate_toolkit(toolkit: str) -> None:
        if toolkit not in MANAGED_TOOLKITS:
            raise ProviderUnavailableError("The requested managed toolkit is not supported.")

    def _error(self, prefix: str, exc: Exception) -> ProviderUnavailableError:
        message = _safe_error(str(exc))
        if self.settings.composio_api_key:
            message = message.replace(self.settings.composio_api_key, "[redacted]")
        logger.warning("%s: %s", prefix, message)
        return ProviderUnavailableError(f"{prefix}. Try again shortly.")


class ManagedGoogleWorkspaceService:
    def __init__(self, settings: Settings, client: ComposioManagedClient | None = None) -> None:
        self.settings = settings
        self.client = client or ComposioManagedClient(settings)

    def search_mail(self, query: str, limit: int = 10) -> dict[str, Any]:
        limit = max(1, min(limit, 25))
        payload = self.client.execute(
            GOOGLE_TOOLKIT,
            "GOOGLESUPER_FETCH_EMAILS",
            {
                "user_id": "me",
                "query": query,
                "max_results": limit,
                "include_payload": False,
                "verbose": False,
            },
        )
        messages = _items(payload, "messages")[:limit]
        threads: list[dict[str, Any]] = []
        seen: set[str] = set()
        for message in messages:
            message_id = _first(message, "messageId", "message_id", "id")
            thread_id = _first(message, "threadId", "thread_id") or message_id
            if not thread_id or thread_id in seen:
                continue
            seen.add(thread_id)
            threads.append(
                {
                    "thread_id": thread_id,
                    "subject": _first(message, "subject")
                    or _header(message, "Subject")
                    or "(no subject)",
                    "participants": _first(message, "sender", "from")
                    or _header(message, "From")
                    or "Unknown sender",
                    "updated_at": _first(message, "messageTimestamp", "internalDate", "date")
                    or _header(message, "Date"),
                    "message_count": 1,
                    "snippet": _first(message, "preview", "snippet") or "",
                    "source": "managed",
                    "provider": "Google Workspace",
                    "connection_mode": "managed",
                    "real": True,
                    "provider_resource_id": message_id,
                }
            )
        return _envelope(
            {"query": query, "threads": threads, "count": len(threads)},
            "Gmail",
        )

    def get_thread(self, thread_id: str) -> dict[str, Any]:
        payload = self.client.execute(
            GOOGLE_TOOLKIT,
            "GOOGLESUPER_FETCH_MESSAGE_BY_THREAD_ID",
            {"user_id": "me", "thread_id": thread_id, "format": "full"},
        )
        messages = [_normalise_managed_message(item) for item in _items(payload, "messages")]
        messages.sort(key=_message_sort_key)
        subject = next(
            (message["subject"] for message in messages if message["subject"]), "(no subject)"
        )
        return _envelope(
            {"id": thread_id, "thread_id": thread_id, "subject": subject, "messages": messages},
            "Gmail",
        )

    def get_message(self, message_id: str) -> dict[str, Any]:
        payload = self.client.execute(
            GOOGLE_TOOLKIT,
            "GOOGLESUPER_FETCH_MESSAGE_BY_MESSAGE_ID",
            {"user_id": "me", "message_id": message_id, "format": "full"},
        )
        message = payload.get("message") if isinstance(payload.get("message"), dict) else payload
        return _envelope(
            {"kind": "message", **_normalise_managed_message(message)},
            "Gmail",
        )

    def create_draft(self, recipient: str, subject: str, body: str) -> dict[str, Any]:
        recipient_email = self._resolve_recipient(recipient)
        payload = self.client.execute(
            GOOGLE_TOOLKIT,
            "GOOGLESUPER_CREATE_EMAIL_DRAFT",
            {
                "user_id": "me",
                "recipient_email": recipient_email,
                "subject": subject,
                "body": body,
            },
        )
        draft = _resource_payload(payload, "draft")
        draft_message = draft.get("message") if isinstance(draft.get("message"), dict) else {}
        resource_id = _first(draft, "id", "draftId", "draft_id")
        message_id = _first(draft, "messageId", "message_id") or _first(
            draft_message, "id", "messageId", "message_id"
        )
        return _envelope(
            {
                "id": resource_id,
                "draft_id": resource_id,
                "message_id": message_id,
                "recipient": recipient_email,
                "subject": subject,
                "body": body,
                "status": "created",
                "verification_unavailable": resource_id is None,
                "provider_resource_id": resource_id,
            },
            "Gmail",
        )

    def mailbox_identity(self) -> str:
        payload = self.client.execute(
            GOOGLE_TOOLKIT,
            "GOOGLESUPER_GET_PROFILE",
            {"user_id": "me"},
        )
        email = _first(payload, "emailAddress", "email")
        if not email or "@" not in email:
            raise ProviderUnavailableError(
                "The connected Gmail mailbox identity could not be resolved."
            )
        return email

    def _resolve_recipient(self, recipient: str) -> str:
        value = recipient.strip()
        if value.lower() in {"me", "myself", "self", "connected mailbox owner"}:
            return self.mailbox_identity()
        if "@" not in value:
            raise ProviderUnavailableError(
                "Gmail requires a real recipient email address for a draft."
            )
        return value


class ManagedCalendarService:
    def __init__(self, settings: Settings, client: ComposioManagedClient | None = None) -> None:
        self.settings = settings
        self.client = client or ComposioManagedClient(settings)

    def list_events(self, start: str, end: str) -> dict[str, Any]:
        payload = self.client.execute(
            GOOGLE_TOOLKIT,
            "GOOGLESUPER_EVENTS_LIST",
            {
                "calendarId": "primary",
                "timeMin": start,
                "timeMax": end,
                "singleEvents": True,
                "orderBy": "startTime",
                "maxResults": 100,
                "timeZone": self.settings.daypilot_timezone,
            },
        )
        events = [_normalise_managed_event(item) for item in _items(payload, "items")]
        return _envelope(
            {"start": start, "end": end, "events": events, "count": len(events)}, "Google Calendar"
        )

    def find_free_slots(self, start: str, end: str, duration_minutes: int) -> dict[str, Any]:
        if not 15 <= duration_minutes <= 480:
            raise ValueError("duration_minutes must be between 15 and 480")
        payload = self.client.execute(
            GOOGLE_TOOLKIT,
            "GOOGLESUPER_FIND_FREE_SLOTS",
            {
                "time_min": start,
                "time_max": end,
                "timezone": self.settings.daypilot_timezone,
                "items": ["primary"],
            },
        )
        slots = _normalise_slots(payload)
        required_duration = timedelta(minutes=duration_minutes)
        slots = [
            slot
            for slot in slots
            if _parse_datetime(slot["end"]) - _parse_datetime(slot["start"]) >= required_duration
        ]
        if not slots:
            busy = _busy_intervals(payload)
            slots = _compute_slots(start, end, duration_minutes, busy)
        return _envelope(
            {
                "start": start,
                "end": end,
                "duration_minutes": duration_minutes,
                "slots": slots[:5],
                "count": len(slots[:5]),
            },
            "Google Calendar",
        )

    def create_event(
        self,
        title: str,
        start: str,
        end: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        start_at = _parse_datetime(start)
        end_at = _parse_datetime(end)
        if end_at <= start_at:
            raise ValueError("Event end must be after start")
        local_zone = ZoneInfo(self.settings.daypilot_timezone)
        local_start = start_at.astimezone(local_zone).replace(tzinfo=None)
        local_end = end_at.astimezone(local_zone).replace(tzinfo=None)
        args: dict[str, Any] = {
            "calendar_id": "primary",
            "summary": title,
            "description": description,
            # The current Composio action strips offsets from these fields and
            # interprets them in `timezone`; send the approved wall-clock value
            # with its explicit IANA zone to preserve the intended instant.
            "start_datetime": local_start.isoformat(timespec="seconds"),
            "end_datetime": local_end.isoformat(timespec="seconds"),
            "timezone": self.settings.daypilot_timezone,
            "create_meeting_room": False,
        }
        payload = self.client.execute(GOOGLE_TOOLKIT, "GOOGLESUPER_CREATE_EVENT", args)
        event = _resource_payload(payload, "event")
        event_id = _first(event, "id", "eventId", "event_id")
        return _envelope(
            {
                "id": event_id,
                "title": _first(event, "summary", "title") or title,
                "start_at": start_at.isoformat(),
                "end_at": end_at.isoformat(),
                "description": _first(event, "description") or description,
                "status": "created",
                "verification_unavailable": event_id is None,
                "htmlLink": _first(event, "htmlLink", "html_link"),
                "external_url": _first(event, "htmlLink", "html_link"),
                "provider_resource_id": event_id,
            },
            "Google Calendar",
        )


class ManagedTasksService:
    def __init__(self, settings: Settings, client: ComposioManagedClient | None = None) -> None:
        self.settings = settings
        self.client = client or ComposioManagedClient(settings)

    def _tasklist_id(self) -> str:
        return self.settings.google_task_list_id or "@default"

    def list_tasks(self) -> dict[str, Any]:
        payload = self.client.execute(
            GOOGLE_TOOLKIT,
            "GOOGLESUPER_LIST_TASKS",
            {"tasklistId": self._tasklist_id(), "showCompleted": True, "maxResults": 100},
        )
        tasks = [_normalise_managed_task(item) for item in _items(payload, "items", "tasks")]
        return _envelope({"tasks": tasks, "count": len(tasks)}, "Google Tasks")

    def create_task(
        self, title: str, notes: str | None = None, due_at: str | None = None
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"tasklist_id": self._tasklist_id(), "title": title}
        if notes:
            args["notes"] = notes
        requested_due_date: str | None = None
        requested_due_time: str | None = None
        if due_at:
            parsed_due = _parse_datetime(due_at)
            local_due = parsed_due.astimezone(ZoneInfo(self.settings.daypilot_timezone))
            requested_due_date = local_due.date().isoformat()
            if local_due.time() != time.min:
                requested_due_time = local_due.strftime("%-I:%M %p")
            # Google Tasks stores only YYYY-MM-DD for `due`; midnight UTC keeps
            # the user's local calendar date stable instead of shifting it.
            args["due"] = f"{requested_due_date}T00:00:00Z"
        payload = self.client.execute(GOOGLE_TOOLKIT, "GOOGLESUPER_INSERT_TASK", args)
        task = _resource_payload(payload, "task")
        task_id = _first(task, "id", "task_id")
        return _envelope(
            {
                **_normalise_managed_task(task),
                "status": "created",
                "provider_resource_id": task_id,
                "verification_unavailable": task_id is None,
                "due_date": requested_due_date,
                "requested_due_time": requested_due_time,
                "due_time_supported": False,
            },
            "Google Tasks",
        )

    def create_task_batch(self, tasks: list[dict[str, Any]]) -> dict[str, Any]:
        if not 1 <= len(tasks) <= 20:
            raise ValueError("A task batch must contain between 1 and 20 tasks")
        created: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for task in tasks:
            try:
                created.append(
                    self.create_task(str(task["title"]), task.get("notes"), task.get("due_at"))
                )
            except Exception as exc:
                failures.append({"title": task.get("title"), "error": _safe_error(str(exc))})
        status = "created" if not failures else "partially_created" if created else "failed"
        if status == "created" and any(item.get("verification_unavailable") for item in created):
            status = "created_unverified"
        if status == "failed":
            reason = failures[0].get("error") if failures else "The provider rejected the task."
            raise ProviderUnavailableError(
                f"Google Tasks could not create the requested task: {reason}"
            )
        return _envelope(
            {"tasks": created, "failed": failures, "count": len(created), "status": status},
            "Google Tasks",
        )

    def complete_task(self, task_id: str) -> dict[str, Any]:
        payload = self.client.execute(
            GOOGLE_TOOLKIT,
            "GOOGLESUPER_PATCH_TASK",
            {"tasklist_id": self._tasklist_id(), "task_id": task_id, "status": "completed"},
        )
        task = _resource_payload(payload, "task")
        return _envelope(
            {**_normalise_managed_task(task), "changed": True, "status": "completed"},
            "Google Tasks",
        )


class ManagedXService:
    def __init__(self, settings: Settings, client: ComposioManagedClient | None = None) -> None:
        self.settings = settings
        self.client = client or ComposioManagedClient(settings)

    def search_posts(self, query: str, limit: int = 10) -> dict[str, Any]:
        payload = self.client.execute(
            X_TOOLKIT,
            "TWITTER_RECENT_SEARCH",
            {"query": query or "-is:retweet", "max_results": max(10, min(limit, 25))},
        )
        posts = [_normalise_managed_post(item) for item in _items(payload, "data", "posts")[:limit]]
        return _envelope({"query": query, "posts": posts, "count": len(posts)}, "X")

    def get_post(self, post_id: str) -> dict[str, Any]:
        if post_id.startswith("x-draft-"):
            return _managed_local_draft(self.settings, post_id)
        payload = self.client.execute(X_TOOLKIT, "TWITTER_POST_LOOKUP_BY_POST_ID", {"id": post_id})
        post = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        return _envelope(_normalise_managed_post(post), "X")

    def get_user_posts(self, username: str, limit: int = 10) -> dict[str, Any]:
        normalized = username.lstrip("@")
        user_payload = self.client.execute(
            X_TOOLKIT,
            "TWITTER_USER_LOOKUP_BY_USERNAME",
            {"username": normalized},
        )
        user = user_payload.get("data") if isinstance(user_payload.get("data"), dict) else {}
        if not user:
            return _envelope({"username": normalized, "posts": [], "count": 0}, "X")
        payload = self.client.execute(
            X_TOOLKIT,
            "TWITTER_USER_HOME_TIMELINE_BY_USER_ID",
            {"id": user.get("id"), "max_results": max(5, min(limit, 25))},
        )
        posts = [
            _normalise_managed_post(item, user) for item in _items(payload, "data", "posts")[:limit]
        ]
        return _envelope({"username": normalized, "posts": posts, "count": len(posts)}, "X")

    def create_post_draft(self, text: str) -> dict[str, Any]:
        account = self.client.account(X_TOOLKIT)
        if not account or str(account.get("status")) != "ACTIVE":
            raise ProviderUnavailableError("X is not connected through Composio. Connect X first.")
        import sqlite3
        from time import gmtime, strftime
        from uuid import uuid4

        ensure_managed_schema(self.settings.database_path)
        post_id = f"x-draft-{uuid4().hex[:12]}"
        account_label = str(account.get("account_label") or "managed X account")
        now = strftime("%Y-%m-%dT%H:%M:%SZ", gmtime())
        with sqlite3.connect(self.settings.database_path) as connection:
            connection.execute(
                "INSERT INTO x_posts("
                "id, username, display_name, text, created_at, published_at, status, source) "
                "VALUES (?, ?, ?, ?, ?, NULL, 'draft', 'daypilot_managed')",
                (post_id, account_label.lstrip("@"), account_label, text, now),
            )
            connection.commit()
        return _envelope(
            {
                "id": post_id,
                "text": text,
                "status": "draft",
                "source": "daypilot",
                "provider": "DayPilot X draft",
                "connection_mode": "managed",
                "real": False,
                "account": account_label,
            },
            "DayPilot X draft",
        )

    def publish_post(self, text: str, draft_id: str | None = None) -> dict[str, Any]:
        if draft_id:
            draft = _managed_local_draft(self.settings, draft_id)
            text = str(draft.get("text", text))
        payload = self.client.execute(X_TOOLKIT, "TWITTER_CREATION_OF_A_POST", {"text": text})
        post = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        post_id = _first(post, "id", "post_id")
        if not post_id:
            raise ProviderUnavailableError("Composio published an X post without returning its ID.")
        if draft_id:
            import sqlite3
            from time import gmtime, strftime

            with sqlite3.connect(self.settings.database_path) as connection:
                connection.execute(
                    "UPDATE x_posts SET status = 'published', published_at = ? WHERE id = ?",
                    (strftime("%Y-%m-%dT%H:%M:%SZ", gmtime()), draft_id),
                )
                connection.commit()
        return _envelope(
            {
                "id": post_id,
                "text": text,
                "status": "published",
                "provider_resource_id": post_id,
                "external_url": f"https://x.com/i/web/status/{post_id}",
            },
            "X",
        )


def ensure_managed_schema(database_path) -> None:
    from mcp_servers.common.database import ensure_demo_database_schema

    ensure_demo_database_schema(database_path)


def _run_async(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    result: list[Any] = []
    error: list[BaseException] = []

    def runner() -> None:
        try:
            result.append(asyncio.run(coro))
        except BaseException as exc:  # Re-raise in the caller thread.
            error.append(exc)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error[0]
    return result[0] if result else None


def _unwrap_result(value: Any) -> Any:
    if isinstance(value, ToolMessage):
        artifact = value.artifact
        if isinstance(artifact, dict) and "structured_content" in artifact:
            value = artifact["structured_content"]
        else:
            value = value.content
    if isinstance(value, list):
        # Streamable HTTP MCP responses commonly arrive as content blocks, with
        # the provider JSON encoded inside a text block.
        for block in value:
            if isinstance(block, dict) and block.get("type") == "text":
                return _unwrap_result(block.get("text", ""))
            if getattr(block, "type", None) == "text":
                return _unwrap_result(getattr(block, "text", ""))
        return value
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {"text": value}
    if isinstance(value, dict):
        if set(value) == {"value"}:
            return _unwrap_result(value["value"])
        if isinstance(value.get("results"), list) and value["results"]:
            first = value["results"][0]
            if isinstance(first, dict):
                nested = first.get("response") or first
                if isinstance(nested, dict):
                    return _unwrap_result(nested)
        if value.get("successful") is False or value.get("error"):
            return value
        data = value.get("data")
        if isinstance(data, dict) and (
            "successful" in value
            or "error" in value
            or set(value).issubset({"data", "successful", "error"})
        ):
            return _unwrap_result(data)
    return value


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {"value": value}


def _resource_payload(payload: dict[str, Any], key: str) -> dict[str, Any]:
    candidates: list[Any] = [payload]
    for container_key in ("data", "response_data", "result"):
        if isinstance(payload.get(container_key), dict):
            candidates.append(payload[container_key])
    for candidate in candidates:
        if isinstance(candidate, dict) and isinstance(candidate.get(key), dict):
            return candidate[key]
    for candidate in candidates:
        if isinstance(candidate, dict) and any(
            candidate.get(name) for name in ("id", "eventId", "event_id")
        ):
            return candidate
    return {}


def _envelope(payload: dict[str, Any], provider: str) -> dict[str, Any]:
    return {
        **payload,
        "provider": payload.get("provider") or provider,
        "source": payload.get("source") or "managed",
        "connection_mode": "managed",
        "real": payload.get("real", True),
    }


def _items(payload: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _first(value: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        item = value.get(key)
        if item is not None and str(item).strip():
            return str(item)
    return None


def _header(value: dict[str, Any], name: str) -> str | None:
    headers = value.get("headers") or value.get("payload", {}).get("headers", [])
    if isinstance(headers, dict):
        return str(headers.get(name) or headers.get(name.lower()) or "") or None
    for header in headers if isinstance(headers, list) else []:
        if str(header.get("name", "")).lower() == name.lower():
            return str(header.get("value") or "") or None
    return None


def _normalise_managed_message(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _first(message, "messageId", "message_id", "id"),
        "message_id": _first(message, "messageId", "message_id", "id"),
        "thread_id": _first(message, "threadId", "thread_id"),
        "sender": _first(message, "sender", "from") or _header(message, "From") or "",
        "recipients": _first(message, "recipients", "to") or _header(message, "To") or "",
        "subject": _first(message, "subject") or _header(message, "Subject") or "",
        "sent_at": _first(message, "messageTimestamp", "internalDate", "date")
        or _header(message, "Date"),
        "body": _message_body(message),
        "snippet": _first(message, "preview", "snippet") or "",
    }


def _message_body(message: dict[str, Any]) -> str:
    for key in ("messageText", "body", "text", "preview", "snippet"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:12_000]
    payload = message.get("payload")
    if isinstance(payload, dict):
        return _message_body(payload)
    return ""


def _message_sort_key(message: dict[str, Any]) -> str:
    return str(message.get("sent_at") or "")


def _normalise_managed_event(event: dict[str, Any]) -> dict[str, Any]:
    start = event.get("start") if isinstance(event.get("start"), dict) else {}
    end = event.get("end") if isinstance(event.get("end"), dict) else {}
    start_at = (
        _first(event, "start_datetime", "start_at") or _first(start, "dateTime", "date") or ""
    )
    end_at = _first(event, "end_datetime", "end_at") or _first(end, "dateTime", "date") or start_at
    return {
        "id": _first(event, "id", "eventId", "event_id"),
        "title": _first(event, "summary", "title") or "(untitled event)",
        "start_at": start_at,
        "end_at": end_at,
        "description": _first(event, "description"),
        "location": _first(event, "location"),
        "htmlLink": _first(event, "htmlLink", "html_link"),
    }


def _normalise_slots(payload: dict[str, Any]) -> list[dict[str, str]]:
    raw = payload.get("slots") or payload.get("free_slots") or payload.get("freeSlots") or []
    slots: list[dict[str, str]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        start = _first(item, "start", "start_at", "start_datetime")
        end = _first(item, "end", "end_at", "end_datetime")
        if start and end:
            slots.append({"start": start, "end": end})
    return slots


def _busy_intervals(payload: dict[str, Any]) -> list[tuple[datetime, datetime]]:
    raw = payload.get("busy") or payload.get("busy_periods") or []
    if not raw and isinstance(payload.get("calendars"), dict):
        primary = payload["calendars"].get("primary")
        if isinstance(primary, dict):
            raw = primary.get("busy") or []
    intervals: list[tuple[datetime, datetime]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        start = _first(item, "start", "start_at")
        end = _first(item, "end", "end_at")
        if start and end:
            intervals.append((_parse_datetime(start), _parse_datetime(end)))
    return intervals


def _compute_slots(
    start: str,
    end: str,
    duration_minutes: int,
    busy: list[tuple[datetime, datetime]],
) -> list[dict[str, str]]:
    cursor = _parse_datetime(start)
    end_at = _parse_datetime(end)
    duration = timedelta(minutes=duration_minutes)
    slots: list[dict[str, str]] = []
    for busy_start, busy_end in sorted(busy):
        while cursor + duration <= min(busy_start, end_at) and len(slots) < 5:
            slots.append({"start": cursor.isoformat(), "end": (cursor + duration).isoformat()})
            cursor += timedelta(minutes=30)
        cursor = max(cursor, busy_end)
    while cursor + duration <= end_at and len(slots) < 5:
        slots.append({"start": cursor.isoformat(), "end": (cursor + duration).isoformat()})
        cursor += timedelta(minutes=30)
    return slots


def _normalise_managed_task(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _first(task, "id", "task_id"),
        "title": _first(task, "title", "name") or "",
        "notes": _first(task, "notes", "description"),
        "due_at": _first(task, "due", "due_at"),
        "completed": str(task.get("status", "")).lower() == "completed",
        "updated_at": _first(task, "updated", "updated_at"),
    }


def _normalise_managed_post(
    post: dict[str, Any], user: dict[str, Any] | None = None
) -> dict[str, Any]:
    user = user or {}
    return {
        "id": _first(post, "id", "post_id"),
        "username": _first(post, "username") or _first(user, "username"),
        "display_name": _first(post, "name", "display_name") or _first(user, "name"),
        "text": _first(post, "text", "full_text") or "",
        "created_at": _first(post, "created_at"),
        "status": "published",
        "author_id": _first(post, "author_id") or _first(user, "id"),
    }


def _managed_local_draft(settings: Settings, post_id: str) -> dict[str, Any]:
    import sqlite3

    ensure_managed_schema(settings.database_path)
    with sqlite3.connect(settings.database_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM x_posts WHERE id = ?", (post_id,)).fetchone()
    if row is None:
        raise ValueError(f"DayPilot X draft {post_id!r} was not found")
    return _envelope({**dict(row), "source": "daypilot", "real": False}, "DayPilot X draft")


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Provider datetime must include a timezone")
    return parsed


def _account_label(account: Any, toolkit: str) -> str:
    alias = getattr(account, "alias", None)
    if alias:
        return str(alias)
    data = getattr(account, "data", None)
    if isinstance(data, dict):
        for key in ("email", "username", "handle", "name"):
            if data.get(key):
                return str(data[key])
    return "Connected Google account" if toolkit == GOOGLE_TOOLKIT else "Connected X account"


def _safe_error(value: str) -> str:
    redacted = re.sub(r"(?i)(?:bearer\s+)[^\s,;]+", "Bearer [redacted]", value)
    redacted = redacted.replace("\n", " ").replace("\r", " ")
    for secret in ("COMPOSIO_API_KEY",):
        redacted = redacted.replace(secret, "[redacted]")
    return redacted[:500]


def _looks_like_reauth_error(value: str) -> bool:
    lowered = value.lower()
    return any(
        marker in lowered
        for marker in (
            "authorization",
            "authenticated",
            "connected account",
            "revoked",
            "scope",
            "401",
            "403",
        )
    )
