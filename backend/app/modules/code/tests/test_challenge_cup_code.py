from __future__ import annotations

import asyncio
import json
import zipfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from sqlmodel import SQLModel, Session, create_engine

from app.db import crud
from app.models.plan_package import PlanPackage
from app.modules.code import code_bundle_import as bundle_import
from app.modules.code import code_research_api
from app.modules.code import codegen_sessions_api
from app.modules.code.execution_assessment import (
    ExecutionAssessment,
    ExecutionClass,
    ExecutionStatus,
    assess_execution,
    execution_gate,
)
from app.modules.code.experiment_evidence_service import (
    build_experiment_evidence,
    build_experiment_feedback,
    save_evidence,
)
from app.modules.code.tests.execution_class_cases import EXECUTION_CLASS_CASES
from app.services import code_project_service as cps


def _minimal_package() -> PlanPackage:
    return PlanPackage.model_validate({
        "packageId": "ppkg_test_bundle",
        "status": "approved",
        "source": {
            "ideaSessionId": "idea_test_bundle",
            "ideaCandidateId": "cand_test_bundle",
        },
        "idea": {
            "id": "cand_test_bundle",
            "title": "Deterministic benchmark analysis",
            "problem": "Compare a method with a fixed baseline.",
            "hypothesisStatement": "The method improves accuracy.",
            "proposedMethod": "Run a deterministic Python comparison.",
        },
        "background": {"summary": "A small reproducible benchmark."},
        "literatureSurvey": {"summary": "Fixture survey.", "papers": []},
        "gap": {"summary": "The baseline comparison is missing.", "items": [], "selectedGapId": ""},
        "principle": {
            "summary": "Paired deterministic comparison.",
            "mechanism": "Evaluate fixed inputs with a fixed seed.",
        },
        "researchQuestion": "Does the method improve benchmark accuracy?",
        "constants": {"seed": 42},
        "stages": [{
            "id": "stage-1", "order": 1, "title": "Evaluation",
            "goal": "Measure accuracy.", "method": "Python benchmark comparison.",
            "steps": [{
                "id": "step-1", "order": 1, "title": "Run benchmark",
                "desc": "Execute the fixed comparison.", "method": "Python",
                "outputs": [{"type": "code", "name": "metrics.json"}],
                "expected": [{"metric": "accuracy", "target": "> 0.9"}],
                "codeHints": {"stopConditions": ["Stop after one fixed-seed run"]},
            }],
        }],
        "evidenceTrace": {"ideaCandidateId": "cand_test_bundle"},
    })


def _ready_assessment(execution_class: ExecutionClass = ExecutionClass.COMPUTATIONAL_READY) -> ExecutionAssessment:
    return ExecutionAssessment(
        runId="run_test",
        questionId="q_test",
        planPackageId="ppkg_test_bundle",
        executionClass=execution_class,
        feasibilityScore=0.9,
        rationale="Fixed inputs, metrics, environment and stop conditions are available.",
        availableInputs=["input.csv"],
        toolsAndEnvironment=["Python"],
        validationMetrics=["accuracy"],
        stopConditions=["Stop after one fixed-seed run"],
        status=ExecutionStatus.READY,
    )


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_cart(root: Path, package_id: str = "ppkg_test_bundle") -> Path:
    cart = root / "cart_test_bundle"
    (cart / "project").mkdir(parents=True)
    (cart / "project" / "main.py").write_text("print('deterministic')\n", encoding="utf-8")
    (cart / "project" / "requirements.txt").write_text("# standard library only\n", encoding="utf-8")
    (cart / "trace" / "step-1").mkdir(parents=True)
    (cart / "trace" / "step-1" / "stdout.log").write_text("accuracy=0.95\n", encoding="utf-8")
    _write_json(cart / "data" / "manifest.json", {
        "cart_id": cart.name, "package_id": package_id, "seed": 42,
    })
    _write_json(cart / "data" / "step-1" / "metrics.json", {"accuracy": 0.95})
    _write_json(cart / "data" / "step-1" / "result.json", {
        "node_id": "step-1", "success": True,
        "outputs": {"metrics": {"accuracy": 0.95}},
        "artifacts": [{"name": "metrics.json", "path": "data/step-1/metrics.json"}],
        "baseline": "majority-class baseline",
        "node_info": {"method": "Fixed-seed Python comparison"},
    })
    _write_json(cart / "event_log.json", [
        {"event_type": "cart_start", "status": "running"},
        {"event_type": "cart_complete", "status": "success", "message": "complete"},
    ])
    _write_json(cart / "blueprint_state.json", {"step-1": {"status": "success"}})
    _write_json(cart / "cart_results.json", {
        "cart_id": cart.name, "package_id": package_id,
        "overall_status": "success", "total_nodes": 1,
        "succeeded": 1, "failed": 0, "skipped": 0,
        "total_duration_ms": 10,
        "proposed_method": "Fixed-seed Python comparison",
    })
    return cart


def test_seven_execution_classes_are_explainable():
    assert len(EXECUTION_CLASS_CASES) == 7
    for fixture in EXECUTION_CLASS_CASES:
        assessment = assess_execution(fixture["source"])
        assert assessment.executionClass.value == fixture["expected"]
        assert assessment.rationale
        assert execution_gate(assessment).allowed == (
            fixture["expected"] in {"computational_ready", "simulation_ready"}
        )


def test_hard_ethics_signal_overrides_optimistic_upstream_class():
    assessment = assess_execution({
        "runId": "run_ethics_override",
        "questionId": "q_ethics_override",
        "executionClass": "computational_ready",
        "researchQuestion": "Run a clinical trial on patient personal data with informed consent.",
        "availableInputs": ["patient dataset"],
        "researchPlan": {"steps": [{
            "id": "step-1",
            "metrics": ["auc"],
            "stopConditions": ["Stop after one run"],
        }]},
    })

    assert assessment.executionClass == ExecutionClass.ETHICS_REVIEW_REQUIRED
    assert assessment.status == ExecutionStatus.NOT_APPLICABLE
    assert execution_gate(assessment).allowed is False
    assert any("overridden" in warning for warning in assessment.warnings)


def test_planpackage_codegen_context_uses_current_package(monkeypatch):
    package = _minimal_package()

    class Storage:
        def get(self, package_id):
            return package if package_id == package.packageId else None

        def get_by_idea_session(self, idea_session_id):
            return package if idea_session_id == package.source.ideaSessionId else None

    monkeypatch.setattr(codegen_sessions_api, "get_plan_package_storage", lambda: Storage())
    context = codegen_sessions_api._resolve_plan_context(
        codegen_sessions_api.CreateSessionRequest(planLinkId=package.packageId)
    )
    assert context["contextSource"] == "plan_package"
    assert context["planPackageId"] == package.packageId
    assert context["planSessionId"] == package.source.ideaSessionId
    assert context["candidateId"] == package.source.ideaCandidateId
    assert context["title"] == package.idea.title
    assert context["research_question"] == package.researchQuestion
    assert context["method"] == package.principle.mechanism


def test_codegen_validation_reads_complete_persisted_file_index(monkeypatch):
    session = SimpleNamespace(status="completed", projectId="cproj_validation")
    records = [
        SimpleNamespace(path="README.md", is_dir=False),
        SimpleNamespace(path="docs/method.md", is_dir=False),
        SimpleNamespace(path="tests/test_pipeline.py", is_dir=False),
        SimpleNamespace(path=".github/workflows/ci.yml", is_dir=False),
        SimpleNamespace(path="src/model.py", is_dir=False),
    ]
    records.extend(
        SimpleNamespace(path=f"src/components/component_{index}.py", is_dir=False)
        for index in range(35)
    )

    @contextmanager
    def fake_session_context():
        yield object()

    monkeypatch.setattr(codegen_sessions_api, "get_session", lambda _session_id: session)
    monkeypatch.setattr(codegen_sessions_api, "get_session_context", fake_session_context)
    monkeypatch.setattr(
        codegen_sessions_api.crud,
        "list_project_files",
        lambda _db, project_id: records if project_id == session.projectId else [],
    )

    result = asyncio.run(codegen_sessions_api.validate_codegen_repo("cgs_validation"))

    assert result["fileCount"] == 40
    assert result["qualityScore"] == 100
    assert result["passed"] is True
    assert result["categories"] == {
        "tests": True,
        "ci": True,
        "db": True,
        "docs": 2,
    }


def test_evidence_requires_existing_reproducibility_artifacts(tmp_path):
    cart = _build_cart(tmp_path)
    first = build_experiment_evidence(cart, _ready_assessment())
    second = build_experiment_evidence(cart, _ready_assessment())
    assert first.status == ExecutionStatus.EXECUTED
    assert first.codeHash == second.codeHash
    assert first.environmentHash == second.environmentHash
    assert first.dataHashes == second.dataHashes
    assert first.metrics[0].name == "accuracy"
    assert first.logRefs

    (cart / "data" / "step-1" / "metrics.json").unlink()
    after_delete = build_experiment_evidence(cart, _ready_assessment())
    assert after_delete.status == ExecutionStatus.FAILED
    assert any("missing" in failure.lower() for failure in after_delete.failures)


def test_protocol_only_never_becomes_executed(tmp_path):
    cart = _build_cart(tmp_path)
    protocol = _ready_assessment().model_copy(update={
        "executionClass": ExecutionClass.PROTOCOL_ONLY,
        "status": ExecutionStatus.NOT_APPLICABLE,
        "missingInputs": ["machine-executable protocol"],
    })
    evidence = build_experiment_evidence(cart, protocol)
    assert evidence.status == ExecutionStatus.NOT_APPLICABLE
    assert not evidence.codeHash
    assert evidence.failures


def test_non_ready_assessment_never_becomes_executed(tmp_path):
    cart = _build_cart(tmp_path)
    blocked = _ready_assessment().model_copy(update={
        "status": ExecutionStatus.NOT_APPLICABLE,
        "missingInputs": ["ethics approval"],
    })

    evidence = build_experiment_evidence(cart, blocked, supported_claims=["candidate claim"])

    assert evidence.status == ExecutionStatus.NOT_APPLICABLE
    assert evidence.supportedClaims == []
    assert evidence.unsupportedClaims == ["candidate claim"]


def test_invalid_metrics_and_missing_baseline_fail_evidence(tmp_path):
    cart = _build_cart(tmp_path)
    result_path = cart / "data" / "step-1" / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["outputs"]["metrics"]["accuracy"] = "excellent"
    result.pop("baseline")
    _write_json(result_path, result)

    evidence = build_experiment_evidence(
        cart,
        _ready_assessment(),
        supported_claims=["the method works"],
    )

    assert evidence.status == ExecutionStatus.FAILED
    assert evidence.supportedClaims == []
    assert evidence.unsupportedClaims == ["the method works"]
    assert any("finite numeric" in failure for failure in evidence.failures)
    assert any("baseline" in failure.lower() for failure in evidence.failures)


def test_metric_feedback_requests_targeted_plan_revision(tmp_path):
    cart = _build_cart(tmp_path)
    result_path = cart / "data" / "step-1" / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["outputs"]["metrics"]["accuracy"] = 0.8
    _write_json(result_path, result)
    evidence = build_experiment_evidence(cart, _ready_assessment())
    feedback = build_experiment_feedback(evidence, _minimal_package())
    assert any("did not satisfy" in item for item in feedback.anomalies)
    assert any(item.action == "revise_parameters_or_hypothesis" for item in feedback.planAdjustments)


def test_bundle_import_registers_project_and_cart(tmp_path, monkeypatch):
    package = _minimal_package()
    bundle = tmp_path / "bundle"
    package_path = bundle / "idea" / "plan_packages" / f"{package.packageId}.json"
    _write_json(package_path, package.model_dump(mode="json"))
    source_cart = _build_cart(bundle / "code" / "cart_artifacts", package.packageId)

    data_root = tmp_path / "runtime_data"
    monkeypatch.setattr(bundle_import, "_DATA_DIR", str(data_root))
    monkeypatch.setattr(cps, "CODE_PROJECTS_DIR", str(data_root / "code_projects"))
    monkeypatch.setattr(bundle_import, "_register_package", lambda value: None)

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        imported = bundle_import.import_bundle(bundle, session)
        project = crud.get_project_v2(session, imported.project_id)
        indexed = crud.list_project_files(session, imported.project_id)

    assert project is not None
    assert project.source_idea_session_id == package.source.ideaSessionId
    assert any(item.path == "main.py" for item in indexed)
    manifest_path = data_root / "cart_artifacts" / imported.cart_id / "data" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["project_id"] == imported.project_id
    assert manifest["package_id"] == package.packageId


def test_bundle_import_handles_zip_directory_entries_without_trailing_slash(tmp_path, monkeypatch):
    package = _minimal_package()
    bundle = tmp_path / "bundle"
    package_path = bundle / "idea" / "plan_packages" / f"{package.packageId}.json"
    _write_json(package_path, package.model_dump(mode="json"))
    _build_cart(bundle / "code" / "cart_artifacts", package.packageId)

    archive_path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("sample", b"")
        archive.writestr("sample/code", b"")
        for path in sorted(item for item in bundle.rglob("*") if item.is_file()):
            archive.write(path, "sample/" + path.relative_to(bundle).as_posix())

    data_root = tmp_path / "runtime_data"
    monkeypatch.setattr(bundle_import, "_DATA_DIR", str(data_root))
    monkeypatch.setattr(cps, "CODE_PROJECTS_DIR", str(data_root / "code_projects"))
    monkeypatch.setattr(bundle_import, "_register_package", lambda value: None)

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        imported = bundle_import.import_bundle(archive_path, session)
        indexed = crud.list_project_files(session, imported.project_id)

    assert any(item.path == "main.py" for item in indexed)


def test_bundle_source_rejects_paths_outside_allowed_root(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.zip"
    outside.write_bytes(b"not a zip")
    with pytest.raises(bundle_import.BundleImportError):
        bundle_import.resolve_bundle_source(str(outside), allowed_roots=[allowed])


def test_bundle_import_rejects_non_object_cart_manifest(tmp_path):
    package = _minimal_package()
    bundle = tmp_path / "bundle"
    package_path = bundle / "idea" / "plan_packages" / f"{package.packageId}.json"
    _write_json(package_path, package.model_dump(mode="json"))
    cart = _build_cart(bundle / "code" / "cart_artifacts", package.packageId)
    _write_json(cart / "data" / "manifest.json", [])

    with pytest.raises(bundle_import.BundleImportError, match="manifest must be a JSON object"):
        bundle_import.inspect_bundle(bundle)


def test_cart_assessment_must_match_manifest_package(tmp_path):
    cart = _build_cart(tmp_path)
    mismatch = _ready_assessment().model_copy(update={"planPackageId": "ppkg_other"})

    with pytest.raises(HTTPException, match="does not match"):
        code_research_api._validate_cart_assessment(cart.name, cart, mismatch)


def test_feedback_requires_the_persisted_cart_evidence(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    cart = _build_cart(data_root / "cart_artifacts")
    monkeypatch.setattr(code_research_api, "_DATA_DIR", str(data_root))
    evidence = build_experiment_evidence(cart, _ready_assessment(), code_run_id=cart.name)
    save_evidence(cart / "data" / "experiment_evidence.json", evidence)
    forged = evidence.model_copy(update={"supportedClaims": ["forged claim"]})

    with pytest.raises(HTTPException, match="differs from persisted"):
        asyncio.run(code_research_api.create_experiment_feedback(
            cart.name,
            code_research_api.FeedbackRequest(evidence=forged),
        ))


def test_assessment_base_dir_is_limited_to_managed_data(tmp_path, monkeypatch):
    managed = tmp_path / "managed"
    managed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setattr(code_research_api, "_DATA_DIR", str(managed))

    assert code_research_api._resolve_assessment_base_dir(str(managed)) == str(managed.resolve())
    with pytest.raises(HTTPException, match="managed backend data"):
        code_research_api._resolve_assessment_base_dir(str(outside))


def test_remote_bundle_upload_requires_configured_token(monkeypatch):
    monkeypatch.delenv("FAROS_BUNDLE_UPLOAD_TOKEN", raising=False)
    remote = Request({"type": "http", "client": ("203.0.113.7", 1234), "headers": []})
    local = Request({"type": "http", "client": ("127.0.0.1", 1234), "headers": []})
    proxied_remote = Request({
        "type": "http",
        "client": ("127.0.0.1", 1234),
        "headers": [(b"x-forwarded-for", b"203.0.113.7")],
    })

    with pytest.raises(HTTPException, match="Remote bundle upload is disabled"):
        code_research_api._authorize_bundle_upload(remote, None)
    with pytest.raises(HTTPException, match="Remote bundle upload is disabled"):
        code_research_api._authorize_bundle_upload(proxied_remote, None)
    code_research_api._authorize_bundle_upload(local, None)


def test_malformed_node_result_downgrades_evidence_instead_of_crashing(tmp_path):
    cart = _build_cart(tmp_path)
    _write_json(cart / "data" / "step-1" / "result.json", [])

    evidence = build_experiment_evidence(cart, _ready_assessment())

    assert evidence.status == ExecutionStatus.FAILED
    assert any("must be a JSON object" in failure for failure in evidence.failures)
