import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.contracts import (
    CONTRACT_MODELS,
    ExperimentEvidence,
    QuestionBatch,
    ResearchDossier,
    contract_json_schemas,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "scientific_research"
FIXTURE_MODELS = {
    "question_run.json": "ScientificQuestionRun",
    "research_dossier.json": "ResearchDossier",
    "execution_assessment.json": "ExecutionAssessment",
    "experiment_evidence.json": "ExperimentEvidence",
    "research_narrative.json": "ResearchNarrative",
    "quality_assessment.json": "QualityAssessment",
    "question_batch.json": "QuestionBatch",
    "question_set_manifest.json": "QuestionSetManifest",
}


def _fixture(name: str):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(("filename", "model_name"), FIXTURE_MODELS.items())
def test_canonical_fixture_validates_and_round_trips(filename: str, model_name: str):
    model = CONTRACT_MODELS[model_name]
    parsed = model.model_validate(_fixture(filename))

    assert parsed.schemaVersion == "scientific-research/v1"
    assert model.model_validate_json(parsed.model_dump_json()) == parsed


def test_contracts_reject_unknown_fields_instead_of_silently_dropping_them():
    payload = _fixture("question_batch.json")
    payload["progess"] = 0.5

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        QuestionBatch.model_validate(payload)


def test_contracts_reject_an_unrecognized_schema_version():
    payload = _fixture("question_batch.json")
    payload["schemaVersion"] = "scientific-research/v2"

    with pytest.raises(ValidationError, match="scientific-research/v1"):
        QuestionBatch.model_validate(payload)


def test_dossier_rejects_unresolved_evidence_references():
    payload = _fixture("research_dossier.json")
    payload["hypotheses"][0]["supportingEvidenceIds"].append("ev_missing")

    with pytest.raises(ValidationError, match="unknown evidence IDs"):
        ResearchDossier.model_validate(payload)


def test_executed_evidence_requires_reproducibility_provenance():
    payload = deepcopy(_fixture("experiment_evidence.json"))
    payload["environmentHash"] = ""

    with pytest.raises(ValidationError, match="environmentHash"):
        ExperimentEvidence.model_validate(payload)


def test_json_schemas_are_available_for_adapters_and_external_validation():
    schemas = contract_json_schemas()

    assert schemas.keys() == CONTRACT_MODELS.keys()
    assert all(schema["type"] == "object" for schema in schemas.values())
