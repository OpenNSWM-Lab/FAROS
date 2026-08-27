"""Turn ReviewX human decisions into executable iteration feedback."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List


ACTIONABLE_STATUSES = {"changes_requested", "rejected"}
APPLICATION_STATUSES = {"applied_to_plan", "queued_for_iteration"}


def _dedupe(values: Iterable[Any]) -> List[str]:
    result: List[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def human_feedback_bundle(record: Dict[str, Any]) -> Dict[str, Any]:
    """Collect every explicit human change request into one stable bundle."""

    items: List[Dict[str, Any]] = []
    signoffs = record.get("humanSignoffs") or {}
    for stage in ("plan", "repair", "conclusion"):
        history = (signoffs.get(stage) or {}).get("history") or []
        for index, decision in enumerate(history):
            status = str(decision.get("status") or "")
            if status not in ACTIONABLE_STATUSES:
                continue
            item = {
                "decisionId": str(
                    decision.get("decisionId")
                    or f"legacy-{stage}-{index + 1}"
                ),
                "stage": stage,
                "status": status,
                "reviewerRole": str(decision.get("reviewerRole") or "human_reviewer"),
                "rationale": str(decision.get("rationale") or "").strip(),
                "conditions": _dedupe(decision.get("conditions") or []),
                "targetSections": _dedupe(decision.get("targetSections") or []),
                "decidedAt": decision.get("decidedAt"),
            }
            items.append(item)

    default_targets = (record.get("iterationDecision") or {}).get("targetSections") or []
    target_sections = _dedupe(
        [target for item in items for target in item["targetSections"]]
        or default_targets
    )
    required_actions = _dedupe(
        action
        for item in items
        for action in [item["rationale"], *item["conditions"]]
    )
    content = {
        "feedbackId": record.get("id"),
        "runId": record.get("runId"),
        "items": items,
        "targetSections": target_sections,
        "requiredActions": required_actions,
    }
    return {**content, "feedbackHash": _hash(content)}


def human_feedback_state(record: Dict[str, Any]) -> Dict[str, Any]:
    bundle = human_feedback_bundle(record)
    application = dict(record.get("humanFeedbackApplication") or {})
    requires_application = bool(bundle["items"])
    applied = (
        not requires_application
        or (
            application.get("feedbackHash") == bundle["feedbackHash"]
            and application.get("status") in APPLICATION_STATUSES
        )
    )
    return {
        **bundle,
        "requiresApplication": requires_application,
        "applied": applied,
        "staleApplication": bool(application) and not applied,
        "application": application or None,
    }


def require_human_feedback_applied(record: Dict[str, Any]) -> Dict[str, Any]:
    state = human_feedback_state(record)
    if state["requiresApplication"] and not state["applied"]:
        reason = "changed after application" if state["staleApplication"] else "not applied"
        raise ValueError(
            f"Apply the current human feedback before approving or creating an iteration ({reason})"
        )
    return state


def iteration_decision_with_human_feedback(
    record: Dict[str, Any],
    decision: Dict[str, Any],
) -> Dict[str, Any]:
    """Attach an applied human bundle to the immutable child-run contract."""

    state = require_human_feedback_applied(record)
    if not state["requiresApplication"]:
        return dict(decision)

    merged = dict(decision)
    merged["targetSections"] = _dedupe([
        *(merged.get("targetSections") or []),
        *state["targetSections"],
    ])
    merged["nextActions"] = _dedupe([
        *(merged.get("nextActions") or []),
        *state["requiredActions"],
    ])
    merged["humanFeedback"] = {
        "feedbackHash": state["feedbackHash"],
        "targetSections": state["targetSections"],
        "requiredActions": state["requiredActions"],
        "items": state["items"],
        "application": state["application"],
    }
    return merged


def human_feedback_comment(state: Dict[str, Any]) -> str:
    lines = ["ReviewX human feedback (must be addressed in the next plan):"]
    for item in state.get("items") or []:
        lines.append(
            f"[{item['stage']}/{item['status']}] {item['rationale']}"
        )
        lines.extend(f"Acceptance condition: {condition}" for condition in item["conditions"])
    return "\n".join(lines)
