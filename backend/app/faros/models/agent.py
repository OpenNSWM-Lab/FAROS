from typing import Any, Dict, List

from pydantic import BaseModel, Field


class AgentSpec(BaseModel):
    """A runtime agent role that can execute workflow nodes."""

    id: str
    name: str
    version: str = "0.1.0"
    role: str
    description: str = ""
    default_skills: List[str] = Field(default_factory=list)
    provider_preferences: Dict[str, Any] = Field(default_factory=dict)
    compatibility: Dict[str, Dict[str, str]] = Field(default_factory=dict)
    handoff_policy: Dict[str, Any] = Field(default_factory=dict)


class AgentBinding(BaseModel):
    """Profile-specific binding for an agent."""

    agent_id: str
    provider_type: str = "llm"
    preferred_provider: str | None = None
    preferred_model: str | None = None
    skill_overrides: List[str] = Field(default_factory=list)
    runtime_policy: Dict[str, Any] = Field(default_factory=dict)
