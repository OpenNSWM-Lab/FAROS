from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ArtifactRecord(BaseModel):
    """A persistent artifact produced by a capability execution."""

    id: str
    type: str
    uri: str
    producer: str
    summary: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ArtifactSchemaSpec(BaseModel):
    """Artifact schema contract known to the FAROS runtime."""

    type: str
    description: str = ''
    required_metadata: List[str] = Field(default_factory=list)
    allowed_uri_prefixes: List[str] = Field(default_factory=list)
    required_producer: Optional[str] = None
