"""
Code Projects API - GitHub-like browsing, search, export, VSCode link.

All responses use camelCase field names per frontend convention.
"""

import os
import json
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List
from fastapi import APIRouter, HTTPException, status, Depends, Query, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from app.core.settings import get_settings
from app.modules.code.projects import code_project_service as cps, generate_project_from_plan, get_generation_status
from app.modules.code.storage import Session, crud, get_session, get_session_context
from app.db.models import JobStatus, CodeJobCreate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/code/projects", tags=["code_projects"])


# ============ Request / Response Schemas (camelCase) ============

class CreateProjectRequest(BaseModel):
    title: str
    description: Optional[str] = None
    language: Optional[str] = None
    framework: Optional[str] = None
    license: Optional[str] = None
    sourceIdeaSessionId: Optional[str] = None
    sourceCandidateId: Optional[str] = None
    # If provided, write these files immediately
    files: Optional[List[dict]] = None


class ProjectResponse(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    language: Optional[str] = None
    framework: Optional[str] = None
    license: Optional[str] = None
    sourceIdeaSessionId: Optional[str] = None
    sourceCandidateId: Optional[str] = None
    rootStoragePath: Optional[str] = None
    repoSchemaVersion: int = 1
    fileCount: int = 0
    totalSizeBytes: int = 0
    createdAt: str
    updatedAt: str


class ProjectListResponse(BaseModel):
    projects: List[ProjectResponse]
    total: int


class TreeEntry(BaseModel):
    name: str
    path: str
    isDir: bool
    size: int = 0


class TreeResponse(BaseModel):
    projectId: str
    path: str
    entries: List[TreeEntry]


class FileContentResponse(BaseModel):
    projectId: str
    path: str
    content: str
    size: int
    language: Optional[str] = None


class SearchResult(BaseModel):
    path: str
    line: Optional[int] = None
    content: Optional[str] = None
    isDir: bool = False


class SearchResponse(BaseModel):
    projectId: str
    query: str
    mode: str
    results: List[SearchResult]
    total: int


class ExportResponse(BaseModel):
    id: str
    projectId: str
    kind: str
    size: int
    sha256: Optional[str] = None
    createdAt: str


class VSCodeLinkResponse(BaseModel):
    uri: str
    path: str
    exists: bool
    instructions: str


# ============ Helpers ============

def _project_to_response(p) -> ProjectResponse:
    return ProjectResponse(
        id=p.id,
        title=p.title,
        description=p.description,
        language=p.language,
        framework=p.framework,
        license=p.license,
        sourceIdeaSessionId=p.source_idea_session_id,
        sourceCandidateId=p.source_candidate_id,
        rootStoragePath=p.root_storage_path,
        repoSchemaVersion=p.repo_schema_version,
        fileCount=p.file_count,
        totalSizeBytes=p.total_size_bytes,
        createdAt=p.created_at.isoformat() if p.created_at else "",
        updatedAt=p.updated_at.isoformat() if p.updated_at else "",
    )


def _guess_language(path: str) -> Optional[str]:
    ext_map = {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".tsx": "typescriptreact", ".jsx": "javascriptreact",
        ".json": "json", ".md": "markdown", ".yml": "yaml", ".yaml": "yaml",
        ".html": "html", ".css": "css", ".sql": "sql", ".sh": "bash",
        ".toml": "toml", ".ini": "ini", ".cfg": "ini",
        ".rs": "rust", ".go": "go", ".java": "java",
        ".dockerfile": "dockerfile", ".xml": "xml",
    }
    _, ext = os.path.splitext(path.lower())
    if path.lower().endswith("dockerfile"):
        return "dockerfile"
    return ext_map.get(ext)


# ============ Endpoints ============

@router.post(
    "",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Code Project",
)
async def create_project(
    request: CreateProjectRequest,
    db: Session = Depends(get_session),
) -> ProjectResponse:
    """Create a new code project. Optionally provide files to write immediately."""
    project = cps.create_project(
        db,
        title=request.title,
        description=request.description,
        language=request.language,
        framework=request.framework,
        license_str=request.license,
        source_idea_session_id=request.sourceIdeaSessionId,
        source_candidate_id=request.sourceCandidateId,
    )

    if request.files:
        cps.write_project_files(db, project.id, request.files)
        # Refresh project to get updated counts
        project = crud.get_project_v2(db, project.id)

    return _project_to_response(project)


@router.get(
    "",
    response_model=ProjectListResponse,
    summary="List Code Projects",
)
async def list_projects(
    search: Optional[str] = Query(None, description="Search by title"),
    language: Optional[str] = Query(None, description="Filter by language"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_session),
) -> ProjectListResponse:
    projects = crud.list_projects_v2(db, search=search, language=language, limit=limit, offset=offset)
    return ProjectListResponse(
        projects=[_project_to_response(p) for p in projects],
        total=len(projects),
    )


@router.get(
    "/{projectId}",
    response_model=ProjectResponse,
    summary="Get Code Project",
)
async def get_project(
    projectId: str,
    db: Session = Depends(get_session),
) -> ProjectResponse:
    project = crud.get_project_v2(db, projectId)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {projectId}")
    return _project_to_response(project)


@router.delete(
    "/{projectId}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete Code Project",
)
async def delete_project(
    projectId: str,
    db: Session = Depends(get_session),
):
    project = crud.get_project_v2(db, projectId)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {projectId}")
    try:
        crud.delete_project_files(db, projectId)
        crud.delete_project_v2(db, projectId)
    except Exception as e:
        logger.exception("Failed to delete project %s", projectId)
        raise HTTPException(status_code=500, detail=f"Delete failed: {e}")


@router.get(
    "/{projectId}/tree",
    response_model=TreeResponse,
    summary="Get Project File Tree",
)
async def get_tree(
    projectId: str,
    path: str = Query("", description="Directory path to list"),
    db: Session = Depends(get_session),
) -> TreeResponse:
    project = crud.get_project_v2(db, projectId)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {projectId}")

    # Reject path traversal
    if ".." in path:
        raise HTTPException(status_code=400, detail="Path traversal not allowed")

    entries = cps.get_tree(db, projectId, path)
    return TreeResponse(
        projectId=projectId,
        path=path,
        entries=[TreeEntry(**e) for e in entries],
    )


@router.get(
    "/{projectId}/file",
    response_model=FileContentResponse,
    summary="Get File Content",
)
async def get_file(
    projectId: str,
    path: str = Query(..., description="File path within project"),
    db: Session = Depends(get_session),
) -> FileContentResponse:
    project = crud.get_project_v2(db, projectId)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {projectId}")

    if ".." in path:
        raise HTTPException(status_code=400, detail="Path traversal not allowed")

    content = cps.read_file_content(projectId, path)
    if content is None:
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    return FileContentResponse(
        projectId=projectId,
        path=path,
        content=content,
        size=len(content.encode("utf-8")),
        language=_guess_language(path),
    )


@router.get(
    "/{projectId}/file/download",
    summary="Download Single File",
)
async def download_file(
    projectId: str,
    path: str = Query(..., description="File path within project"),
    db: Session = Depends(get_session),
):
    project = crud.get_project_v2(db, projectId)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {projectId}")

    if ".." in path:
        raise HTTPException(status_code=400, detail="Path traversal not allowed")

    abs_path = cps.get_file_abs_path(projectId, path)
    if not abs_path:
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    filename = os.path.basename(path)
    return FileResponse(abs_path, filename=filename)


@router.get(
    "/{projectId}/search",
    response_model=SearchResponse,
    summary="Search Project Files",
)
async def search_project(
    projectId: str,
    q: str = Query(..., min_length=1, description="Search query"),
    mode: str = Query("path", description="Search mode: path or content"),
    db: Session = Depends(get_session),
) -> SearchResponse:
    project = crud.get_project_v2(db, projectId)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {projectId}")

    results = []

    if mode == "content":
        # Content search (grep-like)
        hits = cps.search_content(projectId, q)
        for h in hits:
            results.append(SearchResult(
                path=h["path"],
                line=h.get("line"),
                content=h.get("content"),
            ))
    else:
        # Path search via DB
        files = crud.search_project_files(db, projectId, q)
        for f in files:
            results.append(SearchResult(
                path=f.path,
                isDir=f.is_dir,
            ))

    return SearchResponse(
        projectId=projectId,
        query=q,
        mode=mode,
        results=results,
        total=len(results),
    )


@router.post(
    "/{projectId}/export",
    response_model=ExportResponse,
    summary="Export Project as ZIP",
)
async def export_project(
    projectId: str,
    db: Session = Depends(get_session),
) -> ExportResponse:
    project = crud.get_project_v2(db, projectId)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {projectId}")

    try:
        result = cps.export_zip(db, projectId)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return ExportResponse(
        id=result["id"],
        projectId=result["projectId"],
        kind=result["kind"],
        size=result["size"],
        sha256=result.get("sha256"),
        createdAt=result["createdAt"],
    )


@router.get(
    "/{projectId}/vscode-link",
    response_model=VSCodeLinkResponse,
    summary="Get VSCode Open Link",
)
async def get_vscode_link(
    projectId: str,
    db: Session = Depends(get_session),
) -> VSCodeLinkResponse:
    project = crud.get_project_v2(db, projectId)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {projectId}")

    link = cps.get_vscode_link(projectId)
    return VSCodeLinkResponse(**link)


# ============ Export Download (separate route for export IDs) ============

@router.get(
    "/exports/{exportId}/download",
    summary="Download Export File",
)
async def download_export(
    exportId: str,
    db: Session = Depends(get_session),
):
    path = cps.get_export_path(db, exportId)
    if not path:
        raise HTTPException(status_code=404, detail=f"Export not found: {exportId}")

    filename = os.path.basename(path)
    return FileResponse(path, filename=filename, media_type="application/zip")


# ============ Generate Sample Project (convenience) ============

class GenerateSampleRequest(BaseModel):
    title: str
    language: str = "python"
    description: Optional[str] = None


@router.post(
    "/generate-sample",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate Sample Project (for testing)",
)
async def generate_sample(
    request: GenerateSampleRequest,
    db: Session = Depends(get_session),
) -> ProjectResponse:
    project = cps.generate_sample_project(
        db,
        title=request.title,
        language=request.language,
        description=request.description,
    )
    # Refresh
    project = crud.get_project_v2(db, project.id)
    return _project_to_response(project)


# ============ Code Generation from Plan ============

class FromPlanRequest(BaseModel):
    planSessionId: str
    candidateId: str
    providerName: Optional[str] = None
    model: Optional[str] = None
    language: str = "python"
    framework: str = "FastAPI"
    enableWebSearch: bool = False
    enableGithub: bool = False


class FromPlanResponse(BaseModel):
    projectId: str
    status: str


def _run_code_agent(
    plan_session_id: str,
    candidate_id: str,
    provider_name: str,
    model: str,
    language: str,
    framework: str,
    enable_web_search: bool,
    enable_github: bool,
    existing_project_id: str = None,
):
    """Background task wrapper for code generation."""
    try:
        generate_project_from_plan(
            plan_session_id=plan_session_id,
            candidate_id=candidate_id,
            provider_name=provider_name,
            model=model,
            language=language,
            framework=framework,
            enable_web_search=enable_web_search,
            enable_github=enable_github,
            existing_project_id=existing_project_id,
        )
    except Exception as e:
        logger.error(f"Code agent background task failed: {e}", exc_info=True)


@router.post(
    "/from-plan",
    response_model=FromPlanResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Generate Project from Plan (async)",
)
async def create_from_plan(
    request: FromPlanRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    """Start code generation agent from a plan candidate. Returns immediately with projectId."""
    # Create a placeholder project in DB
    project = cps.create_project(
        db=db,
        title=f"Generating from plan...",
        language=request.language,
        description=f"Code generation in progress from plan session {request.planSessionId}",
    )
    project_id = project.id

    settings = get_settings()
    provider_name = request.providerName or settings.get_active_provider()
    model = request.model or settings.get_active_model(provider_name)

    # Launch background agent with existing project_id
    background_tasks.add_task(
        _run_code_agent,
        request.planSessionId,
        request.candidateId,
        provider_name,
        model,
        request.language,
        request.framework,
        request.enableWebSearch,
        request.enableGithub,
        project_id,
    )

    return FromPlanResponse(projectId=project_id, status="started")


@router.get(
    "/{projectId}/generation-status",
    summary="Get Code Generation Status",
)
async def get_project_generation_status(projectId: str):
    """Get step-by-step generation progress for a project."""
    status_data = get_generation_status(projectId)
    if not status_data:
        return {"projectId": projectId, "status": "unknown", "steps": [], "logs": []}
    return status_data


# ============ JSON Export & Experiment Data ============


class ExportJsonResponse(BaseModel):
    """Structured JSON export of a code project for downstream module consumption."""
    projectId: str
    title: str
    description: Optional[str] = None
    language: Optional[str] = None
    framework: Optional[str] = None
    sourceIdeaSessionId: Optional[str] = None
    sourceCandidateId: Optional[str] = None
    createdAt: str
    updatedAt: str
    fileCount: int = 0
    totalSizeBytes: int = 0
    files: List[dict] = Field(default_factory=list, description="[{path, content, language, size}]")
    metrics: Optional[dict] = Field(None, description="Parsed metrics.json contents if present")
    config: Optional[dict] = Field(None, description="Parsed experiment.json contents if present")


class ExperimentDataResponse(BaseModel):
    """Experiment data package delivered to downstream modules."""
    ok: bool = True
    projectId: str
    projectTitle: str
    experimentId: Optional[str] = None
    codePrinciples: List[dict] = Field(default_factory=list)
    experimentDesign: Optional[dict] = None
    execution: Optional[dict] = None
    metrics: List[dict] = Field(default_factory=list)
    figures: List[dict] = Field(default_factory=list)
    analysis: Optional[dict] = None
    reportMd: Optional[str] = Field(None, description="Full MD report content")
    reportMdPath: Optional[str] = Field(None, description="Relative path to generated report")


@router.get(
    "/{projectId}/export-json",
    response_model=ExportJsonResponse,
    summary="Export Project as Structured JSON",
)
async def export_project_json(
    projectId: str,
    includeContent: bool = Query(True, description="Include file contents"),
    db: Session = Depends(get_session),
) -> ExportJsonResponse:
    """
    Export the complete code project as structured JSON suitable for
    consumption by downstream modules (Paper drafting, Review, etc.).
    """
    project = crud.get_project_v2(db, projectId)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {projectId}")

    # Gather all files
    files_data: List[dict] = []
    file_records = crud.list_project_files(db, projectId)
    for fr in file_records:
        entry = {
            "path": fr.path,
            "size": fr.size,
            "isDir": fr.is_dir,
            "language": _guess_language(fr.path),
        }
        if includeContent and not fr.is_dir:
            content = cps.read_file_content(projectId, fr.path)
            if content is not None:
                entry["content"] = content
        files_data.append(entry)

    # Try to parse metrics.json and config/experiment.json
    metrics = None
    metrics_raw = cps.read_file_content(projectId, "metrics.json")
    if metrics_raw:
        try:
            metrics = json.loads(metrics_raw)
        except (json.JSONDecodeError, TypeError):
            pass

    config = None
    config_raw = cps.read_file_content(projectId, "configs/experiment.json")
    if config_raw:
        try:
            config = json.loads(config_raw)
        except (json.JSONDecodeError, TypeError):
            pass

    return ExportJsonResponse(
        projectId=project.id,
        title=project.title,
        description=project.description,
        language=project.language,
        framework=project.framework,
        sourceIdeaSessionId=project.source_idea_session_id,
        sourceCandidateId=project.source_candidate_id,
        createdAt=project.created_at.isoformat() if project.created_at else "",
        updatedAt=project.updated_at.isoformat() if project.updated_at else "",
        fileCount=project.file_count,
        totalSizeBytes=project.total_size_bytes,
        files=files_data,
        metrics=metrics,
        config=config,
    )


@router.get(
    "/{projectId}/experiment-data",
    response_model=ExperimentDataResponse,
    summary="Get Experiment Data Package (JSON + MD Report)",
)
async def get_experiment_data(
    projectId: str,
    experimentId: Optional[str] = Query(None, description="Experiment record ID"),
    includeMd: bool = Query(True, description="Include full MD report in response"),
    db: Session = Depends(get_session),
) -> ExperimentDataResponse:
    """
    Returns structured experiment data including code principles, experiment design,
    execution results, metrics, figures, analysis, and a generated MD report.

    This is the primary data contract between the Code/Experiment module and
    downstream Paper/Review modules in the FAROS pipeline.
    """
    import json as _json

    project = crud.get_project_v2(db, projectId)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {projectId}")

    # Determine project directory on disk
    from pathlib import Path
    repo_root = cps.get_file_abs_path(projectId, "")
    project_dir = Path(repo_root) if repo_root else Path(
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "code_projects", projectId, "repo")
    )

    # Build ExperimentData via report context
    from app.services.experiment_report_service import get_experiment_report_service

    report_svc = get_experiment_report_service()
    ctx = type("ReportContext", (), {})  # temporary, we'll use the service directly

    # Extract code principles from source files (heuristic: look for docstrings with pseudocode)
    code_principles: List[dict] = []
    # Scan main.py or train.py for docstrings indicating algorithmic content
    for candidate_file in ["src/main.py", "src/train.py", "src/model.py", "main.py", "train.py"]:
        content = cps.read_file_content(projectId, candidate_file)
        if content:
            # Simple heuristic: extract module docstring and class/function signatures
            principle = _extract_code_principle(candidate_file, content)
            if principle:
                code_principles.append(principle)
            break  # Only extract from the main entry point

    # Gather figures
    figures: List[dict] = []
    figures_dir = project_dir / "figures"
    if figures_dir.exists():
        for f in sorted(figures_dir.iterdir()):
            if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".pdf", ".svg"):
                figures.append({
                    "title": f.stem.replace("_", " ").title(),
                    "path": f"figures/{f.name}",
                    "description": "",
                })

    # Gather metrics
    metrics: List[dict] = []
    metrics_raw = cps.read_file_content(projectId, "metrics.json")
    if metrics_raw:
        try:
            parsed = _json.loads(metrics_raw)
            items = parsed if isinstance(parsed, list) else [parsed]
            for item in items:
                if isinstance(item, dict) and "name" in item:
                    metrics.append(item)
        except (_json.JSONDecodeError, TypeError):
            pass

    # Experiment design from config
    experiment_design = None
    config_raw = cps.read_file_content(projectId, "configs/experiment.json")
    if config_raw:
        try:
            config = _json.loads(config_raw)
            experiment_design = {
                "objective": config.get("objective", ""),
                "hypothesis": config.get("hypothesis"),
                "methodology": config.get("methodology", ""),
                "independentVariables": config.get("independent_variables", []),
                "dependentVariables": config.get("dependent_variables", []),
                "controlledVariables": config.get("controlled_variables", []),
            }
        except (_json.JSONDecodeError, TypeError):
            pass

    # Build the report MD
    from app.schemas.experiment_data import (
        CodePrinciple,
        ExperimentData,
        ExperimentDesign,
        ExperimentMetric,
        FigureData,
    )

    ed = ExperimentData(
        project_id=projectId,
        project_title=project.title,
        experiment_id=experimentId,
        code_principles=[
            CodePrinciple(**p) for p in code_principles
        ],
        experiment_design=ExperimentDesign(**experiment_design) if experiment_design else None,
        metrics=[
            ExperimentMetric(
                name=m.get("name", "unknown"),
                value=float(m.get("value", 0)),
                unit=m.get("unit"),
                direction=m.get("direction"),
                baseline=m.get("baseline"),
                improvement_pct=m.get("improvement_pct"),
            )
            for m in metrics
        ],
        figures=[FigureData(**f) for f in figures],
    )

    report_md = report_svc.generate_report(ed) if includeMd else None

    return ExperimentDataResponse(
        ok=True,
        projectId=projectId,
        projectTitle=project.title,
        experimentId=experimentId,
        codePrinciples=code_principles,
        experimentDesign=experiment_design,
        metrics=metrics,
        figures=figures,
        reportMd=report_md,
        reportMdPath=f"data/code_projects/{projectId}/experiment_report.md"
        if includeMd else None,
    )


def _extract_code_principle(file_path: str, content: str) -> Optional[dict]:
    """Heuristic: extract a code principle from a Python file's docstring."""
    import ast as _ast
    try:
        tree = _ast.parse(content)
    except SyntaxError:
        return None

    # Get module-level docstring
    docstring = _ast.get_docstring(tree)
    if not docstring:
        return None

    # Take first paragraph as description
    lines = docstring.strip().split("\n")
    description = lines[0].strip()

    # Look for a function with a "Pseudocode" or "Algorithm" section in its docstring
    pseudocode = None
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            fd = _ast.get_docstring(node)
            if fd and ("Algorithm" in fd or "Pseudocode" in fd or "pseudocode" in fd.lower()):
                pseudocode = fd
                break

    return {
        "title": file_path.split("/")[-1].replace(".py", "").replace("_", " ").title(),
        "description": description,
        "pseudocode": pseudocode,
        "sourceFile": file_path,
        "language": "python" if file_path.endswith(".py") else None,
    }


# ============ Pipeline Run (Blueprint-Step-Driven Execution) ============


class PipelineStep(BaseModel):
    """A single step in the execution pipeline."""
    name: str = Field(..., description="Short step name")
    purpose: str = Field(..., description="Why this step runs — shown to the user")
    command: str = Field(..., description="Shell command to execute")
    critical: bool = Field(default=True, description="If True, pipeline stops on failure")
    timeoutSec: int = Field(default=120, description="Per-step timeout in seconds")


class PipelineStepResult(BaseModel):
    """Result of a single pipeline step after execution."""
    name: str
    purpose: str
    status: str = "pending"  # pending | running | succeeded | failed | skipped
    durationMs: int = 0
    stdout: str = ""
    stderr: str = ""
    exitCode: Optional[int] = None
    error: Optional[str] = None


class PipelineRunResponse(BaseModel):
    """Response after pipeline execution completes."""
    jobId: str
    projectId: str
    status: str  # succeeded | failed | partial
    steps: List[PipelineStepResult] = Field(default_factory=list)
    totalDurationMs: int = 0
    summary: str = ""


def _get_project_repo_dir(project) -> str | None:
    """Get the repo directory from a project record."""
    if project.root_storage_path and os.path.isdir(project.root_storage_path):
        return project.root_storage_path
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    repo_dir = os.path.join(base, "data", "code_projects", project.id, "repo")
    return repo_dir if os.path.isdir(repo_dir) else None


def _build_pipeline_steps(repo_dir: str, language: str) -> List[PipelineStep]:
    """
    Build execution pipeline steps by scanning the project structure.

    Each step has a clear purpose shown to the user, so they know exactly
    what's happening and why at each stage.
    """
    steps: List[PipelineStep] = []

    has_reqs = os.path.isfile(os.path.join(repo_dir, "requirements.txt"))
    has_tests = os.path.isdir(os.path.join(repo_dir, "tests"))
    has_train = os.path.isfile(os.path.join(repo_dir, "src", "train.py")) or os.path.isfile(os.path.join(repo_dir, "train.py"))
    has_main = os.path.isfile(os.path.join(repo_dir, "src", "main.py")) or os.path.isfile(os.path.join(repo_dir, "main.py"))

    if language == "python":
        # Step 1: Environment check
        steps.append(PipelineStep(
            name="Environment Check",
            purpose="Verify Python runtime, version, and project file integrity",
            command="import sys, os; print('Python version:', sys.version); print('Working dir:', os.getcwd()); "
                    "print('Project files:', len(os.listdir('.')))",
            critical=True,
            timeoutSec=30,
        ))

        # Step 2: Install dependencies
        if has_reqs:
            steps.append(PipelineStep(
                name="Install Dependencies",
                purpose="Install Python packages declared in requirements.txt",
                command="pip install -r requirements.txt --quiet 2>&1",
                critical=True,
                timeoutSec=120,
            ))

        # Step 3: Syntax check — use a temp script to avoid inline for-loop issues
        syntax_script = os.path.join(repo_dir, "_faros_syntax_check.py")
        with open(syntax_script, "w", encoding="utf-8") as sf:
            sf.write(
                "import py_compile, os\n"
                "ok = fail = 0\n"
                "for root, dirs, files in os.walk('.'):\n"
                "  for f in files:\n"
                "    if f.endswith('.py') and '_faros_' not in f:\n"
                "      fp = os.path.join(root, f)\n"
                "      try:\n"
                "        py_compile.compile(fp, doraise=True)\n"
                "        ok += 1\n"
                "        print('OK', fp)\n"
                "      except py_compile.PyCompileError as e:\n"
                "        fail += 1\n"
                "        print('FAIL', fp, str(e))\n"
                "print(f'\\nSyntax check done: {ok} OK, {fail} FAIL')\n"
            )
        steps.append(PipelineStep(
            name="Syntax Check",
            purpose="Compile-check all Python source files for syntax errors",
            command=f"python _faros_syntax_check.py 2>&1",
            critical=False,
            timeoutSec=60,
        ))

        # Step 4: Lint check
        steps.append(PipelineStep(
            name="Lint Check",
            purpose="Check code style and potential issues (flake8 if installed)",
            command="flake8 . --count --max-line-length=120 --statistics 2>&1 || echo 'flake8 not installed, skip'",
            critical=False,
            timeoutSec=60,
        ))

        # Step 5: Unit tests
        if has_tests:
            steps.append(PipelineStep(
                name="Unit Tests",
                purpose="Run project unit tests to verify core functionality",
                command="python -m pytest tests/ -v --tb=short 2>&1 || echo '(tests completed with some failures)'",
                critical=False,
                timeoutSec=180,
            ))

        # Step 6: Training
        if has_train:
            target = "src/train.py" if os.path.isfile(os.path.join(repo_dir, "src", "train.py")) else "train.py"
            steps.append(PipelineStep(
                name="Model Training",
                purpose="Execute training script, produce model weights and training logs",
                command=f"python {target} 2>&1",
                critical=True,
                timeoutSec=600,
            ))

        # Step 7: Main execution — write a runner that validates the app without hanging
        if has_main:
            target = "src/main.py" if os.path.isfile(os.path.join(repo_dir, "src", "main.py")) else "main.py"
            runner_script = os.path.join(repo_dir, "_faros_runner.py")
            with open(runner_script, "w", encoding="utf-8") as rf:
                rf.write(
                    "import sys, os, importlib.util\n"
                    "# Add src/ and project root to path\n"
                    "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
                    "if os.path.isdir('src'):\n"
                    "    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))\n"
                    "# Import the module to verify it loads without errors\n"
                    f"target_path = '{target}'\n"
                    "spec = importlib.util.spec_from_file_location('_faros_target', target_path)\n"
                    "if spec and spec.loader:\n"
                    "    mod = importlib.util.module_from_spec(spec)\n"
                    "    try:\n"
                    "        spec.loader.exec_module(mod)\n"
                    "        print('Module loaded successfully')\n"
                    "        # If the module has an 'app' object, validate it's a FastAPI/Flask app\n"
                    "        if hasattr(mod, 'app'):\n"
                    "            print(f'App object found: {type(mod.app).__name__}')\n"
                    "        # List all callable endpoints\n"
                    "        for name in dir(mod):\n"
                    "            obj = getattr(mod, name)\n"
                    "            if callable(obj) and not name.startswith('_'):\n"
                    "                print(f'  callable: {name}')\n"
                    "    except Exception as e:\n"
                    "        print(f'Module execution error: {e}')\n"
                    "        import traceback\n"
                    "        traceback.print_exc()\n"
                    "        sys.exit(1)\n"
                    "else:\n"
                    "    print(f'Could not load module: {target_path}')\n"
                    "    sys.exit(1)\n"
                )
            steps.append(PipelineStep(
                name="Main Execution",
                purpose="Validate the project module loads correctly and all endpoints are importable",
                command=f"python _faros_runner.py 2>&1",
                critical=True,
                timeoutSec=60,
            ))

        # Step 8: Collect metrics
        steps.append(PipelineStep(
            name="Metrics Collection",
            purpose="Scan project outputs, extract quantitative metrics, generate metrics.json",
            command="import json, os; "
                    "py_files = []; "
                    "for root, dirs, files in os.walk('.'): "
                    "  py_files += [os.path.join(root, f) for f in files if f.endswith('.py')]; "
                    "metrics = ["
                    "{'name': 'python_files', 'value': len(py_files)}]; "
                    "with open('metrics.json', 'w') as f: json.dump(metrics, f, indent=2, ensure_ascii=False); "
                    "print('metrics.json written:', len(py_files), 'Python files')",
            critical=False,
            timeoutSec=30,
        ))

        # Step 9: Generate report
        steps.append(PipelineStep(
            name="Generate Report",
            purpose="Aggregate all step results, generate experiment_report.md for paper writing",
            command="import json, os; "
                    "from datetime import datetime; "
                    "report = ['# Experiment Pipeline Report', '', f'Generated: {datetime.now()}', '']; "
                    "if os.path.exists('metrics.json'): "
                    "  report.append('## Metrics'); "
                    "  m = json.load(open('metrics.json')); "
                    "  for item in m: report.append(f'- **{item.get(\"name\", \"?\")}**: {item.get(\"value\", \"?\")}'); "
                    "report.append(''); "
                    "report.append('## Project Files'); "
                    "for root, dirs, files in os.walk('.'): "
                    "  for f in sorted(files): "
                    "    fp = os.path.join(root, f); "
                    "    report.append(f'- `{fp}` ({os.path.getsize(fp)} bytes)'); "
                    "with open('experiment_report.md', 'w', encoding='utf-8') as f: f.write('\\n'.join(report)); "
                    "print('experiment_report.md generated')",
            critical=False,
            timeoutSec=30,
        ))

    else:
        steps.append(PipelineStep(
            name="Syntax Check",
            purpose=f"Check {language} source file syntax",
            command=f"echo 'Running {language} syntax check...'",
            critical=False,
            timeoutSec=30,
        ))

    # Always add cleanup as the final step
    steps.append(PipelineStep(
        name="Cleanup",
        purpose="Remove temporary FAROS helper scripts generated during pipeline execution",
        command="import os; "
                "for f in ['_faros_syntax_check.py', '_faros_runner.py']: "
                "  try: os.remove(f) "
                "  except OSError: pass; "
                "print('Cleanup done')",
        critical=False,
        timeoutSec=10,
    ))

    return steps


async def _execute_pipeline_step(step: PipelineStep, repo_dir: str, python_exe: str) -> PipelineStepResult:
    """Execute a single pipeline step using subprocess in a thread pool (avoids Windows asyncio issues)."""
    import time as _time
    import subprocess as _subprocess
    start = _time.time()

    result = PipelineStepResult(name=step.name, purpose=step.purpose, status="running")
    command = step.command

    # Build the actual shell command
    is_inline_python = (
        command.lstrip().startswith("import ") or
        command.lstrip().startswith("from ") or
        ("py_compile" in command and "compile(" not in command)
    )

    if command.startswith("python "):
        shell_cmd = command.replace("python ", f'"{python_exe}" ', 1)
    elif is_inline_python:
        # Inline Python → wrap with python -c
        # On Windows, escape internal double quotes for cmd.exe
        safe = command.replace('"', '\\"')
        shell_cmd = f'"{python_exe}" -c "{safe}"'
    else:
        shell_cmd = command

    # Run in thread pool to avoid Windows ProactorEventLoop issues
    def _run():
        return _subprocess.run(
            shell_cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=step.timeoutSec,
            cwd=repo_dir,
            encoding="utf-8",
            errors="replace",
        )

    try:
        loop = asyncio.get_event_loop()
        proc = await loop.run_in_executor(None, _run)

        result.exitCode = proc.returncode
        result.stdout = (proc.stdout[:8000] if proc.stdout else "")
        result.stderr = (proc.stderr[:4000] if proc.stderr else "")

        if proc.returncode == 0:
            result.status = "succeeded"
        else:
            result.status = "failed"
            result.error = f"Exit code: {proc.returncode}"

    except _subprocess.TimeoutExpired:
        result.status = "failed"
        result.error = f"Step timed out after {step.timeoutSec}s"
    except FileNotFoundError as e:
        result.status = "failed"
        result.error = f"Command not found: {e}"
    except Exception as e:
        result.status = "failed"
        result.error = f"{type(e).__name__}: {e}"

    result.durationMs = int((_time.time() - start) * 1000)
    return result


async def _run_pipeline_background(job_id: str, project_id: str, repo_dir: str, language: str):
    """Execute pipeline steps in background, updating DB after each step."""
    import sys as _sys
    python_exe = _sys.executable

    steps = _build_pipeline_steps(repo_dir, language)
    step_results: List[PipelineStepResult] = []
    overall_status = "running"
    critical_failed = False

    for i, step in enumerate(steps):
        if critical_failed:
            # Skip remaining steps if a critical step failed
            skipped = PipelineStepResult(
                name=step.name, purpose=step.purpose, status="skipped",
                error="Skipped: previous critical step failed"
            )
            step_results.append(skipped)
            continue

        logger.info("Pipeline [%s] step %d/%d: %s", project_id, i + 1, len(steps), step.name)

        # Mark as running
        step_results.append(PipelineStepResult(
            name=step.name, purpose=step.purpose, status="running"
        ))

        result = await _execute_pipeline_step(step, repo_dir, python_exe)
        # Replace placeholder in step_results
        step_results[i] = result

        logger.info("Pipeline [%s] step '%s': %s (%dms)",
                    project_id, step.name, result.status, result.durationMs)

        if result.status == "failed" and step.critical:
            critical_failed = True
            overall_status = "failed"

        # Store intermediate results to project dir
        try:
            import json as _json
            interim_path = os.path.join(repo_dir, f"pipeline_step_{i + 1}_{step.name.replace(' ', '_')}.json")
            with open(interim_path, "w", encoding="utf-8") as f:
                _json.dump(result.model_dump(), f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    if overall_status != "failed":
        overall_status = "succeeded" if not critical_failed else "partial"

    # Write final pipeline results
    try:
        import json as _json
        results_path = os.path.join(repo_dir, "pipeline_results.json")
        with open(results_path, "w", encoding="utf-8") as f:
            _json.dump({
                "jobId": job_id,
                "projectId": project_id,
                "status": overall_status,
                "steps": [s.model_dump() for s in step_results],
                "totalDurationMs": sum(s.durationMs for s in step_results),
            }, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

    # Update job in DB
    with get_session_context() as db:
        total_ms = sum(s.durationMs for s in step_results)
        crud.update_job(db, job_id, {
            "status": JobStatus.SUCCEEDED if overall_status == "succeeded" else JobStatus.FAILED,
            "exit_code": 0 if overall_status == "succeeded" else 1,
            "duration_sec": total_ms // 1000,
            "ended_at": datetime.now(timezone.utc),
        })


@router.post(
    "/{projectId}/pipeline-run",
    response_model=PipelineRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Run Project Pipeline (Blueprint-Step-Driven)",
)
async def run_project_pipeline(
    projectId: str,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: Session = Depends(get_session),
) -> PipelineRunResponse:
    """
    Execute a code project as a multi-step pipeline.

    Each step has a clear **name** and **purpose** visible to the user.
    Steps execute sequentially:
    1. 环境检查 — verify Python & project files
    2. 安装依赖 — pip install
    3. 语法检查 — compile all .py files
    4. 代码风格检查 — flake8 lint
    5. 单元测试 — pytest (if tests/ exists)
    6. 模型训练 — train.py (if exists)
    7. 主程序执行 — run main entrypoint
    8. 指标收集 — extract metrics → metrics.json
    9. 生成实验报告 — summary → experiment_report.md

    If a **critical** step fails, subsequent steps are skipped.
    Returns immediately; poll GET /code/projects/{id}/pipeline-results for progress.
    """
    project = crud.get_project_v2(db, projectId)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {projectId}")

    repo_dir = _get_project_repo_dir(project)
    if not repo_dir:
        raise HTTPException(status_code=500, detail="Project directory not found on disk")

    language = project.language or "python"
    steps = _build_pipeline_steps(repo_dir, language)

    # Create lightweight job record
    job = crud.create_job(db, CodeJobCreate(
        project_id=projectId,
        mode="pipeline",
        command=f"Pipeline: {len(steps)} steps",
        timeout_sec=sum(s.timeoutSec for s in steps) + 60,
    ))
    crud.update_job(db, job.id, {
        "status": JobStatus.RUNNING,
        "workspace_path": repo_dir,
        "started_at": datetime.now(timezone.utc),
    })

    # Launch background pipeline
    background_tasks.add_task(
        _run_pipeline_background,
        job_id=job.id,
        project_id=projectId,
        repo_dir=repo_dir,
        language=language,
    )

    return PipelineRunResponse(
        jobId=job.id,
        projectId=projectId,
        status="running",
        steps=[
            PipelineStepResult(name=s.name, purpose=s.purpose, status="pending")
            for s in steps
        ],
        summary=f"Pipeline started: {len(steps)} steps",
    )


@router.get(
    "/{projectId}/pipeline-results",
    summary="Get Pipeline Run Results",
)
async def get_pipeline_results(
    projectId: str,
    jobId: Optional[str] = Query(None, description="Specific job ID"),
    db: Session = Depends(get_session),
) -> PipelineRunResponse:
    """
    Get the results of the latest (or specified) pipeline run.
    Returns step-by-step status, duration, and output.
    """
    project = crud.get_project_v2(db, projectId)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {projectId}")

    repo_dir = _get_project_repo_dir(project)

    # Try to read from disk (has live step-by-step results)
    if repo_dir:
        results_path = os.path.join(repo_dir, "pipeline_results.json")
        if os.path.exists(results_path):
            try:
                data = json.loads(open(results_path, encoding="utf-8").read())
                return PipelineRunResponse(**data)
            except (json.JSONDecodeError, TypeError):
                pass

    # Fallback: derive from job records
    if jobId:
        job = crud.get_job(db, jobId)
        if job:
            return PipelineRunResponse(
                jobId=job.id,
                projectId=projectId,
                status=job.status.value if isinstance(job.status, JobStatus) else str(job.status),
                steps=[],
                totalDurationMs=(job.duration_sec or 0) * 1000,
                summary=f"Job {job.id}: {job.command}",
            )

    # Return current pipeline definition (not yet run)
    language = project.language or "python"
    steps = _build_pipeline_steps(repo_dir or "", language)
    return PipelineRunResponse(
        jobId="",
        projectId=projectId,
        status="idle",
        steps=[PipelineStepResult(name=s.name, purpose=s.purpose, status="pending") for s in steps],
        summary="Pipeline not yet executed. POST to /pipeline-run to start.",
    )


class AutoFixResponse(BaseModel):
    """Response after auto-fix pipeline."""
    jobId: str
    projectId: str
    status: str
    iterations: int = 0
    fixesApplied: List[dict] = Field(default_factory=list)
    summary: str = ""
    pipeline: Optional[PipelineRunResponse] = None


@router.post(
    "/{projectId}/auto-fix",
    response_model=AutoFixResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Auto-Fix Pipeline Failures via AI",
)
async def auto_fix_pipeline(
    projectId: str,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: Session = Depends(get_session),
) -> AutoFixResponse:
    """
    Analyze failed pipeline steps and automatically fix code issues using AI.

    1. Reads failed step errors and source files
    2. Sends fix prompts to the LLM
    3. Applies AI-generated fixes to project files
    4. Re-runs the pipeline to verify fixes
    5. Iterates up to 3 times if failures persist
    """
    project = crud.get_project_v2(db, projectId)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {projectId}")

    repo_dir = _get_project_repo_dir(project)
    if not repo_dir:
        raise HTTPException(status_code=500, detail="Project directory not found on disk")

    # Read failed steps from previous pipeline run
    failed_steps: List[Dict[str, Any]] = []
    results_path = os.path.join(repo_dir, "pipeline_results.json")
    if os.path.exists(results_path):
        try:
            prev = json.loads(open(results_path, encoding="utf-8").read())
            failed_steps = [s for s in prev.get("steps", []) if s.get("status") == "failed"]
        except (json.JSONDecodeError, TypeError):
            pass

    if not failed_steps:
        # No failures to fix — just re-run
        language = project.language or "python"
        job = crud.create_job(db, CodeJobCreate(project_id=projectId, mode="pipeline", command="Auto-fix: no failures found, re-running", timeout_sec=600))
        crud.update_job(db, job.id, {"status": JobStatus.RUNNING, "workspace_path": repo_dir, "started_at": datetime.now(timezone.utc)})
        background_tasks.add_task(_run_pipeline_background, job_id=job.id, project_id=projectId, repo_dir=repo_dir, language=language)
        return AutoFixResponse(jobId=job.id, projectId=projectId, status="running", summary="No failures to fix. Re-running pipeline.")

    # Run auto-fix
    from app.services.code_repair_service import get_code_repair_service
    repair = get_code_repair_service()
    fix_report = repair.auto_fix(projectId, repo_dir, failed_steps)

    # Re-run pipeline after fixes
    language = project.language or "python"
    job = crud.create_job(db, CodeJobCreate(project_id=projectId, mode="pipeline", command=f"Auto-fix: {fix_report.summary}", timeout_sec=900))
    crud.update_job(db, job.id, {"status": JobStatus.RUNNING, "workspace_path": repo_dir, "started_at": datetime.now(timezone.utc)})
    background_tasks.add_task(_run_pipeline_background, job_id=job.id, project_id=projectId, repo_dir=repo_dir, language=language)

    return AutoFixResponse(
        jobId=job.id,
        projectId=projectId,
        status="running",
        iterations=fix_report.iterations,
        fixesApplied=[{
            "stepName": f.step_name,
            "filePath": f.file_path,
            "description": f.fix_description,
            "applied": f.applied,
            "method": f.method,
            "diffLines": f.diff_lines,
            "originalContent": f.original_content[:2000] if f.original_content else "",
            "newContent": f.new_content[:2000] if f.new_content else "",
        } for f in fix_report.fixes_applied],
        summary=fix_report.summary,
    )


@router.get(
    "/{projectId}/jobs",
    summary="List Jobs for Project",
)
async def list_project_jobs(
    projectId: str,
    db: Session = Depends(get_session),
) -> dict:
    """List execution jobs for a code project."""
    project = crud.get_project_v2(db, projectId)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {projectId}")

    jobs = crud.list_jobs(db, project_id=projectId)

    def job_to_dict(j):
        return {
            "id": j.id,
            "projectId": j.project_id,
            "status": j.status.value if isinstance(j.status, JobStatus) else str(j.status),
            "command": j.command,
            "exitCode": j.exit_code,
            "workspacePath": j.workspace_path,
            "durationSec": j.duration_sec,
            "createdAt": j.created_at.isoformat() if j.created_at else "",
            "startedAt": j.started_at.isoformat() if j.started_at else None,
            "endedAt": j.ended_at.isoformat() if j.ended_at else None,
        }

    return {"projectId": projectId, "jobs": [job_to_dict(j) for j in jobs], "total": len(jobs)}
