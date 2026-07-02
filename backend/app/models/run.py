"""
Run Domain Model

Scientific Responsibility:
- Represent one concrete execution instance of a PlanPackage
- Enforce lifecycle state machine (pending → running → completed/failed/cancelled)
- Maintain immutability after completion (append-only)
- Link to PlanPackage for scientific context
- Reference artifacts without owning them

A Run answers:
- How was this plan executed?
- What compute resources were used?
- What was the outcome?
- Where is the evidence?

It does NOT:
- Aggregate multiple runs (that's Experiment, out of Phase 1 scope)
- Store artifact content (that's Artifact storage)
- Modify itself after completion (immutable)
"""

from datetime import UTC, datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, ConfigDict, Field, field_validator
from enum import Enum

def _utcnow() -> datetime:
    return datetime.now(UTC)


class RunStatus(str, Enum):
    """
    Run lifecycle states.
    
    State machine:
    pending → running → {completed | failed | cancelled}
    
    No backwards transitions allowed (append-only).
    """
    PENDING = "pending"      # Queued, not started
    RUNNING = "running"      # Currently executing
    COMPLETED = "completed"  # Finished successfully
    FAILED = "failed"        # Finished with error
    CANCELLED = "cancelled"  # User-terminated


class RunType(str, Enum):
    """
    Execution mode.
    
    Plan: Structured execution following PlanPackage
    Idea: Exploratory execution without formal plan
    """
    PLAN = "plan"
    IDEA = "idea"


class RunConfig(BaseModel):
    """
    Compute policy and execution parameters.
    
    Specifies HOW the execution will run (resources, model, etc).
    This is separate from WHAT is being tested (PlanPackage).
    """
    # Compute Resources
    model: str = Field(
        ...,
        description="LLM model identifier (e.g., 'gpt-4o-2024-08-06')",
        examples=["gpt-4o-2024-08-06", "claude-3-opus-20240229"]
    )
    maxIterTimes: int = Field(
        default=10,
        description="Maximum iteration limit",
        ge=1,
        le=100
    )
    
    # Execution Context
    instancePath: Optional[str] = Field(
        default=None,
        description="Dataset/benchmark file path",
        examples=["benchmark/final/reasoning/analog_reasoner.json"]
    )
    taskLevel: str = Field(
        default="task1",
        description="Task difficulty level (legacy, prefer paperType)",
        examples=["task1", "task2"]
    )
    paperType: Optional[str] = Field(
        default=None,
        description="Paper type from taxonomy (e.g., 'algorithm', 'system', 'benchmark')"
    )
    
    # Environment
    workplaceName: str = Field(
        default="default_workspace",
        description="Workspace identifier"
    )
    cachePath: str = Field(
        default="cache",
        description="Cache directory path"
    )
    port: int = Field(
        default=8000,
        description="Service port",
        ge=1024,
        le=65535
    )
    
    # Taxonomy Linkage (optional)
    categoryGroup: Optional[str] = Field(
        None,
        description="Research category group (e.g., 'post-training', 'inference')"
    )
    categoryDirectionId: Optional[str] = Field(
        None,
        description="Specific research direction ID from taxonomy"
    )
    templateId: Optional[str] = Field(
        None,
        description="Template used to generate this config"
    )
    
    # Free-form Context
    ideas: Optional[str] = Field(
        None,
        description="Free-form research ideas or notes"
    )
    references: Optional[str] = Field(
        None,
        description="Reference papers or prior work"
    )
    
    # Phase 2: Code Module Integration
    codeSessionId: Optional[str] = Field(
        None,
        description="Code generation session ID (Phase 2)"
    )
    ideaSessionId: Optional[str] = Field(
        None,
        description="Idea session ID for traceability"
    )


class TraceReference(BaseModel):
    """
    Reference to execution trace.
    
    Phase 1: Simple reference
    Future: Full step-by-step execution log
    """
    run_id: str
    workdir: str
    total_steps: int = 0
    successful_steps: int = 0
    failed_steps: int = 0


class Run(BaseModel):
    """
    Immutable execution instance record.
    
    Represents ONE execution of a PlanPackage.
    Once completed/failed, cannot be modified (scientific integrity).
    """
    # Identity & Linkage
    id: str = Field(..., description="Unique run identifier (UUID)")
    planId: Optional[str] = Field(
        None,
        description="PlanPackage ID (if this run executes a formal plan)"
    )
    
    # Lifecycle
    status: RunStatus = Field(
        default=RunStatus.PENDING,
        description="Current lifecycle state"
    )
    createdAt: datetime = Field(
        default_factory=_utcnow,
        description="When run was created/queued"
    )
    startedAt: Optional[datetime] = Field(
        None,
        description="When execution began (status → running)"
    )
    endedAt: Optional[datetime] = Field(
        None,
        description="When execution finished (status → completed/failed/cancelled)"
    )
    
    # Execution Context
    type: RunType = Field(
        default=RunType.PLAN,
        description="Execution mode (plan vs idea)"
    )
    config: RunConfig = Field(
        ...,
        description="Compute policy and execution parameters"
    )
    
    # Evidence Trail (References Only)
    trace: Optional[TraceReference] = Field(
        None,
        description="Execution trace reference (if available)"
    )
    artifactIds: List[str] = Field(
        default_factory=list,
        description="IDs of artifacts generated by this run"
    )
    
    # Failure Handling
    errorMessage: Optional[str] = Field(
        None,
        description="Error description if status=failed"
    )
    
    # Mock Run Support (Phase 1.5)
    isMock: bool = Field(
        default=False,
        description="Whether this is a mock run (no real execution)"
    )
    
    @property
    def duration(self) -> Optional[int]:
        """
        Compute duration in seconds.
        
        Returns:
            Duration in seconds if run has started and ended, None otherwise
        """
        if self.startedAt and self.endedAt:
            delta = self.endedAt - self.startedAt
            return int(delta.total_seconds())
        return None
    
    @field_validator('endedAt')
    @classmethod
    def validate_endedAt(cls, v, info):
        """Ensure endedAt is after startedAt."""
        if v and info.data.get('startedAt'):
            if v < info.data['startedAt']:
                raise ValueError("endedAt must be after startedAt")
        return v
    
    def is_terminal(self) -> bool:
        """Check if run is in terminal state (completed/failed/cancelled)."""
        return self.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
    
    def can_transition_to(self, new_status: RunStatus) -> bool:
        """
        Check if state transition is valid.
        
        Valid transitions:
        - pending → running
        - running → completed/failed/cancelled
        
        Invalid:
        - Any backwards transition
        - Terminal state → any other state
        """
        if self.is_terminal():
            return False  # Terminal states are immutable
        
        valid_transitions = {
            RunStatus.PENDING: {RunStatus.RUNNING},
            RunStatus.RUNNING: {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED},
        }
        
        return new_status in valid_transitions.get(self.status, set())
    
    model_config = ConfigDict(
        use_enum_values=True,
        json_schema_extra={
            "example": {
                "id": "run_abc123def456",
                "plan_id": "plan_dpo_vs_sft_001",
                "status": "completed",
                "type": "plan",
                "createdAt": "2026-02-04T10:00:00Z",
                "startedAt": "2026-02-04T10:01:00Z",
                "endedAt": "2026-02-04T10:45:30Z",
                "config": {
                    "model": "gpt-4o-2024-08-06",
                    "maxIterTimes": 10,
                    "instancePath": "benchmark/final/reasoning/analog_reasoner.json",
                    "taskLevel": "task1",
                    "workplaceName": "dpo_experiment",
                    "cachePath": "cache",
                    "port": 8000,
                    "categoryDirectionId": "preference_optimization_dpo_ipo",
                    "templateId": "dpo_standard"
                },
                "trace": {
                    "run_id": "run_abc123def456",
                    "workdir": "/workspace/runs/run_abc123def456",
                    "total_steps": 8,
                    "successful_steps": 8,
                    "failed_steps": 0
                },
                "artifactIds": ["artifact_001", "artifact_002"],
                "errorMessage": None
            }
        },
    )
