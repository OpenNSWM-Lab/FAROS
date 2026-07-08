"""
Reviews API — Paper review generation and feedback loop.

Endpoints:
- POST /reviews              (create + generate review for a paper)
- GET  /reviews?paperId=     (list reviews)
- GET  /reviews/{id}         (get review detail)
- POST /reviews/{id}/apply   (apply selected action items as improvement requests)
- GET  /reviews/requests     (list improvement requests)
"""

import logging
import threading
from typing import Any, Dict, Optional, List

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.core.settings import get_settings
from app.modules.review.storage import (
    create_review as _create_review, get_review as _get_review,
    list_reviews as _list_reviews, update_review as _update_review,
    create_improvement_request, list_improvement_requests as _list_improvement_requests,
    update_improvement_request as _update_improvement_request,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reviews", tags=["reviews"])


class CreateReviewRequest(BaseModel):
    paperId: str
    reviewerProfile: str = "senior_reviewer"
    providerName: Optional[str] = None
    model: Optional[str] = None


class ApplyFeedbackRequest(BaseModel):
    actionItemIndices: List[int] = Field(..., description="0-based indices of action items to apply")


class RunReviewXRequest(BaseModel):
    paperId: str
    providerName: Optional[str] = None
    model: Optional[str] = None
    budgetMode: str = "balanced"


class UpdateImprovementRequest(BaseModel):
    status: str


def _reviewx_finding_to_dto(finding: Dict[str, Any], review: Dict[str, Any]) -> Dict[str, Any]:
    evidence_by_id = {ev.get("id"): ev for ev in review.get("evidence", [])}
    evidence_lines = []
    for evidence_id in finding.get("evidenceIds", []) or []:
        ev = evidence_by_id.get(evidence_id)
        if not ev:
            continue
        evidence_lines.append(f"{evidence_id}: {ev.get('summary', '')} ({ev.get('sourcePath', '')})")

    return {
        "id": finding.get("id"),
        "paperId": finding.get("paperId") or review.get("paperId"),
        "type": "consistency",
        "severity": str(finding.get("severity", "major")).lower(),
        "title": finding.get("title", "ReviewX finding"),
        "description": finding.get("description", ""),
        "evidence": "\n".join(evidence_lines) if evidence_lines else "No direct evidence linked; this is an evidence gap.",
        "suggestedFix": finding.get("suggestedFix", ""),
        "location": finding.get("location"),
        "riskType": finding.get("riskType"),
        "claimId": finding.get("claimId"),
        "evidenceIds": finding.get("evidenceIds", []),
        "targetModule": finding.get("targetModule", "papers"),
        "confidence": finding.get("confidence"),
        "supportStatus": finding.get("supportStatus"),
        "verifierIds": finding.get("verifierIds", []),
        "reviewerDecision": finding.get("reviewerDecision"),
        "reviewerAssessment": finding.get("reviewerAssessment"),
        "reviewerModel": finding.get("reviewerModel"),
        "cemCalibration": finding.get("cemCalibration", {}),
        "revisionRequestIds": finding.get("revisionRequestIds", []),
        "revisionStatus": finding.get("revisionStatus"),
    }


def _latest_reviewx_for_paper(paper_id: str) -> Optional[Dict[str, Any]]:
    candidates = [
        review for review in _list_reviews(paper_id=paper_id)
        if review.get("reviewKind") == "reviewx" and review.get("status") == "completed"
    ]
    candidates.sort(key=lambda review: review.get("updatedAt") or review.get("createdAt") or "", reverse=True)
    return candidates[0] if candidates else None


def _completed_reviewx_for_paper(paper_id: str) -> List[Dict[str, Any]]:
    reviews = [
        review for review in _list_reviews(paper_id=paper_id)
        if review.get("reviewKind") == "reviewx" and review.get("status") == "completed"
    ]
    reviews.sort(key=lambda review: review.get("updatedAt") or review.get("createdAt") or "", reverse=True)
    return reviews


def _reviewx_history_summary(review: Dict[str, Any]) -> Dict[str, Any]:
    summary = review.get("jsonReport", {}).get("summary", {}) if isinstance(review.get("jsonReport"), dict) else {}
    model_trace = review.get("modelTrace") or {}
    llm_routing = model_trace.get("llmRouting", {}) if isinstance(model_trace, dict) else {}
    support_counts = summary.get("supportCounts", {})
    mismatch = summary.get("mismatch", {}) or {}
    return {
        "id": review.get("id"),
        "paperId": review.get("paperId"),
        "status": review.get("status"),
        "budgetMode": review.get("budgetMode", "balanced"),
        "providerName": review.get("providerName"),
        "model": review.get("model"),
        "scoreSuggestion": review.get("scoreSuggestion"),
        "createdAt": review.get("createdAt"),
        "updatedAt": review.get("updatedAt"),
        "claimCount": summary.get("claimCount", len(review.get("claims", []) or [])),
        "evidenceCount": summary.get("evidenceCount", len(review.get("evidence", []) or [])),
        "findingCount": summary.get("findingCount", len(review.get("findings", []) or [])),
        "riskQuestionCount": summary.get("riskQuestionCount", len(review.get("riskTree", []) or [])),
        "severityCounts": summary.get("severityCounts", {}),
        "verificationCount": summary.get("verificationCount", len(review.get("verifications", []) or [])),
        "supportCounts": support_counts,
        "mismatch": mismatch,
        "llmCallCount": len(model_trace.get("llmCalls", []) or []) if isinstance(model_trace, dict) else 0,
        "llmSkipped": llm_routing.get("skipped"),
        "llmSkipReason": llm_routing.get("skipReason"),
    }


def _reviewx_compare_metrics(review: Dict[str, Any]) -> Dict[str, Any]:
    summary = review.get("jsonReport", {}).get("summary", {}) if isinstance(review.get("jsonReport"), dict) else {}
    severity_counts = summary.get("severityCounts", {}) or {}
    support_counts = summary.get("supportCounts", {}) or {}
    mismatch = summary.get("mismatch", {}) or {}
    requests = _list_improvement_requests(review_id=review.get("id"))
    return {
        "reviewId": review.get("id"),
        "updatedAt": review.get("updatedAt"),
        "score": review.get("scoreSuggestion"),
        "findingCount": summary.get("findingCount", len(review.get("findings", []) or [])),
        "blockerCount": severity_counts.get("blocker", 0),
        "majorCount": severity_counts.get("major", 0),
        "unsupportedCount": support_counts.get("unsupported", 0),
        "contradictedCount": support_counts.get("contradicted", 0),
        "weaklySupportedCount": support_counts.get("weakly_supported", 0),
        "supportedCount": support_counts.get("supported", 0),
        "coverage": summary.get("coverage", 0),
        "verificationCount": summary.get("verificationCount", len(review.get("verifications", []) or [])),
        "riskQuestionCount": summary.get("riskQuestionCount", len(review.get("riskTree", []) or [])),
        "requestCount": len(requests),
        "resolvedRequestCount": len([req for req in requests if req.get("status") in {"resolved", "verified", "completed"}]),
        "meanMismatch": mismatch.get("meanMismatch", 0),
        "maxMismatch": mismatch.get("maxMismatch", 0),
        "highMismatchClaimCount": mismatch.get("highMismatchClaimCount", 0),
    }


def _metric_delta(before: Dict[str, Any], after: Dict[str, Any], key: str) -> Any:
    left = before.get(key)
    right = after.get(key)
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return round(right - left, 3)
    return None


def _finding_signature(finding: Dict[str, Any]) -> str:
    return "|".join([
        str(finding.get("claimId") or ""),
        str(finding.get("riskType") or ""),
        str(finding.get("targetModule") or ""),
        str(finding.get("title") or ""),
    ])


def _finding_summary(finding: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": finding.get("id"),
        "title": finding.get("title"),
        "severity": finding.get("severity"),
        "riskType": finding.get("riskType"),
        "claimId": finding.get("claimId"),
        "targetModule": finding.get("targetModule"),
        "supportStatus": finding.get("supportStatus"),
        "confidence": finding.get("confidence"),
    }


def _reviewx_compare_payload(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    before_metrics = _reviewx_compare_metrics(before)
    after_metrics = _reviewx_compare_metrics(after)
    before_findings = {_finding_signature(finding): finding for finding in before.get("findings", []) or []}
    after_findings = {_finding_signature(finding): finding for finding in after.get("findings", []) or []}
    before_keys = set(before_findings)
    after_keys = set(after_findings)
    metric_keys = [
        "score",
        "findingCount",
        "blockerCount",
        "majorCount",
        "unsupportedCount",
        "contradictedCount",
        "weaklySupportedCount",
        "supportedCount",
        "coverage",
        "requestCount",
        "resolvedRequestCount",
        "meanMismatch",
        "maxMismatch",
        "highMismatchClaimCount",
    ]
    return {
        "paperId": after.get("paperId") or before.get("paperId"),
        "before": before_metrics,
        "after": after_metrics,
        "delta": {key: _metric_delta(before_metrics, after_metrics, key) for key in metric_keys},
        "resolvedFindings": [_finding_summary(before_findings[key]) for key in sorted(before_keys - after_keys)[:20]],
        "newFindings": [_finding_summary(after_findings[key]) for key in sorted(after_keys - before_keys)[:20]],
        "persistentFindings": [_finding_summary(after_findings[key]) for key in sorted(before_keys & after_keys)[:20]],
    }


def _reviewx_eval_record(review: Dict[str, Any]) -> Dict[str, Any]:
    summary = review.get("jsonReport", {}).get("summary", {}) if isinstance(review.get("jsonReport"), dict) else {}
    model_trace = review.get("modelTrace") or {}
    mismatch_report = review.get("mismatchReport") or {}
    evidence_graph = review.get("evidenceGraph") or {}
    requests = _list_improvement_requests(review_id=review.get("id"))
    return {
        "schemaVersion": "reviewx_eval_v1",
        "reviewId": review.get("id"),
        "paperId": review.get("paperId"),
        "createdAt": review.get("createdAt"),
        "updatedAt": review.get("updatedAt"),
        "budgetMode": review.get("budgetMode", "balanced"),
        "providerName": review.get("providerName"),
        "model": review.get("model"),
        "scoreSuggestion": review.get("scoreSuggestion"),
        "summary": summary,
        "mismatchAggregate": mismatch_report.get("aggregate", summary.get("mismatch", {})),
        "claimScores": mismatch_report.get("claimScores", []),
        "graphStats": {
            "nodeCount": evidence_graph.get("nodeCount", 0),
            "edgeCount": evidence_graph.get("edgeCount", 0),
        },
        "findings": [_finding_summary(finding) for finding in review.get("findings", []) or []],
        "actionItems": review.get("actionItems", []) or [],
        "improvementRequests": requests,
        "modelTrace": {
            "routingMode": model_trace.get("routingMode"),
            "localRulePasses": model_trace.get("localRulePasses", []),
            "llmCallCount": len(model_trace.get("llmCalls", []) or []),
            "estimatedTokenCost": model_trace.get("estimatedTokenCost", 0),
            "llmRouting": model_trace.get("llmRouting", {}),
        },
        "metrics": _reviewx_compare_metrics(review),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_review_endpoint(req: CreateReviewRequest):
    """Create a review and immediately start generation in background."""
    from app.modules.review.storage import get_paper
    paper = get_paper(req.paperId)
    if not paper:
        raise HTTPException(status_code=404, detail=f"Paper '{req.paperId}' not found")

    settings = get_settings()
    provider_name = req.providerName or settings.get_active_provider()
    model = req.model or settings.get_active_model(provider_name)
    record = _create_review(req.model_dump() | {"providerName": provider_name, "model": model})

    def _run():
        try:
            from app.modules.review.service import generate_review
            generate_review(record["id"])
        except Exception as e:
            logger.error(f"Review generation failed: {e}", exc_info=True)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return record


@router.get("")
async def list_reviews_endpoint(paperId: Optional[str] = None):
    reviews = _list_reviews(paper_id=paperId)
    return {"reviews": reviews, "total": len(reviews)}


@router.get("/requests")
async def list_improvement_requests_endpoint(
    reviewId: Optional[str] = None,
    paperId: Optional[str] = None,
    targetModule: Optional[str] = None,
):
    requests = _list_improvement_requests(
        review_id=reviewId, paper_id=paperId, target_module=targetModule,
    )
    return {"requests": requests, "total": len(requests)}


@router.patch("/requests/{request_id}")
async def update_improvement_request_endpoint(request_id: str, req: UpdateImprovementRequest):
    allowed_statuses = {"pending", "in_progress", "resolved", "verified", "dismissed", "completed"}
    if req.status not in allowed_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status '{req.status}'")
    updated = _update_improvement_request(request_id, {"status": req.status})
    if not updated:
        raise HTTPException(status_code=404, detail=f"Improvement request '{request_id}' not found")
    return updated


@router.post("/reviewx/run")
async def run_reviewx_endpoint(req: RunReviewXRequest):
    """Run deterministic evidence-grounded ReviewX and return frontend findings."""
    from app.modules.review.storage import get_paper
    paper = get_paper(req.paperId)
    if not paper:
        raise HTTPException(status_code=404, detail=f"Paper '{req.paperId}' not found")

    settings = get_settings()
    provider_name = req.providerName or settings.get_active_provider()
    model = req.model or settings.get_active_model(provider_name)
    record = _create_review({
        "paperId": req.paperId,
        "reviewerProfile": "reviewx_evidence_auditor",
        "providerName": provider_name,
        "model": model,
        "reviewKind": "reviewx",
        "budgetMode": req.budgetMode,
    })

    try:
        from app.modules.review.service import generate_reviewx
        review = generate_reviewx(record["id"])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"ReviewX failed: {str(exc)[:300]}")

    return [_reviewx_finding_to_dto(finding, review) for finding in review.get("findings", [])]


@router.get("/reviewx/findings")
async def list_reviewx_findings_endpoint(paperId: str):
    review = _latest_reviewx_for_paper(paperId)
    if not review:
        return []
    return [_reviewx_finding_to_dto(finding, review) for finding in review.get("findings", [])]


@router.get("/reviewx/history")
async def list_reviewx_history_endpoint(paperId: str):
    reviews = [
        review for review in _list_reviews(paper_id=paperId)
        if review.get("reviewKind") == "reviewx"
    ]
    reviews.sort(key=lambda review: review.get("updatedAt") or review.get("createdAt") or "", reverse=True)
    return {
        "reviews": [_reviewx_history_summary(review) for review in reviews],
        "total": len(reviews),
    }


@router.get("/reviewx/compare")
async def compare_reviewx_endpoint(
    paperId: str,
    baseReviewId: Optional[str] = None,
    targetReviewId: Optional[str] = None,
):
    reviews = _completed_reviewx_for_paper(paperId)
    if len(reviews) < 2 and not (baseReviewId and targetReviewId):
        raise HTTPException(status_code=404, detail="At least two completed ReviewX runs are required for comparison")

    by_id = {review.get("id"): review for review in reviews}
    target = by_id.get(targetReviewId) if targetReviewId else reviews[0]
    if not target:
        raise HTTPException(status_code=404, detail=f"Target ReviewX run '{targetReviewId}' not found")

    base = by_id.get(baseReviewId) if baseReviewId else None
    if not base:
        target_index = next((idx for idx, review in enumerate(reviews) if review.get("id") == target.get("id")), -1)
        if target_index >= 0 and target_index + 1 < len(reviews):
            base = reviews[target_index + 1]
        elif len(reviews) >= 2:
            base = reviews[1]
    if not base:
        raise HTTPException(status_code=404, detail="No earlier ReviewX run found for comparison")

    return _reviewx_compare_payload(base, target)


@router.get("/reviewx/latest")
async def get_latest_reviewx_endpoint(paperId: str):
    review = _latest_reviewx_for_paper(paperId)
    if not review:
        raise HTTPException(status_code=404, detail=f"No completed ReviewX run for paper '{paperId}'")
    return review


@router.get("/reviewx/eval-record")
async def get_reviewx_eval_record_endpoint(
    paperId: Optional[str] = None,
    reviewId: Optional[str] = None,
):
    if reviewId:
        review = _get_review(reviewId)
        if not review:
            raise HTTPException(status_code=404, detail=f"Review '{reviewId}' not found")
    elif paperId:
        review = _latest_reviewx_for_paper(paperId)
        if not review:
            raise HTTPException(status_code=404, detail=f"No completed ReviewX run for paper '{paperId}'")
    else:
        raise HTTPException(status_code=400, detail="paperId or reviewId is required")

    if review.get("reviewKind") != "reviewx":
        raise HTTPException(status_code=400, detail="Review is not a ReviewX run")
    return _reviewx_eval_record(review)


@router.get("/reviewx/{review_id}")
async def get_reviewx_endpoint(review_id: str):
    review = _get_review(review_id)
    if not review:
        raise HTTPException(status_code=404, detail=f"Review '{review_id}' not found")
    if review.get("reviewKind") != "reviewx":
        raise HTTPException(status_code=400, detail="Review is not a ReviewX run")
    return review


@router.get("/reviewx/{review_id}/findings")
async def get_reviewx_findings_endpoint(review_id: str):
    review = _get_review(review_id)
    if not review:
        raise HTTPException(status_code=404, detail=f"Review '{review_id}' not found")
    if review.get("reviewKind") != "reviewx":
        raise HTTPException(status_code=400, detail="Review is not a ReviewX run")
    return [_reviewx_finding_to_dto(finding, review) for finding in review.get("findings", [])]


@router.get("/{review_id}")
async def get_review_endpoint(review_id: str):
    record = _get_review(review_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Review '{review_id}' not found")
    return record


@router.post("/{review_id}/apply")
async def apply_feedback_endpoint(review_id: str, req: ApplyFeedbackRequest):
    """Apply selected action items as improvement requests for target modules."""
    review = _get_review(review_id)
    if not review:
        raise HTTPException(status_code=404, detail=f"Review '{review_id}' not found")
    if review.get("status") != "completed":
        raise HTTPException(status_code=400, detail="Review not completed yet")

    action_items = review.get("actionItems", [])
    if not action_items:
        raise HTTPException(status_code=400, detail="No action items in review")

    existing_requests = _list_improvement_requests(review_id=review_id)
    existing_action_indices = {
        req.get("actionItemIndex")
        for req in existing_requests
        if req.get("actionItemIndex") is not None
    }
    existing_finding_ids = {
        req.get("sourceFindingId")
        for req in existing_requests
        if req.get("sourceFindingId")
    }
    created = []
    skipped = []
    for idx in req.actionItemIndices:
        if idx < 0 or idx >= len(action_items):
            continue
        item = action_items[idx]
        if idx in existing_action_indices or item.get("sourceFindingId") in existing_finding_ids:
            skipped.append(idx)
            continue
        ir = create_improvement_request({
            "reviewId": review_id,
            "paperId": review.get("paperId"),
            "targetModule": item.get("targetModule", "papers"),
            "actionItemIndex": idx,
            "description": item.get("description", ""),
            "severity": item.get("severity", "MAJOR"),
            "sectionPointer": item.get("section", ""),
            "suggestedEdit": item.get("suggestedEdit", ""),
            "sourceFindingId": item.get("sourceFindingId"),
            "claimId": item.get("claimId"),
            "evidenceIds": item.get("evidenceIds", []),
            "riskType": item.get("riskType"),
            "confidence": item.get("confidence"),
            "supportStatus": item.get("supportStatus"),
            "verifierIds": item.get("verifierIds", []),
            "reviewerDecision": item.get("reviewerDecision"),
            "reviewerAssessment": item.get("reviewerAssessment"),
            "reviewerModel": item.get("reviewerModel"),
            "cemCalibration": item.get("cemCalibration", {}),
            "revisionRequestIds": item.get("revisionRequestIds", []),
            "revisionStatus": item.get("revisionStatus"),
            "acceptanceCriteria": item.get("acceptanceCriteria", []),
        })
        created.append(ir)
        existing_action_indices.add(idx)
        if item.get("sourceFindingId"):
            existing_finding_ids.add(item.get("sourceFindingId"))

    return {
        "reviewId": review_id,
        "appliedCount": len(created),
        "skippedCount": len(skipped),
        "requests": created,
    }
