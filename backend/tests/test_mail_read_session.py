from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from backend.app.domain.errors import ToolUnavailableError, UnauthorizedToolCallError
from backend.app.domain.models import PlanAction
from backend.app.mcp.mail_reader import MailReadSession
from backend.app.mcp.policy import WriteAuthorization, plan_hash


def reader_fixture(*, idle_seconds=1_200, fail=False, gate=None, started=None):
    calls, lifecycle = [], []

    @asynccontextmanager
    async def session():
        owner = asyncio.current_task()
        lifecycle.append("open")
        try:
            yield object()
        finally:
            assert asyncio.current_task() is owner
            lifecycle.append("close")

    async def invoke(payload):
        calls.append(payload["name"])
        if started is not None:
            started.set()
        if gate is not None:
            await gate.wait()
        if fail:
            raise RuntimeError("provider failure")
        return {"name": payload["name"]}

    async def load(_session):
        lifecycle.append("discover")
        return [
            SimpleNamespace(name=name, ainvoke=invoke)
            for name in ("search_mail", "get_thread", "get_message")
        ]

    return MailReadSession(session, load, idle_seconds=idle_seconds), calls, lifecycle


@pytest.mark.asyncio
async def test_search_and_dependent_body_read_share_one_owner_session():
    reader, calls, lifecycle = reader_fixture()
    try:
        await reader.tools()  # Startup discovery warms the same transport.
        await reader.invoke("search_mail", {"name": "search_mail"})
        await reader.invoke("get_thread", {"name": "get_thread"})
        assert calls == ["search_mail", "get_thread"]
        assert lifecycle == ["open", "discover"]
    finally:
        await reader.close()
    assert lifecycle == ["open", "discover", "close"]


@pytest.mark.asyncio
async def test_invalidation_and_idle_expiry_release_then_reopen():
    reader, _calls, lifecycle = reader_fixture(idle_seconds=0.02)
    try:
        await reader.tools()
        reader.invalidate()
        await reader.invoke("search_mail", {"name": "search_mail"})
        assert lifecycle.count("open") == 2
        assert lifecycle.count("close") == 1
        await asyncio.sleep(0.04)
        await reader.invoke("get_message", {"name": "get_message"})
        assert lifecycle.count("open") == 3
        assert lifecycle.count("close") == 2
    finally:
        await reader.close()


@pytest.mark.asyncio
async def test_failed_read_is_not_replayed_and_writes_are_rejected():
    reader, calls, lifecycle = reader_fixture(fail=True)
    with pytest.raises(ToolUnavailableError):
        await reader.invoke("create_draft", {"name": "create_draft"})
    assert not lifecycle
    with pytest.raises(RuntimeError, match="provider failure"):
        await reader.invoke("search_mail", {"name": "search_mail"})
    assert calls == ["search_mail"]
    assert lifecycle == ["open", "discover", "close"]
    await reader.close()


@pytest.mark.asyncio
async def test_cancellation_closes_the_owner_without_replaying():
    started, gate = asyncio.Event(), asyncio.Event()
    reader, calls, lifecycle = reader_fixture(gate=gate, started=started)
    request = asyncio.create_task(reader.invoke("search_mail", {"name": "search_mail"}))
    await asyncio.wait_for(started.wait(), 1)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert calls == ["search_mail"]
    assert lifecycle == ["open", "discover", "close"]
    await reader.close()


@pytest.mark.asyncio
async def test_concurrent_readers_do_not_share_an_active_rpc():
    started, gate = asyncio.Event(), asyncio.Event()
    reader, calls, lifecycle = reader_fixture(gate=gate, started=started)
    first = asyncio.create_task(reader.invoke("search_mail", {"name": "search_mail"}))
    await asyncio.wait_for(started.wait(), 1)
    second = asyncio.create_task(reader.invoke("get_thread", {"name": "get_thread"}))
    await asyncio.sleep(0)
    assert calls == ["search_mail"]
    gate.set()
    await asyncio.gather(first, second)
    assert calls == ["search_mail", "get_thread"]
    assert lifecycle == ["open", "discover"]
    await reader.close()


@pytest.mark.asyncio
async def test_gateway_retained_reads_preserve_public_guard_and_stateless_write(
    monkeypatch, tmp_path
):
    from backend.app.config import Settings
    from backend.app.mcp import gateway as gateway_module

    gateway = gateway_module.MCPGateway(
        Settings(
            _env_file=None,
            database_url=f"sqlite:///{tmp_path / 'test.db'}",
            daypilot_demo_mode=False,
            public_demo_mode=True,
        )
    )
    calls, fallback_calls, lifecycle = [], [], []

    class Tool:
        description = "Semantic contract"
        metadata = None

        def __init__(self, name):
            self.name = name
            self.args_schema = {}

        async def ainvoke(self, payload):
            calls.append(payload["name"])
            return {"ok": True}

    @asynccontextmanager
    async def session(_name):
        lifecycle.append("open")
        try:
            yield object()
        finally:
            lifecycle.append("close")

    async def load(_session, **_kwargs):
        return [Tool(name) for name in ("search_mail", "get_thread", "create_draft")]

    async def other_tools(*, server_name):
        return [Tool("search_web" if server_name == "web" else "list_events")]

    def stateless(_session, tool, **_kwargs):
        assert _session is None
        proxy = Tool(tool.name)

        async def fallback(payload):
            fallback_calls.append(payload["name"])
            return {"ok": True}

        proxy.ainvoke = fallback
        return proxy

    monkeypatch.setattr(gateway_module, "load_mcp_tools", load)
    monkeypatch.setattr(gateway_module, "convert_mcp_tool_to_langchain_tool", stateless)
    gateway.client = SimpleNamespace(session=session, get_tools=other_tools)
    try:
        await gateway.discover(admin_authorized=True)
        with pytest.raises(UnauthorizedToolCallError):
            await gateway.invoke("search_mail", {})
        assert calls == []
        await gateway.invoke("search_mail", {}, admin_authorized=True)
        await gateway.invoke("get_thread", {}, admin_authorized=True)
        assert calls == ["search_mail", "get_thread"]
        assert lifecycle == ["open"]
        with pytest.raises(UnauthorizedToolCallError):
            await gateway.invoke("create_draft", {}, admin_authorized=True)
        assert fallback_calls == []
        args = {"recipient": "fixture@example.test", "subject": "Fixture", "body": "Test"}
        action = PlanAction(
            id="draft",
            server_name="mail",
            tool_name="create_draft",
            arguments=args,
            description="Draft fixture",
            reason="Test",
            side_effecting=True,
            depends_on=[],
        )
        authorization = WriteAuthorization(
            "run-fixture", "draft", "create_draft", args, plan_hash([action]), (action,)
        )
        await gateway.invoke(
            "create_draft", args, admin_authorized=True, authorization=authorization
        )
        assert lifecycle == ["open", "close"]
        assert fallback_calls == ["create_draft"]
    finally:
        await gateway.close()
