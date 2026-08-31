from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from backend.app.providers.models import CredentialRecord


class CredentialStoreError(RuntimeError):
    """Raised when the local encrypted credential store cannot be used."""


class EncryptedCredentialStore:
    """Small backend-only encrypted file store for local development.

    The encryption key is either supplied through a server-only environment
    variable or generated in a mode-600 file next to the local database. The
    file store is intentionally replaceable by a deployment secret manager;
    neither credentials nor the key ever cross the MCP/UI boundary.
    """

    def __init__(self, path: Path, key_path: Path, key: str | None = None) -> None:
        self.path = path
        self.key_path = key_path
        self._lock = threading.RLock()
        self._fernet = Fernet(self._load_key(key))

    def get(self, provider: str) -> CredentialRecord | None:
        with self._lock:
            value = self._read().get(provider)
        if not isinstance(value, dict) or not value.get("access_token"):
            return None
        return CredentialRecord.from_dict(provider, value)

    def set(self, credential: CredentialRecord) -> None:
        with self._lock:
            values = self._read()
            values[credential.provider] = credential.as_dict()
            self._write(values)

    def update(self, provider: str, **updates: Any) -> CredentialRecord | None:
        with self._lock:
            existing = self.get(provider)
            if existing is None:
                return None
            values = existing.as_dict()
            values.update(updates)
            credential = CredentialRecord.from_dict(provider, values)
            self._write({**self._read(), provider: credential.as_dict()})
            return credential

    def delete(self, provider: str) -> None:
        with self._lock:
            values = self._read()
            if provider in values:
                del values[provider]
                self._write(values)

    def _load_key(self, explicit: str | None) -> bytes:
        raw = explicit or os.getenv("DAYPILOT_CREDENTIAL_KEY")
        if raw:
            try:
                Fernet(raw.encode())
            except Exception as exc:
                raise CredentialStoreError(
                    "DAYPILOT_CREDENTIAL_KEY is not a valid Fernet key"
                ) from exc
            return raw.encode()
        try:
            self.key_path.parent.mkdir(parents=True, exist_ok=True)
            if self.key_path.exists():
                key = self.key_path.read_bytes().strip()
            else:
                key = Fernet.generate_key()
                self.key_path.write_bytes(key)
            os.chmod(self.key_path, 0o600)
            Fernet(key)
            return key
        except Exception as exc:
            raise CredentialStoreError("Unable to initialize the backend credential key") from exc

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            payload = self._fernet.decrypt(self.path.read_bytes())
            value = json.loads(payload.decode("utf-8"))
            return value if isinstance(value, dict) else {}
        except (InvalidToken, OSError, json.JSONDecodeError) as exc:
            raise CredentialStoreError("Unable to decrypt the backend credential store") from exc

    def _write(self, values: dict[str, Any]) -> None:
        if not values:
            self.path.unlink(missing_ok=True)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        payload = self._fernet.encrypt(
            json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        temporary.write_bytes(payload)
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)
        os.chmod(self.path, 0o600)
