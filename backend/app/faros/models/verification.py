from typing import Any, Dict, List

from pydantic import BaseModel, Field


class VerificationResult(BaseModel):
    """Verification result for one rule evaluation."""

    rule_id: str
    status: str
    message: str
    details: Dict[str, Any] = Field(default_factory=dict)


class VerificationSuiteResult(BaseModel):
    """Aggregated verification result for one runtime step."""

    status: str
    message: str
    verifier_ids: List[str] = Field(default_factory=list)
    results: List[VerificationResult] = Field(default_factory=list)


class VerifierDescriptor(BaseModel):
    """Static verifier metadata exposed by FAROS."""

    id: str
    name: str
    description: str = ''
    default_enabled: bool = False
    tags: List[str] = Field(default_factory=list)


class VerifierPackDescriptor(BaseModel):
    """Metadata for one verifier policy pack."""

    id: str
    name: str
    description: str = ''
    verifier_ids: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    capability_ids: List[str] = Field(default_factory=list)
    provider_types: List[str] = Field(default_factory=list)
    recommended_node_ids: List[str] = Field(default_factory=list)
    package_id: str | None = None


class BlueprintValidationResult(BaseModel):
    """Validation result for blueprint structure and runtime references."""

    blueprint_id: str
    status: str
    message: str
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class ProfileValidationResult(BaseModel):
    """Validation result for profile structure and registry bindings."""

    profile_id: str
    status: str
    message: str
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class PreflightNodeResult(BaseModel):
    """Preflight resolution result for one workflow node."""

    node_id: str
    capability: str
    status: str
    agent_id: str | None = None
    skill_ids: List[str] = Field(default_factory=list)
    provider_types: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class RunPreflightResult(BaseModel):
    """Aggregated preflight validation result before a FAROS run is created."""

    blueprint_id: str
    profile_id: str
    status: str
    message: str
    nodes: List[PreflightNodeResult] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class VerifierPackageSpec(BaseModel):
    """Declarative verifier package manifest."""

    id: str
    name: str
    version: str
    manifest_version: str = '1.0'
    description: str = ''
    verifier_ids: List[str] = Field(default_factory=list)
    packs: Dict[str, VerifierPackDescriptor] = Field(default_factory=dict)
    compatibility: Dict[str, Dict[str, str]] = Field(default_factory=dict)
    manifest_path: str = ''
