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
    """Backend that talks to the server via MCP (stdio) — the real PoC route."""

    name = "mcp"
    _TIMEOUT = 120.0  # s, well above the 30s latency requirement

    def __init__(self, state_file: str | None = None) -> None:
        env = dict(os.environ)
        if state_file:
            env["LTS_MACHINE_STATE"] = state_file
        self._env = env
        self._loop = asyncio.new_event_loop()
        self._exit_stack = None
        self._session = None
        self._loop.run_until_complete(self._connect())

    async def _connect(self) -> None:
        from contextlib import AsyncExitStack

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=sys.executable, args=["-m", "mcp_server.server"],
            env=self._env)
        self._exit_stack = AsyncExitStack()
        read, write = await self._exit_stack.enter_async_context(
            stdio_client(params))
        self._session = await self._exit_stack.enter_async_context(
            ClientSession(read, write))
        await self._session.initialize()

    def list_tools(self) -> list[dict]:
        result = self._loop.run_until_complete(self._session.list_tools())
        return [{"type": "function",
                 "function": {"name": t.name,
                              "description": t.description or "",
                              "parameters": t.inputSchema or
                              {"type": "object", "properties": {}}}}
                for t in result.tools]

    def call_tool(self, name: str, arguments: dict) -> str:
        result = self._loop.run_until_complete(
            self._session.call_tool(name, arguments=arguments))
        parts = [c.text for c in result.content if getattr(c, "text", None)]
        return "\n".join(parts) if parts else "{}"

    def close(self) -> None:
        if self._exit_stack is not None:
            self._loop.run_until_complete(self._exit_stack.aclose())
        self._loop.close()
