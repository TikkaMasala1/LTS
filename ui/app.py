"""
Streamlit HitL interface — "The local troubleshooter" (Ultimum MSP).

Functional requirement 3 (PvA §1.7): the solution may not make changes on
its own. This interface shows the diagnosis and the proposed action and
requires explicit approval by a technician.

Start:   streamlit run ui/app.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.agent import TroubleshooterAgent  # noqa: E402
from agent.backends import DirectBackend, McpBackend  # noqa: E402
from agent.llm_client import GeminiClient, MockLLM, OllamaClient  # noqa: E402

DATASET = ROOT / "evaluation" / "dataset" / "testcases.json"

st.set_page_config(page_title="LTS — Lokale Troubleshooter", page_icon="",
                   layout="wide")


@st.cache_data
def load_cases() -> list[dict]:
    if not DATASET.exists():
        from simulator.log_generator import generate_dataset
        generate_dataset(out_path=DATASET)
    return json.loads(DATASET.read_text(encoding="utf-8"))


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
    "LLM", ["Ollama (lokaal)", "Mock (zonder GPU)", "Gemini API (tijdelijk)"])

st.title("De lokale troubleshooter")
st.caption("Veilige geautomatiseerde ondersteuning voor Managed Services — "
           "Proof of Concept · Ultimum MSP")

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
        except Exception as exc:  # noqa: BLE001
            st.error(f"Diagnose mislukt: {exc}")

result = st.session_state.get("result")
if result:
    d = result["diagnosis"]
    st.divider()
    st.subheader("Diagnose van de agent")
    st.markdown(f"**Scenario:** `{d['scenario']}` · confidence {d['confidence']:.0%} "
                f"· {result['latency_s']:.1f}s")
    st.markdown(f"**Oorzaak:** {d['root_cause']}")
    st.markdown(f"**Voorgestelde actie:** `{d['proposed_action']}` — "
                f"{d['action_details']}")
