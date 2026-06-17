"""
Code Jobs API Endpoints

Provides endpoints for:
- Creating and managing execution jobs
- Starting/stopping jobs
- Streaming logs
- Viewing artifacts
"""

import os
import json
import asyncio
import logging
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, HTTPException, status, BackgroundTasks, Depends
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel, Field
from app.modules.code.runtime import get_job_runner, get_workspace_manager
from app.modules.code.storage import (
    ArtifactKind,
    CodeJob,
    CodeCandidateDB,
    CodeSessionDB,
    JobStatus,
    Session,
    crud,
    get_session,
    get_session_context,
    init_db,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/code/jobs", tags=["code_jobs"])

# Request/Response Models

class CreateJobRequest(BaseModel):
    """Request to create a job."""
    sessionId: Optional[str] = Field(None, description="Code session ID (legacy)")
    projectId: Optional[str] = Field(None, description="Code project ID (V2)")
    candidateId: Optional[str] = Field(None, description="Candidate ID (optional)")
    mode: str = Field(default="quick", description="Execution mode: quick or debug")
    command: str = Field(..., description="Command to execute")
    envVars: Optional[dict] = Field(None, description="Environment variables")
    cwdRel: Optional[str] = Field(None, description="Relative working directory")
    timeoutSec: int = Field(default=300, ge=10, le=3600, description="Timeout in seconds")


class JobResponse(BaseModel):
    """Job response."""
    id: str
    sessionId: Optional[str] = None
    projectId: Optional[str] = None
    candidateId: Optional[str] = None
    status: str
    mode: str
    command: str
    envVars: Optional[dict] = None
    cwdRel: Optional[str] = None
    timeoutSec: int
    workspacePath: Optional[str] = None
    pid: Optional[int] = None
    exitCode: Optional[int] = None
    stdoutPath: Optional[str] = None
    stderrPath: Optional[str] = None
    createdAt: str
    startedAt: Optional[str] = None
    endedAt: Optional[str] = None
    durationSec: Optional[int] = None


class JobListResponse(BaseModel):
    """Job list response."""
    jobs: List[JobResponse]
    total: int


class LogResponse(BaseModel):
    """Log response."""
    jobId: str
    logType: str
    lines: List[str]
    totalLines: int


class ArtifactResponse(BaseModel):
    """Artifact response."""
    id: str
    kind: str
    path: str
    filename: str
    sizeBytes: int
    createdAt: str


class ArtifactListResponse(BaseModel):
    """Artifact list response."""
    artifacts: List[ArtifactResponse]
    total: int


# Helper functions

def job_to_response(job: CodeJob) -> JobResponse:
    env_vars = None
    if job.env_vars:
        try:
            env_vars = json.loads(job.env_vars)
        except:
            pass

    return JobResponse(
        id=job.id,
        sessionId=job.session_id,
        projectId=job.project_id,
        candidateId=job.candidate_id,
        status=job.status.value if isinstance(job.status, JobStatus) else job.status,
        mode=job.mode,
        command=job.command,
        envVars=env_vars,
        cwdRel=job.cwd_rel,
        timeoutSec=job.timeout_sec,
        workspacePath=job.workspace_path,
        pid=job.pid,
        exitCode=job.exit_code,
        stdoutPath=job.stdout_path,
        stderrPath=job.stderr_path,
        createdAt=job.created_at.isoformat() if job.created_at else "",
        startedAt=job.started_at.isoformat() if job.started_at else None,
        endedAt=job.ended_at.isoformat() if job.ended_at else None,
        durationSec=job.duration_sec,
    )


async def run_job_background(
    job_id: str,
    command: str,
    workspace_path: str,
    cwd_rel: Optional[str],
    env_vars: Optional[dict],
    timeout_sec: int,
    session_id: str,
    candidate_id: Optional[str],
):
    """Run job in background and update DB."""
    runner = get_job_runner()
    
    def update_status(status: str, data: dict):
        logger.info(f"Job {job_id} status: {status} - {data}")
    
    # Run the job
    result = await runner.run_job(
        job_id=job_id,
        command=command,
        workspace_path=workspace_path,
        cwd_rel=cwd_rel,
        env_vars=env_vars,
        timeout_sec=timeout_sec,
        on_status=update_status,
    )
    
    # Update job in DB
    with get_session_context() as db:
        updates = {
            "exit_code": result.get("exit_code"),
            "stdout_path": result.get("stdout_path"),
            "stderr_path": result.get("stderr_path"),
            "ended_at": datetime.utcnow(),
            "duration_sec": result.get("duration_sec"),
        }
        
        if result.get("success"):
            updates["status"] = JobStatus.SUCCEEDED
        elif result.get("error"):
            updates["status"] = JobStatus.FAILED
        else:
            updates["status"] = JobStatus.FAILED
        
        crud.update_job(db, job_id, updates)
        
        # Create artifacts for logs
        if result.get("stdout_path"):
            try:
                size = os.path.getsize(result["stdout_path"])
                crud.create_artifact(db, crud.ArtifactCreate(
                    session_id=session_id,
                    job_id=job_id,
                    kind=ArtifactKind.STDOUT,
                    path=result["stdout_path"],
                    filename="stdout.log",
                    size_bytes=size,
                ))
            except:
                pass
        
        if result.get("stderr_path"):
            try:
                size = os.path.getsize(result["stderr_path"])
                crud.create_artifact(db, crud.ArtifactCreate(
                    session_id=session_id,
                    job_id=job_id,
                    kind=ArtifactKind.STDERR,
                    path=result["stderr_path"],
                    filename="stderr.log",
                    size_bytes=size,
                ))
            except:
                pass


# Endpoints

@router.post(
    "",
    response_model=JobResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Job",
    description="Create a new execution job."
)
async def create_job(
    request: CreateJobRequest,
    db: Session = Depends(get_session),
) -> JobResponse:
    """Create a new job (supports both legacy session and new project-based)."""
    # Validate: must have sessionId or projectId
    if not request.sessionId and not request.projectId:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either sessionId or projectId is required"
        )

    # Verify session or project exists
    if request.sessionId:
        code_session = crud.get_code_session(db, request.sessionId)
        if not code_session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {request.sessionId}"
            )
    if request.projectId:
        project = crud.get_project_v2(db, request.projectId)
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Project not found: {request.projectId}"
            )

    # Verify candidate if provided
    candidate = None
    if request.candidateId:
        candidate = crud.get_candidate(db, request.candidateId)
        if not candidate:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Candidate not found: {request.candidateId}"
            )

    # Check command safety
    runner = get_job_runner()
    is_safe, reason = runner.is_command_safe(request.command)
    if not is_safe:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsafe command blocked: {reason}"
        )

    # Create job in DB
    env_vars_json = json.dumps(request.envVars) if request.envVars else None

    job = crud.create_job(db, crud.CodeJobCreate(
        session_id=request.sessionId,
        project_id=request.projectId,
        candidate_id=request.candidateId,
        mode=request.mode,
        command=request.command,
        env_vars=env_vars_json,
        cwd_rel=request.cwdRel,
        timeout_sec=request.timeoutSec,
    ))

    return job_to_response(job)


@router.post(
    "/{job_id}/start",
    response_model=JobResponse,
    summary="Start Job",
    description="Start a pending job."
)
async def start_job(
    job_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
) -> JobResponse:
    """Start a job."""
    job = crud.get_job(db, job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job not found: {job_id}"
        )
    
    if job.status != JobStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job cannot be started. Current status: {job.status.value}"
        )
    
    # Get session for repo path
    code_session = crud.get_code_session(db, job.session_id)
    if not code_session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {job.session_id}"
        )
    
    # Get repo path from session config or project
    repo_path = None
    if code_session.project_id:
        project = crud.get_project(db, code_session.project_id)
        if project:
            repo_path = project.repo_path
    
    if not repo_path:
        # Try to get from goal (legacy sessions store repo path differently)
        # For now, use a default test path
        repo_path = "/data/guiyao/Auto-LLM/AI-Researcher/backend"
    
    # Create workspace
    workspace_manager = get_workspace_manager()
    workspace_path = workspace_manager.create_workspace(job_id, repo_path)
    
    # Apply patch if candidate has one
    if job.candidate_id:
        candidate = crud.get_candidate(db, job.candidate_id)
        if candidate and candidate.patch:
            success, msg = workspace_manager.apply_patch(workspace_path, candidate.patch)
            if not success:
                logger.warning(f"Patch application failed: {msg}")
    
    # Update job status
    job = crud.update_job(db, job_id, {
        "status": JobStatus.RUNNING,
        "workspace_path": workspace_path,
        "started_at": datetime.utcnow(),
    })
    
    # Parse env vars
    env_vars = None
    if job.env_vars:
        try:
            env_vars = json.loads(job.env_vars)
        except:
            pass
    
    # Start background execution
    background_tasks.add_task(
        run_job_background,
        job_id=job_id,
        command=job.command,
        workspace_path=workspace_path,
        cwd_rel=job.cwd_rel,
        env_vars=env_vars,
        timeout_sec=job.timeout_sec,
        session_id=job.session_id,
        candidate_id=job.candidate_id,
    )
    
    return job_to_response(job)


@router.post(
    "/{job_id}/stop",
    response_model=JobResponse,
    summary="Stop Job",
    description="Stop a running job."
)
async def stop_job(
    job_id: str,
    db: Session = Depends(get_session),
) -> JobResponse:
    """Stop a running job."""
    job = crud.get_job(db, job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job not found: {job_id}"
        )
    
    if job.status != JobStatus.RUNNING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job is not running. Current status: {job.status.value}"
        )
    
    # Stop the process
    runner = get_job_runner()
    stopped = await runner.stop_job(job_id)
    
    # Update status
    job = crud.update_job(db, job_id, {
        "status": JobStatus.CANCELLED,
        "ended_at": datetime.utcnow(),
    })
    
    return job_to_response(job)


@router.get(
    "",
    response_model=JobListResponse,
    summary="List Jobs",
    description="List jobs with optional filters."
)
async def list_jobs(
    sessionId: Optional[str] = None,
    candidateId: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
    db: Session = Depends(get_session),
) -> JobListResponse:
    """List jobs."""
    job_status = None
    if status:
        try:
            job_status = JobStatus(status)
        except ValueError:
            pass
    
    jobs = crud.list_jobs(
        db,
        session_id=sessionId,
        candidate_id=candidateId,
        status=job_status,
        limit=limit,
    )
    
    return JobListResponse(
        jobs=[job_to_response(j) for j in jobs],
        total=len(jobs),
    )


@router.get(
    "/{job_id}",
    response_model=JobResponse,
    summary="Get Job",
    description="Get job details."
)
async def get_job(
    job_id: str,
    db: Session = Depends(get_session),
) -> JobResponse:
    """Get job by ID."""
    job = crud.get_job(db, job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job not found: {job_id}"
        )
    
    return job_to_response(job)


@router.get(
    "/{job_id}/logs",
    response_model=LogResponse,
    summary="Get Job Logs",
    description="Get job log tail."
)
async def get_job_logs(
    job_id: str,
    logType: str = "stdout",
    lines: int = 100,
    db: Session = Depends(get_session),
) -> LogResponse:
    """Get job logs."""
    job = crud.get_job(db, job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job not found: {job_id}"
        )
    
    if logType not in ["stdout", "stderr"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="logType must be 'stdout' or 'stderr'"
        )
    
    runner = get_job_runner()
    log_lines = runner.get_log_tail(job_id, logType, lines)
    
    return LogResponse(
        jobId=job_id,
        logType=logType,
        lines=[line.rstrip() for line in log_lines],
        totalLines=len(log_lines),
    )


@router.get(
    "/{job_id}/logs/download",
    summary="Download Job Logs",
    description="Download full job log file."
)
async def download_job_logs(
    job_id: str,
    logType: str = "stdout",
    db: Session = Depends(get_session),
):
    """Download job log file."""
    job = crud.get_job(db, job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job not found: {job_id}"
        )
    
    runner = get_job_runner()
    artifact_dir = runner.get_artifact_dir(job_id)
    log_path = os.path.join(artifact_dir, f"{logType}.log")
    
    if not os.path.exists(log_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Log file not found: {logType}.log"
        )
    
    return FileResponse(
        log_path,
        media_type="text/plain",
        filename=f"{job_id}_{logType}.log",
    )


@router.get(
    "/{job_id}/artifacts",
    response_model=ArtifactListResponse,
    summary="Get Job Artifacts",
    description="List artifacts for a job."
)
async def get_job_artifacts(
    job_id: str,
    db: Session = Depends(get_session),
) -> ArtifactListResponse:
    """Get job artifacts."""
    job = crud.get_job(db, job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job not found: {job_id}"
        )
    
    artifacts = crud.list_artifacts(db, job_id=job_id)
    
    return ArtifactListResponse(
        artifacts=[
            ArtifactResponse(
                id=a.id,
                kind=a.kind.value if isinstance(a.kind, ArtifactKind) else a.kind,
                path=a.path,
                filename=a.filename,
                sizeBytes=a.size_bytes,
                createdAt=a.created_at.isoformat() if a.created_at else "",
            )
            for a in artifacts
        ],
        total=len(artifacts),
    )


class EvalResponse(BaseModel):
    """Evaluation response."""
    id: str
    jobId: str
    candidateId: Optional[str]
    syntaxValid: bool
    lintScore: float
    riskCount: int
    testPassed: Optional[bool]
    testOutput: Optional[str]
    overallScore: float
    grade: str
    scores: Optional[dict]
    createdAt: str


@router.get(
    "/{job_id}/evaluation",
    response_model=EvalResponse,
    summary="Get Job Evaluation",
    description="Get evaluation results for a completed job."
)
async def get_job_evaluation(
    job_id: str,
    db: Session = Depends(get_session),
) -> EvalResponse:
    """Get job evaluation."""
    job = crud.get_job(db, job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job not found: {job_id}"
        )
    
    eval_report = crud.get_eval_report_by_job(db, job_id)
    if not eval_report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No evaluation found for job: {job_id}"
        )
    
    scores = None
    if eval_report.scores:
        try:
            scores = json.loads(eval_report.scores)
        except:
            pass
    
    return EvalResponse(
        id=eval_report.id,
        jobId=eval_report.job_id,
        candidateId=eval_report.candidate_id,
        syntaxValid=eval_report.syntax_valid,
        lintScore=eval_report.lint_score,
        riskCount=eval_report.risk_count,
        testPassed=eval_report.test_passed,
        testOutput=eval_report.test_output,
        overallScore=eval_report.overall_score,
        grade=eval_report.grade,
        scores=scores,
        createdAt=eval_report.created_at.isoformat() if eval_report.created_at else "",
    )


@router.delete(
    "/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete Job",
)
async def delete_job_endpoint(
    job_id: str,
    db: Session = Depends(get_session),
):
    """Delete a job and its associated artifacts, eval reports, and pipeline files."""
    job = crud.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    # Clean up pipeline files on disk
    if job.project_id and job.workspace_path:
        import glob as _glob
        repo_dir = job.workspace_path
        patterns = ["pipeline_results.json", "pipeline_step_*.json"]
        for pattern in patterns:
            for f in _glob.glob(os.path.join(repo_dir, pattern)):
                try:
                    os.remove(f)
                except OSError:
                    pass

    # Clean up artifact files on disk
    if job.stdout_path and os.path.exists(job.stdout_path):
        try: os.remove(job.stdout_path)
        except OSError: pass
    if job.stderr_path and os.path.exists(job.stderr_path):
        try: os.remove(job.stderr_path)
        except OSError: pass

    crud.delete_job(db, job_id)
