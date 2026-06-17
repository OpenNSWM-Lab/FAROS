"""
Code Blueprint API — Project-associated experiment DAG endpoints.

GET /code/projects/{projectId}/blueprint
    Returns the experiment blueprint DAG for a project.
    If a PlanPackage exists (from Idea session), it converts it.
    Otherwise, generates a structural blueprint from project files.

GET /code/projects/{projectId}/blueprints
    List all blueprint sessions for a project.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.db import crud
from app.modules.code.storage import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/code/blueprints", tags=["code_blueprint"])


class BlueprintNode(BaseModel):
    id: str
    label: str
    stage: str = ""
    status: str = "pending"
    description: str = ""
    method: str = ""
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    result: Optional[dict] = None
    startedAt: Optional[str] = None
    finishedAt: Optional[str] = None
    duration: Optional[int] = None


class BlueprintEdge(BaseModel):
    id: str
    source: str
    target: str


class BlueprintResponse(BaseModel):
    projectId: str
    projectTitle: str
    source: str  # "plan_package" | "project_structure" | "mock"
    id: str
    title: str
    description: str = ""
    nodes: list[BlueprintNode] = Field(default_factory=list)
    edges: list[BlueprintEdge] = Field(default_factory=list)


class BlueprintSummary(BaseModel):
    id: str
    title: str
    source: str
    nodeCount: int
    createdAt: Optional[str] = None


@router.get("/{project_id}", response_model=BlueprintResponse)
async def get_project_blueprint(
    project_id: str,
    db: Session = Depends(get_session),
):
    """Get the experiment blueprint DAG for a project.

    1. If project has a PlanPackage (via Idea session), return converted DAG
    2. Otherwise, auto-generate a structural blueprint from project files
    """
    project = crud.get_project_v2(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    repo_dir = _resolve_repo_dir(project)

    # Try PlanPackage path first
    source_idea_id = getattr(project, "source_idea_session_id", None)
    if source_idea_id:
        try:
            blueprint = _load_blueprint_from_idea(source_idea_id)
            if blueprint:
                blueprint["projectId"] = project_id
                blueprint["projectTitle"] = project.title
                blueprint["source"] = "plan_package"
                return blueprint
        except Exception as exc:
            logger.warning("Failed to load blueprint from idea %s: %s", source_idea_id, exc)

    # Fallback: generate from project structure
    blueprint = _generate_structural_blueprint(project_id, project.title, repo_dir)
    blueprint["projectId"] = project_id
    blueprint["projectTitle"] = project.title
    blueprint["source"] = "project_structure"
    return blueprint


@router.get("/{project_id}/list", response_model=list[BlueprintSummary])
async def list_project_blueprints(
    project_id: str,
    db: Session = Depends(get_session),
):
    """List all blueprint sessions for a project."""
    project = crud.get_project_v2(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    summaries: list[BlueprintSummary] = []

    # Check for PlanPackage-based blueprint
    source_idea_id = getattr(project, "source_idea_session_id", None)
    if source_idea_id:
        try:
            from app.modules.platform.storage import get_plan_package_storage
            pkg_storage = get_plan_package_storage()
            # Try to find by idea session
            packages = pkg_storage.list_by_idea_session(source_idea_id)
            for pkg in (packages or []):
                summaries.append(BlueprintSummary(
                    id=pkg.get("packageId", ""),
                    title=pkg.get("researchQuestion", "Blueprint"),
                    source="plan_package",
                    nodeCount=sum(1 + len(s.get("steps", [])) for s in pkg.get("stages", [])),
                    createdAt=pkg.get("createdAt"),
                ))
        except Exception:
            pass

    # Always offer structural blueprint
    summaries.append(BlueprintSummary(
        id=f"structural_{project_id}",
        title=f"Project Structure: {project.title}",
        source="project_structure",
        nodeCount=0,
    ))

    return summaries


# ---- helpers ----

def _resolve_repo_dir(project) -> Optional[str]:
    """Resolve repo dir from a project record."""
    import os as _os

    if hasattr(project, "root_storage_path") and project.root_storage_path:
        if _os.path.isdir(project.root_storage_path):
            return project.root_storage_path
        repo = _os.path.join(project.root_storage_path, "repo")
        if _os.path.isdir(repo):
            return repo

    from app.db.engine import _DATA_DIR
    repo = _os.path.join(_DATA_DIR, "code_projects", project.id, "repo")
    if _os.path.isdir(repo):
        return repo
    proj_dir = _os.path.join(_DATA_DIR, "code_projects", project.id)
    if _os.path.isdir(proj_dir):
        return proj_dir
    return None


def _load_blueprint_from_idea(idea_session_id: str) -> Optional[dict]:
    """Try to load a PlanPackage and convert to blueprint."""
    try:
        from app.modules.platform.storage import get_plan_package_storage
        from app.services.blueprint_converter import convert_plan_package_to_blueprint

        pkg_storage = get_plan_package_storage()
        packages = pkg_storage.list_by_idea_session(idea_session_id)
        if packages and len(packages) > 0:
            pkg = packages[0]
            # Convert to full pkg object if needed
            full_pkg = pkg_storage.get(pkg.get("packageId", ""))
            if full_pkg:
                return convert_plan_package_to_blueprint(full_pkg)
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("Blueprint from idea failed: %s", exc)
    return None


def _generate_structural_blueprint(
    project_id: str, title: str, repo_dir: Optional[str]
) -> dict:
    """Generate a blueprint DAG from project file structure."""
    import uuid

    nodes: list[dict] = []
    edges: list[dict] = []
    edge_idx = 0

    if not repo_dir or not os.path.isdir(repo_dir):
        return {
            "id": f"struct_{project_id}",
            "title": title,
            "description": "No project files found — generate code first.",
            "nodes": [],
            "edges": [],
        }

    # Walk project files to discover stages
    py_files = []
    for root, dirs, files in os.walk(repo_dir):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('__pycache__', 'venv', '.venv', 'node_modules')]
        for f in files:
            if f.endswith('.py') and not f.startswith('_faros_'):
                py_files.append(os.path.join(root, f))

    if not py_files:
        return {
            "id": f"struct_{project_id}",
            "title": title,
            "description": "No Python files found.",
            "nodes": [],
            "edges": [],
        }

    # Build stages from discovered files
    # Stage 1: Environment Setup (requirements.txt, config)
    # Stage 2: Core Logic (main.py, models, etc.)
    # Stage 3: Tests
    # Stage 4: Results/Output

    config_files = [f for f in py_files if 'config' in f.lower() or 'settings' in f.lower()]
    main_files = [f for f in py_files if 'main' in f.lower() or 'app' in f.lower()]
    model_files = [f for f in py_files if 'model' in f.lower()]
    route_files = [f for f in py_files if 'route' in f.lower() or 'api' in f.lower()]
    test_files = [f for f in py_files if 'test' in f.lower()]
    other_files = [f for f in py_files if f not in config_files + main_files + model_files + route_files + test_files]

    stages = [
        ("Environment & Config", config_files, "Project configuration and dependencies"),
        ("Core Entry Point", main_files, "Main application entry point"),
        ("Data Models", model_files, "Data structures and models"),
        ("API / Routes", route_files, "API endpoints and routing"),
        ("Tests", test_files, "Test suite and validation"),
    ]
    if other_files:
        stages.append(("Other Modules", other_files, "Additional project modules"))

    prev_stage_id = None
    stage_idx = 0
    for stage_name, files, desc in stages:
        if not files:
            continue
        stage_idx += 1
        stage_id = f"stage-{stage_idx}"
        nodes.append({
            "id": stage_id,
            "label": f"Stage {stage_idx}: {stage_name}",
            "stage": stage_name,
            "status": "pending",
            "description": desc,
            "method": "auto-discovered",
            "inputs": [],
            "outputs": [],
            "result": None,
            "startedAt": None, "finishedAt": None, "duration": None,
        })
        if prev_stage_id:
            edges.append({"id": f"e-{edge_idx}", "source": prev_stage_id, "target": stage_id})
            edge_idx += 1

        prev_file_id = None
        for fpath in sorted(files):
            rel = os.path.relpath(fpath, repo_dir)
            file_id = f"file-{stage_idx}-{uuid.uuid4().hex[:6]}"
            nodes.append({
                "id": file_id,
                "label": os.path.basename(rel),
                "stage": stage_name,
                "status": "pending",
                "description": rel,
                "method": "file",
                "inputs": [],
                "outputs": [],
                "result": None,
                "startedAt": None, "finishedAt": None, "duration": None,
            })
            if prev_file_id:
                edges.append({"id": f"e-{edge_idx}", "source": prev_file_id, "target": file_id})
                edge_idx += 1
            else:
                # Connect stage header to first file
                edges.append({"id": f"e-{edge_idx}", "source": stage_id, "target": file_id})
                edge_idx += 1
            prev_file_id = file_id

        prev_stage_id = stage_id

    return {
        "id": f"struct_{project_id}",
        "title": f"{title} — Structure",
        "description": f"Auto-generated structural blueprint from {len(py_files)} Python files",
        "nodes": nodes,
        "edges": edges,
    }
