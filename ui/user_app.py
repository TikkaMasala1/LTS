"""
LTS User Portal (eindgebruiker / klantzijde).

Draait naast de servicedesk HitL-app (ui/app.py).
Interactie:
- Melden van een incident → verschijnt direct als [LIVE] entry in de technicus-app.
- Status volgen: ingediend → in diagnose → wacht op goedkeuring → ticket aangemaakt.
- Gebruik dezelfde shared data (live_incidents + pending_drafts + mock_autotask).

Start (naast service app):
    streamlit run ui/user_app.py --server.port 8502
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ui.shared import (
    add_live_incident,
    find_status_for_incident,
    load_live_incidents,
    get_autotask_client,
)

st.set_page_config(
    page_title="LTS — Gebruikersportaal",
    page_icon="🧑‍💻",
    layout="wide"
)

SCENARIO_LABELS = {
    "disk_space": "💾 Schijfruimte — bijna vol (C: drive)",
    "performance": "🐢 Performance — systeem is traag",
    "vpn": "🌐 VPN traag / onstabiel",
    "healthy": "✅ Gezond (testcase — geen echt probleem)",
}

at_client = get_autotask_client()


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

st.sidebar.title("LTS — Gebruikersportaal")
st.sidebar.markdown(
    "Meld uw IT-probleem. De **lokale troubleshooter** (technicus) "
    "analyseert het veilig en lokaal. U krijgt een ticketnummer als het "
    "wordt goedgekeurd."
)
st.sidebar.divider()
st.sidebar.markdown("**Naast de servicedesk-app draaien:**")
st.sidebar.code("streamlit run ui/app.py --server.port 8501", language="bash")
st.sidebar.caption("Open de technicus-UI op poort 8501 om live incidenten te verwerken.")

if st.sidebar.button("🔄 Vernieuw status", use_container_width=True):
    st.rerun()

st.sidebar.divider()
if st.sidebar.button("🗑️ Wis alle live incidenten (demo)", use_container_width=True):
    from ui.shared import clear_live_incidents
    clear_live_incidents()
    st.sidebar.success("Live incidenten gewist.")
    st.rerun()

st.sidebar.caption(f"Autotask: `{at_client.mode}`")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

st.title("LTS Gebruikersportaal")
st.caption("Veilige geautomatiseerde ondersteuning • Ultimum MSP • Human-in-the-Loop")

tab_report, tab_my, tab_info = st.tabs([
    "📝 Probleem melden",
    "📋 Mijn incidenten & status",
    "ℹ️ Hoe werkt het?"
])

# ====================== REPORT TAB ======================
with tab_report:
    st.subheader("Meld een nieuw incident")

    c1, c2 = st.columns([1, 1])

    with c1:
        scenario_key = st.selectbox(
            "Categorie / symptoom",
            list(SCENARIO_LABELS.keys()),
            format_func=lambda k: SCENARIO_LABELS[k],
            index=0
        )
        customer = st.text_input("Klant / organisatie", value="Acme B.V.")
        user_name = st.text_input("Uw naam", value="Jan de Vries")
        hostname = st.text_input(
            "Hostname / computer (optioneel)",
            placeholder="WS-ACME-42 of laat leeg voor auto"
        )

    with c2:
        st.markdown("**Korte beschrijving (optioneel)**")
        default_desc = {
            "disk_space": "Mijn schijf is bijna vol, ik krijg foutmeldingen bij opslaan.",
            "performance": "De computer is extreem traag, vooral Teams en ERP.",
            "vpn": "VPN is traag, RDP valt steeds weg.",
            "healthy": "Gewoon een testmelding (geen echt probleem).",
        }[scenario_key]
        description = st.text_area("Beschrijving", value=default_desc, height=120)

        st.info("Uw melding wordt omgezet in een realistische machine-state (logs, metrics) "
                "en direct zichtbaar voor de servicedesk.")

    if st.button("🚀 Incident indienen voor LTS-analyse", type="primary", use_container_width=True):
        if not customer.strip() or not user_name.strip():
            st.error("Klant en naam zijn verplicht.")
        else:
            h = hostname.strip() or None
            case = add_live_incident(scenario_key, customer.strip(), user_name.strip(), h)
            stt = case["state"]
            st.success(
                f"Incident **{stt['case_id']}** ingediend voor **{stt['hostname']}** "
                f"({stt['customer']}).\n\n"
                "Ga nu naar de **servicedesk app** (technicus), selecteer het [LIVE] item "
                "en start de diagnose."
            )
            st.json({"case_id": stt["case_id"], "hostname": stt["hostname"], "user": stt["user"]})
            st.session_state["last_live_case"] = stt["case_id"]
            # Show quick tip
            st.caption("Tip: Vernieuw de servicedesk-app om het direct te zien.")

# ====================== MY INCIDENTS TAB ======================
with tab_my:
    st.subheader("Status van uw gemelde incidenten")

    incidents = load_live_incidents()
    if not incidents:
        st.info("Nog geen incidenten gemeld via dit portaal. Gebruik de 'Probleem melden' tab.")
    else:
        # Show most recent first
        for inc in sorted(incidents, key=lambda x: x["state"].get("submitted_at", ""), reverse=True)[:15]:
            stt = inc["state"]
            status = find_status_for_incident(stt)

            with st.container(border=True):
                header = f"**{stt['case_id']}** · `{stt['hostname']}` · {stt['customer']} · {stt['user']}"
                st.markdown(header)
                st.caption(f"Ingediend: {stt.get('submitted_at', 'onbekend')}")

                # Status badge
                color = {
                    "SUBMITTED": "orange",
                    "PENDING_HUMAN_APPROVAL": "blue",
                    "APPROVED": "green",
                    "REJECTED": "red",
                    "RESOLVED": "green",
                }.get(status["status"], "gray")

                st.markdown(
                    f"**Status:** :{color}[{status['label']}]"
                )

                if status["status"] == "APPROVED":
                    st.caption("✅ Automatische actie is uitgevoerd op de host (gesimuleerd).")
                elif status["status"] == "REJECTED":
                    st.caption("ℹ️ Ticket overgedragen voor handmatige behandeling door een technicus.")

                cols = st.columns([1, 1, 1])
                with cols[0]:
                    if st.button("🔍 Bekijk logs & context", key=f"logs_{stt['case_id']}"):
                        st.code("\n".join(stt.get("logs", [])[-6:]) or "(geen logs)", language="log")
                        st.json({
                            "disk": stt.get("disk"),
                            "performance": stt.get("performance"),
                            "vpn": stt.get("vpn"),
                        })

                with cols[1]:
                    if status.get("ticket"):
                        st.success(f"Ticket aangemaakt: **{status['ticket']}**")
                        try:
                            ticket = at_client.get_ticket(status["ticket"])
                            with st.expander("Ticket details (diagnose + actie)"):
                                st.text((ticket.get("description") or ticket.get("Description") or "")[:1200])
                        except Exception as ex:
                            st.caption(f"Kon ticket details niet laden: {ex}")

                with cols[2]:
                    if st.button("🔄 Status controleren", key=f"refresh_{stt['case_id']}"):
                        st.rerun()

                if status.get("draft_id"):
                    st.caption(f"Open concept (HitL): {status['draft_id']}")

    st.divider()
    st.caption("Status komt uit gedeelde data (drafts + tickets). Vernieuw om updates te zien "
               "nadat de technicus diagnose + goedkeuring heeft gedaan in de service app.")

# ====================== INFO TAB ======================
with tab_info:
    st.subheader("Hoe werkt de interactie tussen de apps?")
    st.markdown("""
1. **U meldt een probleem** in dit portaal → er wordt een realistische incident-state gegenereerd en opgeslagen.
2. **Technicus (servicedesk)** opent de LTS-app (`ui/app.py`), ziet uw melding als **[LIVE]** incident.
3. Technicus start de **diagnose** met de agent.
4. Technicus kiest:
   - **Goedkeuren**: ticket wordt aangemaakt **én** de actie wordt uitgevoerd op de host machine (simulatie).
   - **Afwijzen**: ticket wordt tóch aangemaakt (prefix [MANUAL]), maar zonder uitvoering — voor handmatige afhandeling.
5. **U** ziet de status updaten, inclusief of de actie uitgevoerd is of dat het handmatig moet worden opgepakt.

**Waarom twee apps?**
- Scheiding van rollen: eindgebruiker vs. technicus (volgens functionele eis).
- Demonstratie van real-time interactie via gedeelde bestanden (`data/live_incidents.json`, `pending_drafts.json`, `mock_autotask.json`).

**Alle verwerking blijft lokaal** (behalve optionele Gemini of sandbox Autotask).
""")

    st.divider()
    st.markdown("**Technicus app starten (andere terminal):**")
    st.code("streamlit run ui/app.py", language="bash")
    st.markdown("Open dan http://localhost:8501 en selecteer uw [LIVE] incident.")

    with st.expander("Technische details"):
        st.json({
            "shared_files": [
                "data/live_incidents.json (uw meldingen)",
                "data/pending_drafts.json (HitL wachtrij)",
                "data/mock_autotask.json (aangemaakte tickets)"
            ],
            "status_polling": "find_status_for_incident() matcht op hostname in titles",
        })
