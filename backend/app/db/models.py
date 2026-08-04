"""
Database Models - SQLModel ORM models for Phase 2.1

Tables:
- code_projects: Top-level project containers
- repo_contexts: Repository context snapshots
- code_sessions: Code generation sessions
- code_candidates: Generated code candidates
- code_jobs: Execution jobs
- eval_reports: Evaluation results
- trace_logs: Structured execution logs
- artifacts: File artifact index
"""

import enum
from datetime import datetime
from typing import Optional, List
from sqlmodel import SQLModel, Field, Relationship, Column, JSON
from sqlalchemy import Text


# Enums

class JobStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SessionStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ArtifactKind(str, enum.Enum):
    PATCH = "patch"
    LOG = "log"
    STDOUT = "stdout"
    STDERR = "stderr"
    EVAL = "eval"
    CONFIG = "config"
    SCREENSHOT = "screenshot"
    OTHER = "other"


# Base Models (for creation)

class CodeProjectCreate(SQLModel):
    """Create schema for CodeProject."""
    name: str
    repo_path: str
    description: Optional[str] = None
    task_goal: Optional[str] = None
    constraints: Optional[str] = None
    expected_commands: Optional[str] = None  # JSON string
    acceptance_criteria: Optional[str] = None


class RepoContextCreate(SQLModel):
    """Create schema for RepoContext."""
    project_id: str
    repo_path: str
    file_count: int = 0
    chunk_count: int = 0
    total_lines: int = 0
    languages: Optional[str] = None  # JSON string
    scan_duration_ms: int = 0


class CodeSessionCreate(SQLModel):
    """Create schema for CodeSession."""
    project_id: Optional[str] = None
    repo_context_id: Optional[str] = None
    goal: str
    provider_name: str = "moonshot"
    model: str = "moonshot-v1-8k"
    max_candidates: int = 3
    max_iterations: int = 3
    constraints: Optional[str] = None
    target_files: Optional[str] = None  # JSON string


class CodeCandidateCreate(SQLModel):
    """Create schema for CodeCandidate."""
    session_id: str
    title: str
    approach: str
    rationale: Optional[str] = None
    patch: str = ""
    files_modified: Optional[str] = None  # JSON string
    testing_notes: Optional[str] = None
    run_commands: Optional[str] = None  # JSON string


class CodeJobCreate(SQLModel):
    """Create schema for CodeJob."""
    session_id: Optional[str] = None  # optional: now supports project-based jobs
    project_id: Optional[str] = None  # NEW: bind to CodeProjectV2
    candidate_id: Optional[str] = None
    mode: str = "quick"  # quick or debug
    command: str
    env_vars: Optional[str] = None  # JSON string
    cwd_rel: Optional[str] = None
    timeout_sec: int = 300


class EvalReportCreate(SQLModel):
    """Create schema for EvalReport."""
    job_id: str
    candidate_id: Optional[str] = None
    syntax_valid: bool = False
    lint_score: float = 0.0
    risk_count: int = 0
    test_passed: Optional[bool] = None
    test_output: Optional[str] = None
    scores: Optional[str] = None  # JSON string
    overall_score: float = 0.0
    grade: str = "F"


class TraceLogCreate(SQLModel):
    """Create schema for TraceLog."""
    job_id: str
    session_id: Optional[str] = None
    step: str
    status: str
    message: Optional[str] = None
    data: Optional[str] = None  # JSON string
    duration_ms: Optional[int] = None


class ArtifactCreate(SQLModel):
    """Create schema for Artifact."""
    project_id: Optional[str] = None
    session_id: Optional[str] = None
    job_id: Optional[str] = None
    kind: ArtifactKind
    path: str
    filename: str
    size_bytes: int = 0
    checksum: Optional[str] = None
    mime_type: Optional[str] = None


# Database Models (with table=True)

class CodeProject(SQLModel, table=True):
    """Top-level project container."""
    __tablename__ = "code_projects"
    
    id: str = Field(primary_key=True)
    name: str = Field(index=True)
    repo_path: str
    description: Optional[str] = None
    task_goal: Optional[str] = Field(default=None, sa_column=Column(Text))
    constraints: Optional[str] = Field(default=None, sa_column=Column(Text))
    expected_commands: Optional[str] = None  # JSON
    acceptance_criteria: Optional[str] = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    # Relationships
    sessions: List["CodeSessionDB"] = Relationship(back_populates="project")
    repo_contexts: List["RepoContextDB"] = Relationship(back_populates="project")


class RepoContextDB(SQLModel, table=True):
    """Repository context snapshot."""
    __tablename__ = "repo_contexts"
    
    id: str = Field(primary_key=True)
    project_id: str = Field(foreign_key="code_projects.id", index=True)
    repo_path: str
    file_count: int = 0
    chunk_count: int = 0
    total_lines: int = 0
    languages: Optional[str] = None  # JSON
    scan_duration_ms: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    # Relationships
    project: Optional[CodeProject] = Relationship(back_populates="repo_contexts")
    session: Optional["CodeSessionDB"] = Relationship(back_populates="repo_context")


class CodeSessionDB(SQLModel, table=True):
    """Code generation session."""
    __tablename__ = "code_sessions"
    
    id: str = Field(primary_key=True)
    project_id: Optional[str] = Field(default=None, foreign_key="code_projects.id", index=True)
    repo_context_id: Optional[str] = Field(default=None, foreign_key="repo_contexts.id")
    status: SessionStatus = Field(default=SessionStatus.PENDING)
    goal: str = Field(sa_column=Column(Text))
    provider_name: str = "moonshot"
    model: str = "moonshot-v1-8k"
    max_candidates: int = 3
    max_iterations: int = 3
    constraints: Optional[str] = Field(default=None, sa_column=Column(Text))
    target_files: Optional[str] = None  # JSON
    current_step: Optional[str] = None
    iteration_count: int = 0
    selected_candidate_id: Optional[str] = None
    summary: Optional[str] = Field(default=None, sa_column=Column(Text))
    error_message: Optional[str] = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_sec: Optional[int] = None
    
    # Relationships
    project: Optional[CodeProject] = Relationship(back_populates="sessions")
    repo_context: Optional[RepoContextDB] = Relationship(back_populates="session")
    candidates: List["CodeCandidateDB"] = Relationship(back_populates="session")
    jobs: List["CodeJob"] = Relationship(back_populates="session")
    trace_logs: List["TraceLogDB"] = Relationship(back_populates="session")


class CodeCandidateDB(SQLModel, table=True):
    """Generated code candidate."""
    __tablename__ = "code_candidates"
    
    id: str = Field(primary_key=True)
    session_id: str = Field(foreign_key="code_sessions.id", index=True)
    title: str
    approach: str = Field(sa_column=Column(Text))
    rationale: Optional[str] = Field(default=None, sa_column=Column(Text))
    patch: str = Field(default="", sa_column=Column(Text))
    files_modified: Optional[str] = None  # JSON
    testing_notes: Optional[str] = Field(default=None, sa_column=Column(Text))
    run_commands: Optional[str] = None  # JSON
    
    # Scores
    score_correctness: float = 0.0
    score_completeness: float = 0.0
    score_efficiency: float = 0.0
    score_readability: float = 0.0
    score_safety: float = 0.0
    overall_score: float = 0.0
    rank: int = 0
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    # Relationships
    session: Optional[CodeSessionDB] = Relationship(back_populates="candidates")
    jobs: List["CodeJob"] = Relationship(back_populates="candidate")


class CodeJob(SQLModel, table=True):
    """Execution job — supports both legacy CodeSession and new CodeProjectV2."""
    __tablename__ = "code_jobs"

    id: str = Field(primary_key=True)
    session_id: Optional[str] = Field(default=None, foreign_key="code_sessions.id", index=True)
    project_id: Optional[str] = Field(default=None, foreign_key="code_projects_v2.id", index=True)
    candidate_id: Optional[str] = Field(default=None, foreign_key="code_candidates.id", index=True)
    status: JobStatus = Field(default=JobStatus.PENDING)
    mode: str = "quick"  # quick or debug
    command: str
    env_vars: Optional[str] = None  # JSON
    cwd_rel: Optional[str] = None
    timeout_sec: int = 300

    # Execution details
    workspace_path: Optional[str] = None
    pid: Optional[int] = None
    exit_code: Optional[int] = None
    stdout_path: Optional[str] = None
    stderr_path: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_sec: Optional[int] = None

    # Relationships
    session: Optional[CodeSessionDB] = Relationship(back_populates="jobs")
    project_v2: Optional["CodeProjectV2"] = Relationship()
    candidate: Optional[CodeCandidateDB] = Relationship(back_populates="jobs")
    eval_report: Optional["EvalReportDB"] = Relationship(back_populates="job")
    trace_logs: List["TraceLogDB"] = Relationship(back_populates="job")
    artifacts: List["ArtifactDB"] = Relationship(back_populates="job")


class EvalReportDB(SQLModel, table=True):
    """Evaluation report."""
    __tablename__ = "eval_reports"
    
    id: str = Field(primary_key=True)
    job_id: str = Field(foreign_key="code_jobs.id", unique=True, index=True)
    candidate_id: Optional[str] = Field(default=None, foreign_key="code_candidates.id", index=True)
    
    # Static eval
    syntax_valid: bool = False
    lint_score: float = 0.0
    risk_count: int = 0
    lint_issues: Optional[str] = None  # JSON
    
    # Dynamic eval
    test_passed: Optional[bool] = None
    test_output: Optional[str] = Field(default=None, sa_column=Column(Text))
    test_duration_ms: Optional[int] = None
    
    # Scores
    scores: Optional[str] = None  # JSON
    overall_score: float = 0.0
    grade: str = "F"
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    # Relationships
    job: Optional[CodeJob] = Relationship(back_populates="eval_report")


class TraceLogDB(SQLModel, table=True):
    """Structured execution log."""
    __tablename__ = "trace_logs"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: str = Field(foreign_key="code_jobs.id", index=True)
    session_id: Optional[str] = Field(default=None, foreign_key="code_sessions.id", index=True)
    step: str
    status: str
    message: Optional[str] = Field(default=None, sa_column=Column(Text))
    data: Optional[str] = None  # JSON
    duration_ms: Optional[int] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    # Relationships
    job: Optional[CodeJob] = Relationship(back_populates="trace_logs")
    session: Optional[CodeSessionDB] = Relationship(back_populates="trace_logs")


class ArtifactDB(SQLModel, table=True):
    """File artifact index."""
    __tablename__ = "artifacts"
    
    id: str = Field(primary_key=True)
    project_id: Optional[str] = Field(default=None, index=True)
    session_id: Optional[str] = Field(default=None, index=True)
    job_id: Optional[str] = Field(default=None, foreign_key="code_jobs.id", index=True)
    kind: ArtifactKind
    path: str  # Relative path from artifacts dir
    filename: str
    size_bytes: int = 0
    checksum: Optional[str] = None
    mime_type: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    # Relationships
    job: Optional[CodeJob] = Relationship(back_populates="artifacts")


# ============ Phase A: Code Project (GitHub-like browsing) Models ============

class GenerationStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class CodeProjectV2Create(SQLModel):
    """Create schema for CodeProjectV2."""
    title: str
    description: Optional[str] = None
    language: Optional[str] = None
    framework: Optional[str] = None
    license: Optional[str] = None
    source_idea_session_id: Optional[str] = None
    source_candidate_id: Optional[str] = None


class CodeProjectV2(SQLModel, table=True):
    """Project-grade code project with multi-file repo storage."""
    __tablename__ = "code_projects_v2"

    id: str = Field(primary_key=True)
    title: str = Field(index=True)
    description: Optional[str] = Field(default=None, sa_column=Column(Text))
    language: Optional[str] = None
    framework: Optional[str] = None
    license: Optional[str] = None
    source_idea_session_id: Optional[str] = Field(default=None, index=True)
    source_candidate_id: Optional[str] = Field(default=None, index=True)
    root_storage_path: Optional[str] = None
    repo_schema_version: int = 1
    file_count: int = 0
    total_size_bytes: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Relationships
    files: List["CodeProjectFile"] = Relationship(back_populates="project")
    generations: List["CodeProjectGeneration"] = Relationship(back_populates="project")
    exports: List["CodeProjectExport"] = Relationship(back_populates="project")


class CodeProjectFileCreate(SQLModel):
    """Create schema for CodeProjectFile."""
    project_id: str
    path: str
    is_dir: bool = False
    size: int = 0
    sha256: Optional[str] = None


class CodeProjectFile(SQLModel, table=True):
    """Indexed file entry within a code project."""
    __tablename__ = "code_project_files"

    id: str = Field(primary_key=True)
    project_id: str = Field(foreign_key="code_projects_v2.id", index=True)
    path: str = Field(index=True)
    is_dir: bool = False
    size: int = 0
    sha256: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Relationships
    project: Optional[CodeProjectV2] = Relationship(back_populates="files")


class CodeProjectGenerationCreate(SQLModel):
    """Create schema for CodeProjectGeneration."""
    project_id: str
    provider_name: Optional[str] = None
    model: Optional[str] = None
    prompt_hash: Optional[str] = None
    status: GenerationStatus = GenerationStatus.PENDING
    notes: Optional[str] = None


class CodeProjectGeneration(SQLModel, table=True):
    """Record of a generation run for a code project."""
    __tablename__ = "code_project_generations"

    id: str = Field(primary_key=True)
    project_id: str = Field(foreign_key="code_projects_v2.id", index=True)
    provider_name: Optional[str] = None
    model: Optional[str] = None
    prompt_hash: Optional[str] = None
    status: GenerationStatus = Field(default=GenerationStatus.PENDING)
    notes: Optional[str] = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Relationships
    project: Optional[CodeProjectV2] = Relationship(back_populates="generations")


class CodeProjectExportCreate(SQLModel):
    """Create schema for CodeProjectExport."""
    project_id: str
    kind: str = "zip"
    file_path: str = ""
    size: int = 0
    sha256: Optional[str] = None


class CodeProjectExport(SQLModel, table=True):
    """Export record (e.g. zip download) for a code project."""
    __tablename__ = "code_project_exports"

    id: str = Field(primary_key=True)
    project_id: str = Field(foreign_key="code_projects_v2.id", index=True)
    kind: str = "zip"
    file_path: str = ""
    size: int = 0
    sha256: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Relationships
    project: Optional[CodeProjectV2] = Relationship(back_populates="exports")


# ============ Autonomous Code Agent Models ============

class AgentRunStatus(str, enum.Enum):
    """Status of an autonomous agent run."""
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    MAX_ITERATIONS = "max_iterations"
    ERROR = "error"


class AgentRunCreate(SQLModel):
    """Create schema for AgentRunDB."""
    project_id: str
    goal: Optional[str] = None
    language: str = "python"
    command: Optional[str] = None
    backend: Optional[str] = None
    max_iterations: int = 3
    execution_timeout: int = 300
    provider_name: Optional[str] = None
    model: Optional[str] = None


class AgentRunDB(SQLModel, table=True):
    """Persistent record of an autonomous agent execution run."""
    __tablename__ = "code_agent_runs"

    id: str = Field(primary_key=True)
    project_id: str = Field(foreign_key="code_projects_v2.id", index=True)
    goal: Optional[str] = Field(default=None, sa_column=Column(Text))
    language: str = "python"
    command: Optional[str] = None
    backend: Optional[str] = None
    status: str = Field(default="running")
    iterations: int = 0
    repairs_applied: int = 0
    trace_path: Optional[str] = None
    events_json: Optional[str] = Field(default=None, sa_column=Column(Text))
    summary: Optional[str] = Field(default=None, sa_column=Column(Text))
    error: Optional[str] = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None

    # Relationship
    project: Optional[CodeProjectV2] = Relationship()
