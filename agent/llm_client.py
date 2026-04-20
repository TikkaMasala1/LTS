"""
LLM client for the agent.

OllamaClient talks to a locally running Ollama runner via the
OpenAI-compatible API (http://localhost:11434/v1). Default model:
phi4-mini (choice justified in PvA §2.2.1/§2.3 — 4 GB VRAM scenario).
For the 16 GB scenario: OLLAMA_MODEL=qwen3:14b.

Interface:  chat(messages, tools) -> dict (OpenAI-formaat)
"""

from __future__ import annotations

import json
import os

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore


class LLMError(RuntimeError):
    pass


class OllamaClient:
    """Local LLM via Ollama (OpenAI-compatible endpoint, incl. tool calling)."""

    def __init__(self, model: str | None = None, base_url: str | None = None,
                 temperature: float = 0.2) -> None:
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


def get_llm():
    """Factory."""
    return OllamaClient()
