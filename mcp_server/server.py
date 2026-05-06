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


if __name__ == "__main__":
    mcp.run()  # stdio transport
