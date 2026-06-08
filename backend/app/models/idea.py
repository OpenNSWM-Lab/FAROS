"""
Idea Generation Domain Models

Scientific Responsibility:
- Represent idea generation sessions and their outputs
- Track literature search results
- Store candidate ideas with scoring
- Maintain full traceability from session to candidates
"""

import hashlib
import re as _re
from datetime import UTC, datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, ConfigDict, Field
from enum import Enum

def _utcnow() -> datetime:
    return datetime.now(UTC)


class IdeaSessionStatus(str, Enum):
    """Idea session lifecycle states."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class IdeaSessionConfig(BaseModel):
    """Configuration for idea generation session."""
    providerName: str = Field(
        default="moonshot",
        description="LLM provider to use"
    )
    model: str = Field(
        default="moonshot-v1-8k",
        description="Model to use for generation"
    )
    directionId: Optional[str] = Field(
        default=None,
        description="Research direction ID from taxonomy"
    )
    seedQuery: str = Field(
        ...,
        description="Initial research topic or query"
    )
    paperType: str = Field(
        default="algorithm",
        description="Type of paper: algorithm, system, application, benchmark, survey, position, theory, evaluation, reproducibility, safety"
    )
    maxCandidates: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum number of candidate ideas to generate"
    )
    maxPapers: int = Field(
        default=120,
        ge=1,
        le=200,
        description="Maximum papers to retrieve in literature search"
    )
    domain: Optional[str] = Field(
        default=None,
        description="Research domain constraint"
    )
    constraints: Optional[List[str]] = Field(
        default=None,
        description="Additional constraints for idea generation"
    )
    mustCiteList: Optional[List[str]] = Field(
        default=None,
        description="Papers that must be cited"
    )
    searchBudget: Optional[int] = Field(
        default=None,
        ge=10,
        le=500,
        description="Optional search budget for BFTS; defaults to maxPapers if unset"
    )


class StepResult(BaseModel):
    """Result of a single pipeline step."""
    name: str
    status: str = Field(description="ok | failed | skipped")
    inputs: Dict[str, Any] = Field(default_factory=dict)
    outputs: Dict[str, Any] = Field(default_factory=dict)
    artifacts: List[str] = Field(default_factory=list)
    startedAt: datetime
    endedAt: datetime
    durationSeconds: float
    error: Optional[str] = None


class WorkflowTrace(BaseModel):
    """Trace of the idea generation workflow."""
    sessionId: str
    startedAt: datetime
    endedAt: Optional[datetime] = None
    totalSteps: int = 0
    successfulSteps: int = 0
    failedSteps: int = 0
    steps: List[StepResult] = Field(default_factory=list)


class IdeaSession(BaseModel):
    """
    Idea generation session.
    
    Represents one complete idea generation workflow execution.
    """
    id: str = Field(..., description="Unique session identifier")
    createdAt: datetime = Field(default_factory=_utcnow)
    status: IdeaSessionStatus = Field(default=IdeaSessionStatus.PENDING)
    config: IdeaSessionConfig
    startedAt: Optional[datetime] = None
    endedAt: Optional[datetime] = None
    trace: Optional[WorkflowTrace] = None
    candidateIds: List[str] = Field(default_factory=list)
    selectedCandidateId: Optional[str] = None
    errorMessage: Optional[str] = None
    
    @property
    def duration(self) -> Optional[int]:
        """Calculate duration in seconds."""
        if self.startedAt and self.endedAt:
            return int((self.endedAt - self.startedAt).total_seconds())
        return None
    
    def is_terminal(self) -> bool:
        """Check if session is in terminal state."""
        return self.status in [
            IdeaSessionStatus.COMPLETED,
            IdeaSessionStatus.FAILED,
            IdeaSessionStatus.CANCELLED
        ]
    
    model_config = ConfigDict(frozen=False)  # Allow updates during execution


class LiteratureItem(BaseModel):
    """
    Literature search result item.
    
    Represents a paper or article found during literature search.
    """
    id: str = Field(..., description="Unique item identifier")
    sessionId: str = Field(..., description="Parent session ID")
    title: str
    authors: List[str] = Field(default_factory=list)
    venue: Optional[str] = None
    year: Optional[int] = None
    url: Optional[str] = None
    doi: Optional[str] = None
    arxivId: Optional[str] = None
    snippet: str = Field(default="", description="Abstract or summary snippet")
    relevanceScore: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Relevance score (0-1)"
    )
    source: str = Field(
        default="stub",
        description="Source of the literature item"
    )
    createdAt: datetime = Field(default_factory=_utcnow)
    
    model_config = ConfigDict(frozen=True)


class RiskItem(BaseModel):
    """A single risk with mitigation strategy."""
    risk: str
    mitigation: str


class ExperimentSpec(BaseModel):
    """Specification for a required experiment."""
    name: str
    description: str
    metrics: List[str] = Field(default_factory=list)
    datasets: List[str] = Field(default_factory=list)


class DraftPlan(BaseModel):
    """Draft research plan that can be converted to ResearchPlan."""
    researchQuestion: str
    hypothesis: str
    variables: Dict[str, Any] = Field(default_factory=dict)
    methodology: str = ""
    expectedOutcomes: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    notes: str = ""


class IdeaCandidate(BaseModel):
    """
    Candidate research idea.
    
    Represents a generated idea with scoring and draft plan.
    """
    id: str = Field(..., description="Unique candidate identifier")
    sessionId: str = Field(..., description="Parent session ID")
    title: str
    problem: str = Field(description="Problem statement")
    keyInsight: str = Field(description="Key insight or contribution")
    
    # Scoring (0-10) — 8 criteria
    novelty: float = Field(default=5.0, ge=0, le=10, description="Novelty score")
    noveltyRationale: str = ""
    feasibility: float = Field(default=5.0, ge=0, le=10, description="Feasibility score")
    feasibilityRationale: str = ""
    impact: float = Field(default=5.0, ge=0, le=10, description="Impact score")
    impactRationale: str = ""
    clarity: float = Field(default=5.0, ge=0, le=10, description="Clarity/specificity score")
    clarityRationale: str = ""
    risk: float = Field(default=5.0, ge=0, le=10, description="Risk score (higher=lower risk)")
    riskRationale: str = ""
    alignment: float = Field(default=5.0, ge=0, le=10, description="Alignment with research direction")
    alignmentRationale: str = ""
    referenceSupport: float = Field(default=5.0, ge=0, le=10, description="Evidence/reference support quality")
    referenceSupportRationale: str = ""
    experimentSpecificity: float = Field(default=5.0, ge=0, le=10, description="Concreteness of proposed experiments")
    experimentSpecificityRationale: str = ""
    
    # Aggregate scoring metadata
    overallRationale: str = Field(default="", description="Overall scoring rationale")
    scoringConfidence: float = Field(default=0.5, ge=0, le=1, description="Confidence in scores")
    scoringMethod: str = Field(default="pending", description="How scores were determined: llm | heuristic | pending")
    
    # Details
    risks: List[RiskItem] = Field(default_factory=list)
    requiredExperiments: List[ExperimentSpec] = Field(default_factory=list)
    expectedMetrics: List[str] = Field(default_factory=list)
    
    # Draft plan for conversion to ResearchPlan
    draftPlan: Optional[DraftPlan] = None
    
    # References
    references: List[str] = Field(
        default_factory=list,
        description="List of LiteratureItem IDs or citation strings"
    )
    
    createdAt: datetime = Field(default_factory=_utcnow)
    
    @property
    def overallScore(self) -> float:
        """Calculate overall score as weighted average of all 8 criteria."""
        return round(
            self.novelty * 0.20
            + self.feasibility * 0.20
            + self.impact * 0.20
            + self.clarity * 0.10
            + self.risk * 0.10
            + self.alignment * 0.10
            + self.referenceSupport * 0.05
            + self.experimentSpecificity * 0.05,
            2,
        )
    
    @property
    def scoreBreakdown(self) -> dict:
        """Return full score breakdown dict for API responses."""
        return {
            "novelty": {"value": round(self.novelty, 1), "rationale": self.noveltyRationale},
            "feasibility": {"value": round(self.feasibility, 1), "rationale": self.feasibilityRationale},
            "impact": {"value": round(self.impact, 1), "rationale": self.impactRationale},
            "clarity": {"value": round(self.clarity, 1), "rationale": self.clarityRationale},
            "risk": {"value": round(self.risk, 1), "rationale": self.riskRationale},
            "alignment": {"value": round(self.alignment, 1), "rationale": self.alignmentRationale},
            "referenceSupport": {"value": round(self.referenceSupport, 1), "rationale": self.referenceSupportRationale},
            "experimentSpecificity": {"value": round(self.experimentSpecificity, 1), "rationale": self.experimentSpecificityRationale},
        }

    model_config = ConfigDict(frozen=True)


# =============================================================================
# Dual-Graph Workflow Models (Phase 1: LiteratureGraph + Deep Reading)
# =============================================================================


def _normalize_title(title: str) -> str:
    """Normalize a title for dedup hashing: lowercase, strip punctuation, collapse whitespace."""
    return _re.sub(r'\s+', ' ', _re.sub(r'[^\w\s]', '', title.lower())).strip()


def _compute_title_hash(title: str) -> str:
    """Compute SHA256 hash of normalized title for dedup."""
    return hashlib.sha256(_normalize_title(title).encode('utf-8')).hexdigest()


# --- Step 1 Output: QueryPlan ---

class BFTSConfig(BaseModel):
    """BFTS search configuration carried through the pipeline.

    Conservative defaults to avoid overwhelming Relay API with too many
    concurrent LLM calls (beamWidth * maxReflectionRounds = total calls).
    """
    maxNodes: int = Field(default=10, ge=5, le=200)
    maxIterations: int = Field(default=2, ge=1, le=10)
    beamWidth: int = Field(default=2, ge=1, le=20)
    expansionWidth: int = Field(default=2, ge=1, le=10)
    maxLiteratureProbes: int = Field(default=6, ge=0, le=100)
    maxReflectionRounds: int = Field(default=1, ge=1, le=10)
    minEvidenceSupport: float = Field(default=0.45, ge=0.0, le=1.0)
    minGraphGrounding: float = Field(default=0.55, ge=0.0, le=1.0)
    pruneDuplicateThreshold: float = Field(default=0.82, ge=0.0, le=1.0)
    scoreWeights: Dict[str, float] = Field(default_factory=lambda: {
        "novelty": 0.35, "feasibility": 0.20, "impact": 0.15,
        "specificity": 0.10, "evidenceSupport": 0.10, "graphGrounding": 0.10,
    })
    model_config = ConfigDict(frozen=True)


class QueryFamily(BaseModel):
    """A family of related search queries."""
    id: str = Field(default="", description="Unique family ID")
    name: str = Field(..., description="Label, e.g. 'core', 'frontier'")
    query: str = Field(default="", description="Primary query string")
    queries: List[str] = Field(default_factory=list)
    keyConcepts: List[str] = Field(default_factory=list)
    intent: str = Field(default="core", description="core | method | dataset | metric | adjacent | contradiction | survey")
    priority: float = Field(default=1.0, ge=0.0, le=2.0)
    model_config = ConfigDict(frozen=True)


class QueryPlan(BaseModel):
    """Output of expandQuery step -- structured search strategy."""
    refinedQuestion: str
    queryFamilies: List[QueryFamily] = Field(default_factory=list)
    expandedTerms: List[str] = Field(default_factory=list)
    keyConcepts: List[str] = Field(default_factory=list)
    pathTemplates: List[str] = Field(default_factory=list)
    bftsConfig: BFTSConfig = Field(default_factory=BFTSConfig)
    model_config = ConfigDict(frozen=True)


# --- Step 2 Output: RawPaper + LiteratureGraph v0 ---

class RawPaper(BaseModel):
    """A paper retrieved from literature search, before structured extraction."""
    id: str = Field(..., description="Unique ID, prefixed 'raw_'")
    sessionId: str
    title: str
    authors: List[str] = Field(default_factory=list)
    year: Optional[int] = None
    venue: Optional[str] = None
    url: str = ""
    doi: Optional[str] = None
    arxivId: Optional[str] = None
    openalexId: Optional[str] = None
    semanticScholarId: Optional[str] = None
    citationCount: int = 0
    abstract: str = ""
    source: List[str] = Field(default_factory=list, description="List of sources: semantic_scholar, arxiv, local, openalex, crossref")
    normalizedTitleHash: str = Field(default="", description="SHA256 of normalized title for dedup")
    references: List[str] = Field(default_factory=list, description="Paper IDs cited by this paper")
    citedBy: List[str] = Field(default_factory=list, description="Paper IDs citing this paper")
    concepts: List[str] = Field(default_factory=list, description="Concept tags from source APIs")
    retrievalScore: float = Field(default=0.0, ge=0.0, le=1.0)
    relevanceScore: float = Field(default=0.0, ge=0.0, le=1.0)
    createdAt: datetime = Field(default_factory=_utcnow)
    model_config = ConfigDict(frozen=True)


class PaperNode(BaseModel):
    """A node in the LiteratureGraph representing a paper."""
    paperId: str
    title: str
    year: Optional[int] = None
    relevanceScore: float = 0.0
    citationScore: float = 0.0
    recencyScore: float = 0.0
    centralityScore: float = 0.0
    clusterId: Optional[str] = None
    role: Optional[str] = Field(default=None, description="core, representative, frontier, bridge, contradiction, must_cite")
    isSelected: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Extra computed metrics: degree_centrality, betweenness, clustering_coefficient")
    model_config = ConfigDict(frozen=True)


class PaperEdge(BaseModel):
    """An edge in the LiteratureGraph."""
    sourceId: str
    targetId: str
    edgeType: str = Field(..., description="semantic_similar, citation, concept, author, evidence")
    weight: float = Field(default=0.5, ge=0.0, le=1.0)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    model_config = ConfigDict(frozen=True)


class LiteratureCluster(BaseModel):
    """A cluster of papers in the LiteratureGraph."""
    clusterId: str
    label: str = ""
    paperIds: List[str] = Field(default_factory=list)
    centroidPaperId: Optional[str] = None
    themeTokens: List[str] = Field(default_factory=list)
    model_config = ConfigDict(frozen=True)


class LiteratureGraph(BaseModel):
    """Graph 1: paper-level literature graph. Version 0 after Step 2, version 1 after Step 3."""
    id: str = Field(..., description="Unique graph ID, prefixed 'lg_'")
    sessionId: str
    version: int = Field(default=0, description="0 after Step 2, 1 after Step 3")
    nodes: List[PaperNode] = Field(default_factory=list)
    edges: List[PaperEdge] = Field(default_factory=list)
    clusters: List[LiteratureCluster] = Field(default_factory=list)
    createdAt: datetime = Field(default_factory=_utcnow)
    model_config = ConfigDict(frozen=True)


# --- Step 3 Output: StructuredPaper + LiteratureMap ---

class Claim(BaseModel):
    """A claim extracted from a paper."""
    claimId: str = Field(..., description="Auto-generated, prefixed 'cl_'")
    paperId: str
    text: str
    claimType: str = Field(default="finding", description="finding, method, comparison, limitation, assumption, hypothesis, gap, premise_conclusion, cause_effect, method_result")
    evidenceText: str = Field(default="", description="The source text supporting this claim")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    evidenceSpan: str = Field(default="", description="The sentence or passage this claim is extracted from")
    model_config = ConfigDict(frozen=True)


class ContradictionMention(BaseModel):
    """A contradiction mentioned in a paper."""
    contradictionId: str = Field(..., description="Auto-generated, prefixed 'cm_'")
    paperId: str
    description: str
    conflictingPaperIds: List[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    model_config = ConfigDict(frozen=True)


class Finding(BaseModel):
    """A research finding extracted from a paper."""
    findingId: str = Field(..., description="Auto-generated, prefixed 'fn_'")
    paperId: str
    description: str
    category: str = Field(default="empirical", description="empirical, theoretical, methodological, negative")
    relatedClaims: List[str] = Field(default_factory=list)
    model_config = ConfigDict(frozen=True)


class MethodMention(BaseModel):
    """A method mentioned in a paper."""
    methodId: str = Field(..., description="Auto-generated, prefixed 'mm_'")
    paperId: str
    name: str
    description: str = ""
    category: str = Field(default="algorithm", description="algorithm, framework, metric, dataset, technique")
    model_config = ConfigDict(frozen=True)


class NoveltyEvidence(BaseModel):
    """Evidence for or against novelty of a direction."""
    evidenceId: str = Field(..., description="Auto-generated, prefixed 'ne_'")
    paperId: str
    evidenceType: str = Field(default="sparse_combination", description="sparse_combination, emerging_method, underexplored_dataset, contradiction, weak_baseline, missing_evaluation")
    direction: str
    description: str = ""
    assessment: str = Field(default="neutral", description="supports, contradicts, overlaps, neutral")
    paperIds: List[str] = Field(default_factory=list)
    clusterIds: List[str] = Field(default_factory=list)
    entityHints: List[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    rationale: str = ""
    model_config = ConfigDict(frozen=True)


class StructuredPaper(BaseModel):
    """A paper after deep-reading with structured extraction."""
    id: str = Field(..., description="Matches RawPaper.id")
    sessionId: str
    rawPaperId: str
    title: str
    abstract: str = ""
    authors: List[str] = Field(default_factory=list)
    year: Optional[int] = None
    venue: str = ""
    citationCount: int = 0
    source: List[str] = Field(default_factory=list)
    graph1Roles: List[str] = Field(default_factory=list, description="core, representative, frontier, bridge, contradiction, must_cite")
    claims: List[Claim] = Field(default_factory=list)
    findings: List[Finding] = Field(default_factory=list)
    methods: List[MethodMention] = Field(default_factory=list)
    datasets: List[str] = Field(default_factory=list)
    metrics: List[str] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)
    baselines: List[str] = Field(default_factory=list)
    contradictions: List[ContradictionMention] = Field(default_factory=list)
    noveltyEvidence: List[NoveltyEvidence] = Field(default_factory=list)
    summary: str = ""
    extractionMethod: str = Field(default="llm", description="llm, heuristic, hybrid")
    qualityScore: float = Field(default=0.0, ge=0.0, le=1.0)
    extractionConfidence: float = Field(default=0.5, ge=0.0, le=1.0)
    createdAt: datetime = Field(default_factory=_utcnow)
    model_config = ConfigDict(frozen=True)


class GapEvidence(BaseModel):
    """Structured gap evidence in LiteratureMap."""
    direction: str = ""
    evidence: str = ""
    paperIds: List[str] = Field(default_factory=list)
    clusterIds: List[str] = Field(default_factory=list)
    entityHints: List[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    model_config = ConfigDict(frozen=True)


class FrontierSignal(BaseModel):
    """Frontier signal in LiteratureMap."""
    paperId: str
    direction: str = ""
    entityHints: List[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    model_config = ConfigDict(frozen=True)


class LiteratureMap(BaseModel):
    """Structured map of the literature space produced in Step 3."""
    id: str = Field(..., description="Unique map ID, prefixed 'lm_'")
    sessionId: str
    paperCount: int = 0
    clusters: List[LiteratureCluster] = Field(default_factory=list)
    frontiers: List[FrontierSignal] = Field(default_factory=list)
    gaps: List[GapEvidence] = Field(default_factory=list)
    noveltyEvidence: List[NoveltyEvidence] = Field(default_factory=list)
    selectedPaperIds: List[str] = Field(default_factory=list)
    selectionReport: Dict[str, Any] = Field(default_factory=dict)
    createdAt: datetime = Field(default_factory=_utcnow)
    model_config = ConfigDict(frozen=True)


# --- Step 4→5 Handoff: BFTSHandoff (Phase 1: preliminary, Phase 2: enriched) ---

class BFTSHandoff(BaseModel):
    """Handoff from Step 4 to Step 5. Preliminary in Phase 1, enriched in Phase 2."""
    id: str = Field(..., description="Unique handoff ID, prefixed 'bh_'")
    sessionId: str
    reasoningKgId: Optional[str] = Field(default=None, description="Graph 2 ID; None until Phase 2")
    literatureMapId: str
    pathSeedIds: List[str] = Field(default_factory=list, description="Empty until Phase 2")
    selectedPaperIds: List[str] = Field(default_factory=list)
    bftsConfig: BFTSConfig = Field(default_factory=BFTSConfig)
    createdAt: datetime = Field(default_factory=_utcnow)
    model_config = ConfigDict(frozen=True)


# --- Phase 2 Models (forward declaration: Graph 2 + Path Seeds) ---

class KGEntity(BaseModel):
    """An entity in the ReasoningKG (Graph 2). Phase 2 implementation."""
    entityId: str = Field(..., description="Auto-generated, prefixed 'ke_'")
    name: str
    entityType: str = Field(default="concept", description="concept, method, metric, dataset, claim, gap")
    normalizedName: str = ""
    sourcePaperIds: List[str] = Field(default_factory=list)
    sourceClaimIds: List[str] = Field(default_factory=list)
    importanceScore: float = Field(default=0.0, ge=0.0, le=1.0)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    model_config = ConfigDict(frozen=True)


class KGRelation(BaseModel):
    """A relation between entities in the ReasoningKG (Graph 2). Phase 2 implementation."""
    relationId: str = Field(..., description="Auto-generated, prefixed 'kr_'")
    sourceEntityId: str
    targetEntityId: str
    relationType: str = Field(..., description="implies (deduction), hypothesizes (abduction), generalizes (induction), supports, contradicts, uses, produces")
    weight: float = Field(default=0.5, ge=0.0, le=1.0)
    sourcePaperIds: List[str] = Field(default_factory=list)
    sourceClaimIds: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    model_config = ConfigDict(frozen=True)


class ReasoningKG(BaseModel):
    """Graph 2: concept-level reasoning knowledge graph. Phase 2 implementation."""
    id: str = Field(..., description="Unique graph ID, prefixed 'rkg_'")
    sessionId: str
    literatureGraphId: str
    literatureMapId: str
    entities: List[KGEntity] = Field(default_factory=list)
    relations: List[KGRelation] = Field(default_factory=list)
    createdAt: datetime = Field(default_factory=_utcnow)
    model_config = ConfigDict(frozen=True)


class GraphEvidenceLink(BaseModel):
    """Links a Graph 1 signal to Graph 2 entities/relations. Phase 2 implementation."""
    linkId: str = Field(..., description="Auto-generated, prefixed 'gel_'")
    signalType: str = Field(..., description="cluster, frontier, gap, contradiction, novelty")
    signalId: str
    targetEntityIds: List[str] = Field(default_factory=list)
    targetRelationIds: List[str] = Field(default_factory=list)
    evidenceType: str = Field(default="semantic", description="semantic (from text) or symbolic (from structured claims)")
    rationale: str = ""
    model_config = ConfigDict(frozen=True)


class PathSeedStep(BaseModel):
    """A single step in a reasoning path seed."""
    stepIndex: int
    stepType: str = Field(default="observation", description="observation, gap, method, mechanism, prediction, validation")
    entityId: str
    relationId: Optional[str] = None
    text: str = ""
    description: str = ""
    required: bool = True
    evidencePaperIds: List[str] = Field(default_factory=list)
    model_config = ConfigDict(frozen=True)


class PathSeedScores(BaseModel):
    """Scores for a reasoning path seed."""
    noveltyPrior: float = Field(default=0.0, ge=0.0, le=1.0)
    feasibilityPrior: float = Field(default=0.0, ge=0.0, le=1.0)
    evidencePrior: float = Field(default=0.0, ge=0.0, le=1.0)
    graphAlignmentPrior: float = Field(default=0.0, ge=0.0, le=1.0)
    model_config = ConfigDict(frozen=True)


class ReasoningPathSeed(BaseModel):
    """A reasoning path seed for BFTS exploration."""
    seedId: str = Field(..., description="Auto-generated, prefixed 'rps_'")
    sessionId: str
    reasoningKgId: str
    templateType: str = Field(default="generic", description="algorithm, system, benchmark, theory, survey, generic")
    anchorEntityIds: List[str] = Field(default_factory=list)
    steps: List[PathSeedStep] = Field(default_factory=list)
    skeleton: List[PathSeedStep] = Field(default_factory=list)
    sourcePaperIds: List[str] = Field(default_factory=list)
    sourceClaimIds: List[str] = Field(default_factory=list)
    evidenceLinkIds: List[str] = Field(default_factory=list)
    linkedGapIds: List[str] = Field(default_factory=list)
    linkedFrontierIds: List[str] = Field(default_factory=list)
    linkedNoveltyEvidenceIds: List[str] = Field(default_factory=list)
    paperTypes: List[str] = Field(default_factory=list)
    initialScores: Optional[PathSeedScores] = None
    scores: Optional[PathSeedScores] = None
    rationale: str = ""
    createdAt: datetime = Field(default_factory=_utcnow)
    model_config = ConfigDict(frozen=True)

# --- Step 5: BFTS Search Tree Nodes ---


def generate_candidate_id() -> str:
    """Generate a unique IdeaCandidate ID."""
    import uuid
    return "ic_" + uuid.uuid4().hex[:12]

def generate_idea_node_id() -> str:
    """Generate a unique IdeaNode ID."""
    import uuid
    return "in_" + uuid.uuid4().hex[:12]


class IdeaNode(BaseModel):
    """BFTS search tree node representing a research idea being explored.

    Each node can be expanded (generating child ideas via ReflectionLoop)
    or terminal (finalized by the LLM via FinalizeIdea action).
    """
    nodeId: str = Field(default_factory=generate_idea_node_id)
    sessionId: str
    parentNodeId: Optional[str] = Field(default=None, description="None for root nodes")
    depth: int = Field(default=0, ge=0, le=10)

    # Idea content (populated by ReflectionLoop)
    title: str = ""
    hypothesis: str = ""
    abstract: str = ""
    experiments: List[Dict[str, Any]] = Field(default_factory=list)
    risks: List[Dict[str, str]] = Field(default_factory=list)

    # Scoring (computed by BFTSSearchTree._score_node)
    noveltyScore: float = Field(default=0.0, ge=0.0, le=10.0)
    feasibilityScore: float = Field(default=0.0, ge=0.0, le=10.0)
    impactScore: float = Field(default=0.0, ge=0.0, le=10.0)
    specificityScore: float = Field(default=0.0, ge=0.0, le=1.0)
    evidenceSupportScore: float = Field(default=0.0, ge=0.0, le=1.0)
    graphGroundingScore: float = Field(default=0.0, ge=0.0, le=1.0)
    combinedScore: float = Field(default=0.0, ge=0.0, le=10.0)

    # Source tracking
    sourceSeedId: Optional[str] = Field(default=None, description="ReasoningPathSeed.seedId that spawned this node")
    reflectionRounds: int = Field(default=0, ge=0, le=20)

    # Reflection history (for debugging / replay)
    reflectionHistory: List[str] = Field(default_factory=list, description="LLM response texts from each reflection round")

    # Status
    isExpanded: bool = False
    isTerminal: bool = False
    finalizedAt: Optional[datetime] = None
    createdAt: datetime = Field(default_factory=_utcnow)

    model_config = ConfigDict(frozen=False)  # Mutable: mutated by ReflectionLoop & BFTSSearchTree
