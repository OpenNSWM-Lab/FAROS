from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.faros.errors import FarosStateTransitionError

from .profile import CapabilityBinding


RUN_STATUS_TRANSITIONS: Dict[str, set[str]] = {
    'planned': {'planned', 'pending', 'running', 'failed', 'completed'},
    'pending': {'pending', 'running', 'failed', 'completed'},
    'running': {'running', 'failed', 'completed'},
    'failed': {'failed', 'pending', 'running'},
    'completed': {'completed', 'pending'},
}

STEP_STATUS_TRANSITIONS: Dict[str, set[str]] = {
    'pending': {'pending', 'ready', 'blocked', 'running', 'skipped'},
    'ready': {'ready', 'blocked', 'running', 'skipped'},
    'blocked': {'blocked', 'ready', 'pending', 'skipped'},
    'running': {'running', 'completed', 'failed'},
    'failed': {'failed', 'pending', 'ready'},
    'completed': {'completed', 'pending'},
    'skipped': {'skipped', 'pending', 'ready'},
}


def can_transition_run_status(current: str, target: str) -> bool:
    return target in RUN_STATUS_TRANSITIONS.get(current, {current})


def can_transition_step_status(current: str, target: str) -> bool:
    return target in STEP_STATUS_TRANSITIONS.get(current, {current})


def assert_run_status_transition(current: str, target: str) -> None:
    if not can_transition_run_status(current, target):
        raise FarosStateTransitionError(f"Invalid FAROS run status transition: {current} -> {target}")


def assert_step_status_transition(current: str, target: str, node_id: str) -> None:
    if not can_transition_step_status(current, target):
        raise FarosStateTransitionError(f"Invalid FAROS step status transition for node '{node_id}': {current} -> {target}")


class ExecutionContext(BaseModel):
    """Runtime context passed into each capability."""

    run_id: str
    blueprint_id: str
    profile_id: str
    node_id: str
    capability_id: str
    agent_id: Optional[str] = None
    skill_ids: List[str] = Field(default_factory=list)
    provider_bindings: Dict[str, CapabilityBinding] = Field(default_factory=dict)
    memory: Dict[str, Any] = Field(default_factory=dict)
    settings: Dict[str, Any] = Field(default_factory=dict)

    def get_binding(self, capability_id: Optional[str] = None) -> Optional[CapabilityBinding]:
        key = capability_id or self.capability_id
        return self.provider_bindings.get(key)


class StepState(BaseModel):
    """Persistent execution state for one workflow node."""

    node_id: str
    capability: str
    agent_id: Optional[str] = None
    skill_ids: List[str] = Field(default_factory=list)
    status: str = 'pending'
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    outputs_summary: Dict[str, Any] = Field(default_factory=dict)
    verification: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    retry_count: int = 0
    checkpoint: Dict[str, Any] = Field(default_factory=dict)


class FarosRunRecord(BaseModel):
    """Top-level run record persisted by the FAROS runtime."""

    id: str
    blueprint_id: str
    profile_id: str
    status: str
    execution_mode: str = 'execute'
    created_at: str
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    inputs: Dict[str, Any] = Field(default_factory=dict)
    runtime_options: Dict[str, Any] = Field(default_factory=dict)
    steps: List[StepState] = Field(default_factory=list)
    output_summary: Dict[str, Any] = Field(default_factory=dict)
    preflight: Dict[str, Any] = Field(default_factory=dict)
    checkpoint: Dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None
