"""
LTS MCP server ("The local troubleshooter").

Exposes the diagnosis toolkit + Autotask integration via the
Model Context Protocol (FastMCP, stdio transport). The local LLM client
(Ollama/Phi-4-mini) uses this server for:

  - Resources : read-only context (filtered incident logs)
  - Tools     : executable diagnosis functions + Autotask actions
  - Prompts   : standardized role instruction for the agent

Start:  python -m mcp_server.server
Env:      LTS_MODE=simulated|live, LTS_MACHINE_STATE=<path to state.json>
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from autotask.client import get_autotask_client
from mcp_server import toolkit

mcp = FastMCP(
    "lts-troubleshooter",
    instructions=(
        "Lokale diagnose-server voor Ultimum Managed Services. "
        "Alle loggegevens zijn door de PII-filterlaag gegaan. "
        "Systeemwijzigingen mogen uitsluitend als VOORSTEL worden aangeboden "
        "(Human-in-the-Loop)."
    ),
)

# ---------------------------------------------------------------------------
# Diagnosis tools (shared toolkit)
# ---------------------------------------------------------------------------

for _fn in toolkit.TOOL_REGISTRY.values():
    mcp.tool()(_fn)

# ---------------------------------------------------------------------------
# Autotask tools (reads may be autonomous; writes = draft only)
# ---------------------------------------------------------------------------

_at = get_autotask_client()


@mcp.tool()
def autotask_search_tickets(status: str = "open", max_results: int = 10) -> str:
    """Search tickets in Autotask (sandbox). Reading is allowed without approval."""
    return _at.search_tickets_json(status=status, max_results=max_results)


@mcp.tool()
def autotask_get_ticket(ticket_id: str) -> str:
    """Fetch a single Autotask ticket (sandbox), including status and description."""
    return _at.get_ticket_json(ticket_id)


@mcp.tool()
def autotask_draft_ticket(title: str, description: str, priority: str = "Medium",
                          queue: str = "Managed Services") -> str:
    """Create a DRAFT ticket. The draft is only actually created in
    Autotask after a servicedesk employee has explicitly
    approved it in the HitL interface (functional requirement 3)."""
    return _at.draft_ticket_json(title=title, description=description,
                                 priority=priority, queue=queue)


if __name__ == "__main__":
    mcp.run()  # stdio transport
