"""
Evaluation Scoring - Combines static and dynamic results into scores.
"""

from typing import Dict, Any, Optional
from dataclasses import dataclass

from .static_eval import StaticEvalResult
from .dynamic_eval import DynamicEvalResult, ExecutionStatus


@dataclass
class EvalScore:
    """Combined evaluation score."""
    overall: float  # 0-100
    static_score: float  # 0-100
    dynamic_score: Optional[float]  # 0-100 or None if not run
    dimensions: Dict[str, float]  # Individual dimension scores
    grade: str  # A, B, C, D, F
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall": self.overall,
            "static_score": self.static_score,
            "dynamic_score": self.dynamic_score,
            "dimensions": self.dimensions,
            "grade": self.grade,
        }


class EvalScorer:
    """
    Scores evaluation results.
    
    Combines static and dynamic results into a unified score.
    """
    
    # Weights for different aspects
    WEIGHTS = {
        "syntax": 0.30,
        "risks": 0.20,
        "lint": 0.10,
        "tests": 0.40,
    }
    
    def _score_static(self, result: StaticEvalResult) -> Dict[str, float]:
        """Score static evaluation results."""
        scores = {}
        
        # Syntax score: 100 if valid, 0 if not
        scores["syntax"] = 100.0 if result.syntax_valid else 0.0
        
        # Risk score: Deduct for each risk found
        risk_penalty = min(len(result.risks) * 10, 100)
        scores["risks"] = max(100.0 - risk_penalty, 0.0)
        
        # Lint score: Deduct for warnings
        warning_penalty = min(result.warning_count * 5, 30)
        info_penalty = min(result.info_count * 1, 10)
        scores["lint"] = max(100.0 - warning_penalty - info_penalty, 60.0)
        
        return scores
    
    def _score_dynamic(self, result: DynamicEvalResult) -> Dict[str, float]:
        """Score dynamic evaluation results."""
        scores = {}
        
        if result.status == ExecutionStatus.SKIPPED:
            scores["tests"] = None  # Not applicable
            return scores
        
        if result.status == ExecutionStatus.SUCCESS:
            if result.tests_found:
                total_tests = result.tests_passed + result.tests_failed + result.tests_skipped
                if total_tests > 0:
                    pass_rate = result.tests_passed / total_tests
                    scores["tests"] = pass_rate * 100
                else:
                    scores["tests"] = 100.0  # No tests but command succeeded
            else:
                scores["tests"] = 80.0  # Command succeeded but no tests
        elif result.status == ExecutionStatus.TIMEOUT:
            scores["tests"] = 20.0
        elif result.status == ExecutionStatus.FAILED:
            if result.tests_found:
                total_tests = result.tests_passed + result.tests_failed + result.tests_skipped
                if total_tests > 0:
                    pass_rate = result.tests_passed / total_tests
                    scores["tests"] = pass_rate * 100
                else:
                    scores["tests"] = 30.0
            else:
                scores["tests"] = 30.0
        else:
            scores["tests"] = 0.0
        
        return scores
    
    def _calculate_grade(self, score: float) -> str:
        """Convert score to letter grade."""
        if score >= 90:
            return "A"
        elif score >= 80:
            return "B"
        elif score >= 70:
            return "C"
        elif score >= 60:
            return "D"
        else:
            return "F"
    
    def score(
        self,
        static_result: StaticEvalResult,
        dynamic_result: Optional[DynamicEvalResult] = None,
    ) -> EvalScore:
        """
        Calculate combined evaluation score.
        
        Args:
            static_result: Static evaluation result
            dynamic_result: Optional dynamic evaluation result
            
        Returns:
            EvalScore with overall and dimension scores
        """
        dimensions = {}
        
        # Score static
        static_scores = self._score_static(static_result)
        dimensions.update(static_scores)
        
        # Score dynamic if available
        dynamic_score = None
        if dynamic_result:
            dynamic_scores = self._score_dynamic(dynamic_result)
            dimensions.update(dynamic_scores)
            if dynamic_scores.get("tests") is not None:
                dynamic_score = dynamic_scores["tests"]
        
        # Calculate weighted overall score
        total_weight = 0.0
        weighted_sum = 0.0
        
        for dim, weight in self.WEIGHTS.items():
            if dim in dimensions and dimensions[dim] is not None:
                weighted_sum += dimensions[dim] * weight
                total_weight += weight
        
        if total_weight > 0:
            overall = weighted_sum / total_weight
        else:
            overall = 0.0
        
        # Calculate static-only score
        static_weight = self.WEIGHTS["syntax"] + self.WEIGHTS["risks"] + self.WEIGHTS["lint"]
        static_sum = (
            dimensions.get("syntax", 0) * self.WEIGHTS["syntax"] +
            dimensions.get("risks", 0) * self.WEIGHTS["risks"] +
            dimensions.get("lint", 0) * self.WEIGHTS["lint"]
        )
        static_score = static_sum / static_weight if static_weight > 0 else 0.0
        
        grade = self._calculate_grade(overall)
        
        return EvalScore(
            overall=round(overall, 1),
            static_score=round(static_score, 1),
            dynamic_score=round(dynamic_score, 1) if dynamic_score is not None else None,
            dimensions={k: round(v, 1) if v is not None else None for k, v in dimensions.items()},
            grade=grade,
        )
