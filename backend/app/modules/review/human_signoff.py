"""Hash-bound human oversight for ReviewX experiment feedback."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Dict, Iterable

from app.modules.review.audit_chain import append_event, record_audit_integrity


SIGNOFF_STAGES = ("plan", "repair", "conclusion")
SIGNOFF_STATUSES = ("pending", "approved", "rejected", "changes_requested")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _blocker_count(record: Dict[str, Any]) -> int:
    findings = (record.get("qualityAssessment") or {}).get("findings") or []
    return sum(str(item.get("severity") or "").lower() == "blocker" for item in findings)


def signoff_required(record: Dict[str, Any], stage: str) -> bool:
    if stage not in SIGNOFF_STAGES:
        raise ValueError(f"Unknown signoff stage: {stage}")
    if stage in {"plan", "conclusion"}:
        return True
    decision = str((record.get("iterationDecision") or {}).get("decision") or "")
    gate = str((record.get("qualityAssessment") or {}).get("gateStatus") or "").lower()
    return decision in {"revise_plan", "rerun_experiment", "needs_human"} or gate == "fail" or _blocker_count(record) > 0


def signoff_subject_hash(record: Dict[str, Any], stage: str) -> str:
    common = {
        "feedbackId": record.get("id"),
        "runId": record.get("runId"),
        "questionId": record.get("questionId"),
        "benchmarkFingerprint": record.get("benchmarkFingerprint"),
        "sourceArtifacts": record.get("sourceArtifacts") or {},
        "reviewPurpose": record.get("reviewPurpose") or "scientific_review",
        "publicationEligible": record.get("publicationEligible", True),
    }
    if stage == "plan":
        subject = {
            **common,
            "iterationDecision": record.get("iterationDecision") or {},
            "planPackageId": record.get("planPackageId"),
            "planRevision": record.get("planRevision"),
            "humanFeedbackApplication": record.get("humanFeedbackApplication"),
        }
    elif stage == "repair":
        subject = {
            **common,
            "qualityAssessment": record.get("qualityAssessment") or {},
            "iterationDecision": record.get("iterationDecision") or {},
            "planRevision": record.get("planRevision"),
            "metricSnapshot": record.get("metricSnapshot") or [],
            "humanFeedbackApplication": record.get("humanFeedbackApplication"),
        }
    elif stage == "conclusion":
        subject = {
            **common,
            "qualityAssessment": record.get("qualityAssessment") or {},
            "iterationDecision": record.get("iterationDecision") or {},
            "metricSnapshot": record.get("metricSnapshot") or [],
            "loopProgress": record.get("loopProgress"),
            "planRevision": record.get("planRevision"),
            "humanFeedbackApplication": record.get("humanFeedbackApplication"),
            "inheritedHumanFeedback": record.get("inheritedHumanFeedback"),
            "humanFeedbackVerifications": record.get("humanFeedbackVerifications"),
        }
    else:
        raise ValueError(f"Unknown signoff stage: {stage}")
    return _hash(subject)


def initialize_human_signoffs(record: Dict[str, Any]) -> Dict[str, Any]:
    created_at = str(record.get("createdAt") or _now())
    return {
        stage: {
            "stage": stage,
            "status": "pending",
            "required": signoff_required(record, stage),
            "artifactHash": signoff_subject_hash(record, stage),
            "reviewerRole": None,
            "reviewerId": None,
            "rationale": "",
            "conditions": [],
            "createdAt": created_at,
            "decidedAt": None,
            "stale": False,
            "history": [],
        }
        for stage in SIGNOFF_STAGES
    }


def human_signoff_state(record: Dict[str, Any]) -> Dict[str, Any]:
    stored = record.get("humanSignoffs") or initialize_human_signoffs(record)
    result: Dict[str, Any] = {}
    for stage in SIGNOFF_STAGES:
        current_hash = signoff_subject_hash(record, stage)
        item = dict(stored.get(stage) or {})
        stored_status = str(item.get("status") or "pending")
        stale = bool(item.get("artifactHash") and item.get("artifactHash") != current_hash)
        item.update({
            "stage": stage,
            "status": "pending" if stale else stored_status,
            "storedStatus": stored_status,
            "required": signoff_required(record, stage),
            "artifactHash": current_hash,
            "stale": stale,
            "history": list(item.get("history") or []),
        })
        result[stage] = item
    return result


def decide_human_signoff(
    record: Dict[str, Any],
    *,
    stage: str,
    status: str,
    reviewer_role: str,
    reviewer_id: str,
    rationale: str,
    conditions: Iterable[str] = (),
    target_sections: Iterable[str] = (),
) -> Dict[str, Any]:
    if stage not in SIGNOFF_STAGES:
        raise ValueError(f"Unknown signoff stage: {stage}")
    if status not in SIGNOFF_STATUSES or status == "pending":
        raise ValueError("A signoff decision must be approved, rejected, or changes_requested")
    if not reviewer_role.strip() or not reviewer_id.strip():
        raise ValueError("Reviewer role and reviewer identity are required")
    if len(rationale.strip()) < 3:
        raise ValueError("A concrete signoff rationale is required")
    if status == "approved":
        from app.modules.review.human_feedback import require_human_feedback_applied

        require_human_feedback_applied(record)
    if status == "approved" and stage in {"repair", "conclusion"}:
        try:
            require_human_signoff(record, "plan")
            if stage == "conclusion" and signoff_required(record, "repair"):
                require_human_signoff(record, "repair")
        except ValueError as exc:
            raise ValueError(
                f"Complete the preceding human signoffs before approving {stage}: {exc}"
            ) from exc
    if stage == "conclusion" and status == "approved":
        decision = str((record.get("iterationDecision") or {}).get("decision") or "")
        if decision != "accept_results":
            raise ValueError(
                "Conclusions can only be approved for an accepted final experiment result"
            )
        from app.modules.review.human_feedback_verification import (
            require_human_conditions_resolved,
        )

        require_human_conditions_resolved(record)
        gate = str((record.get("qualityAssessment") or {}).get("gateStatus") or "").lower()
        if gate == "fail" or _blocker_count(record) > 0:
            raise ValueError("Resolve ReviewX blockers and rerun the audit before approving conclusions")
        if record.get("enforceReviewerSeparation") and record.get("reviewPurpose") != "technical_test":
            plan_reviewer = (human_signoff_state(record).get("plan") or {}).get("reviewerId")
            if plan_reviewer and plan_reviewer == reviewer_id.strip():
                raise ValueError("Conclusion reviewer must differ from the plan reviewer")

    signoffs = human_signoff_state(record)
    current = dict(signoffs[stage])
    decided_at = _now()
    decision = {
        "decisionId": f"hsd_{uuid.uuid4().hex[:12]}",
        "status": status,
        "artifactHash": current["artifactHash"],
        "reviewerRole": reviewer_role.strip(),
        "reviewerId": reviewer_id.strip(),
        "rationale": rationale.strip(),
        "conditions": [str(item).strip() for item in conditions if str(item).strip()],
        "targetSections": [
            str(item).strip() for item in target_sections if str(item).strip()
        ],
        "decidedAt": decided_at,
    }
    history = append_event(current.get("history") or [], decision)
    decision = history[-1]
    current.update({
        **decision,
        "stage": stage,
        "required": signoff_required(record, stage),
        "storedStatus": status,
        "stale": False,
        "history": history,
    })
    signoffs[stage] = current
    return signoffs


def require_human_signoff(record: Dict[str, Any], stage: str) -> Dict[str, Any]:
    signoff = human_signoff_state(record)[stage]
    if signoff["status"] != "approved":
        reason = "stale after artifact changes" if signoff.get("stale") else signoff["status"]
        raise ValueError(f"Human {stage} signoff is required before this action ({reason})")
    return signoff


def publication_ready(record: Dict[str, Any]) -> bool:
    if record.get("publicationEligible", True) is not True:
        return False
    if not record_audit_integrity(record)["valid"]:
        return False
    decision = str((record.get("iterationDecision") or {}).get("decision") or "")
    if decision != "accept_results":
        return False
    try:
        from app.modules.review.human_feedback import require_human_feedback_applied

        require_human_feedback_applied(record)
        require_human_signoff(record, "plan")
        if signoff_required(record, "repair"):
            require_human_signoff(record, "repair")
        require_human_signoff(record, "conclusion")
        from app.modules.review.human_feedback_verification import (
            require_human_conditions_resolved,
        )

        require_human_conditions_resolved(record)
    except ValueError:
        return False
    gate = str((record.get("qualityAssessment") or {}).get("gateStatus") or "").lower()
    return gate != "fail" and _blocker_count(record) == 0
