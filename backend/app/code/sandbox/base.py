"""
SandboxBackend — Abstract base class for all sandbox execution backends.

Each backend provides isolated command execution with lifecycle management.
Implementations: DockerSandbox, SubprocessSandbox, (future) FirecrackerSandbox.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from .models import SandboxResult, ResourceUsage


class SandboxBackend(ABC):
    """Pluggable sandbox execution backend.

    Lifecycle: setup() → execute()* → teardown()
    Each sandbox_id is a unique reference returned by setup().
    """

    # Backend identifier used in config and pool registration
    backend_type: str = "base"

    @abstractmethod
    async def setup(
        self,
        workspace_path: str,
        image: Optional[str] = None,
        env: Optional[dict] = None,
    ) -> str:
        """Create and start an isolated execution environment.

        Args:
            workspace_path: Host path to mount as the sandbox working directory.
            image: Optional container image (Docker backend only).
            env: Optional environment variables to set in the sandbox.

        Returns:
            sandbox_id: Unique identifier for this sandbox instance.
        """
        ...

    @abstractmethod
    async def execute(
        self,
        sandbox_id: str,
        command: str,
        timeout: int = 300,
        env: Optional[dict] = None,
    ) -> SandboxResult:
        """Execute a command inside the sandbox.

        Args:
            sandbox_id: Identifier returned by setup().
            command: Shell command to execute.
            timeout: Maximum execution time in seconds.
            env: Optional extra environment variables.

        Returns:
            SandboxResult with exit_code, stdout, stderr, timing.
        """
        ...

    @abstractmethod
    async def read_file(self, sandbox_id: str, path: str) -> str:
        """Read a file from inside the sandbox.

        Args:
            sandbox_id: Identifier returned by setup().
            path: Path relative to the sandbox working directory.

        Returns:
            File content as string.
        """
        ...

    @abstractmethod
    async def write_file(self, sandbox_id: str, path: str, content: str) -> None:
        """Write a file into the sandbox.

        Args:
            sandbox_id: Identifier returned by setup().
            path: Path relative to the sandbox working directory.
            content: Content to write.
        """
        ...

    @abstractmethod
    async def teardown(self, sandbox_id: str) -> None:
        """Destroy the sandbox and release all resources.

        Must be idempotent — calling teardown() on an already-destroyed
        sandbox should not raise an error.

        Args:
            sandbox_id: Identifier returned by setup().
        """
        ...

    @abstractmethod
    async def get_resource_usage(self, sandbox_id: str) -> ResourceUsage:
        """Get resource usage statistics for a sandbox.

        Args:
            sandbox_id: Identifier returned by setup().

        Returns:
            ResourceUsage with CPU, memory, disk, and duration.
        """
        ...

    def is_available(self) -> bool:
        """Check whether this backend is usable on the current system.

        Override in subclasses to detect Docker, Firecracker, etc.
        Returns True by default for subprocess backend.
        """
        return True
