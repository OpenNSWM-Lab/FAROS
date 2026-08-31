"""Resource-aware execution helpers shared by Code and experiments."""

from .resources import (
    ExecutionProfile,
    execution_is_allowed,
    get_compute_snapshot,
    resolve_execution_profile,
)

__all__ = [
    "ExecutionProfile",
    "execution_is_allowed",
    "get_compute_snapshot",
    "resolve_execution_profile",
]
