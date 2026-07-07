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
    }


def _latest_reviewx_for_paper(paper_id: str) -> Optional[Dict[str, Any]]:
    candidates = [
        review for review in _list_reviews(paper_id=paper_id)
        if review.get("reviewKind") == "reviewx" and review.get("status") == "completed"
    ]
    candidates.sort(key=lambda review: review.get("updatedAt") or review.get("createdAt") or "", reverse=True)
    return candidates[0] if candidates else None


def _reviewx_history_summary(review: Dict[str, Any]) -> Dict[str, Any]:
    summary = review.get("jsonReport", {}).get("summary", {}) if isinstance(review.get("jsonReport"), dict) else {}
    model_trace = review.get("modelTrace") or {}
    llm_routing = model_trace.get("llmRouting", {}) if isinstance(model_trace, dict) else {}
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
        "severityCounts": summary.get("severityCounts", {}),
        "llmCallCount": len(model_trace.get("llmCalls", []) or []) if isinstance(model_trace, dict) else 0,
        "llmSkipped": llm_routing.get("skipped"),
        "llmSkipReason": llm_routing.get("skipReason"),
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


@router.get("/reviewx/latest")
async def get_latest_reviewx_endpoint(paperId: str):
    review = _latest_reviewx_for_paper(paperId)
    if not review:
        raise HTTPException(status_code=404, detail=f"No completed ReviewX run for paper '{paperId}'")
    return review


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

    created = []
    for idx in req.actionItemIndices:
        if idx < 0 or idx >= len(action_items):
            continue
        item = action_items[idx]
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
            "acceptanceCriteria": item.get("acceptanceCriteria", []),
        })
        created.append(ir)

    return {
        "reviewId": review_id,
        "appliedCount": len(created),
        "requests": created,
    }
