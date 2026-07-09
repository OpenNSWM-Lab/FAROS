"""
SubprocessSandbox — Local subprocess execution backend.

Wraps asyncio.create_subprocess_shell for lightweight, host-level execution.
Includes safety checks, timeout enforcement, and workspace management.

This is the fallback backend when Docker is unavailable.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Optional

from .base import SandboxBackend
from .models import SandboxResult, ResourceUsage

logger = logging.getLogger(__name__)

# ---- Dangerous command patterns (same as JobRunner) ----
DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    (r"rm\s+-rf\s+/", "rm -rf /"),
    (r"dd\s+if=", "dd (raw disk write)"),
    (r"mkfs\.", "mkfs (format filesystem)"),
    (r":\(\)\s*\{", "fork bomb"),
    (r"curl\s+.*\|\s*(ba)?sh", "piped curl to shell"),
    (r"wget\s+.*\|\s*(ba)?sh", "piped wget to shell"),
    (r"chmod\s+-R\s+777\s+/", "chmod -R 777 /"),
    (r"chown\s+-R\s+\w+\s+/", "chown -R on root"),
    (r">\s*/dev/sda", "overwrite disk device"),
    (r"mkfs\s+/dev/", "format disk device"),
    (r"(shutdown|reboot|halt|poweroff)", "system power command"),
    (r"python\s+.*-c\s+.*__import__\('os'\)", "suspicious Python import"),
    (r"eval\s+", "eval (potential code injection)"),
]


class SubprocessSandbox(SandboxBackend):
    """Execute commands via asyncio subprocess on the host.

    Provides filesystem isolation via workspace directory copying,
    safety checks, timeout enforcement, and output capture.

    This is a compatible fallback — no true OS-level isolation.
    """

    backend_type = "subprocess"

    def __init__(self, max_timeout: int = 600, log_output_limit: int = 5000):
        self._max_timeout = max_timeout
        self._log_output_limit = log_output_limit
        self._workspaces: dict[str, str] = {}  # sandbox_id -> workspace_path

    # ---- SandboxBackend interface ----

    async def setup(
        self,
        workspace_path: str,
        image: Optional[str] = None,
        env: Optional[dict] = None,
    ) -> str:
        """Create an isolated workspace copy for this sandbox.

        The workspace is a copy of workspace_path to prevent
        one sandbox from affecting another.
        """
        import uuid

        sandbox_id = f"subproc_{uuid.uuid4().hex[:12]}"
        sandbox_dir = os.path.join(
            os.path.dirname(workspace_path), f".sandbox_{sandbox_id}"
        )
        if os.path.exists(sandbox_dir):
            shutil.rmtree(sandbox_dir, ignore_errors=True)

        try:
            self._copy_workspace(workspace_path, sandbox_dir)
        except Exception:
            # If copy fails, use original path directly
            logger.warning(
                "Workspace copy failed, using original path: %s", workspace_path
            )
            sandbox_dir = workspace_path

        self._workspaces[sandbox_id] = sandbox_dir
        logger.info("SubprocessSandbox created: %s -> %s", sandbox_id, sandbox_dir)
        return sandbox_id

    async def execute(
        self,
        sandbox_id: str,
        command: str,
        timeout: int = 300,
        env: Optional[dict] = None,
    ) -> SandboxResult:
        """Execute command via asyncio subprocess with safety check and timeout."""
        workspace = self._workspaces.get(sandbox_id)
        if workspace is None:
            return SandboxResult(
                exit_code=-1,
                stdout="",
                stderr=f"Sandbox not found: {sandbox_id}",
                command=command,
            )

        # Safety check
        is_safe, reason = self._check_command_safety(command)
        if not is_safe:
            return SandboxResult(
                exit_code=-1,
                stdout="",
                stderr=f"Command blocked: {reason}",
                command=command,
            )

        timeout = min(timeout, self._max_timeout)
        start = time.monotonic()

        # Build environment
        proc_env = os.environ.copy()
        proc_env["PYTHONUNBUFFERED"] = "1"
        if env:
            proc_env.update(env)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workspace,
                env=proc_env,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                logger.warning("Sandbox command timed out after %ds: %s", timeout, command[:120])
                try:
                    proc.kill()
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except Exception:
                    pass
                duration_ms = int((time.monotonic() - start) * 1000)
                return SandboxResult(
                    exit_code=-1,
                    stdout="",
                    stderr=f"Command timed out after {timeout}s",
                    duration_ms=duration_ms,
                    timed_out=True,
                    command=command,
                )

            duration_ms = int((time.monotonic() - start) * 1000)

            stdout = self._safe_decode(stdout_bytes)
            stderr = self._safe_decode(stderr_bytes)

            return SandboxResult(
                exit_code=proc.returncode or 0,
                stdout=stdout,
                stderr=stderr,
                duration_ms=duration_ms,
                timed_out=False,
                command=command,
            )

        except FileNotFoundError:
            duration_ms = int((time.monotonic() - start) * 1000)
            return SandboxResult(
                exit_code=-1,
                stdout="",
                stderr=f"Command not found or executable missing: {command[:120]}",
                duration_ms=duration_ms,
                command=command,
            )
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.error("Sandbox execution error: %s", exc)
            return SandboxResult(
                exit_code=-1,
                stdout="",
                stderr=f"Execution error: {exc}",
                duration_ms=duration_ms,
                command=command,
            )

    async def read_file(self, sandbox_id: str, path: str) -> str:
        workspace = self._workspaces.get(sandbox_id)
        if workspace is None:
            raise ValueError(f"Sandbox not found: {sandbox_id}")

        full_path = os.path.join(workspace, path)
        if not os.path.isfile(full_path):
            raise FileNotFoundError(f"File not found in sandbox: {path}")

        return Path(full_path).read_text(encoding="utf-8", errors="replace")

    async def write_file(self, sandbox_id: str, path: str, content: str) -> None:
        workspace = self._workspaces.get(sandbox_id)
        if workspace is None:
            raise ValueError(f"Sandbox not found: {sandbox_id}")

        full_path = os.path.join(workspace, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        Path(full_path).write_text(content, encoding="utf-8")

    async def teardown(self, sandbox_id: str) -> None:
        workspace = self._workspaces.pop(sandbox_id, None)
        if workspace and workspace.endswith(sandbox_id):
            try:
                shutil.rmtree(workspace, ignore_errors=True)
            except Exception as exc:
                logger.warning("Failed to clean up sandbox workspace: %s", exc)
        logger.info("SubprocessSandbox torn down: %s", sandbox_id)

    async def get_resource_usage(self, sandbox_id: str) -> ResourceUsage:
        # subprocess backend can't reliably measure resource usage
        return ResourceUsage()

    # ---- safety ----

    def _check_command_safety(self, command: str) -> tuple[bool, str]:
        """Check if a command is safe to execute. Returns (is_safe, reason)."""
        cmd_lower = command.lower().strip()
        for pattern, description in DANGEROUS_PATTERNS:
            if re.search(pattern, cmd_lower):
                logger.warning("Blocked dangerous command: %s (%s)", command[:100], description)
                return False, description
        return True, ""

    # ---- helpers ----

    @staticmethod
    def _copy_workspace(source: str, dest: str) -> None:
        """Copy workspace files, excluding large/generated directories."""
        exclude_dirs = {
            ".git", "node_modules", "__pycache__", ".venv", "venv",
            ".env", "dist", "build", ".next", ".cache", "coverage",
            ".pytest_cache", ".mypy_cache", ".tox", ".eggs",
            "*.egg-info",
        }
        max_file_size = 10 * 1024 * 1024  # 10 MB

        os.makedirs(dest, exist_ok=True)
        for item in os.listdir(source):
            src_path = os.path.join(source, item)
            dst_path = os.path.join(dest, item)

            if item in exclude_dirs or item.endswith(".egg-info"):
                continue

            try:
                if os.path.isdir(src_path):
                    shutil.copytree(
                        src_path, dst_path,
                        symlinks=False,
                        ignore=shutil.ignore_patterns(*exclude_dirs),
                        dirs_exist_ok=True,
                    )
                elif os.path.isfile(src_path):
                    size = os.path.getsize(src_path)
                    if size <= max_file_size:
                        shutil.copy2(src_path, dst_path)
                # Skip symlinks, sockets, etc.
            except (OSError, shutil.Error) as exc:
                logger.debug("Skipped %s: %s", src_path, exc)

    @staticmethod
    def _safe_decode(data: bytes) -> str:
        """Decode bytes to string, replacing invalid characters."""
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("utf-8", errors="replace")
