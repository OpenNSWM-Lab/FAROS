"""
PR-B13: risk score no longer floored at 50.

With 10+ risks the score should drop to 0, not stay at 50.
With 0 risks the score should be 100.
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.code.eval.scoring import EvalScorer
from app.code.eval.static_eval import StaticEvalResult


def _make_result(risks_count):
    return StaticEvalResult(
        passed=True,
        syntax_valid=True,
        error_count=0,
        warning_count=0,
        info_count=0,
        diagnostics=[],
        risks=[{"type": "test_risk"} for _ in range(risks_count)],
    )


class TestScoringRiskFloor:

    def setup_method(self):
        self.scorer = EvalScorer()

    def test_no_risks_gives_full_score(self):
        result = _make_result(0)
        scores = self.scorer._score_static(result)
        assert scores["risks"] == 100.0

    def test_three_risks_gives_70(self):
        result = _make_result(3)
        scores = self.scorer._score_static(result)
        assert scores["risks"] == 70.0

    def test_five_risks_gives_50(self):
        result = _make_result(5)
        scores = self.scorer._score_static(result)
        assert scores["risks"] == 50.0

    def test_ten_risks_gives_zero(self):
        result = _make_result(10)
        scores = self.scorer._score_static(result)
        assert scores["risks"] == 0.0

    def test_many_risks_clamped_at_zero(self):
        result = _make_result(50)
        scores = self.scorer._score_static(result)
        assert scores["risks"] == 0.0

    def test_one_risk_distinguishable_from_many(self):
        """The old floor made 1 risk and 50 risks look the same (both 50)."""
        score_one = self.scorer._score_static(_make_result(1))["risks"]
        score_many = self.scorer._score_static(_make_result(50))["risks"]
        assert score_one != score_many
        assert score_one > score_many
