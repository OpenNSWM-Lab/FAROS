"""
Tests for the sandbox abstraction layer and subprocess backend.

Covers:
- SandboxResult, ResourceUsage data classes
- SubprocessSandbox: setup, execute, read/write file, teardown
- SandboxPool: acquire, execute, release, concurrency limits
- ExecutionTrace: event recording, summary, persistence
"""

import asyncio
import os
import time

import pytest

from app.code.sandbox.models import SandboxResult, ResourceUsage, ActiveSandbox
from app.code.sandbox.base import SandboxBackend
from app.code.sandbox.subprocess_backend import SubprocessSandbox
from app.code.sandbox.pool import (
    SandboxPool,
    SandboxPoolExhausted,
    SandboxNotFound,
    get_sandbox_pool,
)
from app.code.sandbox.trace import ExecutionEvent, ExecutionTrace


# ---- Data models ----

class TestSandboxResult:
    def test_success_property(self):
        r = SandboxResult(exit_code=0, stdout="ok", stderr="")
        assert r.success is True

        r2 = SandboxResult(exit_code=1, stdout="", stderr="error")
        assert r2.success is False

        r3 = SandboxResult(exit_code=0, stdout="", stderr="", timed_out=True)
        assert r3.success is False

    def test_to_dict_truncates(self):
        r = SandboxResult(exit_code=0, stdout="x" * 6000, stderr="y" * 6000)
        d = r.to_dict()
        assert len(d["stdout"]) <= 5000
        assert len(d["stderr"]) <= 5000
        assert d["exit_code"] == 0
        assert d["success"] is True


class TestActiveSandbox:
    def test_age_and_idle(self):
        now = time.time()
        a = ActiveSandbox(
            sandbox_id="test_1",
            backend="subprocess",
            workspace_path="/tmp",
            created_at=now - 100,
            last_used=now - 10,
        )
        assert a.age_sec(now) == 100
        assert a.idle_sec(now) == 10


# ---- SubprocessSandbox ----

class TestSubprocessSandbox:
    @pytest.mark.asyncio
    async def test_setup_creates_workspace(self, temp_repo_dir):
        sb = SubprocessSandbox()
        sid = await sb.setup(temp_repo_dir)
        assert sid.startswith("subproc_")
        assert sid in sb._workspaces

        await sb.teardown(sid)
        assert sid not in sb._workspaces

    @pytest.mark.asyncio
    async def test_execute_successful_command(self, temp_repo_dir):
        sb = SubprocessSandbox()
        sid = await sb.setup(temp_repo_dir)
        result = await sb.execute(sid, "python src/main.py", timeout=30)

        assert result.exit_code == 0
        assert "Hello from test project" in result.stdout
        assert result.success is True
        assert result.timed_out is False
        assert result.duration_ms > 0

        await sb.teardown(sid)

    @pytest.mark.asyncio
    async def test_execute_failed_command(self, temp_repo_dir):
        sb = SubprocessSandbox()
        sid = await sb.setup(temp_repo_dir)
        result = await sb.execute(sid, "python nonexistent.py", timeout=10)

        assert result.exit_code != 0
        assert result.success is False

        await sb.teardown(sid)

    @pytest.mark.asyncio
    async def test_execute_dangerous_command_blocked(self, temp_repo_dir):
        sb = SubprocessSandbox()
        sid = await sb.setup(temp_repo_dir)
        result = await sb.execute(sid, "rm -rf /", timeout=10)

        assert result.exit_code == -1
        assert "blocked" in result.stderr.lower()

        await sb.teardown(sid)

    @pytest.mark.asyncio
    async def test_execute_timeout(self, temp_repo_dir):
        sb = SubprocessSandbox()
        sid = await sb.setup(temp_repo_dir)
        # Run a command that sleeps longer than timeout
        result = await sb.execute(sid, "sleep 5", timeout=1)

        assert result.timed_out is True
        assert result.exit_code == -1

        await sb.teardown(sid)

    @pytest.mark.asyncio
    async def test_read_write_file(self, temp_repo_dir):
        sb = SubprocessSandbox()
        sid = await sb.setup(temp_repo_dir)

        await sb.write_file(sid, "test.txt", "hello world")
        content = await sb.read_file(sid, "test.txt")
        assert content == "hello world"

        await sb.teardown(sid)

    @pytest.mark.asyncio
    async def test_execute_nonexistent_sandbox(self):
        sb = SubprocessSandbox()
        result = await sb.execute("bad_id", "echo hi", timeout=5)
        assert "not found" in result.stderr.lower()

    @pytest.mark.asyncio
    async def test_teardown_idempotent(self, temp_repo_dir):
        sb = SubprocessSandbox()
        sid = await sb.setup(temp_repo_dir)
        await sb.teardown(sid)
        # Second teardown should not raise
        await sb.teardown(sid)
        await sb.teardown("nonexistent_id")

    @pytest.mark.asyncio
    async def test_is_available_always_true(self):
        sb = SubprocessSandbox()
        assert sb.is_available() is True


# ---- SandboxPool ----

class TestSandboxPool:
    @pytest.mark.asyncio
    async def test_acquire_and_release(self, temp_repo_dir):
        pool = SandboxPool(max_active=4)
        pool.register_backend(SubprocessSandbox())

        sid = await pool.acquire(temp_repo_dir)
        assert sid is not None
        assert pool.active_count == 1

        released = await pool.release(sid)
        assert released is True
        assert pool.active_count == 0

    @pytest.mark.asyncio
    async def test_execute_via_pool(self, temp_repo_dir):
        pool = SandboxPool(max_active=4)
        pool.register_backend(SubprocessSandbox())

        sid = await pool.acquire(temp_repo_dir)
        result = await pool.execute(sid, "python src/main.py", timeout=30)

        assert result.success is True
        assert "Hello from test project" in result.stdout

        await pool.release(sid)

    @pytest.mark.asyncio
    async def test_max_concurrent_enforced(self, temp_repo_dir):
        pool = SandboxPool(max_active=2)
        pool.register_backend(SubprocessSandbox())

        sid1 = await pool.acquire(temp_repo_dir)
        sid2 = await pool.acquire(temp_repo_dir)

        with pytest.raises(SandboxPoolExhausted):
            await pool.acquire(temp_repo_dir)

        await pool.release(sid1)
        await pool.release(sid2)

    @pytest.mark.asyncio
    async def test_execute_nonexistent_sandbox_in_pool(self):
        pool = SandboxPool(max_active=4)
        pool.register_backend(SubprocessSandbox())

        with pytest.raises(SandboxNotFound):
            await pool.execute("nonexistent", "echo hi")

    @pytest.mark.asyncio
    async def test_available_backends(self):
        pool = SandboxPool(max_active=4)
        pool.register_backend(SubprocessSandbox())

        assert "subprocess" in pool.available_backends
        assert pool.default_backend == "subprocess"

    @pytest.mark.asyncio
    async def test_pool_info(self, temp_repo_dir):
        pool = SandboxPool(max_active=4)
        pool.register_backend(SubprocessSandbox())

        sid = await pool.acquire(temp_repo_dir)
        info = pool.pool_info

        assert info["active_count"] == 1
        assert info["max_active"] == 4
        assert len(info["active_sandboxes"]) == 1

        await pool.release(sid)

    @pytest.mark.asyncio
    async def test_teardown_all(self, temp_repo_dir):
        pool = SandboxPool(max_active=4)
        pool.register_backend(SubprocessSandbox())

        await pool.acquire(temp_repo_dir)
        await pool.acquire(temp_repo_dir)
        assert pool.active_count == 2

        await pool.teardown_all()
        assert pool.active_count == 0

    @pytest.mark.asyncio
    async def test_singleton_pool(self, temp_repo_dir):
        pool1 = await get_sandbox_pool(max_active=4)
        pool2 = await get_sandbox_pool()
        assert pool1 is pool2


# ---- ExecutionTrace ----

class TestExecutionTrace:
    def test_record_and_summary(self):
        trace = ExecutionTrace(
            trace_id="test_trace_1",
            project_id="proj_123",
            goal="Test goal",
        )

        trace.record(ExecutionEvent(
            step="plan", status="succeeded",
            message="Found entry point",
        ))
        trace.record(ExecutionEvent(
            step="execute", status="succeeded",
            message="Code ran successfully",
            iteration=1, duration_ms=150,
        ))
        trace.record(ExecutionEvent(
            step="complete", status="succeeded",
            message="Done",
        ))

        summary = trace.summary()
        assert summary["total_events"] == 3
        assert summary["status"] == "succeeded"
        assert summary["iterations"] == 1
        assert summary["repairs_applied"] == 0

    def test_trace_with_repairs(self):
        trace = ExecutionTrace("test_trace_2", "proj_456", "Fix bugs")

        trace.record(ExecutionEvent(step="plan", status="succeeded", message=""))
        trace.record(ExecutionEvent(
            step="execute", status="failed",
            message="Import error", iteration=1,
        ))
        trace.record(ExecutionEvent(
            step="repair", status="succeeded",
            message="Fixed imports", iteration=1,
        ))
        trace.record(ExecutionEvent(
            step="execute", status="succeeded",
            message="OK", iteration=2,
        ))

        summary = trace.summary()
        assert summary["iterations"] == 2
        assert summary["repairs_applied"] == 1
        assert summary["errors"]  # has the failed execute event

    def test_persistence_roundtrip(self, tmp_path):
        # Override trace dir
        import app.code.sandbox.trace as trace_mod
        old_dir = trace_mod._TRACE_DIR
        trace_mod._TRACE_DIR = str(tmp_path)

        try:
            trace = ExecutionTrace("roundtrip_test", "proj_789", "")
            trace.record(ExecutionEvent(
                step="execute", status="succeeded",
                message="test", iteration=1,
            ))

            # Load it back
            loaded = ExecutionTrace.load("roundtrip_test")
            assert loaded is not None
            assert loaded.trace_id == "roundtrip_test"
            assert len(loaded.events) == 1
            assert loaded.events[0].step == "execute"
        finally:
            trace_mod._TRACE_DIR = old_dir

    def test_load_nonexistent(self):
        result = ExecutionTrace.load("does_not_exist_12345")
        assert result is None

    def test_summary_empty_trace(self):
        trace = ExecutionTrace("empty", "p1", "")
        summary = trace.summary()
        assert summary["status"] == "no_events"
        assert summary["total_events"] == 0
