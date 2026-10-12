"""
Code Sessions API Endpoints

Provides endpoints for:
- Creating and managing code generation sessions
- Running the pipeline
- Listing candidates
- Selecting and applying candidates
"""

import uuid
import logging
from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, HTTPException, status, BackgroundTasks
from pydantic import BaseModel, Field

from app.code.pipeline.code_session_model import (
    CodeSession, CodeSessionConfig, CodeSessionStatus,
    CodeCandidate, CandidateScores, TraceStep
)
from app.code.pipeline.pipeline_runner import PipelineRunner
from app.storage.code_session_storage import get_storage
from app.core.settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/code/sessions", tags=["code_sessions"])


# Request/Response Models

class CreateSessionRequest(BaseModel):
    """Request to create a code session."""
    repoPath: str = Field(..., max_length=1024, description="Path to repository")
    goal: str = Field(..., description="What code to generate/modify")
    providerName: Optional[str] = Field(default=None, description="LLM provider; omit for Settings default")
    model: Optional[str] = Field(default=None, description="Model; omit for Settings default")
    maxCandidates: int = Field(default=3, ge=1, le=10, description="Max candidates to generate")
    maxIterations: int = Field(default=3, ge=1, le=10, description="Max refinement iterations")
    constraints: Optional[str] = Field(default=None, description="Additional constraints")
    targetFiles: Optional[List[str]] = Field(default=None, description="Specific files to modify")


class SessionResponse(BaseModel):
    """Session response."""
    id: str
    status: str
    config: dict
    createdAt: str
    startedAt: Optional[str]
    endedAt: Optional[str]
    duration: Optional[int]
    currentStep: Optional[str]
    iterationCount: int
    candidateIds: List[str]
    selectedCandidateId: Optional[str]
    summary: Optional[str]
    errorMessage: Optional[str]


class SessionListItem(BaseModel):
    """Session list item."""
    id: str
    status: str
    goal: str
    repoPath: str
    createdAt: str
    candidateCount: int
    selectedCandidateId: Optional[str]
    summary: Optional[str]


class SessionListResponse(BaseModel):
    """Session list response."""
    sessions: List[SessionListItem]
    total: int


class CandidateResponse(BaseModel):
    """Candidate response."""
    id: str
    sessionId: str
    title: str
    approach: str
    patch: str
    rationale: str
    scores: dict
    overallScore: float
    rank: int
    createdAt: str


class CandidateListResponse(BaseModel):
    """Candidate list response."""
    candidates: List[CandidateResponse]
    total: int


class TraceStepResponse(BaseModel):
    """Trace step response."""
    step: str
    status: str
    timestamp: str
    durationMs: Optional[int]
    inputs: Optional[dict]
    outputs: Optional[dict]
    error: Optional[str]


class TraceResponse(BaseModel):
    """Session trace response."""
    sessionId: str
    steps: List[TraceStepResponse]


class SelectCandidateRequest(BaseModel):
    """Request to select a candidate."""
    candidateId: str = Field(..., description="ID of candidate to select")


class SelectCandidateResponse(BaseModel):
    """Response from selecting a candidate."""
    ok: bool
    candidateId: str
    sessionId: str


# Helper functions

def _session_to_response(session: CodeSession) -> SessionResponse:
    return SessionResponse(
        id=session.id,
        status=session.status.value,
        config=session.config.to_dict(),
        createdAt=session.createdAt,
        startedAt=session.startedAt,
        endedAt=session.endedAt,
        duration=session.duration,
        currentStep=session.currentStep,
        iterationCount=session.iterationCount,
        candidateIds=session.candidateIds,
        selectedCandidateId=session.selectedCandidateId,
        summary=session.summary,
        errorMessage=session.errorMessage,
    )


def _run_pipeline_background(session_id: str):
    """Run pipeline in background."""
    storage = get_storage()
    session_data = storage.get(session_id)
    
    if not session_data:
        logger.error(f"Session not found for background run: {session_id}")
        return
    
    session = CodeSession.from_dict(session_data)
    runner = PipelineRunner(session, storage)
    
    try:
        runner.run()
        logger.info(f"Pipeline completed for session {session_id}")
    except Exception as e:
        logger.error(f"Pipeline failed for session {session_id}: {e}")


# Endpoints

@router.post(
    "",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Code Session",
    description="Create a new code generation session."
)
async def create_session(request: CreateSessionRequest) -> SessionResponse:
    """Create a new code session."""
    import os
    
    # Validate repo path
    if not os.path.isdir(request.repoPath):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Repository path does not exist: {request.repoPath}"
        )
    
    storage = get_storage()
    session_id = f"code_{uuid.uuid4().hex[:12]}"
    created_at = datetime.utcnow().isoformat()

    settings = get_settings()
    provider_name = request.providerName if request.providerName is not None else settings.get_active_provider()
    model_name = request.model if request.model is not None else settings.get_active_model(provider_name)
    
    config = CodeSessionConfig(
        repoPath=request.repoPath,
        goal=request.goal,
        providerName=provider_name,
        model=model_name,
        maxCandidates=request.maxCandidates,
        maxIterations=request.maxIterations,
        constraints=request.constraints,
        targetFiles=request.targetFiles,
    )
    
    session = CodeSession(
        id=session_id,
        status=CodeSessionStatus.PENDING,
        config=config,
        createdAt=created_at,
    )
    
    storage.save(session_id, session.to_dict())
    
    return _session_to_response(session)


@router.post(
    "/{session_id}/start",
    response_model=SessionResponse,
    summary="Start Session",
    description="Start the code generation pipeline."
)
async def start_session(
    session_id: str,
    background_tasks: BackgroundTasks
) -> SessionResponse:
    """Start the pipeline for a session."""
    storage = get_storage()
    session_data = storage.get(session_id)
    
    if not session_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}"
        )
    
    session = CodeSession.from_dict(session_data)
    
    if session.status != CodeSessionStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session cannot be started. Current status: {session.status.value}"
        )
    
    # Start pipeline in background
    background_tasks.add_task(_run_pipeline_background, session_id)
    
    # Update status to running
    session.status = CodeSessionStatus.RUNNING
    session.startedAt = datetime.utcnow().isoformat()
    storage.save(session_id, session.to_dict())
    
    return _session_to_response(session)


@router.post(
    "/{session_id}/cancel",
    response_model=SessionResponse,
    summary="Cancel Session",
    description="Cancel a running session."
)
async def cancel_session(session_id: str) -> SessionResponse:
    """Cancel a session."""
    storage = get_storage()
    session_data = storage.get(session_id)
    
    if not session_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}"
        )
    
    session = CodeSession.from_dict(session_data)
    
    if session.status not in [CodeSessionStatus.PENDING, CodeSessionStatus.RUNNING]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session cannot be cancelled. Current status: {session.status.value}"
        )
    
    session.status = CodeSessionStatus.CANCELLED
    session.endedAt = datetime.utcnow().isoformat()
    storage.save(session_id, session.to_dict())
    
    return _session_to_response(session)


@router.get(
    "",
    response_model=SessionListResponse,
    summary="List Sessions",
    description="List all code sessions."
)
async def list_sessions() -> SessionListResponse:
    """List all sessions."""
    storage = get_storage()
    sessions = storage.list_all()
    
    items = [
        SessionListItem(
            id=s["id"],
            status=s.get("status", "unknown"),
            goal=s.get("config", {}).get("goal", ""),
            repoPath=s.get("config", {}).get("repoPath", ""),
            createdAt=s.get("createdAt", ""),
            candidateCount=s.get("candidateCount", 0),
            selectedCandidateId=s.get("selectedCandidateId"),
            summary=s.get("summary"),
        )
        for s in sessions
    ]
    
    return SessionListResponse(sessions=items, total=len(items))


@router.get(
    "/{session_id}",
    response_model=SessionResponse,
    summary="Get Session",
    description="Get session details."
)
async def get_session(session_id: str) -> SessionResponse:
    """Get session by ID."""
    storage = get_storage()
    session_data = storage.get(session_id)
    
    if not session_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}"
        )
    
    session = CodeSession.from_dict(session_data)
    return _session_to_response(session)


@router.get(
    "/{session_id}/candidates",
    response_model=CandidateListResponse,
    summary="Get Candidates",
    description="Get candidates for a session."
)
async def get_candidates(session_id: str) -> CandidateListResponse:
    """Get candidates for a session."""
    storage = get_storage()
    
    if not storage.get(session_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}"
        )
    
    candidates = storage.get_candidates_for_session(session_id)
    
    items = [
        CandidateResponse(
            id=c["id"],
            sessionId=c["sessionId"],
            title=c["title"],
            approach=c["approach"],
            patch=c["patch"],
            rationale=c["rationale"],
            scores=c.get("scores", {}),
            overallScore=c.get("overallScore", 0.0),
            rank=c.get("rank", 0),
            createdAt=c.get("createdAt", ""),
        )
        for c in candidates
    ]
    
    return CandidateListResponse(candidates=items, total=len(items))


@router.get(
    "/{session_id}/trace",
    response_model=TraceResponse,
    summary="Get Trace",
    description="Get execution trace for a session."
)
async def get_trace(session_id: str) -> TraceResponse:
    """Get trace for a session."""
    storage = get_storage()
    session_data = storage.get(session_id)
    
    if not session_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}"
        )
    
    trace_data = session_data.get("trace", [])
    
    steps = [
        TraceStepResponse(
            step=t["step"],
            status=t["status"],
            timestamp=t["timestamp"],
            durationMs=t.get("durationMs"),
            inputs=t.get("inputs"),
            outputs=t.get("outputs"),
            error=t.get("error"),
        )
        for t in trace_data
    ]
    
    return TraceResponse(sessionId=session_id, steps=steps)


@router.post(
    "/{session_id}/select",
    response_model=SelectCandidateResponse,
    summary="Select Candidate",
    description="Select a candidate as the chosen solution."
)
async def select_candidate(
    session_id: str,
    request: SelectCandidateRequest
) -> SelectCandidateResponse:
    """Select a candidate."""
    storage = get_storage()
    session_data = storage.get(session_id)
    
    if not session_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}"
        )
    
    session = CodeSession.from_dict(session_data)
    
    if session.status != CodeSessionStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session must be completed before selecting. Status: {session.status.value}"
        )
    
    if request.candidateId not in session.candidateIds:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Candidate not found: {request.candidateId}"
        )
    
    session.selectedCandidateId = request.candidateId
    storage.save(session_id, session.to_dict())
    
    return SelectCandidateResponse(
        ok=True,
        candidateId=request.candidateId,
        sessionId=session_id,
    )


@router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete Session",
    description="Delete a code session."
)
async def delete_session(session_id: str):
    """Delete a session."""
    storage = get_storage()
    
    if not storage.get(session_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}"
        )
    
    storage.delete(session_id)
