"""
Review Storage — JSON-file persistence for paper reviews and improvement requests.
"""

import json
import os
import uuid
import logging
from datetime import UTC, datetime
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REVIEWS_DIR = os.path.join(_BASE_DIR, "data", "reviews")
IMPROVEMENT_REQUESTS_DIR = os.path.join(_BASE_DIR, "data", "improvement_requests")
os.makedirs(REVIEWS_DIR, exist_ok=True)
os.makedirs(IMPROVEMENT_REQUESTS_DIR, exist_ok=True)


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def create_review(data: Dict[str, Any]) -> Dict[str, Any]:
    review_id = _gen_id("rev")
    now = _utcnow_iso()
    record = {
        "id": review_id,
        "paperId": data.get("paperId"),
        "reviewerProfile": data.get("reviewerProfile", "senior_reviewer"),
        "providerName": data.get("providerName", "moonshot"),
        "model": data.get("model", "moonshot-v1-8k"),
        "status": "pending",
        "scoreSuggestion": None,
        "jsonReport": None,
        "markdownReport": None,
        "actionItems": [],
        "createdAt": now,
        "updatedAt": now,
    }
    review_dir = os.path.join(REVIEWS_DIR, review_id)
    os.makedirs(review_dir, exist_ok=True)
    _save_record(review_id, record)
    return record


def get_review(review_id: str) -> Optional[Dict[str, Any]]:
    path = os.path.join(REVIEWS_DIR, review_id, "meta.json")
    if not os.path.isfile(path):
        return None
    with open(path) as f:
        return json.load(f)


def update_review(review_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    record = get_review(review_id)
    if not record:
        return None
    record.update(updates)
    record["updatedAt"] = _utcnow_iso()
    _save_record(review_id, record)
    return record


def list_reviews(paper_id: Optional[str] = None) -> List[Dict[str, Any]]:
    results = []
    if not os.path.isdir(REVIEWS_DIR):
        return results
    for name in sorted(os.listdir(REVIEWS_DIR), reverse=True):
        meta = os.path.join(REVIEWS_DIR, name, "meta.json")
        if os.path.isfile(meta):
            try:
                with open(meta) as f:
                    data = json.load(f)
                if paper_id and data.get("paperId") != paper_id:
                    continue
                results.append(data)
            except Exception:
                pass
    return results


def _save_record(review_id: str, record: Dict):
    review_dir = os.path.join(REVIEWS_DIR, review_id)
    os.makedirs(review_dir, exist_ok=True)
    with open(os.path.join(review_dir, "meta.json"), "w") as f:
        json.dump(record, f, indent=2, default=str)


# ── Improvement Requests ──

def create_improvement_request(data: Dict[str, Any]) -> Dict[str, Any]:
    req_id = _gen_id("impr")
    now = _utcnow_iso()
    record = {
        "id": req_id,
        "reviewId": data.get("reviewId"),
        "paperId": data.get("paperId"),
        "targetModule": data.get("targetModule", "papers"),  # papers | experiments | code
        "actionItemIndex": data.get("actionItemIndex"),
        "description": data.get("description", ""),
        "severity": data.get("severity", "MAJOR"),
        "sectionPointer": data.get("sectionPointer", ""),
        "suggestedEdit": data.get("suggestedEdit", ""),
        "status": "pending",  # pending | in_progress | completed | dismissed
        "createdAt": now,
        "updatedAt": now,
    }
    req_dir = os.path.join(IMPROVEMENT_REQUESTS_DIR, req_id)
    os.makedirs(req_dir, exist_ok=True)
    with open(os.path.join(req_dir, "meta.json"), "w") as f:
        json.dump(record, f, indent=2, default=str)

    # Index under review
    review_dir = os.path.join(REVIEWS_DIR, data.get("reviewId", "unknown"))
    if os.path.isdir(review_dir):
        idx_path = os.path.join(review_dir, "requests.json")
        idx = []
        if os.path.isfile(idx_path):
            with open(idx_path) as f:
                idx = json.load(f)
        idx.append(record)
        with open(idx_path, "w") as f:
            json.dump(idx, f, indent=2, default=str)

    return record


def list_improvement_requests(review_id: Optional[str] = None, paper_id: Optional[str] = None, target_module: Optional[str] = None) -> List[Dict[str, Any]]:
    results = []
    if not os.path.isdir(IMPROVEMENT_REQUESTS_DIR):
        return results
    for name in sorted(os.listdir(IMPROVEMENT_REQUESTS_DIR), reverse=True):
        meta = os.path.join(IMPROVEMENT_REQUESTS_DIR, name, "meta.json")
        if os.path.isfile(meta):
            try:
                with open(meta) as f:
                    data = json.load(f)
                if review_id and data.get("reviewId") != review_id:
                    continue
                if paper_id and data.get("paperId") != paper_id:
                    continue
                if target_module and data.get("targetModule") != target_module:
                    continue
                results.append(data)
            except Exception:
                pass
    return results


def get_improvement_request(req_id: str) -> Optional[Dict[str, Any]]:
    path = os.path.join(IMPROVEMENT_REQUESTS_DIR, req_id, "meta.json")
    if not os.path.isfile(path):
        return None
    with open(path) as f:
        return json.load(f)


def update_improvement_request(req_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    record = get_improvement_request(req_id)
    if not record:
        return None
    record.update(updates)
    record["updatedAt"] = _utcnow_iso()
    req_dir = os.path.join(IMPROVEMENT_REQUESTS_DIR, req_id)
    with open(os.path.join(req_dir, "meta.json"), "w") as f:
        json.dump(record, f, indent=2, default=str)
    return record
