"""Strict intermediate schema for PlanPackage LLM writebacks."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class LLMPlanEvidenceRef(BaseModel):
    type: str = ""
    id: str = ""
    source: str = ""
    note: str = ""

    model_config = ConfigDict(extra="forbid")


class LLMPlanOutput(BaseModel):
    type: str = "report"
    name: str
    desc: str = ""
    requiredFor: List[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class LLMPlanExpectedMetric(BaseModel):
    metric: str
    target: str
    desc: str = ""

    model_config = ConfigDict(extra="forbid")


class LLMPlanStep(BaseModel):
    id: Optional[str] = None
    order: Optional[int] = None
    title: str
    desc: str = ""
    method: str = ""
    inputFrom: List[str] = Field(default_factory=list)
    outputs: List[LLMPlanOutput] = Field(default_factory=list)
    expected: List[LLMPlanExpectedMetric] = Field(default_factory=list)
    evidenceRefs: List[LLMPlanEvidenceRef] = Field(default_factory=list)
    codeHints: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class LLMPlanStage(BaseModel):
    id: Optional[str] = None
    order: Optional[int] = None
    title: str
    goal: str = ""
    method: str = ""
    dependsOn: List[str] = Field(default_factory=list)
    steps: List[LLMPlanStep] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class LLMBackground(BaseModel):
    summary: Optional[str] = None
    motivation: Optional[str] = None
    currentLimitations: List[str] = Field(default_factory=list)
    domainContext: List[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class LLMGapItem(BaseModel):
    id: str
    statement: Optional[str] = None
    severity: Optional[str] = None
    existingCoverage: Optional[str] = None
    unresolvedIssue: Optional[str] = None
    proposedEntry: Optional[str] = None
    boundary: Optional[str] = None
    validationNeeds: List[str] = Field(default_factory=list)
    whyUnsolved: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class LLMGap(BaseModel):
    summary: Optional[str] = None
    selectedGapId: Optional[str] = None
    items: List[LLMGapItem] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class LLMPrinciple(BaseModel):
    summary: Optional[str] = None
    mechanism: Optional[str] = None
    noveltyClaim: Optional[str] = None
    assumptions: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class PlanPackageLLMOutput(BaseModel):
    researchQuestion: Optional[str] = None
    hypothesis: Optional[str] = None
    constants: Optional[Dict[str, Any]] = None
    stages: Optional[List[LLMPlanStage]] = None
    background: Optional[LLMBackground] = None
    gap: Optional[LLMGap] = None
    principle: Optional[LLMPrinciple] = None

    model_config = ConfigDict(extra="forbid")


_ALLOWED_TOP_LEVEL = {
    "researchQuestion",
    "hypothesis",
    "constants",
    "stages",
    "background",
    "gap",
    "principle",
}


def _null_paths(value: Any, prefix: str = "") -> List[str]:
    paths: List[str] = []
    if value is None:
        return [prefix or "$"]
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            paths.extend(_null_paths(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            child = f"{prefix}[{index}]"
            paths.extend(_null_paths(item, child))
    return paths


def _validation_error_messages(exc: ValidationError) -> List[str]:
    messages: List[str] = []
    for error in exc.errors()[:12]:
        loc = ".".join(str(part) for part in error.get("loc", [])) or "$"
        messages.append(f"{loc}: {error.get('msg', 'invalid value')}")
    return messages


def validate_llm_plan_output(
    raw: Any,
    *,
    target_sections: Optional[List[str]] = None,
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Validate and normalize an LLM plan JSON object before applying it."""

    if not isinstance(raw, dict):
        return None, ["LLM output must be one JSON object"]
    unknown = sorted(set(raw) - _ALLOWED_TOP_LEVEL)
    if unknown:
        return None, [
            "LLM output contains forbidden top-level keys: " + ", ".join(unknown)
        ]
    nulls = _null_paths(raw)
    if nulls:
        return None, [
            "LLM output contains null values; omit the field or use empty arrays/objects/strings: "
            + ", ".join(nulls[:12])
        ]
    if target_sections is not None:
        writable = set(target_sections)
        non_writable = sorted((set(raw) & _ALLOWED_TOP_LEVEL) - writable)
        if non_writable:
            return None, [
                "LLM output included non-writable sections for this revision: "
                + ", ".join(non_writable)
            ]
    try:
        parsed = PlanPackageLLMOutput.model_validate(raw)
    except ValidationError as exc:
        return None, _validation_error_messages(exc)
    normalized = parsed.model_dump(exclude_none=True)
    return normalized, []


def llm_plan_output_schema_hint(target_sections: Optional[List[str]] = None) -> str:
    writable = target_sections or ["researchQuestion", "hypothesis", "constants", "stages"]
    return (
        "Writable top-level keys: " + ", ".join(writable) + ". "
        "Forbidden top-level keys: literatureSurvey, contributionStatement, evidenceTrace, sourceFields, rawIdeaOutputs, reviewReports. "
        "Do not use null. For unknown optional values, omit the field or use empty arrays/objects/strings. "
        "Each stage must use title/goal/method/steps; each step must use title/desc/method/outputs/expected."
    )
