from __future__ import annotations

import sqlite3
import time
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx

from backend.app.config import Settings
from backend.app.domain.errors import ProviderUnavailableError
from backend.app.providers.credentials import EncryptedCredentialStore
from backend.app.providers.models import CredentialRecord
from mcp_servers.common.database import ensure_demo_database_schema


class XTokenManager:
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
        credential = self.store.get("x")
        if credential is None:
            raise ProviderUnavailableError("X is not connected. Connect X in Preferences.")
        if credential.expired:
            credential = self._refresh(credential)
        if credential.last_error:
            raise ProviderUnavailableError(credential.last_error, requires_reauth=True)
        return credential.access_token

    def _refresh(self, credential: CredentialRecord) -> CredentialRecord:
        if not credential.refresh_token or not self.settings.x_client_id:
            self.store.update("x", last_error="X authorization expired. Reconnect X.")
            raise ProviderUnavailableError(
                "X authorization expired. Reconnect X.", requires_reauth=True
            )
        data = {
            "refresh_token": credential.refresh_token,
            "grant_type": "refresh_token",
            "client_id": self.settings.x_client_id,
        }
        try:
            with httpx.Client(
                timeout=self.settings.provider_http_timeout_seconds,
                transport=self.transport,
            ) as client:
                response = client.post("https://api.x.com/2/oauth2/token", data=data)
        except httpx.RequestError as exc:
            raise ProviderUnavailableError(
                "X could not refresh authorization. Try reconnecting.", requires_reauth=True
            ) from exc
        if response.status_code >= 400:
            message = "X authorization expired or was revoked. Reconnect required."
            self.store.update("x", last_error=message)
            raise ProviderUnavailableError(message, requires_reauth=True)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderUnavailableError(
                "X returned an invalid refresh response.", requires_reauth=True
            ) from exc
        token = payload.get("access_token")
        if not token:
            raise ProviderUnavailableError(
                "X did not return a refreshed access token.", requires_reauth=True
            )
        refreshed = CredentialRecord(
            provider="x",
            access_token=token,
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


class XApiClient:
    def __init__(
        self,
        settings: Settings,
        store: EncryptedCredentialStore,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.transport = transport
        self.tokens = XTokenManager(settings, store, transport)

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
                    method, f"https://api.x.com{path}", headers=headers, **kwargs
                )
                if response.status_code == 401:
                    credential = self.store.get("x")
                    if credential and credential.refresh_token:
                        headers["Authorization"] = (
                            f"Bearer {self.tokens._refresh(credential).access_token}"
                        )
                        response = client.request(
                            method, f"https://api.x.com{path}", headers=headers, **kwargs
                        )
        except httpx.TimeoutException as exc:
            raise ProviderUnavailableError("X timed out while serving this capability.") from exc
        except httpx.RequestError as exc:
            raise ProviderUnavailableError("X could not be reached. Try again shortly.") from exc
        if response.status_code >= 400:
            error = _x_http_error(response)
            if error.requires_reauth:
                self.store.update("x", last_error=str(error))
            raise error
        self.store.update("x", last_error=None)
        if not response.content:
            return {}
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderUnavailableError("X returned an invalid response.") from exc
        return payload if isinstance(payload, dict) else {}


class ConnectedXService:
    """Real X reads/publishing plus explicitly local DayPilot draft storage."""

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self.database_path = settings.database_path
        ensure_demo_database_schema(self.database_path)
        self.store = EncryptedCredentialStore(
            settings.credential_path, settings.credential_key_path
        )
        self.client = XApiClient(settings, self.store, transport)

    def search_posts(self, query: str, limit: int = 10) -> dict[str, Any]:
        limit = max(1, min(limit, 25))
        payload = self.client.request(
            "GET",
            "/2/tweets/search/recent",
            params={
                "query": query or "-is:retweet",
                "max_results": max(10, limit),
                "tweet.fields": "created_at,author_id,public_metrics",
                "expansions": "author_id",
                "user.fields": "name,username",
            },
        )
        users = {user["id"]: user for user in payload.get("includes", {}).get("users", [])}
        posts = [_post_payload(item, users) for item in payload.get("data", [])[:limit]]
        return {
            "query": query,
            "posts": posts,
            "count": len(posts),
            "source": "connected",
            "provider": "X",
        }

    def get_post(self, post_id: str) -> dict[str, Any]:
        if post_id.startswith("x-draft-"):
            return self._get_local_post(post_id)
        payload = self.client.request(
            "GET",
            f"/2/tweets/{quote(post_id)}",
            params={"tweet.fields": "created_at,author_id,public_metrics"},
        )
        return {
            **_post_payload(payload.get("data", {}), {}),
            "source": "connected",
            "provider": "X",
        }

    def get_user_posts(self, username: str, limit: int = 10) -> dict[str, Any]:
        username = username.lstrip("@")
        user_payload = self.client.request("GET", f"/2/users/by/username/{quote(username)}")
        user = user_payload.get("data")
        if not user:
            return {
                "username": username,
                "posts": [],
                "count": 0,
                "source": "connected",
                "provider": "X",
            }
        payload = self.client.request(
            "GET",
            f"/2/users/{quote(str(user['id']))}/tweets",
            params={"max_results": max(5, min(limit, 25)), "tweet.fields": "created_at,author_id"},
        )
        posts = [
            _post_payload(item, {str(user["id"]): user}) for item in payload.get("data", [])[:limit]
        ]
        return {
            "username": username,
            "posts": posts,
            "count": len(posts),
            "source": "connected",
            "provider": "X",
        }

    def create_post_draft(self, text: str) -> dict[str, Any]:
        credential = self.store.get("x")
        if credential is None:
            raise ProviderUnavailableError("X is not connected. Connect X before saving a draft.")
        post_id = f"x-draft-{uuid4().hex[:12]}"
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        account = credential.account_label or "connected X account"
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO x_posts(
                    id, username, display_name, text, created_at, published_at, status, source
                ) VALUES (?, ?, ?, ?, ?, NULL, 'draft', 'daypilot_connected')
                """,
                (post_id, account.lstrip("@"), account, text, now),
            )
            connection.commit()
        return {
            "id": post_id,
            "text": text,
            "status": "draft",
            "source": "daypilot",
            "provider": "DayPilot X draft",
            "account": account,
        }

    def publish_post(self, text: str, draft_id: str | None = None) -> dict[str, Any]:
        if draft_id:
            draft = self._get_local_post(draft_id)
            text = draft["text"]
        payload = self.client.request("POST", "/2/tweets", json={"text": text})
        post = payload.get("data") or {}
        post_id = post.get("id")
        if not post_id:
            raise ProviderUnavailableError("X accepted the request without returning a post ID.")
        if draft_id:
            with sqlite3.connect(self.database_path) as connection:
                connection.execute(
                    "UPDATE x_posts SET status = 'published', published_at = ? WHERE id = ?",
                    (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), draft_id),
                )
                connection.commit()
        return {
            "id": post_id,
            "text": text,
            "status": "published",
            "source": "connected",
            "provider": "X",
            "external_url": f"https://x.com/i/web/status/{post_id}",
        }

    def _get_local_post(self, post_id: str) -> dict[str, Any]:
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM x_posts WHERE id = ?", (post_id,)).fetchone()
        if row is None:
            raise ValueError(f"DayPilot X draft {post_id!r} was not found")
        return {**dict(row), "source": "daypilot", "provider": "DayPilot X draft"}


def _post_payload(post: dict[str, Any], users: dict[str, dict[str, Any]]) -> dict[str, Any]:
    author = users.get(str(post.get("author_id")), {})
    username = author.get("username")
    return {
        "id": post.get("id"),
        "username": username,
        "display_name": author.get("name"),
        "text": post.get("text", ""),
        "created_at": post.get("created_at"),
        "status": "published",
        "author_id": post.get("author_id"),
        "source": "connected",
        "provider": "X",
    }


def _x_http_error(response: httpx.Response) -> ProviderUnavailableError:
    if response.status_code == 401:
        return ProviderUnavailableError(
            "X authorization expired or was revoked. Reconnect required.", requires_reauth=True
        )
    if response.status_code == 403:
        return ProviderUnavailableError(
            "X denied this capability. Check the app scopes or API tier.", requires_reauth=True
        )
    if response.status_code == 429:
        return ProviderUnavailableError("X is rate limiting requests. Try again shortly.")
    if response.status_code >= 500:
        return ProviderUnavailableError("X is temporarily unavailable. Try again shortly.")
    return ProviderUnavailableError(f"X rejected the request (HTTP {response.status_code}).")
