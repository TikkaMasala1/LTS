"""
The local troubleshooting agent.

Orchestrates: LLM (Ollama/Phi-4-mini or mock) and tools (MCP server or direct)
to produce a structured, verifiable diagnosis.

Key safeguards:
  - HitL by design: the agent can only PROPOSE actions
    (propose_remediation / autotask_draft_ticket); execution happens only after
    explicit approval in the Streamlit interface.
  - Audit transcript: all text sent to the LLM is retained,
    so the evaluation can verify that 0% PII leaked.
  - Pydantic validation of the JSON diagnosis; one retry on an
    invalid response, otherwise a safe "unknown" fallback (no guessing).
"""

from __future__ import annotations

import json
import re
import time
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from agent.llm_client import get_llm
from agent.prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE

MAX_ITERATIONS = 8


class Evidence(BaseModel):
    tool: str
    finding: str


class Diagnosis(BaseModel):
    scenario: Literal["disk_space", "performance", "vpn", "healthy", "unknown"]
    root_cause: str
    proposed_action: str
    action_details: str = ""
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[Evidence] = []


class DiagnosisResult(BaseModel):
    diagnosis: Diagnosis
    tool_calls: list[dict] = []          # [{name, arguments}]
    latency_s: float
    model: str
    llm_input_transcript: str            # for PII audit (evaluation)
    parse_recovered: bool = False        # True if the first response was invalid
    error: str | None = None


def _extract_json(text: str) -> dict:
    """Extract the first JSON object from a (possibly polluted) response."""
    text = text.strip()
    if text.startswith("{"):
        return json.loads(text)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("Geen JSON-object gevonden in LLM-antwoord")
    return json.loads(m.group(0))


class TroubleshooterAgent:
    def __init__(self, backend, llm=None) -> None:
        self.backend = backend
        self.llm = llm or get_llm()

    def diagnose(self, hostname: str, customer: str, user: str,
                 trigger: str = "Geautomatiseerde monitoring-melding") -> DiagnosisResult:
        start = time.perf_counter()
        tools = self.backend.list_tools()
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT_TEMPLATE.format(
                hostname=hostname, customer=customer, user=user, trigger=trigger)},
        ]
        tool_calls_log: list[dict] = []
        final_text: str | None = None

        for _ in range(MAX_ITERATIONS):
            reply = self.llm.chat(messages, tools=tools)
            calls = reply.get("tool_calls")
            if not calls:
                final_text = reply.get("content") or ""
                messages.append({"role": "assistant", "content": final_text})
                break
            messages.append({"role": "assistant", "content": reply.get("content"),
                             "tool_calls": calls})
            for call in calls:
                name = call["function"]["name"]
                try:
                    args = json.loads(call["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                output = self.backend.call_tool(name, args)
                tool_calls_log.append({"name": name, "arguments": args})
                messages.append({"role": "tool", "tool_call_id": call.get("id", name),
                                 "name": name, "content": output})
        else:
            final_text = None  # iteration limit reached

        diagnosis, recovered, error = self._parse_with_retry(final_text, messages, tools)
        latency = time.perf_counter() - start

        transcript = "\n".join(
            f"[{m['role']}] {m.get('content') or ''}" for m in messages)

        return DiagnosisResult(
            diagnosis=diagnosis, tool_calls=tool_calls_log, latency_s=latency,
            model=getattr(self.llm, "name", "unknown"),
            llm_input_transcript=transcript,
            parse_recovered=recovered, error=error)

    # ------------------------------------------------------------------

    def _parse_with_retry(self, final_text: str | None, messages: list[dict],
                          tools: list[dict]) -> tuple[Diagnosis, bool, str | None]:
        attempts = 0
        text = final_text
        while attempts < 2:
            if text:
                try:
                    data = _extract_json(text)
                    return Diagnosis.model_validate(data), attempts > 0, None
                except (ValueError, json.JSONDecodeError, ValidationError):
                    pass
            attempts += 1
            if attempts >= 2:
                break
            # One explicit retry: ask for valid JSON
            messages.append({"role": "user", "content":
                             "Je antwoord was geen geldig JSON volgens het schema. "
                             "Antwoord nu met UITSLUITEND het JSON-object."})
            reply = self.llm.chat(messages, tools=tools)
            text = reply.get("content") or ""

        # Safe fallback: no guess, but explicit 'unknown'
        fallback = Diagnosis(
            scenario="unknown",
            root_cause="De agent kon geen valide gestructureerde diagnose leveren.",
            proposed_action="no_action",
            action_details="Handmatige beoordeling door technicus vereist.",
            confidence=0.0, evidence=[])
        return fallback, True, "invalid_llm_output"
