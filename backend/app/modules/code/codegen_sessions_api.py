"""
CodeGen Sessions API — Start, poll, and manage agent-driven code generation sessions.

Endpoints:
- POST /codegen/sessions              Create a new codegen session
- POST /codegen/sessions/{id}/start   Start the agent pipeline
- GET  /codegen/sessions/{id}         Get session status + steps
- GET  /codegen/sessions              List sessions
"""

import logging
import threading
from typing import Optional

from fastapi import APIRouter, HTTPException, status, BackgroundTasks
from pydantic import BaseModel, Field

from app.agents.codegen.kernel import (
    AgentKernel, CodeGenSession as KernelSession,
    get_session, list_sessions, _sessions, _save_session, _gen_id,
)
from app.modules.platform.storage import get_plan_candidate_storage, get_plan_link, get_plan_session_storage
from app.services.code_project_service import create_project
from app.db.engine import get_session_context
from app.core.settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/codegen", tags=["codegen_sessions"])


class CreateSessionRequest(BaseModel):
    planLinkId: Optional[str] = None
    planSessionId: Optional[str] = None
    candidateId: Optional[str] = None
    # Omit to use active provider/model from Settings (provider_config.json / env)
    providerName: Optional[str] = None
    model: Optional[str] = None
    language: str = "python"
    framework: str = "FastAPI"
    enableWebSearch: bool = True
    enableGithub: bool = True
    existingProjectId: Optional[str] = None


class StartSessionRequest(BaseModel):
    pass  # No body needed; config is already in the session


def _resolve_plan_context(req: CreateSessionRequest) -> dict:
    """Resolve plan session + candidate data from link or direct IDs."""
    plan_session_id = req.planSessionId
    candidate_id = req.candidateId

    # If linkId provided, load from link
    if req.planLinkId:
        link_data = get_plan_link(req.planLinkId)
        if link_data:
            plan_session_id = link_data.get("planSessionId", plan_session_id)
            candidate_id = link_data.get("candidateId", candidate_id)

    # Load plan session and candidate
    sess_storage = get_plan_session_storage()
    cand_storage = get_plan_candidate_storage()

    plan_session = sess_storage.get(plan_session_id) if plan_session_id else None
    candidate = cand_storage.get(candidate_id) if candidate_id else None

    title = "Research Project"
    abstract = ""
    method = ""
    research_question = ""
    gap_analysis = ""

    if candidate:
        title = candidate.title or title
        abstract = candidate.planAbstract or ""
        method = candidate.method or ""
        gap_analysis = candidate.gapAnalysis or ""
        if candidate.experimentDesign:
            if hasattr(candidate.experimentDesign, "research_question"):
                research_question = candidate.experimentDesign.research_question
            elif isinstance(candidate.experimentDesign, dict):
                research_question = candidate.experimentDesign.get("research_question", "")

    return {
        "planSessionId": plan_session_id,
        "candidateId": candidate_id,
        "title": title,
        "abstract": abstract,
        "method": method,
        "research_question": research_question,
        "gap_analysis": gap_analysis,
    }


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_codegen_session(req: CreateSessionRequest):
    """Create a new codegen session (does not start it yet)."""
    settings = get_settings()
    provider_name = req.providerName if req.providerName is not None else settings.get_active_provider()
    model_name = req.model if req.model is not None else settings.get_active_model(provider_name)

    ctx = _resolve_plan_context(req)

    # Create or reuse project
    project_id = req.existingProjectId
    if not project_id:
        with get_session_context() as db:
            project = create_project(
                db,
                title=f"{ctx['title']} [{req.language}]",
                description=ctx["abstract"][:500],
                language=req.language,
                framework=req.framework,
                source_idea_session_id=ctx.get("planSessionId"),
                source_candidate_id=ctx.get("candidateId"),
            )
            project_id = project.id

    from datetime import datetime
    session = KernelSession(
        id=_gen_id(),
        projectId=project_id,
        planLinkId=req.planLinkId,
        providerName=provider_name,
        model=model_name,
        status="pending",
        config={
            "language": req.language,
            "framework": req.framework,
            "enableWebSearch": req.enableWebSearch,
            "enableGithub": req.enableGithub,
            "planSessionId": ctx["planSessionId"],
            "candidateId": ctx["candidateId"],
            "title": ctx["title"],
            "abstract": ctx["abstract"][:300],
            "method": ctx["method"][:300],
        },
        createdAt=datetime.utcnow().isoformat(),
    )
    _sessions[session.id] = session
    _save_session(session)

    return session.to_dict()


def _run_agent(session_id: str):
    """Background task: run the agent kernel for a session."""
    session = get_session(session_id)
    if not session:
        logger.error(f"Session {session_id} not found for agent run")
        return

    config = session.config
    kernel = AgentKernel(
        provider_name=session.providerName,
        model=session.model,
        language=config.get("language", "python"),
        framework=config.get("framework", "FastAPI"),
        enable_web_search=config.get("enableWebSearch", True),
        enable_github=config.get("enableGithub", True),
    )

    try:
        kernel.run(
            session=session,
            title=config.get("title", "Research Project"),
            abstract=config.get("abstract", ""),
            method=config.get("method", ""),
            research_question=config.get("research_question", ""),
            gap_analysis=config.get("gap_analysis", ""),
        )
    except Exception as e:
        logger.error(f"Agent run failed for session {session_id}: {e}", exc_info=True)


@router.post("/sessions/{session_id}/start", status_code=status.HTTP_202_ACCEPTED)
async def start_codegen_session(session_id: str, background_tasks: BackgroundTasks):
    """Start the agent pipeline for a session (runs in background)."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    if session.status == "running":
        raise HTTPException(status_code=409, detail="Session already running")
    if session.status == "completed":
        raise HTTPException(status_code=409, detail="Session already completed")

    # Run agent in background thread (not asyncio — the LLM calls are blocking)
    thread = threading.Thread(target=_run_agent, args=(session_id,), daemon=True)
    thread.start()

    return {"sessionId": session_id, "status": "starting", "message": "Agent pipeline started"}


@router.get("/sessions/{session_id}")
async def get_codegen_session(session_id: str):
    """Get session status, steps, and memory summary."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return session.to_dict()


@router.get("/sessions")
async def list_codegen_sessions(projectId: Optional[str] = None):
    """List all codegen sessions."""
    sessions = list_sessions(project_id=projectId)
    return {"sessions": sessions, "total": len(sessions)}


@router.get("/sessions/{session_id}/validate")
async def validate_codegen_repo(session_id: str):
    """Validate the generated repo for project-grade quality."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    if session.status != "completed":
        raise HTTPException(status_code=400, detail="Session not completed yet")

    # Load files from project
    project_id = session.projectId
    try:
        from app.services.code_project_service import get_file_tree, read_file_content
        from app.db.engine import get_session_context
        with get_session_context() as db:
            tree = get_file_tree(db, project_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load project files: {e}")

    paths = [n.get("path", "") for n in (tree or []) if not n.get("is_dir")]
    issues = []
    required = ["README.md"]
    for req in required:
        if req not in paths:
            issues.append({"severity": "error", "message": f"Missing required file: {req}"})

    has_tests = any("test" in p.lower() for p in paths)
    has_ci = any(".github" in p or "ci" in p.lower() for p in paths)
    has_db = any("db" in p.lower() or "model" in p.lower() or "migration" in p.lower() for p in paths)
    has_docs = sum(1 for p in paths if p.endswith(".md") or p.startswith("docs/"))

    if not has_tests:
        issues.append({"severity": "warning", "message": "No test files found"})
    if not has_ci:
        issues.append({"severity": "warning", "message": "No CI/CD config found"})
    if not has_db:
        issues.append({"severity": "warning", "message": "No database layer found"})
    if has_docs < 2:
        issues.append({"severity": "warning", "message": f"Only {has_docs} documentation files"})
    if len(paths) < 20:
        issues.append({"severity": "error", "message": f"Only {len(paths)} files (expected >= 40)"})
    elif len(paths) < 40:
        issues.append({"severity": "warning", "message": f"Only {len(paths)} files (target >= 45)"})

    score = max(0, 100 - sum(10 if i["severity"] == "error" else 3 for i in issues))
    return {
        "sessionId": session_id,
        "projectId": project_id,
        "fileCount": len(paths),
        "issues": issues,
        "qualityScore": score,
        "passed": score >= 60 and not any(i["severity"] == "error" for i in issues),
        "categories": {
            "tests": has_tests,
            "ci": has_ci,
            "db": has_db,
            "docs": has_docs,
        },
    }
