"""
Artifact Domain Model

Scientific Responsibility:
- Represent immutable evidence from Run execution
- Enforce append-only semantics (no updates)
- Link to Run for traceability
"""

from datetime import UTC, datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from enum import Enum

def _utcnow() -> datetime:
    return datetime.now(UTC)


class ArtifactType(str, Enum):
    """Artifact type categories."""
    PAPER = "paper"
    CODE = "code"
    LOG = "log"
    DATA = "data"
    MODEL = "model"
    FIGURE = "figure"


class Artifact(BaseModel):
    """
    Immutable evidence record.
    
    Once created, cannot be modified (scientific integrity).
    """
    id: str = Field(..., description="Unique artifact identifier")
    runId: str = Field(..., description="Run that generated this artifact")
    type: ArtifactType = Field(..., description="Artifact type")
    filename: str = Field(..., description="Original filename")
    size: int = Field(..., description="File size in bytes")
    storagePath: str = Field(..., description="Storage location")
    checksum: Optional[str] = Field(None, description="SHA256 checksum for integrity verification")
    createdAt: datetime = Field(default_factory=_utcnow)
    
    model_config = ConfigDict(frozen=True, use_enum_values=True)
