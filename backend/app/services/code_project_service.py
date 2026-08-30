"""
Code Project Service - Multi-file project generation, storage, indexing, export.

Responsibilities:
- Create project records in DB
- Write generated files to filesystem storage
- Index all file paths into CodeProjectFile for fast browsing/search
- Content search across project files
- Zip export with DB tracking
- VSCode link generation
"""

import os
import json
import hashlib
import zipfile
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path

from sqlmodel import Session

from app.db import crud
from app.core.paths import get_data_dir
from app.db.models import (
    CodeProjectV2, CodeProjectV2Create,
    CodeProjectFileCreate, CodeProjectFile,
    CodeProjectGenerationCreate, GenerationStatus,
    CodeProjectExportCreate,
)

logger = logging.getLogger(__name__)

# Base storage paths
CODE_PROJECTS_DIR = str(get_data_dir() / "code_projects")


def _get_project_repo_dir(project_id: str) -> str:
    """Get the repo directory for a project."""
    return os.path.join(CODE_PROJECTS_DIR, project_id, "repo")


def _get_project_exports_dir(project_id: str) -> str:
    """Get the exports directory for a project."""
    return os.path.join(CODE_PROJECTS_DIR, project_id, "exports")


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _sha256_file(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_path(path: str) -> bool:
    """Reject path traversal attempts."""
    normalized = os.path.normpath(path)
    return not normalized.startswith("..") and "\\.." not in normalized and "/.." not in normalized


# ============ Project CRUD ============

def create_project(
    db: Session,
    title: str,
    description: Optional[str] = None,
    language: Optional[str] = None,
    framework: Optional[str] = None,
    license_str: Optional[str] = None,
    source_idea_session_id: Optional[str] = None,
    source_candidate_id: Optional[str] = None,
) -> CodeProjectV2:
    """Create a new code project and initialize storage directory."""
    project = crud.create_project_v2(db, CodeProjectV2Create(
        title=title,
        description=description,
        language=language,
        framework=framework,
        license=license_str,
        source_idea_session_id=source_idea_session_id,
        source_candidate_id=source_candidate_id,
    ))

    # Create storage directories
    repo_dir = _get_project_repo_dir(project.id)
    os.makedirs(repo_dir, exist_ok=True)
    exports_dir = _get_project_exports_dir(project.id)
    os.makedirs(exports_dir, exist_ok=True)

    # Update root storage path
    crud.update_project_v2(db, project.id, {"root_storage_path": repo_dir})
    project.root_storage_path = repo_dir

    return project


# ============ File Writing + Indexing ============

def _normalize_file_path(raw_path: str) -> tuple:
    """
    Normalize a generated file path.
    
    Returns:
        (normalized_path, is_directory_only)
        
    Rules:
    - Strip leading/trailing whitespace
    - Strip leading '/' or './' 
    - Collapse '..' and '.' via normpath
    - If ends with '/' or has empty basename → directory only
    - Reject traversal paths
    """
    p = raw_path.strip()
    if not p:
        return ("", True)
    
    is_dir = p.endswith("/")
    
    # Normalize
    p = p.lstrip("/")
    if p.startswith("./"):
        p = p[2:]
    p = os.path.normpath(p)
    
    # Reject traversal
    if p.startswith("..") or "/../" in p or p == ".":
        return ("", True)
    
    # Check if basename is empty (directory-only entry)
    basename = os.path.basename(p)
    if not basename or is_dir:
        return (p.rstrip("/"), True) if p and p != "." else ("", True)
    
    return (p, False)


def _safe_join_repo(repo_dir: str, rel_path: str) -> Optional[str]:
    """Safely join repo root + relative path, preventing traversal."""
    abs_path = os.path.realpath(os.path.join(repo_dir, rel_path))
    real_repo = os.path.realpath(repo_dir)
    if not abs_path.startswith(real_repo + os.sep) and abs_path != real_repo:
        return None
    return abs_path


def write_project_files(
    db: Session,
    project_id: str,
    files: List[Dict[str, str]],
) -> Tuple[int, int]:
    """
    Write files to project storage and index them in DB.
    
    Robust handling:
    - Paths ending with '/' are treated as directories (mkdir only)
    - Empty basenames are treated as directories
    - Path traversal is rejected
    - Parent directories are created automatically
    
    Args:
        db: Database session
        project_id: Project ID
        files: List of {"path": "...", "content": "..."} dicts
    
    Returns:
        (file_count, total_bytes)
    """
    project = crud.get_project_v2(db, project_id)
    if not project:
        raise ValueError(f"Project not found: {project_id}")

    repo_dir = _get_project_repo_dir(project_id)
    os.makedirs(repo_dir, exist_ok=True)

    # Clear existing file index
    crud.delete_project_files(db, project_id)

    total_bytes = 0
    file_creates: List[CodeProjectFileCreate] = []
    dir_paths_seen = set()
    skipped_entries = []
    files_written = 0
    dirs_created = 0

    def _ensure_dir_indexed(dir_path: str):
        """Create and index a directory path and all parents."""
        nonlocal dirs_created
        parts = dir_path.split("/")
        for i in range(1, len(parts) + 1):
            dp = "/".join(parts[:i])
            if dp and dp not in dir_paths_seen:
                dir_paths_seen.add(dp)
                safe = _safe_join_repo(repo_dir, dp)
                if safe:
                    os.makedirs(safe, exist_ok=True)
                    file_creates.append(CodeProjectFileCreate(
                        project_id=project_id,
                        path=dp,
                        is_dir=True,
                        size=0,
                    ))
                    dirs_created += 1

    for file_entry in files:
        raw_path = file_entry.get("path", "")
        content = file_entry.get("content", "")

        normalized, is_dir_only = _normalize_file_path(raw_path)

        if not normalized:
            skipped_entries.append(raw_path)
            logger.warning(f"Skipping invalid/empty path: {repr(raw_path)}")
            continue

        # Security check
        safe_abs = _safe_join_repo(repo_dir, normalized)
        if not safe_abs:
            skipped_entries.append(raw_path)
            logger.warning(f"Skipping traversal path: {repr(raw_path)}")
            continue

        if is_dir_only:
            # Directory placeholder — mkdir only, do NOT open as file
            _ensure_dir_indexed(normalized)
            logger.debug(f"Created directory entry: {normalized}")
            continue

        # Ensure parent directories exist and are indexed
        parent = os.path.dirname(normalized)
        if parent:
            _ensure_dir_indexed(parent)

        # Write file (never a directory)
        os.makedirs(os.path.dirname(safe_abs), exist_ok=True)
        with open(safe_abs, "w", encoding="utf-8") as f:
            f.write(content)

        file_size = len(content.encode("utf-8"))
        total_bytes += file_size
        files_written += 1

        file_creates.append(CodeProjectFileCreate(
            project_id=project_id,
            path=normalized,
            is_dir=False,
            size=file_size,
            sha256=_sha256(content),
        ))

    # Bulk insert file index
    crud.bulk_create_project_files(db, file_creates)

    file_count = sum(1 for fc in file_creates if not fc.is_dir)

    # Update project stats
    crud.update_project_v2(db, project_id, {
        "file_count": file_count,
        "total_size_bytes": total_bytes,
    })

    logger.info(f"Persist report: {files_written} files, {dirs_created} dirs, {len(skipped_entries)} skipped")
    return file_count, total_bytes


def index_existing_project_files(db: Session, project_id: str) -> Tuple[int, int]:
    """Index an existing on-disk repository without rewriting its files.

    Import workflows need to preserve binary files and exact hashes, while the
    normal generation path writes UTF-8 content.  This scanner only registers
    files already inside the project's managed repo directory.
    """
    project = crud.get_project_v2(db, project_id)
    if not project:
        raise ValueError(f"Project not found: {project_id}")

    repo_dir = os.path.realpath(_get_project_repo_dir(project_id))
    if not os.path.isdir(repo_dir):
        raise ValueError(f"Project repo not found on disk: {repo_dir}")

    crud.delete_project_files(db, project_id)
    records: List[CodeProjectFileCreate] = []
    total_bytes = 0
    ignored_dirs = {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules"}

    for root, dirs, files in os.walk(repo_dir, followlinks=False):
        dirs[:] = [name for name in dirs if name not in ignored_dirs]
        root_real = os.path.realpath(root)
        if os.path.commonpath([repo_dir, root_real]) != repo_dir:
            dirs[:] = []
            continue

        relative_root = os.path.relpath(root_real, repo_dir)
        if relative_root != ".":
            records.append(CodeProjectFileCreate(
                project_id=project_id,
                path=relative_root.replace(os.sep, "/"),
                is_dir=True,
                size=0,
            ))

        for filename in files:
            absolute = os.path.realpath(os.path.join(root_real, filename))
            if os.path.commonpath([repo_dir, absolute]) != repo_dir or not os.path.isfile(absolute):
                continue
            relative = os.path.relpath(absolute, repo_dir).replace(os.sep, "/")
            size = os.path.getsize(absolute)
            records.append(CodeProjectFileCreate(
                project_id=project_id,
                path=relative,
                is_dir=False,
                size=size,
                sha256=_sha256_file(absolute),
            ))
            total_bytes += size

    crud.bulk_create_project_files(db, records)
    file_count = sum(1 for record in records if not record.is_dir)
    crud.update_project_v2(db, project_id, {
        "file_count": file_count,
        "total_size_bytes": total_bytes,
    })
    logger.info("Indexed existing project %s: %d files, %d bytes", project_id, file_count, total_bytes)
    return file_count, total_bytes


# ============ File Reading ============

def read_file_content(project_id: str, file_path: str) -> Optional[str]:
    """Read content of a specific file in the project."""
    if not _safe_path(file_path):
        return None

    repo_dir = _get_project_repo_dir(project_id)
    abs_path = os.path.join(repo_dir, file_path)

    # Security: ensure resolved path is within repo_dir
    real_abs = os.path.realpath(abs_path)
    real_repo = os.path.realpath(repo_dir)
    if not real_abs.startswith(real_repo):
        return None

    if not os.path.isfile(abs_path):
        return None

    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception as e:
        logger.error(f"Error reading file {abs_path}: {e}")
        return None


def get_file_abs_path(project_id: str, file_path: str) -> Optional[str]:
    """Get absolute path for download, with security check."""
    if not _safe_path(file_path):
        return None

    repo_dir = _get_project_repo_dir(project_id)
    abs_path = os.path.join(repo_dir, file_path)

    real_abs = os.path.realpath(abs_path)
    real_repo = os.path.realpath(repo_dir)
    if not real_abs.startswith(real_repo):
        return None

    if not os.path.isfile(abs_path):
        return None

    return abs_path


# ============ Content Search ============

def search_content(
    project_id: str,
    query: str,
    max_results: int = 50,
) -> List[Dict[str, Any]]:
    """Search file contents for a query string."""
    repo_dir = _get_project_repo_dir(project_id)
    if not os.path.isdir(repo_dir):
        return []

    results = []
    query_lower = query.lower()

    for root, _dirs, filenames in os.walk(repo_dir):
        for fname in filenames:
            if len(results) >= max_results:
                break
            abs_path = os.path.join(root, fname)
            rel_path = os.path.relpath(abs_path, repo_dir)

            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
            except Exception:
                continue

            for line_no, line in enumerate(lines, 1):
                if query_lower in line.lower():
                    results.append({
                        "path": rel_path,
                        "line": line_no,
                        "content": line.rstrip()[:200],
                    })
                    if len(results) >= max_results:
                        break

    return results


# ============ Tree Building ============

def get_tree(
    db: Session,
    project_id: str,
    path: str = "",
) -> List[Dict[str, Any]]:
    """
    Get tree entries for a given path (direct children).
    Returns list of {name, path, isDir, size} dicts.
    """
    files = crud.list_project_files(db, project_id, parent_path=path)

    tree = []
    for f in files:
        name = f.path.split("/")[-1] if "/" in f.path else f.path
        tree.append({
            "name": name,
            "path": f.path,
            "isDir": f.is_dir,
            "size": f.size,
        })

    # Sort: dirs first, then alphabetical
    tree.sort(key=lambda x: (not x["isDir"], x["name"].lower()))
    return tree


# ============ Zip Export ============

def export_zip(db: Session, project_id: str) -> Dict[str, Any]:
    """Create a zip export of the project and return export metadata."""
    project = crud.get_project_v2(db, project_id)
    if not project:
        raise ValueError(f"Project not found: {project_id}")

    repo_dir = _get_project_repo_dir(project_id)
    if not os.path.isdir(repo_dir):
        raise ValueError(f"Project repo not found on disk: {repo_dir}")

    exports_dir = _get_project_exports_dir(project_id)
    os.makedirs(exports_dir, exist_ok=True)

    # Create export record first to get ID
    export_rec = crud.create_project_export(db, CodeProjectExportCreate(
        project_id=project_id,
        kind="zip",
    ))

    zip_filename = f"{export_rec.id}.zip"
    zip_path = os.path.join(exports_dir, zip_filename)

    # Build zip
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, filenames in os.walk(repo_dir):
            for fname in filenames:
                abs_path = os.path.join(root, fname)
                arc_name = os.path.relpath(abs_path, repo_dir)
                zf.write(abs_path, arc_name)

    zip_size = os.path.getsize(zip_path)
    zip_hash = _sha256_file(zip_path)

    # Update export record
    crud.update_project_v2(db, project_id, {})  # touch updated_at
    export_rec.file_path = zip_path
    export_rec.size = zip_size
    export_rec.sha256 = zip_hash
    db.add(export_rec)
    db.commit()
    db.refresh(export_rec)

    return {
        "id": export_rec.id,
        "projectId": project_id,
        "kind": "zip",
        "filePath": zip_path,
        "size": zip_size,
        "sha256": zip_hash,
        "createdAt": export_rec.created_at.isoformat(),
    }


def get_export_path(db: Session, export_id: str) -> Optional[str]:
    """Get the absolute path of an export file."""
    export_rec = crud.get_project_export(db, export_id)
    if not export_rec:
        return None
    if not os.path.isfile(export_rec.file_path):
        return None
    return export_rec.file_path


# ============ VSCode Link ============

def get_vscode_link(project_id: str) -> Dict[str, Any]:
    """Generate a VSCode open link for the project."""
    repo_dir = _get_project_repo_dir(project_id)
    abs_path = os.path.realpath(repo_dir)

    return {
        "uri": f"vscode://file/{abs_path}",
        "path": abs_path,
        "exists": os.path.isdir(abs_path),
        "instructions": (
            "Click the URI above or run: code " + abs_path + "\n"
            "If VSCode is not installed, install it from https://code.visualstudio.com/"
        ),
    }


# ============ Sample Project Generation (for testing/demo) ============

def generate_sample_project(
    db: Session,
    title: str,
    language: str = "python",
    description: Optional[str] = None,
) -> CodeProjectV2:
    """
    Generate a sample multi-file project for testing.
    Produces ≥ 8 files to demonstrate project-grade output.
    """
    project = create_project(
        db,
        title=title,
        description=description or f"Sample {language} project",
        language=language,
        framework="fastapi" if language == "python" else None,
        license_str="MIT",
    )

    # Create generation record
    gen = crud.create_project_generation(db, CodeProjectGenerationCreate(
        project_id=project.id,
        provider_name="sample",
        model="sample-generator",
        status=GenerationStatus.RUNNING,
    ))

    if language == "python":
        files = _sample_python_files(title)
    else:
        files = _sample_generic_files(title, language)

    file_count, total_bytes = write_project_files(db, project.id, files)

    # Update generation status
    crud.update_project_generation(db, gen.id, {
        "status": GenerationStatus.SUCCEEDED,
        "notes": f"Generated {file_count} files, {total_bytes} bytes",
    })

    logger.info(f"Generated sample project {project.id}: {file_count} files, {total_bytes} bytes")
    return project


def _sample_python_files(title: str) -> List[Dict[str, str]]:
    safe_name = title.lower().replace(" ", "_").replace("-", "_")
    return [
        {"path": "README.md", "content": f"# {title}\n\nA sample Python project.\n\n## Setup\n\n```bash\npip install -r requirements.txt\n```\n\n## Run\n\n```bash\nuvicorn src.main:app --reload\n```\n\n## Test\n\n```bash\npytest tests/\n```\n"},
        {"path": "requirements.txt", "content": "fastapi>=0.109.0\nuvicorn[standard]>=0.27.0\npydantic>=2.5.0\npytest>=7.0.0\nhttpx>=0.25.0\n"},
        {"path": "pyproject.toml", "content": f'[project]\nname = "{safe_name}"\nversion = "0.1.0"\ndescription = "{title}"\nrequires-python = ">=3.9"\n\n[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'},
        {"path": ".gitignore", "content": "__pycache__/\n*.pyc\n.env\nvenv/\ndist/\n*.egg-info/\n.pytest_cache/\n"},
        {"path": "src/__init__.py", "content": ""},
        {"path": "src/main.py", "content": f'"""Main application entry point."""\n\nfrom fastapi import FastAPI\nfrom .routes import router\n\napp = FastAPI(title="{title}", version="0.1.0")\napp.include_router(router, prefix="/api")\n\n@app.get("/health")\nasync def health():\n    return {{"status": "ok"}}\n'},
        {"path": "src/routes.py", "content": '"""API routes."""\n\nfrom fastapi import APIRouter\nfrom .models import Item, ItemCreate\n\nrouter = APIRouter()\n\nitems_db: list[Item] = []\n\n@router.get("/items", response_model=list[Item])\nasync def list_items():\n    return items_db\n\n@router.post("/items", response_model=Item, status_code=201)\nasync def create_item(data: ItemCreate):\n    item = Item(id=len(items_db) + 1, **data.model_dump())\n    items_db.append(item)\n    return item\n\n@router.get("/items/{item_id}", response_model=Item)\nasync def get_item(item_id: int):\n    for item in items_db:\n        if item.id == item_id:\n            return item\n    from fastapi import HTTPException\n    raise HTTPException(status_code=404, detail="Item not found")\n'},
        {"path": "src/models.py", "content": '"""Data models."""\n\nfrom pydantic import BaseModel\nfrom typing import Optional\n\nclass ItemCreate(BaseModel):\n    name: str\n    description: Optional[str] = None\n    price: float\n\nclass Item(ItemCreate):\n    id: int\n'},
        {"path": "src/config.py", "content": '"""Configuration."""\n\nimport os\n\nclass Settings:\n    APP_NAME: str = "Sample App"\n    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"\n    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///data.db")\n\nsettings = Settings()\n'},
        {"path": "tests/__init__.py", "content": ""},
        {"path": "tests/test_main.py", "content": '"""Tests for main application."""\n\nimport pytest\nfrom fastapi.testclient import TestClient\nfrom src.main import app\n\nclient = TestClient(app)\n\ndef test_health():\n    response = client.get("/health")\n    assert response.status_code == 200\n    assert response.json()["status"] == "ok"\n\ndef test_create_item():\n    response = client.post("/api/items", json={"name": "Test", "price": 9.99})\n    assert response.status_code == 201\n    assert response.json()["name"] == "Test"\n\ndef test_list_items():\n    response = client.get("/api/items")\n    assert response.status_code == 200\n    assert isinstance(response.json(), list)\n'},
        {"path": "Dockerfile", "content": 'FROM python:3.11-slim\nWORKDIR /app\nCOPY requirements.txt .\nRUN pip install --no-cache-dir -r requirements.txt\nCOPY . .\nEXPOSE 8000\nCMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]\n'},
    ]


def _sample_generic_files(title: str, language: str) -> List[Dict[str, str]]:
    return [
        {"path": "README.md", "content": f"# {title}\n\nA sample {language} project.\n"},
        {"path": ".gitignore", "content": "node_modules/\ndist/\n.env\n"},
        {"path": "src/index.ts", "content": f'// {title} entry point\nconsole.log("Hello from {title}");\n'},
        {"path": "src/utils.ts", "content": "export function greet(name: string): string {\n  return `Hello, ${name}!`;\n}\n"},
        {"path": "src/types.ts", "content": "export interface Config {\n  name: string;\n  debug: boolean;\n}\n"},
        {"path": "tests/index.test.ts", "content": 'import { greet } from "../src/utils";\n\ntest("greet returns greeting", () => {\n  expect(greet("World")).toBe("Hello, World!");\n});\n'},
        {"path": "package.json", "content": f'{{"name": "{title.lower().replace(" ", "-")}", "version": "0.1.0", "scripts": {{"build": "tsc", "test": "jest"}}}}\n'},
        {"path": "tsconfig.json", "content": '{"compilerOptions": {"target": "ES2020", "module": "commonjs", "outDir": "dist", "strict": true}}\n'},
    ]
