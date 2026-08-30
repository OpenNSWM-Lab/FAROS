"""
Degraded fixture tests for the Idea module.

Tests that the module produces valid (but degraded) ResearchDossier outputs
under each of the 4 degradation states:
  - NO_API
  - SEARCH_FAILURE
  - INSUFFICIENT_EVIDENCE
  - TOPIC_DRIFT
"""

import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch

from app.contracts import (
    ResearchDossier,
    RunMode,
    ScientificQuestion,
)
from app.modules.idea.budget_modes import (
    BudgetConfig,
    DegradationReason,
    DegradationState,
    detect_degradation,
)
from app.modules.idea.research_dossier import build_research_dossier


def _make_mock_session(seed: str = "测试低算力平台的模型推理优化"):
    """Create a minimal mock IdeaSession."""
    session = MagicMock()
    session.seed = seed
    session.seedQuery = seed
    session.id = "test-session-degraded"
    session.domain = "AI Systems"
    session.search_queries = [seed]
    session.config = MagicMock()
    session.config.beam_width = 2
    session.config.max_reflection_rounds = 2
    session.config.max_nodes = 20
    session.config.min_candidates = 2
    session.config.providerName = "qwen"
    session.config.model = "qwen-turbo"
    session.config.seedQuery = seed
    session.config.domain = "AI Systems"
    session.config.constraints = None
    session.model_dump = lambda: {"id": session.id, "seed": seed}
    return session


def _make_mock_candidates(count: int = 2):
    """Create minimal mock IdeaCandidate objects."""
    candidates = []
    for i in range(count):
        c = MagicMock()
        c.id = f"cand_{i}"
        c.title = f"Test Hypothesis {i}: Approach {i}"
        c.summary = f"This hypothesis explores approach {i} for edge computing optimization."
        c.hypothesisStatement = f"Approach {i} reduces inference latency on edge devices."
        c.keyInsight = f"Key insight {i}"
        c.problem = f"Problem {i}: high latency on edge"
        c.key_claim = f"Claim {i}"
        c.novelty = 7.5
        c.feasibility = 6.0
        c.evidence_strength = 5.5
        c.impact = 7.0
        c.clarity = 6.5
        c.risk = 4.0
        c.alignment = 8.0
        c.referenceSupport = 5.0
        c.experimentSpecificity = 6.0
        c.scoringConfidence = 0.6
        c.confidence = 0.6
        c.searchNodeId = f"node_{i}"
        c.pathSeedId = f"seed_{i}"
        c.reasoningPathId = f"path_{i}"
        c.references = []
        c.critique = None
        c.expectedOutcome = f"Reduced latency by {20+i}%"
        c.proposedMethod = f"Method {i}: controlled benchmark"
        c.expectedMetrics = ["latency_ms", "throughput_qps"]
        c.draftPlan = None
        exp = MagicMock()
        exp.metrics = ["latency_ms", "throughput_qps"]
        exp.datasets = ["edge_benchmark_dataset"]
        exp.method = "benchmark"
        exp.description = "Run benchmark on edge device"
        c.experiment_specs = [exp]
        c.experimentSpecs = [exp]
        risk_obj = MagicMock()
        risk_obj.risk = "hardware limitation"
        c.risks = [risk_obj]
        c.search_queries = ["edge computing optimization"]
        c.literature_refs = []
        candidates.append(c)
    return candidates


def _make_mock_literature(count: int = 5):
    """Create minimal mock LiteratureItem objects."""
    lit = []
    for i in range(count):
        item = MagicMock()
        item.id = f"lit_{i}"
        item.title = f"Paper {i}: Edge Computing Survey"
        item.authors = [f"Author {i}"]
        item.year = 2024
        item.doi = f"10.1000/test{i}" if i > 0 else None
        item.url = f"https://example.com/paper{i}"
        item.abstract = f"Abstract for paper {i} about edge computing."
        item.snippet = f"Key finding {i}: edge computing optimization reduces latency."
        item.source = "semantic_scholar"
        item.relevanceScore = 0.8 - i * 0.1
        item.arxivId = f"2401.{i:05d}" if i > 0 else None
        lit.append(item)
    return lit


class TestDegradedFixtures:
    """Test degraded states produce valid dossiers."""

    def test_no_api_degradation(self):
        """NO_API: no LLM provider, confidence capped at 0.3."""
        degradation = detect_degradation(
            api_available=False,
            search_result_count=10,
            min_evidence_threshold=3,
        )
        assert degradation.is_degraded
        assert degradation.reason == DegradationReason.NO_API
        assert degradation.confidence_cap <= 0.3

    def test_search_failure_degradation(self):
        """SEARCH_FAILURE: 0 search results."""
        degradation = detect_degradation(
            api_available=True,
            search_result_count=0,
            min_evidence_threshold=3,
        )
        assert degradation.is_degraded
        assert degradation.reason == DegradationReason.SEARCH_FAILURE
        assert degradation.confidence_cap <= 0.2

    def test_insufficient_evidence_degradation(self):
        """INSUFFICIENT_EVIDENCE: fewer than 3 evidence records."""
        degradation = detect_degradation(
            api_available=True,
            search_result_count=2,
            min_evidence_threshold=3,
        )
        assert degradation.is_degraded
        assert degradation.reason == DegradationReason.INSUFFICIENT_EVIDENCE
        assert degradation.confidence_cap <= 0.5

    def test_topic_drift_degradation(self):
        """TOPIC_DRIFT: candidates drifted from seed topic."""
        degradation = detect_degradation(
            api_available=True,
            search_result_count=20,
            min_evidence_threshold=3,
            topic_drift_detected=True,
        )
        assert degradation.is_degraded
        assert degradation.reason == DegradationReason.TOPIC_DRIFT
        assert degradation.confidence_cap <= 0.4

    def test_no_degradation_when_sufficient(self):
        """No degradation when everything is fine."""
        degradation = detect_degradation(
            api_available=True,
            search_result_count=20,
            min_evidence_threshold=3,
        )
        assert not degradation.is_degraded

    def test_degraded_dossier_still_valid(self):
        """Even with insufficient evidence, dossier should be valid."""
        session = _make_mock_session()
        candidates = _make_mock_candidates(2)
        literature = _make_mock_literature(2)  # < 3, triggers INSUFFICIENT_EVIDENCE

        question = ScientificQuestion(
            id="q_degraded",
            text="How to optimize model inference on low-power devices?",
        )

        dossier = build_research_dossier(
            session=session,
            candidates=candidates,
            literature=literature,
            question=question,
            run_id="run_degraded_001",
            mode=RunMode.DEEP,
        )

        # Dossier should still be a valid ResearchDossier
        assert isinstance(dossier, ResearchDossier)
        assert len(dossier.hypotheses) >= 2

        # Evidence may be insufficient but structure is valid
        for hyp in dossier.hypotheses:
            assert 0 <= hyp.confidence <= 1
            assert hyp.falsificationCriteria is not None

    def test_dossier_accepts_session_without_optional_constraints(self):
        """The normal omitted-constraints case must not fail contract validation."""
        session = _make_mock_session()

        dossier = build_research_dossier(
            session=session,
            candidates=_make_mock_candidates(2),
            literature=_make_mock_literature(3),
            mode=RunMode.COVERAGE,
        )

        assert dossier.problemFrame.originalQuestion == session.config.seedQuery
        assert isinstance(dossier, ResearchDossier)

    def test_coverage_mode_budget(self):
        """Coverage mode should have reduced budget settings."""
        budget = BudgetConfig.from_mode(RunMode.COVERAGE)
        assert budget.max_llm_calls <= 10
        assert budget.use_bfts is False
        assert budget.use_deep_reading is False

    def test_deep_mode_budget(self):
        """Deep mode should have full budget settings."""
        budget = BudgetConfig.from_mode(RunMode.DEEP)
        assert budget.max_llm_calls > 10
        assert budget.use_bfts is True
        assert budget.use_deep_reading is True

    def test_degradation_state_to_dict(self):
        """DegradationState should serialize to dict correctly."""
        deg = DegradationState(
            reason=DegradationReason.INSUFFICIENT_EVIDENCE,
            confidence_cap=0.5,
            fallback_actions=["use_local_corpus", "mark_evidence_gaps"],
        )
        d = deg.to_dict()
        assert d["reason"] == "insufficient_evidence"
        assert d["confidenceCap"] == 0.5
        assert "use_local_corpus" in d["fallbackActions"]
