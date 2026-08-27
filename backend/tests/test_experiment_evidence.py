import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.code.sandbox.subprocess_backend import SubprocessSandbox
from app.contracts import ExecutionStatus
from app.faros.capabilities.adapters.experiment import ExperimentCapability
from app.services.code_repair_service import CodeRepairService
from app.faros.capabilities.adapters.paper_drafting import PaperDraftingCapability
from app.services.experiment_evidence_service import build_experiment_evidence
from app.services.experiment_benchmark_service import inherit_frozen_benchmark
from app.services.code_agent_service import (
    SCIENTIFIC_ENTRYPOINT_PROMPT,
    _scientific_entrypoint_issues,
    _step_synthesize_scientific_entrypoint,
)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (repo / "requirements.txt").write_text("numpy\n", encoding="utf-8")
    return repo


def _write_evaluation_records(
    repo: Path, *, positive_class: str = "unsupported", include_baseline_probabilities: bool = True,
) -> None:
    baseline_probabilities = [0.8, 0.7, 0.6, 0.2]
    method_probabilities = [0.9, 0.8, 0.2, 0.1]
    labels = [1, 1, 0, 0]
    baseline_predictions = [1, 1, 1, 0]
    method_predictions = [1, 1, 0, 0]
    records = []
    for index, label in enumerate(labels):
        baseline = {"label": baseline_predictions[index]}
        if include_baseline_probabilities:
            baseline["probability"] = baseline_probabilities[index]
        records.append({
            "sample_id": f"sample_{index}",
            "split": "test",
            "label": label,
            "predictions": {
                "baseline": baseline,
                "method": {
                    "label": method_predictions[index],
                    "probability": method_probabilities[index],
                },
            },
        })
    benchmark = {
        "schema_version": "faros-benchmark/v1",
        "benchmark_id": "benchmark_test_v1",
        "task": "unsupported_claim_detection",
        "positive_label": 1,
        "positive_class": positive_class,
        "seed": 42,
        "generator_version": "test-fixture/v1",
        "feature_schema": [
            {"name": "consistency", "type": "float", "direction": "higher_is_supported"}
        ],
        "records": [
            {
                "sample_id": f"sample_{index}",
                "split": "test",
                "features": [round(1.0 - method_probabilities[index], 4)],
                "label": label,
            }
            for index, label in enumerate(labels)
        ],
    }
    (repo / "data").mkdir(parents=True, exist_ok=True)
    (repo / "data" / "frozen_benchmark.json").write_text(
        json.dumps(benchmark), encoding="utf-8"
    )
    (repo / "evaluation_records.json").write_text(json.dumps({
        "schema_version": "faros-evaluation/v1",
        "positive_label": 1,
        "positive_class": positive_class,
        "records": records,
    }), encoding="utf-8")


def _classification_metrics(*, method_f1: float = 1.0) -> list[dict]:
    return [
        {"name": "baseline_precision", "value": 2 / 3, "unit": "ratio", "definition": "Positive predictive value.", "split": "test"},
        {"name": "baseline_recall", "value": 1.0, "unit": "ratio", "definition": "Positive-class recall.", "split": "test"},
        {"name": "baseline_f1_score", "value": 0.8, "unit": "ratio", "definition": "Harmonic mean of precision and recall.", "split": "test"},
        {"name": "baseline_unsupported_claim_rate", "value": 0.75, "unit": "ratio", "definition": "Predicted unsupported fraction.", "split": "test"},
        {"name": "baseline_expected_calibration_error", "value": 0.325, "unit": "ratio", "definition": "Positive-class calibration gap.", "split": "test"},
        {"name": "method_precision", "value": 1.0, "unit": "ratio", "definition": "Positive predictive value.", "split": "test"},
        {"name": "method_recall", "value": 1.0, "unit": "ratio", "definition": "Positive-class recall.", "split": "test"},
        {"name": "method_f1_score", "value": method_f1, "unit": "ratio", "definition": "Harmonic mean of precision and recall.", "split": "test"},
        {"name": "method_unsupported_claim_rate", "value": 0.5, "unit": "ratio", "definition": "Predicted unsupported fraction.", "split": "test"},
        {"name": "method_expected_calibration_error", "value": 0.15, "unit": "ratio", "definition": "Positive-class calibration gap.", "split": "test"},
    ]


def test_metric_audit_uses_split_for_generic_metric_names(tmp_path: Path):
    repo = _repo(tmp_path)
    _write_evaluation_records(repo)
    metrics = [
        {"name": "F1-Score", "value": 0.8, "unit": "score", "definition": "Baseline F1.", "split": "test_baseline"},
        {"name": "F1-Score", "value": 1.0, "unit": "score", "definition": "Method F1.", "split": "test_method"},
        {"name": "Expected Calibration Error (ECE)", "value": 0.325, "unit": "score", "definition": "Baseline ECE.", "split": "test_baseline"},
        {"name": "Expected Calibration Error (ECE)", "value": 0.15, "unit": "score", "definition": "Method ECE.", "split": "test_method"},
        {"name": "Brier Score", "value": 0.1325, "unit": "score", "definition": "Baseline Brier score.", "split": "test_baseline"},
        {"name": "Brier Score", "value": 0.025, "unit": "score", "definition": "Method Brier score.", "split": "test_method"},
        {"name": "AUROC", "value": 1.0, "unit": "score", "definition": "Baseline AUROC.", "split": "test_baseline"},
        {"name": "AUROC", "value": 1.0, "unit": "score", "definition": "Method AUROC.", "split": "test_method"},
    ]

    evidence = _build_classification_evidence(repo, metrics)

    assert evidence.status == ExecutionStatus.EXECUTED
    assert evidence.metricAudit["status"] == "passed"
    assert evidence.metricAudit["recomputedMetrics"]["baseline_f1_score"] == pytest.approx(0.8)
    assert evidence.metricAudit["recomputedMetrics"]["method_brier_score"] == pytest.approx(0.025)


def _build_classification_evidence(
    repo: Path, metrics: list[dict], *, require_ablation: bool = False,
):
    return build_experiment_evidence(
        repo_dir=repo,
        run_id="run_classification",
        question_id="question_classification",
        code_run_id="code_classification",
        method="Calibrated unsupported-claim detector.",
        baseline="Uncalibrated detector.",
        metrics=metrics,
        execution_result={"command": "python src/main.py", "exit_code": 0},
        require_ablation=require_ablation,
    )


def test_evidence_requires_scientific_metrics(tmp_path: Path):
    evidence = build_experiment_evidence(
        repo_dir=_repo(tmp_path),
        run_id="run_empty",
        question_id="question_empty",
        code_run_id="code_empty",
        method="Paired evaluation.",
        baseline="No-filter baseline.",
        metrics=[],
        execution_result={"command": "python src/main.py", "exit_code": 0, "duration_seconds": 0.1},
        expected_claims=["claim under test"],
    )
    assert evidence.status == ExecutionStatus.FAILED
    assert any("no scientific metric" in item.lower() for item in evidence.failures)
    assert evidence.unsupportedClaims == ["claim under test"]


def test_experiment_adapter_bridges_idea_candidate_without_legacy_plan_arguments(monkeypatch):
    calls = []

    def fake_generate(candidate, **kwargs):
        calls.append((candidate, kwargs))
        return kwargs["existing_project_id"]

    monkeypatch.setattr(
        "app.services.code_agent_service.generate_project_from_research_candidate",
        fake_generate,
    )
    ok = ExperimentCapability()._generate_code_via_agent(
        project_id="project_1",
        idea_session_id="idea_1",
        selected_candidate={"id": "candidate_1", "title": "Candidate"},
        language="python",
        framework="numpy",
        provider_name="qwen",
        model="qwen-plus",
    )
    assert ok is True
    assert calls[0][1] == {
        "idea_session_id": "idea_1",
        "provider_name": "qwen",
        "model": "qwen-plus",
        "language": "python",
        "framework": "numpy",
        "existing_project_id": "project_1",
    }


def test_evidence_persists_hashes_and_metric_provenance(tmp_path: Path):
    repo = _repo(tmp_path)
    evidence = build_experiment_evidence(
        repo_dir=repo,
        run_id="run_ok",
        question_id="question_ok",
        code_run_id="code_ok",
        method="Paired evaluation with a fixed seed.",
        baseline="No-filter baseline.",
        metrics=[{
            "name": "unsupported_claim_rate",
            "value": 0.12,
            "unit": "ratio",
            "definition": "Unsupported factual claims divided by factual claims.",
            "split": "synthetic-test",
        }],
        execution_result={
            "command": "python src/main.py",
            "exit_code": 0,
            "duration_seconds": 0.2,
            "stdout": "ok\n",
            "stderr": "",
        },
    )
    assert evidence.status == ExecutionStatus.EXECUTED
    assert evidence.codeHash.startswith("sha256:")
    assert evidence.environmentHash.startswith("sha256:")
    assert evidence.metrics[0].sourcePath == "metrics.json"
    assert (repo / "artifacts/evidence/experiment_evidence.json").is_file()
    assert (repo / "artifacts/evidence/artifact_hashes.json").is_file()


def test_classification_metric_audit_recomputes_aggregate_metrics(tmp_path: Path):
    repo = _repo(tmp_path)
    _write_evaluation_records(repo)

    evidence = _build_classification_evidence(repo, _classification_metrics())

    assert evidence.status == ExecutionStatus.EXECUTED
    assert evidence.metricAudit["status"] == "passed"
    assert evidence.metricAudit["positiveClass"] == "unsupported"
    assert evidence.metricAudit["recomputedMetrics"]["baseline_f1_score"] == pytest.approx(0.8)
    assert evidence.dataHashes["evaluation_inputs"].startswith("sha256:")
    assert evidence.dataHashes["frozen_benchmark"].startswith("sha256:")
    assert evidence.metricAudit["benchmarkAlignment"]["status"] == "passed"
    assert any(ref.uri == "evaluation_records.json" for ref in evidence.artifactRefs)
    assert any(ref.uri == "data/frozen_benchmark.json" for ref in evidence.artifactRefs)


def test_classification_metric_audit_supports_per_method_decision_thresholds(tmp_path: Path):
    repo = _repo(tmp_path)
    _write_evaluation_records(repo)
    records_path = repo / "evaluation_records.json"
    payload = json.loads(records_path.read_text(encoding="utf-8"))
    payload["decision_threshold"] = 0.5
    payload["decision_thresholds"] = {"baseline": 0.5, "method": 0.85}
    payload["records"][1]["predictions"]["method"]["label"] = 0
    records_path.write_text(json.dumps(payload), encoding="utf-8")
    metrics = _classification_metrics(method_f1=2 / 3)
    for metric in metrics:
        if metric["name"] == "method_recall":
            metric["value"] = 0.5
        elif metric["name"] == "method_unsupported_claim_rate":
            metric["value"] = 0.25

    evidence = _build_classification_evidence(repo, metrics)

    assert evidence.status == ExecutionStatus.EXECUTED
    assert evidence.metricAudit["status"] == "passed"
    assert evidence.metricAudit["decisionThresholds"] == {
        "baseline": 0.5,
        "method": 0.85,
    }


def test_inherited_benchmark_iteration_requires_named_ablation(tmp_path: Path):
    repo = _repo(tmp_path)
    _write_evaluation_records(repo)

    evidence = _build_classification_evidence(
        repo,
        _classification_metrics(),
        require_ablation=True,
    )

    assert evidence.status == ExecutionStatus.FAILED
    assert evidence.metricAudit["ablationAudit"] == {
        "required": True,
        "status": "failed",
        "variants": [],
    }
    assert any("ablation_<component>" in failure for failure in evidence.failures)


def test_inherited_benchmark_iteration_accepts_auditable_ablation(tmp_path: Path):
    repo = _repo(tmp_path)
    _write_evaluation_records(repo)
    records_path = repo / "evaluation_records.json"
    payload = json.loads(records_path.read_text(encoding="utf-8"))
    for record in payload["records"]:
        record["predictions"]["ablation_no_factorization"] = dict(
            record["predictions"]["method"]
        )
    records_path.write_text(json.dumps(payload), encoding="utf-8")
    ablation_metrics = [
        {**metric, "name": metric["name"].replace("method_", "ablation_no_factorization_")}
        for metric in _classification_metrics()
        if metric["name"].startswith("method_")
    ]

    evidence = _build_classification_evidence(
        repo,
        _classification_metrics() + ablation_metrics,
        require_ablation=True,
    )

    assert evidence.status == ExecutionStatus.EXECUTED
    assert evidence.metricAudit["ablationAudit"]["status"] == "passed"
    assert evidence.metricAudit["ablationAudit"]["variants"] == [
        "ablation_no_factorization"
    ]


def test_scientific_iteration_prompt_requires_controlled_ablation():
    assert "substantive method revision" in SCIENTIFIC_ENTRYPOINT_PROMPT
    assert '"baseline", "method", and "ablation_<component>"' in SCIENTIFIC_ENTRYPOINT_PROMPT
    assert "do not fabricate seed variation" in SCIENTIFIC_ENTRYPOINT_PROMPT


def test_frozen_benchmark_fingerprint_ignores_prediction_changes(tmp_path: Path):
    repo = _repo(tmp_path)
    _write_evaluation_records(repo)
    first = _build_classification_evidence(repo, _classification_metrics())

    records_path = repo / "evaluation_records.json"
    payload = json.loads(records_path.read_text(encoding="utf-8"))
    payload["records"][0]["predictions"]["method"]["probability"] = 0.95
    records_path.write_text(json.dumps(payload), encoding="utf-8")
    second = _build_classification_evidence(repo, _classification_metrics())

    assert first.dataHashes["frozen_benchmark"] == second.dataHashes["frozen_benchmark"]
    assert first.dataHashes["evaluation_inputs"] == second.dataHashes["evaluation_inputs"]


def test_frozen_benchmark_fingerprint_changes_with_features(tmp_path: Path):
    repo = _repo(tmp_path)
    _write_evaluation_records(repo)
    first = _build_classification_evidence(repo, _classification_metrics())

    benchmark_path = repo / "data" / "frozen_benchmark.json"
    payload = json.loads(benchmark_path.read_text(encoding="utf-8"))
    payload["records"][0]["features"] = [0.123456]
    benchmark_path.write_text(json.dumps(payload), encoding="utf-8")
    second = _build_classification_evidence(repo, _classification_metrics())

    assert first.dataHashes["frozen_benchmark"] != second.dataHashes["frozen_benchmark"]


def test_frozen_benchmark_accepts_named_feature_schema(tmp_path: Path):
    repo = _repo(tmp_path)
    _write_evaluation_records(repo)
    benchmark_path = repo / "data" / "frozen_benchmark.json"
    payload = json.loads(benchmark_path.read_text(encoding="utf-8"))
    payload["feature_schema"] = {"consistency": "float[0,1]"}
    benchmark_path.write_text(json.dumps(payload), encoding="utf-8")

    evidence = _build_classification_evidence(repo, _classification_metrics())

    assert evidence.status == ExecutionStatus.EXECUTED
    assert evidence.metricAudit["benchmarkAudit"]["status"] == "passed"


def test_frozen_benchmark_is_copied_only_when_fingerprint_matches(tmp_path: Path):
    source = _repo(tmp_path / "source")
    target = _repo(tmp_path / "target")
    _write_evaluation_records(source)
    evidence = _build_classification_evidence(source, _classification_metrics())

    inherited = inherit_frozen_benchmark(
        source_repo_dir=source,
        target_repo_dir=target,
        expected_fingerprint=evidence.dataHashes["frozen_benchmark"],
    )
    rejected = inherit_frozen_benchmark(
        source_repo_dir=source,
        target_repo_dir=tmp_path / "rejected",
        expected_fingerprint="sha256:not-the-parent-benchmark",
    )

    assert inherited["status"] == "passed"
    assert inherited["inherited"] is True
    assert (target / "data" / "frozen_benchmark.json").is_file()
    assert rejected["status"] == "failed"
    assert not (tmp_path / "rejected" / "data" / "frozen_benchmark.json").exists()


def test_classification_metric_audit_requires_frozen_benchmark(tmp_path: Path):
    repo = _repo(tmp_path)
    _write_evaluation_records(repo)
    (repo / "data" / "frozen_benchmark.json").unlink()

    evidence = _build_classification_evidence(repo, _classification_metrics())

    assert evidence.status == ExecutionStatus.FAILED
    assert any("frozen_benchmark.json" in failure for failure in evidence.failures)


def test_classification_metric_audit_rejects_likely_probability_inversion(tmp_path: Path):
    repo = _repo(tmp_path)
    labels = [0, 1] * 10
    probabilities = [0.9 if label == 0 else 0.1 for label in labels]
    (repo / "data").mkdir(parents=True, exist_ok=True)
    (repo / "data" / "frozen_benchmark.json").write_text(json.dumps({
        "schema_version": "faros-benchmark/v1",
        "benchmark_id": "polarity_test_v1",
        "task": "unsupported_claim_detection",
        "positive_label": 1,
        "positive_class": "unsupported",
        "seed": 42,
        "generator_version": "test-fixture/v1",
        "feature_schema": [{"name": "consistency", "type": "float"}],
        "records": [
            {"sample_id": f"sample_{index}", "split": "test", "features": [probability], "label": label}
            for index, (label, probability) in enumerate(zip(labels, probabilities))
        ],
    }), encoding="utf-8")
    (repo / "evaluation_records.json").write_text(json.dumps({
        "schema_version": "faros-evaluation/v1",
        "positive_label": 1,
        "positive_class": "unsupported",
        "decision_threshold": 0.5,
        "records": [
            {
                "sample_id": f"sample_{index}",
                "split": "test",
                "label": label,
                "predictions": {"baseline": {"label": int(probability >= 0.5), "probability": probability}},
            }
            for index, (label, probability) in enumerate(zip(labels, probabilities))
        ],
    }), encoding="utf-8")

    evidence = _build_classification_evidence(repo, [{
        "name": "baseline_auroc",
        "value": 0.0,
        "unit": "score",
        "definition": "Area under the ROC curve.",
        "split": "test",
    }])

    assert evidence.status == ExecutionStatus.FAILED
    assert evidence.metricAudit["polarityDiagnostics"]["baseline"]["suspectedInversion"] is True
    assert any("likely reversed" in failure for failure in evidence.failures)


def test_classification_metric_audit_accepts_prediction_label_alias(tmp_path: Path):
    repo = _repo(tmp_path)
    _write_evaluation_records(repo)
    records_path = repo / "evaluation_records.json"
    payload = json.loads(records_path.read_text(encoding="utf-8"))
    for record in payload["records"]:
        for prediction in record["predictions"].values():
            prediction["predicted_label"] = prediction.pop("label")
    records_path.write_text(json.dumps(payload), encoding="utf-8")

    evidence = _build_classification_evidence(repo, _classification_metrics())

    assert evidence.status == ExecutionStatus.EXECUTED
    assert evidence.metricAudit["status"] == "passed"


def test_classification_metric_audit_rejects_wrong_positive_class(tmp_path: Path):
    repo = _repo(tmp_path)
    _write_evaluation_records(repo, positive_class="supported")

    evidence = _build_classification_evidence(repo, _classification_metrics())

    assert evidence.status == ExecutionStatus.FAILED
    assert any("label polarity" in failure for failure in evidence.failures)


def test_classification_metric_audit_rejects_ece_without_probabilities(tmp_path: Path):
    repo = _repo(tmp_path)
    _write_evaluation_records(repo, include_baseline_probabilities=False)

    evidence = _build_classification_evidence(repo, _classification_metrics())

    assert evidence.status == ExecutionStatus.FAILED
    assert any("baseline_expected_calibration_error" in failure for failure in evidence.failures)


def test_classification_metric_audit_rejects_reported_metric_mismatch(tmp_path: Path):
    repo = _repo(tmp_path)
    _write_evaluation_records(repo)

    evidence = _build_classification_evidence(repo, _classification_metrics(method_f1=0.42))

    assert evidence.status == ExecutionStatus.FAILED
    assert any("FAROS recomputed" in failure for failure in evidence.failures)


def test_paper_gate_rejects_failed_evidence(tmp_path: Path):
    evidence = build_experiment_evidence(
        repo_dir=_repo(tmp_path),
        run_id="run_failed",
        question_id="question_failed",
        code_run_id="code_failed",
        method="Method",
        baseline="Baseline",
        metrics=[],
        execution_result={"command": "python src/main.py", "exit_code": 0},
    )
    with pytest.raises(ValueError, match="Paper drafting is blocked"):
        PaperDraftingCapability._require_experiment_evidence({
            "experimentEvidence": evidence.model_dump(mode="json")
        })


def test_subprocess_sandbox_syncs_metrics_but_not_source_edits(tmp_path: Path):
    repo = _repo(tmp_path)

    async def run():
        runner = repo / "run_outputs.py"
        runner.write_text(
            "import json\n"
            "from pathlib import Path\n"
            "Path('metrics.json').write_text(json.dumps([{'name': 'accuracy', 'value': 0.8, "
            "'definition': 'Correct predictions divided by predictions.', 'split': 'test'}]))\n"
            "Path('data').mkdir(exist_ok=True)\n"
            "Path('data/frozen_benchmark.json').write_text(json.dumps({'schema_version': 'faros-benchmark/v1'}))\n"
            "Path('src/main.py').write_text('changed')\n",
            encoding="utf-8",
        )
        sandbox = SubprocessSandbox()
        sandbox_id = await sandbox.setup(str(repo))
        result = await sandbox.execute(sandbox_id, "python run_outputs.py")
        await sandbox.teardown(sandbox_id)
        return result

    result = asyncio.run(run())
    assert result.exit_code == 0
    assert (repo / "metrics.json").is_file()
    assert json.loads((repo / "metrics.json").read_text())[0]["name"] == "accuracy"
    assert (repo / "data" / "frozen_benchmark.json").is_file()
    assert (repo / "src/main.py").read_text() == "print('ok')\n"


def test_experiment_executor_uses_module_entrypoint_for_src_package(tmp_path: Path, monkeypatch):
    project_id = "project_package_entrypoint"
    repo = tmp_path / "code_projects" / project_id / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "helper.py").write_text("VALUE = 'ok'\n", encoding="utf-8")
    (repo / "src" / "main.py").write_text(
        "from src.helper import VALUE\nprint(VALUE)\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        "app.faros.capabilities.adapters.experiment._DATA_DIR", str(tmp_path)
    )

    result = ExperimentCapability()._execute_project(
        project_id, "python", use_sandbox=False
    )

    assert result["exit_code"] == 0
    assert result["stdout"].strip() == "ok"
    assert "-m src.main" in result["command"]


def test_experiment_executor_keeps_sandbox_inside_running_event_loop(tmp_path: Path, monkeypatch):
    project_id = "project_async_sandbox"
    repo = tmp_path / "code_projects" / project_id / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(
        "app.faros.capabilities.adapters.experiment._DATA_DIR", str(tmp_path)
    )
    capability = ExperimentCapability()

    async def fake_sandbox(project_id_arg, repo_dir_arg, command):
        return {
            "exit_code": 0,
            "stdout": "sandbox\n",
            "stderr": "",
            "duration_seconds": 0.01,
            "command": command,
        }

    def reject_direct(*args, **kwargs):
        raise AssertionError("direct execution must not replace sandbox execution")

    monkeypatch.setattr(capability, "_execute_in_sandbox", fake_sandbox)
    monkeypatch.setattr(capability, "_execute_direct", reject_direct)

    async def invoke():
        return capability._execute_project(project_id, "python", use_sandbox=True)

    result = asyncio.run(invoke())
    assert result["stdout"].strip() == "sandbox"


def test_code_repair_maps_sandbox_traceback_and_repairs_numpy_trapz(tmp_path: Path):
    repo = tmp_path / "repo"
    source = repo / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import numpy as np\nprint(np.trapz([1.0, 2.0]))\n",
        encoding="utf-8",
    )
    stderr = (
        'Traceback (most recent call last):\n'
        f'  File "{repo.parent}/.sandbox_123/src/main.py", line 2, in <module>\n'
        "AttributeError: module 'numpy' has no attribute 'trapz'\n"
    )

    report = CodeRepairService().auto_fix(
        project_id="project_numpy_compat",
        repo_dir=str(repo),
        failed_steps=[{"name": "experiment", "stderr": stderr, "stdout": ""}],
    )

    assert len([fix for fix in report.fixes_applied if fix.applied]) == 1
    assert "np.trapezoid" in source.read_text(encoding="utf-8")


def test_experiment_execution_retries_once_after_applied_repair(monkeypatch):
    capability = ExperimentCapability()
    executions = [
        {
            "exit_code": 1,
            "stdout": "",
            "stderr": "first attempt failed",
            "duration_seconds": 0.1,
            "command": "python src/main.py",
        },
        {
            "exit_code": 0,
            "stdout": '[{"name":"score","value":0.5,"definition":"Measured score.","split":"test"}]',
            "stderr": "",
            "duration_seconds": 0.2,
            "command": "python src/main.py",
        },
    ]
    monkeypatch.setattr(
        capability, "_execute_project", lambda *_args, **_kwargs: executions.pop(0)
    )
    monkeypatch.setattr(
        capability,
        "_repair_failed_execution",
        lambda *_args, **_kwargs: {
            "attempted": True,
            "iterations": 1,
            "applied": True,
            "summary": "fixed",
            "fixes": [{"filePath": "src/main.py", "method": "deterministic"}],
        },
    )

    result, repair, attempts = capability._execute_project_with_repair(
        "project_retry", "python", "qwen", "qwen-plus"
    )

    assert result["exit_code"] == 0
    assert repair["applied"] is True
    assert [attempt["exitCode"] for attempt in attempts] == [1, 0]


def test_experiment_execution_can_repair_two_distinct_failures(monkeypatch):
    capability = ExperimentCapability()
    executions = [
        {"exit_code": 1, "stderr": "np.trapz is unavailable", "command": "python src/main.py"},
        {"exit_code": 1, "stderr": "AssertionError: F1 violation", "command": "python src/main.py"},
        {"exit_code": 0, "stdout": "[]", "stderr": "", "command": "python src/main.py"},
    ]
    repairs = [
        {"attempted": True, "iterations": 1, "applied": True,
         "summary": "compatibility fixed", "fixes": []},
        {"attempted": True, "iterations": 1, "applied": True,
         "summary": "method fixed", "fixes": []},
    ]
    monkeypatch.setattr(capability, "_clear_experiment_outputs", lambda *_args: None)
    monkeypatch.setattr(
        capability, "_execute_project", lambda *_args, **_kwargs: executions.pop(0)
    )
    monkeypatch.setattr(
        capability, "_repair_failed_execution", lambda *_args, **_kwargs: repairs.pop(0)
    )
    monkeypatch.setattr(capability, "_collect_metrics", lambda *_args, **_kwargs: [{"name": "F1"}])

    result, repair, attempts = capability._execute_project_with_repair(
        "project_retry", "python", "qwen", "qwen-plus"
    )

    assert result["exit_code"] == 0
    assert repair["iterations"] == 2
    assert [attempt["exitCode"] for attempt in attempts] == [1, 1, 0]


def test_experiment_retry_clears_outputs_but_preserves_frozen_benchmark(
    tmp_path: Path, monkeypatch,
):
    repo = tmp_path / "code_projects" / "project_stale" / "repo"
    (repo / "data").mkdir(parents=True)
    (repo / "metrics.json").write_text("[]", encoding="utf-8")
    (repo / "evaluation_records.json").write_text("{}", encoding="utf-8")
    benchmark = repo / "data" / "frozen_benchmark.json"
    benchmark.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "app.faros.capabilities.adapters.experiment._DATA_DIR", str(tmp_path)
    )

    ExperimentCapability._clear_experiment_outputs("project_stale")

    assert not (repo / "metrics.json").exists()
    assert not (repo / "evaluation_records.json").exists()
    assert benchmark.exists()


def test_code_repair_rejects_truncated_scientific_entrypoint():
    original = (
        "import json\n"
        + "\n".join(f"VALUE_{index} = {index}" for index in range(90))
        + "\ndef main():\n"
        + "    open('metrics.json', 'w').write('[]')\n"
        + "    open('evaluation_records.json', 'w').write('{}')\n"
        + "    open('data/frozen_benchmark.json').read()\n"
        + "    print('faros-evaluation/v1', 'faros-benchmark/v1')\n"
        + "\nif __name__ == '__main__':\n    main()\n"
    )
    truncated = "def main():\n    pass\n\nif __name__ == '__main__':\n    main()\n"

    reason = CodeRepairService._validate_llm_repair(original, truncated)

    assert "truncated" in reason or "removed required contracts" in reason


def test_experiment_evidence_records_runtime_integrity_failures(tmp_path: Path):
    evidence = build_experiment_evidence(
        repo_dir=_repo(tmp_path),
        run_id="run_integrity",
        question_id="question_integrity",
        code_run_id="code_integrity",
        method="Method",
        baseline="Baseline",
        metrics=[{
            "name": "accuracy",
            "value": 0.5,
            "unit": "ratio",
            "definition": "Correct predictions divided by all predictions.",
            "split": "test",
        }],
        execution_result={
            "command": "python src/main.py",
            "exit_code": 0,
            "integrity_failures": ["Frozen benchmark changed."],
        },
    )

    assert evidence.status == ExecutionStatus.FAILED
    assert "Frozen benchmark changed." in evidence.failures


def test_scientific_entrypoint_generator_retries_unsupported_dependency():
    valid_source = """import json
from pathlib import Path

def main():
    metrics = [{"name": "score_baseline", "value": 0.1, "unit": "ratio", "definition": "Measured score.", "split": "test_baseline"}]
    with Path("metrics.json").open("w", encoding="utf-8") as handle:
        json.dump(metrics, handle)
    print(json.dumps(metrics))

if __name__ == "__main__":
    main()
"""

    class FakeClient:
        def __init__(self):
            self.calls = 0

        def chat(self, **kwargs):
            self.calls += 1
            text = (
                "from sklearn.linear_model import LogisticRegression\n"
                if self.calls == 1
                else f"```python\n{valid_source}```"
            )
            return SimpleNamespace(text=text)

    client = FakeClient()
    content = _step_synthesize_scientific_entrypoint(
        client=client,
        model="qwen-plus",
        title="Calibration",
        abstract="Does calibration help?",
        method="Compare a calibrated method with a baseline.",
        candidate=SimpleNamespace(
            baselines=["uncalibrated"],
            evaluationProtocol={"metrics": ["score"], "datasets": ["synthetic"]},
        ),
    )

    assert client.calls == 2
    assert "from src" not in content
    assert "metrics.json" in content


def test_classification_entrypoint_requires_auditable_records():
    source = """import json
def main():
    with open("metrics.json", "w") as handle:
        json.dump([], handle)
if __name__ == "__main__":
    main()
"""

    issues = _scientific_entrypoint_issues(
        source, require_classification_records=True,
    )

    assert any("auditable evaluation records" in issue for issue in issues)


def test_classification_entrypoint_rejects_ground_truth_as_prediction_label():
    source = """import json
def main():
    labels = [0, 1]
    probabilities = [0.2, 0.8]
    records = []
    for i in range(len(labels)):
        records.append({
            "label": int(labels[i]),
            "predictions": {
                "method": {"label": int(labels[i]), "probability": float(probabilities[i])}
            },
        })
    with open("metrics.json", "w") as handle:
        json.dump([], handle)
    with open("evaluation_records.json", "w") as handle:
        json.dump({"schema_version": "faros-evaluation/v1", "positive_label": 1,
                   "positive_class": "unsupported", "predictions": records,
                   "probability": probabilities}, handle)
if __name__ == "__main__":
    main()
"""

    issues = _scientific_entrypoint_issues(source, require_classification_records=True)

    assert any("not copied from ground truth" in issue for issue in issues)


def test_classification_entrypoint_requires_exact_prediction_label_key():
    source = '''import json
def main():
    labels = [0, 1]
    probabilities = [0.2, 0.8]
    records = []
    for i in range(len(labels)):
        records.append({
            "label": int(labels[i]),
            "predictions": {
                "method": {"predicted_label": int(probabilities[i] >= 0.5), "probability": float(probabilities[i])}
            },
        })
    with open("metrics.json", "w") as handle:
        json.dump([], handle)
    with open("evaluation_records.json", "w") as handle:
        json.dump({"schema_version": "faros-evaluation/v1", "positive_label": 1,
                   "positive_class": "unsupported", "records": records,
                   "predictions": records, "probability": probabilities}, handle)
if __name__ == "__main__":
    main()
'''

    issues = _scientific_entrypoint_issues(source, require_classification_records=True)

    assert any("exact key 'label'" in issue for issue in issues)


def test_inherited_classification_entrypoint_locks_positive_class_and_benchmark():
    source = '''import json
BENCHMARK_PATH = "data/frozen_benchmark.json"
def main():
    with open(BENCHMARK_PATH) as handle:
        benchmark = json.load(handle)
    payload = {"schema_version": "faros-evaluation/v1", "positive_label": 1,
               "positive_class": "supported", "records": [], "predictions": {},
               "probability": 0.5, "benchmark_id": "x", "generator_version": "1",
               "feature_schema": {}, "sample_id": "x", "features": [], "split": "test"}
    with open(BENCHMARK_PATH, "w") as handle:
        json.dump(benchmark, handle)
    with open("evaluation_records.json", "w") as handle:
        json.dump(payload, handle)
    with open("metrics.json", "w") as handle:
        json.dump([], handle)
if __name__ == "__main__":
    main()
'''

    issues = _scientific_entrypoint_issues(
        source,
        require_classification_records=True,
        frozen_benchmark={"positiveClass": "unsupported", "positiveLabel": 1},
    )

    assert any("positive_class must match" in issue for issue in issues)
    assert any("opened read-only" in issue for issue in issues)


def test_inherited_classification_entrypoint_does_not_require_creation_metadata():
    source = '''import json
def main():
    with open("data/frozen_benchmark.json", "r") as handle:
        benchmark = json.load(handle)
    records = benchmark["records"]
    output = {"schema_version": "faros-evaluation/v1",
              "positive_label": 1, "positive_class": "unsupported", "records": []}
    for record in records:
        probability = float(record["features"][0])
        output["records"].append({"sample_id": record["sample_id"],
            "split": record["split"], "label": record["label"],
            "predictions": {"method": {"label": int(probability >= 0.5),
            "probability": probability}}})
    with open("evaluation_records.json", "w") as handle:
        json.dump(output, handle)
    with open("metrics.json", "w") as handle:
        json.dump([], handle)
if __name__ == "__main__":
    main()
'''

    issues = _scientific_entrypoint_issues(
        source,
        require_classification_records=True,
        frozen_benchmark={"positiveClass": "unsupported", "positiveLabel": 1},
    )

    assert not any("auditable evaluation records" in issue for issue in issues)
