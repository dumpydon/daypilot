"""Small dual-dialect database adapters used by the API and MCP children.

DayPilot intentionally keeps its SQL simple and stores JSON as text so the same
repository can run on local SQLite and Render PostgreSQL.  The adapters here
only normalize the DB-API differences; domain code remains SQL-oriented.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any

import aiosqlite

from backend.app.timing import timed

try:  # psycopg is optional for local installs that only use SQLite.
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover - exercised only before production extras install.
    psycopg = None  # type: ignore[assignment]
    dict_row = None  # type: ignore[assignment]


DatabaseTarget = str | Path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
_POSTGRES_PREFIXES = ("postgres://", "postgresql://")


def is_postgres_target(target: DatabaseTarget) -> bool:
    return isinstance(target, str) and target.startswith(_POSTGRES_PREFIXES)


def sqlite_path(target: DatabaseTarget) -> Path:
    if isinstance(target, Path):
        return target.expanduser().resolve()
    if target.startswith("sqlite:///"):
        raw_path = target.removeprefix("sqlite:///")
        path = Path(raw_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.expanduser().resolve()
    return Path(target).expanduser().resolve()


def _require_psycopg() -> Any:
    if psycopg is None:
        raise RuntimeError(
            "PostgreSQL DATABASE_URL requires the psycopg package. "
            "Install the production dependencies before starting DayPilot."
        )
    return psycopg


def _adapt_sql(query: str, parameters: Any, *, postgres: bool) -> str:
    if not postgres:
        return query
    adapted = query.replace("BEGIN IMMEDIATE", "BEGIN")
    ignored_insert = bool(re.search(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", adapted, flags=re.I))
    adapted = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", adapted, flags=re.I)
    if ignored_insert and "ON CONFLICT" not in adapted.upper():
        # SQLite's INSERT OR IGNORE is used for idempotent initialization. The
        # equivalent PostgreSQL clause is safe for every unique constraint.
        if adapted.rstrip().endswith(";"):
            adapted = adapted.rstrip()[:-1].rstrip()
            adapted += " ON CONFLICT DO NOTHING;"
        else:
            adapted = adapted.rstrip() + " ON CONFLICT DO NOTHING"
    adapted = re.sub(r"datetime\(\s*'now'\s*\)", "CURRENT_TIMESTAMP", adapted, flags=re.I)
    adapted = re.sub(r"GROUP_CONCAT\(", "STRING_AGG(", adapted, flags=re.I)
    if isinstance(parameters, Mapping):
        adapted = re.sub(r":([A-Za-z_]\w*)", r"%(\1)s", adapted)
    else:
        adapted = adapted.replace("?", "%s")
    return adapted


def _statements(script: str) -> list[str]:
    # Schemas contain no semicolons inside string literals. Keeping this tiny
    # splitter avoids pulling a SQL parser into the runtime.
    return [statement.strip() for statement in script.split(";") if statement.strip()]


class SyncCursor:
    def __init__(self, cursor: Any, *, postgres: bool = False) -> None:
        self._cursor = cursor
        self._postgres = postgres

    @property
    def rowcount(self) -> int:
        return int(getattr(self._cursor, "rowcount", -1))

    @property
    def lastrowid(self) -> int | None:
        value = getattr(self._cursor, "lastrowid", None)
        return int(value) if value is not None else None

    def fetchone(self) -> Any:
        with timed("postgres.fetchone" if self._postgres else "sqlite.fetchone"):
            return self._cursor.fetchone()

    def fetchall(self) -> list[Any]:
        with timed("postgres.fetchall" if self._postgres else "sqlite.fetchall"):
            return list(self._cursor.fetchall())

    def __iter__(self):
        return iter(self._cursor)


class SyncConnection:
    def __init__(self, connection: Any, *, postgres: bool) -> None:
        self._connection = connection
        self.postgres = postgres

    def execute(self, query: str, parameters: Any = ()) -> SyncCursor:
        with timed("postgres.execute" if self.postgres else "sqlite.execute"):
            cursor = self._connection.execute(
                _adapt_sql(query, parameters, postgres=self.postgres), parameters
            )
        return SyncCursor(cursor, postgres=self.postgres)

    def executemany(
        self,
        query: str,
        parameters: Iterable[Sequence[Any] | Mapping[str, Any]],
    ) -> SyncCursor:
        adapted = _adapt_sql(query, None, postgres=self.postgres)
        cursor = self._connection.cursor()
        try:
            with timed("postgres.executemany" if self.postgres else "sqlite.executemany"):
                cursor.executemany(adapted, parameters)
            return SyncCursor(cursor, postgres=self.postgres)
        finally:
            cursor.close()

    def executescript(self, script: str) -> None:
        for statement in _statements(script):
            if self.postgres and statement.upper().startswith("PRAGMA"):
                continue
            self.execute(statement)

    def commit(self) -> None:
        with timed("postgres.commit" if self.postgres else "sqlite.commit"):
            self._connection.commit()

    def rollback(self) -> None:
        with timed("postgres.rollback" if self.postgres else "sqlite.rollback"):
            self._connection.rollback()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> SyncConnection:
        return self

    def __exit__(self, exc_type: Any, _exc: Any, _tb: Any) -> None:
        try:
            if exc_type:
                self.rollback()
            else:
                self.commit()
        finally:
            self.close()


@contextmanager
def connect_sync(target: DatabaseTarget):
    if is_postgres_target(target):
        driver = _require_psycopg()
        with timed("postgres.connect"):
            connection = driver.connect(str(target), row_factory=dict_row)
        try:
            yield SyncConnection(connection, postgres=True)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return
    path = sqlite_path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    with timed("sqlite.connect"):
        connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        yield SyncConnection(connection, postgres=False)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


class AsyncCursor:
    def __init__(self, cursor: Any, *, postgres: bool = False) -> None:
        self._cursor = cursor
        self._postgres = postgres

    @property
    def rowcount(self) -> int:
        return int(getattr(self._cursor, "rowcount", -1))

    @property
    def lastrowid(self) -> int | None:
        value = getattr(self._cursor, "lastrowid", None)
        return int(value) if value is not None else None

    async def fetchone(self) -> Any:
        with timed("postgres.fetchone" if self._postgres else "sqlite.fetchone"):
            return await self._cursor.fetchone()

    async def fetchall(self) -> list[Any]:
        with timed("postgres.fetchall" if self._postgres else "sqlite.fetchall"):
            return list(await self._cursor.fetchall())


class AsyncConnection:
    def __init__(self, connection: Any, *, postgres: bool) -> None:
        self._connection = connection
        self.postgres = postgres

    async def execute(self, query: str, parameters: Any = ()) -> AsyncCursor:
        with timed("postgres.execute" if self.postgres else "sqlite.execute"):
            cursor = await self._connection.execute(
                _adapt_sql(query, parameters, postgres=self.postgres), parameters
            )
        return AsyncCursor(cursor, postgres=self.postgres)

    async def executemany(
        self,
        query: str,
        parameters: Iterable[Sequence[Any] | Mapping[str, Any]],
    ) -> None:
        adapted = _adapt_sql(query, None, postgres=self.postgres)
        async with self._connection.cursor() as cursor:
            with timed("postgres.executemany" if self.postgres else "sqlite.executemany"):
                await cursor.executemany(adapted, parameters)

    async def executescript(self, script: str) -> None:
        for statement in _statements(script):
            if self.postgres and statement.upper().startswith("PRAGMA"):
                continue
            await self.execute(statement)

    async def commit(self) -> None:
        with timed("postgres.commit" if self.postgres else "sqlite.commit"):
            await self._connection.commit()

    async def rollback(self) -> None:
        with timed("postgres.rollback" if self.postgres else "sqlite.rollback"):
            await self._connection.rollback()

    async def close(self) -> None:
        await self._connection.close()


@asynccontextmanager
async def connect_async(target: DatabaseTarget):
    if is_postgres_target(target):
        driver = _require_psycopg()
        with timed("postgres.connect"):
            connection = await driver.AsyncConnection.connect(str(target), row_factory=dict_row)
        try:
            yield AsyncConnection(connection, postgres=True)
        finally:
            await connection.close()
        return
    path = sqlite_path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    with timed("sqlite.connect"):
        connection = await aiosqlite.connect(path)
    connection.row_factory = aiosqlite.Row
    await connection.execute("PRAGMA journal_mode=WAL")
    await connection.execute("PRAGMA foreign_keys=ON")
    try:
        yield AsyncConnection(connection, postgres=False)
    finally:
        await connection.close()


def is_integrity_error(exc: BaseException) -> bool:
    if isinstance(exc, (sqlite3.IntegrityError, aiosqlite.IntegrityError)):
        return True
    return bool(psycopg is not None and isinstance(exc, psycopg.IntegrityError))
