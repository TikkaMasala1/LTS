# De lokale troubleshooter (LTS) — Proof of Concept

**Veilige geautomatiseerde ondersteuning voor Managed Services**
Stageproject Taran Singh · Ultimum MSP B.V. · Hogeschool Utrecht (AI, derdejaars)

Een lokale AI-servicedeskagent op basis van het Model Context Protocol (MCP):
diagnosticeert terugkerende incidenten (schijfruimte, performance, VPN) en maakt
ná menselijke goedkeuring (Human-in-the-Loop) een ticket aan in Autotask.

## Instalatie

Vereist Python 3.11+ en (voor het echte LLM) [Ollama](https://ollama.com).

```bash
pip install -r requirements.txt
ollama pull phi4-mini
```

Autotask-sandbox (optioneel): kopieer `.env.example` naar `.env` en vul de
credentials in. Zonder credentials draait automatisch de mock-modus.

## Testdataset genereren

```bash
python -m simulator.log_generator
# → evaluation/dataset/testcases.json
```

## Demo (zonder GPU)

```bash
python run_demo.py
```

Draait één case per scenario end-to-end met de mock-LLM: diagnose, bewijs
en voorgestelde actie (wacht op menselijke goedkeuring).

> Streamlit-interface, MCP-server-route en evaluatieframework volgen.
