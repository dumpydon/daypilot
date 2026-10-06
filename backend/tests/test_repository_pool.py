from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.app.persistence.repository import DayPilotRepository


@pytest.mark.asyncio
async def test_owned_pool_reuses_channel_and_keeps_query_transaction_boundaries(monkeypatch):
    import psycopg_pool

    raw = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())
    raw.execute.return_value = SimpleNamespace(
        fetchone=AsyncMock(return_value={"alive": 1, "checked_at": "database-clock"})
    )
    pools = []

    class Pool:
        check_connection = AsyncMock()

        def __init__(self, target, **kwargs):
            assert target == "postgresql://pooler.test/postgres"
            assert kwargs["min_size"] == 0 and kwargs["max_size"] == 4
            assert kwargs["kwargs"]["prepare_threshold"] is None
            assert kwargs["open"] is False
            assert kwargs["check"] is self.check_connection
            self.open = AsyncMock()
            self.close = AsyncMock()
            pools.append(self)

        @asynccontextmanager
        async def connection(self):
            try:
                yield raw
            except BaseException:
                await raw.rollback()
                raise
            else:
                await raw.commit()

    monkeypatch.setattr(psycopg_pool, "AsyncConnectionPool", Pool)
    repo = DayPilotRepository("postgresql://pooler.test/postgres")
    await repo.open_pool()
    await repo.open_pool()
    assert len(pools) == 1
    assert await repo.database_heartbeat() == "database-clock"
    assert await repo.database_heartbeat() == "database-clock"
    assert raw.execute.await_count == 2
    assert raw.commit.await_count == 2
    with pytest.raises(RuntimeError):
        async with repo._connect() as db:
            await db.execute("SELECT ?", (1,))
            raise RuntimeError("fixture failure")
    assert raw.execute.await_args.args == ("SELECT %s", (1,))
    raw.rollback.assert_awaited_once()
    await repo.close()
    await repo.close()
    pools[0].close.assert_awaited_once()


@pytest.mark.asyncio
async def test_sqlite_keeps_existing_connection_path(tmp_path):
    repo = DayPilotRepository(tmp_path / "test.db")
    await repo.open_pool()
    assert repo._pool is None
    await repo.initialize()
    assert await repo.database_heartbeat()
    await repo.close()
