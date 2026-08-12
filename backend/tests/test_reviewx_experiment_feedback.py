import asyncio
import json
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


sys.path.insert(0, str(Path(__file__).parent.parent))

from app.contracts import ExecutionAssessment, ExperimentEvidence, ResearchDossier
from app.modules.review.experiment_feedback import review_experiment_feedback
from app.modules.review.reviews_api import (
    ReviseExperimentPlanRequest,
    RunExperimentFeedbackRequest,
    RunStoredExperimentFeedbackRequest,
    create_next_experiment_run_endpoint,
    list_experiment_feedback_endpoint,
    revise_experiment_plan_endpoint,
    run_experiment_feedback_endpoint,
    run_stored_experiment_feedback_endpoint,
)
from app.modules.review.experiment_feedback_storage import (
    create_experiment_feedback,
    get_experiment_feedback,
)
from app.models.run import RunConfig


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "scientific_research"


@pytest.fixture(autouse=True)
def isolated_experiment_feedback_storage(monkeypatch, tmp_path):
    storage_dir = tmp_path / "reviewx_experiment_feedback"
    monkeypatch.setattr(
        "app.modules.review.experiment_feedback_storage._STORAGE_DIR",
        storage_dir,
    )
    yield


def _payload(name: str):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _models():
    dossier = ResearchDossier.model_validate(_payload("research_dossier.json"))
    assessment = ExecutionAssessment.model_validate(_payload("execution_assessment.json"))
    evidence_payload = _payload("experiment_evidence.json")
    evidence_payload["metrics"].append({
        "name": "citation_precision",
        "value": 0.88,
        "unit": "ratio",
        "definition": "Supported citations divided by all extracted citations.",
        "split": "demo",
        "sourcePath": "artifacts/code_run_demo_001/metrics.json",
    })
    evidence = ExperimentEvidence.model_validate(evidence_payload)
    return dossier, assessment, evidence


def test_complete_experiment_is_accepted_for_interpretation():
    dossier, assessment, evidence = _models()

    result = review_experiment_feedback(dossier, evidence, execution_assessment=assessment)

    assert result.qualityAssessment.gateStatus == "pass"
    assert result.iterationDecision.decision == "accept_results"
    assert result.qualityAssessment.dimensionScores["metricCompleteness"] == 1.0
    assert not result.qualityAssessment.findings


def test_missing_planned_metric_blocks_result_and_targets_plan_revision():
    dossier, assessment, evidence = _models()
    evidence_payload = evidence.model_dump(mode="json")
    evidence_payload["metrics"] = [
        metric for metric in evidence_payload["metrics"] if metric["name"] != "citation_precision"
    ]
    incomplete = ExperimentEvidence.model_validate(evidence_payload)

    result = review_experiment_feedback(dossier, incomplete, execution_assessment=assessment)

    assert result.qualityAssessment.gateStatus == "fail"
    assert result.iterationDecision.decision == "revise_plan"
    assert "expectedMetrics" in result.iterationDecision.targetSections
    assert "EXPECTED_METRICS_MISSING" in {
        finding.code for finding in result.qualityAssessment.findings
    }


def test_unchanged_second_round_requires_a_real_plan_change():
    dossier, assessment, evidence = _models()
    previous = ExperimentEvidence.model_validate(deepcopy(evidence.model_dump(mode="json")))

    result = review_experiment_feedback(
        dossier,
        evidence,
        execution_assessment=assessment,
        previous_experiment=previous,
    )

    assert result.iterationDecision.decision == "revise_plan"
    assert "ITERATION_NO_METRIC_CHANGE" in {
        finding.code for finding in result.qualityAssessment.findings
    }
    assert len(result.iterationDecision.metricDeltas) == 2


def test_failed_experiment_routes_to_rerun_instead_of_paper_rewrite():
    dossier, assessment, evidence = _models()
    failed_payload = evidence.model_dump(mode="json")
    failed_payload.update({
        "status": "failed",
        "metrics": [],
        "failures": ["evaluation process timed out"],
    })
    failed = ExperimentEvidence.model_validate(failed_payload)

    result = review_experiment_feedback(dossier, failed, execution_assessment=assessment)

    assert result.qualityAssessment.gateStatus == "fail"
    assert result.iterationDecision.decision == "rerun_experiment"
    assert any(finding.targetModule == "code" for finding in result.qualityAssessment.findings)


def test_metric_delta_is_reported_without_claiming_directional_improvement():
    dossier, assessment, evidence = _models()
    previous_payload = evidence.model_dump(mode="json")
    for metric in previous_payload["metrics"]:
        if metric["name"] == "unsupported_claim_rate":
            metric["value"] = 0.2
        if metric["name"] == "citation_precision":
            metric["value"] = 0.8
    previous = ExperimentEvidence.model_validate(previous_payload)

    result = review_experiment_feedback(
        dossier,
        evidence,
        execution_assessment=assessment,
        previous_experiment=previous,
    )

    deltas = {item.name: item.delta for item in result.iterationDecision.metricDeltas}
    assert deltas == {"citation_precision": 0.08, "unsupported_claim_rate": -0.08}
    assert result.iterationDecision.decision == "accept_results"
    assert "does not establish scientific truth or metric directionality" in result.qualityAssessment.uncertainty


def test_endpoint_applies_structured_experiment_feedback_to_plan_package(monkeypatch):
    dossier, assessment, evidence = _models()
    payload = evidence.model_dump(mode="json")
    payload["metrics"] = [
        metric for metric in payload["metrics"] if metric["name"] != "citation_precision"
    ]
    incomplete = ExperimentEvidence.model_validate(payload)
    captured = {}

    class FakePlanPackageService:
        def add_feedback(self, package_id, **kwargs):
            captured["package_id"] = package_id
            captured.update(kwargs)

    monkeypatch.setattr(
        "app.services.plan_package_service.get_plan_package_service",
        lambda: FakePlanPackageService(),
    )
    response = asyncio.run(
        run_experiment_feedback_endpoint(
            RunExperimentFeedbackRequest(
                dossier=dossier,
                experimentEvidence=incomplete,
                executionAssessment=assessment,
                planPackageId="plan_pkg_demo",
                applyToPlanPackage=True,
            )
        )
    )

    assert response.iterationDecision.decision == "revise_plan"
    assert response.planFeedback.applied is True
    assert captured["package_id"] == "plan_pkg_demo"
    assert captured["source_view"] == "reviewx"
    assert "expectedMetrics" in captured["target_sections"]
    assert "ReviewX experiment decision: revise_plan" in captured["comment"]


def test_endpoint_rejects_feedback_write_without_plan_package():
    dossier, assessment, evidence = _models()

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            run_experiment_feedback_endpoint(
                RunExperimentFeedbackRequest(
                    dossier=dossier,
                    experimentEvidence=evidence,
                    executionAssessment=assessment,
                    applyToPlanPackage=True,
                )
            )
        )

    assert exc_info.value.status_code == 422


def test_run_endpoint_resolves_contract_artifacts_and_uses_assessment_plan_id(
    monkeypatch,
    tmp_path,
):
    dossier, assessment, evidence = _models()
    payload = evidence.model_dump(mode="json")
    payload["metrics"] = [
        metric for metric in payload["metrics"] if metric["name"] != "citation_precision"
    ]
    files = {
        "research_dossier.json": dossier.model_dump(mode="json"),
        "execution_assessment.json": assessment.model_dump(mode="json"),
        "experiment_evidence.json": payload,
    }
    artifacts = []
    for index, (filename, content) in enumerate(files.items()):
        path = tmp_path / filename
        path.write_text(json.dumps(content), encoding="utf-8")
        artifacts.append(
            SimpleNamespace(
                id=f"artifact_{index}",
                runId=dossier.runId,
                filename=filename,
                storagePath=str(path),
            )
        )
    previous_path = tmp_path / "previous_experiment_evidence.json"
    previous_payload = deepcopy(evidence.model_dump(mode="json"))
    previous_payload["metrics"][0]["value"] = 0.2
    previous_path.write_text(json.dumps(previous_payload), encoding="utf-8")
    artifacts.append(
        SimpleNamespace(
            id="artifact_previous",
            runId=dossier.runId,
            filename="experiment_evidence.json",
            storagePath=str(previous_path),
        )
    )

    class FakeArtifactStorage:
        def list_by_run(self, run_id):
            return [artifact for artifact in artifacts if artifact.runId == run_id]

        def get(self, artifact_id):
            return next((artifact for artifact in artifacts if artifact.id == artifact_id), None)

    captured = {}

    class FakePlanPackageService:
        def add_feedback(self, package_id, **kwargs):
            captured["package_id"] = package_id
            captured.update(kwargs)

    monkeypatch.setattr("app.storage.artifact_storage.get_storage", lambda: FakeArtifactStorage())
    monkeypatch.setattr(
        "app.services.plan_package_service.get_plan_package_service",
        lambda: FakePlanPackageService(),
    )

    response = asyncio.run(
        run_stored_experiment_feedback_endpoint(
            dossier.runId,
            RunStoredExperimentFeedbackRequest(applyToPlanPackage=True),
        )
    )

    assert response.runId == dossier.runId
    assert response.iterationDecision.decision == "revise_plan"
    assert response.sourceArtifacts == {
        "researchDossier": "artifact_0",
        "executionAssessment": "artifact_1",
        "experimentEvidence": "artifact_2",
        "previousExperimentEvidence": "artifact_previous",
    }
    assert response.iterationDecision.metricDeltas[0].name == "unsupported_claim_rate"
    assert response.iterationDecision.metricDeltas[0].delta == -0.08
    assert captured["package_id"] == assessment.planPackageId


def test_stored_feedback_history_survives_refresh(monkeypatch, tmp_path):
    dossier, assessment, evidence = _models()
    artifacts = []
    for index, (filename, model) in enumerate((
        ("research_dossier.json", dossier),
        ("execution_assessment.json", assessment),
        ("experiment_evidence.json", evidence),
    )):
        path = tmp_path / filename
        path.write_text(json.dumps(model.model_dump(mode="json")), encoding="utf-8")
        artifacts.append(SimpleNamespace(
            id=f"history_artifact_{index}",
            runId=dossier.runId,
            filename=filename,
            storagePath=str(path),
        ))

    class FakeArtifactStorage:
        def list_by_run(self, run_id):
            return [artifact for artifact in artifacts if artifact.runId == run_id]

        def get(self, artifact_id):
            return next((artifact for artifact in artifacts if artifact.id == artifact_id), None)

    monkeypatch.setattr("app.storage.artifact_storage.get_storage", lambda: FakeArtifactStorage())
    response = asyncio.run(
        run_stored_experiment_feedback_endpoint(
            dossier.runId,
            RunStoredExperimentFeedbackRequest(),
        )
    )
    history = asyncio.run(list_experiment_feedback_endpoint(runId=dossier.runId, limit=20))

    assert response.feedbackId.startswith("exprev_")
    assert history["total"] == 1
    assert history["records"][0]["id"] == response.feedbackId
    assert history["records"][0]["iterationDecision"]["decision"] == "accept_results"


def test_feedback_actions_revise_plan_then_create_one_idempotent_next_run(monkeypatch):
    record = create_experiment_feedback({
        "runId": "platform_run_001",
        "scientificRunId": "scientific_run_001",
        "questionId": "question_001",
        "planPackageId": "plan_pkg_001",
        "sourceArtifacts": {},
        "qualityAssessment": {"gateStatus": "fail"},
        "iterationDecision": {
            "decision": "revise_plan",
            "targetSections": ["expectedMetrics", "stages"],
        },
        "planFeedback": {"applied": True},
    })
    captured = {}

    class FakePlanPackageService:
        def revise(self, package_id, **kwargs):
            captured["package_id"] = package_id
            captured.update(kwargs)
            return SimpleNamespace(
                packageId=package_id,
                status=SimpleNamespace(value="needs_human_review"),
                revisions=[SimpleNamespace(
                    id="revision_001",
                    changedSections=["expectedMetrics", "stages"],
                )],
            )

    source_run = SimpleNamespace(
        id="platform_run_001",
        config=RunConfig(
            model="qwen-plus",
            maxIterTimes=3,
            workplaceName="direction_b_demo",
            cachePath="cache",
            port=8000,
            ideas="Initial experiment",
        ),
        isMock=False,
    )
    created_runs = {}

    class FakeRunService:
        def get_run(self, run_id):
            if run_id == source_run.id:
                return source_run
            return created_runs.get(run_id)

        def create_run(self, run_data):
            created = SimpleNamespace(
                id="run_next_001",
                status=SimpleNamespace(value="pending"),
                config=run_data.config,
            )
            created_runs[created.id] = created
            return created

    monkeypatch.setattr(
        "app.services.plan_package_service.get_plan_package_service",
        lambda: FakePlanPackageService(),
    )
    monkeypatch.setattr("app.services.run_service.get_service", lambda: FakeRunService())

    revision = asyncio.run(
        revise_experiment_plan_endpoint(
            record["id"],
            ReviseExperimentPlanRequest(),
        )
    )
    first_run = asyncio.run(create_next_experiment_run_endpoint(record["id"]))
    repeated_run = asyncio.run(create_next_experiment_run_endpoint(record["id"]))

    assert revision.revisionId == "revision_001"
    assert captured["generation_mode"] == "deterministic"
    assert captured["target_sections"] == ["expectedMetrics", "stages"]
    assert first_run.runId == "run_next_001"
    assert first_run.reused is False
    assert repeated_run.runId == "run_next_001"
    assert repeated_run.reused is True
    stored = get_experiment_feedback(record["id"])
    assert stored["planRevision"]["revisionId"] == "revision_001"
    assert stored["nextRunId"] == "run_next_001"


def test_revise_plan_rejects_unattached_feedback():
    record = create_experiment_feedback({
        "runId": "run_001",
        "planPackageId": "plan_001",
        "iterationDecision": {"decision": "revise_plan", "targetSections": ["stages"]},
        "planFeedback": {"applied": False},
    })

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            revise_experiment_plan_endpoint(record["id"], ReviseExperimentPlanRequest())
        )

    assert exc_info.value.status_code == 409
