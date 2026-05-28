"""
Database CRUD Operations - Data access layer for Phase 2.1

Provides typed CRUD operations for all code module entities.
"""

import uuid
import json
import logging
from datetime import datetime, UTC
from typing import List, Optional, Dict, Any

from sqlmodel import Session, select

from .models import (
    CodeProject, CodeProjectCreate,
    CodeSessionDB, CodeSessionCreate, SessionStatus,
    RepoContextDB, RepoContextCreate,
    CodeCandidateDB, CodeCandidateCreate,
    CodeJob, CodeJobCreate, JobStatus,
    EvalReportDB, EvalReportCreate,
    TraceLogDB, TraceLogCreate,
    ArtifactDB, ArtifactCreate, ArtifactKind,
    CodeProjectV2, CodeProjectV2Create,
    CodeProjectFile, CodeProjectFileCreate,
    CodeProjectGeneration, CodeProjectGenerationCreate, GenerationStatus,
    CodeProjectExport, CodeProjectExportCreate,
)

logger = logging.getLogger(__name__)


def generate_id(prefix: str = "") -> str:
    """Generate a unique ID with optional prefix."""
    uid = uuid.uuid4().hex[:12]
    return f"{prefix}_{uid}" if prefix else uid


# ============ Projects ============

def create_project(session: Session, data: CodeProjectCreate) -> CodeProject:
    """Create a new project."""
    project = CodeProject(
        id=generate_id("proj"),
        **data.model_dump(),
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


def get_project(session: Session, project_id: str) -> Optional[CodeProject]:
    """Get project by ID."""
    return session.get(CodeProject, project_id)


def list_projects(session: Session, limit: int = 100, offset: int = 0) -> List[CodeProject]:
    """List all projects."""
    stmt = select(CodeProject).order_by(CodeProject.created_at.desc()).offset(offset).limit(limit)
    return list(session.exec(stmt).all())


def update_project(session: Session, project_id: str, updates: Dict[str, Any]) -> Optional[CodeProject]:
    """Update a project."""
    project = session.get(CodeProject, project_id)
    if not project:
        return None
    
    for key, value in updates.items():
        if hasattr(project, key):
            setattr(project, key, value)
    
    project.updated_at = _utcnow()
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


def delete_project(session: Session, project_id: str) -> bool:
    """Delete a project."""
    project = session.get(CodeProject, project_id)
    if not project:
        return False
    session.delete(project)
    session.commit()
    return True


# ============ Repo Contexts ============

def create_repo_context(session: Session, data: RepoContextCreate) -> RepoContextDB:
    """Create a new repo context."""
    ctx = RepoContextDB(
        id=generate_id("ctx"),
        **data.model_dump(),
        created_at=_utcnow(),
    )
    session.add(ctx)
    session.commit()
    session.refresh(ctx)
    return ctx


def get_repo_context(session: Session, context_id: str) -> Optional[RepoContextDB]:
    """Get repo context by ID."""
    return session.get(RepoContextDB, context_id)


def list_repo_contexts(session: Session, project_id: Optional[str] = None) -> List[RepoContextDB]:
    """List repo contexts, optionally filtered by project."""
    stmt = select(RepoContextDB).order_by(RepoContextDB.created_at.desc())
    if project_id:
        stmt = stmt.where(RepoContextDB.project_id == project_id)
    return list(session.exec(stmt).all())


# ============ Code Sessions ============

def create_code_session(session: Session, data: CodeSessionCreate) -> CodeSessionDB:
    """Create a new code session."""
    code_session = CodeSessionDB(
        id=generate_id("code"),
        **data.model_dump(),
        status=SessionStatus.PENDING,
        created_at=_utcnow(),
    )
    session.add(code_session)
    session.commit()
    session.refresh(code_session)
    return code_session


def get_code_session(session: Session, session_id: str) -> Optional[CodeSessionDB]:
    """Get code session by ID."""
    return session.get(CodeSessionDB, session_id)


def list_code_sessions(
    session: Session,
    project_id: Optional[str] = None,
    status: Optional[SessionStatus] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[CodeSessionDB]:
    """List code sessions with optional filters."""
    stmt = select(CodeSessionDB).order_by(CodeSessionDB.created_at.desc())
    
    if project_id:
        stmt = stmt.where(CodeSessionDB.project_id == project_id)
    if status:
        stmt = stmt.where(CodeSessionDB.status == status)
    
    stmt = stmt.offset(offset).limit(limit)
    return list(session.exec(stmt).all())


def update_code_session(session: Session, session_id: str, updates: Dict[str, Any]) -> Optional[CodeSessionDB]:
    """Update a code session."""
    code_session = session.get(CodeSessionDB, session_id)
    if not code_session:
        return None
    
    for key, value in updates.items():
        if hasattr(code_session, key):
            setattr(code_session, key, value)
    
    session.add(code_session)
    session.commit()
    session.refresh(code_session)
    return code_session


def delete_code_session(session: Session, session_id: str) -> bool:
    """Delete a code session."""
    code_session = session.get(CodeSessionDB, session_id)
    if not code_session:
        return False
    session.delete(code_session)
    session.commit()
    return True


# ============ Code Candidates ============

def create_candidate(session: Session, data: CodeCandidateCreate) -> CodeCandidateDB:
    """Create a new candidate."""
    candidate = CodeCandidateDB(
        id=generate_id("cand"),
        **data.model_dump(),
        created_at=_utcnow(),
    )
    session.add(candidate)
    session.commit()
    session.refresh(candidate)
    return candidate


def get_candidate(session: Session, candidate_id: str) -> Optional[CodeCandidateDB]:
    """Get candidate by ID."""
    return session.get(CodeCandidateDB, candidate_id)


def list_candidates(session: Session, session_id: str) -> List[CodeCandidateDB]:
    """List candidates for a session."""
    stmt = (
        select(CodeCandidateDB)
        .where(CodeCandidateDB.session_id == session_id)
        .order_by(CodeCandidateDB.rank)
    )
    return list(session.exec(stmt).all())


def update_candidate(session: Session, candidate_id: str, updates: Dict[str, Any]) -> Optional[CodeCandidateDB]:
    """Update a candidate."""
    candidate = session.get(CodeCandidateDB, candidate_id)
    if not candidate:
        return None
    
    for key, value in updates.items():
        if hasattr(candidate, key):
            setattr(candidate, key, value)
    
    session.add(candidate)
    session.commit()
    session.refresh(candidate)
    return candidate


# ============ Code Jobs ============

def create_job(session: Session, data: CodeJobCreate) -> CodeJob:
    """Create a new job."""
    job = CodeJob(
        id=generate_id("job"),
        **data.model_dump(),
        status=JobStatus.PENDING,
        created_at=_utcnow(),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def get_job(session: Session, job_id: str) -> Optional[CodeJob]:
    """Get job by ID."""
    return session.get(CodeJob, job_id)


def list_jobs(
    session: Session,
    session_id: Optional[str] = None,
    candidate_id: Optional[str] = None,
    status: Optional[JobStatus] = None,
    limit: int = 100,
) -> List[CodeJob]:
    """List jobs with optional filters."""
    stmt = select(CodeJob).order_by(CodeJob.created_at.desc())
    
    if session_id:
        stmt = stmt.where(CodeJob.session_id == session_id)
    if candidate_id:
        stmt = stmt.where(CodeJob.candidate_id == candidate_id)
    if status:
        stmt = stmt.where(CodeJob.status == status)
    
    stmt = stmt.limit(limit)
    return list(session.exec(stmt).all())


def update_job(session: Session, job_id: str, updates: Dict[str, Any]) -> Optional[CodeJob]:
    """Update a job."""
    job = session.get(CodeJob, job_id)
    if not job:
        return None
    
    for key, value in updates.items():
        if hasattr(job, key):
            setattr(job, key, value)
    
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


# ============ Eval Reports ============

def create_eval_report(session: Session, data: EvalReportCreate) -> EvalReportDB:
    """Create a new eval report."""
    report = EvalReportDB(
        id=generate_id("eval"),
        **data.model_dump(),
        created_at=_utcnow(),
    )
    session.add(report)
    session.commit()
    session.refresh(report)
    return report


def get_eval_report(session: Session, report_id: str) -> Optional[EvalReportDB]:
    """Get eval report by ID."""
    return session.get(EvalReportDB, report_id)


def get_eval_report_by_job(session: Session, job_id: str) -> Optional[EvalReportDB]:
    """Get eval report for a job."""
    stmt = select(EvalReportDB).where(EvalReportDB.job_id == job_id)
    return session.exec(stmt).first()


# ============ Trace Logs ============

def create_trace_log(session: Session, data: TraceLogCreate) -> TraceLogDB:
    """Create a new trace log."""
    log = TraceLogDB(
        **data.model_dump(),
        created_at=_utcnow(),
    )
    session.add(log)
    session.commit()
    session.refresh(log)
    return log


def list_trace_logs(
    session: Session,
    job_id: Optional[str] = None,
    session_id: Optional[str] = None,
    limit: int = 1000,
) -> List[TraceLogDB]:
    """List trace logs."""
    stmt = select(TraceLogDB).order_by(TraceLogDB.created_at)
    
    if job_id:
        stmt = stmt.where(TraceLogDB.job_id == job_id)
    if session_id:
        stmt = stmt.where(TraceLogDB.session_id == session_id)
    
    stmt = stmt.limit(limit)
    return list(session.exec(stmt).all())


# ============ Artifacts ============

def create_artifact(session: Session, data: ArtifactCreate) -> ArtifactDB:
    """Create a new artifact."""
    artifact = ArtifactDB(
        id=generate_id("art"),
        **data.model_dump(),
        created_at=_utcnow(),
    )
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    return artifact


def get_artifact(session: Session, artifact_id: str) -> Optional[ArtifactDB]:
    """Get artifact by ID."""
    return session.get(ArtifactDB, artifact_id)


def list_artifacts(
    session: Session,
    job_id: Optional[str] = None,
    session_id: Optional[str] = None,
    project_id: Optional[str] = None,
    kind: Optional[ArtifactKind] = None,
) -> List[ArtifactDB]:
    """List artifacts with optional filters."""
    stmt = select(ArtifactDB).order_by(ArtifactDB.created_at.desc())
    
    if job_id:
        stmt = stmt.where(ArtifactDB.job_id == job_id)
    if session_id:
        stmt = stmt.where(ArtifactDB.session_id == session_id)
    if project_id:
        stmt = stmt.where(ArtifactDB.project_id == project_id)
    if kind:
        stmt = stmt.where(ArtifactDB.kind == kind)
    
    return list(session.exec(stmt).all())




def _utcnow() -> datetime:
    return datetime.now(UTC)


# ============ Code Projects V2 (Phase A) ============

def create_project_v2(session: Session, data: CodeProjectV2Create) -> CodeProjectV2:
    """Create a new code project v2."""
    project = CodeProjectV2(
        id=generate_id("cproj"),
        **data.model_dump(),
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


def get_project_v2(session: Session, project_id: str) -> Optional[CodeProjectV2]:
    return session.get(CodeProjectV2, project_id)


def list_projects_v2(
    session: Session,
    search: Optional[str] = None,
    language: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[CodeProjectV2]:
    stmt = select(CodeProjectV2).order_by(CodeProjectV2.created_at.desc())
    if search:
        stmt = stmt.where(CodeProjectV2.title.contains(search))
    if language:
        stmt = stmt.where(CodeProjectV2.language == language)
    stmt = stmt.offset(offset).limit(limit)
    return list(session.exec(stmt).all())


def update_project_v2(session: Session, project_id: str, updates: Dict[str, Any]) -> Optional[CodeProjectV2]:
    project = session.get(CodeProjectV2, project_id)
    if not project:
        return None
    for key, value in updates.items():
        if hasattr(project, key):
            setattr(project, key, value)
    project.updated_at = _utcnow()
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


def delete_project_v2(session: Session, project_id: str) -> bool:
    project = session.get(CodeProjectV2, project_id)
    if not project:
        return False
    session.delete(project)
    session.commit()
    return True


# ============ Code Project Files ============

def create_project_file(session: Session, data: CodeProjectFileCreate) -> CodeProjectFile:
    f = CodeProjectFile(
        id=generate_id("cpf"),
        **data.model_dump(),
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    session.add(f)
    session.commit()
    session.refresh(f)
    return f


def bulk_create_project_files(session: Session, files: List[CodeProjectFileCreate]) -> int:
    """Bulk insert project files. Returns count inserted."""
    count = 0
    for data in files:
        f = CodeProjectFile(
            id=generate_id("cpf"),
            **data.model_dump(),
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        session.add(f)
        count += 1
    session.commit()
    return count


def list_project_files(
    session: Session,
    project_id: str,
    parent_path: Optional[str] = None,
) -> List[CodeProjectFile]:
    """List files for a project, optionally filtering to direct children of parent_path."""
    stmt = select(CodeProjectFile).where(
        CodeProjectFile.project_id == project_id
    ).order_by(CodeProjectFile.is_dir.desc(), CodeProjectFile.path)

    results = list(session.exec(stmt).all())

    if parent_path is not None:
        prefix = parent_path.rstrip("/") + "/" if parent_path else ""
        filtered = []
        for f in results:
            if not f.path.startswith(prefix):
                continue
            remainder = f.path[len(prefix):]
            if "/" not in remainder:
                filtered.append(f)
        return filtered

    return results


def search_project_files(
    session: Session,
    project_id: str,
    query: str,
    mode: str = "path",
) -> List[CodeProjectFile]:
    """Search files by path name. Content search done at service layer."""
    stmt = select(CodeProjectFile).where(
        CodeProjectFile.project_id == project_id,
        CodeProjectFile.path.contains(query),
    ).order_by(CodeProjectFile.path).limit(200)
    return list(session.exec(stmt).all())


def delete_project_files(session: Session, project_id: str) -> int:
    """Delete all files for a project. Returns count deleted."""
    from sqlalchemy import delete as sa_delete
    stmt = sa_delete(CodeProjectFile).where(CodeProjectFile.project_id == project_id)
    result = session.exec(stmt)
    session.commit()
    return result.rowcount


# ============ Code Project Generations ============

def create_project_generation(session: Session, data: CodeProjectGenerationCreate) -> CodeProjectGeneration:
    gen = CodeProjectGeneration(
        id=generate_id("cpgen"),
        **data.model_dump(),
        created_at=_utcnow(),
    )
    session.add(gen)
    session.commit()
    session.refresh(gen)
    return gen


def get_project_generation(session: Session, gen_id: str) -> Optional[CodeProjectGeneration]:
    return session.get(CodeProjectGeneration, gen_id)


def update_project_generation(session: Session, gen_id: str, updates: Dict[str, Any]) -> Optional[CodeProjectGeneration]:
    gen = session.get(CodeProjectGeneration, gen_id)
    if not gen:
        return None
    for k, v in updates.items():
        if hasattr(gen, k):
            setattr(gen, k, v)
    session.add(gen)
    session.commit()
    session.refresh(gen)
    return gen


# ============ Code Project Exports ============

def create_project_export(session: Session, data: CodeProjectExportCreate) -> CodeProjectExport:
    exp = CodeProjectExport(
        id=generate_id("cpexp"),
        **data.model_dump(),
        created_at=_utcnow(),
    )
    session.add(exp)
    session.commit()
    session.refresh(exp)
    return exp


def get_project_export(session: Session, export_id: str) -> Optional[CodeProjectExport]:
    return session.get(CodeProjectExport, export_id)
