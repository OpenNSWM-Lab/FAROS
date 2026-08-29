from pathlib import Path
import sys

import numpy as np


sys.path.insert(0, str(Path(__file__).parent.parent))

from experiments.reviewx_scifact.closed_loop import (
    Candidate,
    metrics_at_threshold,
    preregistration_payload,
    select_candidate,
    split_fit_feedback,
)
from experiments.reviewx_scifact.run import Example


def _example(claim_id: int, document_id: int) -> Example:
    return Example(
        sample_id=f"sample-{claim_id}-{document_id}",
        split="train",
        claim_id=claim_id,
        document_id=document_id,
        claim="Claim",
        document_title="Paper",
        document_text="Evidence",
        relation="SUPPORT",
        label=0,
    )


def test_split_fit_feedback_keeps_claim_groups_isolated():
    examples = [_example(claim_id, document_id) for claim_id in range(50) for document_id in range(2)]

    fit, feedback = split_fit_feedback(examples)

    assert fit and feedback
    assert not ({item.claim_id for item in fit} & {item.claim_id for item in feedback})
    assert len(fit) + len(feedback) == len(examples)


def test_metrics_at_threshold_changes_only_decision_metrics():
    labels = np.asarray([1, 1, 0, 0], dtype=float)
    probabilities = np.asarray([0.8, 0.4, 0.3, 0.1], dtype=float)

    strict = metrics_at_threshold(labels, probabilities, 0.5)
    relaxed = metrics_at_threshold(labels, probabilities, 0.375)

    assert relaxed["F1-Score"] > strict["F1-Score"]
    assert relaxed["Brier Score"] == strict["Brier Score"]
    assert relaxed["AUROC"] == strict["AUROC"]


def test_selection_policy_uses_guardrails_then_near_best_ece():
    round_one = {
        "Precision": 0.70,
        "Recall": 0.70,
        "F1-Score": 0.70,
        "Brier Score": 0.20,
        "Expected Calibration Error (ECE)": 0.10,
        "AUROC": 0.80,
    }
    diagnostics = {
        "best_f1": {**round_one, "Precision": 0.67, "F1-Score": 0.76, "Expected Calibration Error (ECE)": 0.09},
        "near_best_lower_ece": {**round_one, "Precision": 0.66, "F1-Score": 0.756, "Expected Calibration Error (ECE)": 0.07},
        "infeasible": {**round_one, "Precision": 0.65, "F1-Score": 0.90, "Expected Calibration Error (ECE)": 0.01},
    }
    import experiments.reviewx_scifact.closed_loop as module
    original = module.CANDIDATES
    module.CANDIDATES = (
        Candidate("best_f1", tuple(range(12)), 0.5, "best"),
        Candidate("near_best_lower_ece", tuple(range(11)), 0.5, "near"),
        Candidate("infeasible", tuple(range(10)), 0.5, "bad"),
    )
    try:
        selected, audited = select_candidate(diagnostics, round_one)
    finally:
        module.CANDIDATES = original

    assert selected == "near_best_lower_ece"
    assert audited["infeasible"]["feasible"] is False


def test_preregistration_is_result_free_and_content_addressed():
    payload = preregistration_payload("run_pre", "2026-08-25T00:00:00+00:00")

    assert payload["runId"] == "run_pre"
    assert payload["contentHash"].startswith("sha256:")
    assert payload["datasetProtocol"]["finalHoldoutPolicy"].startswith("Do not load")
    serialized = str(payload).lower()
    assert "f1-score" in serialized
    assert "feedbackresults" not in serialized
    assert "finalholdoutpairs" not in serialized
