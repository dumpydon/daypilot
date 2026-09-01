from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from backend.app.config import Settings
from backend.app.domain.errors import ToolUnavailableError
from backend.app.domain.models import ToolMetadata
from backend.app.mcp.policy import (
    WriteAuthorization,
    enforce_tool_policy,
    get_policy,
)
from backend.app.providers.manager import ConnectionManager


class MCPGateway:
    """The only bridge from orchestration to dynamically discovered MCP tools."""

    def __init__(
        self, settings: Settings, connection_manager: ConnectionManager | None = None
    ) -> None:
        self.settings = settings
        self.connection_manager = connection_manager
        self._tools: dict[str, BaseTool] = {}
        self._metadata: dict[str, ToolMetadata] = {}
        self._server_status: dict[str, dict[str, Any]] = {}
        project_root = Path(__file__).resolve().parents[3]
        # Keep provider secrets/configuration explicit, but retain the standard
        # runtime environment needed by SDK TLS/HTTP clients (notably HOME for
        # the Composio client). Never forward the parent environment wholesale.
        runtime_env = {
            key: os.environ[key]
            for key in ("HOME", "USER", "LANG", "LC_ALL", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")
            if os.environ.get(key)
        }
        base_env = {
            "DAYPILOT_DATABASE_PATH": str(settings.database_path),
            "DAYPILOT_TIMEZONE": settings.daypilot_timezone,
            "PYTHONPATH": str(project_root),
            "PATH": os.getenv("PATH", ""),
            **runtime_env,
            **settings.mcp_environment(),
        }
        self.connections: dict[str, dict[str, Any]] = {
            name: {
                "transport": "stdio",
                "command": sys.executable,
                "args": ["-m", f"mcp_servers.{name}.server"],
                "cwd": str(project_root),
                "env": base_env,
            }
            for name in ("mail", "calendar", "tasks", "files", "x", "web")
        }
        self.client = MultiServerMCPClient(
            self.connections,  # type: ignore[arg-type]
            handle_tool_errors=False,
        )

    async def discover(self, *, force: bool = False) -> list[ToolMetadata]:
        if self._metadata and not force:
            return list(self._metadata.values())
        self._tools.clear()
        self._metadata.clear()
        self._server_status.clear()
        for server_name in self.connections:
            try:
                tools = await self.client.get_tools(server_name=server_name)
                for tool in tools:
                    policy = get_policy(tool.name, server_name)
                    metadata = ToolMetadata(
                        name=tool.name,
                        server_name=server_name,
                        description=tool.description or "",
                        risk_level=policy.risk_level,
                        side_effecting=policy.side_effecting,
                        input_schema=self._input_schema(tool),
                    )
                    self._tools[tool.name] = tool
                    self._metadata[tool.name] = metadata
                self._server_status[server_name] = {
                    "name": server_name,
                    "connected": True,
                    "tool_count": len(tools),
                    "tools": [tool.name for tool in tools],
                    "error": None,
                }
            except Exception as exc:  # transport failures must remain visible
                self._server_status[server_name] = {
                    "name": server_name,
                    "connected": False,
                    "tool_count": 0,
                    "tools": [],
                    "error": str(exc),
                }
        return list(self._metadata.values())

    async def invoke(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        authorization: WriteAuthorization | None = None,
    ) -> Any:
        if not self._tools:
            await self.discover()
        tool = self._tools.get(tool_name)
        metadata = self._metadata.get(tool_name)
        if tool is None or metadata is None:
            raise ToolUnavailableError(f"MCP tool {tool_name!r} is not available")
        policy = get_policy(tool_name, metadata.server_name)
        enforce_tool_policy(tool_name, arguments, policy, authorization)
        result = await tool.ainvoke(
            {
                "type": "tool_call",
                "id": f"mcp-{uuid4().hex}",
                "name": tool_name,
                "args": arguments,
            }
        )
        return self._structured_result(result)

    def catalog(self) -> list[dict[str, Any]]:
        return [
            {
                **status,
                **self._connection_status(server_name),
            }
            for server_name, status in self._server_status.items()
        ]

    def _connection_status(self, server_name: str) -> dict[str, Any]:
        if server_name == "web":
            configured = bool(self.settings.tavily_api_key)
            return {
                "provider": "Tavily",
                "provider_state": "connected" if configured else "unavailable",
                "account_label": "Public web search" if configured else None,
                "requires_reauth": False,
                "last_error": None
                if configured
                else "Set TAVILY_API_KEY to enable fresh public web research.",
                "connection_mode": "direct",
            }
        if self.connection_manager is None:
            return {}
        return self.connection_manager.status(server_name)

    def metadata(self, tool_name: str) -> ToolMetadata | None:
        return self._metadata.get(tool_name)

    def _input_schema(self, tool: BaseTool) -> dict[str, Any]:
        schema = tool.args_schema
        if isinstance(schema, dict):
            return schema
        if schema is None:
            return {}
        return schema.model_json_schema()

    def _structured_result(self, result: Any) -> Any:
        if isinstance(result, ToolMessage):
            artifact = result.artifact
            if isinstance(artifact, dict) and "structured_content" in artifact:
                return artifact["structured_content"]
            return result.content
        return result
