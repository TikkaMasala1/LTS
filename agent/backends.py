"""
Tool backends for the agent.

- McpBackend    : connects via the Model Context Protocol (stdio) to
                  mcp_server/server.py — the production/demo route of the PoC.
- DirectBackend : calls the same toolkit in-process. Used by
                  the automated evaluation and the unit tests (faster and
                  without a subprocess), with identical tool output.

Both provide tool definitions in OpenAI function-calling format, so the
LLM client (Ollama/Mock) can use them directly.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys

from autotask.client import get_autotask_client
from mcp_server import toolkit

PY_TO_JSON = {int: "integer", float: "number", str: "string", bool: "boolean"}


def _fn_to_openai_tool(fn) -> dict:
    sig = inspect.signature(fn)
    props, required = {}, []
    for name, param in sig.parameters.items():
        ann = param.annotation if param.annotation is not inspect.Parameter.empty else str
        props[name] = {"type": PY_TO_JSON.get(ann, "string")}
        if param.default is inspect.Parameter.empty:
            required.append(name)
    doc = (fn.__doc__ or "").strip()
    return {"type": "function",
            "function": {"name": fn.__name__, "description": doc,
                         "parameters": {"type": "object", "properties": props,
                                        "required": required}}}


class DirectBackend:
    """In-process backend (evaluation/tests). Same tools as the MCP server."""

    name = "direct"

    def __init__(self) -> None:
        self._at = get_autotask_client()
        self._fns = dict(toolkit.TOOL_REGISTRY)
        self._fns["autotask_search_tickets"] = self._at_search
        self._fns["autotask_get_ticket"] = self._at_get
        self._fns["autotask_draft_ticket"] = self._at_draft
        # execute_remediation is available after HitL approval only (not listed to LLM)
        self._fns["execute_remediation"] = self._execute_remediation

    def _at_search(self, status: str = "open", max_results: int = 10) -> str:
        """Search tickets in Autotask (sandbox)."""
        return self._at.search_tickets_json(status, max_results)

    def _at_get(self, ticket_id: str) -> str:
        """Fetch a single Autotask ticket."""
        return self._at.get_ticket_json(ticket_id)

    def _at_draft(self, title: str, description: str, priority: str = "Medium",
                  queue: str = "Managed Services") -> str:
        """Create a draft ticket (requires HitL approval)."""
        return self._at.draft_ticket_json(title=title, description=description,
                                          priority=priority, queue=queue)

    def _execute_remediation(self, action: str, target: str = "", reason: str = "") -> str:
        """Execute remediation (only after explicit technician approval)."""
        from mcp_server import toolkit as _tk
        return _tk.execute_remediation(action, target=target, reason=reason)

    def load_state(self, state: dict) -> None:
        toolkit.CTX.load_state(state)

    def list_tools(self) -> list[dict]:
        return [_fn_to_openai_tool(fn) for fn in self._fns.values()]

    def call_tool(self, name: str, arguments: dict) -> str:
        fn = self._fns.get(name)
        if fn is None:
            return json.dumps({"error": f"Onbekende tool: {name}"})
        try:
            return fn(**arguments)
        except TypeError as exc:
            return json.dumps({"error": f"Ongeldige argumenten voor {name}: {exc}"})

    def close(self) -> None:  # symmetry with McpBackend
        pass


class McpBackend:
    """Backend that talks to the server via MCP (stdio) — the real PoC route.

    Implementation note: the MCP client session uses anyio cancel scopes internally,
    which must be opened and closed in the *same* asyncio task. Therefore
    one long-running runner task runs here (in its own background thread)
    that owns the session and handles all requests via a queue. The
    synchronous methods (list_tools/call_tool) put work on that queue and
    wait for the result. An earlier variant with separate run_until_complete
    calls produced on Windows/Python 3.13+: "Attempted to exit cancel scope in
    a different task than it was entered in".
    """

    name = "mcp"
    _TIMEOUT = 120.0  # s, well above the 30s latency requirement

    def __init__(self, state_file: str | None = None) -> None:
        import concurrent.futures
        import threading

        env = dict(os.environ)
        if state_file:
            env["LTS_MACHINE_STATE"] = state_file
        self._env = env

        self._requests: asyncio.Queue | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._startup = concurrent.futures.Future()  # ready or error at startup
        self._thread = threading.Thread(target=self._thread_main,
                                        name="mcp-backend", daemon=True)
        self._thread.start()
        # Wait until the session is initialized (or propagate the startup error).
        self._startup.result(timeout=self._TIMEOUT)

    # ----- background thread: one event loop, one runner task ------------

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._runner())
        except Exception as exc:  # noqa: BLE001
            if not self._startup.done():
                self._startup.set_exception(exc)

    async def _runner(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=sys.executable, args=["-m", "mcp_server.server"],
            env=self._env)

        self._loop = asyncio.get_running_loop()
        self._requests = asyncio.Queue()

        # Context managers are opened and closed in THIS task.
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                self._startup.set_result(True)
                while True:
                    item = await self._requests.get()
                    if item is None:  # sentinel: shut down
                        break
                    coro_fn, args, future = item
                    try:
                        result = await coro_fn(session, *args)
                        future.set_result(result)
                    except Exception as exc:  # noqa: BLE001
                        future.set_exception(exc)

    def _submit(self, coro_fn, *args):
        """Put work on the runner task's queue and wait synchronously."""
        import concurrent.futures
        if self._loop is None or self._requests is None:
            raise RuntimeError("MCP-backend is niet (meer) verbonden")
        future: concurrent.futures.Future = concurrent.futures.Future()
        self._loop.call_soon_threadsafe(
            self._requests.put_nowait, (coro_fn, args, future))
        return future.result(timeout=self._TIMEOUT)

    # ----- public, synchronous interface ----------------------------------

    def list_tools(self) -> list[dict]:
        async def _list(session):
            return await session.list_tools()
        result = self._submit(_list)
        return [{"type": "function",
                 "function": {"name": t.name,
                              "description": t.description or "",
                              "parameters": t.inputSchema or
                              {"type": "object", "properties": {}}}}
                for t in result.tools]

    def call_tool(self, name: str, arguments: dict) -> str:
        async def _call(session, name, arguments):
            return await session.call_tool(name, arguments=arguments)
        result = self._submit(_call, name, arguments)
        parts = [c.text for c in result.content if getattr(c, "text", None)]
        return "\n".join(parts) if parts else "{}"

    def close(self) -> None:
        if self._loop is not None and self._requests is not None:
            try:
                self._loop.call_soon_threadsafe(
                    self._requests.put_nowait, None)  # sentinel
            except RuntimeError:
                pass  # loop already closed
        self._thread.join(timeout=10.0)
        self._loop = None
        self._requests = None
