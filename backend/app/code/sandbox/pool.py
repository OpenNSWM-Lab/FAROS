"""
SandboxPool — Lifecycle manager for concurrent sandbox instances.

Provides a singleton pool that creates, tracks, and reaps sandboxes
across all backend types. Enforces concurrency limits and TTL-based
reclamation to prevent resource leaks.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

from .base import SandboxBackend
from .models import ActiveSandbox, SandboxResult

logger = logging.getLogger(__name__)

# Default limits
DEFAULT_MAX_ACTIVE = int(os.getenv("SANDBOX_MAX_CONCURRENT", "4"))
DEFAULT_TTL_SEC = int(os.getenv("SANDBOX_TTL_SEC", "3600"))
DEFAULT_BACKEND = os.getenv("SANDBOX_DEFAULT_BACKEND", "subprocess")


class SandboxPoolError(Exception):
    """Base exception for sandbox pool errors."""


class SandboxPoolExhausted(SandboxPoolError):
    """Raised when the pool has reached its maximum concurrent sandboxes."""


class SandboxNotFound(SandboxPoolError):
    """Raised when referencing a sandbox that doesn't exist in the pool."""


class BackendNotAvailable(SandboxPoolError):
    """Raised when the requested backend is not registered or unavailable."""


class SandboxPool:
    """Manages the lifecycle of sandbox instances across backend types.

    Responsibilities:
    - Register and select backends (docker, subprocess, future: k8s, firecracker)
    - Enforce maximum concurrent sandbox limit
    - Reap stale sandboxes that exceed their TTL
    - Provide a unified execute() interface
    """

    def __init__(
        self,
        max_active: int = DEFAULT_MAX_ACTIVE,
        default_backend: str = DEFAULT_BACKEND,
        ttl_sec: int = DEFAULT_TTL_SEC,
    ):
        self._backends: dict[str, SandboxBackend] = {}
        self._active: dict[str, ActiveSandbox] = {}
        self._max_active = max_active
        self._default_backend = default_backend
        self._ttl = ttl_sec
        self._lock = asyncio.Lock()

    # ---- backend registration ----

    def register_backend(self, backend: SandboxBackend) -> None:
        """Register a sandbox backend. Overwrites if already registered."""
        self._backends[backend.backend_type] = backend
        logger.info(
            "Registered sandbox backend: %s (available=%s)",
            backend.backend_type,
            backend.is_available(),
        )

    def unregister_backend(self, backend_type: str) -> None:
        """Remove a backend from the pool."""
        self._backends.pop(backend_type, None)

    def get_backend(self, backend_type: Optional[str] = None) -> SandboxBackend:
        """Get a registered backend, falling back to default."""
        bt = backend_type or self._default_backend
        backend = self._backends.get(bt)
        if backend is None:
            available = list(self._backends.keys())
            raise BackendNotAvailable(
                f"Backend '{bt}' not registered. Available: {available}"
            )
        if not backend.is_available():
            logger.warning("Backend '%s' reports it is not available", bt)
        return backend

    @property
    def available_backends(self) -> list[str]:
        """List registered backends that report as available."""
        return [
            name
            for name, b in self._backends.items()
            if b.is_available()
        ]

    @property
    def default_backend(self) -> str:
        """Auto-select the best available backend."""
        if self._default_backend in self.available_backends:
            return self._default_backend
        # Fall back to subprocess (always available)
        if "subprocess" in self.available_backends:
            return "subprocess"
        if self.available_backends:
            return self.available_backends[0]
        return "subprocess"

    # ---- lifecycle ----

    async def acquire(
        self,
        workspace_path: str,
        backend_type: Optional[str] = None,
        image: Optional[str] = None,
        env: Optional[dict] = None,
    ) -> str:
        """Acquire a sandbox for execution.

        Creates a new sandbox via the selected backend, tracking it
        in the active set. Raises SandboxPoolExhausted if the pool
        is full.

        Args:
            workspace_path: Host directory to mount as sandbox workspace.
            backend_type: Backend to use (default: auto-select).
            image: Docker image (Docker backend only).
            env: Extra environment variables.

        Returns:
            sandbox_id to use with execute() and release().
        """
        async with self._lock:
            self._reap_stale()

            if len(self._active) >= self._max_active:
                raise SandboxPoolExhausted(
                    f"Sandbox pool full ({len(self._active)}/{self._max_active}). "
                    "Wait for active sandboxes to complete or increase SANDBOX_MAX_CONCURRENT."
                )

            backend = self.get_backend(backend_type)
            sandbox_id = await backend.setup(workspace_path, image=image, env=env)

            now = time.time()
            self._active[sandbox_id] = ActiveSandbox(
                sandbox_id=sandbox_id,
                backend=backend.backend_type,
                workspace_path=workspace_path,
                created_at=now,
                last_used=now,
            )

            logger.info(
                "Sandbox acquired: %s (backend=%s, active=%d/%d)",
                sandbox_id,
                backend.backend_type,
                len(self._active),
                self._max_active,
            )
            return sandbox_id

    async def execute(
        self,
        sandbox_id: str,
        command: str,
        timeout: int = 300,
        env: Optional[dict] = None,
    ) -> SandboxResult:
        """Execute a command in an active sandbox.

        Args:
            sandbox_id: Identifier from acquire().
            command: Shell command to run.
            timeout: Max execution time in seconds.
            env: Extra environment variables.

        Returns:
            SandboxResult with exit code, output, and timing.
        """
        entry = self._active.get(sandbox_id)
        if entry is None:
            raise SandboxNotFound(f"Sandbox not active: {sandbox_id}")

        backend = self._backends[entry.backend]
        entry.last_used = time.time()

        result = await backend.execute(
            sandbox_id, command, timeout=timeout, env=env
        )
        return result

    async def release(self, sandbox_id: str) -> bool:
        """Release a sandbox, tearing it down and removing from tracking.

        Returns True if the sandbox was found and released.
        """
        async with self._lock:
            entry = self._active.pop(sandbox_id, None)
            if entry is None:
                logger.debug("Sandbox not found for release: %s", sandbox_id)
                return False

            backend = self._backends.get(entry.backend)
            if backend:
                try:
                    await backend.teardown(sandbox_id)
                except Exception as exc:
                    logger.warning(
                        "Error tearing down sandbox %s: %s", sandbox_id, exc
                    )

            logger.info(
                "Sandbox released: %s (active=%d/%d)",
                sandbox_id,
                len(self._active),
                self._max_active,
            )
            return True

    async def teardown_all(self) -> None:
        """Release all active sandboxes. Called on app shutdown."""
        async with self._lock:
            sandbox_ids = list(self._active.keys())

        for sid in sandbox_ids:
            await self.release(sid)

        logger.info("All sandboxes released (teardown_all complete)")

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def pool_info(self) -> dict:
        return {
            "active_count": len(self._active),
            "max_active": self._max_active,
            "default_backend": self._default_backend,
            "available_backends": self.available_backends,
            "active_sandboxes": [
                {
                    "id": e.sandbox_id,
                    "backend": e.backend,
                    "age_sec": round(e.age_sec(time.time()), 1),
                    "idle_sec": round(e.idle_sec(time.time()), 1),
                }
                for e in self._active.values()
            ],
        }

    # ---- internals ----

    def _reap_stale(self) -> int:
        """Remove sandboxes that have exceeded their TTL. Called under lock."""
        now = time.time()
        stale_ids = [
            sid
            for sid, entry in self._active.items()
            if entry.age_sec(now) > self._ttl
        ]
        for sid in stale_ids:
            logger.warning("Reaping stale sandbox: %s (age > %ds)", sid, self._ttl)
            self._active.pop(sid, None)
        return len(stale_ids)


# ---- singleton ----

_pool: Optional[SandboxPool] = None
_pool_lock = asyncio.Lock()


async def get_sandbox_pool(
    max_active: int = DEFAULT_MAX_ACTIVE,
    default_backend: str = DEFAULT_BACKEND,
    ttl_sec: int = DEFAULT_TTL_SEC,
) -> SandboxPool:
    """Get or create the global sandbox pool singleton.

    On first call, registers the SubprocessSandbox (always available)
    and attempts to register DockerSandbox if docker is installed.
    """
    global _pool
    if _pool is None:
        async with _pool_lock:
            if _pool is None:
                _pool = SandboxPool(
                    max_active=max_active,
                    default_backend=default_backend,
                    ttl_sec=ttl_sec,
                )

                # Always register subprocess backend
                from .subprocess_backend import SubprocessSandbox

                _pool.register_backend(SubprocessSandbox())

                # Try to register Docker backend
                try:
                    from .docker_backend import DockerSandbox

                    docker = DockerSandbox()
                    if docker.is_available():
                        _pool.register_backend(docker)
                        if default_backend == "docker" or _pool._default_backend == "subprocess":
                            _pool._default_backend = "docker"
                        logger.info("Docker sandbox backend available and registered")
                    else:
                        logger.info(
                            "Docker sandbox backend loaded but Docker is not available, "
                            "using subprocess backend"
                        )
                except ImportError:
                    logger.info(
                        "docker Python SDK not installed, Docker backend unavailable. "
                        "Install with: pip install docker"
                    )
                except Exception as exc:
                    logger.warning("Failed to load Docker backend: %s", exc)

    return _pool
