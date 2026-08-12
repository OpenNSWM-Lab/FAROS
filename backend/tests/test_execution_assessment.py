from fastapi.testclient import TestClient

from app.contracts import ExecutionClass, ExecutionStatus
from app.main import app
from app.modules.code.execution_assessment import assess_candidate_execution


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
