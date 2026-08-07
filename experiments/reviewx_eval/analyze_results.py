#!/usr/bin/env python3
"""Create paper-ready aggregate tables from ReviewX eval predictions."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


def read_jsonl(path: str) -> list[dict[str, Any]]:
    rows = []
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line and not line.startswith("#"):
                rows.append(json.loads(line))
    return rows


def mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def aggregate(field: str, values: list[float]) -> float:
    if field == "count":
        return round(sum(values), 4)
    return mean(values)


def record_metrics(record: dict[str, Any]) -> dict[str, float]:
    claims = record.get("claimScores", [])
    raw_values = [float(item.get("rawMismatchScore", item.get("mismatchScore", 0)) or 0) for item in claims]
    final_values = [float(item.get("mismatchScore", 0) or 0) for item in claims]
    mismatch = record.get("mismatchAggregate", {})
    trace = record.get("modelTrace", {})
    routing = trace.get("llmRouting", {}) if isinstance(trace.get("llmRouting"), dict) else {}
    selected_ids = set(str(item) for item in routing.get("selectedFindingIds", []) or [])
    findings = record.get("findings", [])
    unsupported_findings = [item for item in findings if item.get("supportStatus") == "unsupported"]
    artifact_absent_findings = [item for item in findings if item.get("supportStatus") == "artifact_absent"]
    needs_human_findings = [item for item in findings if item.get("supportStatus") == "needs_human_verification"]
    selected_findings = [item for item in findings if str(item.get("id") or "") in selected_ids]
    valid_reviewer_decisions = [
        item for item in findings
        if item.get("reviewerDecision") in {"valid", "partially_valid"}
    ]
    low_confidence_citation_findings = [
        item for item in findings
        if (item.get("cemCalibration") or {}).get("lowConfidenceCitation")
    ]
    selected_low_confidence_citation = [
        item for item in selected_findings
        if (item.get("cemCalibration") or {}).get("lowConfidenceCitation")
    ]
    selected_actionability_scores = [
        actionability_score(str(item.get("suggestedFix") or ""))
        for item in selected_findings
    ]
    selected_assessment_scores = [
        assessment_specificity_score(str(item.get("reviewerAssessment") or ""))
        for item in selected_findings
    ]
    selected_grounding_scores = [
        grounding_cue_score(
            " ".join([
                str(item.get("reviewerAssessment") or ""),
                str(item.get("suggestedFix") or ""),
                str(item.get("description") or ""),
            ])
        )
        for item in selected_findings
    ]
    token_cost = float(trace.get("estimatedTokenCost", 0) or 0)
    return {
        "count": 1.0,
        "meanMismatch": float(mismatch.get("meanMismatch", mean(final_values)) or 0),
        "rawMeanMismatch": mean(raw_values),
        "calibrationGain": max(0.0, mean(raw_values) - mean(final_values)),
        "highMismatchClaimCount": float(mismatch.get("highMismatchClaimCount", 0) or 0),
        "findingCount": float(len(findings)),
        "blockerCount": float(len([item for item in findings if item.get("severity") == "blocker"])),
        "unsupportedFindingCount": float(len(unsupported_findings)),
        "artifactAbsentFindingCount": float(len(artifact_absent_findings)),
        "needsHumanVerificationFindingCount": float(len(needs_human_findings)),
        "llmCallCount": float(trace.get("llmCallCount", 0) or 0),
        "estimatedTokenCost": float(trace.get("estimatedTokenCost", 0) or 0),
        "selectedFindingCount": float(trace.get("selectedFindingCount", 0) or 0),
        "validReviewerDecisionCount": float(len(valid_reviewer_decisions)),
        "overestimatedReviewerDecisionCount": float(len([
            item for item in findings
            if item.get("reviewerDecision") == "overestimated"
        ])),
        "llmAddedFindingCount": float(len([
            item for item in findings
            if (item.get("cemCalibration") or {}).get("llmAddedFinding")
        ])),
        "lowConfidenceCitationFindingCount": float(len(low_confidence_citation_findings)),
        "selectedLowConfidenceCitationCount": float(len(selected_low_confidence_citation)),
        "tokenPerSelectedFinding": token_cost / len(selected_findings) if selected_findings else 0.0,
        "tokenPerValidReviewerDecision": token_cost / len(valid_reviewer_decisions) if valid_reviewer_decisions else 0.0,
        "selectedActionabilityScore": mean(selected_actionability_scores),
        "selectedAssessmentSpecificityScore": mean(selected_assessment_scores),
        "selectedGroundingCueScore": mean(selected_grounding_scores),
        "selectedReviewerAssessmentRate": (
            len([item for item in selected_findings if item.get("reviewerAssessment")]) / len(selected_findings)
            if selected_findings else 0.0
        ),
        "revisionFeedbackCount": float(len([
            item for item in claims
            if float((item.get("calibration") or {}).get("revisionAdjustment", 0) or 0) > 0
        ])),
    }


def actionability_score(text: str) -> float:
    content = text.strip().lower()
    if not content:
        return 0.0
    score = 0.0
    if len(content) >= 40:
        score += 0.2
    if len(content) >= 90:
        score += 0.15
    if re.search(r"\b(add|attach|cite|report|include|provide|run|compare|replace|soften|link|document|measure|verify|remove)\b", content):
        score += 0.25
    if re.search(r"\b(metric|citation|evidence|baseline|experiment|table|claim|artifact|run|paper|code)\b", content):
        score += 0.25
    if re.search(r"\b(exact|specific|direct|linked|measured|supporting|parseable)\b", content):
        score += 0.15
    return round(min(1.0, score), 4)


def assessment_specificity_score(text: str) -> float:
    content = text.strip().lower()
    if not content:
        return 0.0
    score = 0.0
    if len(content) >= 35:
        score += 0.2
    if len(content) >= 90:
        score += 0.2
    if re.search(r"\b(evidence|citation|metric|baseline|claim|verdict|support|unsupported|contradict|gap|metadata|domain)\b", content):
        score += 0.35
    if re.search(r"\b(because|since|but|however|therefore|off-topic|missing|mismatch)\b", content):
        score += 0.25
    return round(min(1.0, score), 4)


def grounding_cue_score(text: str) -> float:
    content = text.strip().lower()
    if not content:
        return 0.0
    cues = [
        r"\bevidence\b",
        r"\bcitation\b",
        r"\bmetric\b",
        r"\bbaseline\b",
        r"\bclaim\b",
        r"\bmetadata\b",
        r"\bdomain\b",
        r"\bexperiment\b",
        r"\bartifact\b",
    ]
    hits = sum(1 for pattern in cues if re.search(pattern, content))
    return round(min(1.0, hits / 4.0), 4)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--output", default="experiments/reviewx_eval/outputs/analysis.csv")
    args = parser.parse_args()

    rows = read_jsonl(args.predictions)
    grouped: dict[str, list[dict[str, float]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("method") or "unknown")].append(record_metrics(row))

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "method",
        "count",
        "meanMismatch",
        "rawMeanMismatch",
        "calibrationGain",
        "highMismatchClaimCount",
        "findingCount",
        "blockerCount",
        "unsupportedFindingCount",
        "artifactAbsentFindingCount",
        "needsHumanVerificationFindingCount",
        "llmCallCount",
        "estimatedTokenCost",
        "selectedFindingCount",
        "validReviewerDecisionCount",
        "overestimatedReviewerDecisionCount",
        "llmAddedFindingCount",
        "lowConfidenceCitationFindingCount",
        "selectedLowConfidenceCitationCount",
        "tokenPerSelectedFinding",
        "tokenPerValidReviewerDecision",
        "selectedActionabilityScore",
        "selectedAssessmentSpecificityScore",
        "selectedGroundingCueScore",
        "selectedReviewerAssessmentRate",
        "revisionFeedbackCount",
    ]
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for method, metrics in sorted(grouped.items()):
            writer.writerow({
                "method": method,
                **{field: aggregate(field, [item[field] for item in metrics]) for field in fields if field != "method"},
            })
    print(f"methods={len(grouped)} output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
