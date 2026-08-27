import asyncio
import json
from pathlib import Path

from app.modules.review import reviews_api

from experiments.reviewx_reliability.run import (
    FAULT_CODES,
    FAULT_TYPES,
    BaseArtifact,
    build_cases,
    clean_package,
    faros_audit,
    inject_fault,
    repair_package,
    rules_only_audit,
    score_predictions,
)


def _artifact() -> BaseArtifact:
    records = [
        {"sampleId": "a", "claimId": "1", "label": 0, "probability": 0.2, "prediction": 0},
        {"sampleId": "b", "claimId": "2", "label": 0, "probability": 0.4, "prediction": 0},
        {"sampleId": "c", "claimId": "3", "label": 1, "probability": 0.6, "prediction": 1},
        {"sampleId": "d", "claimId": "4", "label": 1, "probability": 0.8, "prediction": 1},
    ]
    return BaseArtifact(
        dataset="Fixture",
        source_run=str(Path("fixture.json")),
        records=records,
        threshold=0.5,
        split_audit={
            "selectionSplit": "validation",
            "evaluationSplit": "test",
            "testLabelsUsedForSelection": False,
            "groupIntersections": {"trainTest": 0},
        },
        fit_group_ids=["fit-1", "fit-2"],
        evaluation_group_ids=["1", "2", "3", "4"],
    )


def test_clean_package_passes_full_audit():
    package = clean_package(_artifact(), case_id="clean", replica=0)

    assert rules_only_audit(package) == []
    assert faros_audit(package)["decision"] == "accept"


def test_every_fault_is_detected_and_repaired_by_full_faros():
    for fault_type in FAULT_TYPES:
        clean = clean_package(_artifact(), case_id=fault_type, replica=1)
        faulty = inject_fault(clean, fault_type, replica=1)
        audit = faros_audit(faulty)

        assert audit["decision"] == "reject"
        assert FAULT_CODES[fault_type] in audit["issues"]
        repaired = repair_package(faulty, audit["issues"], clean_reference=clean)
        assert faros_audit(repaired)["decision"] == "accept"
        if fault_type in {"holdout_tuning", "claim_group_leakage"}:
            assert repaired["repairTrace"]["mode"] == "paired_clean_reexecution"


def test_static_rules_cannot_recompute_fabricated_metrics():
    clean = clean_package(_artifact(), case_id="metric", replica=0)
    faulty = inject_fault(clean, "reported_metric_fabrication", replica=0)

    assert rules_only_audit(faulty) == []
    assert "REPORTED_METRIC_MISMATCH" in faros_audit(faulty)["issues"]


def test_case_builder_creates_balanced_paired_controls():
    cases = build_cases([_artifact()], replicas=2)

    assert len(cases) == len(FAULT_TYPES) * 2 * 2
    assert sum(case["isFaulty"] for case in cases) == len(cases) / 2
    assert all(sum(item["pairId"] == case["pairId"] for item in cases) == 2 for case in cases)
    assert all(case["faultType"] not in case["caseId"] for case in cases)
    assert all("clean" not in case["caseId"] and "fault" not in case["caseId"] for case in cases)


def test_scoring_reports_detection_and_false_rejection():
    cases = build_cases([_artifact()], replicas=1)
    predictions = {
        case["caseId"]: {
            "decision": "reject" if case["isFaulty"] else "accept",
            "issues": [case["expectedIssueCode"]] if case["isFaulty"] else [],
        }
        for case in cases
    }

    score = score_predictions(cases, predictions)

    assert score["faultDetectionRate"] == 1.0
    assert score["normalFalseRejectRate"] == 0.0
    assert score["issueLocalizationRate"] == 1.0


def test_public_reliability_endpoint_returns_only_aggregate_result(monkeypatch, tmp_path: Path):
    payload = {
        "runId": "reliability-test",
        "datasets": ["SciFact", "Climate-FEVER", "PubHealth"],
        "caseAudit": {"total": 90, "faulty": 45, "clean": 45},
        "scores": {"faros_full": {"faultDetectionRate": 1.0}},
        "repairEvaluation": {"attempted": 45, "passed": 45, "successRate": 1.0},
        "qwenTrace": {"model": "qwen-test", "totalUsage": {"total_tokens": 100}},
        "qwenMisses": [{"caseId": "opaque", "dataset": "SciFact", "faultType": "leakage", "rationale": "missed", "issues": []}],
        "qualityGate": {"status": "passed"},
    }
    (tmp_path / "summary.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(reviews_api, "_RELIABILITY_RESULT_ROOT", tmp_path)

    result = asyncio.run(reviews_api.get_latest_reliability_benchmark_endpoint())

    assert result.totalCases == 90
    assert result.qualityGate == "passed"
    assert result.qwenUsage == {"total_tokens": 100}
    assert not hasattr(result, "qwenTrace")
