import pytest

from app.modules.review.effect_statistics import (
    exact_mcnemar_p_value,
    interval_effect_status,
    paired_transition_audit,
)


def test_exact_mcnemar_matches_six_one_direction_disagreements():
    assert exact_mcnemar_p_value(6, 0) == pytest.approx(0.03125)
    assert exact_mcnemar_p_value(0, 0) == 1.0


def test_interval_status_does_not_call_cross_zero_effect_non_degrading():
    assert interval_effect_status(0.01, 0.04) == "significant_improvement"
    assert interval_effect_status(-0.04, -0.01) == "significant_regression"
    assert interval_effect_status(-0.02, 0.03) == "inconclusive"


def test_transition_audit_separates_corrections_and_regressions():
    audit = paired_transition_audit(
        labels=[1, 1, 0, 0, 1],
        before_predictions=[0, 1, 1, 0, 0],
        after_predictions=[1, 0, 0, 0, 0],
    )

    assert audit["wrongToRight"] == 2
    assert audit["rightToWrong"] == 1
    assert audit["wrongBoth"] == 1
    assert audit["correctBoth"] == 1
    assert audit["changedDecisions"] == 3
    assert audit["netCorrect"] == 1
    assert audit["perClass"]["0"]["netCorrect"] == 1
    assert audit["perClass"]["1"]["netCorrect"] == 0


def test_transition_audit_rejects_non_binary_data():
    with pytest.raises(ValueError, match="binary"):
        paired_transition_audit([2], [0], [1])
