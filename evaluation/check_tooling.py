"""
Diagnostic: does the chosen Ollama model support structured tool calling?

Usage:
  uv run python -m evaluation.check_tooling
  uv run python -m evaluation.check_tooling --model qwen3:4b

The script does two things:
1. Queries the model's capabilities via /api/show. If 'tools' is
   not listed, Ollama ignores the tools parameter and the model can by
   definition not make structured tool calls — switch models in that case.
2. Sends a minimal tool-call test ("what time is it?" with one tool) and
   shows the RAW message from the response. This shows directly whether the model
   provides `tool_calls`, or writes its call as text in `content`
   (and in which format).
"""

from __future__ import annotations

import argparse
import json
import os

import httpx

BASE = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")

TEST_TOOL = {
    "type": "function",
    "function": {
        "name": "get_time",
        "description": "Geeft de huidige tijd terug.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model",
                        default=os.environ.get("OLLAMA_MODEL", "phi4-mini"))
    args = parser.parse_args()

    print(f"Ollama : {BASE}")
    print(f"Model  : {args.model}\n")

    # --- 1. capabilities ---------------------------------------------------
    try:
        resp = httpx.post(f"{BASE}/api/show",
                          json={"model": args.model}, timeout=30.0)
        resp.raise_for_status()
        caps = resp.json().get("capabilities", [])
        print(f"[1] Capabilities: {caps}")
        if "tools" in caps:
            print("    'tools' aanwezig — het template ondersteunt tool calling.")
        else:
            print("    'tools' ONTBREEKT — Ollama negeert de tools-parameter")
            print("       voor dit model. Structured tool calls zijn onmogelijk.")
            print("       → update Ollama + `ollama pull` opnieuw, of kies een")
            print("         ander model (bijv. OLLAMA_MODEL=qwen3:4b).")
    except Exception as exc:  # noqa: BLE001
        print(f"[1] /api/show mislukt: {exc}")

    # --- 2. minimal tool-call test ----------------------------------------
    body = {
        "model": args.model,
        "messages": [
            {"role": "system",
             "content": "Gebruik de beschikbare tool om de vraag te beantwoorden."},
            {"role": "user", "content": "Hoe laat is het nu? Roep de tool aan."},
        ],
        "tools": [TEST_TOOL],
        "temperature": 0.0,
        "stream": False,
    }
    print("\n[2] Minimale tool-call-test...")
    try:
        resp = httpx.post(f"{BASE}/v1/chat/completions", json=body, timeout=120.0)
        resp.raise_for_status()
        msg = resp.json()["choices"][0]["message"]
        print("    Ruwe message:")
        print(json.dumps(msg, indent=2, ensure_ascii=False)[:1500])
        if msg.get("tool_calls"):
            print("\n    Model levert structured tool_calls — geschikt voor de agent.")
        elif msg.get("content"):
            print("\n     Geen tool_calls; het model schreef tekst. Als hierboven")
            print("       een tool-aanroep-achtig formaat staat, vangt de fallback-")
            print("       parser in agent/agent.py dit mogelijk op — maar een model")
            print("       met native tool calling (qwen3:4b) is betrouwbaarder.")
    except httpx.HTTPStatusError as exc:
        print(f"    HTTP {exc.response.status_code}: {exc.response.text[:300]}")
        print("       (Een 400 met 'does not support tools' = template zonder")
        print("        tool-ondersteuning → ander model kiezen.)")
    except Exception as exc:  # noqa: BLE001
        print(f"    Test mislukt: {exc}")


if __name__ == "__main__":
    main()
