"""Persistent action history for ReviewX experiment feedback iterations."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


_STORAGE_DIR = Path(__file__).resolve().parents[3] / "data" / "reviewx_experiment_feedback"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _path(record_id: str) -> Path:
    return _STORAGE_DIR / f"{record_id}.json"


def _write(record: Dict[str, Any]) -> Dict[str, Any]:
    _STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    path = _path(record["id"])
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(record, indent=2, ensure_ascii=True), encoding="utf-8")
    temp_path.replace(path)
    return record


def create_experiment_feedback(record: Dict[str, Any]) -> Dict[str, Any]:
    now = _now()
    stored = {
        **record,
        "id": f"exprev_{uuid.uuid4().hex[:12]}",
        "nextRunId": None,
        "planRevision": None,
        "createdAt": now,
        "updatedAt": now,
    }
    return _write(stored)


def get_experiment_feedback(record_id: str) -> Optional[Dict[str, Any]]:
    path = _path(record_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_experiment_feedback(run_id: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
    if not _STORAGE_DIR.is_dir():
        return []
    records: List[Dict[str, Any]] = []
    for path in _STORAGE_DIR.glob("exprev_*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if run_id and record.get("runId") != run_id:
            continue
        records.append(record)
    records.sort(key=lambda item: item.get("createdAt", ""), reverse=True)
    return records[:limit]


def update_experiment_feedback(record_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    record = get_experiment_feedback(record_id)
    if record is None:
        raise ValueError(f"Experiment feedback '{record_id}' not found")
    record.update(updates)
    record["updatedAt"] = _now()
    return _write(record)
