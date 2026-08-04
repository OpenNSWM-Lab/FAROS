"""
Run API Schemas

Scientific Responsibility:
- Define IO contracts for Run creation and retrieval
- Enforce validation rules at API boundary
- Separate external representation from internal domain model
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, ConfigDict, Field

from app.models.run import RunStatus, RunType, RunConfig, TraceReference
from app.schemas.artifact import ArtifactResponse


class RunCreate(BaseModel):
    """
    Schema for creating a new Run.
    
    Used by POST /api/v1/runs
    """
    planId: Optional[str] = Field(
        None,
        description="PlanPackage ID (if executing a formal plan)"
    )
    type: RunType = Field(
        default=RunType.PLAN,
        description="Execution mode"
    )
    config: RunConfig = Field(
        ...,
        description="Compute policy and execution parameters"
    )
    isMock: bool = Field(
        default=False,
        description="Whether this is a mock run (no real execution)"
    )


class RunUpdate(BaseModel):
    """
    Schema for updating Run status.
    
    Used by PATCH /api/v1/runs/{id}
    Only allows status transitions and timestamp updates.
    """
    status: Optional[RunStatus] = None
    startedAt: Optional[datetime] = None
    endedAt: Optional[datetime] = None
    errorMessage: Optional[str] = None
    trace: Optional[TraceReference] = None
    artifactIds: Optional[List[str]] = None


class RunResponse(BaseModel):
    """
    Schema for Run responses.
    
    Used by GET /api/v1/runs/{id}
    Includes computed duration field.
    """
    id: str
    planId: Optional[str]
    status: RunStatus
    type: RunType
    createdAt: datetime
    startedAt: Optional[datetime]
    endedAt: Optional[datetime]
    duration: Optional[int]  # Computed field (seconds)
    config: RunConfig
    trace: Optional[TraceReference]
    artifacts: List[ArtifactResponse]  # Full artifact objects (frontend contract)
    errorMessage: Optional[str]
    isMock: bool = False  # Phase 1.5: Mock run indicator
    
    model_config = ConfigDict(from_attributes=True)


class RunListResponse(BaseModel):
    """
    Schema for listing multiple Runs.
    """
    runs: List[RunResponse]
    total: int
