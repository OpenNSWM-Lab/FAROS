"""
Test: ResearchDossier schema validation against public contract.

Validates that the dossier builder produces output that passes the
shared contract test (test_scientific_research_contracts.py).
"""

import json
import sys
from pathlib import Path
from datetime import datetime

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.contracts import (
    ResearchDossier,
    ProblemFrame,
    EvidenceMap,
    EvidenceRecord,
    EvidenceStance,
    EvidenceTier,
    Hypothesis,
    ResearchPlan,
    ResearchPlanStep,
    GenerationTrace,
    ArtifactRef,
    ArtifactKind,
    TargetModule,
    ExecutionClass,
    SCHEMA_VERSION,
)


def _make_minimal_dossier() -> ResearchDossier:
    """Build a minimal valid ResearchDossier for testing."""
    return ResearchDossier(
        runId="run_test_001",
        questionId="q_test_001",
        problemFrame=ProblemFrame(
            originalQuestion="How does temperature affect bacterial growth?",
            scopedQuestion="Does temperature increase correlate with bacterial growth rate in E. coli cultures?",
            definitions={"bacterial growth": "Increase in cell population over time"},
            observableVariables=["growth_rate", "temperature"],
            assumptions=["Nutrient availability is constant"],
            outOfScope=["Long-term evolutionary adaptation"],
            subQuestions=["What is the optimal temperature range?"],
        ),
        evidenceMap=EvidenceMap(
            consensus=["Temperature affects growth rate"],
            disputedClaims=[],
            supportingEvidence=[
                EvidenceRecord(
                    id="ev_001",
                    title="Temperature effects on E. coli",
                    summary="Study showing growth rate vs temperature.",
                    stance=EvidenceStance.SUPPORT,
                    sourceType="paper",
                    source="semantic_scholar",
                    authors=["Author A"],
                    year=2024,
                    doi="10.1234/test",
                    evidenceTier=EvidenceTier.PRIMARY,
                    relevanceScore=0.9,
                    verified=True,
                )
            ],
            counterEvidence=[],
            contextualEvidence=[],
            unresolvedGaps=["Effect on other species unknown"],
        ),
        hypotheses=[
            Hypothesis(
                id="hyp_001",
                statement="Higher temperature increases E. coli growth rate up to 37C.",
                rationale="Known optimal growth temperature.",
                derivationTrace=["problem_frame", "ev_001"],
                supportingEvidenceIds=["ev_001"],
                counterEvidenceIds=[],
                falsificationCriteria=["Growth rate does not increase with temperature"],
                confounders=["Nutrient depletion"],
                alternativeExplanations=["pH change causes growth difference"],
                scores={"novelty": 0.6, "feasibility": 0.9},
                confidence=0.8,
            )
        ],
        researchPlan=ResearchPlan(
            objective="Measure E. coli growth at different temperatures.",
            steps=[
                ResearchPlanStep(
                    id="step_001",
                    order=1,
                    title="Culture setup",
                    objective="Prepare bacterial cultures.",
                    inputs=["E. coli stock", "growth medium"],
                    tools=["incubator", "spectrophotometer"],
                    method=["Prepare cultures", "Set temperatures"],
                    outputs=["culture_data.csv"],
                    metrics=["od600"],
                    stopConditions=["All temperatures tested"],
                    dependencies=[],
                    risks=["Contamination"],
                )
            ],
            requiredData=["E. coli cultures"],
            requiredResources=["lab equipment"],
            expectedOutcomes=["Growth curve at each temperature"],
            constraints=["Same medium batch"],
            ethics=["Follow biosafety protocols"],
            executionClass=ExecutionClass.COMPUTATIONAL_READY,
        ),
        uncertainties=["External validity limited"],
        generationTrace=GenerationTrace(
            providerName="test-provider",
            model="test-model",
        ),
        artifactRefs=[
            ArtifactRef(
                id="art_001",
                kind=ArtifactKind.IDEA,
                sourceModule=TargetModule.IDEA,
                uri="artifacts/run_test_001/dossier.json",
                contentHash="sha256:test",
                version="1",
            )
        ],
    )


class TestResearchDossierSchema:
    def test_minimal_dossier_validates(self):
        dossier = _make_minimal_dossier()
        assert dossier.schemaVersion == SCHEMA_VERSION
        assert dossier.runId == "run_test_001"

    def test_dossier_round_trips_json(self):
        dossier = _make_minimal_dossier()
        json_str = dossier.model_dump_json()
        restored = ResearchDossier.model_validate_json(json_str)
        assert restored == dossier

    def test_dossier_rejects_unknown_evidence_ref(self):
        from pydantic import ValidationError
        dossier = _make_minimal_dossier()
        data = dossier.model_dump()
        data["hypotheses"][0]["supportingEvidenceIds"].append("ev_missing")
        with pytest.raises(ValidationError, match="unknown evidence IDs"):
            ResearchDossier.model_validate(data)

    def test_dossier_rejects_unknown_field(self):
        from pydantic import ValidationError
        data = _make_minimal_dossier().model_dump()
        data["unknownField"] = "bad"
        with pytest.raises(ValidationError):
            ResearchDossier.model_validate(data)

    def test_hypothesis_scores_in_range(self):
        from pydantic import ValidationError
        data = _make_minimal_dossier().model_dump()
        data["hypotheses"][0]["scores"]["novelty"] = 1.5
        with pytest.raises(ValidationError):
            ResearchDossier.model_validate(data)

    def test_confidence_in_range(self):
        from pydantic import ValidationError
        data = _make_minimal_dossier().model_dump()
        data["hypotheses"][0]["confidence"] = -0.1
        with pytest.raises(ValidationError):
            ResearchDossier.model_validate(data)

    def test_falsification_criteria_required(self):
        """Each hypothesis must have at least one falsification criterion."""
        from pydantic import ValidationError
        data = _make_minimal_dossier().model_dump()
        data["hypotheses"][0]["falsificationCriteria"] = []
        with pytest.raises(ValidationError):
            ResearchDossier.model_validate(data)

    def test_research_plan_requires_steps(self):
        from pydantic import ValidationError
        data = _make_minimal_dossier().model_dump()
        data["researchPlan"]["steps"] = []
        with pytest.raises(ValidationError):
            ResearchDossier.model_validate(data)

    def test_evidence_stance_values(self):
        """Evidence stance must be support, counter, or context."""
        dossier = _make_minimal_dossier()
        for ev in dossier.evidenceMap.supportingEvidence:
            assert ev.stance == EvidenceStance.SUPPORT

    def test_artifact_ref_kind_values(self):
        dossier = _make_minimal_dossier()
        assert dossier.artifactRefs[0].kind == ArtifactKind.IDEA
        assert dossier.artifactRefs[0].sourceModule == TargetModule.IDEA
