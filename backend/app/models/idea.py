"""
Idea Generation Domain Models

Scientific Responsibility:
- Represent idea generation sessions and their outputs
- Track literature search results
- Store candidate ideas with scoring
- Maintain full traceability from session to candidates
"""

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
        default=10,
        ge=1,
        le=50,
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
