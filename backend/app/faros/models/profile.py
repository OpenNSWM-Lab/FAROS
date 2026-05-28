from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .agent import AgentBinding


class CapabilityBinding(BaseModel):
    """Maps one capability to a concrete provider implementation."""

    provider_type: str = 'llm'
    provider: str
    model: Optional[str] = None
    options: Dict[str, Any] = Field(default_factory=dict)


class MemoryPolicy(BaseModel):
    """Profile-level runtime memory policy."""

    mode: str = 'persistent'
    summary_keys: List[str] = Field(default_factory=lambda: [
        'selectedCandidateId',
        'projectId',
        'experimentId',
        'paperId',
        'reviewId',
        'lastNodeId',
    ])
    scope_strategy: str = 'node'
    max_history_entries: int = 32
    compaction_mode: str = 'summary_only'
    volatile_prefixes: List[str] = Field(default_factory=lambda: ['tmp_', 'draft_', 'scratch_'])
    retained_scopes: List[str] = Field(default_factory=lambda: ['run'])
    remove_archived_keys: bool = False


class Profile(BaseModel):
    """Execution profile for a blueprint."""

    id: str
    name: str
    version: str
    description: str = ''
    capability_bindings: Dict[str, CapabilityBinding] = Field(default_factory=dict)
    agent_bindings: Dict[str, AgentBinding] = Field(default_factory=dict)
    skill_defaults: Dict[str, List[str]] = Field(default_factory=dict)
    defaults: Dict[str, Any] = Field(default_factory=dict)
    verification_policy: Dict[str, Any] = Field(default_factory=dict)
    memory_policy: MemoryPolicy = Field(default_factory=MemoryPolicy)
