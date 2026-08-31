from __future__ import annotations

import base64
import html
import re
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from backend.app.config import Settings
from backend.app.domain.errors import ProviderUnavailableError
from backend.app.providers.credentials import EncryptedCredentialStore
from backend.app.providers.models import CredentialRecord

GOOGLE_PROVIDER_LABELS = {
    "gmail": "Gmail",
    "google_calendar": "Google Calendar",
    "google_tasks": "Google Tasks",
}


class GoogleTokenManager:
    def __init__(
        self,
        settings: Settings,
        store: EncryptedCredentialStore,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.transport = transport

    def access_token(self) -> str:
        credential = self.store.get("google")
        if credential is None:
            raise ProviderUnavailableError(
                "Google Workspace is not connected. Connect Google in Preferences."
            )
        if credential.expired:
            credential = self._refresh(credential)
        if credential.last_error:
            raise ProviderUnavailableError(credential.last_error, requires_reauth=True)
        return credential.access_token

    def _refresh(self, credential: CredentialRecord) -> CredentialRecord:
        if not credential.refresh_token or not self.settings.google_client_id:
            self.store.update(
                "google", last_error="Google authorization expired. Reconnect Google Workspace."
            )
            raise ProviderUnavailableError(
                "Google authorization expired. Reconnect Google Workspace.", requires_reauth=True
            )
        data = {
            "grant_type": "refresh_token",
            "refresh_token": credential.refresh_token,
            "client_id": self.settings.google_client_id,
        }
        if self.settings.google_client_secret:
            data["client_secret"] = self.settings.google_client_secret
        try:
            with httpx.Client(
                timeout=self.settings.provider_http_timeout_seconds,
                transport=self.transport,
            ) as client:
                response = client.post("https://oauth2.googleapis.com/token", data=data)
        except httpx.RequestError as exc:
            raise ProviderUnavailableError(
                "Google could not refresh authorization. Try reconnecting.", requires_reauth=True
            ) from exc
        if response.status_code >= 400:
            message = "Google authorization expired or was revoked. Reconnect required."
            self.store.update(
                "google",
                last_error=message,
            )
            raise ProviderUnavailableError(message, requires_reauth=True)
        payload = _json_response(response, "Google returned an invalid token response.")
        access_token = payload.get("access_token")
        if not access_token:
            raise ProviderUnavailableError(
                "Google did not return a refreshed access token.", requires_reauth=True
            )
        refreshed = CredentialRecord(
            provider="google",
            access_token=access_token,
            refresh_token=payload.get("refresh_token") or credential.refresh_token,
            expires_at=time.time() + float(payload["expires_in"])
            if payload.get("expires_in")
            else None,
            scopes=tuple(payload.get("scope", "").split()) or credential.scopes,
            account_label=credential.account_label,
            metadata=credential.metadata,
            last_error=None,
        )
        self.store.set(refreshed)
        return refreshed


class GoogleApiClient:
    def __init__(
        self,
        settings: Settings,
        store: EncryptedCredentialStore,
        *,
        base_url: str,
        token_manager: GoogleTokenManager | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.base_url = base_url.rstrip("/")
        self.transport = transport
        self.tokens = token_manager or GoogleTokenManager(settings, store, transport)

    def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        token = self.tokens.access_token()
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {token}"
        headers.setdefault("Accept", "application/json")
        try:
            with httpx.Client(
                timeout=self.settings.provider_http_timeout_seconds,
                transport=self.transport,
            ) as client:
                response = client.request(
                    method, f"{self.base_url}/{path.lstrip('/')}", headers=headers, **kwargs
                )
                if response.status_code == 401:
                    credential = self.store.get("google")
                    if credential and credential.refresh_token:
                        token = self.tokens._refresh(credential).access_token
                        headers["Authorization"] = f"Bearer {token}"
                        response = client.request(
                            method, f"{self.base_url}/{path.lstrip('/')}", headers=headers, **kwargs
                        )
        except httpx.TimeoutException as exc:
            raise ProviderUnavailableError(
                "Google timed out while serving this capability."
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderUnavailableError(
                "Google could not be reached. Try again shortly."
            ) from exc
        if response.status_code >= 400:
            error = _provider_http_error("Google", response)
            if error.requires_reauth:
                self.store.update("google", last_error=str(error))
            raise error
        self.store.update("google", last_error=None)
        return (
            _json_response(response, "Google returned an invalid response.")
            if response.content
            else {}
        )


class GmailService:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        store = EncryptedCredentialStore(settings.credential_path, settings.credential_key_path)
        self.client = GoogleApiClient(
            settings,
            store,
            base_url="https://gmail.googleapis.com/gmail/v1/users/me",
            transport=transport,
        )

    def search_mail(self, query: str, limit: int = 10) -> dict[str, Any]:
        limit = max(1, min(limit, 25))
        listing = self.client.request("GET", "messages", params={"q": query, "maxResults": limit})
        threads: list[dict[str, Any]] = []
        seen_threads: set[str] = set()
        for item in listing.get("messages", [])[:limit]:
            message = self.client.request(
                "GET",
                f"messages/{quote(str(item['id']))}",
                params={"format": "metadata", "metadataHeaders": ["From", "To", "Subject", "Date"]},
            )
            headers = _gmail_headers(message)
            thread_id = str(message.get("threadId", item["id"]))
            if thread_id in seen_threads:
                continue
            seen_threads.add(thread_id)
            threads.append(
                {
                    "thread_id": thread_id,
                    "subject": headers.get("Subject", "(no subject)"),
                    "participants": headers.get("From", "Unknown sender"),
                    "updated_at": headers.get("Date"),
                    "message_count": 1,
                    "snippet": message.get("snippet", ""),
                    "source": "connected",
                    "provider": "Gmail",
                }
            )
        return {
            "query": query,
            "threads": threads,
            "count": len(threads),
            "source": "connected",
            "provider": "Gmail",
        }

    def get_thread(self, thread_id: str) -> dict[str, Any]:
        payload = self.client.request(
            "GET", f"threads/{quote(thread_id)}", params={"format": "full"}
        )
        messages = [_normalise_gmail_message(item) for item in payload.get("messages", [])]
        subject = next(
            (message["subject"] for message in messages if message["subject"]), "(no subject)"
        )
        return {
            "id": payload.get("id", thread_id),
            "thread_id": payload.get("id", thread_id),
            "subject": subject,
            "messages": messages,
            "source": "connected",
            "provider": "Gmail",
        }

    def get_message(self, message_id: str) -> dict[str, Any]:
        payload = self.client.request(
            "GET", f"messages/{quote(message_id)}", params={"format": "full"}
        )
        return {
            "kind": "message",
            **_normalise_gmail_message(payload),
            "source": "connected",
            "provider": "Gmail",
        }

    def create_draft(self, recipient: str, subject: str, body: str) -> dict[str, Any]:
        message = EmailMessage()
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")
        payload = self.client.request("POST", "drafts", json={"message": {"raw": raw}})
        message_payload = payload.get("message") or {}
        draft_id = payload.get("id")
        message_id = message_payload.get("id")
        resource_id = draft_id or message_id
        if not resource_id:
            raise ProviderUnavailableError("Gmail created a draft without returning its ID.")
        return {
            "id": resource_id,
            "draft_id": draft_id,
            "message_id": message_id,
            "recipient": recipient,
            "subject": subject,
            "body": body,
            "status": "created",
            "source": "connected",
            "provider": "Gmail",
            "external_url": (
                f"https://mail.google.com/mail/u/0/#drafts/{draft_id}" if draft_id else None
            ),
        }


class GoogleCalendarService:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        store = EncryptedCredentialStore(settings.credential_path, settings.credential_key_path)
        self.settings = settings
        self.timezone = ZoneInfo(settings.daypilot_timezone)
        self.client = GoogleApiClient(
            settings,
            store,
            base_url="https://www.googleapis.com/calendar/v3",
            transport=transport,
        )

    def list_events(self, start: str, end: str) -> dict[str, Any]:
        start_at = _parse_datetime(start)
        end_at = _parse_datetime(end)
        if end_at <= start_at:
            raise ValueError("Calendar range end must be after start")
        payload = self.client.request(
            "GET",
            "calendars/primary/events",
            params={
                "timeMin": start_at.isoformat(),
                "timeMax": end_at.isoformat(),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": 100,
            },
        )
        events = [
            _normalise_calendar_event(item, self.timezone) for item in payload.get("items", [])
        ]
        return {
            "start": start_at.isoformat(),
            "end": end_at.isoformat(),
            "events": events,
            "count": len(events),
            "source": "connected",
            "provider": "Google Calendar",
        }

    def find_free_slots(self, start: str, end: str, duration_minutes: int) -> dict[str, Any]:
        if not 15 <= duration_minutes <= 480:
            raise ValueError("duration_minutes must be between 15 and 480")
        start_at = _round_up(_parse_datetime(start), 15)
        end_at = _parse_datetime(end)
        events = self.list_events(start_at.isoformat(), end_at.isoformat())["events"]
        duration = timedelta(minutes=duration_minutes)
        cursor = start_at
        slots: list[dict[str, str]] = []
        for event in events:
            event_start = _parse_datetime(event["start_at"])
            event_end = _parse_datetime(event["end_at"])
            while cursor + duration <= min(event_start, end_at) and len(slots) < 5:
                slots.append({"start": cursor.isoformat(), "end": (cursor + duration).isoformat()})
                cursor += timedelta(minutes=30)
            cursor = max(cursor, event_end)
        while cursor + duration <= end_at and len(slots) < 5:
            slots.append({"start": cursor.isoformat(), "end": (cursor + duration).isoformat()})
            cursor += timedelta(minutes=30)
        return {
            "start": start_at.isoformat(),
            "end": end_at.isoformat(),
            "duration_minutes": duration_minutes,
            "slots": slots,
            "count": len(slots),
            "source": "connected",
            "provider": "Google Calendar",
        }

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
        payload = self.client.request(
            "POST",
            "calendars/primary/events",
            json={
                "summary": title,
                "description": description,
                "start": {"dateTime": start_at.isoformat(), "timeZone": str(self.timezone)},
                "end": {"dateTime": end_at.isoformat(), "timeZone": str(self.timezone)},
            },
        )
        return {
            "id": payload.get("id"),
            "title": payload.get("summary", title),
            "start_at": start_at.isoformat(),
            "end_at": end_at.isoformat(),
            "description": payload.get("description", description),
            "source": "connected",
            "provider": "Google Calendar",
            "status": "created",
            "htmlLink": payload.get("htmlLink"),
            "external_url": payload.get("htmlLink"),
        }


class GoogleTasksService:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        store = EncryptedCredentialStore(settings.credential_path, settings.credential_key_path)
        self.settings = settings
        self.client = GoogleApiClient(
            settings,
            store,
            base_url="https://tasks.googleapis.com/tasks/v1",
            transport=transport,
        )

    def _list_id(self) -> str:
        configured = self.settings.google_task_list_id
        if configured:
            return configured
        payload = self.client.request("GET", "users/@me/lists", params={"maxResults": 100})
        lists = payload.get("items", [])
        if not lists:
            raise ProviderUnavailableError("Google Tasks has no available task list.")
        return str(lists[0]["id"])

    def list_tasks(self) -> dict[str, Any]:
        payload = self.client.request(
            "GET",
            f"lists/{quote(self._list_id())}/tasks",
            params={"maxResults": 100, "showCompleted": "true"},
        )
        tasks = [_normalise_task(item) for item in payload.get("items", [])]
        return {
            "tasks": tasks,
            "count": len(tasks),
            "source": "connected",
            "provider": "Google Tasks",
        }

    def create_task(
        self, title: str, notes: str | None = None, due_at: str | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"title": title}
        if notes:
            payload["notes"] = notes
        if due_at:
            payload["due"] = _parse_datetime(due_at).isoformat().replace("+00:00", "Z")
        result = self.client.request("POST", f"lists/{quote(self._list_id())}/tasks", json=payload)
        return {
            **_normalise_task(result),
            "status": "created",
            "source": "connected",
            "provider": "Google Tasks",
        }

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
                failures.append({"title": task.get("title"), "error": str(exc)})
        return {
            "tasks": created,
            "failed": failures,
            "count": len(created),
            "status": "created" if not failures else "partially_created" if created else "failed",
            "source": "connected",
            "provider": "Google Tasks",
        }

    def complete_task(self, task_id: str) -> dict[str, Any]:
        result = self.client.request(
            "PATCH",
            f"lists/{quote(self._list_id())}/tasks/{quote(task_id)}",
            json={"status": "completed"},
        )
        return {
            **_normalise_task(result),
            "changed": True,
            "status": "completed",
            "source": "connected",
            "provider": "Google Tasks",
        }


def _json_response(response: httpx.Response, message: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderUnavailableError(message) from exc
    return payload if isinstance(payload, dict) else {}


def _provider_http_error(provider: str, response: httpx.Response) -> ProviderUnavailableError:
    if response.status_code == 401:
        return ProviderUnavailableError(
            f"{provider} authorization expired or was revoked. Reconnect required.",
            requires_reauth=True,
        )
    if response.status_code == 403:
        return ProviderUnavailableError(
            f"{provider} denied this capability. Check the granted scopes.", requires_reauth=True
        )
    if response.status_code == 429:
        return ProviderUnavailableError(f"{provider} is rate limiting requests. Try again shortly.")
    if response.status_code >= 500:
        return ProviderUnavailableError(
            f"{provider} is temporarily unavailable. Try again shortly."
        )
    return ProviderUnavailableError(
        f"{provider} rejected the request (HTTP {response.status_code})."
    )


def _gmail_headers(message: dict[str, Any]) -> dict[str, str]:
    return {
        header.get("name", ""): header.get("value", "")
        for header in message.get("payload", {}).get("headers", [])
    }


def _normalise_gmail_message(message: dict[str, Any]) -> dict[str, Any]:
    headers = _gmail_headers(message)
    return {
        "id": message.get("id"),
        "message_id": message.get("id"),
        "thread_id": message.get("threadId"),
        "sender": headers.get("From", ""),
        "recipients": headers.get("To", ""),
        "subject": headers.get("Subject", ""),
        "sent_at": headers.get("Date"),
        "body": _gmail_body(message.get("payload", {})),
        "snippet": message.get("snippet", ""),
    }


def _gmail_body(payload: dict[str, Any]) -> str:
    parts = payload.get("parts") or []
    if parts:
        preferred = next((part for part in parts if part.get("mimeType") == "text/plain"), parts[0])
        body = _gmail_body(preferred)
        if body:
            return body
    data = (payload.get("body") or {}).get("data")
    if not data:
        return ""
    try:
        decoded = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode(
            "utf-8", errors="replace"
        )
    except (ValueError, UnicodeError):
        return ""
    if payload.get("mimeType") == "text/html":
        decoded = re.sub(r"<[^>]+>", " ", decoded)
        decoded = html.unescape(decoded)
    return re.sub(r"\s+", " ", decoded).strip()[:12_000]


def _normalise_calendar_event(event: dict[str, Any], timezone: ZoneInfo) -> dict[str, Any]:
    start = event.get("start", {})
    end = event.get("end", {})
    if start.get("dateTime"):
        start_at = _parse_datetime(start["dateTime"])
        end_at = _parse_datetime(end.get("dateTime", start["dateTime"]))
    else:
        start_at = datetime.fromisoformat(start["date"]).replace(tzinfo=timezone)
        end_at = datetime.fromisoformat(end.get("date", start["date"])).replace(tzinfo=timezone)
    return {
        "id": event.get("id"),
        "title": event.get("summary") or "(untitled event)",
        "start_at": start_at.isoformat(),
        "end_at": end_at.isoformat(),
        "description": event.get("description"),
        "location": event.get("location"),
        "source": "connected",
        "provider": "Google Calendar",
        "htmlLink": event.get("htmlLink"),
    }


def _normalise_task(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": task.get("id"),
        "title": task.get("title", ""),
        "notes": task.get("notes"),
        "due_at": task.get("due"),
        "completed": task.get("status") == "completed",
        "updated_at": task.get("updated"),
    }


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Provider datetime must include a timezone")
    return parsed


def _round_up(value: datetime, minutes: int) -> datetime:
    remainder = value.minute % minutes
    if remainder == 0 and value.second == 0 and value.microsecond == 0:
        return value
    return (value + timedelta(minutes=minutes - remainder)).replace(second=0, microsecond=0)
