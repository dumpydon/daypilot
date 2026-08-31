from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class CredentialRecord:
    provider: str
    access_token: str
    refresh_token: str | None
    expires_at: float | None
    scopes: tuple[str, ...]
    account_label: str | None = None
    metadata: dict[str, Any] | None = None
    last_error: str | None = None

    @classmethod
    def from_dict(cls, provider: str, value: dict[str, Any]) -> CredentialRecord:
        return cls(
            provider=provider,
            access_token=str(value.get("access_token", "")),
            refresh_token=value.get("refresh_token"),
            expires_at=float(value["expires_at"]) if value.get("expires_at") is not None else None,
            scopes=tuple(str(scope) for scope in value.get("scopes", [])),
            account_label=value.get("account_label"),
            metadata=dict(value.get("metadata") or {}),
            last_error=value.get("last_error"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "scopes": list(self.scopes),
            "account_label": self.account_label,
            "metadata": self.metadata or {},
            "last_error": self.last_error,
        }

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= datetime.now().timestamp() + 60
