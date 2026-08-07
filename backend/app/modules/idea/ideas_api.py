"""
Idea Generation API Endpoints

Provides endpoints for managing idea generation sessions.
"""

import json
import logging
from typing import Any, Dict, Optional, List
from datetime import datetime
from fastapi import APIRouter, HTTPException, status, BackgroundTasks, Query
from pydantic import BaseModel, Field

from app.modules.idea.contracts import (
    IdeaSession,
    IdeaSessionStatus,
    IdeaSessionConfig,
    IdeaCandidate,
    LiteratureItem,
    WorkflowTrace,
    StepResult,
)
from app.modules.idea.service import get_idea_service
from app.modules.idea.storage import (
    get_raw_paper_storage,
    get_literature_graph_storage,
    get_structured_paper_storage,
    get_literature_map_storage,
    get_handoff_storage,
    get_reasoning_kg_storage,
    get_evidence_link_storage,
    get_path_seed_storage,
    get_ranked_output_storage,
    get_search_tree_storage,
    get_graph_patch_storage,
    get_probe_literature_storage,
)
from app.core.settings import get_settings
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ideas", tags=["ideas"])


# Request/Response Schemas

class CreateSessionRequest(BaseModel):
    """Request to create an idea generation session."""
    providerName: Optional[str] = Field(default=None)
    model: Optional[str] = Field(default=None)
    directionId: Optional[str] = None
    seedQuery: str = Field(..., min_length=3)
    paperType: str = Field(default="algorithm", description="Type of paper: algorithm, system, application, benchmark, survey, position, theory, evaluation, reproducibility, safety")
    maxCandidates: int = Field(default=5, ge=1, le=20)
    maxPapers: int = Field(default=120, ge=1, le=200)
    domain: Optional[str] = None
    constraints: Optional[List[str]] = None
    mustCiteList: Optional[List[str]] = None
    searchBudget: Optional[int] = Field(default=None, ge=10, le=500)
    maxReviewIterations: int = Field(default=3, ge=1, le=5)


class SessionResponse(BaseModel):
    """Response for session operations."""
    id: str
    createdAt: str
    status: str
    config: dict
    startedAt: Optional[str] = None
    endedAt: Optional[str] = None
    duration: Optional[int] = None
    candidateIds: List[str] = []
    finalCandidateIds: List[str] = []
    hiddenCandidateIds: List[str] = []
    rejectedCandidateIds: List[str] = []
    qualityLoopSummary: dict = {}
    selectedCandidateId: Optional[str] = None
    errorMessage: Optional[str] = None


class TraceResponse(BaseModel):
    """Response for session trace."""
    sessionId: str
    startedAt: Optional[str] = None
    endedAt: Optional[str] = None
    totalSteps: int = 0
    successfulSteps: int = 0
    failedSteps: int = 0
    steps: List[dict] = []


class LiteratureResponse(BaseModel):
    """Response for literature items."""
    items: List[dict]
    total: int


class CandidateResponse(BaseModel):
    """Response for a single candidate (PDF v5 compatible)."""
    id: str
    sessionId: str
    title: str
    # PDF v5 traceability
    searchNodeId: Optional[str] = None
    pathSeedId: Optional[str] = None
    reasoningPathId: Optional[str] = None
    # Core content
    problem: str
    hypothesisStatement: str = ""
    keyInsight: str
    proposedMethod: str = ""
    expectedOutcome: str = ""
    # Scoring
    novelty: float
    noveltyRationale: str
    feasibility: float
    feasibilityRationale: str
    impact: float
    impactRationale: str
    clarity: float = 5.0
    clarityRationale: str = ""
    risk: float = 5.0
    riskRationale: str = ""
    alignment: float = 5.0
    alignmentRationale: str = ""
    referenceSupport: float = 5.0
    referenceSupportRationale: str = ""
    experimentSpecificity: float = 5.0
    experimentSpecificityRationale: str = ""
    overallScore: float
    scoreBreakdown: dict = {}
    scores: dict = {}
    overallRationale: str = ""
    scoringConfidence: float = 0.5
    scoringMethod: str = "pending"
    # Details
    risks: List[dict] = []
    requiredExperiments: List[dict] = []
    expectedMetrics: List[str] = []
    draftPlan: Optional[dict] = None
    references: List[str] = []
    # PDF v5 embedded evidence
    graphEvidence: Optional[dict] = None
    closestPriorWork: List[dict] = []
    critique: Optional[dict] = None
    createdAt: str


class CandidatesResponse(BaseModel):
    """Response for candidates list."""
    candidates: List[CandidateResponse]
    total: int
    view: str = "final"
    allCandidateCount: int = 0


class SelectCandidateRequest(BaseModel):
    """Request to select a candidate."""
    candidateId: str


class SelectCandidateResponse(BaseModel):
    """Response after selecting a candidate."""
    ok: bool
    candidateId: str
    selectedCandidateId: str


class SessionListResponse(BaseModel):
    """Response for listing sessions."""
    sessions: List[SessionResponse]
    total: int


# --- Dual-Graph Response Schemas ---

class QueryPlanResponse(BaseModel):
    """Response for query plan."""
    refinedQuestion: str
    queryFamilies: List[dict]
    expandedTerms: List[str]
    keyConcepts: List[str]
    pathTemplates: List[str]
    bftsConfig: dict


class RawPapersResponse(BaseModel):
    """Response for raw papers list."""
    papers: List[dict]
    total: int


class LiteratureGraphResponse(BaseModel):
    """Response for literature graph."""
    id: str
    sessionId: str
    version: int
    nodes: List[dict]
    edges: List[dict]
    clusters: List[dict]
    createdAt: str


class LiteratureMapResponse(BaseModel):
    """Response for literature map."""
    id: str
    sessionId: str
    paperCount: int = 0
    clusters: List[dict]
    frontiers: List[dict]
    gaps: List[dict]
    noveltyEvidence: List[dict]
    selectedPaperIds: List[str]
    selectionReport: dict = {}
    createdAt: str


class StructuredPapersResponse(BaseModel):
    """Response for structured papers list."""
    papers: List[dict]
    total: int


class BFTSHandoffResponse(BaseModel):
    """Response for BFTS handoff."""
    id: str
    sessionId: str
    reasoningKgId: Optional[str] = None
    literatureMapId: str
    pathSeedIds: List[str]
    selectedPaperIds: List[str]
    bftsConfig: dict
    createdAt: str


class ReasoningKGResponse(BaseModel):
    """Response for reasoning knowledge graph (Graph 2)."""
    id: str
    sessionId: str
    literatureGraphId: str
    literatureMapId: str
    entityCount: int
    relationCount: int
    entities: List[dict]
    relations: List[dict]
    createdAt: str


class PathSeedsResponse(BaseModel):
    """Response for reasoning path seeds."""
    seeds: List[dict]
    total: int


class RankedIdeaOutputResponse(BaseModel):
    """Response for Step 6 ranking output."""
    id: str
    sessionId: str
    rankedCandidates: List[dict]
    evidence: List[dict]
    priorWorkComparisons: List[dict]
    critiques: List[dict]
    scoreVariance: float
    minScore: float
    maxScore: float
    rankedCount: int
    topCandidateId: Optional[str] = None
    rankingMethod: str
    createdAt: str


class SearchTreeResponse(BaseModel):
    """Response for Step 5 BFTS search tree."""
    id: str
    sessionId: str
    rootNodeIds: List[str]
    nodeCount: int
    edgeCount: int
    nodes: List[dict]
    edges: List[dict]
    config: dict
    searchReport: dict
    createdAt: str


class GraphPatchesResponse(BaseModel):
    """Response for Step 5 graph patches."""
    patches: List[dict]
    total: int


class ProbeResultsResponse(BaseModel):
    """Response for Step 5 literature probe results."""
    results: List[dict]
    total: int


def _session_to_response(session: IdeaSession) -> SessionResponse:
    """Convert session to response format."""
    return SessionResponse(
        id=session.id,
        createdAt=session.createdAt.isoformat() if session.createdAt else "",
        status=session.status.value,
        config=session.config.model_dump(),
        startedAt=session.startedAt.isoformat() if session.startedAt else None,
        endedAt=session.endedAt.isoformat() if session.endedAt else None,
        duration=session.duration,
        candidateIds=session.candidateIds,
        finalCandidateIds=getattr(session, "finalCandidateIds", []),
        hiddenCandidateIds=getattr(session, "hiddenCandidateIds", []),
        rejectedCandidateIds=getattr(session, "rejectedCandidateIds", []),
        qualityLoopSummary=getattr(session, "qualityLoopSummary", {}),
        selectedCandidateId=session.selectedCandidateId,
        errorMessage=session.errorMessage,
    )


def _candidate_to_response(candidate: IdeaCandidate) -> CandidateResponse:
    """Convert candidate to response format (PDF v5 compatible)."""
    def _dump_optional(value):
        if value is None:
            return None
        return value.model_dump() if hasattr(value, 'model_dump') else value

    return CandidateResponse(
        id=candidate.id,
        sessionId=candidate.sessionId,
        title=candidate.title,
        # PDF v5 traceability
        searchNodeId=getattr(candidate, 'searchNodeId', None),
        pathSeedId=getattr(candidate, 'pathSeedId', None),
        reasoningPathId=getattr(candidate, 'reasoningPathId', None),
        # Core content
        problem=candidate.problem,
        hypothesisStatement=getattr(candidate, 'hypothesisStatement', '') or '',
        keyInsight=candidate.keyInsight,
        proposedMethod=getattr(candidate, 'proposedMethod', '') or '',
        expectedOutcome=getattr(candidate, 'expectedOutcome', '') or '',
        # Scoring
        novelty=candidate.novelty,
        noveltyRationale=candidate.noveltyRationale,
        feasibility=candidate.feasibility,
        feasibilityRationale=candidate.feasibilityRationale,
        impact=candidate.impact,
        impactRationale=candidate.impactRationale,
        clarity=getattr(candidate, 'clarity', 5.0),
        clarityRationale=getattr(candidate, 'clarityRationale', ''),
        risk=getattr(candidate, 'risk', 5.0),
        riskRationale=getattr(candidate, 'riskRationale', ''),
        alignment=getattr(candidate, 'alignment', 5.0),
        alignmentRationale=getattr(candidate, 'alignmentRationale', ''),
        referenceSupport=getattr(candidate, 'referenceSupport', 5.0),
        referenceSupportRationale=getattr(candidate, 'referenceSupportRationale', ''),
        experimentSpecificity=getattr(candidate, 'experimentSpecificity', 5.0),
        experimentSpecificityRationale=getattr(candidate, 'experimentSpecificityRationale', ''),
        overallScore=candidate.overallScore,
        scoreBreakdown=candidate.scoreBreakdown,
        scores=getattr(candidate, 'scores', None).model_dump() if getattr(candidate, 'scores', None) else {},
        overallRationale=getattr(candidate, 'overallRationale', ''),
        scoringConfidence=getattr(candidate, 'scoringConfidence', 0.5),
        scoringMethod=getattr(candidate, 'scoringMethod', 'pending'),
        # Details
        risks=[r.model_dump() for r in candidate.risks],
        requiredExperiments=[e.model_dump() for e in candidate.requiredExperiments],
        expectedMetrics=candidate.expectedMetrics,
        draftPlan=candidate.draftPlan.model_dump() if candidate.draftPlan else None,
        references=candidate.references,
        # PDF v5 embedded evidence
        graphEvidence=_dump_optional(getattr(candidate, 'graphEvidence', None)),
        closestPriorWork=[p.model_dump() if hasattr(p, 'model_dump') else p for p in (getattr(candidate, 'closestPriorWork', None) or [])],
        critique=_dump_optional(getattr(candidate, 'critique', None)),
        createdAt=candidate.createdAt.isoformat() if candidate.createdAt else "",
    )


# Endpoints

@router.post(
    "/sessions",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Idea Session",
    description="Create a new idea generation session."
)
async def create_session(request: CreateSessionRequest) -> SessionResponse:
    """Create a new idea generation session."""
    service = get_idea_service()
    settings = get_settings()
    provider_name = request.providerName or settings.get_active_provider()
    model_name = request.model or settings.get_active_model(provider_name)
    
    config = IdeaSessionConfig(
        providerName=provider_name,
        model=model_name,
        directionId=request.directionId,
        seedQuery=request.seedQuery,
        paperType=request.paperType,
        maxCandidates=request.maxCandidates,
        maxPapers=request.maxPapers,
        domain=request.domain,
        constraints=request.constraints,
        mustCiteList=request.mustCiteList,
        searchBudget=request.searchBudget,
        maxReviewIterations=request.maxReviewIterations,
    )
    
    session = service.create_session(config)
    return _session_to_response(session)


@router.get(
    "/sessions",
    response_model=SessionListResponse,
    summary="List Idea Sessions",
    description="List all idea generation sessions."
)
async def list_sessions(status_filter: Optional[str] = None) -> SessionListResponse:
    """List all sessions."""
    service = get_idea_service()
    
    status_enum = None
    if status_filter:
        try:
            status_enum = IdeaSessionStatus(status_filter)
        except ValueError:
            pass
    
    sessions = service.list_sessions(status_enum)
    return SessionListResponse(
        sessions=[_session_to_response(s) for s in sessions],
        total=len(sessions),
    )


@router.get(
    "/sessions/{session_id}",
    response_model=SessionResponse,
    summary="Get Idea Session",
    description="Get an idea generation session by ID."
)
async def get_session(session_id: str) -> SessionResponse:
    """Get session by ID."""
    service = get_idea_service()
    session = service.get_session(session_id)
    
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found"
        )
    
    return _session_to_response(session)


@router.post(
    "/sessions/{session_id}/start",
    response_model=SessionResponse,
    summary="Start Idea Session",
    description="Start an idea generation session and run the pipeline."
)
async def start_session(
    session_id: str,
    background_tasks: BackgroundTasks
) -> SessionResponse:
    """Start a session and run pipeline in background."""
    service = get_idea_service()
    
    try:
        session = service.start_session(session_id)
        
        # Run pipeline in background
        background_tasks.add_task(service.run_pipeline, session_id)
        
        return _session_to_response(session)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.post(
    "/sessions/{session_id}/resume",
    response_model=SessionResponse,
    summary="Resume Idea Session",
    description="Resume a session waiting for evidence or approved ideas.",
)
async def resume_session(
    session_id: str,
    background_tasks: BackgroundTasks,
) -> SessionResponse:
    service = get_idea_service()
    try:
        session = service.resume_session(session_id)
        background_tasks.add_task(service.run_pipeline, session_id)
        return _session_to_response(session)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post(
    "/sessions/{session_id}/cancel",
    response_model=SessionResponse,
    summary="Cancel Idea Session",
    description="Cancel a running idea generation session."
)
async def cancel_session(session_id: str) -> SessionResponse:
    """Cancel a session."""
    service = get_idea_service()
    
    try:
        session = service.cancel_session(session_id)
        return _session_to_response(session)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.post(
    "/sessions/{session_id}/revalidate-final-candidates",
    response_model=SessionResponse,
    summary="Revalidate Final Idea Candidates",
    description=(
        "Run current hard quality gates on an existing idea session and update "
        "the user-facing final candidate shortlist without new LLM calls."
    )
)
async def revalidate_final_candidates(session_id: str) -> SessionResponse:
    """Revalidate final candidates for old sessions after gate upgrades."""
    service = get_idea_service()

    try:
        session = service.revalidate_final_candidates(session_id)
        return _session_to_response(session)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


@router.get(
    "/sessions/{session_id}/trace",
    response_model=TraceResponse,
    summary="Get Session Trace",
    description="Get the workflow trace for a session."
)
async def get_session_trace(session_id: str) -> TraceResponse:
    """Get session trace."""
    service = get_idea_service()
    session = service.get_session(session_id)
    
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found"
        )
    
    trace = session.trace
    if not trace:
        return TraceResponse(sessionId=session_id)
    
    return TraceResponse(
        sessionId=session_id,
        startedAt=trace.startedAt.isoformat() if trace.startedAt else None,
        endedAt=trace.endedAt.isoformat() if trace.endedAt else None,
        totalSteps=trace.totalSteps,
        successfulSteps=trace.successfulSteps,
        failedSteps=trace.failedSteps,
        steps=[
            {
                "name": s.name,
                "status": s.status,
                "inputs": s.inputs,
                "outputs": s.outputs,
                "artifacts": s.artifacts,
                "startedAt": s.startedAt.isoformat() if s.startedAt else None,
                "endedAt": s.endedAt.isoformat() if s.endedAt else None,
                "durationSeconds": s.durationSeconds,
                "error": s.error,
            }
            for s in trace.steps
        ],
    )


@router.get(
    "/sessions/{session_id}/literature",
    response_model=LiteratureResponse,
    summary="Get Session Literature",
    description="Get literature items for a session."
)
async def get_session_literature(session_id: str) -> LiteratureResponse:
    """Get literature items for a session."""
    service = get_idea_service()
    
    session = service.get_session(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found"
        )
    
    items = service.get_literature(session_id)
    return LiteratureResponse(
        items=[
            {
                "id": item.id,
                "sessionId": item.sessionId,
                "title": item.title,
                "authors": item.authors,
                "venue": item.venue,
                "year": item.year,
                "url": item.url,
                "doi": item.doi,
                "arxivId": item.arxivId,
                "snippet": item.snippet,
                "relevanceScore": item.relevanceScore,
                "source": item.source,
                "createdAt": item.createdAt.isoformat() if item.createdAt else "",
            }
            for item in items
        ],
        total=len(items),
    )


@router.get(
    "/sessions/{session_id}/candidates",
    response_model=CandidatesResponse,
    summary="Get Session Candidates",
    description="Get candidate ideas for a session."
)
async def get_session_candidates(
    session_id: str,
    view: str = Query(
        "final",
        pattern="^(final|debug|all)$",
        description="final returns user-facing shortlisted candidates; debug/all returns every generated candidate.",
    ),
) -> CandidatesResponse:
    """Get candidates for a session."""
    service = get_idea_service()
    
    session = service.get_session(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found"
        )
    
    candidates = service.get_candidates(session_id, view=view)
    all_candidate_count = len(service.get_candidates(session_id, view="debug"))
    return CandidatesResponse(
        candidates=[_candidate_to_response(c) for c in candidates],
        total=len(candidates),
        view=view,
        allCandidateCount=all_candidate_count,
    )


# =============================================================================
# Dual-Graph Endpoints (Phase 2)
# =============================================================================


@router.get(
    "/sessions/{session_id}/graph/reasoning",
    response_model=ReasoningKGResponse,
    summary="Get Reasoning Knowledge Graph",
    description="Get the concept-level reasoning knowledge graph (Graph 2)."
)
async def get_reasoning_graph(session_id: str) -> ReasoningKGResponse:
    """Get reasoning KG for a session."""
    service = get_idea_service()
    session = service.get_session(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found"
        )

    kg_storage = get_reasoning_kg_storage()
    kg = kg_storage.get_by_session(session_id)
    if not kg:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Reasoning KG not yet generated. Run the pipeline first."
        )

    return ReasoningKGResponse(
        id=kg.id,
        sessionId=kg.sessionId,
        literatureGraphId=kg.literatureGraphId,
        literatureMapId=kg.literatureMapId,
        entityCount=len(kg.entities),
        relationCount=len(kg.relations),
        entities=[e.model_dump() for e in kg.entities],
        relations=[r.model_dump() for r in kg.relations],
        createdAt=kg.createdAt.isoformat() if kg.createdAt else "",
    )


@router.get(
    "/sessions/{session_id}/path-seeds",
    response_model=PathSeedsResponse,
    summary="Get Reasoning Path Seeds",
    description="Get the reasoning path seeds for BFTS exploration."
)
async def get_path_seeds(session_id: str) -> PathSeedsResponse:
    """Get path seeds for a session."""
    service = get_idea_service()
    session = service.get_session(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found"
        )

    seed_storage = get_path_seed_storage()
    seeds = seed_storage.list_by_session(session_id)
    if not seeds:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Path seeds not yet generated. Run the pipeline first."
        )

    return PathSeedsResponse(
        seeds=[s.model_dump() for s in seeds],
        total=len(seeds),
    )


# =============================================================================
# Step 6 Endpoint: Ranking Output
# =============================================================================


@router.get(
    "/sessions/{session_id}/ranking-output",
    response_model=RankedIdeaOutputResponse,
    summary="Get Ranking Output (Step 6)",
    description="Get the full Step 6 ranking output with evidence binding, prior work comparisons, and critiques."
)
async def get_ranking_output(session_id: str) -> RankedIdeaOutputResponse:
    """Get Step 6 ranking output for a session."""
    service = get_idea_service()
    session = service.get_session(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found"
        )

    ranked_storage = get_ranked_output_storage()
    ranked_output = ranked_storage.get_by_session(session_id)
    if not ranked_output:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ranking output not yet generated. Run the pipeline to Step 6 first."
        )

    return RankedIdeaOutputResponse(
        id=ranked_output.id,
        sessionId=ranked_output.sessionId,
        rankedCandidates=[c.model_dump() for c in ranked_output.rankedCandidates],
        evidence=[e.model_dump() for e in ranked_output.evidence],
        priorWorkComparisons=[p.model_dump() for p in ranked_output.priorWorkComparisons],
        critiques=[c.model_dump() for c in ranked_output.critiques],
        scoreVariance=ranked_output.scoreVariance,
        minScore=ranked_output.minScore,
        maxScore=ranked_output.maxScore,
        rankedCount=ranked_output.rankedCount,
        topCandidateId=ranked_output.topCandidateId,
        rankingMethod=ranked_output.rankingMethod,
        createdAt=ranked_output.createdAt.isoformat() if ranked_output.createdAt else "",
    )


# =============================================================================
# Step 5 Endpoints: Search Tree + Graph Patches + Probe Results (PDF v5)
# =============================================================================


@router.get(
    "/sessions/{session_id}/search-tree",
    response_model=SearchTreeResponse,
    summary="Get BFTS Search Tree",
    description="Get the full BFTS idea search tree (Step 5 output)."
)
async def get_search_tree(session_id: str) -> SearchTreeResponse:
    """Get BFTS search tree for a session."""
    service = get_idea_service()
    session = service.get_session(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found"
        )

    tree_storage = get_search_tree_storage()
    tree = tree_storage.get_by_session(session_id)
    if not tree:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Search tree not yet generated. Run the pipeline through Step 5 first."
        )

    return SearchTreeResponse(
        id=tree.id,
        sessionId=tree.sessionId,
        rootNodeIds=tree.rootNodeIds,
        nodeCount=len(tree.nodes),
        edgeCount=len(tree.edges),
        nodes=[n.model_dump() for n in tree.nodes],
        edges=[e.model_dump() for e in tree.edges],
        config=tree.config.model_dump(),
        searchReport=tree.searchReport.model_dump(),
        createdAt=tree.createdAt.isoformat() if tree.createdAt else "",
    )


@router.get(
    "/sessions/{session_id}/graph-patches",
    response_model=GraphPatchesResponse,
    summary="Get Graph Patches",
    description="Get graph patches applied during BFTS literature probes (Step 5)."
)
async def get_graph_patches(session_id: str) -> GraphPatchesResponse:
    """Get graph patches for a session."""
    service = get_idea_service()
    session = service.get_session(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found"
        )

    patch_storage = get_graph_patch_storage()
    patches = patch_storage.list_by_session(session_id)

    return GraphPatchesResponse(
        patches=[p.model_dump() for p in patches],
        total=len(patches),
    )


@router.get(
    "/sessions/{session_id}/probe-results",
    response_model=ProbeResultsResponse,
    summary="Get Literature Probe Results",
    description="Get literature probe results from BFTS literature probes (Step 5)."
)
async def get_probe_results(session_id: str) -> ProbeResultsResponse:
    """Get literature probe results for a session."""
    service = get_idea_service()
    session = service.get_session(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found"
        )

    probe_storage = get_probe_literature_storage()
    results = probe_storage.list_by_session(session_id)

    return ProbeResultsResponse(
        results=[r.model_dump() for r in results],
        total=len(results),
    )


# =============================================================================
# Dual-Graph Endpoints (Phase 1)
# =============================================================================


@router.get(
    "/sessions/{session_id}/query-plan",
    response_model=QueryPlanResponse,
    summary="Get Query Plan",
    description="Get the structured query plan produced in Step 1 (expandQuery)."
)
async def get_query_plan(session_id: str) -> QueryPlanResponse:
    """Get query plan for a session."""
    service = get_idea_service()
    session = service.get_session(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found"
        )

    query_plan_dict = service._get_step_output(session, "expandQuery", "queryPlan")
    if not query_plan_dict:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Query plan not yet generated. Start the session pipeline first."
        )

    return QueryPlanResponse(**query_plan_dict)


@router.get(
    "/sessions/{session_id}/literature/raw",
    response_model=RawPapersResponse,
    summary="Get Raw Papers",
    description="Get raw papers from literature search with full metadata and dedup keys."
)
async def get_raw_papers(session_id: str) -> RawPapersResponse:
    """Get raw papers for a session."""
    service = get_idea_service()
    session = service.get_session(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found"
        )

    raw_storage = get_raw_paper_storage()
    papers = raw_storage.list_by_session(session_id)
    if not papers:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Raw papers not yet generated. Run the pipeline first."
        )

    return RawPapersResponse(
        papers=[p.model_dump() for p in papers],
        total=len(papers),
    )


@router.get(
    "/sessions/{session_id}/graph/literature",
    response_model=LiteratureGraphResponse,
    summary="Get Literature Graph",
    description="Get the paper-level literature graph (Graph 1)."
)
async def get_literature_graph(session_id: str) -> LiteratureGraphResponse:
    """Get literature graph for a session."""
    service = get_idea_service()
    session = service.get_session(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found"
        )

    graph_storage = get_literature_graph_storage()
    graph = graph_storage.get_by_session(session_id)
    if not graph:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Literature graph not yet generated. Run the pipeline first."
        )

    return LiteratureGraphResponse(
        id=graph.id,
        sessionId=graph.sessionId,
        version=graph.version,
        nodes=[n.model_dump() for n in graph.nodes],
        edges=[e.model_dump() for e in graph.edges],
        clusters=[c.model_dump() for c in graph.clusters],
        createdAt=graph.createdAt.isoformat() if graph.createdAt else "",
    )


@router.get(
    "/sessions/{session_id}/literature-map",
    response_model=LiteratureMapResponse,
    summary="Get Literature Map",
    description="Get the structured literature map with clusters, frontiers, and gaps."
)
async def get_literature_map(session_id: str) -> LiteratureMapResponse:
    """Get literature map for a session."""
    service = get_idea_service()
    session = service.get_session(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found"
        )

    map_storage = get_literature_map_storage()
    lit_map = map_storage.get_by_session(session_id)
    if not lit_map:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Literature map not yet generated. Run the pipeline first."
        )

    return LiteratureMapResponse(
        id=lit_map.id,
        sessionId=lit_map.sessionId,
        paperCount=lit_map.paperCount,
        clusters=[c.model_dump() for c in lit_map.clusters],
        frontiers=[f.model_dump() for f in lit_map.frontiers],
        gaps=[g.model_dump() for g in lit_map.gaps],
        noveltyEvidence=[n.model_dump() for n in lit_map.noveltyEvidence],
        selectedPaperIds=lit_map.selectedPaperIds,
        selectionReport=lit_map.selectionReport,
        createdAt=lit_map.createdAt.isoformat() if lit_map.createdAt else "",
    )


@router.get(
    "/sessions/{session_id}/literature/structured",
    response_model=StructuredPapersResponse,
    summary="Get Structured Papers",
    description="Get deep-read structured papers with extracted claims, findings, and methods."
)
async def get_structured_papers(session_id: str) -> StructuredPapersResponse:
    """Get structured papers for a session."""
    service = get_idea_service()
    session = service.get_session(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found"
        )

    structured_storage = get_structured_paper_storage()
    papers = structured_storage.list_by_session(session_id)
    if not papers:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Structured papers not yet generated. Run the pipeline first."
        )

    return StructuredPapersResponse(
        papers=[p.model_dump() for p in papers],
        total=len(papers),
    )


@router.get(
    "/sessions/{session_id}/bfts-handoff",
    response_model=BFTSHandoffResponse,
    summary="Get BFTS Handoff",
    description="Get the BFTS handoff artifact for Step 5 consumption."
)
async def get_bfts_handoff(session_id: str) -> BFTSHandoffResponse:
    """Get BFTS handoff for a session."""
    service = get_idea_service()
    session = service.get_session(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found"
        )

    handoff_storage = get_handoff_storage()
    handoff = handoff_storage.get_by_session(session_id)
    if not handoff:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="BFTS handoff not yet generated. Run the pipeline first."
        )

    return BFTSHandoffResponse(
        id=handoff.id,
        sessionId=handoff.sessionId,
        reasoningKgId=handoff.reasoningKgId,
        literatureMapId=handoff.literatureMapId,
        pathSeedIds=handoff.pathSeedIds,
        selectedPaperIds=handoff.selectedPaperIds,
        bftsConfig=handoff.bftsConfig.model_dump(),
        createdAt=handoff.createdAt.isoformat() if handoff.createdAt else "",
    )


@router.post(
    "/sessions/{session_id}/select",
    response_model=SelectCandidateResponse,
    summary="Select Candidate",
    description="Select the final idea candidate used to create a PlanPackage."
)
async def select_candidate(
    session_id: str,
    request: SelectCandidateRequest
) -> SelectCandidateResponse:
    """Select a candidate without creating a legacy plan object."""
    service = get_idea_service()
    session = service.get_session(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found"
        )

    if session.status != IdeaSessionStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session must be completed before selecting. Current status: {session.status.value}"
        )

    if session.selectedCandidateId == request.candidateId:
        return SelectCandidateResponse(
            ok=True,
            candidateId=request.candidateId,
            selectedCandidateId=request.candidateId,
        )

    try:
        service.select_candidate(session_id, request.candidateId)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return SelectCandidateResponse(
        ok=True,
        candidateId=request.candidateId,
        selectedCandidateId=request.candidateId,
    )


# =============================================================================
# Seed Query Pre-Check Endpoint
# =============================================================================


class SeedCheckRequest(BaseModel):
    """Request to pre-check a seed query before running the full pipeline."""
    seedQuery: str = Field(..., min_length=3)


class SeedCheckResponse(BaseModel):
    """Response for seed query pre-check."""
    paperCount: int
    isSufficient: bool
    threshold: int
    topicTerms: List[str] = []
    generalizedQuery: Optional[str] = None
    suggestion: Optional[str] = None
    topPaperTitles: List[str] = []


@router.post(
    "/seed-check",
    response_model=SeedCheckResponse,
    summary="Pre-check Seed Query",
    description=(
        "Quickly search for papers matching the seed query and assess whether "
        "enough literature exists. If not, generate a generalized query suggestion."
    ),
)
async def seed_check(request: SeedCheckRequest) -> SeedCheckResponse:
    """Pre-check a seed query before committing to a full pipeline run."""
    from app.services.search_service import get_search_service
    from app.llm.provider_client import get_provider_client, ChatMessage
    from app.core.settings import get_settings
    from app.modules.idea.service import _topic_terms_from_seed

    threshold = int(__import__("os").getenv("FAROS_PAPER_GATE_MIN_PAPERS", "4"))
    seed = request.seedQuery.strip()
    if len(seed) < 3:
        return SeedCheckResponse(
            paperCount=0,
            isSufficient=False,
            threshold=threshold,
        )

    # 1. Quick search across all sources
    search_service = get_search_service()
    try:
        results = search_service.search(seed, limit=10)
    except Exception as exc:
        logger.warning("seed-check search failed: %s", exc)
        results = []

    paper_count = len(results)
    topic_terms = _topic_terms_from_seed(seed)
    top_titles = [getattr(r, "title", "") for r in results[:5]]

    if paper_count >= threshold:
        return SeedCheckResponse(
            paperCount=paper_count,
            isSufficient=True,
            threshold=threshold,
            topicTerms=topic_terms[:12],
            topPaperTitles=top_titles,
        )

    # 2. Not enough papers — ask LLM to generalize the seed
    settings = get_settings()
    provider_name = settings.get_active_provider()
    model_name = settings.get_active_model(provider_name)

    generalize_prompt = (
        "You are a research advisor helping a user refine their search query.\n"
        "The user's query returned too few academic papers. "
        "Generalize it into a broader but still relevant research topic.\n\n"
        "Rules:\n"
        "1. Keep the core research intent.\n"
        "2. Replace niche tool names with their broader research area.\n"
        "3. Replace specific numeric parameters with the general research question.\n"
        "4. Output ONLY a single-line generalized query, nothing else.\n\n"
        f"Original query: {seed}\n"
        f"Papers found: {paper_count}\n"
        f"Topic terms extracted: {topic_terms[:8]}\n\n"
        "Generalized query:"
    )

    generalized_query = None
    suggestion = None
    try:
        client = get_provider_client(provider_name)
        response = client.chat(
            [ChatMessage(role="user", content=generalize_prompt)],
            model=model_name,
            temperature=0.3,
            max_tokens=120,
        )
        generalized_query = (response.text or "").strip().split("\n")[0].strip()
        if generalized_query.startswith('"') and generalized_query.endswith('"'):
            generalized_query = generalized_query[1:-1]
        # Validate: don't return if it's too similar or empty
        if not generalized_query or generalized_query.lower() == seed.lower():
            generalized_query = None
    except Exception as exc:
        logger.warning("seed-check LLM generalization failed: %s", exc)

    if generalized_query:
        suggestion = (
            f"Only {paper_count} papers found for this topic. "
            f"Consider using the generalized query: \"{generalized_query}\""
        )
    else:
        suggestion = (
            f"Only {paper_count} papers found. "
            "Try broadening your topic or using more general research terms."
        )

    return SeedCheckResponse(
        paperCount=paper_count,
        isSufficient=False,
        threshold=threshold,
        topicTerms=topic_terms[:12],
        generalizedQuery=generalized_query,
        suggestion=suggestion,
        topPaperTitles=top_titles,
    )


# =============================================================================
# Research Dossier Endpoints (Public Contract)
# =============================================================================

class BuildDossierRequest(BaseModel):
    """Request to build a ResearchDossier from an existing session."""
    sessionId: str = Field(..., description="Idea session ID")
    runId: Optional[str] = Field(default=None, description="Override run ID")
    mode: str = Field(default="deep", description="coverage or deep")
    questionId: Optional[str] = Field(default=None)
    questionText: Optional[str] = Field(default=None)
    domainHint: Optional[str] = None


class BuildDossierResponse(BaseModel):
    """Response containing the built ResearchDossier."""
    dossier: Dict[str, Any]
    degradationState: Optional[Dict[str, Any]] = None


@router.post(
    "/dossier",
    response_model=BuildDossierResponse,
    summary="Build a ResearchDossier from a completed session",
)
def build_dossier(request: BuildDossierRequest):
    """Build a contract-compliant ResearchDossier from an idea session."""
    from app.contracts import RunMode, ScientificQuestion
    from app.modules.idea.research_dossier import build_research_dossier
    from app.modules.idea.budget_modes import BudgetConfig, detect_degradation

    service = get_idea_service()
    session = service.session_storage.get(request.sessionId)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {request.sessionId} not found")

    candidates = service.get_candidates(request.sessionId)
    literature = service.get_literature(request.sessionId)

    if not candidates:
        raise HTTPException(status_code=400, detail="Session has no candidates")

    mode = RunMode.DEEP if request.mode == "deep" else RunMode.COVERAGE
    budget = BudgetConfig.from_mode(mode)

    # Build question
    question = None
    if request.questionText:
        question = ScientificQuestion(
            id=request.questionId or f"question_{request.sessionId}",
            text=request.questionText,
            domainHint=request.domainHint,
        )

    # Detect degradation
    degradation = detect_degradation(
        api_available=True,
        search_result_count=len(literature),
        min_evidence_threshold=3,
    )

    dossier = build_research_dossier(
        session=session,
        candidates=candidates,
        literature=literature,
        question=question,
        run_id=request.runId,
        mode=mode,
    )

    return BuildDossierResponse(
        dossier=dossier.model_dump(mode="json"),
        degradationState=degradation.to_dict() if degradation.is_degraded else None,
    )


class DiffDossiersRequest(BaseModel):
    """Request to diff two dossier versions."""
    v1: Dict[str, Any] = Field(..., description="First dossier version (JSON)")
    v2: Dict[str, Any] = Field(..., description="Second dossier version (JSON)")


@router.post(
    "/dossier/diff",
    summary="Compute v1/v2 diff between two dossier versions",
)
def diff_dossiers(request: DiffDossiersRequest):
    """Compute a structured diff between two ResearchDossier versions."""
    from app.contracts import ResearchDossier
    from app.modules.idea.research_dossier import diff_dossiers as _diff

    try:
        v1 = ResearchDossier.model_validate(request.v1)
        v2 = ResearchDossier.model_validate(request.v2)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid dossier: {e}")

    return _diff(v1, v2)
