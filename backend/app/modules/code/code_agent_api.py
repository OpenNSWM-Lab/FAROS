"""
Code Agent API — REST endpoints for the autonomous code-execution agent.

POST   /code/agent/run        — Start an autonomous agent run (async background)
GET    /code/agent/runs/{id}  — Get run status, trace, and events
GET    /code/agent/runs       — List agent runs
DELETE /code/agent/runs/{id}  — Delete a run record
GET    /code/agent/status     — Get agent/pool health status
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.code.sandbox import get_sandbox_pool
from app.code.sandbox.trace import ExecutionTrace
from app.db import crud
from app.db.models import AgentRunCreate, AgentRunDB, AgentRunStatus
from app.modules.code.storage import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/code/agent", tags=["code_agent"])


# ---- Request/Response models ----

class AgentRunRequest(BaseModel):
    """Request to start an autonomous agent run."""
    projectId: str = Field(..., description="Code project ID")
    goal: Optional[str] = Field(None, description="Natural language goal for the agent")
    language: str = Field("python", description="Programming language")
    command: Optional[str] = Field(None, description="Explicit command to execute (skips plan phase)")
    backend: Optional[str] = Field(None, description="Sandbox backend: 'docker' or 'subprocess'")
    maxIterations: int = Field(3, ge=1, le=10, description="Max repair iterations")
    executionTimeout: int = Field(300, ge=10, le=3600, description="Timeout per execution (seconds)")
    providerName: Optional[str] = Field(None, description="LLM provider for repair suggestions")
    model: Optional[str] = Field(None, description="LLM model override")


class AgentRunResponse(BaseModel):
    """Response after starting an agent run."""
    runId: str
    traceId: str
    status: str = "started"
    message: str = "Agent run started in background"


class AgentRunSummary(BaseModel):
    """Summary of a completed or in-progress agent run."""
    id: str
    projectId: str
    goal: Optional[str] = None
    language: str = "python"
    status: str
    iterations: int = 0
    repairsApplied: int = 0
    traceId: Optional[str] = None
    summary: Optional[str] = None
    error: Optional[str] = None
    createdAt: Optional[str] = None
    completedAt: Optional[str] = None


class AgentRunDetail(AgentRunSummary):
    """Full agent run detail including events."""
    events: list[dict] = Field(default_factory=list)


class AgentRunListResponse(BaseModel):
    """List of agent runs."""
    runs: list[AgentRunSummary]
    total: int


class AgentStatusResponse(BaseModel):
    """Health status of the agent system."""
    available: bool
    defaultBackend: str
    availableBackends: list[str]
    pool: dict


# ---- Endpoints ----

@router.post("/run", status_code=202, response_model=AgentRunResponse)
async def start_agent_run(
    request: AgentRunRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    """Start an autonomous agent run in the background.

    The agent will:
    1. Plan the execution command (or use the provided one)
    2. Execute in an isolated sandbox (Docker or subprocess)
    3. If it fails, auto-repair and retry (up to maxIterations)
    4. Record all events in an ExecutionTrace
    """
    # Validate project exists
    project = crud.get_project_v2(db, request.projectId)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {request.projectId}")

    # Resolve repo directory
    repo_dir = _resolve_repo_dir(project)
    if not repo_dir:
        raise HTTPException(
            status_code=400,
            detail=f"Project {request.projectId} has no repo directory on disk. "
                   "Generate the project first before running the agent.",
        )

    # Create DB record
    run_record = crud.create_agent_run(db, AgentRunCreate(
        project_id=request.projectId,
        goal=request.goal,
        language=request.language,
        command=request.command,
        backend=request.backend,
        max_iterations=request.maxIterations,
        execution_timeout=request.executionTimeout,
        provider_name=request.providerName,
        model=request.model,
    ))

    # Build trace ID — must match what GET endpoint and CodeAgentLoop expect
    # Format: agent_{12 hex chars}
    trace_id = f"agent_{run_record.id[-12:]}" if len(run_record.id) >= 12 else f"agent_{run_record.id}"

    # Launch background task
    background_tasks.add_task(
        _run_agent_background,
        run_id=run_record.id,
        trace_id=trace_id,
        project_id=request.projectId,
        repo_dir=repo_dir,
        goal=request.goal or "",
        language=request.language,
        command=request.command,
        backend=request.backend,
        max_iterations=request.maxIterations,
        execution_timeout=request.executionTimeout,
        provider_name=request.providerName,
        model=request.model,
    )

    logger.info(
        "Agent run started: %s (project=%s, backend=%s)",
        run_record.id, request.projectId, request.backend or "auto",
    )

    return AgentRunResponse(
        runId=run_record.id,
        traceId=trace_id,
        status="started",
    )


@router.post("/debug-run")
async def debug_agent_run(
    request: AgentRunRequest,
):
    """Synchronous agent run for debugging — returns full result immediately."""
    project_id = request.projectId
    repo_dir = _resolve_repo_dir_by_id(project_id)
    if not repo_dir:
        raise HTTPException(status_code=400, detail=f"No repo found for project {project_id}")

    from app.services.code_agent_loop import CodeAgentLoop
    trace_id = f"debug_{project_id[:12]}"

    loop = CodeAgentLoop(
        pool=await get_sandbox_pool(),
        max_iterations=request.maxIterations,
        execution_timeout=request.executionTimeout,
        backend=request.backend,
    )
    result = await loop.run(
        project_id=project_id,
        repo_dir=repo_dir,
        goal=request.goal or "",
        language=request.language,
        command=request.command,
        trace_id=trace_id,
    )

    return {
        "status": result.status,
        "iterations": result.iterations,
        "error": result.error,
        "events": [e.to_dict() for e in (result.trace.events if result.trace else [])],
        "stdout_tail": result.final_result.stdout[-500:] if result.final_result else "",
        "stderr_tail": result.final_result.stderr[-500:] if result.final_result else "",
    }


class ClaudeStreamRequest(BaseModel):
    """Request to start a streaming Claude Code agent session."""
    projectId: str = Field(..., description="Code project ID")
    goal: str = Field(..., description="Research goal / task description")
    systemPrompt: str = Field("", description="Custom system prompt (empty = use template)")
    template: str = Field("run_experiment", description="Preset template: run_experiment, fix_and_verify, analyze_and_plot, custom")
    model: str = Field("claude-sonnet-4-6", description="Claude model")
    maxBudget: float = Field(10.0, description="Max USD budget")
    timeout: int = Field(900, ge=60, le=3600, description="Max execution time (seconds)")
    sessionId: Optional[str] = Field(None, description="Resume a previous session")


@router.post("/claude-stream")
async def claude_stream(request: ClaudeStreamRequest):
    """Stream Claude Code execution in real-time via Server-Sent Events.

    Returns text/event-stream with structured JSON events:
    - event_type: "thinking" | "tool_use" | "tool_result" | "error" | "done"
    """
    repo_dir = _resolve_repo_dir_by_id(request.projectId)
    if not repo_dir:
        raise HTTPException(status_code=400, detail=f"No repo found for project {request.projectId}")

    from app.services.claude_agent import ClaudeCodeAgent, RESEARCH_TEMPLATES
    from fastapi.responses import StreamingResponse

    # Resolve system prompt
    system_prompt = request.systemPrompt
    if not system_prompt and request.template in RESEARCH_TEMPLATES:
        system_prompt = RESEARCH_TEMPLATES[request.template]

    agent = ClaudeCodeAgent(
        model=request.model,
        max_budget=request.maxBudget,
        timeout=request.timeout,
    )

    async def event_stream():
        """SSE event generator."""
        session_id = request.sessionId
        events: list[dict] = []

        async for event in agent.stream(
            workspace=repo_dir,
            goal=request.goal,
            system_prompt=system_prompt,
            session_id=session_id,
        ):
            events.append(event.to_dict())
            yield event.to_sse()

        # Save session after completion
        if events:
            import uuid
            sid = session_id or f"clsess_{uuid.uuid4().hex[:12]}"
            from app.services.claude_agent import save_session
            save_session(sid, {
                "project_id": request.projectId,
                "goal": request.goal,
                "model": request.model,
                "events": events,
            })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


class CartRunRequest(BaseModel):
    """Request to start a Cart pipeline run."""
    projectId: str = Field(..., description="Code project ID")
    packageId: str = Field("demo_ppkg_math", description="PlanPackage ID (or path to ppkg JSON)")
    timeout: int = Field(900, ge=60, le=3600)


@router.post("/cart/run")
async def cart_stream(request: CartRunRequest):
    """Run a full Cart pipeline on a PlanPackage, streaming progress via SSE.

    Loads the PlanPackage, topologically sorts the DAG, and executes
    each node via Claude Code agent. Returns SSE events for real-time
    frontend monitoring.
    """
    import os as _os
    from fastapi.responses import StreamingResponse

    repo_dir = _resolve_repo_dir_by_id(request.projectId)
    if not repo_dir:
        raise HTTPException(status_code=400, detail=f"No repo found for project {request.projectId}")

    # Load PlanPackage
    ppkg_path = request.packageId
    if not _os.path.isabs(ppkg_path):
        from app.db.engine import _DATA_DIR
        ppkg_dir = _os.path.join(_DATA_DIR, "plan_packages")
        candidates = [
            _os.path.join(ppkg_dir, f"{request.packageId}.json"),
            _os.path.join(ppkg_dir, "demo_ppkg_math.json"),
        ]
        ppkg_path = None
        for c in candidates:
            if _os.path.isfile(c):
                ppkg_path = c
                break
        if not ppkg_path:
            raise HTTPException(status_code=404, detail=f"PlanPackage not found: {request.packageId}")

    with open(ppkg_path, "r", encoding="utf-8") as f:
        ppkg = json.load(f)

    from app.services.cart_runner import CartRunner
    runner = CartRunner()

    async def event_stream():
        async for event in runner.run(ppkg):
            yield event.to_sse()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/runs/{run_id}", response_model=AgentRunDetail)
async def get_agent_run(
    run_id: str,
    db: Session = Depends(get_session),
):
    """Get agent run status, trace, and events."""
    run = crud.get_agent_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Agent run not found: {run_id}")

    # Load events from DB (primary) or trace file (fallback)
    events: list[dict] = []
    trace_id = f"agent_{run.id[-12:]}" if len(run.id) >= 12 else run.id
    if run.events_json:
        try:
            events = json.loads(run.events_json)
        except Exception:
            pass
    if not events:
        # Fallback: try loading from trace file
        trace = ExecutionTrace.load(trace_id)
        if trace:
            events = [e.to_dict() for e in trace.events]

    return AgentRunDetail(
        id=run.id,
        projectId=run.project_id,
        goal=run.goal,
        language=run.language,
        status=run.status,
        iterations=run.iterations,
        repairsApplied=run.repairs_applied,
        traceId=trace_id,
        summary=run.summary,
        error=run.error,
        createdAt=run.created_at.isoformat() if run.created_at else None,
        completedAt=run.completed_at.isoformat() if run.completed_at else None,
        events=events,
    )


@router.get("/runs", response_model=AgentRunListResponse)
async def list_agent_runs(
    projectId: Optional[str] = Query(None, description="Filter by project ID"),
    status: Optional[str] = Query(None, description="Filter by status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_session),
):
    """List agent runs, optionally filtered by project or status."""
    runs = crud.list_agent_runs(
        db, project_id=projectId, status=status, limit=limit, offset=offset
    )

    summaries = [
        AgentRunSummary(
            id=r.id,
            projectId=r.project_id,
            goal=r.goal,
            language=r.language,
            status=r.status,
            iterations=r.iterations,
            repairsApplied=r.repairs_applied,
            traceId=f"agent_{r.id[-12:]}" if len(r.id) >= 12 else r.id,
            summary=r.summary,
            error=r.error,
            createdAt=r.created_at.isoformat() if r.created_at else None,
            completedAt=r.completed_at.isoformat() if r.completed_at else None,
        )
        for r in runs
    ]

    return AgentRunListResponse(runs=summaries, total=len(summaries))


@router.delete("/runs/{run_id}")
async def delete_agent_run(
    run_id: str,
    db: Session = Depends(get_session),
):
    """Delete an agent run record."""
    deleted = crud.delete_agent_run(db, run_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Agent run not found: {run_id}")
    return {"deleted": True, "runId": run_id}


@router.get("/status", response_model=AgentStatusResponse)
async def get_agent_status():
    """Get agent system health status."""
    pool = await get_sandbox_pool()
    return AgentStatusResponse(
        available=True,
        defaultBackend=pool.default_backend,
        availableBackends=pool.available_backends,
        pool=pool.pool_info,
    )


# ---- Background task ----

async def _run_agent_background(
    run_id: str,
    trace_id: str,
    project_id: str,
    repo_dir: str,
    goal: str,
    language: str,
    command: Optional[str],
    backend: Optional[str],
    max_iterations: int,
    execution_timeout: int,
    provider_name: Optional[str],
    model: Optional[str],
) -> None:
    """Background coroutine that runs the agent loop and updates the DB."""
    from app.db.crud import update_agent_run
    from app.db.engine import get_session_context
    from app.services.code_agent_loop import CodeAgentLoop

    # Run the agent loop
    loop = CodeAgentLoop(
        pool=await get_sandbox_pool(),
        provider_name=provider_name or "qwen",
        model=model or "qwen-max",
        max_iterations=max_iterations,
        execution_timeout=execution_timeout,
        backend=backend,
    )

    try:
        result = await loop.run(
            project_id=project_id,
            repo_dir=repo_dir,
            goal=goal,
            language=language,
            command=command,
            trace_id=trace_id,
        )
    except Exception as exc:
        logger.exception("Agent background task error: %s", exc)
        result = None
        # Update DB with error
        try:
            with get_session_context() as db:
                update_agent_run(db, run_id, {
                    "status": "error",
                    "error": str(exc)[:500],
                    "iterations": 0,
                    "completed_at": _utcnow(),
                })
        except Exception:
            pass
        return

    if result is None:
        return

    # Compute summary
    summary_parts = []
    if result.success:
        summary_parts.append(f"Project ran successfully after {result.iterations} iteration(s)")
    elif result.status == "failed":
        summary_parts.append("Unable to repair the project automatically")
    elif result.status == "max_iterations":
        summary_parts.append(f"Failed after {result.iterations} iterations (max reached)")
    else:
        summary_parts.append(f"Agent run ended with status: {result.status}")

    if result.trace:
        repairs = result.trace.summary().get("repairs_applied", 0)
        if repairs:
            summary_parts.append(f"{repairs} repair(s) applied")

    summary = "; ".join(summary_parts) if summary_parts else None

    # Persist to DB
    try:
        with get_session_context() as db:
            status = result.status
            if status == "succeeded":
                db_status = AgentRunStatus.SUCCEEDED.value
            elif status == "failed":
                db_status = AgentRunStatus.FAILED.value
            elif status == "max_iterations":
                db_status = AgentRunStatus.MAX_ITERATIONS.value
            else:
                db_status = AgentRunStatus.ERROR.value

            # Serialize events to JSON for DB storage
            events_json = None
            if result.trace and result.trace.events:
                try:
                    events_json = json.dumps(
                        [e.to_dict() for e in result.trace.events],
                        ensure_ascii=False,
                    )
                except Exception:
                    pass

            update_agent_run(db, run_id, {
                "status": db_status,
                "iterations": result.iterations,
                "repairs_applied": (
                    result.trace.summary().get("repairs_applied", 0)
                    if result.trace else 0
                ),
                "trace_path": result.trace._get_file_path() if result.trace else None,
                "events_json": events_json,
                "summary": summary,
                "error": result.error[:500] if result.error else None,
                "completed_at": _utcnow(),
            })
    except Exception as exc:
        logger.error("Failed to persist agent run result: %s", exc)


# ---- helpers ----

def _get_backend_data_dir() -> str:
    """Get the absolute path to backend/data/ using engine's _DATA_DIR."""
    from app.db.engine import _DATA_DIR
    return _DATA_DIR


def _resolve_repo_dir_by_id(project_id: str) -> Optional[str]:
    """Resolve repo dir from project ID (without DB object)."""
    import os as _os
    data_dir = _get_backend_data_dir()
    repo = _os.path.join(data_dir, "code_projects", project_id, "repo")
    if _os.path.isdir(repo):
        return repo
    proj_dir = _os.path.join(data_dir, "code_projects", project_id)
    if _os.path.isdir(proj_dir):
        return proj_dir
    return None


def _resolve_repo_dir(project) -> Optional[str]:
    """Resolve the repo directory on disk from a project record."""
    import os as _os

    # Try root_storage_path
    if hasattr(project, "root_storage_path") and project.root_storage_path:
        repo = _os.path.join(project.root_storage_path, "repo")
        if _os.path.isdir(repo):
            return repo
        if _os.path.isdir(project.root_storage_path):
            return project.root_storage_path

    # Fall back to data/code_projects/{project_id}/repo
    data_dir = _get_backend_data_dir()
    repo = _os.path.join(data_dir, "code_projects", project.id, "repo")
    if _os.path.isdir(repo):
        return repo

    # Try without /repo suffix
    proj_dir = _os.path.join(data_dir, "code_projects", project.id)
    if _os.path.isdir(proj_dir):
        return proj_dir

    return None


def _utcnow():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)
