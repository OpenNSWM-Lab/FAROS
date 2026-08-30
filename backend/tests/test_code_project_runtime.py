from types import SimpleNamespace

from app.modules.code.code_projects_api import _build_pipeline_steps, _get_project_repo_dir


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
