from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.app.config import Settings
from backend.app.domain.errors import FileAccessError, ProviderUnavailableError

SUPPORTED_SUFFIXES = {".txt", ".md", ".json", ".csv"}
SENSITIVE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    "id_rsa",
    "id_ed25519",
    "known_hosts",
    "credentials.json",
    "token.json",
}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}
SENSITIVE_PARTS = {".ssh", ".aws", ".config", "credentials", "keychains"}


class LocalFilesService:
    """Read-only file adapter constrained to canonical, configured roots."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database_target = settings.database_target

    def list_files(self, query: str | None = None, limit: int = 25) -> dict[str, Any]:
        files = list(self._iter_files())
        if query and query.strip():
            files = self._rank(files, query)
        else:
            files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        payload = [
            self._metadata(path)
            for path in files[: min(limit, self.settings.local_file_max_results)]
        ]
        return {
            "files": payload,
            "count": len(payload),
            "source": "connected",
            "provider": "Local Mac",
            "roots": len(self._roots()),
        }

    def search_files(self, query: str, limit: int = 10) -> dict[str, Any]:
        return self.list_files(query, limit)

    def get_file_metadata(self, file_id: str) -> dict[str, Any]:
        path = self._path_for_id(file_id)
        return {**self._metadata(path), "source": "connected", "provider": "Local Mac"}

    def read_file(self, file_id: str) -> dict[str, Any]:
        path = self._path_for_id(file_id)
        metadata = self._metadata(path)
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise FileAccessError(f"Unsupported local file type {path.suffix or '(none)'}.")
        if path.stat().st_size > self.settings.local_file_max_bytes:
            raise FileAccessError("The local file is larger than DayPilot's configured read limit.")
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise FileAccessError("The local file could not be read; check permissions.") from exc
        truncated = len(content) > self.settings.local_file_max_bytes
        content = content[: self.settings.local_file_max_bytes]
        return {
            **metadata,
            "content": content,
            "truncated": truncated,
            "source": "connected",
            "provider": "Local Mac",
        }

    def _roots(self) -> list[Path]:
        try:
            from backend.app.persistence.database import connect_sync

            with connect_sync(self.database_target) as connection:
                rows = connection.execute(
                    "SELECT path FROM file_roots ORDER BY added_at"
                ).fetchall()
        except Exception as exc:
            raise ProviderUnavailableError("Local Files configuration is unavailable.") from exc
        roots: list[Path] = []
        for row in rows:
            raw_path = row["path"]
            try:
                root = Path(raw_path).resolve(strict=True)
            except OSError:
                continue
            if root.is_dir():
                roots.append(root)
        if not roots:
            raise ProviderUnavailableError(
                "No readable local folders are connected. Add a folder in Preferences."
            )
        return roots

    def _iter_files(self) -> Iterable[Path]:
        count = 0
        for root in self._roots():
            try:
                candidates = root.rglob("*")
            except OSError as exc:
                raise ProviderUnavailableError(
                    "A configured local folder could not be scanned."
                ) from exc
            for candidate in candidates:
                if count >= self.settings.local_file_max_results * 20:
                    return
                try:
                    canonical = candidate.resolve(strict=True)
                    canonical.relative_to(root)
                    relative = canonical.relative_to(root)
                    if (
                        not canonical.is_file()
                        or len(relative.parts) > self.settings.local_file_max_depth
                    ):
                        continue
                    self._guard_path(canonical, root)
                    if canonical.suffix.lower() not in SUPPORTED_SUFFIXES:
                        continue
                    if canonical.stat().st_size > self.settings.local_file_max_bytes:
                        continue
                except (OSError, ValueError, FileAccessError):
                    continue
                count += 1
                yield canonical

    def _rank(self, files: list[Path], query: str) -> list[Path]:
        tokens = [token for token in re.findall(r"[a-z0-9_]+", query.lower()) if len(token) > 1]
        ranked: list[tuple[int, Path]] = []
        for path in files:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")[:20_000].lower()
            except OSError:
                continue
            haystack = f"{path.name.lower()} {content}"
            score = sum(
                4 if token in path.name.lower() else 1 for token in tokens if token in haystack
            )
            if score:
                ranked.append((score, path))
        ranked.sort(key=lambda item: (item[0], item[1].stat().st_mtime), reverse=True)
        return [path for _, path in ranked]

    def _metadata(self, path: Path) -> dict[str, Any]:
        root = self._root_for(path)
        relative = path.relative_to(root)
        stat = path.stat()
        return {
            "id": self._file_id(root, relative),
            "filename": path.name,
            "file_type": path.suffix.lower(),
            "description": f"Local file in {root.name}",
            "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
            "size_bytes": stat.st_size,
            "relative_path": str(relative),
            "root": root.name or str(root),
        }

    def _path_for_id(self, file_id: str) -> Path:
        if not re.fullmatch(r"local:[a-f0-9]{16}", file_id):
            raise FileAccessError("Local file ID is not valid.")
        for root in self._roots():
            for candidate in root.rglob("*"):
                try:
                    canonical = candidate.resolve(strict=True)
                    relative = canonical.relative_to(root)
                    if (
                        not canonical.is_file()
                        or len(relative.parts) > self.settings.local_file_max_depth
                    ):
                        continue
                    self._guard_path(canonical, root)
                    if self._file_id(root, relative) == file_id:
                        return canonical
                except (OSError, ValueError, FileAccessError):
                    continue
        raise FileAccessError("The local file was not found in the connected folders.")

    def _root_for(self, path: Path) -> Path:
        for root in self._roots():
            try:
                path.relative_to(root)
                return root
            except ValueError:
                continue
        raise FileAccessError("The file is outside configured local folders.")

    @staticmethod
    def _guard_path(path: Path, root: Path) -> None:
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise FileAccessError("The file resolves outside its configured folder.") from exc
        name = path.name.lower()
        if (
            name in {sensitive.lower() for sensitive in SENSITIVE_NAMES}
            or name.startswith(".env.")
            or path.suffix.lower() in SENSITIVE_SUFFIXES
        ):
            raise FileAccessError("Sensitive local files are not available to DayPilot.")
        if any(part.lower() in SENSITIVE_PARTS for part in path.parts):
            raise FileAccessError("Files in protected credential folders are not available.")

    @staticmethod
    def _file_id(root: Path, relative: Path) -> str:
        digest = hashlib.sha256(f"{root}\0{relative}".encode()).hexdigest()[:16]
        return f"local:{digest}"
