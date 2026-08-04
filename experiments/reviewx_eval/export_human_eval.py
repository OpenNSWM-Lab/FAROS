#!/usr/bin/env python3
"""Export ReviewX selected findings for human quality annotation.

The output is intentionally flat CSV/JSONL so annotators can score whether an
LLM-refined finding is correct, specific, grounded, and actionable.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
from pathlib import Path
from typing import Any


HUMAN_FIELDS = [
    "annotationId",
    "method",
    "sampleId",
    "paperId",
    "sourcePaperId",
    "findingId",
    "claimId",
    "claimText",
    "claimSection",
    "riskType",
    "supportStatus",
    "severity",
    "confidence",
    "reviewerDecision",
    "reviewerAssessment",
    "findingTitle",
    "findingDescription",
    "suggestedFix",
    "lowConfidenceCitation",
    "citationMismatchReasons",
    "claimDomainTerms",
    "selectedFindingGoldMatch",
    "matchedGoldCorruptionType",
    "estimatedTokenCost",
    "tokenPerSelectedFinding",
    "humanCorrectness",
    "humanActionability",
    "humanSpecificity",
    "humanGrounding",
    "humanSeverityAgreement",
    "humanNotes",
]


def read_jsonl(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    rows = []
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line and not line.startswith("#"):
                rows.append(json.loads(line))
    return rows


def tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", text.lower())}


def jaccard(left: str, right: str) -> float:
    a = tokens(left)
    b = tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def finding_matches_gold(
    finding: dict[str, Any],
    claim: dict[str, Any],
    gold: dict[str, Any],
) -> bool:
    expected_risk = gold.get("expectedRiskType")
    expected_support = gold.get("expectedSupportStatus")
    target_claim = str(gold.get("targetClaimText") or "")
    target_section = gold.get("targetSection")
    risk_match = bool(expected_risk and finding.get("riskType") == expected_risk)
    support_match = bool(expected_support and finding.get("supportStatus") == expected_support)
    claim_overlap = jaccard(str(claim.get("text") or ""), target_claim) >= 0.16 if target_claim else True
    section_match = bool(target_section and (claim.get("sourceSpan") or {}).get("section") == target_section)
    location_match = claim_overlap if target_claim else section_match
    return (risk_match or support_match) and (location_match or not target_claim)


def is_strict_finding(finding: dict[str, Any]) -> bool:
    return (
        finding.get("supportStatus") in {"unsupported", "contradicted"}
        or finding.get("riskType") in {
            "unsupported_claim", "traceability_gap", "citation_mismatch", "metric_mismatch",
        }
        or finding.get("severity") == "blocker"
    )


def gold_by_sample(gold_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in gold_rows:
        sample_id = str(row.get("sampleId") or row.get("paperId") or "")
        if sample_id:
            grouped.setdefault(sample_id, []).append(row)
    return grouped


def selected_ids(record: dict[str, Any]) -> set[str]:
    trace = record.get("modelTrace", {})
    routing = trace.get("llmRouting", {}) if isinstance(trace.get("llmRouting"), dict) else {}
    ids = routing.get("selectedFindingIds", []) or trace.get("selectedFindingIds", []) or []
    return {str(item) for item in ids}


def claim_scores_by_id(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("claimId")): item
        for item in record.get("claimScores", [])
        if item.get("claimId")
    }


def citation_semantic(finding: dict[str, Any]) -> dict[str, Any]:
    calibration = finding.get("cemCalibration") or {}
    citation = calibration.get("citationSemantic")
    return citation if isinstance(citation, dict) else {}


def row_for_finding(
    *,
    record: dict[str, Any],
    finding: dict[str, Any],
    claim: dict[str, Any],
    sample_gold: list[dict[str, Any]],
    annotation_index: int,
) -> dict[str, Any]:
    citation = citation_semantic(finding)
    matched_gold = None
    for gold in sample_gold:
        if finding_matches_gold(finding, claim, gold):
            matched_gold = gold
            break
    token_cost = float((record.get("modelTrace") or {}).get("estimatedTokenCost", 0) or 0)
    selected_count = max(1, len(selected_ids(record)))
    return {
        "annotationId": f"ann_{annotation_index:05d}",
        "method": record.get("method") or record.get("budgetMode") or "",
        "sampleId": record.get("sampleId") or "",
        "paperId": record.get("paperId") or "",
        "sourcePaperId": record.get("sourcePaperId") or "",
        "findingId": finding.get("id") or "",
        "claimId": finding.get("claimId") or "",
        "claimText": claim.get("text") or "",
        "claimSection": (claim.get("sourceSpan") or {}).get("section") or "",
        "riskType": finding.get("riskType") or "",
        "supportStatus": finding.get("supportStatus") or "",
        "severity": finding.get("severity") or "",
        "confidence": finding.get("confidence"),
        "reviewerDecision": finding.get("reviewerDecision") or "",
        "reviewerAssessment": finding.get("reviewerAssessment") or "",
        "findingTitle": finding.get("title") or "",
        "findingDescription": finding.get("description") or "",
        "suggestedFix": finding.get("suggestedFix") or "",
        "lowConfidenceCitation": bool((finding.get("cemCalibration") or {}).get("lowConfidenceCitation")),
        "citationMismatchReasons": ";".join(str(item) for item in citation.get("mismatchReasons", []) or []),
        "claimDomainTerms": ";".join(str(item) for item in citation.get("claimDomainTerms", []) or []),
        "selectedFindingGoldMatch": bool(matched_gold),
        "matchedGoldCorruptionType": (matched_gold or {}).get("corruptionType", ""),
        "estimatedTokenCost": token_cost,
        "tokenPerSelectedFinding": round(token_cost / selected_count, 4),
        "humanCorrectness": "",
        "humanActionability": "",
        "humanSpecificity": "",
        "humanGrounding": "",
        "humanSeverityAgreement": "",
        "humanNotes": "",
    }


def export_rows(
    predictions: list[dict[str, Any]],
    gold_rows: list[dict[str, Any]],
    selected_only: bool,
    strict_only: bool,
    blind: bool,
) -> list[dict[str, Any]]:
    grouped_gold = gold_by_sample(gold_rows)
    rows = []
    for record in predictions:
        selected = selected_ids(record)
        claims = claim_scores_by_id(record)
        sample_id = str(record.get("sampleId") or record.get("paperId") or "")
        sample_gold = grouped_gold.get(sample_id, [])
        for finding in record.get("findings", []):
            if strict_only and not is_strict_finding(finding):
                continue
            finding_id = str(finding.get("id") or "")
            if selected_only and finding_id not in selected:
                continue
            claim = claims.get(str(finding.get("claimId") or ""), {})
            rows.append(row_for_finding(
                record=record,
                finding=finding,
                claim=claim,
                sample_gold=sample_gold,
                annotation_index=len(rows) + 1,
            ))
    return blind_rows(rows) if blind else rows


def blind_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    method_codes: dict[str, str] = {}
    sample_codes: dict[str, str] = {}
    paper_codes: dict[str, str] = {}
    blinded = []
    for row in rows:
        method = str(row.get("method") or "unknown")
        if method not in method_codes:
            digest = hashlib.sha1(method.encode("utf-8")).hexdigest()[:6]
            method_codes[method] = f"method_{digest}"
        sample_id = str(row.get("sampleId") or "unknown")
        paper_id = str(row.get("paperId") or "unknown")
        if sample_id not in sample_codes:
            digest = hashlib.sha1(f"sample|{sample_id}".encode()).hexdigest()[:10]
            sample_codes[sample_id] = f"sample_{digest}"
        if paper_id not in paper_codes:
            digest = hashlib.sha1(f"paper|{paper_id}".encode()).hexdigest()[:10]
            paper_codes[paper_id] = f"paper_{digest}"
        copy = dict(row)
        copy["method"] = method_codes[method]
        copy["sampleId"] = sample_codes[sample_id]
        copy["paperId"] = paper_codes[paper_id]
        copy["sourcePaperId"] = ""
        copy["selectedFindingGoldMatch"] = ""
        copy["matchedGoldCorruptionType"] = ""
        copy["estimatedTokenCost"] = ""
        copy["tokenPerSelectedFinding"] = ""
        blinded.append(copy)
    return blinded


def shuffled_rows(rows: list[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    shuffled = [dict(row) for row in rows]
    random.Random(seed).shuffle(shuffled)
    for index, row in enumerate(shuffled, start=1):
        row["annotationId"] = f"ann_{index:05d}"
    return shuffled


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=HUMAN_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", action="append", required=True, help="Prediction JSONL. May be repeated.")
    parser.add_argument("--gold", help="Optional gold labels for selected finding match flags")
    parser.add_argument("--csv-output", required=True)
    parser.add_argument("--jsonl-output")
    parser.add_argument("--all-findings", action="store_true", help="Export every finding, not only LLM-selected findings")
    parser.add_argument(
        "--strict-findings-only",
        action="store_true",
        help="Keep only unsupported/contradicted or strict-risk findings.",
    )
    parser.add_argument("--blind", action="store_true", help="Mask method/gold/cost fields for unbiased human annotation")
    parser.add_argument("--shuffle-seed", type=int, help="Randomize row order reproducibly and regenerate annotation IDs")
    args = parser.parse_args()

    predictions = []
    for path in args.predictions:
        predictions.extend(read_jsonl(path))
    gold_rows = read_jsonl(args.gold)
    rows = export_rows(
        predictions,
        gold_rows,
        selected_only=not args.all_findings,
        strict_only=args.strict_findings_only,
        blind=args.blind,
    )
    if args.shuffle_seed is not None:
        rows = shuffled_rows(rows, args.shuffle_seed)
    write_csv(Path(args.csv_output), rows)
    if args.jsonl_output:
        write_jsonl(Path(args.jsonl_output), rows)
    print(f"rows={len(rows)} csv={args.csv_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
