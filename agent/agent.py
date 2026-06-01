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
from mcp_server.filters.pii_filter import PIIFilter

_PII = PIIFilter()

MAX_ITERATIONS = 12

# ---------------------------------------------------------------------------
# Fallback: tool calls that appear as text in 'content' instead of 'tool_calls'
# ---------------------------------------------------------------------------
# Known quirk of phi4-mini in Ollama (among others): the model writes
#   functools[{"name": "get_recent_logs", "arguments": {"max_lines": 40}}]
# as plain text, depending on the Ollama version/chat template. This
# parser recognizes that pattern (and bare JSON arrays/objects with a "name"
# field matching an existing tool) and converts it to the
# standard OpenAI format so the agent loop can continue normally.

_EMBEDDED_CALL_RE = re.compile(r"functools\s*\[(.*)\]", re.DOTALL)
_FENCE_RE = re.compile(r"```(?:json|python|tool_code)?\s*(.*?)```", re.DOTALL)
_TOOL_TOKEN_RE = re.compile(r"<\|/?tool(?:_call)?\|>")
_NAME_KEYS = ("name", "tool", "function", "tool_name")

NUDGE_PROMPT = (
    "Je hebt nog geen enkele tool aangeroepen en dus geen feitelijke data. "
    "Een diagnose zonder toolresultaten is niet toegestaan. Roep nu eerst "
    "de tool get_recent_logs aan (via een tool call, niet als tekst), "
    "verifieer daarna met een passende diagnose-tool, en geef pas dan je "
    "JSON-diagnose."
)


def _candidate_payloads(content: str) -> list[str]:
    """Collect text fragments that may contain (a list of) tool calls."""
    candidates: list[str] = []
    # 1) functools[...] (phi4-mini quirk)
    m = _EMBEDDED_CALL_RE.search(content)
    if m:
        candidates.append("[" + m.group(1).strip().rstrip(",") + "]")
    # 2) contents of markdown code fences (```json ... ```)
    candidates.extend(f.strip() for f in _FENCE_RE.findall(content))
    # 3) content with tool tokens (<|tool|> ... <|/tool|>) stripped out
    stripped = _TOOL_TOKEN_RE.sub("", content).strip()
    candidates.append(stripped)
    # 4) first [...] block and first {...} block in the text
    for open_c, close_c in (("[", "]"), ("{", "}")):
        i = content.find(open_c)
        j = content.rfind(close_c)
        if 0 <= i < j:
            candidates.append(content[i:j + 1])
    return candidates


def _extract_embedded_calls(content: str | None,
                            tool_names: set[str]) -> list[dict] | None:
    """Recognize tool calls that appear as text in the content. Returns None
    if the content has no (valid) embedded tool calls."""
    if not content:
        return None
    for raw in _candidate_payloads(content):
        if not raw:
            continue
        if raw.startswith("{"):
            raw = f"[{raw}]"
        if not raw.startswith("["):
            continue
        try:
            items = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(items, list) or not items:
            continue
        calls: list[dict] = []
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                calls = []
                break
            # Evidence items from the diagnosis ({"tool": ..., "finding": ...})
            # and the diagnosis itself are not tool calls.
            if "finding" in item or "scenario" in item:
                calls = []
                break
            # The tool name may appear under different keys; for nested
            # OpenAI form ({"function": {"name": ...}}) also look inside it.
            name = next((item[k] for k in _NAME_KEYS
                         if isinstance(item.get(k), str)), None)
            if name is None and isinstance(item.get("function"), dict):
                name = item["function"].get("name")
                item = {**item, **item["function"]}
            # Only accept if every element names an existing tool;
            # otherwise this is probably the JSON diagnosis itself.
            if name not in tool_names:
                calls = []
                break
            args = item.get("arguments", item.get("parameters",
                            item.get("args", {})))
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            if not isinstance(args, dict):
                args = {}
            calls.append({"id": f"embedded_{i}_{name}", "type": "function",
                          "function": {"name": name,
                                       "arguments": json.dumps(args)}})
        if calls:
            return calls
    return None

VALID_ACTIONS = {"cleanup_disk", "restart_process", "restart_service",
                 "update_vpn_client", "reconnect_vpn", "flush_dns", "no_action"}


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
        # Defense in depth: the trigger (often a raw log line from monitoring)
        # also passes through the PII filter before it goes to the LLM.
        trigger, _trigger_report = _PII.filter_text(trigger)
        tools = self.backend.list_tools()
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT_TEMPLATE.format(
                hostname=hostname, customer=customer, user=user, trigger=trigger)},
        ]
        tool_calls_log: list[dict] = []
        final_text: str | None = None
        tool_names = {t["function"]["name"] for t in tools}
        nudged = False

        for _ in range(MAX_ITERATIONS):
            reply = self.llm.chat(messages, tools=tools)
            calls = reply.get("tool_calls")
            if not calls:
                # Fallback: tool calls as text in content (phi4-mini quirk).
                calls = _extract_embedded_calls(reply.get("content"), tool_names)
            if not calls:
                # Diagnosis without a single tool call? Push back once:
                # without factual data, any diagnosis is by definition a guess.
                if not tool_calls_log and not nudged:
                    nudged = True
                    messages.append({"role": "assistant",
                                     "content": reply.get("content") or ""})
                    messages.append({"role": "user", "content": NUDGE_PROMPT})
                    continue
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
                    if data.get("proposed_action") not in VALID_ACTIONS:
                        data["proposed_action"] = "no_action"
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
