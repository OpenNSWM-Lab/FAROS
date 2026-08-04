"""Platform-domain storage facade."""

import json
import os
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from app.storage.artifact_storage import get_storage as get_artifact_storage
from app.storage.experiment_storage import (
    create_experiment,
    get_dataset,
    get_dataset_preview,
    get_experiment,
    get_figure,
    get_metrics,
    ingest_metrics,
    list_datasets,
    list_experiments,
    list_figures,
    save_dataset,
    save_figure_artifact,
    update_experiment,
)
from app.storage.plan_session_storage import (
    get_candidate_storage as get_plan_candidate_storage,
    get_session_storage as get_plan_session_storage,
)
from app.storage.plan_package_storage import get_plan_package_storage
from app.storage.run_storage import get_storage as get_run_storage

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_PLAN_LINKS_DIR = os.path.join(_BASE_DIR, "data", "plan_links")
os.makedirs(_PLAN_LINKS_DIR, exist_ok=True)


def _serialize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _serialize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    return value


def _serialize_record(record: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if record is None:
        return None
    return {key: _serialize_value(value) for key, value in record.items()}


def _plan_link_path(link_id: str) -> str:
    return os.path.join(_PLAN_LINKS_DIR, f"{link_id}.json")


def create_plan_link(plan_session_id: str, candidate_id: str, candidate_index: Optional[int] = None) -> Dict[str, Any]:
    record = {
        "linkId": f"plink_{uuid.uuid4().hex[:12]}",
        "planSessionId": plan_session_id,
        "candidateId": candidate_id,
        "candidateIndex": candidate_index,
        "createdAt": datetime.utcnow().isoformat(),
    }
    with open(_plan_link_path(record["linkId"]), "w") as handle:
        json.dump(record, handle, indent=2, default=str)
    return record


def get_plan_link(link_id: str) -> Optional[Dict[str, Any]]:
    path = _plan_link_path(link_id)
    if not os.path.isfile(path):
        return None
    with open(path) as handle:
        return json.load(handle)


def get_plan_link_context(link_id: str) -> Optional[Dict[str, Any]]:
    record = get_plan_link(link_id)
    if not record:
        return None

    sess_storage = get_plan_session_storage()
    cand_storage = get_plan_candidate_storage()

    plan_session = sess_storage.get(record["planSessionId"])
    candidate = cand_storage.get(record["candidateId"])

    session_data = _serialize_record(plan_session.model_dump()) if plan_session else None
    candidate_data = _serialize_record(candidate.model_dump()) if candidate else None

    return {
        "linkId": record["linkId"],
        "planSessionId": record["planSessionId"],
        "candidateId": record["candidateId"],
        "candidateIndex": record.get("candidateIndex"),
        "session": session_data,
        "candidate": candidate_data,
        "createdAt": record["createdAt"],
    }


__all__ = [
    "create_experiment",
    "create_plan_link",
    "get_artifact_storage",
    "get_dataset",
    "get_dataset_preview",
    "get_experiment",
    "get_figure",
    "get_metrics",
    "get_plan_link",
    "get_plan_link_context",
    "get_plan_package_storage",
    "ingest_metrics",
    "get_plan_candidate_storage",
    "get_plan_session_storage",
    "get_run_storage",
    "list_datasets",
    "list_experiments",
    "list_figures",
    "save_dataset",
    "save_figure_artifact",
    "update_experiment",
]
