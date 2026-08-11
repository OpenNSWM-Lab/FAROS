#!/usr/bin/env python3
"""Summarize human annotation CSVs exported by export_human_eval.py."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


HUMAN_SCORE_FIELDS = [
    "humanCorrectness",
    "humanActionability",
    "humanSpecificity",
    "humanGrounding",
    "humanSeverityAgreement",
]


def load_csv(path: str) -> list[dict[str, Any]]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def parse_score(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if score < 1 or score > 5:
        return None
    return score


def mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def bool_rate(values: list[str]) -> float:
    cleaned = [str(value).strip().lower() for value in values if str(value).strip()]
    if not cleaned:
        return 0.0
    positives = len([value for value in cleaned if value in {"true", "1", "yes"}])
    return round(positives / len(cleaned), 4)


def summarize_group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    annotated_rows = [
        row for row in rows
        if any(parse_score(row.get(field)) is not None for field in HUMAN_SCORE_FIELDS)
    ]
    summary: dict[str, Any] = {
        "count": len(rows),
        "annotatedCount": len(annotated_rows),
        "selectedFindingGoldPrecision": bool_rate([str(row.get("selectedFindingGoldMatch", "")) for row in rows]),
    }
    for field in HUMAN_SCORE_FIELDS:
        scores = [score for row in rows if (score := parse_score(row.get(field))) is not None]
        summary[f"{field}Mean"] = mean(scores)
        summary[f"{field}Count"] = len(scores)
    complete_scores = []
    for row in rows:
        values = [parse_score(row.get(field)) for field in HUMAN_SCORE_FIELDS]
        if all(value is not None for value in values):
            complete_scores.append(sum(value for value in values if value is not None) / len(values))
    summary["overallHumanQualityMean"] = mean(complete_scores)
    summary["overallHumanQualityCount"] = len(complete_scores)
    return summary


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("method") or "unknown")].append(row)
    return [
        {"method": method, **summarize_group(items)}
        for method, items in sorted(grouped.items())
    ]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    preferred = [
        "method",
        "count",
        "annotatedCount",
        "overallHumanQualityMean",
        "overallHumanQualityCount",
        "selectedFindingGoldPrecision",
    ]
    fields = [field for field in preferred if field in fields] + [field for field in fields if field not in preferred]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Annotated CSV from export_human_eval.py")
    parser.add_argument("--csv-output", required=True)
    parser.add_argument("--json-output")
    args = parser.parse_args()

    rows = load_csv(args.input)
    summaries = summarize(rows)
    write_csv(Path(args.csv_output), summaries)
    if args.json_output:
        Path(args.json_output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_output).write_text(json.dumps(summaries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"methods={len(summaries)} rows={len(rows)} output={args.csv_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
