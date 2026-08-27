"""Evidence-bound verification for inherited human acceptance conditions."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Dict, Iterable, List

from app.modules.review.audit_chain import append_event


VERIFICATION_STATUSES = {"pending", "passed", "failed", "waived"}


def _hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _condition_id(feedback_hash: str, decision_id: str, index: int, condition: str) -> str:
    digest = _hash({
        "feedbackHash": feedback_hash,
        "decisionId": decision_id,
        "index": index,
        "condition": condition,
    }).removeprefix("sha256:")
    return f"hfc_{digest[:16]}"


def inherited_human_conditions(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    feedback = record.get("inheritedHumanFeedback") or {}
    feedback_hash = str(feedback.get("feedbackHash") or "")
    conditions: List[Dict[str, Any]] = []
    for item in feedback.get("items") or []:
        decision_id = str(item.get("decisionId") or "legacy")
        for index, value in enumerate(item.get("conditions") or []):
            condition = str(value or "").strip()
            if not condition:
                continue
            conditions.append({
                "conditionId": _condition_id(feedback_hash, decision_id, index, condition),
                "feedbackHash": feedback_hash,
                "decisionId": decision_id,
                "stage": str(item.get("stage") or "plan"),
                "condition": condition,
                "targetSections": [
                    str(section).strip()
                    for section in item.get("targetSections") or []
                    if str(section).strip()
                ],
            })
    return conditions


def condition_subject_hash(record: Dict[str, Any], condition: Dict[str, Any]) -> str:
    return _hash({
        "condition": condition,
        "runId": record.get("runId"),
        "sourceArtifacts": record.get("sourceArtifacts") or {},
        "benchmarkFingerprint": record.get("benchmarkFingerprint"),
        "metricSnapshot": record.get("metricSnapshot") or [],
        "qualityAssessment": record.get("qualityAssessment") or {},
    })


def human_condition_verification_state(record: Dict[str, Any]) -> Dict[str, Any]:
    stored = record.get("humanFeedbackVerifications") or {}
    entries: List[Dict[str, Any]] = []
    for condition in inherited_human_conditions(record):
        current_hash = condition_subject_hash(record, condition)
        item = dict(stored.get(condition["conditionId"]) or {})
        stored_status = str(item.get("status") or "pending")
        stale = bool(item.get("subjectHash") and item.get("subjectHash") != current_hash)
        item.update({
            **condition,
            "status": "pending" if stale else stored_status,
            "storedStatus": stored_status,
            "subjectHash": current_hash,
            "stale": stale,
            "evidenceArtifactIds": list(item.get("evidenceArtifactIds") or []),
            "history": list(item.get("history") or []),
        })
        entries.append(item)
    unresolved = [
        item for item in entries if item["status"] not in {"passed", "waived"}
    ]
    return {
        "required": bool(entries),
        "allResolved": not unresolved,
        "total": len(entries),
        "passed": sum(item["status"] == "passed" for item in entries),
        "waived": sum(item["status"] == "waived" for item in entries),
        "unresolved": len(unresolved),
        "conditions": entries,
    }


def decide_human_condition_verification(
    record: Dict[str, Any],
    *,
    condition_id: str,
    status: str,
    verifier_role: str,
    verifier_id: str,
    rationale: str,
    evidence_artifact_ids: Iterable[str] = (),
) -> Dict[str, Any]:
    if status not in VERIFICATION_STATUSES or status == "pending":
        raise ValueError("Verification must be passed, failed, or waived")
    if not verifier_role.strip() or not verifier_id.strip():
        raise ValueError("Verifier role and identity are required")
    if len(rationale.strip()) < 3:
        raise ValueError("A concrete verification rationale is required")

    state = human_condition_verification_state(record)
    current = next(
        (item for item in state["conditions"] if item["conditionId"] == condition_id),
        None,
    )
    if current is None:
        raise ValueError(f"Human feedback condition '{condition_id}' not found")
    evidence_ids = [
        str(item).strip() for item in evidence_artifact_ids if str(item).strip()
    ]
    allowed_evidence_ids = set((record.get("sourceArtifacts") or {}).values())
    unknown = [item for item in evidence_ids if item not in allowed_evidence_ids]
    if unknown:
        raise ValueError(
            "Verification evidence must reference this audit's source artifacts: "
            + ", ".join(unknown)
        )
    if status == "passed" and not evidence_ids:
        raise ValueError("Passed conditions require at least one source artifact as evidence")

    decided_at = datetime.now(UTC).isoformat()
    decision = {
        "verificationId": f"hfv_{uuid.uuid4().hex[:12]}",
        "status": status,
        "subjectHash": current["subjectHash"],
        "verifierRole": verifier_role.strip(),
        "verifierId": verifier_id.strip(),
        "rationale": rationale.strip(),
        "evidenceArtifactIds": evidence_ids,
        "decidedAt": decided_at,
    }
    history = append_event(current.get("history") or [], decision)
    decision = history[-1]
    stored = dict(record.get("humanFeedbackVerifications") or {})
    stored[condition_id] = {
        **decision,
        "conditionId": condition_id,
        "history": history,
    }
    return stored


def require_human_conditions_resolved(record: Dict[str, Any]) -> Dict[str, Any]:
    state = human_condition_verification_state(record)
    if not state["allResolved"]:
        raise ValueError(
            f"Resolve all inherited human acceptance conditions before this action "
            f"({state['unresolved']} unresolved)"
        )
    return state
