from __future__ import annotations

import pytest
from langchain_mcp_adapters.client import MultiServerMCPClient

from backend.app.config import Settings
from backend.app.mcp.gateway import MCPGateway
from mcp_servers.common.database import initialize_demo_database


@pytest.mark.asyncio
async def test_real_stdio_mail_session_keeps_grounded_search_and_body_reads(tmp_path):
    path = tmp_path / "mail.db"
    initialize_demo_database(path, "Asia/Kolkata")
    gateway = MCPGateway(
        Settings(_env_file=None, database_url=f"sqlite:///{path}", daypilot_demo_mode=True)
    )
    gateway.connections = {"mail": gateway.connections["mail"]}
    gateway.client = MultiServerMCPClient(gateway.connections, handle_tool_errors=False)
    try:
        tools = await gateway.discover()
        assert {tool.name for tool in tools} == {
            "search_mail",
            "get_thread",
            "get_message",
            "create_draft",
        }
        owner = gateway._mail_reader._task
        result = await gateway.invoke("search_mail", {"query": "Rahul interview", "limit": 1})
        assert result["count"] == 1
        thread = await gateway.invoke(
            "get_thread", {"thread_id": result["threads"][0]["thread_id"]}
        )
        assert thread["id"] == result["threads"][0]["thread_id"]
        assert thread["messages"]
        assert gateway._mail_reader._task is owner
    finally:
        await gateway.close()
    assert owner.done()
