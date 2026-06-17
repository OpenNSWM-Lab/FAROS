"""
Sandbox Abstraction Layer — Pluggable execution backends for code agent.

Provides a unified interface for executing code in isolated environments
(Docker, subprocess, future: Firecracker, K8s, WASM).

Usage:
    from app.code.sandbox import get_sandbox_pool
    pool = await get_sandbox_pool()
    sid = await pool.acquire("/path/to/workspace")
    result = await pool.execute(sid, "python main.py")
    await pool.release(sid)
"""

from .models import SandboxResult, ResourceUsage, ActiveSandbox
from .base import SandboxBackend
from .subprocess_backend import SubprocessSandbox
from .pool import SandboxPool, get_sandbox_pool
from .trace import ExecutionEvent, ExecutionTrace

__all__ = [
    "SandboxBackend",
    "SandboxResult",
    "ResourceUsage",
    "ActiveSandbox",
    "SandboxPool",
    "SubprocessSandbox",
    "get_sandbox_pool",
    "ExecutionEvent",
    "ExecutionTrace",
]
