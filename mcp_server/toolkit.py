"""
Toolkit: the actual implementation of all diagnosis tools.

This module is deliberately decoupled from the MCP protocol:
  - mcp_server/server.py registers these functions as MCP tools (production/demo);
  - agent/backends.DirectBackend calls them in-process (evaluation/tests).

Two modes:
  - SIMULATED: reads a MachineState (JSON) from the simulator. This is the
    controlled test environment from the Plan van Aanpak (scope: no
    production endpoints).
  - LIVE: uses psutil on the local machine (demo purposes).

All log output and free text passes through the PII filter layer before it can
reach the LLM context (Privacy by Design).
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import time
from pathlib import Path

from mcp_server.filters.pii_filter import PIIFilter

_FILTER = PIIFilter()
_LAST_FILTER_REPORT: dict = {}


# ---------------------------------------------------------------------------
# Machine state (simulated test environment)
# ---------------------------------------------------------------------------

class MachineContext:
    """Tracks which (virtual) machine the tools run against."""

    def __init__(self) -> None:
        self.mode = os.environ.get("LTS_MODE", "simulated")
        self._state: dict | None = None
        state_file = os.environ.get("LTS_MACHINE_STATE")
        if state_file:
            self.load_state_file(state_file)

    def load_state_file(self, path: str | Path) -> None:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self._state = data.get("state", data)

    def load_state(self, state: dict) -> None:
        self._state = state

    @property
    def state(self) -> dict:
        if self._state is None:
            raise RuntimeError(
                "Geen machine state geladen. Zet LTS_MACHINE_STATE of gebruik live-modus."
            )
        return self._state


CTX = MachineContext()


def _j(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _filtered(text: str) -> str:
    cleaned, report = _FILTER.filter_text(text)
    global _LAST_FILTER_REPORT
    _LAST_FILTER_REPORT = report.as_dict()
    return cleaned


# ---------------------------------------------------------------------------
# Tools — logging & system info
# ---------------------------------------------------------------------------

def get_recent_logs(max_lines: int = 40, level: str = "ALL") -> str:
    """Fetch the most recent system/application logs (PII-filtered).

    level: ALL | INFO | WARN | ERROR
    """
    if CTX.mode == "live":
        lines = [f"{time.strftime('%Y-%m-%d %H:%M:%S')} INFO Live mode: geen logbron gekoppeld."]
    else:
        lines = list(CTX.state.get("logs", []))
    if level != "ALL":
        lines = [l for l in lines if f" {level.upper()} " in f" {l} ".upper()]
    lines = lines[-max_lines:]
    cleaned, report = _FILTER.filter_lines(lines)
    return _j({"hostname": CTX.state.get("hostname") if CTX.mode != "live" else platform.node(),
               "log_lines": cleaned,
               "pii_filter_report": report.as_dict()})


def search_logs(query: str, max_lines: int = 20) -> str:
    """Search the logs for lines containing a keyword (PII-filtered)."""
    lines = [l for l in CTX.state.get("logs", []) if query.lower() in l.lower()][:max_lines]
    cleaned, report = _FILTER.filter_lines(lines)
    return _j({"query": query, "matches": cleaned, "match_count": len(cleaned),
               "pii_filter_report": report.as_dict()})


def get_system_info() -> str:
    """Basic information about the endpoint: hostname, OS, user, customer, uptime."""
    if CTX.mode == "live":
        return _j({"hostname": platform.node(), "os": platform.platform(),
                   "uptime_days": None, "mode": "live"})
    s = CTX.state
    return _j({"hostname": s["hostname"], "os": s["os"], "user": s["user"],
               "username": s["username"], "customer": s["customer"],
               "uptime_days": s["uptime_days"], "pending_updates": s["pending_updates"]})


def get_uptime() -> str:
    """System uptime in days (long uptime may indicate a reboot is needed)."""
    days = CTX.state.get("uptime_days", 0)
    return _j({"uptime_days": days, "reboot_recommended": days >= 30})


TOOL_REGISTRY = {
    f.__name__: f for f in [
        get_recent_logs, search_logs, get_system_info, get_uptime,
    ]
}
