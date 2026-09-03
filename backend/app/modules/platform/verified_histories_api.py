"""Read-only API for verified, end-to-end research histories.

The records exposed here are manifests over normal FAROS artifacts. A history
is reported as verified only when all six workflow entities and every declared
source artifact still exist and match their recorded SHA-256 digest.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.core.paths import get_data_dir


router = APIRouter(prefix="/workspace/verified-histories", tags=["verified_histories"])

_SCHEMA_VERSION = "faros-verified-workflow-history/v1"
_REQUIRED_STAGES = ("idea", "plan", "code", "experiment", "paper", "reviewx")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _manifest_root(data_dir: Path) -> Path:
    return data_dir / "verified_workflow_histories"


def _read_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schemaVersion") != _SCHEMA_VERSION:
        raise ValueError(f"Unsupported verified-history manifest: {path.name}")
    if str(payload.get("id") or "") != path.stem:
        raise ValueError(f"Manifest ID does not match filename: {path.name}")
    return payload


def _safe_data_path(data_dir: Path, relative_path: str) -> Path:
    root = data_dir.resolve()
    candidate = (root / relative_path).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("Artifact path leaves the FAROS data directory")
    return candidate


def _stage_entity_path(data_dir: Path, stage: dict[str, Any]) -> Path | None:
    stage_id = str(stage.get("id") or "")
    entity_id = str(stage.get("entityId") or "")
    if not entity_id:
        return None
    paths = {
        "idea": data_dir / "ideas" / "sessions" / f"{entity_id}.json",
        "plan": data_dir / "plan_packages" / f"{entity_id}.json",
        "code": data_dir / "code_projects" / entity_id / "repo",
        "experiment": data_dir / "experiments" / entity_id / "experiment.json",
        "paper": data_dir / "papers" / entity_id / "meta.json",
        "reviewx": data_dir / "reviews" / entity_id / "meta.json",
    }
    return paths.get(stage_id)


def _public_manifest(data_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    stages = payload.get("stages") or []
    stage_by_id = {
        str(stage.get("id") or ""): stage
        for stage in stages
        if isinstance(stage, dict)
    }
    missing_stages = [stage_id for stage_id in _REQUIRED_STAGES if stage_id not in stage_by_id]
    broken_stages: list[str] = []
    public_stages: list[dict[str, Any]] = []
    for stage_id in _REQUIRED_STAGES:
        stage = stage_by_id.get(stage_id)
        if not stage:
            continue
        entity_path = _stage_entity_path(data_dir, stage)
        exists = bool(entity_path and entity_path.exists())
        if not exists:
            broken_stages.append(stage_id)
        public_stages.append({**stage, "status": "passed" if exists else "missing"})

    broken_artifacts: list[str] = []
    public_artifacts: list[dict[str, Any]] = []
    history_id = str(payload["id"])
    for artifact in payload.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        artifact_id = str(artifact.get("id") or "")
        try:
            path = _safe_data_path(data_dir, str(artifact.get("path") or ""))
            digest = _sha256(path) if path.is_file() else None
        except (OSError, ValueError):
            digest = None
        expected = str(artifact.get("sha256") or "")
        verified = bool(digest and expected and digest == expected)
        if not verified:
            broken_artifacts.append(artifact_id or "unknown")
        public_artifacts.append({
            "id": artifact_id,
            "label": artifact.get("label") or artifact_id,
            "kind": artifact.get("kind") or "artifact",
            "sha256": expected,
            "verified": verified,
            "url": f"/api/v1/workspace/verified-histories/{history_id}/artifacts/{artifact_id}",
        })

    integrity_verified = not missing_stages and not broken_stages and not broken_artifacts
    return {
        **{key: value for key, value in payload.items() if key not in {"artifacts", "integrity"}},
        "stages": public_stages,
        "artifacts": public_artifacts,
        "integrity": {
            "status": "verified" if integrity_verified else "incomplete",
            "missingStages": missing_stages,
            "brokenStages": broken_stages,
            "brokenArtifacts": broken_artifacts,
        },
    }


def load_verified_histories(data_dir: Path) -> list[dict[str, Any]]:
    root = _manifest_root(data_dir)
    if not root.is_dir():
        return []
    histories: list[dict[str, Any]] = []
    for path in root.glob("*.json"):
        try:
            histories.append(_public_manifest(data_dir, _read_manifest(path)))
        except (json.JSONDecodeError, OSError, ValueError):
            continue
    histories.sort(key=lambda item: str(item.get("completedAt") or ""), reverse=True)
    return histories


def _get_manifest(data_dir: Path, history_id: str) -> dict[str, Any]:
    if not history_id or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in history_id.lower()):
        raise HTTPException(status_code=404, detail="Verified history not found")
    path = _manifest_root(data_dir) / f"{history_id}.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Verified history not found")
    try:
        return _read_manifest(path)
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=f"Verified history manifest is invalid: {exc}") from exc


@router.get("")
async def list_verified_histories() -> dict[str, Any]:
    histories = load_verified_histories(get_data_dir())
    return {
        "schemaVersion": "faros-verified-workflow-history-index/v1",
        "histories": histories,
        "total": len(histories),
    }


@router.get("/{history_id}")
async def get_verified_history(history_id: str) -> dict[str, Any]:
    data_dir = get_data_dir()
    return _public_manifest(data_dir, _get_manifest(data_dir, history_id))


@router.get("/{history_id}/artifacts/{artifact_id}")
async def get_verified_history_artifact(history_id: str, artifact_id: str):
    data_dir = get_data_dir()
    manifest = _get_manifest(data_dir, history_id)
    artifact = next(
        (
            item for item in manifest.get("artifacts") or []
            if isinstance(item, dict) and str(item.get("id") or "") == artifact_id
        ),
        None,
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="Verified history artifact not found")
    try:
        path = _safe_data_path(data_dir, str(artifact.get("path") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Verified history artifact is missing")
    expected = str(artifact.get("sha256") or "")
    if not expected or _sha256(path) != expected:
        raise HTTPException(status_code=409, detail="Verified history artifact failed its integrity check")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name)
