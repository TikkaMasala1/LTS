"""
Tool backends for the agent.

DirectBackend calls the toolkit in-process and provides tool definitions in
OpenAI function-calling format, so the LLM client (Ollama/Mock) can use them
directly. An McpBackend (stdio) follows once the server runs stably.
"""

from __future__ import annotations

import inspect
import json

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

