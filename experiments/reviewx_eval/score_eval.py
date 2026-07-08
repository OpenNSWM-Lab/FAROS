#!/usr/bin/env python3
"""Score ReviewX JSONL predictions against lightweight gold labels."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


def read_jsonl(path: str) -> list[dict[str, Any]]:
    rows = []
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
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


def f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def finding_matches_gold(finding: dict[str, Any], claim_scores: list[dict[str, Any]], gold: dict[str, Any]) -> bool:
    expected_risk = gold.get("expectedRiskType")
    expected_support = gold.get("expectedSupportStatus")
    target_claim = str(gold.get("targetClaimText") or "")
    target_section = gold.get("targetSection")

    risk_match = bool(expected_risk and finding.get("riskType") == expected_risk)
    support_match = bool(expected_support and finding.get("supportStatus") == expected_support)
    title_overlap = jaccard(str(finding.get("title") or ""), target_claim) >= 0.12
    finding_claim_id = finding.get("claimId")
    claim_overlap = False
    if finding_claim_id:
        for claim in claim_scores:
            if claim.get("claimId") == finding_claim_id:
                claim_overlap = jaccard(str(claim.get("text") or ""), target_claim) >= 0.16
                break
    section_match = bool(target_section and finding.get("location", {}).get("section") == target_section)
    return (risk_match or support_match) and (claim_overlap or title_overlap or section_match or not target_claim)


def record_detects_gold(record: dict[str, Any], gold: dict[str, Any]) -> bool:
    claim_scores = record.get("claimScores", [])
    for finding in record.get("findings", []):
        if finding_matches_gold(finding, claim_scores, gold):
            return True

    expected_support = gold.get("expectedSupportStatus")
    target_claim = str(gold.get("targetClaimText") or "")
    if expected_support:
        for claim in claim_scores:
            support_match = claim.get("supportStatus") == expected_support
            text_match = jaccard(str(claim.get("text") or ""), target_claim) >= 0.16 if target_claim else True
            if support_match and text_match:
                return True
    return False


def gold_issue_type(gold: dict[str, Any]) -> str:
    support = gold.get("expectedSupportStatus")
    if support in {"unsupported", "contradicted"}:
        return support
    corruption = str(gold.get("corruptionType") or "issue")
    if "numeric" in corruption or "guardrail" in corruption:
        return "contradicted"
    return "unsupported"


def summarize_record(record: dict[str, Any]) -> dict[str, float]:
    metrics = record.get("metrics", {})
    mismatch = record.get("mismatchAggregate", {})
    model_trace = record.get("modelTrace", {})
    return {
        "meanMismatch": float(mismatch.get("meanMismatch", metrics.get("meanMismatch", 0)) or 0),
        "maxMismatch": float(mismatch.get("maxMismatch", metrics.get("maxMismatch", 0)) or 0),
        "highMismatchClaimCount": float(mismatch.get("highMismatchClaimCount", metrics.get("highMismatchClaimCount", 0)) or 0),
        "findingCount": float(metrics.get("findingCount", len(record.get("findings", []))) or 0),
        "blockerCount": float(metrics.get("blockerCount", 0) or 0),
        "actionItemCount": float(len(record.get("actionItems", []))),
        "estimatedTokenCost": float(model_trace.get("estimatedTokenCost", 0) or 0),
        "llmCallCount": float(model_trace.get("llmCallCount", 0) or 0),
        "runnerElapsedMs": float(record.get("runnerElapsedMs", 0) or 0),
    }


def mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--gold", required=True)
    parser.add_argument("--output", default="experiments/reviewx_eval/outputs/scores.json")
    parser.add_argument("--csv-output", default="experiments/reviewx_eval/outputs/scores.csv")
    args = parser.parse_args()

    predictions = read_jsonl(args.predictions)
    gold_rows = read_jsonl(args.gold)
    gold_by_sample: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for gold in gold_rows:
        gold_by_sample[str(gold.get("sampleId") or gold.get("paperId"))].append(gold)

    records_by_method: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in predictions:
        records_by_method[str(record.get("method") or record.get("budgetMode") or "unknown")].append(record)

    results: dict[str, Any] = {"methods": {}, "goldCount": len(gold_rows), "predictionCount": len(predictions)}
    for method, records in sorted(records_by_method.items()):
        tp_by_type = defaultdict(int)
        fp_by_type = defaultdict(int)
        fn_by_type = defaultdict(int)
        aggregate_values: dict[str, list[float]] = defaultdict(list)

        for record in records:
            sample_id = str(record.get("sampleId") or record.get("paperId"))
            sample_gold = gold_by_sample.get(sample_id, [])
            matched_gold_indexes = set()

            for finding in record.get("findings", []):
                matched_type = None
                for idx, gold in enumerate(sample_gold):
                    if idx in matched_gold_indexes:
                        continue
                    if finding_matches_gold(finding, record.get("claimScores", []), gold):
                        matched_gold_indexes.add(idx)
                        matched_type = gold_issue_type(gold)
                        break
                if matched_type:
                    tp_by_type[matched_type] += 1
                else:
                    issue_type = finding.get("supportStatus") or finding.get("riskType") or "issue"
                    if issue_type in {"unsupported", "contradicted"}:
                        fp_by_type[issue_type] += 1

            for idx, gold in enumerate(sample_gold):
                issue_type = gold_issue_type(gold)
                if idx not in matched_gold_indexes and record_detects_gold(record, gold):
                    tp_by_type[issue_type] += 1
                    matched_gold_indexes.add(idx)
                if idx not in matched_gold_indexes:
                    fn_by_type[issue_type] += 1

            for key, value in summarize_record(record).items():
                if not math.isnan(value):
                    aggregate_values[key].append(value)

        method_result: dict[str, Any] = {"count": len(records)}
        for issue_type in ["unsupported", "contradicted"]:
            tp = tp_by_type[issue_type]
            fp = fp_by_type[issue_type]
            fn = fn_by_type[issue_type]
            precision = tp / (tp + fp) if tp + fp else 0.0
            recall = tp / (tp + fn) if tp + fn else 0.0
            method_result[f"{issue_type}Precision"] = round(precision, 4)
            method_result[f"{issue_type}Recall"] = round(recall, 4)
            method_result[f"{issue_type}F1"] = round(f1(precision, recall), 4)
            method_result[f"{issue_type}TP"] = tp
            method_result[f"{issue_type}FP"] = fp
            method_result[f"{issue_type}FN"] = fn
        for key, values in aggregate_values.items():
            method_result[key] = round(mean(values), 4)
        results["methods"][method] = method_result

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    csv_output = Path(args.csv_output)
    csv_output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({key for value in results["methods"].values() for key in value.keys()})
    with csv_output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["method", *fieldnames])
        writer.writeheader()
        for method, values in sorted(results["methods"].items()):
            writer.writerow({"method": method, **values})

    print(f"methods={len(results['methods'])} output={output} csv={csv_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
