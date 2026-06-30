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
from mcp_server import toolkit as remediation_toolkit  # noqa: E402

from ui.shared import get_all_incidents, load_live_incidents  # noqa: E402

AUDIT_LOG = ROOT / "data" / "hitl_audit.jsonl"

st.set_page_config(page_title="LTS — Lokale Troubleshooter", page_icon="",
                   layout="wide")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_cases() -> list[dict]:
    # Combined: live user-reported incidents first (interact with user_app.py), then test dataset
    return get_all_incidents(include_test=True)


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

live = load_live_incidents()
st.sidebar.caption(f"Live incidenten (van gebruikersportaal): **{len(live)}**")
if st.sidebar.button("Vernieuw incidenten", use_container_width=True):
    st.rerun()

cases = load_cases()
labels = []
for c in cases:
    src = "LIVE" if c.get("state", {}).get("source") == "user_portal" else "TEST"
    labels.append(f"[{src}] {c['state']['case_id']} · {c['state']['hostname']} · {c['state']['customer']}")

idx = st.sidebar.selectbox("Inkomend incident",
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
           "Service / Technicus UI  ·  (draai naast ui/user_app.py) · Ultimum MSP")

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
        st.code("\n".join(state["logs"][-8:]), language="log")

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
        st.info("De agent voert **niets** uit. Pas na jouw expliciete goedkeuring wordt "
                "het ticket aangemaakt **én** de voorgestelde actie uitgevoerd op de host machine.")
        approver = st.text_input("Naam technicus", value="", placeholder="bijv. S. Bakker")
        feedback = st.text_area("Opmerking (optioneel, gebruikt voor kwalitatieve "
                                "evaluatie)", "")
        c_ok, c_no = st.columns(2)
        with c_ok:
            if st.button("Goedkeuren → ticket + uitvoeren op host", type="primary",
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

                # 1. Create the ticket (success path)
                ticket = at_client.create_ticket(
                    title=f"[LTS] {d['scenario']} op {state['hostname']} "
                          f"({state['customer']})",
                    description=body,
                    priority="High" if d["scenario"] != "healthy" else "Low")

                # 2. EXECUTE the proposed remediation on the host machine (simulated state)
                exec_result = None
                try:
                    remediation_toolkit.CTX.load_state(state)
                    exec_result = remediation_toolkit.execute_remediation(
                        d["proposed_action"],
                        target=d.get("action_details", ""),
                        reason=d.get("root_cause", "")
                    )
                except Exception as ex:  # noqa: BLE001
                    exec_result = json.dumps({"error": str(ex)}, ensure_ascii=False)

                audit("approved", {"case": state["case_id"], "approver": approver,
                                   "ticket": ticket.get("ticketNumber"),
                                   "feedback": feedback,
                                   "executed": d["proposed_action"]})

                st.success(f"Ticket **{ticket.get('ticketNumber')}** aangemaakt in "
                           f"Autotask ({at_client.mode}).")
                st.success("Remediation uitgevoerd op de host machine (simulatie).")
                with st.expander("Uitvoeringsresultaat (host state na actie)"):
                    st.code(exec_result or "{}", language="json")

        with c_no:
            if st.button("Afwijzen", use_container_width=True,
                         disabled=not approver):
                evidence_lines = "\n".join(f"  - {e['tool']}: {e['finding']}"
                                           for e in d["evidence"])
                body = TICKET_TEMPLATE.format(
                    hostname=state["hostname"], customer=state["customer"],
                    user=state["user"], scenario=d["scenario"],
                    root_cause=d["root_cause"], proposed_action=d["proposed_action"],
                    action_details=d["action_details"], confidence=d["confidence"],
                    evidence_lines=evidence_lines, latency=result["latency_s"],
                    model=result["model"])

                # Afwijzen: create a ticket anyway, but leave it for manual pickup ("new")
                # Do NOT execute any remediation.
                manual_title = f"[MANUAL] {d['scenario']} op {state['hostname']} ({state['customer']})"
                manual_body = (
                    "LTS diagnose werd AFGEWEZEN door de technicus.\n"
                    "Geen automatische actie uitgevoerd op de host.\n\n"
                    "--- LTS diagnose (ter info) ---\n" + body +
                    "\n\nTechnicus feedback: " + (feedback or "(geen opmerking)") +
                    "\n\nDeze ticket is aangemaakt zodat een technicus het handmatig kan oppakken."
                )
                ticket = at_client.create_ticket(
                    title=manual_title,
                    description=manual_body,
                    priority="Medium")

                audit("rejected", {"case": state["case_id"], "approver": approver,
                                   "ticket": ticket.get("ticketNumber"),
                                   "feedback": feedback,
                                   "note": "ticket_created_for_manual_handling"})

                st.info(f"Ticket **{ticket.get('ticketNumber')}** aangemaakt (status: new/open) "
                        "voor handmatige afhandeling. Geen actie uitgevoerd op de host.")
                st.warning("Diagnose afgewezen — ticket overgedragen aan menselijke technicus.")

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
                tnum = (res.get("ticket") or {}).get("ticketNumber") if isinstance(res.get("ticket"), dict) else res.get("ticket")
                st.success(f"Ticket aangemaakt + (indien van toepassing) actie uitgevoerd: {tnum}")
                st.rerun()
            if b.button("Afwijzen", key=f"r{dft['draft_id']}"):
                res = at_client.resolve_draft(dft["draft_id"], False, "UI")
                tnum = (res.get("ticket") or {}).get("ticketNumber") if isinstance(res.get("ticket"), dict) else res.get("ticket")
                st.info(f"Ticket voor handmatige opvolging aangemaakt: {tnum} (geen auto-actie)")
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
