"""
Tests for the CodeAgentLoop autonomous execution agent.

Covers:
- Successful first-try execution
- Failed execution with repair and retry
- Max iterations exhaustion
- Failure classification
- Command planning/auto-discovery
"""

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from app.code.sandbox.models import SandboxResult
from app.code.sandbox.subprocess_backend import SubprocessSandbox
from app.code.sandbox.pool import SandboxPool


# Helper: create a minimal working project
def _make_project(base_dir: str, content: str = None, name: str = "main.py"):
    """Create a minimal Python project in base_dir."""
    repo = Path(base_dir) / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    src = repo / "src"
    src.mkdir(exist_ok=True)
    (src / "__init__.py").write_text("")

    if content is None:
        content = (
            'import sys\n'
            'def main():\n'
            '    print("Hello World")\n'
            '    return 0\n'
            'if __name__ == "__main__":\n'
            '    sys.exit(main())\n'
        )
    (src / name).write_text(content)
    return str(repo)


class TestCodeAgentLoop:
    """Test the autonomous agent loop with real subprocess sandbox."""

    @pytest.fixture
    def pool(self):
        """Create a SandboxPool with subprocess backend."""
        pool = SandboxPool(max_active=4, default_backend="subprocess")
        pool.register_backend(SubprocessSandbox())
        return pool

    @pytest.mark.asyncio
    async def test_successful_first_try(self, pool, temp_repo_dir):
        """Agent loop succeeds on first attempt."""
        from app.services.code_agent_loop import CodeAgentLoop

        loop = CodeAgentLoop(
            pool=pool,
            max_iterations=2,
            execution_timeout=30,
            backend="subprocess",
        )

        result = await loop.run(
            project_id="test_proj",
            repo_dir=temp_repo_dir,
            goal="Run the main.py script",
            language="python",
            command="python src/main.py",
        )

        assert result.status == "succeeded"
        assert result.iterations == 1
        assert result.final_result is not None
        assert result.final_result.exit_code == 0
        assert "Hello from test project" in result.final_result.stdout
        assert result.trace is not None
        assert len(result.trace.events) >= 3  # plan, execute, complete

    @pytest.mark.asyncio
    async def test_failure_with_repair(self, pool, temp_broken_repo):
        """Agent detects failure and attempts repair on broken code."""
        from app.services.code_agent_loop import CodeAgentLoop

        loop = CodeAgentLoop(
            pool=pool,
            max_iterations=3,
            execution_timeout=30,
            backend="subprocess",
        )

        result = await loop.run(
            project_id="test_proj_broken",
            repo_dir=temp_broken_repo,
            goal="Fix and run the project",
            language="python",
            command="python src/main.py",
        )

        # The agent should detect the error and attempt repair
        assert result.iterations >= 1
        assert result.trace is not None

        # Verify that repair was attempted
        repair_events = [
            e for e in result.trace.events
            if e.step == "repair"
        ]
        assert len(repair_events) >= 1, "Should have attempted at least one repair"

        # If LLM available, the fix may succeed (self-healing works!)
        if result.status == "succeeded":
            assert result.iterations >= 2, "Should take at least 2 iterations (fail + succeed)"
        else:
            assert result.status in ("failed", "max_iterations", "error")

    @pytest.mark.asyncio
    async def test_failure_with_import_error(self, pool, temp_import_error_repo):
        """Agent detects import error and attempts to fix."""
        from app.services.code_agent_loop import CodeAgentLoop

        loop = CodeAgentLoop(
            pool=pool,
            max_iterations=3,
            execution_timeout=30,
            backend="subprocess",
        )

        result = await loop.run(
            project_id="test_proj_import",
            repo_dir=temp_import_error_repo,
            goal="Fix import error and run",
            language="python",
            command="python main.py",
        )

        # The import error repair won't fix a truly nonexistent module,
        # but it should try
        assert result.iterations >= 1
        assert result.trace is not None

        # There should be an execute event with non-zero exit code
        exec_events = [
            e for e in result.trace.events
            if e.step == "execute" and e.status == "failed"
        ]
        assert len(exec_events) >= 1

    @pytest.mark.asyncio
    async def test_returns_error_on_missing_repo(self, pool):
        """Agent returns error when repo directory doesn't exist."""
        from app.services.code_agent_loop import CodeAgentLoop

        loop = CodeAgentLoop(pool=pool, backend="subprocess")
        result = await loop.run(
            project_id="test",
            repo_dir="/nonexistent/path/12345",
            command="echo test",
        )

        assert result.status == "error"
        assert "not found" in result.error.lower() or result.error != ""

    @pytest.mark.asyncio
    async def test_trace_completeness(self, pool, temp_repo_dir):
        """Execution trace contains all expected phases."""
        from app.services.code_agent_loop import CodeAgentLoop

        loop = CodeAgentLoop(
            pool=pool,
            max_iterations=2,
            execution_timeout=30,
            backend="subprocess",
        )

        result = await loop.run(
            project_id="test_trace",
            repo_dir=temp_repo_dir,
            command="python src/main.py",
        )

        trace = result.trace
        assert trace is not None

        steps = [e.step for e in trace.events]
        # Expected: plan, setup, execute, complete (on success)
        assert "setup" in steps
        assert "execute" in steps
        assert "complete" in steps

    @pytest.mark.asyncio
    async def test_result_to_dict(self, pool, temp_repo_dir):
        """AgentLoopResult.to_dict() produces valid output."""
        from app.services.code_agent_loop import CodeAgentLoop

        loop = CodeAgentLoop(pool=pool, backend="subprocess")
        result = await loop.run(
            project_id="test_dict",
            repo_dir=temp_repo_dir,
            command="python src/main.py",
        )

        d = result.to_dict()
        assert d["status"] == "succeeded"
        assert d["iterations"] == 1
        assert d["exit_code"] == 0
        assert d["trace_id"] is not None


class TestFailureClassification:
    """Test _classify_failure static method."""

    def test_classify_syntax_error(self):
        from app.services.code_agent_loop import CodeAgentLoop
        r = SandboxResult(exit_code=1, stdout="", stderr="SyntaxError: invalid syntax")
        assert CodeAgentLoop._classify_failure(r) == "syntax_error"

    def test_classify_module_not_found(self):
        from app.services.code_agent_loop import CodeAgentLoop
        r = SandboxResult(exit_code=1, stdout="", stderr="ModuleNotFoundError: No module named 'xxx'")
        assert CodeAgentLoop._classify_failure(r) == "module_not_found"

    def test_classify_import_error(self):
        from app.services.code_agent_loop import CodeAgentLoop
        r = SandboxResult(exit_code=1, stdout="", stderr="ImportError: cannot import name 'X'")
        assert CodeAgentLoop._classify_failure(r) == "import_error"

    def test_classify_timeout(self):
        from app.services.code_agent_loop import CodeAgentLoop
        r = SandboxResult(exit_code=-1, stdout="", stderr="", timed_out=True)
        assert CodeAgentLoop._classify_failure(r) == "timeout"

    def test_classify_unknown(self):
        from app.services.code_agent_loop import CodeAgentLoop
        r = SandboxResult(exit_code=2, stdout="", stderr="something weird happened")
        assert CodeAgentLoop._classify_failure(r) == "non_zero_exit_code_2"


class TestCommandDiscovery:
    """Test auto-discovery of execution commands."""

    def test_discovers_pytest(self):
        from app.services.code_agent_loop import CodeAgentLoop
        # Create a project with tests/
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            tests = repo / "tests"
            tests.mkdir(parents=True)
            (tests / "test_something.py").write_text("def test_pass(): pass")
            (repo / "pyproject.toml").write_text("[tool.pytest.ini_options]")

            cmd = CodeAgentLoop._discover_command(str(repo), "python")
            assert cmd is not None
            assert "pytest" in cmd

    def test_discovers_main_py(self):
        from app.services.code_agent_loop import CodeAgentLoop
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            src = repo / "src"
            src.mkdir(parents=True)
            (src / "main.py").write_text("print('hi')")

            cmd = CodeAgentLoop._discover_command(str(repo), "python")
            assert cmd == "python src/main.py"

    def test_no_entry_point_returns_none(self):
        from app.services.code_agent_loop import CodeAgentLoop
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir(parents=True)
            # Empty repo, no Python files
            cmd = CodeAgentLoop._discover_command(str(repo), "python")
            assert cmd is None

    def test_fallback_command(self):
        from app.services.code_agent_loop import CodeAgentLoop
        cmd = CodeAgentLoop._fallback_command("python")
        assert "python" in cmd
        cmd_unknown = CodeAgentLoop._fallback_command("rust")
        assert "exit 1" in cmd_unknown


class TestCodeRepairServiceEnhanced:
    """Test enhanced repair service features."""

    def test_backup_created_and_rollback(self, temp_repo_dir):
        """Verify backup is created before file modification."""
        from app.services.code_repair_service import _backup_file, rollback_file, list_backups

        main_file = Path(temp_repo_dir) / "src" / "main.py"
        original = main_file.read_text()
        backup_path = str(main_file) + ".farosbak"

        # Backup should be created
        result = _backup_file(main_file)
        assert result is not None
        assert os.path.exists(backup_path)

        # Modify the file
        main_file.write_text("modified content")

        # Rollback should restore
        restored = rollback_file(str(main_file))
        assert restored is True
        assert main_file.read_text() == original
        assert not os.path.exists(backup_path)  # backup cleaned up

    def test_backup_nonexistent_file(self):
        from app.services.code_repair_service import _backup_file
        result = _backup_file(Path("/nonexistent/file.txt"))
        assert result is None

    def test_rollback_no_backup(self):
        from app.services.code_repair_service import rollback_file
        assert rollback_file("/nonexistent/file.py") is False

    def test_list_backups(self, temp_repo_dir):
        from app.services.code_repair_service import _backup_file, list_backups

        main_file = Path(temp_repo_dir) / "src" / "main.py"
        _backup_file(main_file)

        backups = list_backups(temp_repo_dir)
        assert len(backups) >= 1
        assert any(".farosbak" in b for b in backups)

    def test_deterministic_fix_relative_import(self):
        from app.services.code_repair_service import CodeRepairService, FixResult

        svc = CodeRepairService()
        content = "from .routes import router\nfrom .models import User\n\napp = 'test'\n"
        stderr = "ModuleNotFoundError: No module named 'routes'"

        result = svc._apply_deterministic_fixes(
            "test_step", "main.py", content, stderr, ""
        )

        assert result is not None, "Should produce a fix result"
        assert result.method == "deterministic"
        assert "from ." not in result.new_content

    def test_deterministic_fix_adds_main_guard(self):
        from app.services.code_repair_service import CodeRepairService

        svc = CodeRepairService()
        content = (
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n"
            "@app.get('/')\n"
            "def root():\n"
            "    return {'status': 'ok'}\n"
        )

        result = svc._apply_deterministic_fixes(
            "test_step", "main.py", content, "", ""
        )

        assert result is not None
        assert "if __name__" in result.new_content
        assert "uvicorn" in result.new_content

    def test_deterministic_fix_no_change_needed(self):
        from app.services.code_repair_service import CodeRepairService

        svc = CodeRepairService()
        content = "print('hello world')\n"
        result = svc._apply_deterministic_fixes(
            "test", "main.py", content, "", ""
        )

        assert result is None  # No fix needed
