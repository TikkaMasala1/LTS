"""
Streamlit HitL interface — "The local troubleshooter" (Ultimum MSP).

Functional requirement 3 (PvA §1.7): the solution may not make changes on
its own. This interface shows the diagnosis, the proposed action and the
relevant context (including customer and user names, see final report DV1)
and requires EXPLICIT approval by a technician before the ticket is
created in Autotask.

Start:   streamlit run ui/app.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.agent import TroubleshooterAgent  # noqa: E402
from agent.backends import DirectBackend, McpBackend  # noqa: E402
from agent.llm_client import GeminiClient, MockLLM, OllamaClient  # noqa: E402
from agent.prompts import TICKET_TEMPLATE  # noqa: E402
from autotask.client import get_autotask_client  # noqa: E402
from mcp_server.filters.pii_filter import detect_leaks  # noqa: E402

AUDIT_LOG = ROOT / "data" / "hitl_audit.jsonl"
DATASET = ROOT / "evaluation" / "dataset" / "testcases.json"

st.set_page_config(page_title="LTS — Lokale Troubleshooter", page_icon="",
                   layout="wide")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@st.cache_data
def load_cases() -> list[dict]:
    if not DATASET.exists():
        from simulator.log_generator import generate_dataset
        generate_dataset(out_path=DATASET)
    return json.loads(DATASET.read_text(encoding="utf-8"))


def audit(event: str, payload: dict) -> None:
    """Append-only audit log of all HitL decisions (accountability)."""
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"),
                            "event": event, **payload}, ensure_ascii=False) + "\n")


def build_agent(use_mcp: bool, llm_choice: str, state: dict) -> TroubleshooterAgent:
    if use_mcp:
        tmp = ROOT / "data" / "_ui_state.json"
        tmp.write_text(json.dumps({"state": state}, ensure_ascii=False), encoding="utf-8")
        backend = McpBackend(state_file=str(tmp))
    else:
        backend = DirectBackend()
        backend.load_state(state)
    llm = {"Ollama (lokaal)": OllamaClient, "Mock (zonder GPU)": MockLLM,
           "Gemini API (tijdelijk)": GeminiClient}[llm_choice]()
    return TroubleshooterAgent(backend, llm)


# ---------------------------------------------------------------------------
# Sidebar — configuration
# ---------------------------------------------------------------------------

st.sidebar.title("LTS — Configuratie")
cases = load_cases()
labels = [f"{c['state']['case_id']} · {c['state']['hostname']} · "
          f"{c['state']['customer']}" for c in cases]
idx = st.sidebar.selectbox("Inkomend incident (testomgeving)",
                           range(len(cases)), format_func=lambda i: labels[i])
case = cases[idx]
state = case["state"]

use_mcp = st.sidebar.toggle("Via MCP-server (stdio)", value=True,
                            help="Uit = directe toolkit (sneller, zelfde tools)")
llm_choice = st.sidebar.radio(
    "LLM", ["Ollama (lokaal)", "Mock (zonder GPU)", "Gemini API (tijdelijk)"],
    help="Ollama = phi4-mini lokaal (PvA §2.3, env OLLAMA_MODEL). "
         "Gemini = cloud-vervanging voor tests zonder GPU (GEMINI_API_KEY vereist).")
if llm_choice == "Gemini API (tijdelijk)":
    st.sidebar.warning("Cloud-LLM: alleen voor de gesimuleerde testdataset. "
                       "Doorbreekt de datasoevereiniteit van de eindoplossing — "
                       "nooit met echte klantdata gebruiken.")

at_client = get_autotask_client()
st.sidebar.markdown(f"**Autotask-modus:** `{at_client.mode}`")
if at_client.mode == "mock":
    st.sidebar.caption("Geen sandbox-credentials in .env gevonden — "
                       "lokale mock actief (risicomaatregel PvA hfst. 4).")

# ---------------------------------------------------------------------------
# Main screen
# ---------------------------------------------------------------------------

st.title("De lokale troubleshooter")
st.caption("Veilige geautomatiseerde ondersteuning voor Managed Services — "
           "Proof of Concept · Ultimum MSP")

tab_diag, tab_drafts, tab_audit = st.tabs(
    ["Diagnose & goedkeuring", "Openstaande concepten", "Auditlog"])

# ====================== TAB 1: Diagnose & HitL ======================
with tab_diag:
    col_ctx, col_logs = st.columns([1, 2])
    with col_ctx:
        st.subheader("Context")
        st.markdown(
            f"**Endpoint:** `{state['hostname']}`  \n"
            f"**Klant:** {state['customer']}  \n"
            f"**Gebruiker:** {state['user']} (`{state['username']}`)  \n"
            f"**OS:** {state['os']} · uptime {state['uptime_days']} d")
    with col_logs:
        st.subheader("Recente logregels (ruw, vóór filtering)")
        st.code("\n".join(state["logs"][:8]), language="log")

    if st.button("Start diagnose", type="primary", use_container_width=True):
        with st.spinner("Agent verzamelt bewijs via MCP-tools…"):
            try:
                agent = build_agent(use_mcp, llm_choice, state)
                result = agent.diagnose(hostname=state["hostname"],
                                        customer=state["customer"],
                                        user=state["user"],
                                        trigger=state["logs"][-1])
                st.session_state["result"] = result.model_dump()
                st.session_state["case_id"] = state["case_id"]
                audit("diagnosis", {"case": state["case_id"],
                                    "scenario": result.diagnosis.scenario,
                                    "latency_s": round(result.latency_s, 2),
                                    "model": result.model})
            except Exception as exc:  # noqa: BLE001
                st.error(f"Diagnose mislukt: {exc}")

    result = st.session_state.get("result")
    if result and st.session_state.get("case_id") == state["case_id"]:
        d = result["diagnosis"]
        st.divider()
        st.subheader("Diagnose van de agent")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Scenario", d["scenario"])
        m2.metric("Confidence", f"{d['confidence']:.0%}")
        m3.metric("Latency", f"{result['latency_s']:.1f} s",
                  delta="< 30 s eis" if result["latency_s"] < 30 else "boven eis",
                  delta_color="normal" if result["latency_s"] < 30 else "inverse")
        leaks = detect_leaks(result["llm_input_transcript"])
        m4.metric("PII richting LLM", "0 lekken" if not leaks else f"{len(leaks)} ")

        st.markdown(f"**Oorzaak:** {d['root_cause']}")
        st.markdown(f"**Voorgestelde actie:** `{d['proposed_action']}` — "
                    f"{d['action_details']}")

        with st.expander("Bewijs (bronnen per conclusie — anti-hallucinatie)"):
            for ev in d["evidence"]:
                st.markdown(f"- **{ev['tool']}** → {ev['finding']}")
            st.caption("Aangeroepen tools: " +
                       ", ".join(t["name"] for t in result["tool_calls"]))

        st.divider()
        st.subheader("Human-in-the-Loop beslissing")
        st.info("De agent voert **niets** uit. Pas na jouw goedkeuring wordt het "
                "ticket in Autotask aangemaakt (functionele eis 3: 100% expliciete "
                "menselijke goedkeuring).")
        approver = st.text_input("Naam technicus", value="", placeholder="bijv. S. Bakker")
        feedback = st.text_area("Opmerking (optioneel, gebruikt voor kwalitatieve "
                                "evaluatie)", "")
        c_ok, c_no = st.columns(2)
        with c_ok:
            if st.button("Goedkeuren → ticket aanmaken", type="primary",
                         use_container_width=True, disabled=not approver):
                evidence_lines = "\n".join(f"  - {e['tool']}: {e['finding']}"
                                           for e in d["evidence"])
                body = TICKET_TEMPLATE.format(
                    hostname=state["hostname"], customer=state["customer"],
                    user=state["user"], scenario=d["scenario"],
                    root_cause=d["root_cause"], proposed_action=d["proposed_action"],
                    action_details=d["action_details"], confidence=d["confidence"],
                    evidence_lines=evidence_lines, latency=result["latency_s"],
                    model=result["model"])
                ticket = at_client.create_ticket(
                    title=f"[LTS] {d['scenario']} op {state['hostname']} "
                          f"({state['customer']})",
                    description=body,
                    priority="High" if d["scenario"] != "healthy" else "Low")
                audit("approved", {"case": state["case_id"], "approver": approver,
                                   "ticket": ticket.get("ticketNumber"),
                                   "feedback": feedback})
                st.success(f"Ticket **{ticket.get('ticketNumber')}** aangemaakt in "
                           f"Autotask ({at_client.mode}).")
        with c_no:
            if st.button("Afwijzen (geen wijziging)", use_container_width=True,
                         disabled=not approver):
                audit("rejected", {"case": state["case_id"], "approver": approver,
                                   "feedback": feedback})
                st.warning("Diagnose afgewezen en gelogd. Er is niets gewijzigd.")

# ====================== TAB 2: Draft tickets ======================
with tab_drafts:
    st.subheader("Concept-tickets in de HitL-wachtrij")
    drafts = at_client.list_drafts("PENDING_HUMAN_APPROVAL")
    if not drafts:
        st.caption("Geen openstaande concepten.")
    for dft in drafts:
        with st.container(border=True):
            st.markdown(f"**{dft['title']}** · `{dft['draft_id']}` · "
                        f"{dft['created_at']}")
            st.text(dft["description"][:600])
            a, b = st.columns(2)
            if a.button("Goedkeuren", key=f"a{dft['draft_id']}"):
                res = at_client.resolve_draft(dft["draft_id"], True, "UI")
                st.success(f"Aangemaakt: {res['ticket']['ticketNumber']}")
                st.rerun()
            if b.button("Afwijzen", key=f"r{dft['draft_id']}"):
                at_client.resolve_draft(dft["draft_id"], False, "UI")
                st.rerun()

# ====================== TAB 3: Audit log ======================
with tab_audit:
    st.subheader("Auditlog (append-only, verantwoordingsplicht)")
    if AUDIT_LOG.exists():
        lines = AUDIT_LOG.read_text(encoding="utf-8").strip().splitlines()
        for line in reversed(lines[-50:]):
            st.code(line, language="json")
    else:
        st.caption("Nog geen HitL-beslissingen gelogd.")
