"""Attach review-to-revision feedback signals to ReviewX findings."""

from __future__ import annotations

from typing import Any, Dict, List

from app.modules.review.reviewx_models import Finding
from app.modules.review.storage import list_improvement_requests


_STATUS_ADJUSTMENT = {
    "pending": 0.0,
    "in_progress": 0.08,
    "resolved": 0.22,
    "completed": 0.22,
    "verified": 0.35,
    "dismissed": 0.28,
}

_STATUS_RANK = {
    "verified": 5,
    "resolved": 4,
    "completed": 4,
    "dismissed": 3,
    "in_progress": 2,
    "pending": 1,
}


def attach_revision_feedback(paper_id: str, findings: List[Finding]) -> Dict[str, Any]:
    requests = list_improvement_requests(paper_id=paper_id)
    if not requests:
        return {"matchedRequestCount": 0, "statusCounts": {}}

    status_counts: Dict[str, int] = {}
    matched_count = 0
    for finding in findings:
        matches = _matching_requests(finding, requests)
        if not matches:
            continue
        matched_count += len(matches)
        best = max(matches, key=lambda item: _STATUS_RANK.get(str(item.get("status")), 0))
        status = str(best.get("status") or "pending")
        status_counts[status] = status_counts.get(status, 0) + 1
        finding.revisionStatus = status
        finding.revisionRequestIds = [str(item.get("id")) for item in matches if item.get("id")]
        finding.cemCalibration = {
            **finding.cemCalibration,
            "revisionStatus": status,
            "revisionAdjustment": _STATUS_ADJUSTMENT.get(status, 0.0),
            "revisionRequestIds": finding.revisionRequestIds,
        }

    return {
        "matchedRequestCount": matched_count,
        "statusCounts": status_counts,
    }


def _matching_requests(finding: Finding, requests: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    matches = []
    for request in requests:
        if request.get("sourceFindingId") == finding.id:
            matches.append(request)
            continue
        if finding.claimId and request.get("claimId") == finding.claimId:
            matches.append(request)
            continue
        if (
            not finding.claimId
            and request.get("riskType") == finding.riskType
            and request.get("targetModule") == finding.targetModule
        ):
            matches.append(request)
    return matches
