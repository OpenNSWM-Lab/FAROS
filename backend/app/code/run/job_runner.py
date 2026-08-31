"""
Job Runner - Executes code jobs in isolated workspaces.

Features:
- Async subprocess execution
- Timeout handling
- Log capture (stdout/stderr)
- Process cancellation
- Dangerous command filtering
"""

import os
import asyncio
import logging
import shlex
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable
from pathlib import Path

from app.core.paths import get_data_dir
from app.code.execution import execution_is_allowed, resolve_execution_profile

from .workspace import WorkspaceManager

logger = logging.getLogger(__name__)

# Base directory for artifacts
ARTIFACTS_DIR = str(get_data_dir() / "artifacts")

# Dangerous command patterns to block
DANGEROUS_PATTERNS = [
    "rm -rf /",
    "rm -rf /*",
    "rm -rf ~",
    "dd if=",
    "mkfs",
    ":(){:|:&};:",  # Fork bomb
    "curl | sh",
    "curl | bash",
    "wget | sh",
    "wget | bash",
    "> /dev/sd",
    "chmod -R 777 /",
    "chown -R",
]


class JobRunner:
    """Runs code jobs in isolated workspaces."""
    
    def __init__(
        self,
        workspace_manager: Optional[WorkspaceManager] = None,
        artifacts_dir: str = ARTIFACTS_DIR,
    ):
        self.workspace_manager = workspace_manager or WorkspaceManager()
        self.artifacts_dir = artifacts_dir
        os.makedirs(self.artifacts_dir, exist_ok=True)
        
        # Track running processes
        self._processes: Dict[str, asyncio.subprocess.Process] = {}
        self._sandboxes: Dict[str, str] = {}
    
    def is_command_safe(self, command: str) -> tuple[bool, str]:
        """
        Check if a command is safe to execute.
        
        Returns:
            Tuple of (is_safe, reason)
        """
        command_lower = command.lower()
        
        for pattern in DANGEROUS_PATTERNS:
            if pattern.lower() in command_lower:
                return False, f"Blocked dangerous pattern: {pattern}"
        
        return True, "Command appears safe"
    
    def get_artifact_dir(self, job_id: str, session_id: Optional[str] = None) -> str:
        """Get artifact directory for a job."""
        if session_id:
            path = os.path.join(self.artifacts_dir, session_id, job_id)
        else:
            path = os.path.join(self.artifacts_dir, job_id)
        os.makedirs(path, exist_ok=True)
        return path
    
    async def run_job(
        self,
        job_id: str,
        command: str,
        workspace_path: str,
        cwd_rel: Optional[str] = None,
        env_vars: Optional[Dict[str, str]] = None,
        timeout_sec: int = 300,
        on_stdout: Optional[Callable[[str], None]] = None,
        on_stderr: Optional[Callable[[str], None]] = None,
        on_status: Optional[Callable[[str, Dict], None]] = None,
    ) -> Dict[str, Any]:
        """
        Run a job command in the workspace.
        
        Args:
            job_id: Unique job identifier
            command: Command to execute
            workspace_path: Path to workspace
            cwd_rel: Relative working directory within workspace
            env_vars: Additional environment variables
            timeout_sec: Timeout in seconds
            on_stdout: Callback for stdout lines
            on_stderr: Callback for stderr lines
            on_status: Callback for status updates
            
        Returns:
            Dict with execution results
        """
        result = {
            "job_id": job_id,
            "success": False,
            "exit_code": None,
            "stdout_path": None,
            "stderr_path": None,
            "started_at": None,
            "ended_at": None,
            "duration_sec": None,
            "error": None,
            "execution": None,
        }

        if not execution_is_allowed():
            result["error"] = (
                "This server is a control node. Restore the private compute node "
                "before starting Code execution."
            )
            if on_status:
                on_status("blocked", {"reason": result["error"]})
            return result
        
        # Check command safety
        is_safe, reason = self.is_command_safe(command)
        if not is_safe:
            result["error"] = reason
            if on_status:
                on_status("blocked", {"reason": reason})
            return result
        
        # Determine working directory
        workspace_root = Path(workspace_path).resolve()
        cwd = workspace_root
        if cwd_rel:
            cwd = (workspace_root / cwd_rel).resolve()
            try:
                relative_cwd = cwd.relative_to(workspace_root).as_posix()
            except ValueError:
                result["error"] = "Working directory must stay inside the job workspace"
                return result
            cwd.mkdir(parents=True, exist_ok=True)
        else:
            relative_cwd = ""
        
        safe_env = {"PYTHONUNBUFFERED": "1"}
        for name, value in (env_vars or {}).items():
            upper_name = str(name).upper()
            if not str(name).replace("_", "a").isalnum():
                continue
            if any(marker in upper_name for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")):
                continue
            safe_env[str(name)] = str(value)
        
        # Setup artifact paths
        artifact_dir = self.get_artifact_dir(job_id)
        stdout_path = os.path.join(artifact_dir, "stdout.log")
        stderr_path = os.path.join(artifact_dir, "stderr.log")
        
        result["stdout_path"] = stdout_path
        result["stderr_path"] = stderr_path
        
        sandbox_id: Optional[str] = None
        try:
            if on_status:
                on_status("starting", {"command": command, "cwd": str(cwd)})
            
            result["started_at"] = datetime.utcnow().isoformat()
            start_time = datetime.utcnow()
            
            from app.code.sandbox import get_sandbox_pool

            profile = await asyncio.to_thread(
                resolve_execution_profile,
                workspace_root,
                str((env_vars or {}).get("FAROS_EXECUTION_PROFILE") or "auto"),
            )
            pool = await get_sandbox_pool()
            sandbox_id = await pool.acquire(
                str(workspace_root),
                env={"FAROS_EXECUTION_PROFILE": profile.name},
            )
            self._sandboxes[job_id] = sandbox_id
            run_command = command
            if relative_cwd:
                run_command = f"cd {shlex.quote(relative_cwd)} && {command}"
            if on_status:
                on_status("running", {
                    "sandboxId": sandbox_id,
                    "profile": profile.name,
                })
            sandbox_result = await pool.execute(
                sandbox_id,
                run_command,
                timeout=min(timeout_sec, profile.timeout_seconds),
                env=safe_env,
            )
            Path(stdout_path).write_text(sandbox_result.stdout, encoding="utf-8")
            Path(stderr_path).write_text(sandbox_result.stderr, encoding="utf-8")
            if on_stdout:
                for line in sandbox_result.stdout.splitlines():
                    on_stdout(line)
            if on_stderr:
                for line in sandbox_result.stderr.splitlines():
                    on_stderr(line)
            result["exit_code"] = sandbox_result.exit_code
            result["success"] = sandbox_result.success
            result["execution"] = {
                "backend": sandbox_result.backend,
                "nodeName": sandbox_result.execution_node,
                "profile": sandbox_result.profile,
                "resourceLimits": sandbox_result.resource_limits,
            }
            if sandbox_result.timed_out:
                result["error"] = f"Timeout after {min(timeout_sec, profile.timeout_seconds)} seconds"
                if on_status:
                    on_status("timeout", {"timeout_sec": min(timeout_sec, profile.timeout_seconds)})
            
            end_time = datetime.utcnow()
            result["ended_at"] = end_time.isoformat()
            result["duration_sec"] = int((end_time - start_time).total_seconds())
            
            if on_status:
                status = "succeeded" if result["success"] else "failed"
                on_status(status, {"exit_code": result["exit_code"]})
            
        except Exception as e:
            result["error"] = str(e)
            logger.error(f"Job {job_id} execution error: {e}")
            
            if on_status:
                on_status("error", {"error": str(e)})
                
        finally:
            if sandbox_id:
                try:
                    await pool.release(sandbox_id)
                except Exception:
                    logger.exception("Failed to release sandbox for job %s", job_id)
            self._processes.pop(job_id, None)
            self._sandboxes.pop(job_id, None)
        
        return result
    
    async def _read_output(
        self,
        process: asyncio.subprocess.Process,
        stdout_file,
        stderr_file,
        on_stdout: Optional[Callable[[str], None]],
        on_stderr: Optional[Callable[[str], None]],
    ):
        """Read process output streams."""
        
        async def read_stream(stream, file, callback):
            while True:
                line = await stream.readline()
                if not line:
                    break
                decoded = line.decode('utf-8', errors='replace')
                file.write(decoded)
                file.flush()
                if callback:
                    callback(decoded.rstrip())
        
        await asyncio.gather(
            read_stream(process.stdout, stdout_file, on_stdout),
            read_stream(process.stderr, stderr_file, on_stderr),
        )
        
        await process.wait()
        return None, None
    
    async def stop_job(self, job_id: str) -> bool:
        """
        Stop a running job.
        
        Args:
            job_id: Job identifier
            
        Returns:
            True if job was stopped
        """
        sandbox_id = self._sandboxes.get(job_id)
        if sandbox_id:
            try:
                from app.code.sandbox import get_sandbox_pool

                pool = await get_sandbox_pool()
                stopped = await pool.release(sandbox_id)
                self._sandboxes.pop(job_id, None)
                return stopped
            except Exception as exc:
                logger.error("Error stopping sandbox job %s: %s", job_id, exc)
                return False

        process = self._processes.get(job_id)
        if not process:
            return False
        try:
            # Try graceful termination first
            process.terminate()
            
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                # Force kill
                process.kill()
                await process.wait()
            
            logger.info(f"Stopped job {job_id}")
            return True
            
        except ProcessLookupError:
            return False
        except Exception as e:
            logger.error(f"Error stopping job {job_id}: {e}")
            return False
    
    def is_job_running(self, job_id: str) -> bool:
        """Check if a job is currently running."""
        return job_id in self._processes
    
    def get_log_tail(
        self,
        job_id: str,
        log_type: str = "stdout",
        lines: int = 100,
    ) -> List[str]:
        """
        Get the last N lines from a job's log.
        
        Args:
            job_id: Job identifier
            log_type: "stdout" or "stderr"
            lines: Number of lines to return
            
        Returns:
            List of log lines
        """
        artifact_dir = self.get_artifact_dir(job_id)
        log_path = os.path.join(artifact_dir, f"{log_type}.log")
        
        if not os.path.exists(log_path):
            return []
        
        try:
            with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                all_lines = f.readlines()
                return all_lines[-lines:] if len(all_lines) > lines else all_lines
        except Exception as e:
            logger.error(f"Error reading log {log_path}: {e}")
            return []
    
    def get_full_log(self, job_id: str, log_type: str = "stdout") -> str:
        """Get full log content."""
        artifact_dir = self.get_artifact_dir(job_id)
        log_path = os.path.join(artifact_dir, f"{log_type}.log")
        
        if not os.path.exists(log_path):
            return ""
        
        try:
            with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                return f.read()
        except Exception as e:
            logger.error(f"Error reading log {log_path}: {e}")
            return ""
