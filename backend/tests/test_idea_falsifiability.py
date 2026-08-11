"""
Test: Falsifiability — every hypothesis must have falsification criteria,
confounders, and alternative explanations.

Tests that the Hypothesis contract model enforces falsification and that
the candidate-to-hypothesis converter populates these fields correctly.
"""

import sys
from pathlib import Path
from datetime import datetime

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.contracts import Hypothesis, ResearchDossier
from app.modules.idea.research_dossier import _candidate_to_hypothesis, _score_to_01
from app.models.idea import IdeaCandidate, RiskItem, ExperimentSpec


def _make_candidate(**overrides) -> IdeaCandidate:
    defaults = dict(
        id="cand_test_001",
        sessionId="sess_test",
        title="Test Hypothesis",
        problem="Test problem statement",
        hypothesisStatement="Increasing temperature increases reaction rate.",
        keyInsight="Temperature catalyzes the reaction",
        proposedMethod="Controlled experiment with varying temperatures",
        expectedOutcome="Reaction rate increases linearly with temperature",
        novelty=7.0,
        feasibility=8.0,
        impact=6.0,
        clarity=7.0,
        risk=6.0,
        alignment=8.0,
        referenceSupport=7.0,
        experimentSpecificity=6.0,
        scoringConfidence=0.8,
        scoringMethod="llm",
        risks=[RiskItem(risk="Confounding variable: pressure", mitigation="Control pressure")],
        experimentSpecs=[
            ExperimentSpec(
                name="temp_experiment",
                description="Vary temperature from 20C to 80C",
                metrics=["reaction_rate", "yield"],
                datasets=["synthetic_data"],
            )
        ],
        expectedMetrics=["reaction_rate", "yield"],
        references=["ev_001", "ev_002"],
    )
    defaults.update(overrides)
    return IdeaCandidate(**defaults)


class TestFalsifiability:
    def test_hypothesis_requires_falsification_criteria(self):
        """Hypothesis must have at least one falsification criterion."""
        with pytest.raises(ValidationError, match="falsificationCriteria"):
            Hypothesis(
                id="hyp_001",
                statement="Test statement here",
                falsificationCriteria=[],
            )

    def test_candidate_to_hypothesis_has_falsification(self):
        """Converted hypothesis should have non-empty falsification criteria."""
        candidate = _make_candidate()
        hyp = _candidate_to_hypothesis(candidate, evidence_ids={"ev_001", "ev_002"})
        assert len(hyp.falsificationCriteria) >= 1
        assert all(len(fc) > 0 for fc in hyp.falsificationCriteria)

    def test_candidate_to_hypothesis_has_confounders(self):
        """Converted hypothesis should have confounders from risks."""
        candidate = _make_candidate()
        hyp = _candidate_to_hypothesis(candidate, evidence_ids=set())
        assert len(hyp.confounders) >= 1
        assert "pressure" in hyp.confounders[0]

    def test_candidate_to_hypothesis_has_alternative_explanations(self):
        """Converted hypothesis should have alternative explanations."""
        candidate = _make_candidate()
        hyp = _candidate_to_hypothesis(candidate, evidence_ids=set())
        assert len(hyp.alternativeExplanations) >= 1

    def test_scores_converted_to_01_scale(self):
        """Internal 0-10 scores should be converted to 0-1 scale."""
        candidate = _make_candidate(novelty=7.0, feasibility=8.0)
        hyp = _candidate_to_hypothesis(candidate, evidence_ids=set())
        assert 0.0 <= hyp.scores["novelty"] <= 1.0
        assert abs(hyp.scores["novelty"] - 0.7) < 0.01
        assert abs(hyp.scores["feasibility"] - 0.8) < 0.01

    def test_confidence_in_range(self):
        """Confidence must be in [0, 1]."""
        candidate = _make_candidate(scoringConfidence=0.8)
        hyp = _candidate_to_hypothesis(candidate, evidence_ids=set())
        assert 0.0 <= hyp.confidence <= 1.0
        assert abs(hyp.confidence - 0.8) < 0.01

    def test_supporting_evidence_ids_filtered(self):
        """Only evidence IDs in the known set should be in supportingEvidenceIds."""
        candidate = _make_candidate(references=["ev_001", "ev_002", "ev_unknown"])
        hyp = _candidate_to_hypothesis(candidate, evidence_ids={"ev_001", "ev_002"})
        assert "ev_001" in hyp.supportingEvidenceIds
        assert "ev_002" in hyp.supportingEvidenceIds
        assert "ev_unknown" not in hyp.supportingEvidenceIds

    def test_derivation_trace_populated(self):
        """Derivation trace should include BFTS lineage."""
        candidate = _make_candidate(
            searchNodeId="node_123",
            pathSeedId="seed_456",
            reasoningPathId="path_789",
        )
        hyp = _candidate_to_hypothesis(candidate, evidence_ids=set())
        assert any("node_123" in t for t in hyp.derivationTrace)
        assert any("seed_456" in t for t in hyp.derivationTrace)
        assert any("path_789" in t for t in hyp.derivationTrace)

    def test_falsification_from_experiment_metrics(self):
        """Falsification criteria should reference experiment metrics."""
        candidate = _make_candidate()
        hyp = _candidate_to_hypothesis(candidate, evidence_ids=set())
        falsification_text = " ".join(hyp.falsificationCriteria).lower()
        assert "reaction_rate" in falsification_text or "yield" in falsification_text

    def test_hypothesis_scores_validation(self):
        """Scores outside [0,1] should be rejected."""
        with pytest.raises(ValidationError, match="scores must be in"):
            Hypothesis(
                id="hyp_bad",
                statement="Bad scores test statement here",
                falsificationCriteria=["test"],
                scores={"novelty": 1.5},
            )

    def test_dossier_rejects_unresolved_evidence(self):
        """Dossier should reject hypotheses referencing unknown evidence."""
        from app.contracts import (
            ProblemFrame, EvidenceMap, ResearchPlan, ResearchPlanStep,
            GenerationTrace, ExecutionClass,
        )
        with pytest.raises(ValidationError, match="unknown evidence IDs"):
            ResearchDossier(
                runId="r1",
                questionId="q1",
                problemFrame=ProblemFrame(
                    originalQuestion="Test question here",
                    scopedQuestion="Scoped test question here",
                ),
                evidenceMap=EvidenceMap(),
                hypotheses=[
                    Hypothesis(
                        id="h1",
                        statement="Test statement here for validation",
                        falsificationCriteria=["test"],
                        supportingEvidenceIds=["ev_missing"],
                    )
                ],
                researchPlan=ResearchPlan(
                    objective="Test",
                    steps=[ResearchPlanStep(
                        id="s1", order=1, title="S1", objective="O1"
                    )],
                ),
            )
