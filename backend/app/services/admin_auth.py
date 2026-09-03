from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from backend.app.config import Settings
from backend.app.persistence.repository import DayPilotRepository

ADMIN_COOKIE_NAME = "daypilot_admin_session"


@dataclass(frozen=True)
class AdminSession:
    token: str
    expires_at: datetime


class AdminAuthService:
    """Small owner gate for the public demo; this is not a user account system."""

    def __init__(self, settings: Settings, repository: DayPilotRepository) -> None:
        self.settings = settings
        self.repository = repository
        self._failures: dict[str, list[float]] = {}

    async def authenticate(self, access_code: str, client_key: str) -> AdminSession | None:
        now = datetime.now(UTC)
        await self.repository.purge_admin_sessions(now.isoformat())
        if self._is_rate_limited(client_key):
            return None
        configured = self.settings.admin_secret or ""
        valid = bool(configured) and hmac.compare_digest(access_code, configured)
        if not valid:
            self._record_failure(client_key)
            return None
        self._failures.pop(client_key, None)
        token = secrets.token_urlsafe(32)
        expires_at = now + timedelta(seconds=self.settings.admin_session_ttl_seconds)
        await self.repository.create_admin_session(
            self._hash(token), now.isoformat(), expires_at.isoformat()
        )
        return AdminSession(token=token, expires_at=expires_at)

    async def authenticated(self, token: str | None) -> bool:
        if not token:
            return False
        return await self.repository.is_admin_session_valid(
            self._hash(token), datetime.now(UTC).isoformat()
        )

    async def expiry(self, token: str | None) -> datetime | None:
        if not token:
            return None
        raw = await self.repository.admin_session_expiry(self._hash(token))
        if raw is None:
            return None
        try:
            expiry = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return expiry if expiry > datetime.now(UTC) else None

    async def revoke(self, token: str | None) -> None:
        if token:
            await self.repository.revoke_admin_session(
                self._hash(token), datetime.now(UTC).isoformat()
            )

    def cookie_options(self) -> dict[str, object]:
        secure = self.settings.site_url.startswith("https://")
        return {
            "key": ADMIN_COOKIE_NAME,
            "httponly": True,
            "secure": secure,
            "samesite": "none" if secure else "lax",
            "path": "/",
            "max_age": self.settings.admin_session_ttl_seconds,
        }

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _is_rate_limited(self, client_key: str) -> bool:
        now = time.monotonic()
        attempts = [stamp for stamp in self._failures.get(client_key, []) if now - stamp < 300]
        self._failures[client_key] = attempts
        return len(attempts) >= 5

    def _record_failure(self, client_key: str) -> None:
        self._failures.setdefault(client_key, []).append(time.monotonic())
