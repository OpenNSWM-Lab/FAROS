from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ProviderTask(BaseModel):
    """Provider invocation request."""

    capability_id: str
    provider: str
    model: Optional[str] = None
    prompt: Optional[str] = None
    messages: List[Dict[str, str]] = Field(default_factory=list)
    options: Dict[str, Any] = Field(default_factory=dict)


class ProviderResult(BaseModel):
    """Provider invocation response."""

    ok: bool
    provider: str
    model: str
    text: str = ""
    usage: Dict[str, Any] = Field(default_factory=dict)
    payload: Dict[str, Any] = Field(default_factory=dict)
    latency_ms: int = 0
    error: Optional[str] = None


class ProviderDescriptor(BaseModel):
    """Static provider metadata exposed by the FAROS runtime."""

    type: str
    name: str
    description: str = ''
    supported_capabilities: List[str] = Field(default_factory=list)
    supported_provider_ids: List[str] = Field(default_factory=list)
    default_provider_id: Optional[str] = None
    defaults: Dict[str, Any] = Field(default_factory=dict)


class ProviderHealth(BaseModel):
    """Runtime health summary for one provider implementation."""

    type: str
    status: str
    message: str
    provider_ids: List[str] = Field(default_factory=list)
    details: Dict[str, Any] = Field(default_factory=dict)
