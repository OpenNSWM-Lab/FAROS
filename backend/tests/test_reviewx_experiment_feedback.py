import asyncio
import json
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError


sys.path.insert(0, str(Path(__file__).parent.parent))

from app.contracts import ExecutionAssessment, ExperimentEvidence, ResearchDossier
from app.faros.capabilities.adapters.experiment import ExperimentCapability
from app.faros.runtime.orchestrator import FarosOrchestrator
from app.modules.review.experiment_feedback import review_experiment_feedback
from app.modules.review.experiment_series import (
    ExperimentLoopPolicy,
    MetricGuardrail,
    evaluate_experiment_series,
    iteration_controller_feedback,
)
from app.modules.review.reviews_api import (
    AdvanceExperimentLoopRequest,
    ReviseExperimentPlanRequest,
    RunExperimentFeedbackRequest,
    RunSciFactCompetitionCaseRequest,
    RunStoredExperimentFeedbackRequest,
    _faros_run_lineage,
    _register_scifact_human_review,
    _write_scifact_job,
    _stored_feedback_response,
    advance_experiment_loop_endpoint,
    create_next_experiment_run_endpoint,
    get_scifact_competition_artifact_endpoint,
    get_experiment_feedback_endpoint,
    list_experiment_feedback_endpoint,
    revise_experiment_plan_endpoint,
    run_experiment_feedback_endpoint,
    run_stored_experiment_feedback_endpoint,
    start_scifact_competition_case_endpoint,
)
from app.modules.review.experiment_feedback_storage import (
    create_experiment_feedback,
    get_experiment_feedback,
    update_experiment_feedback,
)
from app.modules.review.human_signoff import decide_human_signoff
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


def test_faros_lineage_preserves_inherited_human_feedback(monkeypatch):
    inherited_feedback = {
        "feedbackHash": "sha256:feedback",
        "items": [{
            "decisionId": "human_1",
            "stage": "plan",
            "conditions": ["Report calibration on the fixed test split"],
        }],
    }

    class FakeStateStore:
        def get_run(self, run_id):
            assert run_id == "faros_child"
            return {
                "id": run_id,
                "parent_run_id": "faros_parent",
                "research_series_id": "series_1",
                "iteration_number": 2,
                "inputs": {"iterationFeedback": {"humanFeedback": inherited_feedback}},
            }

    monkeypatch.setattr(
        "app.faros.runtime.state_store.FarosStateStore",
        FakeStateStore,
    )

    lineage = _faros_run_lineage("faros_child")

    assert lineage is not None
    assert lineage["parentRunId"] == "faros_parent"
    assert lineage["iterationNumber"] == 2
    assert lineage["inheritedHumanFeedback"] == inherited_feedback


def _approve_feedback_stages(feedback_id: str, *stages: str):
    record = get_experiment_feedback(feedback_id)
    assert record is not None
    for stage in stages:
        signoffs = decide_human_signoff(
            record,
            stage=stage,
            status="approved",
            reviewer_role="team_lead",
            reviewer_id="test-reviewer",
            rationale=f"Approved {stage} for the test workflow.",
        )
        record = update_experiment_feedback(feedback_id, {"humanSignoffs": signoffs})
    return record


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


def test_run_endpoint_reads_contracts_from_faros_state_store(monkeypatch, tmp_path):
    dossier, assessment, evidence = _models()
    run_dir = tmp_path / dossier.runId
    run_dir.mkdir()
    artifact_records = []
    for index, (filename, model) in enumerate((
        ("research_dossier.json", dossier),
        ("execution_assessment.json", assessment),
        ("experiment_evidence.json", evidence),
    )):
        path = run_dir / filename
        path.write_text(json.dumps(model.model_dump(mode="json")), encoding="utf-8")
        artifact_records.append({
            "id": f"faros_artifact_{index}",
            "type": filename.removesuffix(".json"),
            "uri": f"file://{path}",
        })

    class EmptyPlatformArtifactStorage:
        def list_by_run(self, run_id):
            return []

        def get(self, artifact_id):
            return None

    class FakeFarosStateStore:
        def list_artifacts(self, run_id):
            return artifact_records if run_id == dossier.runId else []

        def list_runs(self):
            return [{"id": dossier.runId}]

    monkeypatch.setattr("app.storage.artifact_storage.get_storage", lambda: EmptyPlatformArtifactStorage())
    monkeypatch.setattr("app.faros.runtime.state_store.FarosStateStore", FakeFarosStateStore)

    response = asyncio.run(
        run_stored_experiment_feedback_endpoint(
            dossier.runId,
            RunStoredExperimentFeedbackRequest(),
        )
    )

    assert response.runId == dossier.runId
    assert response.sourceArtifacts == {
        "researchDossier": "faros_artifact_0",
        "executionAssessment": "faros_artifact_1",
        "experimentEvidence": "faros_artifact_2",
    }


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
    _approve_feedback_stages(record["id"], "plan", "repair")
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


def test_cross_run_experiments_compare_when_lineage_is_verified():
    dossier, assessment, current = _models()
    previous_payload = current.model_dump(mode="json")
    previous_payload["runId"] = "faros_parent"
    previous_payload["questionId"] = "faros_parent:question"
    previous_payload["metrics"][0]["value"] = 0.2
    previous = ExperimentEvidence.model_validate(previous_payload)

    result = review_experiment_feedback(
        dossier,
        current,
        execution_assessment=assessment,
        previous_experiment=previous,
        same_research_series=True,
    )

    codes = {finding.code for finding in result.qualityAssessment.findings}
    deltas = {item.name: item.delta for item in result.iterationDecision.metricDeltas}
    assert "PREVIOUS_EXPERIMENT_ID_MISMATCH" not in codes
    assert deltas["unsupported_claim_rate"] == -0.08
    assert any(
        item["rule"] == "previous_experiment_research_series_match"
        and item["status"] == "pass"
        and item["detail"] == "lineage"
        for item in result.qualityAssessment.ruleTrace
    )


def test_metric_comparison_normalizes_method_prefixes_and_split_suffixes():
    dossier, assessment, current = _models()
    assessment.validationMetrics = [
        "F1-Score",
        "Expected Calibration Error (ECE)",
    ]
    current_payload = current.model_dump(mode="json")
    current_payload["metrics"] = [
        {
            "name": "Baseline_F1-Score",
            "value": 0.7,
            "unit": "score",
            "definition": "Baseline F1.",
            "split": "test",
            "sourcePath": "metrics.json",
        },
        {
            "name": "Method_F1-Score",
            "value": 0.9,
            "unit": "score",
            "definition": "Method F1.",
            "split": "test",
            "sourcePath": "metrics.json",
        },
        {
            "name": "Baseline_Expected Calibration Error (ECE)",
            "value": 0.2,
            "unit": "score",
            "definition": "Baseline ECE.",
            "split": "test",
            "sourcePath": "metrics.json",
        },
        {
            "name": "Method_Expected Calibration Error (ECE)",
            "value": 0.05,
            "unit": "score",
            "definition": "Method ECE.",
            "split": "test",
            "sourcePath": "metrics.json",
        },
    ]
    previous_payload = deepcopy(current_payload)
    previous_payload["runId"] = "faros_parent"
    previous_payload["questionId"] = "faros_parent:question"
    previous_payload["metrics"] = [
        {**metric, "name": metric["name"].split("_", 1)[1], "split": (
            "test_baseline" if metric["name"].startswith("Baseline_") else "test_method"
        )}
        for metric in previous_payload["metrics"]
    ]

    result = review_experiment_feedback(
        dossier,
        ExperimentEvidence.model_validate(current_payload),
        execution_assessment=assessment,
        previous_experiment=ExperimentEvidence.model_validate(previous_payload),
        same_research_series=True,
    )

    codes = {finding.code for finding in result.qualityAssessment.findings}
    delta_names = {item.name for item in result.iterationDecision.metricDeltas}
    assert "EXPECTED_METRICS_MISSING" not in codes
    assert "ITERATION_METRICS_NOT_COMPARABLE" not in codes
    assert delta_names == {
        "baseline:F1-Score",
        "baseline:Expected Calibration Error (ECE)",
        "method:F1-Score",
        "method:Expected Calibration Error (ECE)",
    }


def test_iteration_warns_when_evaluation_record_count_changes():
    dossier, assessment, current = _models()
    current_payload = current.model_dump(mode="json")
    current_payload["metricAudit"] = {"status": "passed", "recordCount": 400}
    previous_payload = deepcopy(current_payload)
    previous_payload["runId"] = "faros_parent"
    previous_payload["questionId"] = "faros_parent:question"
    previous_payload["metricAudit"] = {"status": "passed", "recordCount": 200}

    result = review_experiment_feedback(
        dossier,
        ExperimentEvidence.model_validate(current_payload),
        execution_assessment=assessment,
        previous_experiment=ExperimentEvidence.model_validate(previous_payload),
        same_research_series=True,
    )

    codes = {finding.code for finding in result.qualityAssessment.findings}
    assert "ITERATION_BENCHMARK_CHANGED" in codes
    assert result.qualityAssessment.gateStatus == "warn"
    assert result.qualityAssessment.dimensionScores["iterationReadiness"] == 0.0
    assert result.iterationDecision.decision == "revise_plan"


def test_gate_uses_assessed_metrics_and_content_addressed_evaluation_records():
    dossier, assessment, evidence = _models()
    dossier.researchPlan.steps[0].metrics.append("generic_placeholder_metric")
    payload = evidence.model_dump(mode="json")
    payload["dataHashes"] = {}
    payload["unsupportedClaims"] = ["A broader hypothesis not established by one run."]
    payload["artifactRefs"].append({
        "id": "code_run_demo_001:evaluation-records",
        "kind": "other",
        "sourceModule": "code",
        "uri": "evaluation_records.json",
        "contentHash": "sha256:evaluation-records",
        "version": "1",
    })
    audited = ExperimentEvidence.model_validate(payload)

    result = review_experiment_feedback(
        dossier,
        audited,
        execution_assessment=assessment,
    )

    codes = {finding.code for finding in result.qualityAssessment.findings}
    assert "EXPECTED_METRICS_MISSING" not in codes
    assert "EXPERIMENT_DATA_PROVENANCE_MISSING" not in codes
    assert result.qualityAssessment.gateStatus == "pass"
    assert result.iterationDecision.decision == "accept_results"
    assert "Required corrections" not in result.iterationDecision.feedbackComment
    assert "Scope notes" in result.iterationDecision.feedbackComment
    unsupported = next(
        finding
        for finding in result.qualityAssessment.findings
        if finding.code == "EXPERIMENT_UNSUPPORTED_CLAIMS"
    )
    assert unsupported.severity == "info"


def test_orchestrator_iteration_inherits_inputs_and_injects_feedback():
    parent = {
        "id": "faros_parent",
        "blueprint_id": "ml_paper",
        "profile_id": "faros_llm",
        "status": "completed",
        "execution_mode": "execute",
        "runtime_options": {"readyNodePolicy": "fifo"},
        "research_series_id": "faros_series",
        "iteration_number": 1,
        "inputs": {
            "seedQuery": "Does the method improve calibration?",
            "constraints": [
                "Use fixed inputs",
                "ReviewX experiment decision: stale feedback from iteration 1",
            ],
        },
    }
    captured = {}

    class FakeStateStore:
        def get_run(self, run_id):
            return parent if run_id == parent["id"] else None

        def list_runs(self):
            return [parent]

    class FakeEventLog:
        def info(self, *args, **kwargs):
            captured["event"] = {"args": args, "kwargs": kwargs}

    orchestrator = FarosOrchestrator.__new__(FarosOrchestrator)
    orchestrator.state_store = FakeStateStore()
    orchestrator.event_log = FakeEventLog()
    orchestrator.get_run_memory = lambda _run_id: {"data": {
        "ideaSessionId": "idea_completed",
        "projectId": "project_parent",
        "experimentEvidence": {
            "dataHashes": {"frozen_benchmark": "sha256:frozen-inputs"}
        },
    }}

    def fake_create_run(**kwargs):
        captured.update(kwargs)
        return {
            "id": "faros_child",
            "status": "pending",
            "research_series_id": kwargs["research_series_id"],
            "iteration_number": kwargs["iteration_number"],
        }

    orchestrator.create_run = fake_create_run
    feedback = {
        "decision": "revise_plan",
        "rationale": "Calibration evidence is incomplete.",
        "targetSections": ["expectedMetrics"],
        "metricDeltas": [],
        "nextActions": ["Add Brier score."],
        "feedbackComment": "ReviewX experiment decision: revise_plan",
    }

    child, reused = orchestrator.create_iteration_run(parent["id"], "exprev_demo", feedback)

    assert reused is False
    assert child["id"] == "faros_child"
    assert captured["parent_run_id"] == parent["id"]
    assert captured["research_series_id"] == "faros_series"
    assert captured["iteration_number"] == 2
    assert captured["iteration_feedback_id"] == "exprev_demo"
    assert captured["inputs"]["resumeIdeaSessionId"] == "idea_completed"
    assert captured["inputs"]["previousProjectId"] == "project_parent"
    assert captured["inputs"]["frozenBenchmarkFingerprint"] == "sha256:frozen-inputs"
    assert captured["inputs"]["iterationFeedback"]["nextActions"] == ["Add Brier score."]
    assert captured["inputs"]["constraints"] == ["Use fixed inputs"]


def test_faros_feedback_creates_idempotent_next_run_without_plan_package(monkeypatch):
    record = create_experiment_feedback({
        "runId": "faros_parent",
        "runKind": "faros",
        "researchSeriesId": "faros_parent",
        "iterationNumber": 1,
        "planPackageId": None,
        "iterationDecision": {
            "decision": "revise_plan",
            "rationale": "Add a stronger baseline.",
            "targetSections": ["stages"],
            "metricDeltas": [],
            "nextActions": ["Add an embedding baseline."],
            "feedbackComment": "ReviewX experiment decision: revise_plan",
        },
        "planFeedback": {"applied": False},
    })
    child = {
        "id": "faros_child",
        "status": "pending",
        "research_series_id": "faros_parent",
        "iteration_number": 2,
    }
    calls = []

    class FakeOrchestrator:
        def get_run(self, run_id):
            return child if run_id == child["id"] else None

        def create_iteration_run(self, parent_run_id, feedback_id, feedback):
            calls.append((parent_run_id, feedback_id, feedback))
            return child, False

    monkeypatch.setattr(
        "app.faros.runtime.orchestrator.get_orchestrator",
        lambda: FakeOrchestrator(),
    )

    _approve_feedback_stages(record["id"], "plan", "repair")
    first = asyncio.run(create_next_experiment_run_endpoint(record["id"]))
    repeated = asyncio.run(create_next_experiment_run_endpoint(record["id"]))

    assert first.runKind == "faros"
    assert first.runId == child["id"]
    assert first.planPackageId is None
    assert first.iterationNumber == 2
    assert repeated.reused is True
    assert len(calls) == 1
    assert get_experiment_feedback(record["id"])["nextRunId"] == child["id"]


def test_experiment_adapter_forwards_feedback_only_for_iteration_runs(monkeypatch):
    captured = {}

    def fake_generate(_candidate, **kwargs):
        captured.update(kwargs)
        return kwargs["existing_project_id"]

    monkeypatch.setattr(
        "app.services.code_agent_service.generate_project_from_research_candidate",
        fake_generate,
    )
    feedback = {
        "decision": "revise_plan",
        "nextActions": ["Add a stronger baseline."],
    }

    succeeded = ExperimentCapability()._generate_code_via_agent(
        project_id="project_iteration",
        idea_session_id="idea_completed",
        selected_candidate={"id": "candidate_1", "title": "Candidate"},
        language="python",
        framework="numpy",
        provider_name="qwen",
        model="qwen-plus",
        iteration_feedback=feedback,
    )

    assert succeeded is True
    assert captured["iteration_feedback"] == feedback


def test_iteration_compares_only_matching_frozen_benchmarks():
    dossier, assessment, current = _models()
    current_payload = current.model_dump(mode="json")
    current_payload["metricAudit"] = {
        "status": "passed",
        "schemaVersion": "faros-evaluation/v1",
        "recordCount": 100,
    }
    current_payload["dataHashes"]["frozen_benchmark"] = "sha256:benchmark-v1"
    previous_payload = deepcopy(current_payload)
    previous_payload["runId"] = "faros_parent"
    previous_payload["questionId"] = "faros_parent:question"
    previous_payload["dataHashes"]["frozen_benchmark"] = "sha256:benchmark-v0"

    result = review_experiment_feedback(
        dossier,
        ExperimentEvidence.model_validate(current_payload),
        execution_assessment=assessment,
        previous_experiment=ExperimentEvidence.model_validate(previous_payload),
        same_research_series=True,
    )

    assert "ITERATION_BENCHMARK_CHANGED" in {
        finding.code for finding in result.qualityAssessment.findings
    }
    assert result.qualityAssessment.dimensionScores["iterationReadiness"] == 0.0


def _series_record(
    iteration, value, *, decision="accept_results", run_id=None,
    benchmark_fingerprint="sha256:current",
):
    return {
        "id": f"feedback_{iteration}",
        "runId": run_id or f"run_{iteration}",
        "researchSeriesId": "series_1",
        "iterationNumber": iteration,
        "createdAt": f"2026-08-2{iteration}T00:00:00+00:00",
        "benchmarkFingerprint": benchmark_fingerprint,
        "metricSnapshot": [
            {"name": "method:Expected Calibration Error (ECE)", "value": value},
            {"name": "method:F1-Score", "value": 0.8},
            {"name": "method:Brier Score", "value": 0.1},
            {"name": "method:AUROC", "value": 0.99},
        ],
        "qualityAssessment": {"gateStatus": "pass"},
        "iterationDecision": {"decision": decision, "nextActions": []},
    }


def _series_policy(**updates):
    payload = {
        "primaryMetric": "method:Expected Calibration Error (ECE)",
        "direction": "minimize",
        "minIterations": 3,
        "maxIterations": 5,
        "minAbsoluteImprovement": 0.001,
        "patience": 2,
    }
    payload.update(updates)
    return ExperimentLoopPolicy(**payload)


def test_series_policy_rejects_inverted_iteration_bounds():
    with pytest.raises(ValidationError):
        _series_policy(minIterations=4, maxIterations=3)


def test_series_policy_rejects_primary_metric_as_guardrail():
    with pytest.raises(ValidationError):
        _series_policy(guardrails=[{
            "metric": "method:Expected Calibration Error (ECE)",
            "direction": "minimize",
            "threshold": 0.1,
        }])


def test_series_continues_until_minimum_iterations_are_observed():
    progress = evaluate_experiment_series(
        "series_1",
        [_series_record(1, 0.30), _series_record(2, 0.25)],
        _series_policy(),
    )

    assert progress.status == "continue"
    assert progress.stopReason == "minimum_iterations_not_reached"
    assert progress.bestValue == pytest.approx(0.25)
    assert progress.rounds[-1].improved is True


def test_series_continues_after_minimum_while_optimization_budget_remains():
    progress = evaluate_experiment_series(
        "series_1",
        [_series_record(1, 0.30), _series_record(2, 0.25), _series_record(3, 0.20)],
        _series_policy(),
    )

    assert progress.status == "continue"
    assert progress.stopReason == "optimization_budget_remaining"
    assert progress.bestIteration == 3
    assert progress.consecutiveNoImprovement == 0


def test_series_stops_honestly_when_patience_is_exhausted():
    progress = evaluate_experiment_series(
        "series_1",
        [_series_record(1, 0.20), _series_record(2, 0.21), _series_record(3, 0.22)],
        _series_policy(),
    )

    assert progress.status == "completed"
    assert progress.stopReason == "no_improvement_patience_exhausted"
    assert progress.bestIteration == 1
    assert progress.consecutiveNoImprovement == 2


def test_series_blocks_when_primary_metric_is_missing():
    record = _series_record(1, 0.30)
    record["metricSnapshot"] = [{"name": "method:F1-Score", "value": 0.8}]

    progress = evaluate_experiment_series("series_1", [record], _series_policy())

    assert progress.status == "blocked"
    assert progress.stopReason == "primary_metric_missing"
    assert progress.availableMetrics == ["method:F1-Score"]


def test_series_controller_feedback_targets_best_value_and_next_round():
    progress = evaluate_experiment_series(
        "series_1", [_series_record(1, 0.30)], _series_policy()
    )

    feedback = iteration_controller_feedback(_series_policy(), progress)

    assert "controlled iteration 2" in feedback["nextAction"]
    assert "decrease 'method:Expected Calibration Error (ECE)'" in feedback["nextAction"]
    assert "inherited frozen benchmark" in feedback["nextAction"]
    assert "Surpass the best guardrail-feasible result from iteration 1" in feedback["nextAction"]


def test_series_best_excludes_rounds_from_an_old_frozen_benchmark():
    progress = evaluate_experiment_series(
        "series_1",
        [
            _series_record(1, 0.004, benchmark_fingerprint="sha256:old"),
            _series_record(2, 0.16, benchmark_fingerprint="sha256:current"),
            _series_record(3, 0.14, benchmark_fingerprint="sha256:current"),
        ],
        _series_policy(),
    )

    assert progress.bestValue == pytest.approx(0.14)
    assert progress.bestIteration == 3
    assert progress.rounds[0].comparableBenchmark is False


def test_series_requires_guardrail_recovery_before_accepting_primary_improvement():
    records = [_series_record(1, 0.30), _series_record(2, 0.03)]
    records[-1]["metricSnapshot"][1]["value"] = 0.0
    records[-1]["metricSnapshot"][2]["value"] = 0.24
    policy = _series_policy(
        maxIterations=6,
        guardrails=[
            MetricGuardrail(metric="method:F1-Score", direction="maximize", threshold=0.75),
            MetricGuardrail(metric="method:Brier Score", direction="minimize", threshold=0.15),
        ],
    )

    progress = evaluate_experiment_series("series_1", records, policy)
    feedback = iteration_controller_feedback(policy, progress)

    assert progress.status == "continue"
    assert progress.stopReason == "guardrail_recovery_required"
    assert progress.guardrailsSatisfied is False
    assert {item.metric for item in progress.guardrailViolations} == {
        "method:F1-Score",
        "method:Brier Score",
    }
    assert progress.bestValue == pytest.approx(0.03)
    assert progress.bestFeasibleValue == pytest.approx(0.30)
    assert "Recover current violations first" in feedback["nextAction"]
    assert feedback["policy"]["guardrails"][0]["threshold"] == pytest.approx(0.75)


def test_series_reports_unresolved_guardrails_when_budget_is_exhausted():
    record = _series_record(5, 0.03)
    record["metricSnapshot"][1]["value"] = 0.0

    progress = evaluate_experiment_series(
        "series_1",
        [record],
        _series_policy(
            maxIterations=5,
            guardrails=[{
                "metric": "method:F1-Score",
                "direction": "maximize",
                "threshold": 0.75,
            }],
        ),
    )

    assert progress.status == "completed"
    assert progress.stopReason == "maximum_iterations_reached_with_guardrail_violations"


def test_series_advance_injects_controller_feedback_and_creates_pending_child(monkeypatch):
    stored = create_experiment_feedback(
        _series_record(1, 0.30, run_id="faros_parent")
    )
    captured = {}

    class FakeOrchestrator:
        def create_iteration_run(self, run_id, feedback_id, decision):
            captured.update({
                "run_id": run_id,
                "feedback_id": feedback_id,
                "decision": decision,
            })
            return {
                "id": "faros_child",
                "status": "pending",
                "research_series_id": "series_1",
                "iteration_number": 2,
            }, False

    monkeypatch.setattr(
        "app.modules.review.reviews_api._faros_run_lineage",
        lambda _run_id: {
            "runKind": "faros",
            "parentRunId": None,
            "researchSeriesId": "series_1",
            "iterationNumber": 1,
        },
    )
    monkeypatch.setattr(
        "app.faros.runtime.orchestrator.get_orchestrator",
        lambda: FakeOrchestrator(),
    )

    _approve_feedback_stages(stored["id"], "plan")
    response = asyncio.run(
        advance_experiment_loop_endpoint(
            "faros_parent",
            AdvanceExperimentLoopRequest(policy=_series_policy()),
        )
    )

    assert response.nextRunId == "faros_child"
    assert response.progress.status == "continue"
    assert captured["run_id"] == "faros_parent"
    assert "controlled iteration 2" in captured["decision"]["nextActions"][-1]
    updated = get_experiment_feedback(stored["id"])
    assert updated["nextRunId"] == "faros_child"
    assert updated["loopPolicy"]["minIterations"] == 3


def test_stored_feedback_response_restores_loop_policy_and_progress():
    dossier, assessment, evidence = _models()
    feedback = review_experiment_feedback(
        dossier,
        evidence,
        execution_assessment=assessment,
    )
    policy = _series_policy()
    progress = evaluate_experiment_series(
        "series_1",
        [_series_record(1, 0.30)],
        policy,
    )
    record = create_experiment_feedback({
        "runId": "faros_parent",
        "runKind": "faros",
        "researchSeriesId": "series_1",
        "iterationNumber": 1,
        "sourceArtifacts": {},
        "metricSnapshot": _series_record(1, 0.30)["metricSnapshot"],
        "qualityAssessment": feedback.qualityAssessment.model_dump(mode="json"),
        "iterationDecision": feedback.iterationDecision.model_dump(mode="json"),
        "planFeedback": {},
        "loopPolicy": policy.model_dump(mode="json"),
        "loopProgress": progress.model_dump(mode="json"),
    })

    response = _stored_feedback_response(record)

    assert response.loopPolicy is not None
    assert response.loopPolicy.primaryMetric == "method:Expected Calibration Error (ECE)"
    assert response.loopProgress is not None
    assert response.loopProgress.bestFeasibleValue == pytest.approx(0.30)


def test_scifact_competition_endpoint_queues_one_controlled_job(monkeypatch, tmp_path):
    monkeypatch.setattr("app.modules.review.reviews_api._SCIFACT_CASE_ROOT", tmp_path)
    started = {}

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            started.update({"target": target, "args": args, "name": name, "daemon": daemon})

        def start(self):
            started["started"] = True

    monkeypatch.setattr("app.modules.review.reviews_api.threading.Thread", FakeThread)

    response = asyncio.run(start_scifact_competition_case_endpoint(
        RunSciFactCompetitionCaseRequest(reuseLatest=False, bootstrapSamples=500),
    ))

    assert response.status == "queued"
    assert response.stage == "queued"
    assert response.progressPercent == 5
    assert response.bootstrapSamples == 500
    assert started["started"] is True
    assert started["daemon"] is True
    assert (tmp_path / "jobs" / f"{response.jobId}.json").is_file()


def test_scifact_competition_endpoint_reuses_latest_passed_job(monkeypatch, tmp_path):
    monkeypatch.setattr("app.modules.review.reviews_api._SCIFACT_CASE_ROOT", tmp_path)
    monkeypatch.setattr(
        "app.modules.review.reviews_api._register_scifact_human_review",
        lambda _job: None,
    )
    now = "2026-08-25T00:00:00+00:00"
    stored = _write_scifact_job({
        "jobId": "scifact_case_existing",
        "status": "completed",
        "createdAt": now,
        "updatedAt": now,
        "model": "qwen3.7-plus-2026-05-26",
        "bootstrapSamples": 2000,
        "qualityGate": "passed",
        "runId": "scifact_loop_existing",
    })

    response = asyncio.run(start_scifact_competition_case_endpoint(
        RunSciFactCompetitionCaseRequest(),
    ))

    assert response.jobId == stored["jobId"]
    assert response.reused is True


def test_completed_scifact_case_registers_idempotent_common_human_review(monkeypatch, tmp_path):
    monkeypatch.setattr("app.modules.review.reviews_api._SCIFACT_CASE_ROOT", tmp_path)
    dossier, assessment, evidence = _models()
    feedback = review_experiment_feedback(
        dossier,
        evidence,
        execution_assessment=assessment,
    )
    run_id = "scifact_loop_human_review"
    job = _write_scifact_job({
        "jobId": "scifact_case_human_review",
        "status": "completed",
        "createdAt": "2026-08-25T00:00:00+00:00",
        "updatedAt": "2026-08-25T00:00:00+00:00",
        "model": "qwen3.7-plus-2026-05-26",
        "bootstrapSamples": 2000,
        "qualityGate": "passed",
        "runId": run_id,
    })
    output = tmp_path / "runs" / job["jobId"]
    output.mkdir(parents=True)
    (output / "summary.json").write_text(json.dumps({
        "runId": run_id,
        "benchmarkFingerprint": "sha256:fixed-scifact",
    }), encoding="utf-8")
    (output / "reviewx_round_2.json").write_text(json.dumps({
        "qualityAssessment": feedback.qualityAssessment.model_dump(mode="json"),
        "iterationDecision": feedback.iterationDecision.model_dump(mode="json"),
    }, default=str), encoding="utf-8")
    progress = evaluate_experiment_series(
        run_id,
        [_series_record(1, 0.40), _series_record(2, 0.50)],
        _series_policy(minIterations=2, maxIterations=2),
    )
    (output / "experiment_series.json").write_text(
        progress.model_dump_json(indent=2), encoding="utf-8",
    )

    first = _register_scifact_human_review(job)
    second = _register_scifact_human_review(job)

    assert first is not None
    assert second is not None
    assert second["id"] == first["id"]
    assert first["competitionCase"] == "SciFact"
    assert first["benchmarkFingerprint"] == "sha256:fixed-scifact"
    assert "summary.json" in first["sourceArtifacts"]
    response = asyncio.run(get_experiment_feedback_endpoint(first["id"]))
    assert response.feedbackId == first["id"]
    assert response.humanSignoffs["conclusion"]["status"] == "pending"


def test_scifact_public_artifact_endpoint_uses_allowlist(monkeypatch, tmp_path):
    monkeypatch.setattr("app.modules.review.reviews_api._SCIFACT_CASE_ROOT", tmp_path)
    now = "2026-08-25T00:00:00+00:00"
    _write_scifact_job({
        "jobId": "scifact_case_files",
        "status": "completed",
        "createdAt": now,
        "updatedAt": now,
        "model": "qwen3.7-plus-2026-05-26",
        "bootstrapSamples": 2000,
        "qualityGate": "passed",
    })
    output = tmp_path / "runs" / "scifact_case_files"
    output.mkdir(parents=True)
    (output / "summary.json").write_text("{}", encoding="utf-8")
    (output / "evaluation_records.json").write_text("{}", encoding="utf-8")

    response = asyncio.run(get_scifact_competition_artifact_endpoint(
        "scifact_case_files", "summary.json",
    ))
    assert Path(response.path).name == "summary.json"
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(get_scifact_competition_artifact_endpoint(
            "scifact_case_files", "evaluation_records.json",
        ))
    assert exc_info.value.status_code == 404
