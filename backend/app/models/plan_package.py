"""PlanPackage domain model.

PlanPackage is the primary deliverable of the idea+plan stage. It contains the
selected idea, required research context, and an implementation plan. It does
not represent executed experiments or observed results.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


class PlanOutputType(str, Enum):
    METRICS = "metrics"
    CHART = "chart"
    TABLE = "table"
    CHECKPOINT = "checkpoint"
    CODE = "code"
    REPORT = "report"
    LOG = "log"


class PlanEvidenceRef(BaseModel):
    type: str = Field(default="")
    id: str = Field(default="")
    source: str = Field(default="")
    note: str = Field(default="")


class PlanSource(BaseModel):
    ideaSessionId: str
    planSessionId: Optional[str] = None
    ideaCandidateId: str
    rankedOutputId: Optional[str] = None
    searchTreeId: Optional[str] = None
    searchNodeId: Optional[str] = None
    pathSeedId: Optional[str] = None
    reasoningKgId: Optional[str] = None
    literatureMapId: Optional[str] = None
    bftsHandoffId: Optional[str] = None
    selectedResearchPlanId: Optional[str] = None


class PlanIdeaSummary(BaseModel):
    id: str
    title: str = ""
    problem: str = ""
    hypothesisStatement: str = ""
    keyInsight: str = ""
    proposedMethod: str = ""
    expectedOutcome: str = ""
    scores: Dict[str, Any] = Field(default_factory=dict)
    critiqueSummary: str = ""
    closestPriorWork: List[Dict[str, Any]] = Field(default_factory=list)


class PlanBackground(BaseModel):
    summary: str
    motivation: str = ""
    currentLimitations: List[str] = Field(default_factory=list)
    domainContext: List[str] = Field(default_factory=list)
    evidenceRefs: List[PlanEvidenceRef] = Field(default_factory=list)


class PlanLiteratureCoverage(BaseModel):
    rawPaperCount: int = 0
    selectedPaperCount: int = 0
    structuredPaperCount: int = 0
    probePaperCount: int = 0
    clusterCount: int = 0


class PlanLiteraturePaperSummary(BaseModel):
    paperId: str
    structuredPaperId: Optional[str] = None
    source: str = Field(description="structured | probe")
    title: str
    authors: List[str] = Field(default_factory=list)
    year: Optional[int] = None
    venue: str = ""
    url: str = ""
    role: str = "background"
    summary: str
    methods: List[Dict[str, Any]] = Field(default_factory=list)
    findings: List[Dict[str, Any]] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)
    claims: List[Dict[str, Any]] = Field(default_factory=list)
    usedByStageIds: List[str] = Field(default_factory=list)
    usedByStepIds: List[str] = Field(default_factory=list)
    evidenceRefs: List[PlanEvidenceRef] = Field(default_factory=list)


class PlanLiteratureSurvey(BaseModel):
    summary: str
    coverage: PlanLiteratureCoverage = Field(default_factory=PlanLiteratureCoverage)
    clusters: List[Dict[str, Any]] = Field(default_factory=list)
    papers: List[PlanLiteraturePaperSummary]


class PlanGapItem(BaseModel):
    id: str
    statement: str
    severity: str = Field(default="medium")
    whyUnsolved: str = ""
    supportedByPaperIds: List[str] = Field(default_factory=list)
    supportedByClaimIds: List[str] = Field(default_factory=list)
    linkedGraphSignalIds: List[str] = Field(default_factory=list)


class PlanGap(BaseModel):
    summary: str
    items: List[PlanGapItem]
    selectedGapId: str


class PlanGraphGrounding(BaseModel):
    entityIds: List[str] = Field(default_factory=list)
    relationIds: List[str] = Field(default_factory=list)
    pathSeedIds: List[str] = Field(default_factory=list)
    searchNodeIds: List[str] = Field(default_factory=list)


class PlanProbeGrounding(BaseModel):
    probeResultIds: List[str] = Field(default_factory=list)
    graphPatchIds: List[str] = Field(default_factory=list)
    probePaperIds: List[str] = Field(default_factory=list)


class PlanPrinciple(BaseModel):
    summary: str
    mechanism: str = ""
    noveltyClaim: str = ""
    assumptions: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    reasoningPath: List[Dict[str, Any]] = Field(default_factory=list)
    graphGrounding: PlanGraphGrounding = Field(default_factory=PlanGraphGrounding)
    probeGrounding: PlanProbeGrounding = Field(default_factory=PlanProbeGrounding)


class PlanOutput(BaseModel):
    type: PlanOutputType
    name: str
    desc: str = ""
    requiredFor: List[str] = Field(default_factory=list)


class PlanExpectedMetric(BaseModel):
    metric: str
    target: str
    desc: str = ""


class PlanStep(BaseModel):
    id: str
    order: int
    title: str
    desc: str
    method: str
    inputFrom: List[str] = Field(default_factory=list)
    outputs: List[PlanOutput]
    expected: List[PlanExpectedMetric]
    evidenceRefs: List[PlanEvidenceRef] = Field(default_factory=list)
    codeHints: Dict[str, Any] = Field(default_factory=dict)


class PlanStage(BaseModel):
    id: str
    order: int
    title: str
    goal: str
    method: str
    dependsOn: List[str] = Field(default_factory=list)
    steps: List[PlanStep]


class PlanEvidenceTrace(BaseModel):
    ideaCandidateId: str
    searchNodeId: Optional[str] = None
    pathSeedId: Optional[str] = None
    reasoningKgId: Optional[str] = None
    literatureMapId: Optional[str] = None
    selectedPaperIds: List[str] = Field(default_factory=list)
    structuredPaperIds: List[str] = Field(default_factory=list)
    probeResultIds: List[str] = Field(default_factory=list)
    graphPatchIds: List[str] = Field(default_factory=list)
    probePaperIds: List[str] = Field(default_factory=list)
    candidateGraphEvidence: Dict[str, Any] = Field(default_factory=dict)
    reasoningTrace: List[Dict[str, Any]] = Field(default_factory=list)


class PlanDownstreamContract(BaseModel):
    implementation: Dict[str, Any] = Field(default_factory=lambda: {
        "consume": ["researchQuestion", "hypothesis", "constants", "stages"],
        "requiredOutputs": ["metrics", "table", "chart", "log"],
    })
    code: Dict[str, Any] = Field(default_factory=lambda: {
        "consume": ["stages.steps", "steps.outputs", "constants", "principle"],
        "requiredOutputs": ["code", "checkpoint", "log", "metrics"],
    })
    paper: Dict[str, Any] = Field(default_factory=lambda: {
        "consume": ["background", "literatureSurvey", "gap", "principle", "stages", "evidenceTrace"],
        "requiredOutputs": ["table", "chart", "report"],
    })
    review: Dict[str, Any] = Field(default_factory=lambda: {
        "consume": ["idea", "gap", "principle", "qualityGate", "evidenceTrace"],
        "requiredOutputs": ["report"],
    })


class PlanQualityGate(BaseModel):
    schemaValid: bool = False
    evidenceValid: bool = False
    implementationReady: bool = False
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class PlanGenerationMetadata(BaseModel):
    mode: str = Field(default="deterministic", description="deterministic | hybrid")
    providerName: Optional[str] = None
    model: Optional[str] = None
    promptVersion: str = ""
    llmUsedSections: List[str] = Field(default_factory=list)
    repairRounds: int = 0
    fallbackUsed: bool = False
    warnings: List[str] = Field(default_factory=list)


class PlanSourceFieldMap(BaseModel):
    idea: List[str] = Field(default_factory=list)
    background: List[str] = Field(default_factory=list)
    literatureSurvey: List[str] = Field(default_factory=list)
    gap: List[str] = Field(default_factory=list)
    principle: List[str] = Field(default_factory=list)
    evidenceTrace: List[str] = Field(default_factory=list)
    implementationPlan: List[str] = Field(default_factory=list)


class PlanPackage(BaseModel):
    """Complete idea+plan deliverable consumed by downstream modules."""

    schemaVersion: str = Field(default="plan-package/v2")
    packageId: str
    createdAt: datetime = Field(default_factory=_utcnow)
    source: PlanSource
    idea: PlanIdeaSummary
    background: PlanBackground
    literatureSurvey: PlanLiteratureSurvey
    gap: PlanGap
    principle: PlanPrinciple
    researchQuestion: str
    hypothesis: str = ""
    constants: Dict[str, Any] = Field(default_factory=dict)
    stages: List[PlanStage]
    evidenceTrace: PlanEvidenceTrace
    downstreamContract: PlanDownstreamContract = Field(default_factory=PlanDownstreamContract)
    qualityGate: PlanQualityGate = Field(default_factory=PlanQualityGate)
    generation: PlanGenerationMetadata = Field(default_factory=PlanGenerationMetadata)
    sourceFields: PlanSourceFieldMap = Field(default_factory=PlanSourceFieldMap)
    rawIdeaOutputs: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(frozen=False)
