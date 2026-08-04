#!/usr/bin/env python3
"""Build compact paper tables from ReviewX score outputs."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


MAIN_FIELDS = [
    "method",
    "unsupportedRecall",
    "unsupportedF1",
    "contradictedRecall",
    "contradictedF1",
    "avgCorruptionDetection",
    "issueFindingDeltaFromClean",
    "cleanIssueFindingsPerSample",
    "findingCount",
    "unsupportedFindingCount",
    "artifactAbsentFindingCount",
    "needsHumanVerificationFindingCount",
    "selectedFindingCount",
    "validReviewerDecisionCount",
    "selectedFindingGoldPrecision",
    "selectedActionabilityScore",
    "selectedAssessmentSpecificityScore",
    "selectedGroundingCueScore",
    "lowConfidenceCitationFindingCount",
    "selectedLowConfidenceCitationCount",
    "llmAddedFindingCount",
    "calibrationGain",
    "llmCallCount",
    "estimatedTokenCost",
    "tokenPerSelectedFinding",
    "tokenPerValidReviewerDecision",
    "runnerElapsedMs",
]

LLM_UTILITY_FIELDS = [
    "method",
    "llmCallCount",
    "estimatedTokenCost",
    "selectedFindingCount",
    "validReviewerDecisionCount",
    "selectedFindingGoldPrecision",
    "selectedFindingGoldMatchCount",
    "selectedActionabilityScore",
    "selectedAssessmentSpecificityScore",
    "selectedGroundingCueScore",
    "selectedReviewerAssessmentRate",
    "lowConfidenceCitationFindingCount",
    "selectedLowConfidenceCitationCount",
    "tokenPerSelectedFinding",
    "tokenPerValidReviewerDecision",
    "llmAddedFindingCount",
    "overestimatedReviewerDecisionCount",
    "runnerElapsedMs",
]


def load_json(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_csv_by_method(path: str | None) -> dict[str, dict[str, Any]]:
    if not path:
        return {}
    rows: dict[str, dict[str, Any]] = {}
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            method = row.get("method")
            if method:
                rows[method] = row
    return rows


def maybe_number(value: Any) -> Any:
    if value in {None, ""}:
        return value
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return value


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def method_rows(scores: dict[str, Any], analysis_by_method: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for method, metrics in sorted(scores.get("methods", {}).items()):
        corruption_rates = [
            float(item.get("detectionRate", 0) or 0)
            for item in scores.get("corruptions", {}).get(method, {}).values()
        ]
        analysis = analysis_by_method.get(method, {})
        row = {
            "method": method,
            "avgCorruptionDetection": round(mean(corruption_rates), 4),
        }
        for field in MAIN_FIELDS:
            if field in {"method", "avgCorruptionDetection"}:
                continue
            row[field] = metrics.get(field, maybe_number(analysis.get(field, 0)))
        rows.append(row)
    return rows


def corruption_rows(scores: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for method, corruptions in sorted(scores.get("corruptions", {}).items()):
        for corruption, values in sorted(corruptions.items()):
            rows.append({
                "method": method,
                "corruptionType": corruption,
                "detected": values.get("detected", 0),
                "total": values.get("total", 0),
                "detectionRate": values.get("detectionRate", 0),
            })
    return rows


def llm_utility_rows(scores: dict[str, Any], analysis_by_method: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for method, metrics in sorted(scores.get("methods", {}).items()):
        analysis = analysis_by_method.get(method, {})
        row = {"method": method}
        for field in LLM_UTILITY_FIELDS:
            if field == "method":
                continue
            row[field] = metrics.get(field, maybe_number(analysis.get(field, 0)))
        rows.append(row)
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def markdown_table(rows: list[dict[str, Any]], fields: list[str]) -> str:
    lines = [
        "| " + " | ".join(fields) + " |",
        "| " + " | ".join(["---"] * len(fields)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(fmt(row.get(field, "")) for field in fields) + " |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scores", required=True)
    parser.add_argument("--analysis", help="Optional analyze_results.py CSV for routing/calibration columns")
    parser.add_argument("--output-dir", default="experiments/reviewx_eval/outputs/paper_tables")
    args = parser.parse_args()

    scores = load_json(args.scores)
    analysis_by_method = load_csv_by_method(args.analysis)
    output_dir = Path(args.output_dir)
    rows = method_rows(scores, analysis_by_method)
    corruptions = corruption_rows(scores)
    llm_rows = llm_utility_rows(scores, analysis_by_method)

    write_csv(output_dir / "method_summary.csv", rows, MAIN_FIELDS)
    write_csv(output_dir / "llm_utility.csv", llm_rows, LLM_UTILITY_FIELDS)
    write_csv(
        output_dir / "corruption_detection.csv",
        corruptions,
        ["method", "corruptionType", "detected", "total", "detectionRate"],
    )

    markdown = "\n\n".join([
        "# ReviewX CEM-Bench Tables",
        "## Method Summary",
        markdown_table(rows, MAIN_FIELDS),
        "## LLM Utility",
        markdown_table(llm_rows, LLM_UTILITY_FIELDS),
        "## Corruption Detection",
        markdown_table(corruptions, ["method", "corruptionType", "detected", "total", "detectionRate"]),
        (
            "Note: clean controls are same-source FAROS papers, not human-certified perfect papers. "
            "`issueFindingDeltaFromClean` reports how many issue findings are added by a corruption relative to that control."
        ),
        "",
    ])
    (output_dir / "tables.md").write_text(markdown, encoding="utf-8")
    print(f"methods={len(rows)} corruptions={len(corruptions)} outputDir={output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
