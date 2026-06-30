"""
Shared helpers for the LTS Streamlit apps (service + user portal).
Enables the user app and service app to run side-by-side and interact
via a live incidents queue + shared Autotask draft/ticket state.
"""
from __future__ import annotations

import json
import random
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from simulator.log_generator import make_case  # noqa: E402

LIVE_INCIDENTS = ROOT / "data" / "live_incidents.json"
AUDIT_LOG = ROOT / "data" / "hitl_audit.jsonl"


def _load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_test_cases() -> list[dict]:
    """Load the static evaluation test dataset."""
    dataset = ROOT / "evaluation" / "dataset" / "testcases.json"
    if not dataset.exists():
        from simulator.log_generator import generate_dataset
        generate_dataset(out_path=dataset)
    return _load_json(dataset, [])


def load_live_incidents() -> list[dict]:
    """Load dynamically reported incidents from the user portal."""
    return _load_json(LIVE_INCIDENTS, [])


def save_live_incidents(incidents: list[dict]) -> None:
    _save_json(LIVE_INCIDENTS, incidents)


def generate_live_case(scenario: str, customer: str, user: str, hostname: str | None = None,
                       seed: int | None = None) -> dict:
    """Create a realistic incident state using the simulator, then override fields."""
    rng = random.Random(seed if seed is not None else datetime.now().microsecond)
    case = make_case(999, scenario, rng)  # base id overwritten below
    state = case["state"]

    # Override with user-provided values
    state["customer"] = customer
    state["user"] = user
    state["username"] = (user[0] + "." + user.split()[-1].replace(" ", "")).lower() if " " in user else user.lower()
    if hostname:
        state["hostname"] = hostname
    else:
        prefix = customer.split()[0].upper()[:5]
        state["hostname"] = f"WS-{prefix}-LIVE"

    # Mark as live user-reported
    state["case_id"] = f"LIVE-{datetime.now().strftime('%H%M%S')}"
    state["source"] = "user_portal"
    state["submitted_at"] = datetime.now().isoformat(timespec="seconds")

    # Keep ground truth for consistency (but UI rarely uses it)
    return case


def add_live_incident(scenario: str, customer: str, user: str, hostname: str | None = None) -> dict:
    """Generate + persist a new live incident. Returns the case dict."""
    case = generate_live_case(scenario, customer, user, hostname)
    incidents = load_live_incidents()
    incidents.append(case)
    save_live_incidents(incidents)
    return case


def get_all_incidents(include_test: bool = True) -> list[dict]:
    """Return live incidents + (optionally) test cases. Live appended last."""
    cases = []
    if include_test:
        cases.extend(load_test_cases())
    cases.extend(load_live_incidents())
    return cases


def clear_live_incidents() -> None:
    save_live_incidents([])


# ---------------------------------------------------------------------------
# Status helpers (used by user portal to show progress against service app)
# ---------------------------------------------------------------------------

def get_autotask_client():
    # Late import to avoid circulars
    from autotask.client import get_autotask_client as _get
    return _get()


def find_status_for_incident(state: dict) -> dict:
    """
    Determine current status of a reported incident by inspecting
    pending drafts + created (mock) tickets. Matches on hostname.
    """
    hostname = state.get("hostname", "")
    case_id = state.get("case_id", "")
    client = get_autotask_client()

    # 1. Check pending drafts (created by service app HitL)
    drafts = client.list_drafts("ALL")
    for d in drafts:
        if hostname and hostname in d.get("title", ""):
            status = d["status"]
            label = {
                "PENDING_HUMAN_APPROVAL": "Wacht op goedkeuring technicus",
                "APPROVED": "Goedgekeurd en uitgevoerd op host",
                "REJECTED": "Afgewezen — ticket voor handmatige afhandeling",
            }.get(status, status)
            info = {
                "status": status,
                "label": label,
                "draft_id": d["draft_id"],
                "title": d["title"],
                "updated": d.get("resolved_at") or d.get("created_at"),
            }
            if d.get("ticket"):
                t = d["ticket"] if isinstance(d["ticket"], dict) else {}
                tnum = t.get("ticketNumber") or t.get("id")
                if tnum:
                    info["ticket"] = tnum
            return info

    # 2. Check created tickets (after approval)
    try:
        tickets = client.search_tickets("ALL", max_results=50)
    except Exception:
        tickets = []
    for t in tickets:
        title = t.get("title", "") or t.get("Title", "")
        if hostname and hostname in title:
            return {
                "status": "RESOLVED",
                "label": "Ticket aangemaakt",
                "ticket": t.get("ticketNumber") or t.get("id"),
                "title": title,
                "created": t.get("createDate") or t.get("CreateDate"),
            }

    # 3. Nothing yet
    return {
        "status": "SUBMITTED",
        "label": "Ingediend — wacht op verwerking door servicedesk",
        "updated": state.get("submitted_at", ""),
    }
