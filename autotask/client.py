"""
Autotask PSA REST API integration (sandbox).

Security measures (see PvA §3.2.2 and final report DV2):
  - Authentication via API tracking identifier with *least privilege* rights
    (only Tickets: read/create in the sandbox tenant).
  - Secrets only via environment variables (.env), never hardcoded.
  - Exponential backoff on rate limits (HTTP 429) and server errors (5xx),
    following the risk mitigation from chapter 4 of the PvA.
  - Mock mode: if there are no sandbox credentials, the client simulates the
    API locally (tickets in data/mock_autotask.json), so development and
    evaluation can continue when the sandbox is offline (risk mitigation).

Write actions ALWAYS go through a draft + explicit HitL approval.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

try:
    import httpx
except ImportError:  # mock mode also works without httpx
    httpx = None  # type: ignore

DATA_DIR = Path(os.environ.get("LTS_DATA_DIR", "data"))
MOCK_DB = DATA_DIR / "mock_autotask.json"
DRAFTS_DB = DATA_DIR / "pending_drafts.json"

PRIORITY_MAP = {"Critical": 4, "High": 1, "Medium": 2, "Low": 3}


def _load(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def _save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


class AutotaskError(RuntimeError):
    pass


class BaseAutotaskClient:
    """Shared draft flow (HitL) for both the real and mock client."""

    # ---- Draft queue (Human-in-the-Loop) ----------------------------

    def draft_ticket(self, title: str, description: str,
                     priority: str = "Medium", queue: str = "Managed Services") -> dict:
        drafts = _load(DRAFTS_DB, [])
        draft = {
            "draft_id": f"D-{uuid.uuid4().hex[:8]}",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "title": title, "description": description,
            "priority": priority, "queue": queue,
            "status": "PENDING_HUMAN_APPROVAL",
        }
        drafts.append(draft)
        _save(DRAFTS_DB, drafts)
        return draft

    def list_drafts(self, status: str = "PENDING_HUMAN_APPROVAL") -> list[dict]:
        return [d for d in _load(DRAFTS_DB, []) if status in ("ALL", d["status"])]

    def resolve_draft(self, draft_id: str, approved: bool, approver: str,
                      feedback: str = "") -> dict:
        """Approve => ticket is actually created; reject => only logged."""
        drafts = _load(DRAFTS_DB, [])
        for d in drafts:
            if d["draft_id"] == draft_id:
                d["resolved_at"] = datetime.now().isoformat(timespec="seconds")
                d["approver"] = approver
                d["feedback"] = feedback
                if approved:
                    ticket = self.create_ticket(d["title"], d["description"],
                                                d["priority"], d["queue"])
                    d["status"] = "APPROVED"
                    d["ticket"] = ticket
                else:
                    d["status"] = "REJECTED"
                _save(DRAFTS_DB, drafts)
                return d
        raise AutotaskError(f"Draft {draft_id} niet gevonden")

    # ---- JSON wrappers for MCP tools -----------------------------------

    def search_tickets_json(self, status: str, max_results: int) -> str:
        return json.dumps(self.search_tickets(status, max_results),
                          ensure_ascii=False, indent=2)

    def get_ticket_json(self, ticket_id: str) -> str:
        return json.dumps(self.get_ticket(ticket_id), ensure_ascii=False, indent=2)

    def draft_ticket_json(self, **kwargs) -> str:
        return json.dumps(self.draft_ticket(**kwargs), ensure_ascii=False, indent=2)

    # ---- To be implemented by subclasses --------------------------------

    def search_tickets(self, status: str, max_results: int) -> list[dict]:
        raise NotImplementedError

    def get_ticket(self, ticket_id: str) -> dict:
        raise NotImplementedError

    def create_ticket(self, title: str, description: str,
                      priority: str, queue: str) -> dict:
        raise NotImplementedError


class MockAutotaskClient(BaseAutotaskClient):
    """Local simulation of the Autotask sandbox (offline development/evaluation)."""

    mode = "mock"

    def __init__(self) -> None:
        if not MOCK_DB.exists():
            _save(MOCK_DB, {"next_number": 1001, "tickets": []})

    def search_tickets(self, status: str = "open", max_results: int = 10) -> list[dict]:
        db = _load(MOCK_DB, {"tickets": []})
        tickets = db["tickets"]
        if status != "ALL":
            tickets = [t for t in tickets if t["status"].lower() == status.lower()]
        return tickets[-max_results:]

    def get_ticket(self, ticket_id: str) -> dict:
        for t in _load(MOCK_DB, {"tickets": []})["tickets"]:
            if t["ticketNumber"] == ticket_id or str(t["id"]) == str(ticket_id):
                return t
        raise AutotaskError(f"Ticket {ticket_id} niet gevonden")

    def create_ticket(self, title: str, description: str,
                      priority: str = "Medium", queue: str = "Managed Services") -> dict:
        db = _load(MOCK_DB, {"next_number": 1001, "tickets": []})
        n = db["next_number"]
        ticket = {
            "id": n,
            "ticketNumber": f"T2026{n:04d}",
            "title": title[:255],
            "description": description,
            "priority": priority,
            "queue": queue,
            "status": "open",
            "createDate": datetime.now().isoformat(timespec="seconds"),
            "source": "LTS-agent (HitL approved)",
        }
        db["tickets"].append(ticket)
        db["next_number"] = n + 1
        _save(MOCK_DB, db)
        return ticket


class SandboxAutotaskClient(BaseAutotaskClient):
    """Real Autotask REST API client (sandbox tenant)."""

    mode = "sandbox"
    MAX_RETRIES = 5

    def __init__(self, base_url: str, integration_code: str,
                 username: str, secret: str) -> None:
        if httpx is None:
            raise AutotaskError("httpx is vereist voor sandbox-modus (pip install httpx)")
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "ApiIntegrationCode": integration_code,
            "UserName": username,
            "Secret": secret,
            "Content-Type": "application/json",
        }

    # -- HTTP with exponential backoff (risk mitigation for API rate limits) ----

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.base_url}{path}"
        delay = 1.0
        last_exc: Exception | None = None
        for attempt in range(self.MAX_RETRIES):
            try:
                resp = httpx.request(method, url, headers=self.headers,
                                     timeout=20.0, **kwargs)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise AutotaskError(f"HTTP {resp.status_code}")
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:  # noqa: BLE001 — intentionally broad: retry path
                last_exc = exc
                time.sleep(delay)
                delay = min(delay * 2, 30)  # 1, 2, 4, 8, 16 s
        raise AutotaskError(f"Autotask onbereikbaar na {self.MAX_RETRIES} pogingen: {last_exc}")

    # -- API operations ------------------------------------------------------

    def search_tickets(self, status: str = "open", max_results: int = 10) -> list[dict]:
        query = {"MaxRecords": max_results,
                 "filter": [{"op": "noteq", "field": "Status", "value": 5}]
                 if status == "open" else []}
        data = self._request("POST", "/v1.0/Tickets/query", json=query)
        return data.get("items", [])

    def get_ticket(self, ticket_id: str) -> dict:
        data = self._request("GET", f"/v1.0/Tickets/{ticket_id}")
        return data.get("item", data)

    def create_ticket(self, title: str, description: str,
                      priority: str = "Medium", queue: str = "Managed Services") -> dict:
        body = {
            "title": title[:255],
            "description": description[:8000],
            "priority": PRIORITY_MAP.get(priority, 2),
            "status": 1,  # New
            "companyID": int(os.environ.get("AUTOTASK_COMPANY_ID", "0")),
            "queueID": int(os.environ.get("AUTOTASK_QUEUE_ID", "0")),
        }
        data = self._request("POST", "/v1.0/Tickets", json=body)
        return {"id": data.get("itemId"), "ticketNumber": str(data.get("itemId")),
                "title": title, "status": "open"}


def get_autotask_client() -> BaseAutotaskClient:
    """Factory: sandbox client if credentials are present, otherwise mock."""
    base = os.environ.get("AUTOTASK_BASE_URL")
    code = os.environ.get("AUTOTASK_INTEGRATION_CODE")
    user = os.environ.get("AUTOTASK_API_USER")
    secret = os.environ.get("AUTOTASK_API_SECRET")
    if all([base, code, user, secret]):
        return SandboxAutotaskClient(base, code, user, secret)
    return MockAutotaskClient()
