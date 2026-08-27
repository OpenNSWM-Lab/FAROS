import json
from pathlib import Path

from app.modules.review.competition_evidence import build_competition_evidence_dashboard
from app.modules.review.plan_delta import seal_plan_delta_contract


METRICS_ONE = {
    "Precision": 0.69,
    "Recall": 0.71,
    "F1-Score": 0.70,
    "Brier Score": 0.21,
    "Expected Calibration Error (ECE)": 0.09,
    "AUROC": 0.68,
}
METRICS_TWO = {
    "Precision": 0.67,
    "Recall": 0.88,
    "F1-Score": 0.76,
    "Brier Score": 0.2104,
    "Expected Calibration Error (ECE)": 0.08,
    "AUROC": 0.681,
}


def _write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")


def _contract():
    return seal_plan_delta_contract({
        "contractId": "delta-case",
        "researchSeriesId": "series",
        "fromRunId": "run-1",
        "toRunId": "run-2",
        "createdAt": "2026-08-27T00:00:00+00:00",
        "benchmarkFingerprint": f"sha256:{'a' * 64}",
        "evidenceGateStatus": "pass",
        "scientificDecision": "revise_plan",
        "trigger": {
            "status": "optimization_opportunity",
            "statement": "A feasible candidate meets the improvement target.",
            "metric": "F1-Score",
            "observedValue": 0.70,
            "targetValue": 0.71,
            "comparator": ">=",
            "evidenceIds": ["round-one", "arena"],
        },
        "evidence": [
            {
                "id": "round-one",
                "artifact": "round_1/metrics.json",
                "jsonPath": "$.method",
                "authority": "observed",
                "summary": "First-round metrics.",
            },
            {
                "id": "arena",
                "artifact": "candidate_diagnostics.json",
                "jsonPath": "$",
                "authority": "deterministic",
                "summary": "Candidate audit.",
            },
            {
                "id": "qwen-plan",
                "artifact": "qwen_iteration_plan.json",
                "jsonPath": "$",
                "authority": "qwen",
                "summary": "Qwen explanation.",
            },
        ],
        "candidates": [
            {
                "candidateId": "retain",
                "feasible": True,
                "metrics": METRICS_ONE,
                "change": "Retain the first-round plan.",
            },
            {
                "candidateId": "selected",
                "feasible": True,
                "metrics": METRICS_TWO,
                "change": "Lower the threshold.",
            },
        ],
        "selectedCandidateId": "selected",
        "changes": [{
            "fieldPath": "decisionThreshold",
            "before": 0.5,
            "after": 0.375,
            "rationale": "Recover false negatives.",
            "expectedEffect": "Increase recall.",
            "evidenceIds": ["round-one", "arena", "qwen-plan"],
        }],
        "qwenContribution": {
            "model": "qwen3.7-plus-2026-05-26",
            "role": "Explain the frozen selection.",
            "selectedCandidateId": "selected",
            "rationale": "The selected candidate is feasible.",
            "expectedTradeoff": "Higher recall for lower precision.",
            "falsificationCriteria": ["Holdout F1 regresses."],
            "promptHash": f"sha256:{'b' * 64}",
            "finalHoldoutExposed": False,
        },
        "stopConditions": ["No feasible candidate remains."],
        "finalHoldoutPolicy": "Load once after plan freeze.",
    }).model_dump(mode="json")


def _fixture(tmp_path: Path):
    case = tmp_path / "case"
    contract = _contract()
    summary = {
        "runId": "series",
        "dataset": {"name": "SciFact", "fitPairs": 738, "feedbackPairs": 181, "finalHoldoutPairs": 339},
        "benchmarkFingerprint": f"sha256:{'a' * 64}",
        "feedbackResults": {"roundOne": METRICS_ONE, "roundTwo": METRICS_TWO},
        "finalHoldout": {
            "roundOne": METRICS_ONE,
            "roundTwo": METRICS_TWO,
            "pairedBootstrap": {"F1-Score": {"ci95Low": -0.02, "ci95High": 0.03}},
        },
        "qualityGate": {
            "status": "passed",
            "checks": {
                "roundOneEvidenceAuditPassed": True,
                "qwenFollowedPreregisteredPolicy": True,
            },
        },
    }
    prereg = {
        "scientificQuestion": "Can feedback improve a controlled scientific classifier?",
        "hypothesis": "One constrained revision improves frozen-feedback F1.",
    }
    candidates = {
        "retain": {"metrics": METRICS_ONE, "checks": {"precision": True}, "feasible": True},
        "selected": {"metrics": METRICS_TWO, "checks": {"precision": True}, "feasible": True},
    }
    qwen = {
        "provider": "qwen",
        "model": "qwen3.7-plus-2026-05-26",
        "latencyMs": 12000,
        "usage": {"total_tokens": 1000},
        "promptSha256": f"sha256:{'b' * 64}",
        "finalHoldoutExposedToQwen": False,
    }
    timeline = {"events": [
        {"event": "preregistration_frozen"},
        {"event": "round_one_executed_and_audited"},
        {"event": "plan_delta_contract_frozen"},
        {"event": "round_two_plan_frozen"},
        {"event": "round_two_executed_and_audited"},
        {"event": "official_dev_loaded_after_plan_freeze"},
        {"event": "final_holdout_evaluated_once"},
        {"event": "quality_gate_completed"},
    ]}
    artifacts = {
        "summary.json": summary,
        "preregistration.json": prereg,
        "round_2_plan.json": {"selectedCandidateId": "selected"},
        "candidate_diagnostics.json": candidates,
        "qwen_trace.json": qwen,
        "timeline.json": timeline,
        "plan_delta_contract.json": contract,
    }
    for name, payload in artifacts.items():
        _write(case / name, payload)

    reliability_path = tmp_path / "reliability.json"
    _write(reliability_path, {
        "runId": "reliability",
        "datasets": ["SciFact", "Climate-FEVER", "PubHealth"],
        "caseAudit": {"total": 90, "faulty": 45, "clean": 45},
        "scores": {
            "qwen_only": {"faultDetectionRate": 0.87, "normalFalseRejectRate": 0, "issueLocalizationRate": 0.76, "f1": 0.93},
            "rules_only": {"faultDetectionRate": 0.8, "normalFalseRejectRate": 0, "issueLocalizationRate": 0.8, "f1": 0.89},
            "faros_full": {"faultDetectionRate": 1, "normalFalseRejectRate": 0, "issueLocalizationRate": 1, "f1": 1},
        },
        "repairEvaluation": {"attempted": 45, "passed": 45},
        "qwenMisses": [{"caseId": "miss"}],
        "qualityGate": {"status": "passed"},
    })
    planning_path = tmp_path / "planning.json"
    _write(planning_path, {
        "runCount": 3,
        "seeds": [11, 22, 33],
        "methods": {
            "qwen_one_shot": {
                "planExecutabilityRate": {"mean": 1},
                "constraintSatisfactionRate": {"mean": 1},
                "pooledPolicyAgreement": {"rate": 0.83, "successes": 15, "total": 18, "wilson95": [0.61, 0.94]},
            },
            "qwen_reviewx": {
                "planExecutabilityRate": {"mean": 1},
                "constraintSatisfactionRate": {"mean": 1},
                "pooledPolicyAgreement": {"rate": 1, "successes": 18, "total": 18, "wilson95": [0.82, 1]},
            },
        },
        "qwenCost": {"totalTokens": 9034},
    })
    return case, reliability_path, planning_path, list(artifacts)


def test_competition_dashboard_exposes_one_verified_evidence_story(tmp_path: Path):
    case, reliability, planning, artifacts = _fixture(tmp_path)

    payload = build_competition_evidence_dashboard(
        job={"jobId": "case-1", "model": "qwen3.7-plus-2026-05-26", "feedbackId": "feedback-1"},
        case_dir=case,
        reliability_summary_path=reliability,
        planning_summary_path=planning,
        feedback_record={
            "id": "feedback-1",
            "humanSignoffs": {"plan": {}, "repair": {}, "conclusion": {}},
            "enforceReviewerSeparation": True,
        },
        public_artifacts=artifacts,
    )

    assert payload["status"]["technicalReady"] is True
    assert payload["status"]["publicationReady"] is False
    assert all(item["status"] == "passed" for item in payload["evaluationMatrix"])
    assert payload["planDelta"]["changes"][0]["fieldPath"] == "decisionThreshold"
    assert payload["feedbackMetrics"][2]["delta"] > 0
    assert payload["qwen"]["finalHoldoutExposed"] is False
    assert len(payload["evidenceManifest"]) == len(artifacts)


def test_competition_dashboard_marks_tampered_contract_not_ready(tmp_path: Path):
    case, reliability, planning, artifacts = _fixture(tmp_path)
    contract = json.loads((case / "plan_delta_contract.json").read_text())
    contract["changes"][0]["after"] = 0.25
    _write(case / "plan_delta_contract.json", contract)

    payload = build_competition_evidence_dashboard(
        job={"jobId": "case-1", "model": "qwen3.7-plus-2026-05-26"},
        case_dir=case,
        reliability_summary_path=reliability,
        planning_summary_path=planning,
        public_artifacts=artifacts,
    )

    assert payload["status"]["technicalReady"] is False
    assert payload["status"]["planDeltaAudit"]["status"] == "failed"
    assert next(item for item in payload["evaluationMatrix"] if item["id"] == "feedback_changes_plan")["status"] == "failed"
