"""
Sandbox data models — Result structures shared across all backends.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SandboxResult:
    """Result of executing a command inside a sandbox."""

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int = 0
    timed_out: bool = False
    command: str = ""

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self) -> dict:
        return {
            "exit_code": self.exit_code,
            "stdout": self.stdout[-5000:] if len(self.stdout) > 5000 else self.stdout,
            "stderr": self.stderr[-5000:] if len(self.stderr) > 5000 else self.stderr,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
            "command": self.command,
            "success": self.success,
        }


@dataclass
class ResourceUsage:
    """Resource usage statistics for a sandbox."""

    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    disk_mb: float = 0.0
    duration_sec: float = 0.0

    def to_dict(self) -> dict:
        return {
            "cpu_percent": round(self.cpu_percent, 1),
            "memory_mb": round(self.memory_mb, 1),
            "disk_mb": round(self.disk_mb, 1),
            "duration_sec": round(self.duration_sec, 1),
        }


@dataclass
class ActiveSandbox:
    """Metadata for an active sandbox tracked by the pool."""

    sandbox_id: str
    backend: str  # "docker" | "subprocess"
    workspace_path: str
    created_at: float  # time.time()
    last_used: float  # time.time()

    def age_sec(self, now: float) -> float:
        return now - self.created_at

    def idle_sec(self, now: float) -> float:
        return now - self.last_used
