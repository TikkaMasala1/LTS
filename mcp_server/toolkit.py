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


# ---------------------------------------------------------------------------
# Tools — storage (scenario 1)
# ---------------------------------------------------------------------------

def get_disk_usage(drive: str = "C:") -> str:
    """Disk usage per volume: total/used/free in GB and percentage."""
    if CTX.mode == "live":
        usage = shutil.disk_usage("/")
        gb = 1024 ** 3
        return _j({"drive": "/", "total_gb": round(usage.total / gb, 1),
                   "used_gb": round(usage.used / gb, 1),
                   "free_gb": round(usage.free / gb, 1),
                   "used_pct": round(100 * usage.used / usage.total, 1)})
    info = CTX.state["disk"].get(drive) or next(iter(CTX.state["disk"].values()))
    return _j({"drive": drive, **info,
               "status": "CRITICAL" if info["used_pct"] >= 90 else
                         "WARNING" if info["used_pct"] >= 80 else "OK"})


def list_large_files(top_n: int = 5) -> str:
    """The largest files/folders on the system (candidates for cleanup)."""
    files = CTX.state.get("large_files", [])[:top_n]
    return _j({"large_files": files})


def get_temp_files_size() -> str:
    """Total size of temporary files (Windows Temp, browser caches)."""
    size = CTX.state.get("temp_size_gb", 0)
    return _j({"temp_size_gb": size,
               "cleanup_potential": "high" if size >= 5 else "low"})


# ---------------------------------------------------------------------------
# Tools — performance (scenario 2)
# ---------------------------------------------------------------------------

def get_performance_metrics() -> str:
    """Current CPU and RAM load of the endpoint."""
    if CTX.mode == "live":
        try:
            import psutil
            return _j({"cpu_pct": psutil.cpu_percent(interval=0.3),
                       "ram_pct": psutil.virtual_memory().percent})
        except ImportError:
            return _j({"error": "psutil niet beschikbaar"})
    p = CTX.state["performance"]
    return _j({"cpu_pct": p["cpu_pct"], "ram_pct": p["ram_pct"],
               "status": "CRITICAL" if max(p["cpu_pct"], p["ram_pct"]) >= 90 else
                         "WARNING" if max(p["cpu_pct"], p["ram_pct"]) >= 75 else "OK"})


def get_top_processes(top_n: int = 5) -> str:
    """Processes with the highest CPU/RAM usage."""
    procs = CTX.state["performance"].get("top_processes", [])[:top_n]
    return _j({"top_processes": procs})


def check_service_status(service_name: str) -> str:
    """Status of a Windows service (e.g. Spooler, RasMan, Dnscache)."""
    services = CTX.state.get("services", {})
    status = services.get(service_name, "unknown")
    return _j({"service": service_name, "status": status})


def get_pending_updates() -> str:
    """Number of pending Windows updates."""
    return _j({"pending_updates": CTX.state.get("pending_updates", 0)})


TOOL_REGISTRY = {
    f.__name__: f for f in [
        get_recent_logs, search_logs, get_system_info, get_uptime,
        get_disk_usage, list_large_files, get_temp_files_size,
        get_performance_metrics, get_top_processes, check_service_status,
        get_pending_updates,
    ]
}
