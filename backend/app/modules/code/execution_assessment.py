"""Scientific execution assessment and pre-execution gate for the Code module.

The public contract lives on the integration branch.  These internal models
mirror its frozen ``scientific-research/v1`` boundary so this branch can be
developed and tested independently, then converted without changing fields.
"""

from __future__ import annotations

import os
import re
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


SCHEMA_VERSION = "scientific-research/v1"


class ExecutionClass(str, Enum):
    COMPUTATIONAL_READY = "computational_ready"
    SIMULATION_READY = "simulation_ready"
    DATA_REQUIRED = "data_required"
    INSTRUMENT_REQUIRED = "instrument_required"
    ETHICS_REVIEW_REQUIRED = "ethics_review_required"
    PROOF_REQUIRED = "proof_required"
    PROTOCOL_ONLY = "protocol_only"
    NOT_ASSESSED = "not_assessed"


class ExecutionStatus(str, Enum):
    NOT_ASSESSED = "not_assessed"
    READY = "ready"
    RUNNING = "running"
    EXECUTED = "executed"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class ArtifactKind(str, Enum):
    IDEA = "idea"
    PLAN = "plan"
    CODE = "code"
    PAPER = "paper"
    REVIEW = "review"
    DATASET = "dataset"
    METRIC = "metric"
    LOG = "log"
    REPORT = "report"
    OTHER = "other"


class TargetModule(str, Enum):
    IDEA = "idea"
    CODE = "code"
    PAPER = "paper"
    REVIEW = "review"
    PLATFORM = "platform"


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArtifactRef(ContractModel):
    id: str = Field(min_length=1)
    kind: ArtifactKind
    sourceModule: TargetModule
    uri: str = ""
    contentHash: str = ""
    version: str = ""
    createdAt: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ExecutionAssessment(ContractModel):
    schemaVersion: str = SCHEMA_VERSION
    runId: str = Field(min_length=1)
    questionId: str = Field(min_length=1)
    planPackageId: Optional[str] = None
    executionClass: ExecutionClass
    feasibilityScore: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)
    availableInputs: List[str] = Field(default_factory=list)
    missingInputs: List[str] = Field(default_factory=list)
    toolsAndEnvironment: List[str] = Field(default_factory=list)
    validationMetrics: List[str] = Field(default_factory=list)
    stopConditions: List[str] = Field(default_factory=list)
    safetyConstraints: List[str] = Field(default_factory=list)
    estimatedRuntimeSeconds: Optional[float] = Field(default=None, ge=0.0)
    estimatedCost: Optional[float] = Field(default=None, ge=0.0)
    status: ExecutionStatus = ExecutionStatus.NOT_ASSESSED
    warnings: List[str] = Field(default_factory=list)
    artifactRefs: List[ArtifactRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_gate_state(self) -> "ExecutionAssessment":
        ready_classes = {
            ExecutionClass.COMPUTATIONAL_READY,
            ExecutionClass.SIMULATION_READY,
        }
        if self.status == ExecutionStatus.READY and self.executionClass not in ready_classes:
            raise ValueError("only computational_ready or simulation_ready may have ready status")
        if self.status == ExecutionStatus.READY and self.missingInputs:
            raise ValueError("ready assessment cannot contain missingInputs")
        return self


class ExecutionGateDecision(ContractModel):
    allowed: bool
    executionClass: ExecutionClass
    status: ExecutionStatus
    reason: str
    missingInputs: List[str] = Field(default_factory=list)


class ExecutionGateError(RuntimeError):
    def __init__(self, assessment: ExecutionAssessment):
        self.assessment = assessment
        missing = ", ".join(assessment.missingInputs) or "required approval or protocol"
        super().__init__(
            f"execution blocked: {assessment.executionClass.value}; missing: {missing}"
        )


_READY_CLASSES = {
    ExecutionClass.COMPUTATIONAL_READY,
    ExecutionClass.SIMULATION_READY,
}

_SIGNALS: dict[ExecutionClass, tuple[str, ...]] = {
    ExecutionClass.ETHICS_REVIEW_REQUIRED: (
        "human subject", "patient", "clinical trial", "personal data", "informed consent",
        "伦理", "受试者", "患者", "临床试验", "个人隐私", "知情同意",
    ),
    ExecutionClass.INSTRUMENT_REQUIRED: (
        "microscope", "telescope", "spectrometer", "sensor deployment", "wet lab",
        "laboratory experiment", "field sampling", "显微镜", "望远镜", "光谱仪",
        "传感器部署", "湿实验", "实验室实验", "实地采样", "仪器",
    ),
    ExecutionClass.PROOF_REQUIRED: (
        "prove that", "mathematical proof", "theorem", "lemma", "formal proof",
        "证明", "定理", "引理", "形式化证明",
    ),
    ExecutionClass.SIMULATION_READY: (
        "simulation", "simulate", "monte carlo", "synthetic experiment", "agent-based model",
        "仿真", "模拟", "蒙特卡洛", "合成实验", "参数扫描",
    ),
    ExecutionClass.COMPUTATIONAL_READY: (
        "dataset", "benchmark", "python", "algorithm", "model evaluation", "data analysis",
        "compute", "code", "pipeline", "数据集", "基准", "算法", "模型评估",
        "数据分析", "计算", "代码", "流程",
    ),
}

_DATA_TERMS = (
    "dataset", "corpus", "database", "records", "measurements", "observations",
    "数据集", "语料", "数据库", "记录", "测量数据", "观测数据",
)


def _as_dict(source: Any) -> dict[str, Any]:
    if isinstance(source, Mapping):
        return dict(source)
    if hasattr(source, "model_dump"):
        return source.model_dump(mode="json")
    raise TypeError("assessment source must be a mapping or Pydantic model")


def _flatten_text(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        chunks: list[str] = []
        for key, item in value.items():
            if key not in {"closestPriorWork", "literatureSurvey", "evidenceMap", "evidenceTrace"}:
                chunks.extend(_flatten_text(item))
        return chunks
    if isinstance(value, list):
        chunks = []
        for item in value:
            chunks.extend(_flatten_text(item))
        return chunks
    return []


def _contains(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def _path_is_available(raw: Any, base_dir: Optional[str]) -> bool:
    if isinstance(raw, Mapping):
        if raw.get("contentHash") or raw.get("hash"):
            return True
        raw = raw.get("uri") or raw.get("path") or raw.get("value")
    if not isinstance(raw, str) or not raw.strip():
        return False
    value = raw.strip()
    if value.startswith(("http://", "https://", "s3://", "oss://")):
        return True
    candidate = Path(value)
    if not candidate.is_absolute() and base_dir:
        candidate = Path(base_dir) / candidate
    return candidate.exists()


def _extract_plan_steps(data: dict[str, Any]) -> list[dict[str, Any]]:
    research_plan = data.get("researchPlan") or {}
    if isinstance(research_plan, Mapping) and research_plan.get("steps"):
        return [dict(step) for step in research_plan.get("steps", []) if isinstance(step, Mapping)]
    return [
        dict(step)
        for stage in data.get("stages", [])
        if isinstance(stage, Mapping)
        for step in stage.get("steps", [])
        if isinstance(step, Mapping)
    ]


def _extract_available_inputs(data: dict[str, Any], base_dir: Optional[str]) -> tuple[list[str], list[str]]:
    available: list[str] = []
    missing: list[str] = []
    artifacts = data.get("artifactRefs", [])
    for artifact in artifacts if isinstance(artifacts, list) else []:
        if not isinstance(artifact, Mapping):
            continue
        label = str(artifact.get("uri") or artifact.get("id") or "artifact")
        if artifact.get("contentHash") or _path_is_available(artifact, base_dir):
            available.append(label)

    constants = data.get("constants", {})
    if isinstance(constants, Mapping):
        for name, value in constants.items():
            label = f"{name}={value}" if not isinstance(value, (dict, list)) else str(name)
            if _path_is_available(value, base_dir):
                available.append(label)

    declared_available = data.get("availableInputs", [])
    if isinstance(declared_available, list):
        available.extend(str(item) for item in declared_available)

    research_plan = data.get("researchPlan") or {}
    required_data = research_plan.get("requiredData", []) if isinstance(research_plan, Mapping) else []
    for item in required_data:
        item_text = str(item)
        if not any(item_text.lower() in existing.lower() for existing in available):
            missing.append(item_text)

    explicit_missing = data.get("missingInputs", [])
    if isinstance(explicit_missing, list):
        missing.extend(str(item) for item in explicit_missing)
    return _unique(available), _unique(missing)


def _extract_metrics_and_stops(data: dict[str, Any], steps: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    metrics: list[str] = []
    stops: list[str] = []
    for step in steps:
        metrics.extend(str(item) for item in step.get("metrics", []) if item)
        stops.extend(str(item) for item in step.get("stopConditions", []) if item)
        for expected in step.get("expected", []):
            if not isinstance(expected, Mapping):
                continue
            metric = str(expected.get("metric") or "").strip()
            target = str(expected.get("target") or "").strip()
            if metric:
                metrics.append(metric)
            if metric and target:
                stops.append(f"Stop or review when {metric} does not satisfy {target}")
        hints = step.get("codeHints", {})
        if isinstance(hints, Mapping):
            for item in hints.get("stopConditions", []) or []:
                stops.append(str(item))
    return _unique(metrics), _unique(stops)


def _explicit_class(data: dict[str, Any]) -> Optional[ExecutionClass]:
    candidates = [data.get("executionClass")]
    research_plan = data.get("researchPlan")
    if isinstance(research_plan, Mapping):
        candidates.append(research_plan.get("executionClass"))
    for value in candidates:
        if value and value != ExecutionClass.NOT_ASSESSED.value:
            try:
                return ExecutionClass(value)
            except ValueError:
                continue
    return None


def assess_execution(
    source: Any,
    *,
    run_id: Optional[str] = None,
    question_id: Optional[str] = None,
    base_dir: Optional[str] = None,
) -> ExecutionAssessment:
    """Classify a ResearchDossier or PlanPackage without invoking an LLM."""

    data = _as_dict(source)
    source_info = data.get("source", {}) if isinstance(data.get("source"), Mapping) else {}
    idea = data.get("idea", {}) if isinstance(data.get("idea"), Mapping) else {}
    resolved_run_id = run_id or str(data.get("runId") or source_info.get("ideaSessionId") or data.get("packageId") or "code_assessment")
    resolved_question_id = question_id or str(data.get("questionId") or idea.get("id") or source_info.get("ideaCandidateId") or "unknown_question")
    steps = _extract_plan_steps(data)
    text = " ".join(_flatten_text(data)).lower()
    available, missing = _extract_available_inputs(data, base_dir)
    metrics, stop_conditions = _extract_metrics_and_stops(data, steps)
    warnings: list[str] = []
    safety: list[str] = [
        "Execution must stay inside the configured FAROS workspace and artifact storage.",
        "Generated results must not be marked executed unless reproducibility artifacts exist.",
    ]

    explicit = _explicit_class(data)
    execution_class: ExecutionClass
    signals: list[str] = []
    # Hard safety/proof signals are derived from the full task and must not be
    # weakened by an optimistic upstream executionClass declaration.
    if _contains(text, _SIGNALS[ExecutionClass.ETHICS_REVIEW_REQUIRED]):
        execution_class = ExecutionClass.ETHICS_REVIEW_REQUIRED
        missing.append("documented ethics approval and data-governance clearance")
        safety.append("Do not process human or sensitive data before ethics and privacy approval.")
        signals.append("human/clinical/ethics signal detected")
    elif _contains(text, _SIGNALS[ExecutionClass.INSTRUMENT_REQUIRED]):
        execution_class = ExecutionClass.INSTRUMENT_REQUIRED
        missing.append("instrument access, calibration record, and collected observations")
        signals.append("physical instrument or laboratory signal detected")
    elif _contains(text, _SIGNALS[ExecutionClass.PROOF_REQUIRED]):
        execution_class = ExecutionClass.PROOF_REQUIRED
        missing.append("formal proof strategy or proof-assistant specification")
        signals.append("theorem/proof signal detected")
    elif explicit:
        execution_class = explicit
        signals.append(f"upstream executionClass={explicit.value}")
    elif _contains(text, _SIGNALS[ExecutionClass.SIMULATION_READY]):
        execution_class = ExecutionClass.SIMULATION_READY
        signals.append("simulation or parameter-sweep method detected")
    else:
        data_needed = _contains(text, _DATA_TERMS)
        has_computational_signal = _contains(text, _SIGNALS[ExecutionClass.COMPUTATIONAL_READY])
        if data_needed and not available:
            execution_class = ExecutionClass.DATA_REQUIRED
            missing.append("versioned dataset or corpus with a resolvable local/remote reference")
            signals.append("data-dependent method has no resolvable data artifact")
        elif has_computational_signal:
            execution_class = ExecutionClass.COMPUTATIONAL_READY
            signals.append("computational method and runnable evaluation signal detected")
        else:
            execution_class = ExecutionClass.PROTOCOL_ONLY
            missing.append("machine-executable method, inputs, and validation procedure")
            signals.append("no safe machine-executable path could be established")

    available = _unique(available)
    missing = _unique(missing)
    if explicit and explicit != execution_class:
        warnings.append(
            f"Upstream executionClass={explicit.value} was overridden by "
            f"the stricter detected class {execution_class.value}."
        )
    if not metrics:
        warnings.append("No explicit validation metrics were found.")
    if not stop_conditions:
        warnings.append("No explicit stop condition was found; define one before execution.")
    if not steps:
        warnings.append("No executable plan steps were found.")

    ready = execution_class in _READY_CLASSES and not missing and bool(steps) and bool(metrics) and bool(stop_conditions)
    status = ExecutionStatus.READY if ready else ExecutionStatus.NOT_APPLICABLE
    if execution_class in _READY_CLASSES and not ready:
        if not steps:
            missing.append("at least one executable plan step")
        if not metrics:
            missing.append("validation metric")
        if not stop_conditions:
            missing.append("stop condition")
        execution_class = ExecutionClass.DATA_REQUIRED if not available and _contains(text, _DATA_TERMS) else ExecutionClass.PROTOCOL_ONLY

    score_by_class = {
        ExecutionClass.COMPUTATIONAL_READY: 0.9,
        ExecutionClass.SIMULATION_READY: 0.82,
        ExecutionClass.DATA_REQUIRED: 0.35,
        ExecutionClass.INSTRUMENT_REQUIRED: 0.25,
        ExecutionClass.ETHICS_REVIEW_REQUIRED: 0.15,
        ExecutionClass.PROOF_REQUIRED: 0.3,
        ExecutionClass.PROTOCOL_ONLY: 0.4,
        ExecutionClass.NOT_ASSESSED: 0.0,
    }
    rationale = "; ".join(signals) + "."
    if missing:
        rationale += " Execution is gated until missing prerequisites are supplied."

    tools = []
    research_plan = data.get("researchPlan") or {}
    if isinstance(research_plan, Mapping):
        tools.extend(str(item) for item in research_plan.get("requiredResources", []) if item)
    for step in steps:
        tools.extend(str(item) for item in step.get("tools", []) if item)
        hints = step.get("codeHints")
        if isinstance(hints, Mapping):
            tools.extend(str(item) for item in hints.get("dependencies", []) or [] if item)
    if execution_class in _READY_CLASSES and not tools:
        tools.append("FAROS sandbox runtime")

    return ExecutionAssessment(
        runId=resolved_run_id,
        questionId=resolved_question_id,
        planPackageId=data.get("packageId"),
        executionClass=execution_class,
        feasibilityScore=score_by_class[execution_class],
        rationale=rationale,
        availableInputs=available,
        missingInputs=_unique(missing),
        toolsAndEnvironment=_unique(tools),
        validationMetrics=metrics,
        stopConditions=stop_conditions,
        safetyConstraints=_unique(safety),
        status=status,
        warnings=warnings,
        artifactRefs=[],
    )


def assess_plan_package(
    package: Any,
    *,
    run_id: Optional[str] = None,
    question_id: Optional[str] = None,
    base_dir: Optional[str] = None,
) -> ExecutionAssessment:
    return assess_execution(
        package,
        run_id=run_id,
        question_id=question_id,
        base_dir=base_dir,
    )


def execution_gate(assessment: ExecutionAssessment | Mapping[str, Any]) -> ExecutionGateDecision:
    value = assessment if isinstance(assessment, ExecutionAssessment) else ExecutionAssessment.model_validate(assessment)
    allowed = value.executionClass in _READY_CLASSES and value.status == ExecutionStatus.READY and not value.missingInputs
    reason = "Scientific execution prerequisites are satisfied." if allowed else value.rationale
    return ExecutionGateDecision(
        allowed=allowed,
        executionClass=value.executionClass,
        status=value.status,
        reason=reason,
        missingInputs=value.missingInputs,
    )


def require_execution_ready(assessment: ExecutionAssessment | Mapping[str, Any]) -> ExecutionAssessment:
    value = assessment if isinstance(assessment, ExecutionAssessment) else ExecutionAssessment.model_validate(assessment)
    if not execution_gate(value).allowed:
        raise ExecutionGateError(value)
    return value


def validate_with_public_contract(assessment: ExecutionAssessment) -> Any:
    """Validate against the frozen shared class when the integration baseline exists."""

    try:
        from app.contracts.scientific_research import ExecutionAssessment as PublicAssessment
    except ImportError:
        return ExecutionAssessment.model_validate(assessment.model_dump(mode="json"))
    return PublicAssessment.model_validate(assessment.model_dump(mode="json"))


__all__ = [
    "ExecutionAssessment",
    "ExecutionClass",
    "ExecutionGateDecision",
    "ExecutionGateError",
    "ExecutionStatus",
    "assess_execution",
    "assess_plan_package",
    "execution_gate",
    "require_execution_ready",
    "validate_with_public_contract",
]
