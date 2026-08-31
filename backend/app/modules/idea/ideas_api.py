"""
Idea Generation API Endpoints

Provides endpoints for managing idea generation sessions.
"""

import json
import logging
import threading
import uuid
from typing import Any, Dict, Optional, List
from datetime import datetime
from fastapi import APIRouter, HTTPException, status, BackgroundTasks, Query
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.core.user_context import call_with_current_context, get_current_user_id
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
from app.modules.idea.seed_coach import build_seed_coach_prompt, parse_seed_suggestions

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
        msg = str(e)
        if "not found" in msg:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=msg
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=msg
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
    paperType: str = Field(default="algorithm")


class SeedCheckResponse(BaseModel):
    """Response for seed query pre-check."""
    paperCount: int
    isSufficient: bool
    threshold: int
    rawPaperCount: int = 0
    alignedPaperCount: int = 0
    topicTerms: List[str] = []
    generalizedQuery: Optional[str] = None
    suggestedQuery: Optional[str] = None
    suggestedQueries: List[dict] = []
    suggestionProvider: Optional[str] = None
    suggestionModel: Optional[str] = None
    diagnosisCode: Optional[str] = None
    suggestion: Optional[str] = None
    topPaperTitles: List[str] = []


class SeedSuggestionRequest(BaseModel):
    """Ask Qwen to turn a rough interest into search-ready research seeds."""

    userIdea: str = Field(default="", max_length=1000)
    paperType: str = Field(default="algorithm")
    count: int = Field(default=3, ge=2, le=4)
    diagnosisCode: Optional[str] = None


class SeedSuggestionItem(BaseModel):
    titleZh: str
    titleEn: str
    query: str
    rationaleZh: str = ""
    rationaleEn: str = ""


class SeedSuggestionResponse(BaseModel):
    providerName: str
    model: str
    suggestions: List[SeedSuggestionItem]


class SeedSuggestionJobResponse(BaseModel):
    jobId: str
    status: str
    result: Optional[SeedSuggestionResponse] = None
    error: Optional[str] = None


_seed_suggestion_jobs: Dict[str, Dict[str, Any]] = {}
_seed_suggestion_jobs_lock = threading.Lock()


def _request_qwen_seed_suggestions(
    *,
    user_idea: str,
    paper_type: str,
    count: int = 3,
    diagnosis_code: Optional[str] = None,
) -> SeedSuggestionResponse:
    """Call the current user's Qwen account and validate its topic suggestions."""

    from app.llm.provider_client import ChatMessage, ProviderError, get_provider_client

    settings = get_settings()
    provider_name = "qwen"
    if not settings.get_api_key(provider_name):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Qwen API key is not configured for this account. Open Settings > LLM Providers and configure Qwen first.",
        )

    model_name = settings.get_active_model(provider_name)
    prompt = build_seed_coach_prompt(
        user_idea=user_idea,
        paper_type=paper_type,
        count=count,
        diagnosis=diagnosis_code,
    )
    client = get_provider_client(provider_name)
    response = None
    suggestions: List[dict] = []
    for attempt in range(2):
        attempt_prompt = prompt
        if attempt:
            attempt_prompt += (
                "\nYour previous response failed automatic validation because fewer than two queries "
                "had 10+ English words and explicit evaluation criteria. Rewrite all suggestions, "
                "follow the schema exactly, and include the literal phrase 'evaluated by' in every query.\n"
                f"Previous response:\n{response.text[:2500] if response else ''}"
            )
        try:
            response = client.chat(
                [ChatMessage(role="user", content=attempt_prompt)],
                model=model_name,
                temperature=0.55 if attempt else 0.65,
                max_tokens=1400,
                structured_output=True,
            )
        except ProviderError as exc:
            logger.warning("Qwen seed coach request failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Qwen could not generate topic suggestions. Check the Qwen key/model in Settings and try again.",
            ) from exc

        suggestions = parse_seed_suggestions(response.text, limit=count)
        if len(suggestions) >= 2:
            break
        logger.warning(
            "Qwen seed coach validation failed on attempt %s: %s",
            attempt + 1,
            response.text[:500],
        )

    if len(suggestions) < 2 or response is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Qwen returned an invalid topic format. Please try again.",
        )
    return SeedSuggestionResponse(
        providerName=provider_name,
        model=response.model or model_name,
        suggestions=[SeedSuggestionItem(**item) for item in suggestions],
    )


@router.post(
    "/seed-suggestions",
    response_model=SeedSuggestionResponse,
    summary="Generate Research Topic Suggestions with Qwen",
)
async def seed_suggestions(request: SeedSuggestionRequest) -> SeedSuggestionResponse:
    """Return two to four novice-friendly, search-ready research topics."""

    return await run_in_threadpool(
        call_with_current_context(
            _request_qwen_seed_suggestions,
            user_idea=request.userIdea.strip(),
            paper_type=request.paperType,
            count=request.count,
            diagnosis_code=request.diagnosisCode,
        )
    )


def _run_seed_suggestion_job(job_id: str, request: SeedSuggestionRequest) -> None:
    with _seed_suggestion_jobs_lock:
        job = _seed_suggestion_jobs.get(job_id)
        if not job:
            return
        job["status"] = "running"

    try:
        result = _request_qwen_seed_suggestions(
            user_idea=request.userIdea.strip(),
            paper_type=request.paperType,
            count=request.count,
            diagnosis_code=request.diagnosisCode,
        )
    except HTTPException as exc:
        error = str(exc.detail)
        with _seed_suggestion_jobs_lock:
            job = _seed_suggestion_jobs.get(job_id)
            if job:
                job.update(status="failed", error=error)
        return
    except Exception as exc:
        logger.warning("Qwen seed suggestion job failed: %s", exc, exc_info=True)
        with _seed_suggestion_jobs_lock:
            job = _seed_suggestion_jobs.get(job_id)
            if job:
                job.update(status="failed", error="Qwen topic recommendation failed. Check the model settings and retry.")
        return

    with _seed_suggestion_jobs_lock:
        job = _seed_suggestion_jobs.get(job_id)
        if job:
            job.update(status="completed", result=result, error=None)


@router.post(
    "/seed-suggestion-jobs",
    response_model=SeedSuggestionJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a Qwen Research Topic Suggestion Job",
)
async def create_seed_suggestion_job(request: SeedSuggestionRequest) -> SeedSuggestionJobResponse:
    """Start topic coaching without holding a fragile public HTTP connection open."""

    job_id = f"seedjob_{uuid.uuid4().hex[:12]}"
    with _seed_suggestion_jobs_lock:
        if len(_seed_suggestion_jobs) >= 100:
            completed_ids = [
                existing_id
                for existing_id, job in _seed_suggestion_jobs.items()
                if job.get("status") in {"completed", "failed"}
            ]
            for existing_id in completed_ids[:50]:
                _seed_suggestion_jobs.pop(existing_id, None)
        _seed_suggestion_jobs[job_id] = {
            "ownerId": get_current_user_id(),
            "status": "pending",
            "result": None,
            "error": None,
        }

    worker = threading.Thread(
        target=call_with_current_context(_run_seed_suggestion_job, job_id, request),
        daemon=True,
        name=f"seed-coach-{job_id[-6:]}",
    )
    worker.start()
    return SeedSuggestionJobResponse(jobId=job_id, status="pending")


@router.get(
    "/seed-suggestion-jobs/{job_id}",
    response_model=SeedSuggestionJobResponse,
    summary="Get a Qwen Research Topic Suggestion Job",
)
async def get_seed_suggestion_job(job_id: str) -> SeedSuggestionJobResponse:
    with _seed_suggestion_jobs_lock:
        job = _seed_suggestion_jobs.get(job_id)
        if not job or job.get("ownerId") != get_current_user_id():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Topic suggestion job not found")
        return SeedSuggestionJobResponse(
            jobId=job_id,
            status=str(job.get("status") or "pending"),
            result=job.get("result"),
            error=job.get("error"),
        )


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
    from collections import Counter

    from app.services.search_service import get_search_service
    from app.modules.idea.evidence_relevance import (
        EvidenceTier,
        assess_search_result,
        build_topic_intent_profile,
        diagnose_literature_failure,
    )
    from app.modules.idea.service import _evaluate_paper_quality_gate, _topic_terms_from_seed

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
        results = await run_in_threadpool(
            call_with_current_context(search_service.search, seed, limit=10)
        )
    except Exception as exc:
        logger.warning("seed-check search failed: %s", exc)
        results = []

    raw_paper_count = len(results)
    topic_terms = _topic_terms_from_seed(seed)
    role_queries = {
        "domain": [seed],
        "task": [seed],
        "method": [f"{seed} methods techniques algorithms"],
        "evaluation": [f"{seed} evaluation benchmark metrics limitations"],
    }
    profile = build_topic_intent_profile(
        seed=seed,
        domain="",
        role_queries=role_queries,
    )
    eligible_results = []
    rejected_results = []
    for result in results:
        assessment = assess_search_result(result, profile)
        result.evidence_tier = assessment.tier.value
        result.decisive_anchors = list(assessment.decisive_anchors)
        result.relevance_components = dict(assessment.score_components)
        result.rejection_reason = assessment.rejection_reason
        result.relevance_score = assessment.score
        if assessment.tier is EvidenceTier.REJECTED:
            rejected_results.append(result)
        else:
            eligible_results.append(result)

    quality_gate = _evaluate_paper_quality_gate(
        seed=seed,
        domain="",
        papers=eligible_results,
        stage="seedCheck",
        extra_terms=[seed],
        paper_type=request.paperType,
    )
    paper_count = int(quality_gate.get("paperCount", len(eligible_results)) or 0)
    aligned_paper_count = int(quality_gate.get("alignedPaperCount", 0) or 0)
    top_titles = [getattr(result, "title", "") for result in eligible_results[:5]]

    if quality_gate.get("passed", False):
        return SeedCheckResponse(
            paperCount=paper_count,
            isSufficient=True,
            threshold=threshold,
            rawPaperCount=raw_paper_count,
            alignedPaperCount=aligned_paper_count,
            topicTerms=topic_terms[:12],
            topPaperTitles=top_titles,
        )

    rejection_reason_counts = Counter(
        result.rejection_reason for result in rejected_results
    )
    diagnosis = diagnose_literature_failure(
        seed=seed,
        raw_result_count=raw_paper_count,
        unique_result_count=raw_paper_count,
        gate=quality_gate,
        rejection_reason_counts=rejection_reason_counts,
        seed_anchors=profile.seed_anchors,
    )
    diagnosis_code = str(diagnosis.get("code", "evidence_quality_failed"))

    # Qwen coaching runs through the short-polling job endpoint so a slow model
    # response cannot hold this pre-check request open.
    suggested_queries: List[dict] = []
    suggestion_provider = None
    suggestion_model = None
    generalized_query = None
    suggestion = (
        f"The search returned {raw_paper_count} raw papers, but only {paper_count} passed relevance filtering. "
        "Qwen topic coaching has been started separately; choose one of its search-ready alternatives."
    )

    return SeedCheckResponse(
        paperCount=paper_count,
        isSufficient=False,
        threshold=threshold,
        rawPaperCount=raw_paper_count,
        alignedPaperCount=aligned_paper_count,
        topicTerms=topic_terms[:12],
        generalizedQuery=generalized_query,
        suggestedQuery=generalized_query,
        suggestedQueries=suggested_queries,
        suggestionProvider=suggestion_provider,
        suggestionModel=suggestion_model,
        diagnosisCode=diagnosis_code,
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


# ---------------------------------------------------------------------------
# Child Run: accept Review finding, create v2 session from parent
# ---------------------------------------------------------------------------

class CreateChildRunRequest(BaseModel):
    """Request to create a child run from a parent session and review findings."""
    parentSessionId: str = Field(..., description="Parent idea session ID")
    findings: List[Dict[str, Any]] = Field(..., description="Review findings (list of finding dicts with at least 'description' field)")
    mode: Optional[str] = Field(default=None, description="Override mode: 'deep' or 'coverage'. Defaults to parent's mode.")


class CreateChildRunResponse(BaseModel):
    """Response containing the child run and new session info."""
    childRunId: str
    parentRunId: str
    newSessionId: str
    findingsCount: int
    status: str


@router.post(
    "/dossier/child-run",
    summary="Create a child run from a parent session and review findings",
)
def create_child_run(request: CreateChildRunRequest):
    """
    Accept Review findings and create a child ScientificQuestionRun.

    This implements P0 task #8: "Accept Review finding, create child run."
    The child run inherits the parent's question and mode, with parentRunId
    set to the parent's run ID. A new idea session is created so the pipeline
    can be re-run with the review feedback incorporated.
    """
    from app.contracts import RunMode, RunStatus, ScientificQuestion, ScientificQuestionRun
    from app.modules.idea.research_dossier import create_child_run as _create_child_run

    service = get_idea_service()

    # 1. Get parent session
    parent_session = service.session_storage.get(request.parentSessionId)
    if not parent_session:
        raise HTTPException(status_code=404, detail=f"Parent session {request.parentSessionId} not found")

    # 2. Build parent ScientificQuestionRun
    parent_mode = RunMode.DEEP
    if request.mode:
        parent_mode = RunMode.DEEP if request.mode == "deep" else RunMode.COVERAGE
    elif hasattr(parent_session.config, 'mode') and parent_session.config.mode:
        parent_mode = RunMode.DEEP if parent_session.config.mode == "deep" else RunMode.COVERAGE

    seed_query = (parent_session.config.seedQuery if hasattr(parent_session, 'config') and hasattr(parent_session.config, 'seedQuery')
                  else getattr(parent_session, 'seedQuery', getattr(parent_session, 'seed', '')))

    parent_question = ScientificQuestion(
        id=f"q_{request.parentSessionId}",
        text=seed_query,
        domainHint=getattr(parent_session.config, 'domain', None),
    )

    parent_run = ScientificQuestionRun(
        runId=f"run_{request.parentSessionId}",
        question=parent_question,
        mode=parent_mode,
        status=RunStatus.COMPLETED,
        providerName=getattr(parent_session.config, 'providerName', None),
        model=getattr(parent_session.config, 'model', None),
    )

    # 3. Create child run
    child_run = _create_child_run(parent_run=parent_run, findings=request.findings)

    # 4. Create new idea session from parent config
    new_config = IdeaSessionConfig(
        providerName=parent_session.config.providerName,
        model=parent_session.config.model,
        seedQuery=seed_query,
        paperType=getattr(parent_session.config, 'paperType', 'full'),
        maxCandidates=getattr(parent_session.config, 'maxCandidates', 10),
        maxPapers=getattr(parent_session.config, 'maxPapers', 50),
        domain=getattr(parent_session.config, 'domain', None),
        constraints=getattr(parent_session.config, 'constraints', None),
        mustCiteList=getattr(parent_session.config, 'mustCiteList', []),
        searchBudget=getattr(parent_session.config, 'searchBudget', 30),
        maxReviewIterations=getattr(parent_session.config, 'maxReviewIterations', 2),
    )

    new_session = service.create_session(new_config)

    # 5. Store review findings in session qualityLoopSummary for the pipeline to use
    new_session.qualityLoopSummary['parentSessionId'] = request.parentSessionId
    new_session.qualityLoopSummary['parentRunId'] = parent_run.runId
    new_session.qualityLoopSummary['childRunId'] = child_run.runId
    new_session.qualityLoopSummary['reviewFindings'] = request.findings
    service.session_storage.update(new_session)

    return CreateChildRunResponse(
        childRunId=child_run.runId,
        parentRunId=parent_run.runId,
        newSessionId=new_session.id,
        findingsCount=len(request.findings),
        status="created",
    )
