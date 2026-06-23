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
    ideaCandidateId: str
    rankedOutputId: Optional[str] = None
    searchTreeId: Optional[str] = None
    searchNodeId: Optional[str] = None
    pathSeedId: Optional[str] = None
    reasoningKgId: Optional[str] = None
    literatureMapId: Optional[str] = None
    bftsHandoffId: Optional[str] = None


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
    relevanceScore: float = Field(default=0.0, ge=0.0, le=1.0)
    relevanceSignals: List[str] = Field(default_factory=list)
    relevanceReason: str = ""
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
    kind: str = Field(default="supporting_signal", description="selected | supporting_signal | literature_limitation")
    statement: str
    severity: str = Field(default="medium")
    existingCoverage: str = ""
    unresolvedIssue: str = ""
    proposedEntry: str = ""
    boundary: str = ""
    validationNeeds: List[str] = Field(default_factory=list)
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


class PlanContributionStatement(BaseModel):
    id: str
    type: str = Field(description="method | system | evaluation | analysis | application")
    statement: str
    noveltyBasis: str = ""
    validationStageIds: List[str] = Field(default_factory=list)
    validationStepIds: List[str] = Field(default_factory=list)
    evidenceRefs: List[PlanEvidenceRef] = Field(default_factory=list)


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
        "consume": ["background", "literatureSurvey", "gap", "principle", "contributionStatement", "stages", "evidenceTrace"],
        "requiredOutputs": ["table", "chart", "report"],
    })
    review: Dict[str, Any] = Field(default_factory=lambda: {
        "consume": ["idea", "gap", "principle", "contributionStatement", "qualityGate", "evidenceTrace"],
        "requiredOutputs": ["report"],
    })


class PlanPackageStatus(str, Enum):
    DRAFT = "draft"
    AGENT_REVIEWING = "agent_reviewing"
    NEEDS_REVISION = "needs_revision"
    NEEDS_HUMAN_REVIEW = "needs_human_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class PlanHumanFeedback(BaseModel):
    id: str
    sectionPath: str
    feedbackType: str = Field(
        default="comment",
        description="comment | correction | reject | regenerate | approve",
    )
    comment: str
    severity: str = Field(default="medium", description="low | medium | high | blocking")
    requestedAction: str = Field(default="revise")
    createdAt: datetime = Field(default_factory=_utcnow)
    resolved: bool = False
    resolvedByRevisionId: Optional[str] = None


class PlanRevision(BaseModel):
    id: str
    parentPackageId: str
    createdAt: datetime = Field(default_factory=_utcnow)
    changedSections: List[str] = Field(default_factory=list)
    feedbackIds: List[str] = Field(default_factory=list)
    summary: str = ""
    generationMode: str = ""
    repairRounds: int = 0


class PlanReviewerIssue(BaseModel):
    id: str
    severity: str = Field(default="warning", description="info | warning | blocking")
    sectionPath: str = ""
    message: str
    evidenceRefs: List[PlanEvidenceRef] = Field(default_factory=list)


class PlanReviewerReport(BaseModel):
    reviewer: str
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    passed: bool = False
    blockingIssues: List[PlanReviewerIssue] = Field(default_factory=list)
    warnings: List[PlanReviewerIssue] = Field(default_factory=list)
    repairSuggestions: List[str] = Field(default_factory=list)
    evidenceRefs: List[PlanEvidenceRef] = Field(default_factory=list)
    createdAt: datetime = Field(default_factory=_utcnow)


class PlanMetaReview(BaseModel):
    overallScore: float = Field(default=0.0, ge=0.0, le=1.0)
    decision: str = Field(default="revise", description="approve | revise | reject")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    blockingIssues: List[PlanReviewerIssue] = Field(default_factory=list)
    warnings: List[PlanReviewerIssue] = Field(default_factory=list)
    requiredRepairs: List[str] = Field(default_factory=list)
    reviewerScores: Dict[str, float] = Field(default_factory=dict)
    createdAt: datetime = Field(default_factory=_utcnow)


class PlanQualityGate(BaseModel):
    schemaValid: bool = False
    evidenceValid: bool = False
    topicRelevant: bool = False
    citationFaithful: bool = False
    planSpecific: bool = False
    agentApproved: bool = False
    humanApproved: bool = False
    implementationReady: bool = False
    overallScore: float = Field(default=0.0, ge=0.0, le=1.0)
    reviewDecision: str = Field(default="draft")
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class PlanGenerationMetadata(BaseModel):
    mode: str = Field(default="deterministic", description="deterministic | hybrid")
    providerName: Optional[str] = None
    model: Optional[str] = None
    promptVersion: str = ""
    llmUsedSections: List[str] = Field(default_factory=list)
    reviewerMode: str = Field(default="hybrid", description="deterministic | hybrid")
    llmReviewerUsed: bool = False
    repairRounds: int = 0
    fallbackUsed: bool = False
    warnings: List[str] = Field(default_factory=list)


class PlanSourceFieldMap(BaseModel):
    idea: List[str] = Field(default_factory=list)
    background: List[str] = Field(default_factory=list)
    literatureSurvey: List[str] = Field(default_factory=list)
    gap: List[str] = Field(default_factory=list)
    principle: List[str] = Field(default_factory=list)
    contributionStatement: List[str] = Field(default_factory=list)
    evidenceTrace: List[str] = Field(default_factory=list)
    implementationPlan: List[str] = Field(default_factory=list)


class PlanPackage(BaseModel):
    """Complete idea+plan deliverable consumed by downstream modules."""

    schemaVersion: str = Field(default="plan-package/v4")
    packageId: str
    createdAt: datetime = Field(default_factory=_utcnow)
    status: PlanPackageStatus = PlanPackageStatus.DRAFT
    source: PlanSource
    idea: PlanIdeaSummary
    background: PlanBackground
    literatureSurvey: PlanLiteratureSurvey
    gap: PlanGap
    principle: PlanPrinciple
    contributionStatement: List[PlanContributionStatement] = Field(default_factory=list)
    researchQuestion: str
    hypothesis: str = ""
    constants: Dict[str, Any] = Field(default_factory=dict)
    stages: List[PlanStage]
    evidenceTrace: PlanEvidenceTrace
    downstreamContract: PlanDownstreamContract = Field(default_factory=PlanDownstreamContract)
    qualityGate: PlanQualityGate = Field(default_factory=PlanQualityGate)
    generation: PlanGenerationMetadata = Field(default_factory=PlanGenerationMetadata)
    humanFeedback: List[PlanHumanFeedback] = Field(default_factory=list)
    revisions: List[PlanRevision] = Field(default_factory=list)
    reviewReports: List[PlanReviewerReport] = Field(default_factory=list)
    metaReview: Optional[PlanMetaReview] = None
    sourceFields: PlanSourceFieldMap = Field(default_factory=PlanSourceFieldMap)
    rawIdeaOutputs: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(frozen=False)


class PlanReadablePaper(BaseModel):
    paperId: str
    title: str = ""
    source: str = ""
    relevanceScore: float = Field(default=0.0, ge=0.0, le=1.0)
    summary: str = ""
    methods: List[str] = Field(default_factory=list)
    findings: List[str] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)
    supports: List[str] = Field(default_factory=list)


class PlanReadableStep(BaseModel):
    id: str
    order: int
    title: str
    description: str
    method: str
    outputs: List[Dict[str, str]] = Field(default_factory=list)
    expected: List[Dict[str, str]] = Field(default_factory=list)


class PlanReadableStage(BaseModel):
    id: str
    order: int
    title: str
    goal: str
    method: str
    dependsOn: List[str] = Field(default_factory=list)
    steps: List[PlanReadableStep] = Field(default_factory=list)


class PlanPresentationBackground(BaseModel):
    summary: str = ""
    whyValuable: str = ""
    currentLimitations: List[str] = Field(default_factory=list)
    scope: List[str] = Field(default_factory=list)


class PlanPresentationGap(BaseModel):
    statement: str = ""
    existingCoverage: str = ""
    unresolvedIssue: str = ""
    proposedEntry: str = ""
    boundary: str = ""
    validationNeeds: List[str] = Field(default_factory=list)


class PlanPresentationMethod(BaseModel):
    principle: str = ""
    mechanism: str = ""
    noveltyClaim: str = ""
    contributions: List[str] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)


class PlanPresentationLiterature(BaseModel):
    summary: str = ""
    keyPapers: List[PlanReadablePaper] = Field(default_factory=list)
    weakOrUnconfirmedPapers: List[PlanReadablePaper] = Field(default_factory=list)


class PlanPresentationEvidenceSummary(BaseModel):
    confidence: str = Field(default="medium", description="high | medium | low")
    summary: str = ""
    supportingPapers: List[PlanReadablePaper] = Field(default_factory=list)
    weakPoints: List[str] = Field(default_factory=list)


class PlanPresentationReviewSummary(BaseModel):
    decision: str = ""
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    mainConcerns: List[str] = Field(default_factory=list)
    requiredFixes: List[str] = Field(default_factory=list)
    reviewerMode: str = ""
    llmReviewerUsed: bool = False


class PlanPresentationDebugRef(BaseModel):
    fullPackageEndpoint: str = ""
    packageId: str = ""
    ideaSessionId: str = ""
    ideaCandidateId: str = ""


class PlanPackagePresentation(BaseModel):
    """Human-readable PlanPackage view for product UI."""

    schemaVersion: str = Field(default="plan-package-presentation/v1")
    packageId: str
    packageStatus: str = ""
    title: str = ""
    executiveSummary: str = ""
    researchQuestion: str = ""
    hypothesis: str = ""
    background: PlanPresentationBackground = Field(default_factory=PlanPresentationBackground)
    gap: PlanPresentationGap = Field(default_factory=PlanPresentationGap)
    method: PlanPresentationMethod = Field(default_factory=PlanPresentationMethod)
    literature: PlanPresentationLiterature = Field(default_factory=PlanPresentationLiterature)
    implementationPlan: List[PlanReadableStage] = Field(default_factory=list)
    evidenceSummary: PlanPresentationEvidenceSummary = Field(default_factory=PlanPresentationEvidenceSummary)
    reviewSummary: PlanPresentationReviewSummary = Field(default_factory=PlanPresentationReviewSummary)
    nextActions: List[str] = Field(default_factory=list)
    debug: PlanPresentationDebugRef = Field(default_factory=PlanPresentationDebugRef)


class PlanHandoffEvidenceTrace(BaseModel):
    ideaCandidateId: str
    searchNodeId: Optional[str] = None
    pathSeedId: Optional[str] = None
    reasoningKgId: Optional[str] = None
    literatureMapId: Optional[str] = None
    selectedPaperIds: List[str] = Field(default_factory=list)
    structuredPaperIds: List[str] = Field(default_factory=list)
    probePaperIds: List[str] = Field(default_factory=list)


class PlanPackageHandoff(BaseModel):
    """Compact machine-oriented handoff for downstream modules."""

    schemaVersion: str = Field(default="plan-package-handoff/v1")
    packageId: str
    status: str = ""
    idea: PlanIdeaSummary
    researchQuestion: str
    hypothesis: str = ""
    constants: Dict[str, Any] = Field(default_factory=dict)
    backgroundSummary: str = ""
    selectedGap: PlanGapItem
    principle: PlanPrinciple
    contributionStatement: List[PlanContributionStatement] = Field(default_factory=list)
    keyPapers: List[PlanReadablePaper] = Field(default_factory=list)
    stages: List[PlanStage] = Field(default_factory=list)
    qualityGate: PlanQualityGate = Field(default_factory=PlanQualityGate)
    evidenceTrace: PlanHandoffEvidenceTrace
    downstreamContract: PlanDownstreamContract = Field(default_factory=PlanDownstreamContract)
