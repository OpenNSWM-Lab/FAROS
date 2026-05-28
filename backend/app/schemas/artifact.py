"""
Artifact API Schemas

Scientific Responsibility:
- Define IO contracts for Artifact operations
- Enforce validation rules at API boundary
- Maintain immutability guarantees
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, ConfigDict, Field

from app.models.artifact import ArtifactType


class ArtifactCreate(BaseModel):
    """
    Schema for registering a new Artifact.
    
    Used by POST /api/v1/artifacts
    Phase 1: Metadata only (no file upload)
    """
    runId: str = Field(..., description="Run that generated this artifact")
    type: ArtifactType = Field(..., description="Artifact type")
    filename: str = Field(..., description="Original filename")
    size: int = Field(..., description="File size in bytes", ge=0)
    storagePath: str = Field(..., description="Storage location")
    checksum: Optional[str] = Field(None, description="SHA256 checksum")


class ArtifactResponse(BaseModel):
    """
    Schema for Artifact responses.
    
    Used by GET /api/v1/artifacts/{id}
    """
    id: str
    runId: str
    type: ArtifactType
    filename: str
    size: int
    storagePath: str
    checksum: Optional[str]
    createdAt: datetime
    
    model_config = ConfigDict(from_attributes=True)


class ArtifactListResponse(BaseModel):
    """
    Schema for listing multiple Artifacts.
    """
    artifacts: List[ArtifactResponse]
    total: int
