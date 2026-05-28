from typing import Any, Dict, List

from pydantic import BaseModel, Field


class SkillSpec(BaseModel):
    """Reusable research behavior package addressable by FAROS runtime."""

    id: str
    name: str
    version: str = '0.1.0'
    manifest_version: str = '1.0'
    kind: str = 'reasoning'
    description: str = ''
    agent_roles: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Dict[str, Any] = Field(default_factory=dict)
    artifact_types: List[str] = Field(default_factory=list)
    verification_hooks: List[str] = Field(default_factory=list)
    provider_requirements: Dict[str, Any] = Field(default_factory=dict)
    compatibility: Dict[str, Dict[str, str]] = Field(default_factory=dict)
    manifest_path: str = ''
