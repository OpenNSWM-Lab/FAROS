"""
Budget Modes and Degradation States

Implements P0 task #7: "coverage/deep two budget modes output same schema;
no API, search failure, insufficient evidence and topic drift all have
explicit degradation states."

- coverage mode: fewer LLM calls, smaller search budget, fallback problem framing
- deep mode: full LLM calls, larger search budget, LLM problem framing

Both modes output the same ResearchDossier schema.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from app.contracts import RunMode

logger = logging.getLogger(__name__)


class DegradationReason(str, Enum):
    """Reasons for degradation in output quality."""
    NO_API = "no_api"
    SEARCH_FAILURE = "search_failure"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    TOPIC_DRIFT = "topic_drift"
    NONE = "none"


@dataclass
class BudgetConfig:
    """Configuration for a budget mode."""
    mode: RunMode
    max_papers: int = 120
    max_candidates: int = 5
    max_llm_calls: int = 20
    use_llm_problem_framing: bool = True
    use_bfts: bool = True
    use_deep_reading: bool = True
    max_reflection_rounds: int = 2
    parallel_search: bool = True

    @classmethod
    def coverage(cls) -> "BudgetConfig":
        """Coverage mode: fast, fewer resources, for 125-question batch."""
        return cls(
            mode=RunMode.COVERAGE,
            max_papers=40,
            max_candidates=3,
            max_llm_calls=8,
            use_llm_problem_framing=False,
            use_bfts=False,
            use_deep_reading=False,
            max_reflection_rounds=1,
            parallel_search=True,
        )

    @classmethod
    def deep(cls) -> "BudgetConfig":
        """Deep mode: full resources, for representative questions."""
        return cls(
            mode=RunMode.DEEP,
            max_papers=120,
            max_candidates=5,
            max_llm_calls=30,
            use_llm_problem_framing=True,
            use_bfts=True,
            use_deep_reading=True,
            max_reflection_rounds=2,
            parallel_search=True,
        )

    @classmethod
    def from_mode(cls, mode: RunMode) -> "BudgetConfig":
        return cls.coverage() if mode == RunMode.COVERAGE else cls.deep()


@dataclass
class DegradationState:
    """Tracks degradation reasons and their impact on the output."""
    reason: DegradationReason = DegradationReason.NONE
    details: str = ""
    fallback_actions: List[str] = field(default_factory=list)
    confidence_cap: float = 1.0  # Maximum confidence allowed in this state

    @property
    def is_degraded(self) -> bool:
        return self.reason != DegradationReason.NONE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reason": self.reason.value,
            "details": self.details,
            "fallbackActions": self.fallback_actions,
            "confidenceCap": self.confidence_cap,
        }


def detect_degradation(
    *,
    api_available: bool = True,
    search_result_count: int = 0,
    min_evidence_threshold: int = 3,
    topic_drift_detected: bool = False,
) -> DegradationState:
    """
    Detect degradation state from pipeline conditions.

    Returns a DegradationState with the most severe reason and appropriate
    fallback actions.
    """
    if not api_available:
        return DegradationState(
            reason=DegradationReason.NO_API,
            details="LLM API unavailable; using heuristic fallbacks only.",
            fallback_actions=[
                "Skipped LLM problem framing, used rule-based scoping",
                "Skipped BFTS, used legacy brainstorm",
                "Skipped deep reading, used title-only evidence",
            ],
            confidence_cap=0.3,
        )

    if search_result_count == 0:
        return DegradationState(
            reason=DegradationReason.SEARCH_FAILURE,
            details="All search sources returned zero results.",
            fallback_actions=[
                "Expanded seed query with synonyms",
                "Retried with broader domain terms",
                "Generated candidates from domain knowledge only",
            ],
            confidence_cap=0.2,
        )

    if search_result_count < min_evidence_threshold:
        return DegradationState(
            reason=DegradationReason.INSUFFICIENT_EVIDENCE,
            details=f"Only {search_result_count} evidence sources found (minimum {min_evidence_threshold} required).",
            fallback_actions=[
                "Lowered confidence scores proportionally",
                "Added explicit uncertainty about evidence coverage",
                "Marked hypotheses as weakly_supported",
            ],
            confidence_cap=0.5,
        )

    if topic_drift_detected:
        return DegradationState(
            reason=DegradationReason.TOPIC_DRIFT,
            details="Generated candidates drifted from the original seed query topic.",
            fallback_actions=[
                "Applied topic alignment filter",
                "Re-scoped problem frame to original question",
                "Rejected off-topic candidates",
            ],
            confidence_cap=0.4,
        )

    return DegradationState(reason=DegradationReason.NONE)
