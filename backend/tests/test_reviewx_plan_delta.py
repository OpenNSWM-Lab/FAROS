import pytest

from app.modules.review.plan_delta import (
    PlanDeltaContract,
    seal_plan_delta_contract,
    verify_plan_delta_contract,
)


def _draft():
    return {
        "contractId": "delta-series-1",
        "researchSeriesId": "series-1",
        "fromRunId": "run-1",
        "toRunId": "run-2",
        "createdAt": "2026-08-27T00:00:00+00:00",
        "benchmarkFingerprint": f"sha256:{'a' * 64}",
        "evidenceGateStatus": "pass",
        "scientificDecision": "revise_plan",
        "trigger": {
            "status": "optimization_opportunity",
            "statement": "A feasible revision reaches the preregistered improvement target.",
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
                "jsonPath": "$.method.F1-Score",
                "authority": "observed",
                "summary": "Metric recomputed from frozen records.",
                "value": 0.70,
            },
            {
                "id": "arena",
                "artifact": "candidate_diagnostics.json",
                "jsonPath": "$",
                "authority": "deterministic",
                "summary": "Candidate feasibility audit.",
            },
            {
                "id": "qwen-plan",
                "artifact": "qwen_iteration_plan.json",
                "jsonPath": "$",
                "authority": "qwen",
                "summary": "Qwen tradeoff explanation.",
            },
        ],
        "candidates": [
            {
                "candidateId": "retain",
                "feasible": True,
                "metrics": {"F1-Score": 0.70},
                "change": "Keep the first-round plan.",
            },
            {
                "candidateId": "selected",
                "feasible": True,
                "metrics": {"F1-Score": 0.76},
                "change": "Lower the threshold.",
            },
            {
                "candidateId": "unsafe",
                "feasible": False,
                "metrics": {"F1-Score": 0.80},
                "failedConstraints": ["precisionAtLeast066"],
                "change": "Remove the precision guardrail.",
            },
        ],
        "selectedCandidateId": "selected",
        "changes": [
            {
                "fieldPath": "decisionThreshold",
                "before": 0.5,
                "after": 0.375,
                "rationale": "Recover false negatives under the precision floor.",
                "expectedEffect": "Increase recall and F1.",
                "evidenceIds": ["round-one", "arena", "qwen-plan"],
            },
        ],
        "qwenContribution": {
            "model": "qwen3.7-plus-2026-05-26",
            "role": "Explain the frozen deterministic selection.",
            "selectedCandidateId": "selected",
            "rationale": "The candidate is feasible and near-optimal.",
            "expectedTradeoff": "Recall rises while precision may decline.",
            "falsificationCriteria": ["Final holdout F1 regresses."],
            "promptHash": f"sha256:{'b' * 64}",
            "finalHoldoutExposed": False,
        },
        "stopConditions": ["No candidate satisfies all guardrails."],
        "finalHoldoutPolicy": "Load the final holdout once after the plan is frozen.",
    }


def test_plan_delta_contract_is_content_addressed_and_auditable():
    contract = seal_plan_delta_contract(_draft())

    assert contract.contentHash.startswith("sha256:")
    audit = verify_plan_delta_contract(contract.model_dump(mode="json"))
    assert audit["status"] == "passed"
    assert all(audit["checks"].values())


def test_plan_delta_contract_detects_tampering():
    payload = seal_plan_delta_contract(_draft()).model_dump(mode="json")
    payload["changes"][0]["after"] = 0.25

    audit = verify_plan_delta_contract(payload)

    assert audit["status"] == "failed"
    assert audit["checks"]["schemaValid"] is True
    assert audit["checks"]["contentHashValid"] is False


def test_plan_delta_contract_rejects_unresolved_evidence_reference():
    draft = _draft()
    draft["changes"][0]["evidenceIds"].append("missing")

    with pytest.raises(ValueError, match="unknown evidence IDs"):
        seal_plan_delta_contract(draft)


def test_plan_delta_contract_rejects_infeasible_selection():
    draft = _draft()
    draft["selectedCandidateId"] = "unsafe"
    draft["qwenContribution"]["selectedCandidateId"] = "unsafe"

    with pytest.raises(ValueError, match="feasible candidate"):
        seal_plan_delta_contract(draft)


def test_plan_delta_contract_rejects_noop_change():
    draft = _draft()
    draft["changes"][0]["after"] = 0.5

    with pytest.raises(ValueError, match="does not change"):
        PlanDeltaContract.model_validate({**draft, "contentHash": f"sha256:{'c' * 64}"})
