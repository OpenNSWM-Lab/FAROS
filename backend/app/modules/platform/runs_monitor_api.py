"""Platform-owned runs monitor API implementation."""

import logging
import os
import subprocess
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session

from app.core.user_context import sanitized_subprocess_env
from app.db import crud
from app.db.engine import get_session
from app.services.code_project_service import CODE_PROJECTS_DIR, get_vscode_link

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/workspace", tags=["runs_monitor"])

_cache: Dict[str, Any] = {}
_cache_ts: float = 0.0
_CACHE_TTL = 3.0


class WorkspaceProjectInfo(BaseModel):
    id: str
    title: str
    language: Optional[str] = None
    description: Optional[str] = None
    fileCount: int = 0
    totalSizeBytes: int = 0
    workspacePath: str = ""
    lastFileChangeTime: Optional[str] = None
    gitStatusSummary: Optional[str] = None
    vscodeUri: Optional[str] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None


class MonitorResponse(BaseModel):
    projects: List[WorkspaceProjectInfo] = []
    projectCount: int = 0
    timestamp: str = ""


def _scan_workspace(project_id: str, repo_dir: str) -> Dict[str, Any]:
    result = {
        "fileCount": 0,
        "totalSizeBytes": 0,
        "lastFileChangeTime": None,
        "gitStatusSummary": None,
    }

    if not os.path.isdir(repo_dir):
        return result

    try:
        latest_mtime = 0.0
        file_count = 0
        total_size = 0

        for root, dirs, files in os.walk(repo_dir):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d != '__pycache__' and d != 'node_modules']
            for filename in files:
                filepath = os.path.join(root, filename)
                try:
                    stat = os.stat(filepath)
                    file_count += 1
                    total_size += stat.st_size
                    if stat.st_mtime > latest_mtime:
                        latest_mtime = stat.st_mtime
                except OSError:
                    continue

        result["fileCount"] = file_count
        result["totalSizeBytes"] = total_size
        if latest_mtime > 0:
            result["lastFileChangeTime"] = datetime.fromtimestamp(latest_mtime).isoformat()
    except Exception as exc:
        logger.warning("Workspace scan error for %s: %s", project_id, exc)

    git_dir = os.path.join(repo_dir, ".git")
    if os.path.isdir(git_dir):
        try:
            proc = subprocess.run(
                ["git", "status", "--short"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                timeout=2,
                env=sanitized_subprocess_env(),
            )
            lines = proc.stdout.strip().split("\n") if proc.stdout.strip() else []
            result["gitStatusSummary"] = f"{len(lines)} changed files" if lines else "clean"
        except Exception:
            result["gitStatusSummary"] = None

    return result


@router.get("/monitor", response_model=MonitorResponse)
async def monitor(db: Session = Depends(get_session)) -> MonitorResponse:
    global _cache, _cache_ts

    now = time.time()
    if now - _cache_ts < _CACHE_TTL and _cache:
        return MonitorResponse(**_cache)

    projects_db = crud.list_projects_v2(db)
    project_infos = []

    for project in projects_db:
        repo_dir = os.path.join(CODE_PROJECTS_DIR, project.id, "repo")
        workspace_info = _scan_workspace(project.id, repo_dir)
        vscode = get_vscode_link(project.id)

        project_infos.append(
            WorkspaceProjectInfo(
                id=project.id,
                title=project.title or "",
                language=project.language,
                description=(project.description or "")[:200],
                fileCount=workspace_info["fileCount"] or (project.file_count or 0),
                totalSizeBytes=workspace_info["totalSizeBytes"] or (project.total_size_bytes or 0),
                workspacePath=repo_dir,
                lastFileChangeTime=workspace_info["lastFileChangeTime"],
                gitStatusSummary=workspace_info["gitStatusSummary"],
                vscodeUri=vscode.get("uri"),
                createdAt=project.created_at.isoformat() if project.created_at else None,
                updatedAt=project.updated_at.isoformat() if project.updated_at else None,
            )
        )

    result = {
        "projects": [project_info.model_dump() for project_info in project_infos],
        "projectCount": len(project_infos),
        "timestamp": datetime.utcnow().isoformat(),
    }

    _cache = result
    _cache_ts = now
    return MonitorResponse(**result)
