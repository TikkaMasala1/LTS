"""
Qualitative evaluation — analysis of the feedback forms.

Input : evaluation/qualitative/feedback_responses.csv
        (same column layout as sample_feedback.csv; one row per respondent)
Output: evaluation/results/qualitative_summary.md
        - Anonymized quotes per theme (R1, R2, ...)

Usage:
  python -m evaluation.analyze_feedback
  python -m evaluation.analyze_feedback --input path/to/responses.csv
"""

from __future__ import annotations

import argparse
import csv
import statistics
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

    lines.append("\n---\n*Deze samenvatting vormt de basis voor Bijlage E "
                 "van het eindverslag.*")
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
