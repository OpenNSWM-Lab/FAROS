"""
CodeAgentLoop — Autonomous code execution agent with self-healing.

Orchestrates a Think → Execute → Observe → Repair loop:
1. PLAN:   Analyze project structure, LLM decides what command to run
2. EXEC:   Run command in sandbox (Docker or subprocess)
3. OBSERVE: Capture stdout/stderr/exit_code, classify failure type
4. REPAIR: Call CodeRepairService (deterministic fixes + LLM fixes)
5. REPEAT: Up to MAX_ITERATIONS times, or until success
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from app.code.sandbox import (
    ExecutionEvent,
    ExecutionTrace,
    SandboxPool,
    SandboxResult,
    get_sandbox_pool,
)

logger = logging.getLogger(__name__)

# Defaults
DEFAULT_MAX_ITERATIONS = 3
DEFAULT_EXECUTION_TIMEOUT = 300
DEFAULT_LANGUAGE = "python"


@dataclass
class AgentLoopResult:
    """Result of an autonomous agent run."""

    status: str  # "succeeded", "failed", "max_iterations", "error"
    iterations: int = 0
    final_result: Optional[SandboxResult] = None
    repair_report: Any = None  # AutoFixReport (lazy import)
    trace: Optional[ExecutionTrace] = None
    error: str = ""
    events: list[dict] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.status == "succeeded"

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "iterations": self.iterations,
            "exit_code": self.final_result.exit_code if self.final_result else None,
            "stdout_tail": (
                self.final_result.stdout[-500:]
                if self.final_result and self.final_result.stdout
                else ""
            ),
            "stderr_tail": (
                self.final_result.stderr[-500:]
                if self.final_result and self.final_result.stderr
                else ""
            ),
            "duration_ms": (
                self.final_result.duration_ms if self.final_result else 0
            ),
            "error": self.error,
            "trace_id": self.trace.trace_id if self.trace else None,
            "events": self.events,
        }


class CodeAgentLoop:
    """Autonomous code execution agent.

    Loop (per iteration):
    1. PLAN:    LLM plans the execution command
    2. EXEC:    SandboxPool.execute() runs it
    3. OBSERVE: Parse result, classify failure
    4. REPAIR:  CodeRepairService.auto_fix() if failed
    5. GOTO 2   (up to max_iterations)

    On success, returns immediately. If all iterations exhausted,
    returns final failed result with repair history.
    """

    def __init__(
        self,
        pool: Optional[SandboxPool] = None,
        provider_name: str = "qwen",
        model: str = "qwen-max",
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        execution_timeout: int = DEFAULT_EXECUTION_TIMEOUT,
        backend: Optional[str] = None,
    ):
        self._pool = pool
        self._provider_name = provider_name
        self._model = model
        self.max_iterations = max_iterations
        self.execution_timeout = execution_timeout
        self.backend = backend

    async def run(
        self,
        project_id: str,
        repo_dir: str,
        goal: str = "",
        language: str = DEFAULT_LANGUAGE,
        command: Optional[str] = None,
        on_event: Optional[Callable[[ExecutionEvent], None]] = None,
        trace_id: Optional[str] = None,
    ) -> AgentLoopResult:
        """Execute the autonomous agent loop for a project.

        Args:
            project_id: CodeProject identifier.
            repo_dir: Path to the project repository on disk.
            goal: Human-readable goal for the agent.
            language: Programming language ("python", etc.).
            command: Optional explicit command. If None, LLM plans it.
            on_event: Optional callback for streaming events.
            trace_id: Optional trace identifier. If not provided, a new one is generated.

        Returns:
            AgentLoopResult with status, trace, and execution details.
        """
        pool = self._pool or await get_sandbox_pool()
        trace = ExecutionTrace(
            trace_id=trace_id or _generate_trace_id(),
            project_id=project_id,
            goal=goal or f"Execute and validate {language} project",
        )

        sandbox_id = None
        events: list[dict] = []

        try:
            # ---- Phase 0: Validate project ----
            if not os.path.isdir(repo_dir):
                return AgentLoopResult(
                    status="error",
                    error=f"Repo directory not found: {repo_dir}",
                    trace=trace,
                )

            # ---- Phase 1: PLAN ----
            trace.record(ExecutionEvent(
                step="plan", status="started",
                message="Analyzing project and planning execution...",
            ))

            if command:
                final_command = command
                trace.record(ExecutionEvent(
                    step="plan", status="succeeded",
                    message=f"Using provided command: {command[:200]}",
                    details={"command": command},
                ))
            else:
                final_command = await self._plan_command(
                    repo_dir, goal, language
                )
                trace.record(ExecutionEvent(
                    step="plan", status="succeeded",
                    message=f"Planned command: {final_command[:200]}",
                    details={"command": final_command},
                ))
            events.append({"phase": "plan", "command": final_command})

            # ---- Phase 2: Sandbox Setup ----
            trace.record(ExecutionEvent(
                step="setup", status="started",
                message=f"Acquiring sandbox (backend={pool.default_backend})...",
            ))

            try:
                sandbox_id = await pool.acquire(
                    workspace_path=repo_dir,
                    backend_type=self.backend,
                )
                trace.record(ExecutionEvent(
                    step="setup", status="succeeded",
                    sandbox_id=sandbox_id,
                    message=f"Sandbox acquired: {sandbox_id}",
                ))
            except Exception as exc:
                logger.error("Failed to acquire sandbox: %s", exc)
                trace.record(ExecutionEvent(
                    step="setup", status="failed",
                    message=f"Sandbox acquisition failed: {exc}",
                ))
                return AgentLoopResult(
                    status="error",
                    error=f"Sandbox setup failed: {exc}",
                    trace=trace,
                    events=events,
                )

            events.append({"phase": "setup", "sandbox_id": sandbox_id})

            # ---- Phase 3: Main execute-observe-repair loop ----
            iteration = 0
            last_result: Optional[SandboxResult] = None
            repair_report = None

            while iteration < self.max_iterations:
                iteration += 1
                logger.info(
                    "Agent loop iteration %d/%d (project=%s)",
                    iteration, self.max_iterations, project_id,
                )

                # EXECUTE
                trace.record(ExecutionEvent(
                    step="execute", status="started",
                    iteration=iteration,
                    message=f"Iteration {iteration}: executing...",
                ))

                result = await pool.execute(
                    sandbox_id, final_command,
                    timeout=self.execution_timeout,
                )
                trace.record(ExecutionEvent(
                    step="execute",
                    status="succeeded" if result.success else "failed",
                    iteration=iteration,
                    message=(
                        f"Exit code {result.exit_code}, "
                        f"{result.duration_ms}ms"
                    ),
                    details=result.to_dict(),
                    duration_ms=result.duration_ms,
                ))
                last_result = result

                events.append({
                    "phase": "execute",
                    "iteration": iteration,
                    "exit_code": result.exit_code,
                    "timed_out": result.timed_out,
                    "duration_ms": result.duration_ms,
                    "stdout_tail": result.stdout[-300:] if result.stdout else "",
                    "stderr_tail": result.stderr[-300:] if result.stderr else "",
                })

                # OBSERVE
                if result.success:
                    trace.record(ExecutionEvent(
                        step="complete", status="succeeded",
                        iteration=iteration,
                        message=f"Project executed successfully in {iteration} iteration(s)",
                        duration_ms=result.duration_ms,
                    ))
                    return AgentLoopResult(
                        status="succeeded",
                        iterations=iteration,
                        final_result=result,
                        repair_report=repair_report,
                        trace=trace,
                        events=events,
                    )

                # Classify failure
                failure_type = self._classify_failure(result)
                trace.record(ExecutionEvent(
                    step="observe", status="failed",
                    iteration=iteration,
                    message=f"Failure detected: {failure_type}",
                    details={
                        "failure_type": failure_type,
                        "exit_code": result.exit_code,
                        "stderr_summary": result.stderr[:300],
                    },
                ))
                events.append({"phase": "observe", "iteration": iteration,
                               "failure_type": failure_type})

                # REPAIR
                trace.record(ExecutionEvent(
                    step="repair", status="started",
                    iteration=iteration,
                    message=f"Attempting repair for: {failure_type}",
                ))

                repair_report = await self._repair(
                    project_id, repo_dir, result, failure_type, iteration
                )

                applied = [
                    f for f in (repair_report.fixes_applied or [])
                    if getattr(f, "applied", False)
                ]
                if applied:
                    trace.record(ExecutionEvent(
                        step="repair", status="succeeded",
                        iteration=iteration,
                        message=f"Applied {len(applied)} fix(es): "
                                + ", ".join(
                                    f"{getattr(f, 'file_path', '?')}: "
                                    f"{getattr(f, 'fix_description', '?')[:60]}"
                                    for f in applied
                                ),
                        details={"fixes_applied": len(applied)},
                    ))
                    events.append({
                        "phase": "repair",
                        "iteration": iteration,
                        "fixes_applied": len(applied),
                    })
                else:
                    trace.record(ExecutionEvent(
                        step="repair", status="failed",
                        iteration=iteration,
                        message="No fix could be applied, giving up",
                    ))
                    events.append({
                        "phase": "repair",
                        "iteration": iteration,
                        "fixes_applied": 0,
                        "message": "No fix available",
                    })
                    # Give up if no fix was possible
                    return AgentLoopResult(
                        status="failed",
                        iterations=iteration,
                        final_result=result,
                        repair_report=repair_report,
                        trace=trace,
                        error=f"Unable to repair: {failure_type}",
                        events=events,
                    )

            # Exhausted iterations
            trace.record(ExecutionEvent(
                step="complete", status="failed",
                iteration=iteration,
                message=f"Exhausted {self.max_iterations} iterations without success",
            ))
            return AgentLoopResult(
                status="max_iterations",
                iterations=iteration,
                final_result=last_result,
                repair_report=repair_report,
                trace=trace,
                error=f"Failed after {self.max_iterations} iterations",
                events=events,
            )

        except Exception as exc:
            logger.exception("Agent loop error: %s", exc)
            trace.record(ExecutionEvent(
                step="error", status="failed",
                message=f"Unexpected error: {exc}",
            ))
            return AgentLoopResult(
                status="error",
                error=str(exc),
                trace=trace,
                events=events,
            )

        finally:
            if sandbox_id:
                try:
                    await pool.release(sandbox_id)
                except Exception as exc:
                    logger.warning("Failed to release sandbox: %s", exc)

    # ---- plan ----

    async def _plan_command(
        self,
        repo_dir: str,
        goal: str,
        language: str,
    ) -> str:
        """Use LLM or heuristics to plan the execution command."""
        # Phase 1: Auto-discover command based on project structure
        auto_cmd = self._discover_command(repo_dir, language)
        if auto_cmd:
            logger.info("Auto-discovered command: %s", auto_cmd)
            return auto_cmd

        # Phase 2: Fall back to LLM planning
        try:
            return await self._llm_plan_command(repo_dir, goal, language)
        except Exception as exc:
            logger.warning("LLM planning failed (%s), using fallback", exc)
            return self._fallback_command(language)

    @staticmethod
    def _discover_command(repo_dir: str, language: str) -> Optional[str]:
        """Auto-discover the best execution command from project structure.

        Priority order:
        1. pytest if tests/ directory exists
        2. python src/main.py or python main.py
        3. python -c "compile all .py files for syntax check"
        """
        repo = Path(repo_dir)

        # Check for pytest
        test_dir = (
            repo / "tests" if (repo / "tests").is_dir()
            else repo / "test" if (repo / "test").is_dir()
            else None
        )
        if test_dir:
            # Check if pytest is configured
            has_pytest = (
                (repo / "pytest.ini").exists()
                or (repo / "pyproject.toml").exists()
                or (repo / "setup.cfg").exists()
                or (repo / "conftest.py").exists()
            )
            if has_pytest or any(test_dir.rglob("test_*.py")):
                return "python -m pytest tests/ -x --tb=short 2>&1"

        # Check for main entry point
        for entry in ["src/main.py", "main.py", "src/app.py", "app.py",
                       "src/run.py", "run.py"]:
            if (repo / entry).is_file():
                return f"python {entry}"

        # Default: syntax check all Python files
        py_files = list(repo.rglob("*.py"))
        if py_files:
            # Filter out venv/node_modules/etc
            filtered = [
                str(f.relative_to(repo))
                for f in py_files
                if not any(
                    skip in f.parts
                    for skip in (".venv", "venv", "node_modules", "__pycache__",
                                 ".git", "build", "dist", ".tox")
                )
                and "test" not in f.name.lower()  # skip test files for main exec
            ]
            if filtered:
                # Just run the most likely entry point found above
                pass

        return None

    async def _llm_plan_command(
        self,
        repo_dir: str,
        goal: str,
        language: str,
    ) -> str:
        """Use LLM to plan the execution command."""
        from app.llm.provider_client import ChatMessage, ProviderClient

        # Gather project context
        repo = Path(repo_dir)
        file_list = []
        for f in sorted(repo.rglob("*.py")):
            rel = str(f.relative_to(repo))
            if not any(s in rel for s in (".venv", "venv", "__pycache__", ".git")):
                file_list.append(rel)
            if len(file_list) >= 30:
                break

        main_content = ""
        for candidate in ["src/main.py", "main.py"]:
            candidate_path = repo / candidate
            if candidate_path.is_file():
                try:
                    main_content = candidate_path.read_text(
                        encoding="utf-8", errors="replace"
                    )[:2000]
                except Exception:
                    pass
                break

        prompt = (
            f"You are a code execution planner. Analyze the project and propose "
            f"a single shell command to validate it works correctly.\n\n"
            f"Goal: {goal or 'Make the project run successfully'}\n"
            f"Language: {language}\n\n"
            f"Files:\n"
            + "\n".join(f"  - {f}" for f in file_list[:30])
            + ("\n\nMain file content (first 2000 chars):\n```python\n"
               + main_content + "\n```" if main_content else "")
            + "\n\nReturn ONLY the shell command to execute. "
            "Prefer 'python -m pytest' if tests exist, "
            "otherwise 'python src/main.py' or equivalent. "
            "No explanations, just the command."
        )

        try:
            client = ProviderClient(self._provider_name)
            response = client.chat(
                messages=[ChatMessage(role="user", content=prompt)],
                model=self._model,
                temperature=0.1,
                max_tokens=200,
            )
            command = response.text.strip()
            # Clean up common LLM artifacts
            command = command.replace("```", "").replace("bash", "").strip()
            if not command or len(command) > 500:
                return self._fallback_command(language)
            return command
        except Exception:
            raise

    @staticmethod
    def _fallback_command(language: str) -> str:
        """Last-resort fallback command."""
        if language == "python":
            return (
                "python -c \""
                "import sys, os, py_compile; "
                "errors = []; "
                "[errors.append(str(f)) if py_compile.compile(str(f), doraise=False) is None else None "
                "for f in __import__('pathlib').Path('.').rglob('*.py') "
                "if '.venv' not in str(f) and 'venv' not in str(f)]; "
                "sys.exit(1) if errors else print(f'Checked {len(list(__import__(\"pathlib\").Path(\".\").rglob(\"*.py\")))} files, all OK')\""
            )
        return f"echo 'No entry point found for language: {language}' && exit 1"

    # ---- classify ----

    @staticmethod
    def _classify_failure(result: SandboxResult) -> str:
        """Classify the type of failure from stderr/stdout."""
        stderr = result.stderr.lower()
        stdout = result.stdout.lower()
        combined = stderr + stdout

        if result.timed_out:
            return "timeout"

        # Order matters: check specific patterns before generic ones
        checks = [
            ("syntax_error", r"syntaxerror"),
            ("indentation_error", r"indentationerror"),
            ("module_not_found", r"modulenotfounderror|no module named"),
            ("import_error", r"importerror"),
            ("attribute_error", r"attributeerror"),
            ("type_error", r"typeerror"),
            ("name_error", r"nameerror"),
            ("file_not_found", r"filenotfounderror|no such file"),
            ("key_error", r"keyerror"),
            ("value_error", r"valueerror"),
            ("assertion_error", r"assertionerror|assert"),
            ("runtime_error", r"runtimeerror"),
            ("permission_error", r"permissionerror|permission denied"),
            ("memory_error", r"memoryerror|out of memory"),
            ("test_failure", r"failed|failure|assert"),
            ("connection_error", r"connection.*error|connection refused"),
        ]

        for failure_type, pattern in checks:
            if re.search(pattern, combined):
                return failure_type

        if result.exit_code != 0:
            return f"non_zero_exit_code_{result.exit_code}"

        return "unknown"

    # ---- repair ----

    async def _repair(
        self,
        project_id: str,
        repo_dir: str,
        result: SandboxResult,
        failure_type: str,
        iteration: int,
    ):
        """Run CodeRepairService to fix the failed execution."""
        from app.services.code_repair_service import (
            CodeRepairService,
            AutoFixReport,
        )

        repair_svc = CodeRepairService(
            provider_name=self._provider_name,
            model=self._model,
        )

        failed_steps = [{
            "name": f"execution_iter_{iteration}_{failure_type}",
            "stderr": result.stderr,
            "stdout": result.stdout,
        }]

        # Run in thread to avoid blocking (auto_fix is synchronous)
        loop = asyncio.get_event_loop()
        report = await loop.run_in_executor(
            None,
            lambda: repair_svc.auto_fix(project_id, repo_dir, failed_steps),
        )
        return report


# ---- helpers ----

def _generate_trace_id() -> str:
    import uuid
    return f"agent_{uuid.uuid4().hex[:12]}"


# Need re for classify
import re


# ---- singleton ----

_loop: Optional[CodeAgentLoop] = None


async def get_code_agent_loop(
    pool: Optional[SandboxPool] = None,
    provider_name: str = "qwen",
    model: str = "qwen-max",
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    execution_timeout: int = DEFAULT_EXECUTION_TIMEOUT,
    backend: Optional[str] = None,
) -> CodeAgentLoop:
    """Get or create a CodeAgentLoop instance."""
    global _loop
    if _loop is None:
        _loop = CodeAgentLoop(
            pool=pool,
            provider_name=provider_name,
            model=model,
            max_iterations=max_iterations,
            execution_timeout=execution_timeout,
            backend=backend,
        )
    return _loop
