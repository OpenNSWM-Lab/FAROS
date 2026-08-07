"""Versioned contracts shared by Idea, Code, Paper, Review, and Platform.

These models describe module boundaries, not module-internal persistence. A
module may keep richer internal models, but handoffs must validate here.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Type

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "scientific-research/v1"
SchemaVersion = Literal["scientific-research/v1"]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunMode(str, Enum):
    COVERAGE = "coverage"
    DEEP = "deep"


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"
    CANCELLED = "cancelled"


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


class EvidenceStance(str, Enum):
    SUPPORT = "support"
    COUNTER = "counter"
    CONTEXT = "context"


class EvidenceTier(str, Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    TERTIARY = "tertiary"
    UNKNOWN = "unknown"


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


class GateStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    NOT_ASSESSED = "not_assessed"


class Severity(str, Enum):
    BLOCKER = "blocker"
    MAJOR = "major"
    MINOR = "minor"
    INFO = "info"


class TargetModule(str, Enum):
    IDEA = "idea"
    CODE = "code"
    PAPER = "paper"
    REVIEW = "review"
    PLATFORM = "platform"


class SupportStatus(str, Enum):
    SUPPORTED = "supported"
    WEAKLY_SUPPORTED = "weakly_supported"
    UNSUPPORTED = "unsupported"
    CONTRADICTED = "contradicted"
    NEEDS_HUMAN_VERIFICATION = "needs_human_verification"
    NOT_ASSESSED = "not_assessed"


class ArtifactRef(ContractModel):
    id: str = Field(min_length=1)
    kind: ArtifactKind
    sourceModule: TargetModule
    uri: str = ""
    contentHash: str = ""
    version: str = ""
    createdAt: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ScientificQuestion(ContractModel):
    id: str = Field(min_length=1)
    text: str = Field(min_length=5)
    language: str = "auto"
    domainHint: Optional[str] = None
    constraints: List[str] = Field(default_factory=list)
    source: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ScientificQuestionRun(ContractModel):
    schemaVersion: SchemaVersion = SCHEMA_VERSION
    runId: str = Field(min_length=1)
    question: ScientificQuestion
    mode: RunMode = RunMode.DEEP
    status: RunStatus = RunStatus.PENDING
    providerName: Optional[str] = None
    model: Optional[str] = None
    parentRunId: Optional[str] = None
    createdAt: datetime = Field(default_factory=_utcnow)
    updatedAt: datetime = Field(default_factory=_utcnow)
    artifactRefs: List[ArtifactRef] = Field(default_factory=list)
    errorMessage: Optional[str] = None


class ProblemFrame(ContractModel):
    originalQuestion: str = Field(min_length=5)
    scopedQuestion: str = Field(min_length=5)
    definitions: Dict[str, str] = Field(default_factory=dict)
    observableVariables: List[str] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    outOfScope: List[str] = Field(default_factory=list)
    subQuestions: List[str] = Field(default_factory=list)


class EvidenceRecord(ContractModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    summary: str = ""
    stance: EvidenceStance = EvidenceStance.CONTEXT
    sourceType: str = "unknown"
    source: str = ""
    authors: List[str] = Field(default_factory=list)
    year: Optional[int] = None
    doi: Optional[str] = None
    url: Optional[str] = None
    evidenceTier: EvidenceTier = EvidenceTier.UNKNOWN
    relevanceScore: float = Field(default=0.0, ge=0.0, le=1.0)
    verified: bool = False
    claimIds: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class EvidenceMap(ContractModel):
    consensus: List[str] = Field(default_factory=list)
    disputedClaims: List[str] = Field(default_factory=list)
    supportingEvidence: List[EvidenceRecord] = Field(default_factory=list)
    counterEvidence: List[EvidenceRecord] = Field(default_factory=list)
    contextualEvidence: List[EvidenceRecord] = Field(default_factory=list)
    unresolvedGaps: List[str] = Field(default_factory=list)

    def evidence_ids(self) -> set[str]:
        records = self.supportingEvidence + self.counterEvidence + self.contextualEvidence
        return {record.id for record in records}


class Hypothesis(ContractModel):
    id: str = Field(min_length=1)
    statement: str = Field(min_length=5)
    rationale: str = ""
    derivationTrace: List[str] = Field(default_factory=list)
    supportingEvidenceIds: List[str] = Field(default_factory=list)
    counterEvidenceIds: List[str] = Field(default_factory=list)
    falsificationCriteria: List[str] = Field(min_length=1)
    confounders: List[str] = Field(default_factory=list)
    alternativeExplanations: List[str] = Field(default_factory=list)
    scores: Dict[str, float] = Field(default_factory=dict)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_scores(self) -> "Hypothesis":
        invalid = {key: value for key, value in self.scores.items() if not 0.0 <= value <= 1.0}
        if invalid:
            raise ValueError(f"hypothesis scores must be in [0, 1]: {invalid}")
        return self


class ResearchPlanStep(ContractModel):
    id: str = Field(min_length=1)
    order: int = Field(ge=1)
    title: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    inputs: List[str] = Field(default_factory=list)
    tools: List[str] = Field(default_factory=list)
    method: List[str] = Field(default_factory=list)
    outputs: List[str] = Field(default_factory=list)
    metrics: List[str] = Field(default_factory=list)
    stopConditions: List[str] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)


class ResearchPlan(ContractModel):
    objective: str = Field(min_length=1)
    steps: List[ResearchPlanStep] = Field(min_length=1)
    requiredData: List[str] = Field(default_factory=list)
    requiredResources: List[str] = Field(default_factory=list)
    expectedOutcomes: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    ethics: List[str] = Field(default_factory=list)
    executionClass: ExecutionClass = ExecutionClass.NOT_ASSESSED


class GenerationTrace(ContractModel):
    providerName: Optional[str] = None
    model: Optional[str] = None
    localRulePasses: List[str] = Field(default_factory=list)
    llmCalls: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    cacheHits: int = Field(default=0, ge=0)
    estimatedTokenCost: Optional[int] = Field(default=None, ge=0)
    startedAt: Optional[datetime] = None
    endedAt: Optional[datetime] = None


class ResearchDossier(ContractModel):
    schemaVersion: SchemaVersion = SCHEMA_VERSION
    runId: str = Field(min_length=1)
    questionId: str = Field(min_length=1)
    problemFrame: ProblemFrame
    evidenceMap: EvidenceMap
    hypotheses: List[Hypothesis] = Field(min_length=1)
    researchPlan: ResearchPlan
    uncertainties: List[str] = Field(default_factory=list)
    generationTrace: GenerationTrace = Field(default_factory=GenerationTrace)
    artifactRefs: List[ArtifactRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_evidence_references(self) -> "ResearchDossier":
        known = self.evidenceMap.evidence_ids()
        referenced = {
            evidence_id
            for hypothesis in self.hypotheses
            for evidence_id in hypothesis.supportingEvidenceIds + hypothesis.counterEvidenceIds
        }
        unknown = referenced - known
        if unknown:
            raise ValueError(f"hypotheses reference unknown evidence IDs: {sorted(unknown)}")
        return self


class ExecutionAssessment(ContractModel):
    schemaVersion: SchemaVersion = SCHEMA_VERSION
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


class MetricEvidence(ContractModel):
    name: str = Field(min_length=1)
    value: Any
    unit: str = ""
    definition: str = ""
    split: str = ""
    sourcePath: str = ""


class ExperimentEvidence(ContractModel):
    schemaVersion: SchemaVersion = SCHEMA_VERSION
    runId: str = Field(min_length=1)
    questionId: str = Field(min_length=1)
    codeRunId: str = Field(min_length=1)
    status: ExecutionStatus
    dataHashes: Dict[str, str] = Field(default_factory=dict)
    environmentHash: str = ""
    codeHash: str = ""
    method: str = ""
    baseline: str = ""
    metrics: List[MetricEvidence] = Field(default_factory=list)
    logRefs: List[str] = Field(default_factory=list)
    artifactRefs: List[ArtifactRef] = Field(default_factory=list)
    supportedClaims: List[str] = Field(default_factory=list)
    unsupportedClaims: List[str] = Field(default_factory=list)
    failures: List[str] = Field(default_factory=list)
    durationSeconds: Optional[float] = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def validate_executed_evidence(self) -> "ExperimentEvidence":
        if self.status == ExecutionStatus.EXECUTED:
            missing = []
            if not self.codeHash:
                missing.append("codeHash")
            if not self.environmentHash:
                missing.append("environmentHash")
            if not self.artifactRefs:
                missing.append("artifactRefs")
            if missing:
                raise ValueError(f"executed evidence requires: {', '.join(missing)}")
        return self


class ClaimBinding(ContractModel):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    sourcePath: str = ""
    evidenceIds: List[str] = Field(default_factory=list)
    metricRefs: List[str] = Field(default_factory=list)
    supportStatus: SupportStatus = SupportStatus.NOT_ASSESSED


class ResearchNarrative(ContractModel):
    schemaVersion: SchemaVersion = SCHEMA_VERSION
    runId: str = Field(min_length=1)
    questionId: str = Field(min_length=1)
    title: str = Field(min_length=1)
    problemAndScope: str = Field(min_length=1)
    currentEvidence: str = ""
    openGaps: str = ""
    candidateHypotheses: str = ""
    falsificationAndResearchPlan: str = ""
    executionStatusAndResults: str = ""
    limitationsAndUncertainty: str = ""
    citations: List[EvidenceRecord] = Field(default_factory=list)
    claimBindings: List[ClaimBinding] = Field(default_factory=list)
    artifactRefs: List[ArtifactRef] = Field(default_factory=list)


class QualityFinding(ContractModel):
    id: str = Field(min_length=1)
    code: str = Field(min_length=1)
    severity: Severity
    targetModule: TargetModule
    fieldPath: str = ""
    evidenceIds: List[str] = Field(default_factory=list)
    description: str = Field(min_length=1)
    suggestedFix: str = ""


class QualityAssessment(ContractModel):
    schemaVersion: SchemaVersion = SCHEMA_VERSION
    runId: str = Field(min_length=1)
    questionId: str = Field(min_length=1)
    gateStatus: GateStatus
    dimensionScores: Dict[str, float] = Field(default_factory=dict)
    findings: List[QualityFinding] = Field(default_factory=list)
    ruleTrace: List[Dict[str, Any]] = Field(default_factory=list)
    llmTrace: List[Dict[str, Any]] = Field(default_factory=list)
    uncertainty: str = ""
    configVersion: str = ""
    reviewedAt: datetime = Field(default_factory=_utcnow)

    @model_validator(mode="after")
    def validate_dimension_scores(self) -> "QualityAssessment":
        invalid = {key: value for key, value in self.dimensionScores.items() if not 0.0 <= value <= 1.0}
        if invalid:
            raise ValueError(f"quality scores must be in [0, 1]: {invalid}")
        return self


class QuestionBatch(ContractModel):
    schemaVersion: SchemaVersion = SCHEMA_VERSION
    batchId: str = Field(min_length=1)
    questionSetId: Optional[str] = None
    questionIds: List[str] = Field(min_length=1)
    childRunIds: List[str] = Field(default_factory=list)
    chunkSize: int = Field(default=1, ge=1)
    concurrency: int = Field(default=1, ge=1)
    status: RunStatus = RunStatus.PENDING
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    failedQuestionIds: List[str] = Field(default_factory=list)
    configHash: str = ""
    createdAt: datetime = Field(default_factory=_utcnow)
    updatedAt: datetime = Field(default_factory=_utcnow)

    @model_validator(mode="after")
    def validate_run_cardinality(self) -> "QuestionBatch":
        if len(self.childRunIds) > len(self.questionIds):
            raise ValueError("childRunIds cannot outnumber questionIds")
        unknown_failures = set(self.failedQuestionIds) - set(self.questionIds)
        if unknown_failures:
            raise ValueError(f"failedQuestionIds are not in this batch: {sorted(unknown_failures)}")
        return self


class QuestionSetManifest(ContractModel):
    schemaVersion: SchemaVersion = SCHEMA_VERSION
    questionSetId: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    source: str = ""
    contentHash: str = Field(min_length=1)
    questions: List[ScientificQuestion] = Field(min_length=1)


CONTRACT_MODELS: Dict[str, Type[BaseModel]] = {
    model.__name__: model
    for model in (
        ScientificQuestionRun,
        ResearchDossier,
        ExecutionAssessment,
        ExperimentEvidence,
        ResearchNarrative,
        QualityAssessment,
        QuestionBatch,
        QuestionSetManifest,
    )
}


def contract_json_schemas() -> Dict[str, Dict[str, Any]]:
    """Return JSON Schemas for adapters, validation tools, and documentation."""
    return {name: model.model_json_schema() for name, model in CONTRACT_MODELS.items()}
