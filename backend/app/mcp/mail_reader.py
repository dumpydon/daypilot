"""A bounded, gateway-owned session for semantic Mail reads.

The owner task enters and exits the MCP/AnyIO context itself. Calls are serialized;
no session/cancel scope is transferred between graph tasks or stored globally.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from backend.app.domain.errors import ToolUnavailableError
from backend.app.timing import timed

MAIL_READ_TOOLS = frozenset({"search_mail", "get_thread", "get_message"})
MAIL_SESSION_IDLE_SECONDS = 20 * 60


def _consume_exception(future: Any) -> None:
    if not future.cancelled():
        future.exception()


class MailReadSession:
    def __init__(
        self,
        session_factory: Callable[[], Any],
        load_tools: Callable[[Any], Awaitable[list[Any]]],
        *,
        idle_seconds: float = MAIL_SESSION_IDLE_SECONDS,
    ) -> None:
        self._session_factory = session_factory
        self._load_tools = load_tools
        self._idle_seconds = idle_seconds
        self._operation_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._ready: asyncio.Future | None = None
        self._queue: asyncio.Queue | None = None
        self._generation = 0
        self._owner_generation = -1
        self._accepting = False

    def invalidate(self) -> None:
        # Safe for synchronous connection-change callbacks; teardown happens
        # on the owning event loop before the next read (never during a write).
        self._generation += 1

    async def tools(self) -> list[Any]:
        async with self._start_lock:
            if self._owner_generation != self._generation:
                await self._discard()
            if self._task is None or self._task.done() or not self._accepting:
                await self._discard()
                self._queue = asyncio.Queue()
                self._ready = asyncio.get_running_loop().create_future()
                self._ready.add_done_callback(_consume_exception)
                self._owner_generation = self._generation
                self._accepting = True
                self._task = asyncio.create_task(
                    self._serve(self._queue, self._ready),
                    name="daypilot-mail-read-session",
                )
                self._task.add_done_callback(_consume_exception)
            with timed("mcp.mail_session_prepare"):
                return await asyncio.shield(self._ready)

    async def invoke(self, name: str, payload: dict[str, Any]) -> Any:
        if name not in MAIL_READ_TOOLS:
            raise ToolUnavailableError("Only semantic Mail reads use the retained session")
        with timed("mcp.mail_read_queue_wait"):
            await self._operation_lock.acquire()
        try:
            await self.tools()
            result = asyncio.get_running_loop().create_future()
            result.add_done_callback(_consume_exception)
            await self._queue.put((name, payload, result))
            try:
                return await asyncio.shield(result)
            except BaseException:
                # Never automatically replay a failed/cancelled provider call.
                await self._discard()
                raise
        finally:
            self._operation_lock.release()

    async def close(self) -> None:
        async with self._operation_lock:
            await self._discard()

    async def _discard(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _serve(self, queue: asyncio.Queue, ready: asyncio.Future) -> None:
        pending: asyncio.Future | None = None
        try:
            async with self._session_factory() as session:
                with timed("mcp.mail_session_tool_discovery"):
                    tools = await self._load_tools(session)
                by_name = {tool.name: tool for tool in tools}
                ready.set_result(tools)
                while True:
                    try:
                        name, payload, pending = await asyncio.wait_for(
                            queue.get(),
                            timeout=self._idle_seconds,
                        )
                    except TimeoutError:
                        self._accepting = False
                        break
                    try:
                        tool = by_name.get(name)
                        if tool is None or name not in MAIL_READ_TOOLS:
                            raise ToolUnavailableError("The Mail read capability is unavailable")
                        value = await tool.ainvoke(payload)
                        if not pending.done():
                            pending.set_result(value)
                        pending = None
                    except Exception as exc:
                        if not pending.done():
                            pending.set_exception(exc)
                        pending = None
                        break
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc)
            raise
        finally:
            self._accepting = False
            if pending is not None and not pending.done():
                pending.set_exception(RuntimeError("Mail read session closed"))
            while not queue.empty():
                _, _, result = queue.get_nowait()
                if not result.done():
                    result.set_exception(RuntimeError("Mail read session closed"))
