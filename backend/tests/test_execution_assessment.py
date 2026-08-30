import json

from fastapi.testclient import TestClient

from app.contracts import ExecutionClass, ExecutionStatus
from app.main import app
from app.modules.code.execution_assessment import assess_candidate_execution, assess_plan_package


def _candidate(*, datasets=None, metrics=None, text="Evaluate an algorithm"):
    return {
        "title": "Test candidate",
        "problem": text,
        "proposedMethod": text,
        "requiredExperiments": [{
            "datasets": datasets or [],
            "metrics": metrics or [],
            "stopConditions": ["Stop after the declared evaluation completes"],
        }],
    }


def test_self_contained_simulation_is_ready():
    result = assess_candidate_execution(
        run_id="run_1",
        question_id="question_1",
        research_question="Can a Monte Carlo simulation estimate this probability?",
        candidate=_candidate(datasets=["synthetic data"], metrics=["absolute error"]),
        inputs={"availableInputs": ["fixed random seed"]},
    )
    assert result.executionClass == ExecutionClass.SIMULATION_READY
    assert result.status == ExecutionStatus.READY
    assert result.validationMetrics == ["absolute error"]


def test_missing_named_dataset_blocks_execution():
    result = assess_candidate_execution(
        run_id="run_2",
        question_id="question_2",
        research_question="Evaluate a classifier on an annotated benchmark corpus.",
        candidate=_candidate(datasets=["PrivateReviewBench"], metrics=["F1"]),
        inputs={"availableInputs": []},
    )
    assert result.executionClass == ExecutionClass.DATA_REQUIRED
    assert result.status == ExecutionStatus.NOT_APPLICABLE
    assert "PrivateReviewBench" in result.missingInputs[0]


def test_versioned_code_project_data_manifest_satisfies_execution_gate(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "manifest.json").write_text(json.dumps({
        "dataset": "SciFact official release",
        "source_uri": "https://example.org/scifact.tar.gz",
        "archive_sha256": "a" * 64,
    }), encoding="utf-8")
    package = {
        "packageId": "ppkg_manifest",
        "idea": {
            "id": "candidate_manifest",
            "title": "Benchmark a Python claim verification pipeline on a dataset",
        },
        "stages": [{
            "id": "stage-1",
            "steps": [{
                "id": "step-1",
                "title": "Run the benchmark",
                "method": "Execute the Python evaluation pipeline on the dataset",
                "expected": [{"metric": "macro F1", "target": ">= baseline"}],
            }],
        }],
    }

    result = assess_plan_package(package, base_dir=str(tmp_path))

    assert result.executionClass == ExecutionClass.COMPUTATIONAL_READY
    assert result.status == ExecutionStatus.READY
    assert result.missingInputs == []
    assert any("SciFact official release" in item for item in result.availableInputs)


def test_unversioned_remote_manifest_does_not_bypass_execution_gate(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "manifest.json").write_text(json.dumps({
        "dataset": "Unverified corpus",
        "source_uri": "https://example.org/corpus.tar.gz",
    }), encoding="utf-8")
    package = {
        "packageId": "ppkg_unverified",
        "idea": {"id": "candidate_unverified", "title": "Evaluate a Python model on a dataset"},
        "stages": [{
            "id": "stage-1",
            "steps": [{
                "id": "step-1",
                "title": "Run evaluation",
                "expected": [{"metric": "F1", "target": "> 0"}],
            }],
        }],
    }

    result = assess_plan_package(package, base_dir=str(tmp_path))

    assert result.executionClass == ExecutionClass.DATA_REQUIRED
    assert result.status == ExecutionStatus.NOT_APPLICABLE


def test_local_synthetic_constraint_overrides_candidate_external_dataset_suggestion():
    result = assess_candidate_execution(
        run_id="run_local_synthetic",
        question_id="question_local_synthetic",
        research_question="Evaluate calibrated unsupported-claim detection.",
        candidate=_candidate(
            datasets=["SciFact", "Custom AI-generated review dataset"],
            metrics=["F1", "expected calibration error"],
            text="Implement a Python algorithm and benchmark pipeline.",
        ),
        inputs={
            "availableInputs": ["locally generated fixed-seed synthetic claim-evidence records"],
            "constraints": ["Use a deterministic fixed-seed synthetic benchmark generated locally"],
        },
    )

    assert result.status == ExecutionStatus.READY
    assert result.executionClass == ExecutionClass.COMPUTATIONAL_READY
    assert result.missingInputs == []
    assert "SciFact" not in result.availableInputs


def test_ethics_and_proof_tasks_are_not_auto_executed():
    ethics = assess_candidate_execution(
        run_id="run_3",
        question_id="question_3",
        research_question="Run a clinical trial involving human participants.",
        candidate=_candidate(metrics=["response rate"]),
    )
    proof = assess_candidate_execution(
        run_id="run_4",
        question_id="question_4",
        research_question="Provide a formal proof of the theorem.",
        candidate=_candidate(metrics=["proof completeness"]),
    )
    assert ethics.executionClass == ExecutionClass.ETHICS_REVIEW_REQUIRED
    assert proof.executionClass == ExecutionClass.PROOF_REQUIRED
    assert ethics.status == proof.status == ExecutionStatus.NOT_APPLICABLE


def test_assessment_api_and_batch_use_same_service():
    client = TestClient(app)
    item = {
        "runId": "run_api",
        "questionId": "question_api",
        "researchQuestion": "Use synthetic data simulation to compare algorithms.",
        "candidate": _candidate(datasets=["synthetic data"], metrics=["accuracy"]),
        "inputs": {"availableInputs": ["fixed seed"]},
    }
    single = client.post("/api/v1/code/execution-assessments", json=item)
    batch = client.post("/api/v1/code/execution-assessments/batch", json={"items": [item]})
    assert single.status_code == 200
    assert batch.status_code == 200
    assert batch.json()[0] == single.json()
