import asyncio
import csv
import json
import subprocess
import sys
from types import SimpleNamespace

from app.modules.code.code_projects_api import _build_pipeline_steps, _get_project_repo_dir
from app.services.cart_runner import CartNodeResult, CartRunner


def test_pipeline_definition_does_not_write_helper_files(tmp_path):
    repo = tmp_path / "repo"
    source = repo / "src"
    source.mkdir(parents=True)
    (source / "main.py").write_text("print('ok')\n", encoding="utf-8")
    before = sorted(path.relative_to(repo).as_posix() for path in repo.rglob("*"))

    steps = _build_pipeline_steps(str(repo), "python")

    after = sorted(path.relative_to(repo).as_posix() for path in repo.rglob("*"))
    assert after == before
    assert any(step.command == "python -m compileall -q . 2>&1" for step in steps)
    assert all("_faros_syntax_check.py" not in step.command for step in steps if step.name != "Cleanup")


def test_project_repo_fallback_uses_configured_data_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    repo = tmp_path / "code_projects" / "project-1" / "repo"
    repo.mkdir(parents=True)
    project = SimpleNamespace(id="project-1", root_storage_path="/stale/development/path")

    assert _get_project_repo_dir(project) == str(repo)


def test_cart_generated_scripts_use_active_interpreter_without_provider_secrets(tmp_path, monkeypatch):
    calls = []
    llm_calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"status":"ok"}', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "must-not-reach-generated-code")
    node = {
        "id": "step-1",
        "title": "Calculate prime numbers",
        "desc": "Generate a small deterministic result",
        "expected": [],
        "outputs": [],
    }
    direct_result = CartNodeResult(node_id="step-1", success=False)

    assert CartRunner._execute_direct(node, str(tmp_path), direct_result)

    class FakeProvider:
        def chat(self, **kwargs):
            llm_calls.append(kwargs)
            return SimpleNamespace(text="print('ok')")

    from app.llm import provider_client

    monkeypatch.setattr(provider_client, "get_provider_client", lambda: FakeProvider())
    api_result = CartNodeResult(node_id="step-1", success=False)
    assert asyncio.run(CartRunner()._execute_via_llm_api(
        node,
        {"packageId": "ppkg-test"},
        str(tmp_path),
        api_result,
    ))

    assert len(calls) == 2
    assert all(command[0] == sys.executable for command, _kwargs in calls)
    assert all("DASHSCOPE_API_KEY" not in kwargs["env"] for _command, kwargs in calls)
    assert llm_calls[0]["max_tokens"] == 3200
    assert "ppkg-test" in llm_calls[0]["messages"][0].content
    assert "planned_not_executed" in llm_calls[0]["messages"][0].content


def test_llm_execution_requires_exact_declared_outputs(tmp_path, monkeypatch):
    def fake_run(_command, **_kwargs):
        return SimpleNamespace(returncode=0, stdout='{"status":"ok"}', stderr="")

    class FakeProvider:
        def chat(self, **_kwargs):
            return SimpleNamespace(text="print('completed without writing the contract file')")

    from app.llm import provider_client

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(provider_client, "get_provider_client", lambda: FakeProvider())
    node = {
        "id": "step-missing",
        "title": "Write an evidence report",
        "desc": "Ground the report in the PlanPackage.",
        "expected": [],
        "outputs": [{"type": "report", "name": "expected_report.md"}],
    }
    result = CartNodeResult(node_id=node["id"], success=False)

    assert not asyncio.run(CartRunner()._execute_via_llm_api(
        node,
        {"packageId": "ppkg-contract"},
        str(tmp_path),
        result,
    ))
    assert "expected_report.md" in result.message
    assert (tmp_path / "_llm_step_missing.py").is_file()


def test_planning_contract_repair_is_grounded_and_marks_outputs_unexecuted(tmp_path):
    node = {
        "id": "step-plan",
        "title": "Plan evidence-grounded validation",
        "desc": "Prepare literature, baselines, ablations, and result slots.",
        "method": "Compare a claim verifier with a grounded baseline on the same split.",
        "expected": [
            {"metric": "citation faithfulness", "target": "mean_delta > 0"},
            {"metric": "refusal accuracy", "target": ">= baseline"},
        ],
        "outputs": [
            {"type": "report", "name": "literature_survey.md"},
            {"type": "metrics", "name": "validation_metrics.json"},
            {"type": "code", "name": "verifier_implementation_plan.json"},
            {"type": "table", "name": "baseline_scope.csv"},
            {"type": "table", "name": "ablation_plan.csv"},
            {"type": "chart", "name": "planned_results_chart.png"},
            {"type": "report", "name": "failure_analysis_plan.md"},
        ],
    }
    ppkg = {
        "packageId": "ppkg-grounded",
        "researchQuestion": "Does claim verification improve citation faithfulness?",
        "hypothesis": "The verifier improves faithfulness over vanilla RAG.",
        "idea": {
            "title": "Citation-faithful RAG",
            "problem": "Citations may not support generated claims.",
            "proposedMethod": "Verify each claim against cited evidence.",
            "expectedOutcome": "Fewer unsupported claims.",
        },
        "literatureSurvey": {
            "summary": "Prior work evaluates claim-level attribution.",
            "coverage": {"structuredPaperCount": 1},
            "papers": [{
                "paperId": "paper-rag-1",
                "title": "Citation Attribution for Retrieval-Augmented Generation",
                "year": 2025,
                "venue": "TestConf",
                "url": "https://example.test/paper-rag-1",
                "role": "supporting_evidence",
                "summary": "Defines a claim-level citation baseline.",
                "limitations": ["Does not jointly evaluate refusal."],
                "methods": [{
                    "name": "vanilla RAG",
                    "description": "Retrieve then generate without claim verification.",
                    "role": "baseline",
                }],
            }],
        },
        "gap": {
            "selectedGapId": "gap-1",
            "items": [{
                "id": "gap-1",
                "statement": "Citation support and refusal are evaluated separately.",
            }],
        },
        "principle": {
            "summary": "Verify claims before allowing an answer.",
            "mechanism": "Align claims to cited spans and refuse unsupported claims.",
            "noveltyClaim": "Use one evidence test for attribution and refusal.",
            "assumptions": ["The corpus contains evidence for answerable questions."],
            "risks": ["An over-conservative verifier may reduce coverage."],
        },
        "constants": {"randomSeeds": [13, 29, 47]},
    }
    result = CartNodeResult(node_id=node["id"], success=False)

    assert CartRunner._materialize_planning_artifacts(node, ppkg, str(tmp_path), result)

    survey = (tmp_path / "literature_survey.md").read_text(encoding="utf-8")
    failure_plan = (tmp_path / "failure_analysis_plan.md").read_text(encoding="utf-8")
    metrics = json.loads((tmp_path / "validation_metrics.json").read_text(encoding="utf-8"))
    implementation = json.loads((tmp_path / "verifier_implementation_plan.json").read_text(encoding="utf-8"))
    with (tmp_path / "baseline_scope.csv").open(encoding="utf-8", newline="") as handle:
        baseline_rows = list(csv.DictReader(handle))
    with (tmp_path / "ablation_plan.csv").open(encoding="utf-8", newline="") as handle:
        ablation_rows = list(csv.DictReader(handle))

    assert "Citation Attribution for Retrieval-Augmented Generation" in survey
    assert "paper-rag-1" in survey
    assert "planned_not_executed" in survey
    assert "over-conservative verifier" in failure_plan
    assert metrics["status"] == "planned_not_executed"
    assert metrics["observedResults"] is None
    assert implementation["status"] == "planned_not_executed"
    assert all(item["observedValue"] is None for item in metrics["expectedMetrics"])
    assert baseline_rows[0]["baseline_name"] == "vanilla RAG"
    assert baseline_rows[0]["observed_value"] == ""
    assert baseline_rows[0]["status"] == "planned_not_executed"
    assert all(row["observed_value"] == "" for row in ablation_rows)
    assert (tmp_path / "planned_results_chart.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert "No observed experiment result was asserted" in result.message


def test_planning_contract_repair_rejects_executable_and_escaping_outputs(tmp_path):
    ppkg = {"packageId": "ppkg-safe"}
    result = CartNodeResult(node_id="step-safe", success=False)
    code_node = {
        "id": "step-safe",
        "outputs": [{"type": "code", "name": "implementation.py"}],
    }
    mislabeled_code_node = {
        "id": "step-safe",
        "outputs": [{"type": "code", "name": "implementation.json"}],
    }
    escaping_node = {
        "id": "step-safe",
        "outputs": [{"type": "report", "name": "../escape.md"}],
    }

    assert not CartRunner._materialize_planning_artifacts(code_node, ppkg, str(tmp_path), result)
    assert not CartRunner._materialize_planning_artifacts(mislabeled_code_node, ppkg, str(tmp_path), result)
    assert not CartRunner._materialize_planning_artifacts(escaping_node, ppkg, str(tmp_path), result)
    assert not (tmp_path.parent / "escape.md").exists()
