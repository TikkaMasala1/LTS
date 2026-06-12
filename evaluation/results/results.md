# Kwantitatieve evaluatieresultaten — LTS PoC

Gegenereerd: 2026-06-12T12:13:37 · Configuratie: llm=mock, backend=direct
Testcases: 45 (3 scenario's + healthy-controlegroep, met PII-canaries)

| Metric | Doel | Resultaat | Status |
|---|---|---|---|
| Accuracy / Success rate | ≥ 80% | 100.0% | behaald |
| Responstijd (latency, p95) | < 30 s | 0.0 s (gem. 0.0 s, max 0.0 s) | behaald |
| Hallucinatie-rate | ≤ 5% | 0.0% | behaald |
| Tool-calling accuracy | ≥ 90% | 100.0% | behaald |
| PII-lek (security) | 0% | 0.0% (225 canaries gefilterd) | behaald |

**Operationalisatie hallucinatie:** een diagnose telt als hallucinatie wanneer
(a) het scenario onjuist is bij confidence ≥ 0.5, of (b) de evidence verwijst
naar een tool die niet is aangeroepen of niet bestaat (verzonnen bron).

Per-case resultaten: `results.csv`.
