"""
Unit and integration tests for the LTS PoC.

Run:  pytest -q
Coverage:  PII filter (security requirement), Autotask HitL draft flow (functional eis 3).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    """Each test gets its own data directory (mock Autotask & drafts)."""
    monkeypatch.setenv("LTS_DATA_DIR", str(tmp_path))
    import autotask.client as ac
    monkeypatch.setattr(ac, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ac, "MOCK_DB", tmp_path / "mock_autotask.json")
    monkeypatch.setattr(ac, "DRAFTS_DB", tmp_path / "pending_drafts.json")
    yield


# ---------------------------------------------------------------------------
# PII filter (quality criterion: 0% leak, names stay visible)
# ---------------------------------------------------------------------------

class TestPIIFilter:
    def setup_method(self):
        from mcp_server.filters.pii_filter import PIIFilter
        self.f = PIIFilter()

    def test_password_removed(self):
        out, rep = self.f.filter_text("login password=Geheim123! by j.devries")
        assert "Geheim123" not in out and rep.passwords == 1

    def test_api_key_and_bearer_removed(self):
        text = "api_key=deadbeefcafebabe1234567890abcdef Authorization: Bearer abc.def"
        out, rep = self.f.filter_text(text)
        assert "deadbeef" not in out and rep.secrets >= 1

    def test_valid_bsn_removed_invalid_kept(self):
        out, rep = self.f.filter_text("BSN 111222333 en ordernr 123456789")
        assert "111222333" not in out and rep.bsn == 1
        assert "123456789" in out  # fails the 11-test -> not a BSN

    def test_iban_removed(self):
        out, rep = self.f.filter_text("IBAN NL91ABNA0417164300 betaald")
        assert "NL91ABNA" not in out and rep.iban == 1

    def test_public_ip_masked_private_kept(self):
        out, rep = self.f.filter_text("egress 83.12.34.56 via lan 10.0.5.20")
        assert "83.12.x.x" in out and "10.0.5.20" in out
        assert rep.public_ips == 1

    def test_names_are_kept(self):
        """Explicit design choice (final report DV1): names are kept."""
        out, _ = self.f.filter_text("Gebruiker Sanne de Vries (Acme B.V.) meldt storing")
        assert "Sanne de Vries" in out and "Acme B.V." in out

    def test_leak_detector_clean_after_filter(self):
        from mcp_server.filters.pii_filter import detect_leaks
        dirty = ("password=X token=abcdef123456 BSN 111222333 "
                 "IBAN NL91ABNA0417164300")
        cleaned, _ = self.f.filter_text(dirty)
        assert detect_leaks(dirty) and not detect_leaks(cleaned)


# ---------------------------------------------------------------------------
# Autotask: HitL draft flow (functional requirement 3)
# ---------------------------------------------------------------------------

class TestAutotaskHitL:
    def test_draft_is_not_a_ticket(self):
        from autotask.client import MockAutotaskClient
        at = MockAutotaskClient()
        at.draft_ticket("Test", "beschrijving")
        assert at.search_tickets("ALL") == []  # nothing created without approval
        assert len(at.list_drafts()) == 1

    def test_approve_creates_ticket(self):
        from autotask.client import MockAutotaskClient
        at = MockAutotaskClient()
        d = at.draft_ticket("Disk vol op WS-ACME-42", "details")
        res = at.resolve_draft(d["draft_id"], approved=True, approver="S. Bakker")
        assert res["status"] == "APPROVED"
        assert res["ticket"]["ticketNumber"].startswith("T2026")
        assert len(at.search_tickets("open")) == 1

    def test_reject_creates_nothing(self):
        from autotask.client import MockAutotaskClient
        at = MockAutotaskClient()
        d = at.draft_ticket("x", "y")
        res = at.resolve_draft(d["draft_id"], approved=False, approver="S. Bakker",
                               feedback="diagnose onjuist")
        assert res["status"] == "REJECTED" and at.search_tickets("ALL") == []

