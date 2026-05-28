from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class WorkflowNode(BaseModel):
    """A single executable node in a FAROS workflow graph."""

    id: str
    capability: str
    name: Optional[str] = None
    agent: Optional[str] = None
    skills: List[str] = Field(default_factory=list)
    outputs: List[str] = Field(default_factory=list)
    inputs: Dict[str, Any] = Field(default_factory=dict)
    verifier: Optional[str] = None
    description: Optional[str] = None
    policies: Dict[str, Any] = Field(default_factory=dict)


class WorkflowEdge(BaseModel):
    """A directed dependency between workflow nodes."""

    source: str = Field(alias='from')
    target: str = Field(alias='to')


class Blueprint(BaseModel):
    """A research workflow definition independent of concrete providers."""

    id: str
    name: str
    version: str
    domain: str = 'general'
    description: str = ''
    workflow: List[WorkflowNode]
    edges: List[WorkflowEdge] = Field(default_factory=list)
    artifact_schema: Dict[str, Any] = Field(default_factory=dict)
    verification_rules: List[Dict[str, Any]] = Field(default_factory=list)
    writing_constraints: Dict[str, Any] = Field(default_factory=dict)
    output_contract: Dict[str, Any] = Field(default_factory=dict)
    compatibility: Dict[str, Dict[str, str]] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
