import asyncio
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
        def chat(self, **_kwargs):
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
