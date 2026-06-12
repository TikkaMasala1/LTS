# De lokale troubleshooter (LTS) — Proof of Concept

**Veilige geautomatiseerde ondersteuning voor Managed Services**
Stageproject Taran Singh · Ultimum MSP B.V. · Hogeschool Utrecht (AI, derdejaars)

Een *secure-by-design*, volledig **lokale** AI-servicedeskagent op basis van het
**Model Context Protocol (MCP)**. De agent diagnosticeert zelfstandig drie
ondersteuningsscenario's — **schijfruimte**, **performance (traag systeem)** en
**trage VPN** — en maakt ná expliciete menselijke goedkeuring (Human-in-the-Loop)
een ticket aan in **Autotask**.

```
                ┌────────────────────────  endpoint / test-VM  ────────────────────────┐
                │                                                                      │
  logs/metrics ─┤  ┌──────────────┐   PII-filterlaag    ┌──────────────────────────┐   │
                │  │  Simulator / │ ─────────────────►  │  MCP-server (FastMCP)    │   │
                │  │  live psutil │  (selectief: wacht- │  15+ tools · resources · │   │
                │  └──────────────┘   woorden, tokens,  │  prompts                 │   │
                │                     BSN, IBAN, publ.  └─────────────┬────────────┘   │
                │                     IP's; namen       MCP (stdio)   │                │
                │                     blijven zichtbaar)              ▼                │
                │                                       ┌──────────────────────────┐   │
                │                                       │  Agent  ⇄  lokale LLM    │   │
                │                                       │  (Ollama · phi4-mini /   │   │
                │                                       │   qwen3:14b)             │   │
                │                                       └─────────────┬────────────┘   │
                └─────────────────────────────────────────────────────┼────────────────┘
                                                                      ▼
                                          ┌──────────────────────────────────────────┐
                                          │  Streamlit HitL-UI: technicus keurt      │
                                          │  diagnose + actie expliciet goed/af      │
                                          └─────────────┬────────────────────────────┘
                                                        ▼  (alleen ná goedkeuring)
                                          ┌──────────────────────────────────────────┐
                                          │  Autotask REST API (sandbox, least       │
                                          │  privilege, exponential backoff) of mock │
                                          └──────────────────────────────────────────┘
```

**Geen data verlaat het netwerk:** de LLM draait lokaal (Ollama). De PII-filterlaag
is een extra verdedigingslinie (defense in depth) die wachtwoorden, tokens/keys,
BSN, IBAN en publieke IP-adressen maskeert vóórdat logdata de LLM-context bereikt.
Gebruikers- en klantnamen blijven bewust zichtbaar (wens servicedesk; zie
eindverslag, deelvraag 1).

---

## 1. Installatie

Vereisten: [uv](https://docs.astral.sh/uv/) (regelt zelf Python 3.11+),
[Ollama](https://ollama.com) (voor het echte LLM).

```bash
uv sync                            # maakt .venv aan en installeert alles
uv sync --group dev                # idem, inclusief pytest

# Lokaal model (4 GB VRAM-scenario, zie PvA §2.3):
ollama pull phi4-mini
# Of voor het 16 GB-scenario:
ollama pull qwen3:14b   # daarna: export OLLAMA_MODEL=qwen3:14b
```

Alle commando's hieronder kunnen met `uv run` worden gedraaid zonder de
virtualenv te activeren, bijv. `uv run python -m simulator.log_generator` of
`uv run streamlit run ui/app.py`. Liever klassiek? `pip install -r
requirements.txt` werkt nog steeds (het bestand blijft als fallback bestaan;
`pyproject.toml` is leidend).

Autotask-sandbox (optioneel): kopieer `.env.example` naar `.env` en vul de
sandbox-credentials in. **Zonder credentials draait automatisch de mock-modus**
(risicomaatregel uit PvA hfst. 4: ontwikkeling gaat door als de sandbox offline is).

## 2. Testdataset genereren

```bash
python -m simulator.log_generator
# → evaluation/dataset/testcases.json (45 cases: 3×14 scenario's + 3 healthy,
#   incl. PII-canaries voor de securitymeting)
```

## 3. HitL-demo starten (Streamlit)

```bash
streamlit run ui/app.py
```

In de sidebar kies je een inkomend incident en of de agent via de **MCP-server**
(stdio) of de directe toolkit draait, en of je het echte lokale LLM of de
mock-LLM gebruikt (demo zonder GPU). De UI toont diagnose, confidence,
latency, bewijs per conclusie en de PII-audit; goedkeuren maakt het ticket aan
in Autotask, afwijzen logt alleen. Alle beslissingen komen in het auditlog
(`data/hitl_audit.jsonl`).

De MCP-server is ook los te starten/inspecteren:

```bash
LTS_MACHINE_STATE=data/_ui_state.json python -m mcp_server.server
# of interactief: npx @modelcontextprotocol/inspector python -m mcp_server.server
```

## 4. Evaluatie

**Kwantitatief** (meet de vijf metrics uit het eindverslag — accuracy ≥80%,
latency <30 s, hallucinatie ≤5%, tool-calling ≥90%, PII-lek 0%):

```bash
# Echte meting met het lokale LLM via de MCP-server:
python -m evaluation.quantitative --llm ollama --backend mcp

# Snelle pipeline-verificatie zonder GPU (mock-LLM):
python -m evaluation.quantitative
```

### Tijdelijke vervanging: Gemini API (zonder GPU)

Geen lokale GPU beschikbaar? Dan kan de agent tijdelijk via de Google Gemini
API draaien (OpenAI-compatibel endpoint, inclusief tool calling):

```bash
export GEMINI_API_KEY=...        # sleutel via https://aistudio.google.com
python -m evaluation.quantitative --llm gemini --backend mcp
# of in de UI: sidebar → LLM → "Gemini API (tijdelijk)"
```

> **Belangrijk:** dit doorbreekt bewust de datasoevereiniteit die centraal
> staat in deze PoC. Gebruik Gemini uitsluitend met de **gesimuleerde**
> testdataset, nooit met echte klantlogs. In deze modus is het PII-filter niet
> langer een extra verdedigingslaag maar de enige — de PII-lek-metric (0%-eis)
> blijft daarom ook hier van kracht. De metingen voor het eindverslag worden
> uitsluitend met het lokale model (Ollama) gerapporteerd.

Resultaten: `evaluation/results/results.csv` + `results.md` (kant-en-klare tabel).

**Kwalitatief** (demosessies met servicedeskmedewerkers):

1. Gebruik `evaluation/qualitative/feedback_form.md` tijdens de sessies.
2. Voer de antwoorden in als `evaluation/qualitative/feedback_responses.csv`
   (kolomindeling zoals `sample_feedback.csv`).
3. `python -m evaluation.analyze_feedback`
   → `evaluation/results/qualitative_summary.md` (Likert-statistiek + thema's
   + geanonimiseerde citaten).

## 5. Tests

```bash
pytest -q
```

Dekt het PII-filter (0% lek, namen blijven zichtbaar), de HitL-draftflow
(concept ≠ ticket; alleen goedkeuring maakt aan) en de end-to-end agent-pipeline
op alle scenario's.

## 6. Projectstructuur

```
mcp_server/
  server.py            MCP-server (FastMCP): 15+ tools, resources, prompt
  toolkit.py           Toolimplementaties (simulated/live), PII-gefilterd
  filters/pii_filter.py Selectieve filterlaag + lek-detector (audit)
agent/
  agent.py             Agentic loop, Pydantic-gevalideerde diagnose, audit-transcript
  llm_client.py        Ollama-client (phi4-mini/qwen3) + deterministische MockLLM
  backends.py          McpBackend (stdio) en DirectBackend (evaluatie/tests)
  prompts.py           Rolprompt, JSON-schema, anti-hallucinatie-instructies
autotask/client.py     Sandbox-client (backoff, least privilege) + mock + HitL-drafts
ui/app.py              Streamlit HitL-interface + auditlog
simulator/log_generator.py  Testomgeving: machine states + logs + PII-canaries
evaluation/            Kwantitatieve en kwalitatieve evaluatie + dataset
tests/test_poc.py      Unit-/integratietests
```

## 7. Beveiligingsmaatregelen (samengevat)

| Maatregel | Implementatie |
|---|---|
| Datasoevereiniteit | LLM draait 100% lokaal (Ollama); geen cloud-API's |
| Privacy by Design | Selectieve PII-filterlaag vóór elke LLM-context; lek-detector in evaluatie |
| Human-in-the-Loop | Agent kan alleen *voorstellen*; uitvoeren vereist expliciete goedkeuring (100%) |
| Least privilege | Autotask-API-key alleen Tickets read/create in sandbox |
| Fouttolerantie | Exponential backoff (1→30 s) bij 429/5xx; mock-fallback bij offline sandbox |
| Anti-hallucinatie | Bron verplicht per conclusie; "unknown" bij twijfel; verzonnen bron = hallucinatie in de meting |
| Verantwoording | Append-only auditlog van alle HitL-beslissingen |

## Troubleshooting: model roept geen tools aan

Symptoom: `tools_called` is leeg in `results.csv`, hallucinatie-rate schiet
omhoog en tool-calling accuracy is 0% — terwijl een deel van de diagnoses
"toevallig" klopt. Oorzaak: het model levert geen structured tool calls.
Bekend bij **phi4-mini in Ollama**: afhankelijk van de Ollama-versie en het
chat-template ontbreekt tool-ondersteuning, of zet het model zijn tool calls
als platte tekst in het antwoord (het `functools[{...}]`-formaat).

De agent bevat hiervoor twee vangnetten (zie `agent/agent.py`):
1. een fallback-parser die embedded/tekstuele tool calls herkent en alsnog
   uitvoert, en
2. een eenmalige "nudge" die het model terugstuurt als het een diagnose
   geeft zonder ook maar één tool te hebben aangeroepen.

Blijft het misgaan, controleer dan in deze volgorde:
1. `ollama show phi4-mini` → staat **tools** bij *Capabilities*? Zo niet:
   `ollama pull phi4-mini` opnieuw (template is later toegevoegd) en werk
   Ollama zelf bij naar de nieuwste versie.
2. Probeer een model met robuuste native tool calling als alternatief binnen
   het 4 GB-scenario: `OLLAMA_MODEL=qwen3:4b` (Qwen3-4B was de runner-up in
   de multicriteria-analyse, PvA §2.2.1). Voor het 16 GB-scenario:
   `OLLAMA_MODEL=qwen3:14b`.
3. Vergelijk de runs in `results.csv` (kolommen `tools_called` en `model`) —
   dit verschil tussen papieren MCA-keuze en praktijkvalidatie is bruikbaar
   bewijs voor deelvraag 4 in het eindverslag.

## Troubleshooting: extreme latency / timeouts met qwen3

Qwen3-modellen "denken" standaard hardop (thinking mode) vóór elke tool call;
in de praktijk kostte dat 100+ seconden per case en liep de HTTP-client tegen
zijn timeout aan. Maatregelen in de code:

- De `OllamaClient` schakelt thinking voor qwen3-modellen automatisch uit via
  Qwen's `/no_think`-soft-switch (terugzetten kan met `OLLAMA_THINK=1`) en
  stript eventuele `<think>`-blokken uit de respons.
- De HTTP-timeout is configureerbaar via `OLLAMA_TIMEOUT` (default 300 s) en
  een timeout geeft nu een eerlijke foutmelding (traag model ≠ onbereikbaar).
- De evaluatie is crash-bestendig: een mislukte case wordt als foutregel in
  `results.csv` opgenomen (kolom `error`, `pred_scenario=error`, telt als
  incorrect) en de run gaat door met de volgende case. Foutcases tellen niet
  mee in de latency-statistiek.

Blijft de latency te hoog, controleer dan met `ollama ps` of het model echt
op de GPU draait (kolom *Processor*: `100% GPU`). Bij `100% CPU` is geen
enkel 4B-model snel genoeg voor de 30s-eis.
