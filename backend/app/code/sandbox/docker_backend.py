"""
DockerSandbox — Docker-based isolated code execution backend.

Creates ephemeral Docker containers with strict resource limits:
- No network access (network_mode=none)
- Memory limit (default 512m)
- CPU quota (default 0.5 core)
- PID limit (prevents fork bombs)
- Read-only rootfs with writable /workspace
- Automatic cleanup on teardown

Uses the docker Python SDK for container creation and docker CLI
(via asyncio subprocess) for command execution to avoid blocking.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from .base import SandboxBackend
from .models import SandboxResult, ResourceUsage
from app.code.execution.resources import get_compute_snapshot, resolve_execution_profile

logger = logging.getLogger(__name__)

# ---- defaults (overridable via env) ----
DEFAULT_DOCKER_IMAGE = os.getenv("SANDBOX_DOCKER_IMAGE", "python:3.12-slim")
DEFAULT_MEM_LIMIT = os.getenv("SANDBOX_MEM_LIMIT", "512m")
DEFAULT_CPU_QUOTA = int(os.getenv("SANDBOX_CPU_QUOTA", "50000"))  # 0.5 core
DEFAULT_PIDS_LIMIT = int(os.getenv("SANDBOX_PIDS_LIMIT", "64"))
DEFAULT_TIMEOUT_GRACE = int(os.getenv("SANDBOX_TIMEOUT_GRACE", "5"))  # grace seconds for kill


class DockerSandbox(SandboxBackend):
    """Execute commands inside isolated Docker containers.

    Each sandbox = one ephemeral container:
    - Base image: python:3.12-slim (configurable)
    - Workspace mounted at /workspace
    - Network disabled
    - Resource limits enforced
    - Auto-destroyed on teardown()

    Uses docker CLI for async exec (docker SDK's exec_run is synchronous).
    """

    backend_type = "docker"

    def __init__(
        self,
        image: str = DEFAULT_DOCKER_IMAGE,
        mem_limit: str = DEFAULT_MEM_LIMIT,
        cpu_quota: int = DEFAULT_CPU_QUOTA,
        pids_limit: int = DEFAULT_PIDS_LIMIT,
    ):
        self._image = image
        self._mem_limit = mem_limit
        self._cpu_quota = cpu_quota
        self._pids_limit = pids_limit
        self._docker_available: Optional[bool] = None

    # ---- SandboxBackend interface ----

    async def setup(
        self,
        workspace_path: str,
        image: Optional[str] = None,
        env: Optional[dict] = None,
    ) -> str:
        """Create and start a Docker container for isolated execution.

        Container configuration:
        - Runs `sleep infinity` to stay alive for exec commands
        - Workspace bind-mounted read-write at /workspace
        - Network disabled, resource limits enforced
        - Container name: faros-sandbox-{uuid_hex[:12]}
        """
        requested_profile = str((env or {}).get("FAROS_EXECUTION_PROFILE") or "auto")
        profile = await asyncio.to_thread(
            resolve_execution_profile,
            workspace_path,
            requested_profile,
        )
        image = image or profile.image or self._image
        sandbox_id = f"faros-sandbox-{uuid.uuid4().hex[:12]}"

        # Ensure workspace path is absolute and exists
        workspace_path = os.path.abspath(workspace_path)
        if not os.path.isdir(workspace_path):
            os.makedirs(workspace_path, exist_ok=True)

        await self._ensure_image(image)

        gpu_index: Optional[int] = None
        if profile.gpu_count:
            gpu_index = self._acquire_gpu()
            if gpu_index is None:
                raise RuntimeError("No GPU is currently available for the requested execution profile")

        # Build docker run command
        cmd = [
            "docker", "run",
            "--detach",
            "--name", sandbox_id,
            "--label", "faros.sandbox=true",
            "--label", f"faros.profile={profile.name}",
            "--workdir", "/workspace",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--env", "HOME=/tmp",
            "--volume", f"{workspace_path}:/workspace:rw",
            "--network", "none",
            "--memory", profile.memory_limit,
            "--cpus", str(profile.cpu_limit),
            "--pids-limit", str(profile.pids_limit),
            "--shm-size", "2g" if profile.name == "gpu" else "512m",
            "--security-opt", "no-new-privileges",
            "--cap-drop", "ALL",
            "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=256m",
        ]
        if gpu_index is not None:
            cmd.extend(["--gpus", f"device={gpu_index}"])
        cmd.extend([image, "sleep", "infinity"])

        logger.info("Creating Docker sandbox: %s (image=%s)", sandbox_id, image)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=30
            )

            if proc.returncode != 0:
                stderr_str = self._safe_decode(stderr)
                raise RuntimeError(
                    f"Docker container creation failed (exit={proc.returncode}): {stderr_str[:500]}"
                )

            container_id = self._safe_decode(stdout).strip()[:64]
            if not container_id:
                # Try to get ID from container name
                container_id = sandbox_id

            logger.info("Docker sandbox created: %s (container=%s)", sandbox_id, container_id)

            # Store the mapping from sandbox_id to container name/ID
            self._containers[sandbox_id] = {
                "name": sandbox_id,
                "id": container_id,
                "workspace": workspace_path,
                "profile": profile,
                "gpu_index": gpu_index,
            }

            return sandbox_id

        except asyncio.TimeoutError:
            # Container creation timed out — try to clean up
            await self._force_remove_container(sandbox_id)
            if gpu_index is not None:
                self._release_gpu(gpu_index)
            raise RuntimeError(f"Docker container creation timed out for {sandbox_id}")

        except Exception:
            await self._force_remove_container(sandbox_id)
            if gpu_index is not None:
                self._release_gpu(gpu_index)
            raise

    async def execute(
        self,
        sandbox_id: str,
        command: str,
        timeout: int = 300,
        env: Optional[dict] = None,
    ) -> SandboxResult:
        """Execute a command inside the Docker container via docker exec."""
        container_info = self._containers.get(sandbox_id)
        if container_info is None:
            return SandboxResult(
                exit_code=-1,
                stdout="",
                stderr=f"Sandbox not found: {sandbox_id}",
                command=command,
            )

        container_name = container_info["name"]
        profile = container_info["profile"]
        if container_info.get("terminated"):
            return SandboxResult(
                exit_code=-1,
                stdout="",
                stderr="Sandbox was terminated after a previous command exceeded its time limit",
                command=command,
                backend=self.backend_type,
                execution_node=get_compute_snapshot()["nodeName"],
                profile=profile.name,
                resource_limits=profile.to_public_dict(),
            )
        start = time.monotonic()

        # Build docker exec command
        exec_cmd = ["docker", "exec"]

        # Add environment variables
        exec_env = {"PYTHONUNBUFFERED": "1"}
        if env:
            exec_env.update(env)
        for key, val in exec_env.items():
            exec_cmd.extend(["-e", f"{key}={val}"])

        # Target container and shell
        bounded_command = (
            f"timeout --signal=TERM --kill-after={DEFAULT_TIMEOUT_GRACE}s "
            f"{max(1, timeout)}s sh -c {shlex.quote(command)}"
        )
        exec_cmd.extend([container_name, "sh", "-c", bounded_command])

        logger.debug("Docker exec: %s", " ".join(exec_cmd[:6]) + " ...")

        try:
            proc = await asyncio.create_subprocess_exec(
                *exec_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout + DEFAULT_TIMEOUT_GRACE + 5
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Docker exec timed out after %ds: %s (sandbox=%s)",
                    timeout, command[:120], sandbox_id
                )
                try:
                    proc.kill()
                    await asyncio.wait_for(proc.wait(), timeout=DEFAULT_TIMEOUT_GRACE)
                except Exception:
                    pass
                # Killing docker exec only terminates the client. Remove the
                # container to guarantee that the experiment process cannot
                # survive in the background and consume CPU/GPU resources.
                await self._force_remove_container(container_name)
                container_info["terminated"] = True
                duration_ms = int((time.monotonic() - start) * 1000)
                return SandboxResult(
                    exit_code=-1,
                    stdout="",
                    stderr=f"Command timed out after {timeout}s",
                    duration_ms=duration_ms,
                    timed_out=True,
                    command=command,
                    backend=self.backend_type,
                    execution_node=get_compute_snapshot()["nodeName"],
                    profile=profile.name,
                    resource_limits=profile.to_public_dict(),
                )

            duration_ms = int((time.monotonic() - start) * 1000)
            command_timed_out = proc.returncode == 124

            return SandboxResult(
                exit_code=proc.returncode or 0,
                stdout=self._safe_decode(stdout_bytes),
                stderr=self._safe_decode(stderr_bytes),
                duration_ms=duration_ms,
                timed_out=command_timed_out,
                command=command,
                backend=self.backend_type,
                execution_node=get_compute_snapshot()["nodeName"],
                profile=profile.name,
                resource_limits=profile.to_public_dict(),
            )

        except FileNotFoundError:
            duration_ms = int((time.monotonic() - start) * 1000)
            return SandboxResult(
                exit_code=-1,
                stdout="",
                stderr="Docker CLI not found. Is Docker Desktop installed and running?",
                duration_ms=duration_ms,
                command=command,
                backend=self.backend_type,
                execution_node=get_compute_snapshot()["nodeName"],
                profile=profile.name,
                resource_limits=profile.to_public_dict(),
            )
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.error("Docker exec error: %s", exc)
            return SandboxResult(
                exit_code=-1,
                stdout="",
                stderr=f"Docker execution error: {exc}",
                duration_ms=duration_ms,
                command=command,
                backend=self.backend_type,
                execution_node=get_compute_snapshot()["nodeName"],
                profile=profile.name,
                resource_limits=profile.to_public_dict(),
            )

    async def read_file(self, sandbox_id: str, path: str) -> str:
        """Read a file from inside the Docker container via docker cp + host fs."""
        container_info = self._containers.get(sandbox_id)
        if container_info is None:
            raise ValueError(f"Sandbox not found: {sandbox_id}")

        # With bind mount, we can read directly from the host workspace
        workspace = container_info["workspace"]
        full_path = os.path.normpath(os.path.join(workspace, path))

        # Security: ensure path is within workspace
        if os.path.commonpath((os.path.realpath(workspace), os.path.realpath(full_path))) != os.path.realpath(workspace):
            raise ValueError(f"Path traversal detected: {path}")

        if not os.path.isfile(full_path):
            raise FileNotFoundError(f"File not found in sandbox: {path}")

        return Path(full_path).read_text(encoding="utf-8", errors="replace")

    async def write_file(self, sandbox_id: str, path: str, content: str) -> None:
        """Write a file into the sandbox. With bind mount, writes directly to host."""
        container_info = self._containers.get(sandbox_id)
        if container_info is None:
            raise ValueError(f"Sandbox not found: {sandbox_id}")

        workspace = container_info["workspace"]
        full_path = os.path.normpath(os.path.join(workspace, path))

        # Security: ensure path is within workspace
        if os.path.commonpath((os.path.realpath(workspace), os.path.realpath(full_path))) != os.path.realpath(workspace):
            raise ValueError(f"Path traversal detected: {path}")

        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        Path(full_path).write_text(content, encoding="utf-8")

    async def teardown(self, sandbox_id: str) -> None:
        """Stop and remove the Docker container. Idempotent."""
        container_info = self._containers.pop(sandbox_id, None)
        if container_info is None:
            logger.debug("Sandbox already removed or never created: %s", sandbox_id)
            return

        container_name = container_info["name"]
        await self._force_remove_container(container_name)
        gpu_index = container_info.get("gpu_index")
        if gpu_index is not None:
            self._release_gpu(int(gpu_index))
        logger.info("Docker sandbox torn down: %s", sandbox_id)

    async def get_resource_usage(self, sandbox_id: str) -> ResourceUsage:
        """Get resource usage via docker stats (best-effort)."""
        container_info = self._containers.get(sandbox_id)
        if container_info is None:
            return ResourceUsage()

        container_name = container_info["name"]
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "stats", container_name,
                "--no-stream", "--format", "{{.CPUPerc}}|{{.MemUsage}}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            output = self._safe_decode(stdout).strip()

            cpu_str, mem_str = "", ""
            if "|" in output:
                cpu_str, mem_str = output.rsplit("|", 1)[0], output.split("|")[1]

            cpu = float(re.sub(r"[^0-9.]", "", cpu_str) or "0")
            mem_match = re.search(r"([\d.]+)\s*MiB", mem_str)
            mem = float(mem_match.group(1)) if mem_match else 0.0

            return ResourceUsage(cpu_percent=cpu, memory_mb=mem)
        except Exception:
            return ResourceUsage()

    # ---- availability ----

    def is_available(self) -> bool:
        """Check if Docker is installed and the daemon is reachable (synchronous check)."""
        if self._docker_available is not None:
            return self._docker_available

        # Check if docker CLI exists
        if not shutil.which("docker"):
            logger.info("Docker CLI not found in PATH")
            self._docker_available = False
            return False

        # Check if daemon is running (quick sync check via subprocess)
        import subprocess as _sp
        try:
            result = _sp.run(
                ["docker", "info"],
                stdout=_sp.DEVNULL,
                stderr=_sp.DEVNULL,
                timeout=5,
            )
            self._docker_available = result.returncode == 0
        except Exception:
            self._docker_available = False

        if self._docker_available:
            # Schedule async image check but don't block on it
            logger.debug("Docker is available, sandbox ready")
        else:
            logger.info("Docker daemon not reachable")

        return self._docker_available

    async def _ensure_image(self, image: Optional[str] = None) -> None:
        """Pull the base image if not already present."""
        image = image or self._image
        if not image:
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "image", "inspect", image,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.communicate(), timeout=5)
            if proc.returncode != 0:
                logger.info("Pulling Docker image: %s ...", image)
                pull_proc = await asyncio.create_subprocess_exec(
                    "docker", "pull", image,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(pull_proc.communicate(), timeout=300)
                if pull_proc.returncode != 0:
                    raise RuntimeError(f"Failed to pull sandbox image {image}")
                else:
                    logger.info("Docker image pulled: %s", image)
        except Exception as exc:
            raise RuntimeError(f"Sandbox image is unavailable: {image}: {exc}") from exc

    # ---- internals ----

    _containers: dict[str, dict] = {}  # sandbox_id -> {name, id, workspace}
    _gpu_lock = threading.Lock()
    _allocated_gpus: set[int] = set()

    @classmethod
    def _acquire_gpu(cls) -> Optional[int]:
        with cls._gpu_lock:
            try:
                min_free_mib = max(1, int(os.getenv("FAROS_GPU_MIN_FREE_MIB", "8192")))
            except ValueError:
                min_free_mib = 8192
            try:
                max_utilization = max(
                    1,
                    min(100, int(os.getenv("FAROS_GPU_MAX_UTILIZATION", "80"))),
                )
            except ValueError:
                max_utilization = 80
            inventory = sorted(
                get_compute_snapshot(refresh=True).get("gpus", []),
                key=lambda item: (-int(item.get("memoryFreeMiB") or 0), int(item.get("index") or 0)),
            )
            for gpu in inventory:
                index = int(gpu["index"])
                if (
                    index not in cls._allocated_gpus
                    and int(gpu.get("memoryFreeMiB") or 0) >= min_free_mib
                    and int(gpu.get("utilizationPercent") or 0) <= max_utilization
                ):
                    cls._allocated_gpus.add(index)
                    return index
        return None

    @classmethod
    def _release_gpu(cls, index: int) -> None:
        with cls._gpu_lock:
            cls._allocated_gpus.discard(index)

    @staticmethod
    async def _force_remove_container(name: str) -> None:
        """Force-remove a Docker container by name or ID."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
        except Exception as exc:
            logger.warning("Failed to force-remove container %s: %s", name, exc)

    @staticmethod
    def _safe_decode(data: bytes) -> str:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("utf-8", errors="replace")
