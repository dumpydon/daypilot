from __future__ import annotations

import asyncio
import logging
import os
import sys
from copy import deepcopy
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import uuid4

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from backend.app.config import Settings
from backend.app.domain.errors import ToolUnavailableError, UnauthorizedToolCallError
from backend.app.domain.models import ToolMetadata
from backend.app.mcp.policy import (
    WriteAuthorization,
    enforce_tool_policy,
    get_policy,
)
from backend.app.providers.manager import ConnectionManager
from backend.app.timing import timed

logger = logging.getLogger(__name__)
CATALOG_CACHE_TTL_SECONDS = 2.0


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
        self._catalog_cache: dict[bool, tuple[float, list[dict[str, Any]]]] = {}
        # Keep the raw flag for diagnostics/backwards compatibility; cache
        # decisions use the effective visibility scope below.
        self._discovery_admin_authorized: bool | None = None
        self._discovery_full_access: bool | None = None
        self._discovery_lock = asyncio.Lock()
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
            "DAYPILOT_TIMEZONE": settings.daypilot_timezone,
            "PYTHONPATH": str(project_root),
            "PATH": os.getenv("PATH", ""),
            "DATABASE_URL": settings.database_connection_url,
            **runtime_env,
            **settings.mcp_environment(),
        }
        if not settings.database_is_postgres:
            base_env["DAYPILOT_DATABASE_PATH"] = str(settings.database_path)
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

    async def discover(
        self,
        *,
        force: bool = False,
        admin_authorized: bool = False,
    ) -> list[ToolMetadata]:
        with timed("mcp.discovery"):
            async with self._discovery_lock:
                return await self._discover_locked(
                    force=force,
                    admin_authorized=admin_authorized,
                )

    async def _discover_locked(
        self,
        *,
        force: bool,
        admin_authorized: bool,
    ) -> list[ToolMetadata]:
        discovery_scope = not self._is_public_restricted(admin_authorized)
        if self._metadata and not force and self._discovery_full_access == discovery_scope:
            return list(self._metadata.values())
        self._tools.clear()
        self._metadata.clear()
        self._server_status.clear()
        self._catalog_cache.clear()
        self._discovery_admin_authorized = admin_authorized
        self._discovery_full_access = discovery_scope
        server_names = self._visible_server_names(admin_authorized)
        for server_name in self.connections:
            if server_name not in server_names:
                self._server_status[server_name] = {
                    "name": server_name,
                    "connected": False,
                    "tool_count": 0,
                    "tools": [],
                    "error": "Personal capability available to admin only.",
                }
                continue
            try:
                with timed(f"mcp.discovery.{server_name}"):
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
                logger.warning(
                    "MCP server %s discovery failed (%s)",
                    server_name,
                    type(exc).__name__,
                )
                self._server_status[server_name] = {
                    "name": server_name,
                    "connected": False,
                    "tool_count": 0,
                    "tools": [],
                    "error": f"{server_name.title()} capability could not initialize.",
                }
        # A catalog request may have raced the sequential discovery loop;
        # discard any partial status snapshot before exposing the completed one.
        self._catalog_cache.clear()
        return list(self._metadata.values())

    async def invoke(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        authorization: WriteAuthorization | None = None,
        admin_authorized: bool = False,
    ) -> Any:
        discovery_scope = not self._is_public_restricted(admin_authorized)
        async with self._discovery_lock:
            if not self._tools or (
                self._discovery_full_access is not None
                and self._discovery_full_access != discovery_scope
            ):
                await self._discover_locked(
                    force=bool(self._tools),
                    admin_authorized=admin_authorized,
                )
            tool = self._tools.get(tool_name)
            metadata = self._metadata.get(tool_name)
        if tool is None or metadata is None:
            raise ToolUnavailableError(f"MCP tool {tool_name!r} is not available")
        if self._is_public_restricted(admin_authorized) and metadata.server_name != "web":
            raise UnauthorizedToolCallError(
                "Personal connected services are disabled in the public demo."
            )
        policy = get_policy(tool_name, metadata.server_name)
        enforce_tool_policy(tool_name, arguments, policy, authorization)
        stage = {
            "search_mail": "mcp.search_mail",
            "get_thread": "mcp.get_thread",
        }.get(tool_name, "mcp.tool_invoke")
        with timed(stage):
            result = await tool.ainvoke(
                {
                    "type": "tool_call",
                    "id": f"mcp-{uuid4().hex}",
                    "name": tool_name,
                    "args": arguments,
                }
            )
        return self._structured_result(result)

    def catalog(self, *, admin_authorized: bool = False) -> list[dict[str, Any]]:
        cache_scope = not self._is_public_restricted(admin_authorized)
        cached = self._catalog_cache.get(cache_scope)
        if cached is not None:
            cached_at, cached_catalog = cached
            if monotonic() - cached_at < CATALOG_CACHE_TTL_SECONDS:
                return deepcopy(cached_catalog)
            self._catalog_cache.pop(cache_scope, None)
        with timed("mcp.catalog"):
            result = self._build_catalog(admin_authorized=admin_authorized)
        self._catalog_cache[cache_scope] = (monotonic(), deepcopy(result))
        return result

    def invalidate_catalog(self) -> None:
        """Drop provider-status snapshots after an explicit connection change."""
        self._catalog_cache.clear()

    def _build_catalog(self, *, admin_authorized: bool) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for server_name, status in self._server_status.items():
            if self._is_public_restricted(admin_authorized) and server_name != "web":
                result.append(
                    {
                        **status,
                        "connected": False,
                        "tool_count": 0,
                        "tools": [],
                        "error": "Personal capability available to admin only.",
                        "provider": "Google Workspace"
                        if server_name in {"mail", "calendar", "tasks"}
                        else "Unavailable",
                        "provider_state": "unavailable",
                        "account_label": None,
                        "requires_reauth": False,
                        "last_error": "Available to admin only.",
                        "connection_mode": "managed",
                    }
                )
                continue
            result.append({**status, **self._connection_status(server_name)})
        return result

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

    def _visible_server_names(self, admin_authorized: bool) -> tuple[str, ...]:
        if self._is_public_restricted(admin_authorized):
            return ("web",)
        return tuple(self.connections)

    def _is_public_restricted(self, admin_authorized: bool) -> bool:
        return (
            self.settings.public_demo_mode
            and not admin_authorized
            and not self.settings.daypilot_demo_mode
        )

    def public_restricted(self, admin_authorized: bool = False) -> bool:
        return self._is_public_restricted(admin_authorized)

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
