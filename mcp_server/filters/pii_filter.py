"""
Selective PII filter layer (Privacy by Design).

Design choice (see final report, sub-question 1):
- The LLM runs entirely locally, so aggressive anonymization is not needed.
- User and customer names stay VISIBLE (explicit servicedesk request:
  needed for context and prioritization).
- Only highly sensitive data is removed/masked before data
  enters the LLM context:
    * passwords / secrets / API keys / tokens
    * BSN numbers (NL national ID, GDPR especially sensitive)
    * IBAN account numbers
    * public (external) IP addresses -> masked; private RFC1918 addresses
      are kept because they are operationally relevant and are not an external
      identifiable data point ("IP addresses in certain contexts").

Every replacement is counted so the evaluation (PII leak = 0%) is auditable.
"""

from __future__ import annotations

import ipaddress
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

# IBAN (NL + generic European format)
RE_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")

# Candidate BSN: 9 consecutive digits (validated via the 11-test)
RE_BSN_CANDIDATE = re.compile(r"\b\d{9}\b")

# IPv4 addresses
RE_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def _is_valid_bsn(digits: str) -> bool:
    """Dutch 11-test for BSN (prevents false positives on random numbers)."""
    if len(digits) != 9 or not digits.isdigit():
        return False
    weights = [9, 8, 7, 6, 5, 4, 3, 2, 1]
    total = sum(int(d) * w for d, w in zip(digits, weights))
    return total % 11 == 0


def _is_private_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True  # no valid IP -> do not mask
    return addr.is_private or addr.is_loopback or addr.is_link_local


@dataclass
class FilterReport:
    """Audit report of a single filter run."""
    passwords: int = 0
    secrets: int = 0
    bsn: int = 0
    iban: int = 0
    public_ips: int = 0
    replacements: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.passwords + self.secrets + self.bsn + self.iban + self.public_ips

    def as_dict(self) -> dict:
        return {
            "passwords": self.passwords,
            "secrets": self.secrets,
            "bsn": self.bsn,
            "iban": self.iban,
            "public_ips": self.public_ips,
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
        text = _sub(RE_IBAN, "[GEFILTERD:IBAN]", "iban", text)

        # BSN with the 11-test
        def _bsn_repl(m: re.Match) -> str:
            if _is_valid_bsn(m.group(0)):
                report.bsn += 1
                return "[GEFILTERD:BSN]"
            return m.group(0)
        text = RE_BSN_CANDIDATE.sub(_bsn_repl, text)

        # Mask public IPs, keep private ones
        def _ip_repl(m: re.Match) -> str:
            ip = m.group(0)
            if _is_private_ip(ip):
                return ip
            report.public_ips += 1
            parts = ip.split(".")
            return f"{parts[0]}.{parts[1]}.x.x"
        text = RE_IPV4.sub(_ip_repl, text)

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
            merged.bsn += rep.bsn
            merged.iban += rep.iban
            merged.public_ips += rep.public_ips
            merged.replacements.extend(rep.replacements)
        return out, merged


# Patterns that must never appear after filtering.
# Used by evaluation/quantitative.py for the "PII leak = 0%" measurement.
LEAK_DETECTORS: list[re.Pattern] = [
    RE_PASSWORD,
    RE_BEARER,
    RE_SECRET_KV,
    RE_IBAN,
]


def detect_leaks(text: str) -> list[str]:
    """Scans text (e.g. the full LLM input) for remaining sensitive data."""
    hits: list[str] = []
    for pattern in LEAK_DETECTORS:
        hits.extend(m.group(0) for m in pattern.finditer(text))
    for m in RE_BSN_CANDIDATE.finditer(text):
        if _is_valid_bsn(m.group(0)):
            hits.append(m.group(0))
    return hits
