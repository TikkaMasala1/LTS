"""
Selective PII filter layer (Privacy by Design).

Design choice (see final report, sub-question 1):
- The LLM runs entirely locally, so aggressive anonymization is not needed.
- User and customer names stay VISIBLE (explicit servicedesk request:
  needed for context and prioritization).
- Only highly sensitive data is removed/masked before data
  enters the LLM context: passwords, secrets, API keys and tokens.

Every replacement is counted so the evaluation (PII leak = 0%) is auditable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Regex definitions
# ---------------------------------------------------------------------------

# password=..., pwd: ..., wachtwoord ... (key/value-like constructs)
RE_PASSWORD = re.compile(
    r"(?i)\b(password|passwd|pwd|wachtwoord)\b\s*[:=]\s*\S+"
)

# Bearer tokens / Authorization headers
RE_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]+=*")

# Generic secrets: api_key=..., secret=..., token=...
RE_SECRET_KV = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|client[_-]?secret|integration[_-]?code)\b\s*[:=]\s*\S+"
)

# Long hex/base64-like strings (>= 32 chars) — almost always keys/hashes
RE_LONG_KEY = re.compile(r"\b[A-Fa-f0-9]{32,}\b|\b[A-Za-z0-9+/]{40,}={0,2}\b")


@dataclass
class FilterReport:
    """Audit report of a single filter run."""
    passwords: int = 0
    secrets: int = 0
    replacements: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.passwords + self.secrets

    def as_dict(self) -> dict:
        return {
            "passwords": self.passwords,
            "secrets": self.secrets,
            "total": self.total,
        }


class PIIFilter:
    """Lightweight, targeted filter layer for log lines and tool results."""

    def filter_text(self, text: str) -> tuple[str, FilterReport]:
        report = FilterReport()

        def _sub(pattern: re.Pattern, repl: str, counter: str, s: str) -> str:
            def _do(m: re.Match) -> str:
                setattr(report, counter, getattr(report, counter) + 1)
                report.replacements.append(m.group(0)[:12] + "…")
                return repl
            return pattern.sub(_do, s)

        text = _sub(RE_PASSWORD, "[GEFILTERD:WACHTWOORD]", "passwords", text)
        text = _sub(RE_BEARER, "[GEFILTERD:TOKEN]", "secrets", text)
        text = _sub(RE_SECRET_KV, "[GEFILTERD:SECRET]", "secrets", text)
        text = _sub(RE_LONG_KEY, "[GEFILTERD:KEY]", "secrets", text)

        return text, report

    # Convenience helpers -------------------------------------------------

    def filter_lines(self, lines: list[str]) -> tuple[list[str], FilterReport]:
        merged = FilterReport()
        out: list[str] = []
        for line in lines:
            cleaned, rep = self.filter_text(line)
            out.append(cleaned)
            merged.passwords += rep.passwords
            merged.secrets += rep.secrets
            merged.replacements.extend(rep.replacements)
        return out, merged
