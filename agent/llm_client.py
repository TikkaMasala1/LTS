"""
LLM clients for the agent.

- OllamaClient : talks to a locally running Ollama runner via the
  OpenAI-compatible API (http://localhost:11434/v1). Default model:
  phi4-mini (choice justified in PvA §2.2.1/§2.3 — 4 GB VRAM scenario).
  For the 16 GB scenario: OLLAMA_MODEL=qwen3:14b.

- GeminiClient : temporary cloud replacement via the Google Gemini API
  (OpenAI-compatible endpoint). Only for development/tests with the
  simulated dataset when no local GPU is available; deliberately
  breaks the data sovereignty of the final solution (see class docstring).

- MockLLM : deterministic, rule-based fallback with an identical
  interface. Makes it possible to run and demo the full pipeline (MCP tools, PII
  filter, HitL, Autotask, evaluation) without a
  GPU. The final report of course reports the measurements with the real
  local model.

Both clients implement:  chat(messages, tools) -> dict (OpenAI format)
"""

from __future__ import annotations

import json
import os
import re

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore


class LLMError(RuntimeError):
    pass


class OllamaClient:
    """Local LLM via Ollama (OpenAI-compatible endpoint, incl. tool calling)."""

    def __init__(self, model: str | None = None, base_url: str | None = None,
                 temperature: float = 0.1) -> None:
        if httpx is None:
            raise LLMError("httpx is vereist voor Ollama-modus (pip install httpx)")
        self.model = model or os.environ.get("OLLAMA_MODEL", "phi4-mini")
        self.base_url = (base_url or os.environ.get("OLLAMA_URL",
                         "http://localhost:11434")).rstrip("/")
        self.temperature = temperature
        self.timeout = 120.0

    @property
    def name(self) -> str:
        return f"ollama/{self.model}"

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        body: dict = {"model": self.model, "messages": messages,
                      "temperature": self.temperature, "stream": False}
        if tools:
            body["tools"] = tools
        try:
            resp = httpx.post(f"{self.base_url}/v1/chat/completions",
                              json=body, timeout=self.timeout)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            raise LLMError(
                f"Ollama niet bereikbaar op {self.base_url}. "
                f"Draai 'ollama serve' en 'ollama pull {self.model}'. ({exc})"
            ) from exc
        msg = resp.json()["choices"][0]["message"]
        return msg


class GeminiClient:
    """Google Gemini via the OpenAI-compatible endpoint (temporary replacement).

    NOTE — deviation from the project goal: this sends prompts and (filtered)
    log context to an external cloud API and thereby breaks the
    data sovereignty that is central to this PoC (PvA §1.4, §2.5). Only
    intended as a temporary replacement during development/tests when no
    local GPU is available — never use with real customer data, only with
    the simulated dataset. In this mode the PII filter is no longer an extra
    defense layer but the only one; the PII leak metric (0% requirement) is
    therefore extra relevant here. Requires: GEMINI_API_KEY (https://aistudio.google.com).
    """

    BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"

    def __init__(self, model: str | None = None, api_key: str | None = None,
                 temperature: float = 0.1) -> None:
        if httpx is None:
            raise LLMError("httpx is vereist voor Gemini-modus (pip install httpx)")
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        if not self.api_key:
            raise LLMError("GEMINI_API_KEY ontbreekt (zet deze in .env of als "
                           "omgevingsvariabele; sleutel via https://aistudio.google.com)")
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        self.temperature = temperature

    @property
    def name(self) -> str:
        return f"gemini/{self.model}"

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        body: dict = {"model": self.model, "messages": messages,
                      "temperature": self.temperature}
        if tools:
            body["tools"] = tools
        try:
            resp = httpx.post(f"{self.BASE_URL}/chat/completions",
                              headers={"Authorization": f"Bearer {self.api_key}"},
                              json=body, timeout=120.0)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise LLMError(f"Gemini API-fout {exc.response.status_code}: "
                           f"{exc.response.text[:300]}") from exc
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"Gemini niet bereikbaar: {exc}") from exc
        msg = resp.json()["choices"][0]["message"]
        # Normalize: some responses lack 'content' on tool calls.
        msg.setdefault("content", None)
        return msg


class MockLLM:
    """Rule-based simulation of the agent LLM (same interface as Ollama).

    Behavior: first calls get_recent_logs, then picks the scenario based on keywords in
    the log output, verifies with the matching diagnosis tool,
    proposes an action via propose_remediation, and returns the JSON diagnosis.
    """

    name = "mock/rule-based"

    SCENARIO_RULES = [
        ("disk_space", ["disk space", "disk_full", "not enough disk", "insufficient free space"],
         "get_disk_usage", "cleanup_disk",
         "Volume C: is vrijwel vol; OneDrive/updates falen door ruimtegebrek."),
        ("vpn", ["vpn", "packet loss", "tunnel", "re-key"],
         "get_vpn_status", "update_vpn_client",
         "VPN-tunnel toont hoge latency en packet loss; client lijkt verouderd."),
        ("performance", ["cpu sustained", "memory pressure", "not responding", "traag"],
         "get_performance_metrics", "restart_process",
         "Aanhoudend hoge CPU-/RAM-druk door een vastgelopen proces."),
    ]

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        called = [m.get("name") for m in tool_msgs]

        # Step 1: always fetch logs first
        if "get_recent_logs" not in called:
            return self._tool_call("get_recent_logs", {"max_lines": 40})

        log_text = " ".join(m.get("content", "") for m in tool_msgs
                            if m.get("name") == "get_recent_logs").lower()
        scenario = self._classify(log_text)

        if scenario in ("healthy", "unknown"):
            return self._final(scenario, "Geen incidentpatroon in de logs aangetroffen."
                               if scenario == "healthy" else
                               "Onvoldoende eenduidig bewijs in de beschikbare data.",
                               "no_action", "Geen actie nodig; blijven monitoren.",
                               0.9 if scenario == "healthy" else 0.3,
                               [{"tool": "get_recent_logs",
                                 "finding": "Geen ERROR/WARN-patronen die op een bekend scenario wijzen."}])

        _, _, verify_tool, action, root_cause = next(
            r for r in self.SCENARIO_RULES if r[0] == scenario)

        # Step 2: verify with the scenario-specific tool
        if verify_tool not in called:
            return self._tool_call(verify_tool, {})

        verify_text = " ".join(m.get("content", "") for m in tool_msgs
                               if m.get("name") == verify_tool)

        # Step 3: propose an action (HitL)
        if "propose_remediation" not in called:
            return self._tool_call("propose_remediation",
                                   {"action": action, "target": "endpoint",
                                    "reason": root_cause})

        finding = self._extract_finding(scenario, verify_text)
        evidence = [
            {"tool": "get_recent_logs", "finding": self._extract_log_finding(scenario, log_text)},
            {"tool": verify_tool, "finding": finding},
        ]
        details = {
            "cleanup_disk": "Temp-bestanden en grootste downloads opruimen; gebruiker informeren.",
            "restart_process": "Vastgelopen proces beëindigen en opnieuw starten; daarna belasting monitoren.",
            "update_vpn_client": "VPN-client bijwerken naar 5.2.x en tunnel opnieuw opbouwen.",
        }[action]
        return self._final(scenario, root_cause, action, details, 0.92, evidence)

    # ------------------------------------------------------------------

    def _classify(self, log_text: str) -> str:
        for scenario, keywords, *_ in self.SCENARIO_RULES:
            if any(k in log_text for k in keywords):
                return scenario
        if "scheduled check ok" in log_text or "no anomalies" in log_text:
            return "healthy"
        return "unknown"

    @staticmethod
    def _extract_log_finding(scenario: str, log_text: str) -> str:
        return {
            "disk_space": "Logs: 'Low disk space on C:' en DISK_FULL-schrijffouten.",
            "performance": "Logs: 'CPU sustained above 90%' en proces 'not responding'.",
            "vpn": "Logs: hoge latency, packet loss en herhaalde re-key timeouts op de tunnel.",
        }.get(scenario, "n.v.t.")

    @staticmethod
    def _extract_finding(scenario: str, verify_text: str) -> str:
        m = re.search(r'"used_pct":\s*([\d.]+)', verify_text)
        if scenario == "disk_space" and m:
            return f"Schijfgebruik C: {m.group(1)}% (status CRITICAL)."
        m = re.search(r'"cpu_pct":\s*([\d.]+)', verify_text)
        if scenario == "performance" and m:
            return f"CPU-belasting {m.group(1)}% (status CRITICAL)."
        m = re.search(r'"latency_ms":\s*([\d.]+)', verify_text)
        if scenario == "vpn" and m:
            return f"Tunnel-latency {m.group(1)} ms met packet loss (health CRITICAL)."
        return "Tool-output bevestigt het scenario."

    @staticmethod
    def _tool_call(name: str, args: dict) -> dict:
        return {"role": "assistant", "content": None,
                "tool_calls": [{"id": f"call_{name}", "type": "function",
                                "function": {"name": name,
                                             "arguments": json.dumps(args)}}]}

    @staticmethod
    def _final(scenario: str, root_cause: str, action: str, details: str,
               confidence: float, evidence: list[dict]) -> dict:
        return {"role": "assistant",
                "content": json.dumps({
                    "scenario": scenario, "root_cause": root_cause,
                    "proposed_action": action, "action_details": details,
                    "confidence": confidence, "evidence": evidence,
                }, ensure_ascii=False)}


def get_llm(use_mock: bool | None = None):
    """Factory. LTS_LLM=mock|ollama|gemini (default: ollama)."""
    if use_mock is True:
        return MockLLM()
    choice = os.environ.get("LTS_LLM", "ollama") if use_mock is None else "ollama"
    if choice == "mock":
        return MockLLM()
    if choice == "gemini":
        return GeminiClient()
    return OllamaClient()
