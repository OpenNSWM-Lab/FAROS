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


@router.get("/artifacts/{node_id}/{filename:path}")
async def get_artifact_file(node_id: str, filename: str, download: bool = False):
    """Serve a cart artifact file — inline preview or download."""
    import os as _os, glob as _glob
    from fastapi.responses import FileResponse, PlainTextResponse
    from app.db.engine import _DATA_DIR
    cart_base = _os.path.join(_DATA_DIR, "cart_artifacts")
    if not _os.path.isdir(cart_base):
        raise HTTPException(status_code=404, detail="No artifacts")
    for cart_dir in sorted(_glob.glob(_os.path.join(cart_base, "cart_*")), reverse=True):
        for sub in ["runs", "data"]:
            fp = _os.path.join(cart_dir, sub, node_id, filename)
            if _os.path.isfile(fp):
                if download:
                    return FileResponse(fp, filename=filename)
                # For text files, return content inline (not download)
                text_exts = {'.py', '.js', '.ts', '.json', '.yml', '.yaml', '.txt', '.md', '.csv', '.log', '.xml', '.html', '.css', '.sh', '.bat', '.cfg', '.ini', '.toml'}
                _, ext = _os.path.splitext(filename)
                if ext.lower() in text_exts:
                    try:
                        with open(fp, 'r', encoding='utf-8', errors='replace') as f:
                            content = f.read()
                        return PlainTextResponse(content, media_type="text/plain; charset=utf-8")
                    except Exception:
                        pass
                return FileResponse(fp, filename=filename)
    raise HTTPException(status_code=404, detail=f"File not found: {filename}")


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
    source_idea_id = getattr(project, "source_idea_session_id", None)

    # Try PlanPackage path first — use source_idea_session_id to find the correct one
    ppkg = _load_plan_package_for_project(project_id, project.title, source_idea_id)
    if ppkg:
        # Use the proper blueprint converter from the service layer
        blueprint = _convert_ppkg_to_blueprint(ppkg, project_id, project.title)
        blueprint["projectId"] = project_id
        blueprint["projectTitle"] = project.title
        blueprint["source"] = "plan_package"
        # Find the correct cart for this PlanPackage
        ppkg_id = ppkg.get("packageId")
        cart_dir = _find_cart_dir_for_project(project_id, ppkg_id)
        _merge_cart_state(blueprint, cart_dir)
        return blueprint

    # Try legacy Idea session path
    if source_idea_id:
        try:
            blueprint = _load_blueprint_from_idea(source_idea_id)
            if blueprint:
                blueprint["projectId"] = project_id
                blueprint["projectTitle"] = project.title
                blueprint["source"] = "plan_package"
                cart_dir = _find_cart_dir_for_project(project_id)
                _merge_cart_state(blueprint, cart_dir)
                return blueprint
        except Exception as exc:
            logger.warning("Failed to load blueprint from idea %s: %s", source_idea_id, exc)

    # Fallback: generate from project structure
    blueprint = _generate_structural_blueprint(project_id, project.title, repo_dir)
    blueprint["projectId"] = project_id
    blueprint["projectTitle"] = project.title
    blueprint["source"] = "project_structure"

    # Merge execution state from the correct cart only
    cart_dir = _find_cart_dir_for_project(project_id)
    _merge_cart_state(blueprint, cart_dir)
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

def _load_plan_package_for_project(project_id: str, title: str, source_idea_session_id: Optional[str] = None) -> Optional[dict]:
    """Try to load the PlanPackage associated with this project.

    Lookup order:
    1. If project has source_idea_session_id, find the PlanPackage whose
       source.ideaSessionId matches.
    2. Check codegen sessions for this project to find the planSessionId.
    3. Fall back: return the most recent approved PlanPackage.
    """
    import os as _os
    from app.db.engine import _DATA_DIR
    ppkg_dir = _os.path.join(_DATA_DIR, "plan_packages")
    if not _os.path.isdir(ppkg_dir):
        return None

    # Collect all PlanPackage JSON files
    candidates: list[tuple[str, dict]] = []
    for fname in _os.listdir(ppkg_dir):
        if fname.endswith(".json"):
            try:
                path = _os.path.join(ppkg_dir, fname)
                with open(path, "r", encoding="utf-8") as f:
                    ppkg = json.load(f)
                if ppkg.get("packageId"):
                    candidates.append((fname, ppkg))
            except Exception:
                pass

    if not candidates:
        return None

    # 1. Match by source_idea_session_id
    if source_idea_session_id:
        for _fname, ppkg in candidates:
            if ppkg.get("source", {}).get("ideaSessionId") == source_idea_session_id:
                return ppkg

    # 2. Check codegen sessions for this project to discover the planSessionId
    try:
        from app.agents.codegen.kernel import list_sessions as list_codegen_sessions
        sessions = list_codegen_sessions(project_id=project_id)
        for sess in sessions:
            plan_session_id = sess.get("config", {}).get("planSessionId")
            if plan_session_id:
                for _fname, ppkg in candidates:
                    if ppkg.get("source", {}).get("ideaSessionId") == plan_session_id:
                        return ppkg
    except Exception as exc:
        logger.warning("Failed to query codegen sessions for project %s: %s", project_id, exc)

    return None


def _convert_ppkg_to_blueprint(ppkg: dict, project_id: str, title: str) -> dict:
    """Convert PlanPackage to ExperimentBlueprint DAG format.

    Uses the proper blueprint_converter service for correct field mapping,
    falls back to inline conversion if the model can't be loaded.
    """
    # Try using the proper service-layer converter
    try:
        from app.models.plan_package import PlanPackage
        from app.services.blueprint_converter import convert_plan_package_to_blueprint
        model = PlanPackage.model_validate(ppkg)
        result = convert_plan_package_to_blueprint(model)
        result["id"] = f"ppkg_{project_id}"
        result["title"] = f"{title} — Experiment Blueprint"
        return result
    except Exception as exc:
        logger.warning("Service converter failed, using inline fallback: %s", exc)

    # Inline fallback for raw dicts that don't validate as PlanPackage
    nodes: list[dict] = []
    edges: list[dict] = []
    edge_idx = 0

    for stage in ppkg.get("stages", []):
        sid = stage["id"]
        nodes.append({
            "id": sid,
            "label": stage.get("title", sid),
            "stage": stage.get("title", ""),
            "status": "pending",
            "description": stage.get("goal", stage.get("desc", "")),
            "method": "stage-header",
            "inputs": [],
            "outputs": [],
            "result": None,
            "startedAt": None, "finishedAt": None, "duration": None,
        })
        prev_step_id = sid
        for step in stage.get("steps", []):
            step_id = step["id"]
            nodes.append({
                "id": step_id,
                "label": f"{step.get('order', 0)}. {step.get('title', step_id)}",
                "stage": stage.get("title", ""),
                "status": "pending",
                "description": step.get("description", step.get("desc", "")),
                "method": step.get("method", ""),
                "inputs": step.get("inputFrom", step.get("inputs", [])),
                "outputs": [o.get("name", "") for o in step.get("outputs", [])],
                "result": None,
                "startedAt": None, "finishedAt": None, "duration": None,
            })
            edges.append({"id": f"e-{edge_idx}", "source": prev_step_id, "target": step_id})
            edge_idx += 1
            prev_step_id = step_id

    return {
        "id": f"ppkg_{project_id}",
        "title": f"{title} — Experiment Blueprint",
        "description": ppkg.get("idea", {}).get("title", ""),
        "nodes": nodes,
        "edges": edges,
    }


def _find_cart_dir_for_project(project_id: str, ppkg_id: Optional[str] = None) -> Optional[str]:
    """Find the correct cart directory for a project.

    Matches only by project ID or explicit PlanPackage ID in the cart manifest.
    """
    import os as _os, glob as _glob
    from app.db.engine import _DATA_DIR
    cart_base = _os.path.join(_DATA_DIR, "cart_artifacts")
    if not _os.path.isdir(cart_base):
        return None

    cart_dirs = _glob.glob(_os.path.join(cart_base, "cart_*"))
    if not cart_dirs:
        return None

    for cd in cart_dirs:
        manifest_path = _os.path.join(cd, "data", "manifest.json")
        if not _os.path.isfile(manifest_path):
            continue
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            manifest_project_id = manifest.get("project_id") or manifest.get("projectId")
            manifest_package_id = manifest.get("package_id")
            if manifest_project_id == project_id:
                return cd
            if not manifest_project_id and ppkg_id and manifest_package_id == ppkg_id:
                return cd
        except Exception:
            pass
    return None


def _list_cart_artifacts(node_id: str, cart_dir: Optional[str] = None) -> list[str]:
    """List all files in cart data/ and runs/ for a node."""
    import os as _os
    files = []
    if not cart_dir:
        return files
    for sub in ["data", "runs"]:
        node_dir = _os.path.join(cart_dir, sub, node_id)
        if _os.path.isdir(node_dir):
            for fname in sorted(_os.listdir(node_dir)):
                if not fname.startswith('.') and fname != 'result.json' and fname not in files:
                    files.append(fname)
    return files


def _load_cart_node_result(node_id: str, cart_dir: Optional[str] = None) -> Optional[dict]:
    """Load result.json from a cart run for a specific node."""
    import os as _os
    if not cart_dir:
        return None
    result_path = _os.path.join(cart_dir, "data", node_id, "result.json")
    if _os.path.isfile(result_path):
        try:
            with open(result_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None


def _merge_cart_state(blueprint: dict, cart_dir: Optional[str] = None) -> None:
    """Merge execution status from cart blueprint_state.json into DAG nodes."""
    import os as _os
    if not cart_dir:
        return
    bp_file = _os.path.join(cart_dir, "blueprint_state.json")
    if not _os.path.isfile(bp_file):
        return
    try:
        with open(bp_file, "r", encoding="utf-8") as f:
            states = json.load(f)
    except Exception:
        return
    if not states:
        return
    nodes = blueprint.get("nodes", [])
    for node in nodes:
        nid = node.get("id", "")
        if nid in states:
            node["status"] = states[nid].get("status", "pending")
            # Merge full result data from cart
            cart_data = _load_cart_node_result(nid, cart_dir)
            if cart_data:
                # Collect artifacts from THIS cart directory only
                all_artifacts = _list_cart_artifacts(nid, cart_dir)
                node["result"] = {
                    "summary": cart_data.get("message", ""),
                    "metrics": cart_data.get("outputs", {}).get("metrics", {}),
                    "artifacts": all_artifacts or [a.get("name","") for a in cart_data.get("artifacts", [])],
                    "logs": cart_data.get("message", "").split("\n") if cart_data.get("message") else [],
                    "error": cart_data.get("error"),
                }
                node["startedAt"] = cart_data.get("started_at")
                node["finishedAt"] = cart_data.get("finished_at")
                node["duration"] = cart_data.get("duration_ms")

    # Update stage headers based on child step statuses
    for node in nodes:
        if node["id"].startswith("stage-"):
            stage_ref = node.get("stage", "")  # e.g., "stage-1"
            child_statuses = [
                n.get("status", "pending")
                for n in nodes
                if n.get("stage") == stage_ref and not n["id"].startswith("stage-")
            ]
            if child_statuses:
                if all(s == "success" for s in child_statuses):
                    node["status"] = "success"
                elif any(s == "running" for s in child_statuses):
                    node["status"] = "running"
                elif any(s == "failed" for s in child_statuses):
                    node["status"] = "failed"
                else:
                    node["status"] = "pending"


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
