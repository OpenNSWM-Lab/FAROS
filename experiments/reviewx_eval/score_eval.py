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


SUPPORT_STATUSES = (
    "supported",
    "weakly_supported",
    "artifact_absent",
    "needs_human_verification",
    "unsupported",
    "contradicted",
)


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
    location_match = claim_overlap or title_overlap if target_claim else section_match
    return (risk_match or support_match) and (location_match or not target_claim)


def target_diagnostics(record: dict[str, Any], gold: dict[str, Any]) -> dict[str, Any]:
    target_claim = str(gold.get("targetClaimText") or "")
    if not target_claim:
        return {
            "extracted": False,
            "flagged": False,
            "bestOverlap": 0.0,
            "observedSupportStatus": None,
            "observedSupportStatuses": [],
        }
    ranked = sorted(
        (
            (jaccard(str(claim.get("text") or ""), target_claim), claim)
            for claim in record.get("claimScores", [])
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    best_overlap, best_claim = ranked[0] if ranked else (0.0, {})
    extracted = best_overlap >= 0.16
    claim_id = best_claim.get("claimId") if extracted else None
    flagged = bool(claim_id and any(
        finding.get("claimId") == claim_id
        for finding in record.get("findings", [])
    ))
    observed_statuses = set(best_claim.get("supportStatuses") or []) if extracted else set()
    if extracted and best_claim.get("supportStatus"):
        observed_statuses.add(best_claim.get("supportStatus"))
    return {
        "extracted": extracted,
        "flagged": flagged,
        "bestOverlap": round(best_overlap, 4),
        "observedSupportStatus": best_claim.get("supportStatus") if extracted else None,
        "observedSupportStatuses": sorted(observed_statuses),
    }


def record_detects_gold(record: dict[str, Any], gold: dict[str, Any]) -> bool:
    claim_scores = record.get("claimScores", [])
    for finding in record.get("findings", []):
        if finding_matches_gold(finding, claim_scores, gold):
            return True

    expected_support = gold.get("expectedSupportStatus")
    target_claim = str(gold.get("targetClaimText") or "")
    if expected_support:
        for claim in claim_scores:
            observed_statuses = set(claim.get("supportStatuses") or [])
            if claim.get("supportStatus"):
                observed_statuses.add(claim.get("supportStatus"))
            support_match = expected_support in observed_statuses
            text_match = jaccard(str(claim.get("text") or ""), target_claim) >= 0.16 if target_claim else True
            if support_match and text_match:
                return True
    return False


def matching_finding_ids(record: dict[str, Any], gold: dict[str, Any]) -> list[str]:
    claim_scores = record.get("claimScores", [])
    matches = []
    for finding in record.get("findings", []):
        if finding_matches_gold(finding, claim_scores, gold):
            matches.append(str(finding.get("id") or ""))
    return [item for item in matches if item]


def gold_issue_type(gold: dict[str, Any]) -> str:
    support = gold.get("expectedSupportStatus")
    if support in SUPPORT_STATUSES:
        return str(support)
    return "unsupported"


def is_issue_finding(finding: dict[str, Any]) -> bool:
    support = finding.get("supportStatus")
    risk = finding.get("riskType")
    severity = finding.get("severity")
    return (
        support in {"unsupported", "contradicted"}
        or risk in {"unsupported_claim", "traceability_gap", "citation_mismatch", "metric_mismatch"}
        or severity == "blocker"
    )


def summarize_record(record: dict[str, Any]) -> dict[str, float]:
    metrics = record.get("metrics", {})
    mismatch = record.get("mismatchAggregate", {})
    model_trace = record.get("modelTrace", {})
    claim_scores = record.get("claimScores", [])
    raw_values = [
        float(item.get("rawMismatchScore", item.get("mismatchScore", 0)) or 0)
        for item in claim_scores
    ]
    final_values = [
        float(item.get("mismatchScore", 0) or 0)
        for item in claim_scores
    ]
    raw_mean = mean(raw_values)
    final_mean = mean(final_values)
    llm_calibrated = len([
        item for item in claim_scores
        if (item.get("calibration") or {}).get("llmDecision")
    ])
    revision_calibrated = len([
        item for item in claim_scores
        if float((item.get("calibration") or {}).get("revisionAdjustment", 0) or 0) > 0
    ])
    findings = record.get("findings", [])
    return {
        "meanMismatch": float(mismatch.get("meanMismatch", metrics.get("meanMismatch", 0)) or 0),
        "rawMeanMismatch": raw_mean,
        "mismatchCalibrationGain": max(0.0, raw_mean - final_mean),
        "maxMismatch": float(mismatch.get("maxMismatch", metrics.get("maxMismatch", 0)) or 0),
        "highMismatchClaimCount": float(mismatch.get("highMismatchClaimCount", metrics.get("highMismatchClaimCount", 0)) or 0),
        "findingCount": float(metrics.get("findingCount", len(findings)) or 0),
        "blockerCount": float(metrics.get("blockerCount", 0) or 0),
        "unsupportedFindingCount": float(len([item for item in findings if item.get("supportStatus") == "unsupported"])),
        "artifactAbsentFindingCount": float(len([item for item in findings if item.get("supportStatus") == "artifact_absent"])),
        "needsHumanVerificationFindingCount": float(len([
            item for item in findings if item.get("supportStatus") == "needs_human_verification"
        ])),
        "actionItemCount": float(len(record.get("actionItems", []))),
        "estimatedTokenCost": float(model_trace.get("estimatedTokenCost", 0) or 0),
        "llmCallCount": float(model_trace.get("llmCallCount", 0) or 0),
        "selectedFindingCount": float(model_trace.get("selectedFindingCount", 0) or 0),
        "llmCalibratedClaimCount": float(llm_calibrated),
        "revisionCalibratedClaimCount": float(revision_calibrated),
        "runnerElapsedMs": float(record.get("runnerElapsedMs", 0) or 0),
    }


def llm_quality_metrics(record: dict[str, Any], sample_gold: list[dict[str, Any]]) -> dict[str, float]:
    findings = record.get("findings", [])
    model_trace = record.get("modelTrace", {})
    routing = model_trace.get("llmRouting", {}) if isinstance(model_trace.get("llmRouting"), dict) else {}
    selected_ids = set(str(item) for item in routing.get("selectedFindingIds", []) or [])
    if not selected_ids:
        selected_ids = set(str(item) for item in model_trace.get("selectedFindingIds", []) or [])
    selected_findings = [finding for finding in findings if str(finding.get("id") or "") in selected_ids]
    low_confidence_citation_findings = [
        finding for finding in findings
        if (finding.get("cemCalibration") or {}).get("lowConfidenceCitation")
    ]
    selected_low_confidence = [
        finding for finding in selected_findings
        if (finding.get("cemCalibration") or {}).get("lowConfidenceCitation")
    ]
    reviewer_decisions = [
        str(finding.get("reviewerDecision") or "")
        for finding in findings
        if finding.get("reviewerDecision")
    ]
    selected_gold_matches = 0
    if sample_gold and selected_findings:
        for finding in selected_findings:
            if any(finding_matches_gold(finding, record.get("claimScores", []), gold) for gold in sample_gold):
                selected_gold_matches += 1
        selected_gold_precision = selected_gold_matches / len(selected_findings)
    else:
        selected_gold_precision = math.nan

    token_cost = float(model_trace.get("estimatedTokenCost", 0) or 0)
    selected_count = float(len(selected_findings))
    valid_decision_count = float(len([
        decision for decision in reviewer_decisions
        if decision in {"valid", "partially_valid"}
    ]))
    selected_actionability_scores = [
        actionability_score(str(finding.get("suggestedFix") or ""))
        for finding in selected_findings
    ]
    selected_assessment_scores = [
        assessment_specificity_score(str(finding.get("reviewerAssessment") or ""))
        for finding in selected_findings
    ]
    selected_grounding_cues = [
        grounding_cue_score(
            " ".join([
                str(finding.get("reviewerAssessment") or ""),
                str(finding.get("suggestedFix") or ""),
                str(finding.get("description") or ""),
            ])
        )
        for finding in selected_findings
    ]
    return {
        "reviewerDecisionCount": float(len(reviewer_decisions)),
        "validReviewerDecisionCount": valid_decision_count,
        "overestimatedReviewerDecisionCount": float(len([
            decision for decision in reviewer_decisions
            if decision == "overestimated"
        ])),
        "llmAddedFindingCount": float(len([
            finding for finding in findings
            if (finding.get("cemCalibration") or {}).get("llmAddedFinding")
        ])),
        "lowConfidenceCitationFindingCount": float(len(low_confidence_citation_findings)),
        "selectedLowConfidenceCitationCount": float(len(selected_low_confidence)),
        "selectedFindingGoldMatchCount": float(selected_gold_matches),
        "selectedFindingGoldPrecision": float(selected_gold_precision),
        "tokenPerSelectedFinding": token_cost / selected_count if selected_count else 0.0,
        "tokenPerValidReviewerDecision": token_cost / valid_decision_count if valid_decision_count else 0.0,
        "selectedActionabilityScore": mean(selected_actionability_scores),
        "selectedAssessmentSpecificityScore": mean(selected_assessment_scores),
        "selectedGroundingCueScore": mean(selected_grounding_cues),
        "selectedReviewerAssessmentRate": (
            len([finding for finding in selected_findings if finding.get("reviewerAssessment")]) / selected_count
            if selected_count else 0.0
        ),
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


def sample_meta(record: dict[str, Any], samples_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    sample_id = str(record.get("sampleId") or record.get("paperId"))
    return samples_by_id.get(sample_id, {})


def source_paper_id(record: dict[str, Any], samples_by_id: dict[str, dict[str, Any]]) -> str:
    meta = sample_meta(record, samples_by_id)
    return str(record.get("sourcePaperId") or meta.get("sourcePaperId") or record.get("paperId") or "")


def sample_type(record: dict[str, Any], samples_by_id: dict[str, dict[str, Any]]) -> str:
    meta = sample_meta(record, samples_by_id)
    return str(record.get("sampleType") or meta.get("sampleType") or "")


def mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--gold", required=True)
    parser.add_argument("--samples", help="Optional JSONL sample manifest, used for clean-baseline deltas")
    parser.add_argument("--output", default="experiments/reviewx_eval/outputs/scores.json")
    parser.add_argument("--csv-output", default="experiments/reviewx_eval/outputs/scores.csv")
    parser.add_argument("--corruption-csv-output", help="Optional per-corruption detection CSV output")
    parser.add_argument("--sample-output", help="Optional sample-level JSONL output")
    parser.add_argument(
        "--ignore-unmatched-findings",
        action="store_true",
        help="Deprecated compatibility flag. Non-exhaustive gold is now the default.",
    )
    parser.add_argument(
        "--gold-is-exhaustive",
        action="store_true",
        help="Treat every unmatched issue finding as a false positive. Use only when every finding has been adjudicated.",
    )
    args = parser.parse_args()

    predictions = read_jsonl(args.predictions)
    gold_rows = read_jsonl(args.gold)
    sample_rows_input = read_jsonl(args.samples) if args.samples else []
    samples_by_id = {
        str(row.get("sampleId") or row.get("paperId")): row
        for row in sample_rows_input
    }
    gold_by_sample: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for gold in gold_rows:
        gold_by_sample[str(gold.get("sampleId") or gold.get("paperId"))].append(gold)

    records_by_method: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in predictions:
        records_by_method[str(record.get("method") or record.get("budgetMode") or "unknown")].append(record)

    results: dict[str, Any] = {
        "methods": {},
        "corruptions": {},
        "supportStatuses": {},
        "cleanControls": {},
        "goldCount": len(gold_rows),
        "predictionCount": len(predictions),
        "ignoreUnmatchedFindings": not args.gold_is_exhaustive,
        "goldIsExhaustive": args.gold_is_exhaustive,
        "precisionScope": "exhaustive_gold" if args.gold_is_exhaustive else "targeted_gold_only",
    }
    sample_rows: list[dict[str, Any]] = []
    for method, records in sorted(records_by_method.items()):
        tp_by_type = defaultdict(int)
        fp_by_type = defaultdict(int)
        fn_by_type = defaultdict(int)
        detected_by_corruption = defaultdict(int)
        total_by_corruption = defaultdict(int)
        aggregate_values: dict[str, list[float]] = defaultdict(list)
        clean_record_count = 0
        clean_flagged_count = 0
        clean_issue_finding_count = 0
        clean_baseline_by_source: dict[str, int] = {}
        delta_from_clean_values: list[float] = []
        all_issue_finding_count = 0
        matched_gold_issue_finding_count = 0
        unmatched_issue_finding_count = 0
        target_count = 0
        target_extracted_count = 0
        target_flagged_count = 0

        for record in records:
            sample_id = str(record.get("sampleId") or record.get("paperId"))
            sample_gold = gold_by_sample.get(sample_id, [])
            if sample_gold:
                continue
            issue_findings = [finding for finding in record.get("findings", []) if is_issue_finding(finding)]
            source_id = source_paper_id(record, samples_by_id)
            if source_id:
                clean_baseline_by_source[source_id] = len(issue_findings)

        for record in records:
            sample_id = str(record.get("sampleId") or record.get("paperId"))
            sample_gold = gold_by_sample.get(sample_id, [])
            matched_gold_indexes = set()
            issue_findings = [finding for finding in record.get("findings", []) if is_issue_finding(finding)]
            source_id = source_paper_id(record, samples_by_id)
            baseline_issue_count = clean_baseline_by_source.get(source_id)

            if not sample_gold:
                clean_record_count += 1
                clean_issue_finding_count += len(issue_findings)
                if issue_findings:
                    clean_flagged_count += 1
                sample_rows.append({
                    "method": method,
                    "sampleId": sample_id,
                    "paperId": record.get("paperId"),
                    "sourcePaperId": source_id or None,
                    "sampleType": sample_type(record, samples_by_id),
                    "corruptionType": "clean_control",
                    "issueType": "clean_control",
                    "detected": bool(issue_findings),
                    "matchedFindingIds": [str(item.get("id") or "") for item in issue_findings if item.get("id")],
                    "issueFindingCount": len(issue_findings),
                    "cleanBaselineIssueFindingCount": None,
                    "issueFindingDeltaFromClean": None,
                    "targetClaimText": None,
                    "expectedSupportStatus": None,
                    "expectedRiskType": None,
                    "targetExtracted": None,
                    "targetFlagged": None,
                    "targetBestOverlap": None,
                    "targetObservedSupportStatus": None,
                    "targetObservedSupportStatuses": None,
                })

            for finding in record.get("findings", []):
                matched_type = None
                issue_finding = is_issue_finding(finding)
                if issue_finding:
                    all_issue_finding_count += 1
                for idx, gold in enumerate(sample_gold):
                    if idx in matched_gold_indexes:
                        continue
                    if finding_matches_gold(finding, record.get("claimScores", []), gold):
                        matched_gold_indexes.add(idx)
                        matched_type = gold_issue_type(gold)
                        break
                if matched_type:
                    tp_by_type[matched_type] += 1
                    if issue_finding:
                        matched_gold_issue_finding_count += 1
                elif issue_finding:
                    unmatched_issue_finding_count += 1
                if not matched_type and issue_finding and args.gold_is_exhaustive:
                    issue_type = finding.get("supportStatus") or finding.get("riskType") or "issue"
                    if issue_type in {"unsupported", "contradicted"}:
                        fp_by_type[issue_type] += 1
                    else:
                        fp_by_type["unsupported"] += 1

            for idx, gold in enumerate(sample_gold):
                target_count += 1
                diagnostics = target_diagnostics(record, gold)
                target_extracted_count += int(diagnostics["extracted"])
                target_flagged_count += int(diagnostics["flagged"])
                issue_type = gold_issue_type(gold)
                corruption_type = str(gold.get("corruptionType") or issue_type)
                total_by_corruption[corruption_type] += 1
                detected = idx in matched_gold_indexes or record_detects_gold(record, gold)
                matched_findings = matching_finding_ids(record, gold)
                if detected:
                    detected_by_corruption[corruption_type] += 1
                sample_rows.append({
                    "method": method,
                    "sampleId": sample_id,
                    "paperId": record.get("paperId"),
                    "sourcePaperId": source_id or None,
                    "sampleType": sample_type(record, samples_by_id),
                    "corruptionType": corruption_type,
                    "issueType": issue_type,
                    "detected": detected,
                    "matchedFindingIds": matched_findings,
                    "issueFindingCount": len(issue_findings),
                    "cleanBaselineIssueFindingCount": baseline_issue_count,
                    "issueFindingDeltaFromClean": (
                        len(issue_findings) - baseline_issue_count
                        if baseline_issue_count is not None
                        else None
                    ),
                    "targetClaimText": gold.get("targetClaimText"),
                    "expectedSupportStatus": gold.get("expectedSupportStatus"),
                    "expectedRiskType": gold.get("expectedRiskType"),
                    "targetExtracted": diagnostics["extracted"],
                    "targetFlagged": diagnostics["flagged"],
                    "targetBestOverlap": diagnostics["bestOverlap"],
                    "targetObservedSupportStatus": diagnostics["observedSupportStatus"],
                    "targetObservedSupportStatuses": diagnostics["observedSupportStatuses"],
                })
                if baseline_issue_count is not None:
                    delta_from_clean_values.append(float(len(issue_findings) - baseline_issue_count))
                if idx not in matched_gold_indexes and detected:
                    tp_by_type[issue_type] += 1
                    matched_gold_indexes.add(idx)
                if idx not in matched_gold_indexes:
                    fn_by_type[issue_type] += 1

            for key, value in summarize_record(record).items():
                if not math.isnan(value):
                    aggregate_values[key].append(value)
            for key, value in llm_quality_metrics(record, sample_gold).items():
                if not math.isnan(value):
                    aggregate_values[key].append(value)

        method_result: dict[str, Any] = {"count": len(records)}
        for issue_type in ["unsupported", "contradicted"]:
            tp = tp_by_type[issue_type]
            fp = fp_by_type[issue_type]
            fn = fn_by_type[issue_type]
            precision = tp / (tp + fp) if tp + fp else 0.0
            targeted_precision = 1.0 if tp else 0.0
            recall = tp / (tp + fn) if tp + fn else 0.0
            method_result[f"{issue_type}Precision"] = round(precision, 4) if args.gold_is_exhaustive else None
            method_result[f"{issue_type}Recall"] = round(recall, 4)
            method_result[f"{issue_type}F1"] = round(f1(precision, recall), 4) if args.gold_is_exhaustive else None
            method_result[f"{issue_type}TargetedPrecision"] = round(targeted_precision, 4)
            method_result[f"{issue_type}TargetedF1"] = round(f1(targeted_precision, recall), 4)
            method_result[f"{issue_type}TP"] = tp
            method_result[f"{issue_type}FP"] = fp
            method_result[f"{issue_type}FN"] = fn
        support_status_result: dict[str, Any] = {}
        for support_status in SUPPORT_STATUSES:
            tp = tp_by_type[support_status]
            fn = fn_by_type[support_status]
            total = tp + fn
            if not total:
                continue
            support_status_result[support_status] = {
                "detected": tp,
                "total": total,
                "detectionRate": round(tp / total, 4),
            }
        triage_tp = sum(tp_by_type[status] for status in SUPPORT_STATUSES)
        triage_fn = sum(fn_by_type[status] for status in SUPPORT_STATUSES)
        method_result["triageTP"] = triage_tp
        method_result["triageFN"] = triage_fn
        method_result["triageRecall"] = round(
            triage_tp / (triage_tp + triage_fn),
            4,
        ) if triage_tp + triage_fn else 0.0
        method_result["targetCount"] = target_count
        method_result["targetExtractedCount"] = target_extracted_count
        method_result["targetExtractionRecall"] = round(
            target_extracted_count / target_count,
            4,
        ) if target_count else 0.0
        method_result["targetFlaggedCount"] = target_flagged_count
        method_result["targetFlagRecall"] = round(
            target_flagged_count / target_count,
            4,
        ) if target_count else 0.0
        for key, values in aggregate_values.items():
            method_result[key] = round(mean(values), 4)
        method_result["cleanRecordCount"] = clean_record_count
        method_result["cleanFlaggedCount"] = clean_flagged_count
        method_result["cleanFlagRate"] = round(clean_flagged_count / clean_record_count, 4) if clean_record_count else 0.0
        method_result["cleanIssueFindingCount"] = clean_issue_finding_count
        method_result["cleanIssueFindingsPerSample"] = round(clean_issue_finding_count / clean_record_count, 4) if clean_record_count else 0.0
        method_result["variantCountWithCleanBaseline"] = len(delta_from_clean_values)
        method_result["issueFindingDeltaFromClean"] = round(mean(delta_from_clean_values), 4)
        method_result["goldIsExhaustive"] = args.gold_is_exhaustive
        method_result["precisionScope"] = "exhaustive_gold" if args.gold_is_exhaustive else "targeted_gold_only"
        method_result["allIssueFindingCount"] = all_issue_finding_count
        method_result["matchedGoldIssueFindingCount"] = matched_gold_issue_finding_count
        method_result["unmatchedIssueFindingCount"] = unmatched_issue_finding_count
        method_result["goldMatchRateAmongIssueFindings"] = round(
            matched_gold_issue_finding_count / all_issue_finding_count,
            4,
        ) if all_issue_finding_count else 0.0
        results["methods"][method] = method_result
        results["supportStatuses"][method] = support_status_result
        results["corruptions"][method] = {
            corruption: {
                "detected": detected_by_corruption[corruption],
                "total": total,
                "detectionRate": round(detected_by_corruption[corruption] / total, 4) if total else 0.0,
            }
            for corruption, total in sorted(total_by_corruption.items())
        }
        results["cleanControls"][method] = {
            "cleanRecordCount": clean_record_count,
            "cleanFlaggedCount": clean_flagged_count,
            "cleanFlagRate": round(clean_flagged_count / clean_record_count, 4) if clean_record_count else 0.0,
            "cleanIssueFindingCount": clean_issue_finding_count,
            "cleanIssueFindingsPerSample": round(clean_issue_finding_count / clean_record_count, 4) if clean_record_count else 0.0,
            "cleanBaselineSourceCount": len(clean_baseline_by_source),
        }

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

    if args.corruption_csv_output:
        corruption_output = Path(args.corruption_csv_output)
        corruption_output.parent.mkdir(parents=True, exist_ok=True)
        with corruption_output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["method", "corruptionType", "detected", "total", "detectionRate"])
            writer.writeheader()
            for method, corruptions in sorted(results["corruptions"].items()):
                for corruption, values in sorted(corruptions.items()):
                    writer.writerow({"method": method, "corruptionType": corruption, **values})

    if args.sample_output:
        sample_output = Path(args.sample_output)
        sample_output.parent.mkdir(parents=True, exist_ok=True)
        with sample_output.open("w", encoding="utf-8") as handle:
            for row in sample_rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"methods={len(results['methods'])} output={output} csv={csv_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
