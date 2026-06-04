"""
Qualitative evaluation — analysis of the feedback forms.

Input : evaluation/qualitative/feedback_responses.csv
        (same column layout as sample_feedback.csv; one row per respondent)
Output: evaluation/results/qualitative_summary.md
        - Mean + spread per Likert question (Q1–Q8)
        - Thematic clustering of open answers (keyword-based)
        - Anonymized quotes per theme (R1, R2, ...)

Usage:
  python -m evaluation.analyze_feedback
  python -m evaluation.analyze_feedback --input path/to/responses.csv
"""

from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "evaluation" / "qualitative" / "feedback_responses.csv"
SAMPLE_INPUT = ROOT / "evaluation" / "qualitative" / "sample_feedback.csv"
OUTPUT = ROOT / "evaluation" / "results" / "qualitative_summary.md"

LIKERT = {
    "Q1": "Diagnose correct/herkenbaar",
    "Q2": "Bewijs (bronnen) duidelijk",
    "Q3": "Voorgestelde actie bruikbaar",
    "Q4": "Klant-/gebruikersnamen zichtbaar nuttig",
    "Q5": "HitL geeft voldoende controle",
    "Q6": "Responstijd acceptabel",
    "Q7": "Verlaagt werkdruk merkbaar",
    "Q8": "Vertrouwen voor productiegebruik",
}

OPEN_FIELDS = ["open_twijfel", "open_ontbreekt", "open_volgende_incidenten",
               "open_niet_vertrouwen", "open_overig"]

# Keyword-based themes for the open answers
THEMES = {
    "Vertrouwen & confidence": ["twijfel", "vertrouw", "confidence", "zeker"],
    "Transparantie & bewijs": ["bewijs", "bron", "logregel", "uitleg", "transparant"],
    "UI/Workflow-verbeteringen": ["knop", "filter", "aanpassen", "scherm", "wachtrij"],
    "Uitbreiding scenario's": ["printer", "wachtwoord", "outlook", "office",
                               "bitlocker", "incident"],
    "Performance/responstijd": ["responstijd", "snel", "traag", "latency"],
    "Privacy & context (namen)": ["naam", "namen", "klant", "context", "privacy"],
}


def analyze(path: Path) -> str:
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    if not rows:
        raise SystemExit(f"Geen respondenten gevonden in {path}")

    lines = [f"# Kwalitatieve evaluatie — samenvatting",
             f"\nGegenereerd: {datetime.now().isoformat(timespec='seconds')} · "
             f"Respondenten: {len(rows)} · Bron: `{path.name}`\n",
             "## Likert-resultaten (schaal 1–5)\n",
             "| Vraag | Stelling | Gemiddelde | Min–Max | n≥4 (eens) |",
             "|---|---|---|---|---|"]

    for q, label in LIKERT.items():
        scores = [int(r[q]) for r in rows if r.get(q, "").strip().isdigit()]
        if not scores:
            continue
        agree = sum(1 for s in scores if s >= 4)
        lines.append(f"| {q} | {label} | **{statistics.mean(scores):.1f}** | "
                     f"{min(scores)}–{max(scores)} | {agree}/{len(scores)} |")

    # Thematic analysis of open answers
    theme_quotes: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        rid = r.get("respondent", "?")
        for field in OPEN_FIELDS:
            answer = (r.get(field) or "").strip()
            if not answer:
                continue
            lower = answer.lower()
            matched = False
            for theme, keywords in THEMES.items():
                if any(k in lower for k in keywords):
                    theme_quotes[theme].append(f"*\u201c{answer}\u201d* — {rid}")
                    matched = True
            if not matched:
                theme_quotes["Overig"].append(f"*\u201c{answer}\u201d* — {rid}")

    lines.append("\n## Thema's uit de open antwoorden\n")
    for theme, quotes in theme_quotes.items():
        lines.append(f"### {theme} ({len(quotes)} fragment(en))")
        lines.extend(f"- {q}" for q in quotes)
        lines.append("")

    lines.append("---\n*Citaten zijn geanonimiseerd (respondent-ID's). Deze "
                 "samenvatting vormt de basis voor Bijlage E van het eindverslag.*")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=None)
    args = parser.parse_args()

    path = args.input or (DEFAULT_INPUT if DEFAULT_INPUT.exists() else SAMPLE_INPUT)
    if path == SAMPLE_INPUT:
        print("feedback_responses.csv niet gevonden — sample_feedback.csv "
              "gebruikt als voorbeeld.\n")
    report = analyze(path)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(report, encoding="utf-8")
    print(report)
    print(f"\n→ {OUTPUT}")


if __name__ == "__main__":
    main()
