"""Build one judge-facing, read-only evidence view for Challenge Cup track 1B."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from app.modules.review.effect_statistics import (
    exact_mcnemar_p_value,
    interval_effect_status,
)
from app.modules.review.plan_delta import verify_plan_delta_contract


def _read_json(path: Path, *, required: bool = True) -> Dict[str, Any]:
    if not path.is_file():
        if required:
            raise FileNotFoundError(path)
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected one JSON object in {path}")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _metric_rows(
    first: Dict[str, Any],
    second: Dict[str, Any],
    names: Iterable[str],
) -> list[Dict[str, Any]]:
    minimize = {"Brier Score", "Expected Calibration Error (ECE)"}
    rows = []
    for name in names:
        before = float(first[name])
        after = float(second[name])
        raw_delta = after - before
        rows.append({
            "name": name,
            "roundOne": before,
            "roundTwo": after,
            "delta": raw_delta,
            "direction": "minimize" if name in minimize else "maximize",
            "improved": raw_delta < 0 if name in minimize else raw_delta > 0,
        })
    return rows


def _candidate_rows(
    diagnostics: Dict[str, Any],
    contract: Dict[str, Any],
) -> list[Dict[str, Any]]:
    changes = {
        str(item.get("candidateId")): str(item.get("change") or "")
        for item in contract.get("candidates") or []
    }
    selected = str(contract.get("selectedCandidateId") or "")
    rows = []
    for candidate_id, item in diagnostics.items():
        checks = item.get("checks") or {}
        rows.append({
            "candidateId": candidate_id,
            "selected": candidate_id == selected,
            "feasible": bool(item.get("feasible")),
            "change": changes.get(candidate_id, ""),
            "metrics": item.get("metrics") or {},
            "failedConstraints": [
                name for name, passed in checks.items() if not passed
            ],
        })
    return sorted(
        rows,
        key=lambda item: (
            not item["selected"],
            not item["feasible"],
            -float((item["metrics"] or {}).get("F1-Score") or 0),
        ),
    )


def _multidomain_effect_rows(summary: Dict[str, Any]) -> list[Dict[str, Any]]:
    rows = []
    for dataset_name, result in (summary.get("results") or {}).items():
        metrics = result.get("results") or {}
        first = metrics.get("within_domain") or {}
        second = metrics.get("within_domain_calibrated") or {}
        inference = (
            (result.get("effectInference") or {}).get("Macro F1")
            or (((result.get("pairedBootstrap") or {}).get(
                "calibratedVsFixedWithinDomain"
            ) or {}).get("Macro F1"))
            or {}
        )
        selection = result.get("validationThresholdSelection") or {}
        ci_low = float(inference.get("ci95Low") or 0)
        ci_high = float(inference.get("ci95High") or 0)
        round_one = float(first.get("Macro F1") or 0)
        round_two = float(second.get("Macro F1") or 0)
        rows.append({
            "dataset": dataset_name,
            "roundOneMacroF1": round_one,
            "roundTwoMacroF1": round_two,
            "delta": round_two - round_one,
            "roundOneAccuracy": first.get("Accuracy"),
            "roundTwoAccuracy": second.get("Accuracy"),
            "roundOneBalancedAccuracy": first.get("Balanced Accuracy"),
            "roundTwoBalancedAccuracy": second.get("Balanced Accuracy"),
            "roundOneMcc": first.get("Matthews Correlation Coefficient"),
            "roundTwoMcc": second.get("Matthews Correlation Coefficient"),
            "ci95": [ci_low, ci_high],
            "probabilityOfImprovement": inference.get("probabilityOfImprovement"),
            "effectStatus": inference.get("effectStatus")
            or interval_effect_status(ci_low, ci_high),
            "resamplingUnit": inference.get("resamplingUnit") or "claim_id",
            "proposedThreshold": selection.get(
                "proposedThreshold", selection.get("selectedThreshold")
            ),
            "appliedThreshold": selection.get(
                "appliedThreshold", selection.get("selectedThreshold")
            ),
            "gateDecision": selection.get("gateDecision") or "legacy_apply_revision",
            "interventionAudit": result.get("interventionAudit") or {},
        })
    return sorted(rows, key=lambda item: item["dataset"])


def _reliability_paired_effects(
    reliability: Dict[str, Any],
    records: Dict[str, Any],
) -> Dict[str, Any]:
    scores = reliability.get("scores") or {}
    qwen = scores.get("qwen_only") or {}
    full = scores.get("faros_full") or {}
    faulty_cases = int(
        qwen.get("faultyCases")
        or (reliability.get("caseAudit") or {}).get("faulty")
        or 0
    )
    qwen_detection = int(
        (qwen.get("confusionMatrix") or {}).get("tp")
        or round(float(qwen.get("faultDetectionRate") or 0) * faulty_cases)
    )
    full_detection = int(
        (full.get("confusionMatrix") or {}).get("tp")
        or round(float(full.get("faultDetectionRate") or 0) * faulty_cases)
    )
    qwen_localization = int(round(float(qwen.get("issueLocalizationRate") or 0) * faulty_cases))
    full_localization = int(round(float(full.get("issueLocalizationRate") or 0) * faulty_cases))

    cases = [
        item for item in records.get("cases") or []
        if isinstance(item, dict) and item.get("isFaulty") is True
    ]
    predictions = records.get("predictions") or {}
    qwen_predictions = predictions.get("qwen_only") or {}
    full_predictions = predictions.get("faros_full") or {}
    paired_records_available = bool(
        cases
        and len(cases) == faulty_cases
        and all(
            item.get("caseId") in qwen_predictions
            and item.get("caseId") in full_predictions
            for item in cases
        )
    )

    def paired_correctness(endpoint: str) -> tuple[list[bool], list[bool]]:
        before: list[bool] = []
        after: list[bool] = []
        for item in cases:
            case_id = str(item["caseId"])
            qwen_prediction = qwen_predictions[case_id]
            full_prediction = full_predictions[case_id]
            if endpoint == "detection":
                before.append(qwen_prediction.get("decision") == "reject")
                after.append(full_prediction.get("decision") == "reject")
            else:
                expected = str(item.get("expectedIssueCode") or "")
                before.append(expected in (qwen_prediction.get("issues") or []))
                after.append(expected in (full_prediction.get("issues") or []))
        return before, after

    def effect(
        before_count: int,
        after_count: int,
        endpoint: str,
    ) -> Dict[str, Any]:
        if paired_records_available:
            before, after = paired_correctness(endpoint)
            corrected = sum(not first and second for first, second in zip(before, after))
            regressed = sum(first and not second for first, second in zip(before, after))
            before_count = sum(before)
            after_count = sum(after)
            evidence_source = "evaluation_records.json"
            paired_inference_available = True
        elif after_count == faulty_cases and before_count <= after_count:
            corrected = after_count - before_count
            regressed = 0
            evidence_source = "aggregate endpoint uniquely determines discordance"
            paired_inference_available = True
        else:
            corrected = 0
            regressed = 0
            evidence_source = "paired records unavailable"
            paired_inference_available = False
        p_value = exact_mcnemar_p_value(corrected, regressed)
        effect_status = "inconclusive"
        if paired_inference_available and p_value < 0.05:
            if corrected > regressed:
                effect_status = "significant_improvement"
            elif regressed > corrected:
                effect_status = "significant_regression"
        return {
            "beforeCorrect": before_count,
            "afterCorrect": after_count,
            "total": faulty_cases,
            "corrected": corrected,
            "regressed": regressed,
            "exactMcNemarPValue": p_value,
            "pairedInferenceAvailable": paired_inference_available,
            "evidenceSource": evidence_source,
            "effectStatus": effect_status,
        }

    return {
        "comparison": "FAROS full minus Qwen only",
        "faultDetection": effect(qwen_detection, full_detection, "detection"),
        "issueLocalization": effect(qwen_localization, full_localization, "localization"),
        "pairedCaseCount": len(cases) if paired_records_available else faulty_cases,
        "pairedCaseIdsHash": (
            "sha256:"
            + hashlib.sha256(
                "\n".join(sorted(str(item["caseId"]) for item in cases)).encode("utf-8")
            ).hexdigest()
            if paired_records_available
            else None
        ),
        "scope": "Two-sided exact paired McNemar test on controlled faulty cases.",
    }


_PEERQA_METHOD_LABELS = {
    "qwen_single_prompt_matched_budget": "Qwen single prompt",
    "qwen_structured_rubric_matched_budget": "Qwen structured rubric",
    "reviewx_balanced_qwen37_matched_budget": "FAROS ReviewX",
}


def _peerqa_method_rows(summary: Dict[str, Any]) -> list[Dict[str, Any]]:
    rows = []
    for item in summary.get("methods") or []:
        method_id = str(item.get("method") or "")
        alignment = item.get("automaticAlignment") or {}
        quality = item.get("runQuality") or {}
        rows.append({
            "methodId": method_id,
            "label": _PEERQA_METHOD_LABELS.get(method_id, method_id),
            "candidateRate": alignment.get("candidateRate"),
            "candidateRatePaperClusterBootstrap95": alignment.get(
                "candidateRatePaperClusterBootstrap95"
            ),
            "meanBestMatchScore": alignment.get("meanBestMatchScore"),
            "meanTotalTokens": quality.get("meanTotalTokens"),
            "meanLatencyMs": quality.get("meanLatencyMs"),
            "llmEscalationRate": quality.get("llmEscalationRate"),
            "localOnlyRunCount": quality.get("localOnlyRunCount"),
            "meanGeneratedFindingCount": quality.get("meanFindingCount"),
            "failedRunCount": quality.get("failedRunCount"),
            "budgetExceededCount": quality.get("budgetExceededCount"),
        })
    return rows


def _reviewx_pairwise_rows(summary: Dict[str, Any]) -> list[Dict[str, Any]]:
    efficiency = {
        (str(item.get("methodA") or ""), str(item.get("methodB") or "")): item
        for item in summary.get("efficiencyPairwise") or []
    }
    rows = []
    reviewx_id = "reviewx_balanced_qwen37_matched_budget"
    for item in summary.get("pairwise") or []:
        left = str(item.get("methodA") or "")
        right = str(item.get("methodB") or "")
        if reviewx_id not in {left, right}:
            continue
        sign = 1 if right == reviewx_id else -1
        baseline_id = left if right == reviewx_id else right
        candidate_ci = item.get("candidateRateDeltaPaperClusterBootstrap95") or [None, None]
        score_ci = item.get("scoreDeltaPaperClusterBootstrap95") or [None, None]
        if sign < 0:
            candidate_ci = [
                -candidate_ci[1] if candidate_ci[1] is not None else None,
                -candidate_ci[0] if candidate_ci[0] is not None else None,
            ]
            score_ci = [
                -score_ci[1] if score_ci[1] is not None else None,
                -score_ci[0] if score_ci[0] is not None else None,
            ]
        efficiency_item = efficiency.get((left, right)) or {}
        latency = efficiency_item.get("latencyDeltaMs") or {}
        tokens = efficiency_item.get("tokenDelta") or {}
        latency_ci = latency.get("paperClusterBootstrap95") or [None, None]
        token_ci = tokens.get("paperClusterBootstrap95") or [None, None]
        if sign < 0:
            latency_ci = [
                -latency_ci[1] if latency_ci[1] is not None else None,
                -latency_ci[0] if latency_ci[0] is not None else None,
            ]
            token_ci = [
                -token_ci[1] if token_ci[1] is not None else None,
                -token_ci[0] if token_ci[0] is not None else None,
            ]
        rows.append({
            "baselineMethodId": baseline_id,
            "baselineLabel": _PEERQA_METHOD_LABELS.get(baseline_id, baseline_id),
            "candidateRateDelta": sign * float(item.get("candidateRateDelta") or 0),
            "candidateRateDeltaPaperClusterBootstrap95": candidate_ci,
            "meanBestMatchScoreDelta": sign * float(item.get("meanBestMatchScoreDelta") or 0),
            "scoreDeltaPaperClusterBootstrap95": score_ci,
            "exactMcNemarPValue": (item.get("candidateDiscordance") or {}).get(
                "exactMcNemarPValue"
            ),
            "meanLatencyDeltaMs": sign * float(latency.get("mean") or 0),
            "latencyDeltaPaperClusterBootstrap95": latency_ci,
            "meanTokenDelta": sign * float(tokens.get("mean") or 0),
            "tokenDeltaPaperClusterBootstrap95": token_ci,
        })
    return rows


def _peerqa_evidence_view(
    fair_summary: Dict[str, Any],
    full_audit_summary: Dict[str, Any],
) -> Dict[str, Any]:
    if not fair_summary:
        return {"available": False}
    protocol = fair_summary.get("protocolAudit") or {}
    return {
        "available": True,
        "dataset": "PeerQA",
        "split": fair_summary.get("split"),
        "paperCount": fair_summary.get("paperCount"),
        "questionCount": fair_summary.get("questionCount"),
        "sourceCount": protocol.get("sourceCount"),
        "providerName": protocol.get("providerName"),
        "model": protocol.get("model"),
        "temperature": protocol.get("temperature"),
        "repetitions": protocol.get("repetitions"),
        "protocolHash": protocol.get("sha256"),
        "protocolVerified": bool(protocol.get("passed")),
        "fairTop5": {
            "qualityGate": (fair_summary.get("qualityGate") or {}).get("status"),
            "findingLimit": fair_summary.get("evaluatedFindingLimit"),
            "methods": _peerqa_method_rows(fair_summary),
            "reviewxEffects": _reviewx_pairwise_rows(fair_summary),
        },
        "fullAudit": {
            "available": bool(full_audit_summary),
            "qualityGate": (full_audit_summary.get("qualityGate") or {}).get("status"),
            "fairMethodComparison": False,
            "methods": _peerqa_method_rows(full_audit_summary),
            "reviewxEffects": _reviewx_pairwise_rows(full_audit_summary),
        },
        "reportUrl": "/api/v1/reviews/reviewx/competition/peerqa/report",
        "fullAuditReportUrl": "/api/v1/reviews/reviewx/competition/peerqa/full-audit-report",
        "reportingBoundary": fair_summary.get("reportingBoundary") or {},
        "scope": (
            "Frozen lexical evidence-alignment proxy on previously unused real papers; "
            "blind human labels remain required for expert-recall or correctness claims."
        ),
    }


def _artifact_manifest(
    case_dir: Path,
    filenames: Iterable[str],
    artifact_base_url: str,
) -> list[Dict[str, Any]]:
    authority = {
        "preregistration.json": "human + deterministic",
        "reviewx_round_1.json": "deterministic",
        "candidate_diagnostics.json": "deterministic",
        "plan_delta_contract.json": "deterministic + Qwen",
        "qwen_iteration_plan.json": "Qwen",
        "qwen_trace.json": "provider trace",
        "round_2_plan.json": "deterministic + Qwen",
        "reviewx_round_2.json": "deterministic",
        "experiment_series.json": "deterministic",
        "timeline.json": "runtime",
        "execution_timing.json": "runtime",
        "summary.json": "deterministic",
        "competition_report.md": "derived report",
    }
    result = []
    for filename in filenames:
        path = case_dir / filename
        if not path.is_file():
            continue
        result.append({
            "filename": filename,
            "sizeBytes": path.stat().st_size,
            "sha256": _sha256(path),
            "authority": authority.get(filename, "runtime"),
            "url": f"{artifact_base_url}/{filename}",
        })
    return result


def build_competition_evidence_dashboard(
    *,
    job: Dict[str, Any],
    case_dir: Path,
    reliability_summary_path: Path,
    planning_summary_path: Path,
    multidomain_summary_path: Optional[Path] = None,
    peerqa_summary_path: Optional[Path] = None,
    peerqa_full_audit_summary_path: Optional[Path] = None,
    feedback_record: Optional[Dict[str, Any]] = None,
    public_artifacts: Iterable[str] = (),
) -> Dict[str, Any]:
    """Compose verified artifacts without asking an LLM to summarize its own result."""

    summary = _read_json(case_dir / "summary.json")
    preregistration = _read_json(case_dir / "preregistration.json")
    round_two_plan = _read_json(case_dir / "round_2_plan.json")
    candidates = _read_json(case_dir / "candidate_diagnostics.json")
    qwen_trace = _read_json(case_dir / "qwen_trace.json")
    timeline = _read_json(case_dir / "timeline.json")
    delta_contract = _read_json(
        case_dir / "plan_delta_contract.json",
        required=False,
    )
    delta_audit = (
        verify_plan_delta_contract(delta_contract)
        if delta_contract
        else {
            "status": "missing",
            "checks": {},
            "error": "This run predates the plan-delta contract.",
        }
    )
    reliability = _read_json(reliability_summary_path)
    reliability_records = _read_json(
        reliability_summary_path.with_name("evaluation_records.json"),
        required=False,
    )
    planning = _read_json(planning_summary_path)
    multidomain = (
        _read_json(multidomain_summary_path, required=False)
        if multidomain_summary_path is not None
        else {}
    )
    peerqa = (
        _read_json(peerqa_summary_path, required=False)
        if peerqa_summary_path is not None
        else {}
    )
    peerqa_full_audit = (
        _read_json(peerqa_full_audit_summary_path, required=False)
        if peerqa_full_audit_summary_path is not None
        else {}
    )
    multidomain_effects = _multidomain_effect_rows(multidomain)
    significant_multidomain = [
        item for item in multidomain_effects
        if item["effectStatus"] == "significant_improvement"
    ]
    multidomain_headline = max(
        significant_multidomain or multidomain_effects,
        key=lambda item: (item["ci95"][0], item["delta"]),
        default=None,
    )
    reliability_paired_effects = _reliability_paired_effects(
        reliability,
        reliability_records,
    )

    feedback_first = summary["feedbackResults"]["roundOne"]
    feedback_second = summary["feedbackResults"]["roundTwo"]
    holdout_first = summary["finalHoldout"]["roundOne"]
    holdout_second = summary["finalHoldout"]["roundTwo"]
    holdout_f1_inference = (
        (summary.get("finalHoldout") or {}).get("pairedBootstrap") or {}
    ).get("F1-Score") or {}
    holdout_effect_status = (summary.get("finalHoldout") or {}).get("effectStatus")
    if not holdout_effect_status:
        holdout_effect_status = interval_effect_status(
            float(holdout_f1_inference.get("ci95Low") or 0),
            float(holdout_f1_inference.get("ci95High") or 0),
        )
    holdout_f1_effect = {
        **holdout_f1_inference,
        "effectStatus": holdout_effect_status,
        "claim": {
            "significant_improvement": "A statistically significant final-holdout F1 improvement was detected.",
            "significant_regression": "A statistically significant final-holdout F1 regression was detected.",
            "inconclusive": "No statistically significant final-holdout F1 change was detected.",
        }.get(holdout_effect_status, "Final-holdout F1 effect status is unavailable."),
    }
    metric_names = [
        "Precision",
        "Recall",
        "F1-Score",
        "Brier Score",
        "Expected Calibration Error (ECE)",
        "AUROC",
    ]
    artifact_base_url = (
        f"/api/v1/reviews/reviewx/competition/scifact/jobs/{job['jobId']}/artifacts"
    )
    manifest = _artifact_manifest(
        case_dir,
        sorted(set(public_artifacts)),
        artifact_base_url,
    )
    manifest_names = {item["filename"] for item in manifest}
    required_manifest = {
        "preregistration.json",
        "summary.json",
        "round_2_plan.json",
        "plan_delta_contract.json",
        "qwen_trace.json",
        "timeline.json",
    }
    quality_checks = summary.get("qualityGate", {}).get("checks") or {}
    quality_passed = summary.get("qualityGate", {}).get("status") == "passed"
    timeline_events = timeline.get("events") or []
    timeline_names = [str(item.get("event") or "") for item in timeline_events]
    expected_sequence = [
        "preregistration_frozen",
        "round_one_executed_and_audited",
        "plan_delta_contract_frozen",
        "round_two_plan_frozen",
        "round_two_executed_and_audited",
        "official_dev_loaded_after_plan_freeze",
        "final_holdout_evaluated_once",
        "quality_gate_completed",
    ]
    sequence_complete = all(name in timeline_names for name in expected_sequence)
    if sequence_complete:
        sequence_complete = [timeline_names.index(name) for name in expected_sequence] == sorted(
            timeline_names.index(name) for name in expected_sequence
        )

    signoffs = (feedback_record or {}).get("humanSignoffs") or {}
    human_status = {
        stage: str((signoffs.get(stage) or {}).get("status") or "pending")
        for stage in ("plan", "repair", "conclusion")
    }
    reviewer_separation_required = bool(
        (feedback_record or {}).get("enforceReviewerSeparation")
    )
    reviewer_policy = str(
        (feedback_record or {}).get("reviewerPolicy")
        or (
            "separated_reviewers"
            if reviewer_separation_required
            else "single_accountable_reviewer"
        )
    )
    publication_ready = bool((feedback_record or {}).get("publicationReady"))
    if feedback_record is not None and "publicationReady" not in feedback_record:
        try:
            from app.modules.review.human_signoff import publication_ready as is_ready

            publication_ready = is_ready(feedback_record)
        except (KeyError, TypeError, ValueError):
            publication_ready = False

    evaluation_matrix = [
        {
            "id": "closed_loop",
            "label": "闭环链条完整",
            "status": "passed" if sequence_complete else "failed",
            "evidence": "timeline.json",
        },
        {
            "id": "executable_plan",
            "label": "计划变化可执行",
            "status": "passed" if bool(delta_contract.get("changes")) else "failed",
            "evidence": "plan_delta_contract.json",
        },
        {
            "id": "evidence_grounding",
            "label": "假设与判断有证据",
            "status": "passed" if delta_audit.get("status") == "passed" else "failed",
            "evidence": "preregistration.json + plan_delta_contract.json",
        },
        {
            "id": "feedback_changes_plan",
            "label": "真实结果改变下一轮计划",
            "status": "passed" if delta_audit.get("status") == "passed" else "failed",
            "evidence": "round_1/metrics.json -> plan_delta_contract.json -> round_2_plan.json",
        },
        {
            "id": "iteration_visible",
            "label": "迭代过程清楚可追溯",
            "status": "passed" if sequence_complete and bool(manifest) else "failed",
            "evidence": "timeline.json + evidence manifest",
        },
        {
            "id": "measured_improvement",
            "label": "第二轮结果完成独立复验",
            "status": "passed" if quality_passed else "failed",
            "evidence": "summary.json + paired bootstrap",
        },
    ]

    selected_candidate = str(
        delta_contract.get("selectedCandidateId")
        or round_two_plan.get("selectedCandidateId")
        or ""
    )
    model = str(qwen_trace.get("model") or job.get("model") or "")
    qwen_verified = model.lower().startswith("qwen") and bool(qwen_trace.get("promptSha256"))
    evidence_complete = required_manifest <= manifest_names
    technical_ready = bool(
        quality_passed
        and delta_audit.get("status") == "passed"
        and sequence_complete
        and qwen_verified
        and evidence_complete
        and all(item["status"] == "passed" for item in evaluation_matrix)
    )

    limitations = [
        "The representative case is a public-data computational experiment, not a wet-lab or instrument deployment.",
        "The 90-case reliability benchmark uses controlled injected faults and cannot estimate natural-error prevalence.",
        "Public scientific conclusions remain blocked until real, independent human signoffs are current.",
    ]
    if holdout_effect_status == "inconclusive":
        limitations.insert(
            1,
            "The final-holdout F1 interval crosses zero, so no significant improvement or non-inferiority is claimed.",
        )
    if peerqa:
        limitations.extend([
            "PeerQA lexical alignment is a candidate-generation proxy, not expert review recall or correctness.",
            "The PeerQA full-audit view exposes more findings than the baselines and is not a fair output-count comparison.",
        ])

    reliability_methods = []
    for method_id, values in (reliability.get("scores") or {}).items():
        reliability_methods.append({
            "methodId": method_id,
            "label": {
                "qwen_only": "Qwen only",
                "rules_only": "Rules only",
                "faros_full": "FAROS full",
            }.get(method_id, method_id),
            "faultDetectionRate": values.get("faultDetectionRate"),
            "faultDetectionWilson95": values.get("faultDetectionRateWilson95"),
            "normalFalseRejectRate": values.get("normalFalseRejectRate"),
            "issueLocalizationRate": values.get("issueLocalizationRate"),
            "f1": values.get("f1"),
        })

    planning_methods = []
    for method_id, values in (planning.get("methods") or {}).items():
        agreement = values.get("pooledPolicyAgreement") or {}
        planning_methods.append({
            "methodId": method_id,
            "label": {
                "qwen_one_shot": "Qwen one-shot",
                "frozen_rules": "Frozen rules",
                "qwen_reviewx": "Qwen + ReviewX",
            }.get(method_id, method_id),
            "executabilityRate": (values.get("planExecutabilityRate") or {}).get("mean"),
            "constraintSatisfactionRate": (values.get("constraintSatisfactionRate") or {}).get("mean"),
            "policyAgreementRate": agreement.get("rate"),
            "policyAgreementCount": [agreement.get("successes"), agreement.get("total")],
            "wilson95": agreement.get("wilson95"),
        })

    stages = [
        {
            "id": "question",
            "label": "研究问题与约束",
            "authority": "human",
            "status": "frozen",
            "detail": preregistration.get("scientificQuestion"),
            "evidence": "preregistration.json",
        },
        {
            "id": "round_one",
            "label": "第一轮执行",
            "authority": "observed",
            "status": "executed",
            "detail": f"F1 {float(feedback_first['F1-Score']):.4f}; Recall {float(feedback_first['Recall']):.4f}",
            "evidence": "round_1/metrics.json",
        },
        {
            "id": "reviewx",
            "label": "ReviewX证据审计",
            "authority": "deterministic",
            "status": "passed" if quality_checks.get("roundOneEvidenceAuditPassed") else "failed",
            "detail": "复算指标、校验来源、隔离未见集并审计候选约束。",
            "evidence": "reviewx_round_1.json + candidate_diagnostics.json",
        },
        {
            "id": "qwen",
            "label": "Qwen权衡规划",
            "authority": "qwen",
            "status": "called" if qwen_verified else "unverified",
            "detail": f"{model}; selected {selected_candidate}; final holdout hidden",
            "evidence": "qwen_trace.json + qwen_iteration_plan.json",
        },
        {
            "id": "delta",
            "label": "计划变化合同",
            "authority": "deterministic",
            "status": delta_audit.get("status"),
            "detail": f"{len(delta_contract.get('changes') or [])} fields changed and hash-bound to evidence.",
            "evidence": "plan_delta_contract.json",
        },
        {
            "id": "round_two",
            "label": "第二轮复验",
            "authority": "observed",
            "status": "executed",
            "detail": f"F1 {float(feedback_second['F1-Score']):.4f}; Recall {float(feedback_second['Recall']):.4f}",
            "evidence": "round_2/metrics.json",
        },
        {
            "id": "human",
            "label": "人工结论签核",
            "authority": "human",
            "status": human_status["conclusion"],
            "detail": "Automated evidence cannot approve a public scientific conclusion.",
            "evidence": "ReviewX human signoff record",
        },
    ]

    return {
        "schemaVersion": "faros-competition-evidence-dashboard/v2",
        "generatedAt": datetime.now(UTC).isoformat(),
        "track": {
            "id": "track-1b",
            "name": "科学实验任务规划与反馈迭代",
            "officialFocus": "真实实验结果必须改变下一轮计划，并逐轮复验成效。",
            "reportPageLimit": 30,
        },
        "status": {
            "technicalReady": technical_ready,
            "publicationReady": publication_ready,
            "label": "技术证据就绪，正式人工材料待补" if technical_ready and not publication_ready else (
                "技术与人工发布门均通过" if technical_ready and publication_ready else "技术证据仍有缺口"
            ),
            "qualityGate": summary.get("qualityGate", {}).get("status"),
            "planDeltaAudit": delta_audit,
            "evidenceComplete": evidence_complete,
            "qwenVerified": qwen_verified,
        },
        "case": {
            "jobId": job.get("jobId"),
            "runId": summary.get("runId"),
            "dataset": summary.get("dataset"),
            "benchmarkFingerprint": summary.get("benchmarkFingerprint"),
            "scientificQuestion": preregistration.get("scientificQuestion"),
            "hypothesis": preregistration.get("hypothesis"),
            "selectedCandidateId": selected_candidate,
            "qualityGate": summary.get("qualityGate"),
        },
        "evaluationMatrix": evaluation_matrix,
        "stages": stages,
        "feedbackMetrics": _metric_rows(feedback_first, feedback_second, metric_names),
        "holdoutMetrics": _metric_rows(holdout_first, holdout_second, metric_names),
        "holdoutInference": summary.get("finalHoldout", {}).get("pairedBootstrap"),
        "holdoutEffect": holdout_f1_effect,
        "interventionAudit": summary.get("interventionAudit") or {},
        "planDelta": {
            "available": bool(delta_contract),
            "audit": delta_audit,
            "trigger": delta_contract.get("trigger"),
            "selectedCandidateId": selected_candidate,
            "changes": delta_contract.get("changes") or [],
            "candidates": _candidate_rows(candidates, delta_contract),
            "qwenContribution": delta_contract.get("qwenContribution") or {
                "model": model,
                "role": "Legacy run: Qwen plan is available but no delta contract was frozen.",
                "selectedCandidateId": selected_candidate,
            },
            "contentHash": delta_contract.get("contentHash"),
        },
        "qwen": {
            "provider": qwen_trace.get("provider"),
            "model": model,
            "latencyMs": qwen_trace.get("latencyMs"),
            "usage": qwen_trace.get("usage") or {},
            "promptHash": qwen_trace.get("promptSha256"),
            "policyFollowed": summary.get("qualityGate", {}).get("checks", {}).get("qwenFollowedPreregisteredPolicy"),
            "finalHoldoutExposed": qwen_trace.get("finalHoldoutExposedToQwen"),
        },
        "reliability": {
            "runId": reliability.get("runId"),
            "datasets": reliability.get("datasets") or [],
            "caseAudit": reliability.get("caseAudit") or {},
            "methods": reliability_methods,
            "repairEvaluation": reliability.get("repairEvaluation") or {},
            "qwenMissCount": len(reliability.get("qwenMisses") or []),
            "qualityGate": reliability.get("qualityGate"),
            "pairedEffects": reliability_paired_effects,
            "scope": "Paired controlled faults derived from real datasets; not a prevalence estimate for natural research errors.",
        },
        "planning": {
            "runCount": planning.get("runCount"),
            "seeds": planning.get("seeds") or [],
            "methods": planning_methods,
            "qwenCost": planning.get("qwenCost") or {},
            "responseHashes": planning.get("responseHashes") or [],
            "scope": "Six controlled decision scenarios per seed from one real SciFact candidate arena.",
        },
        "multidomain": {
            "datasets": [item.get("name") for item in multidomain.get("datasets") or []],
            "qualityGate": multidomain.get("qualityGate"),
            "effects": multidomain_effects,
            "headline": multidomain_headline,
            "scope": "Cross-domain stress evidence; it does not establish zero-shot universal transfer.",
        },
        "externalReview": _peerqa_evidence_view(peerqa, peerqa_full_audit),
        "humanGovernance": {
            "feedbackId": (feedback_record or {}).get("id") or job.get("feedbackId"),
            "signoffs": human_status,
            "publicationReady": publication_ready,
            "reviewerSeparationRequired": reviewer_separation_required,
            "reviewerPolicy": reviewer_policy,
            "responsibleReviewerCount": 2 if reviewer_separation_required else 1,
            "note": "One identified reviewer may approve all required stages; each decision remains separately hash-bound.",
        },
        "evidenceManifest": manifest,
        "limitations": limitations,
        "provenanceLegend": [
            {"id": "observed", "label": "真实观测", "description": "由执行记录直接产生并可复算"},
            {"id": "deterministic", "label": "程序判定", "description": "规则、哈希、门禁和统计计算"},
            {"id": "qwen", "label": "Qwen输出", "description": "解释、权衡、步骤与证伪条件"},
            {"id": "human", "label": "人工决策", "description": "方案、风险和结论责任签核"},
        ],
    }
