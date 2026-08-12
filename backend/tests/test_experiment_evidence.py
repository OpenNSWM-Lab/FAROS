import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.code.sandbox.subprocess_backend import SubprocessSandbox
from app.contracts import ExecutionStatus
from app.faros.capabilities.adapters.experiment import ExperimentCapability
from app.faros.capabilities.adapters.paper_drafting import PaperDraftingCapability
from app.services.experiment_evidence_service import build_experiment_evidence
from app.services.code_agent_service import _step_synthesize_scientific_entrypoint


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (repo / "requirements.txt").write_text("numpy\n", encoding="utf-8")
    return repo


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
