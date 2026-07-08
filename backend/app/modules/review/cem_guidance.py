"""Claim-Evidence Mismatch guidance for ReviewX.

CEM-Review treats claim-evidence mismatch as the shared control signal for
risk-tree expansion and budget-aware model routing.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List

from app.modules.review.reviewx_models import Finding, RiskNode


SHALLOW_THRESHOLD = 0.3
DEEP_THRESHOLD = 0.72


def annotate_risk_tree_with_mismatch(
    risk_tree: List[RiskNode],
    mismatch_report: Dict[str, Any],
) -> List[RiskNode]:
    claim_scores = _claim_scores_by_id(mismatch_report)
    nodes_by_id = {node.id: node for node in risk_tree}

    for node in risk_tree:
        direct_scores = [claim_scores[claim_id] for claim_id in node.claimIds if claim_id in claim_scores]
        if direct_scores:
            _apply_node_guidance(node, direct_scores)

    for node in reversed(risk_tree):
        children = [nodes_by_id[child_id] for child_id in node.children if child_id in nodes_by_id]
        child_scores = [child.mismatchScore for child in children if child.mismatchScore is not None]
        if child_scores:
            node.mismatchScore = round(max(child_scores), 3)
            node.riskScore = round(max(float(node.riskScore or 0), node.mismatchScore), 3)
            node.expansionPolicy = _policy_for_score(node.mismatchScore)
            node.assignedModel = _model_for_score(node.mismatchScore, node.assignedModel)
            node.status = _status_for_score(node.mismatchScore, node.status)
            node.mismatchDrivers = _merge_drivers([child.mismatchDrivers for child in children])

    return risk_tree


def build_cem_budget_plan(
    findings: List[Finding],
    mismatch_report: Dict[str, Any] | None,
    budget_mode: str,
) -> Dict[str, Any]:
    mode = (budget_mode or "balanced").lower()
    claim_scores = _claim_scores_by_id(mismatch_report or {})
    threshold = 0.35 if mode == "deep" else 0.62
    limit = 8 if mode == "deep" else 3
    if mode == "local_only":
        threshold = 1.1
        limit = 0

    allocations = []
    for finding in findings:
        claim_score = claim_scores.get(finding.claimId or "", {})
        mismatch = float(claim_score.get("mismatchScore", 0.0))
        dimensions = claim_score.get("dimensions", {}) if isinstance(claim_score.get("dimensions"), dict) else {}
        priority = _budget_priority(finding, mismatch, dimensions)
        allocation = _model_for_score(mismatch, "rules")
        selected = mode != "local_only" and priority >= threshold
        allocations.append({
            "findingId": finding.id,
            "claimId": finding.claimId,
            "priority": priority,
            "mismatchScore": round(mismatch, 3),
            "severity": finding.severity,
            "supportStatus": finding.supportStatus,
            "recommendedModel": allocation,
            "selected": selected,
            "drivers": _top_dimensions(dimensions),
        })

    allocations.sort(key=lambda item: (not item["selected"], -item["priority"], -item["mismatchScore"], item["findingId"]))
    selected_allocations = [item for item in allocations if item["selected"]][:limit]
    selected_ids = {item["findingId"] for item in selected_allocations}
    for item in allocations:
        item["selected"] = item["findingId"] in selected_ids

    return {
        "policy": "cem_mismatch_guided",
        "formula": (
            "priority(f)=0.55*mismatch(c)+0.25*confidence(f)+severity_bonus"
            "+evidence_gap_bonus+contradiction_bonus"
        ),
        "thresholds": {
            "shallow": SHALLOW_THRESHOLD,
            "deep": DEEP_THRESHOLD,
            "selection": threshold,
        },
        "mode": mode,
        "limit": limit,
        "allocations": allocations,
        "selectedFindingIds": [item["findingId"] for item in selected_allocations],
    }


def _claim_scores_by_id(mismatch_report: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(item.get("claimId")): item
        for item in mismatch_report.get("claimScores", [])
        if isinstance(item, dict) and item.get("claimId")
    }


def _apply_node_guidance(node: RiskNode, scores: List[Dict[str, Any]]) -> None:
    mismatch = max(float(item.get("mismatchScore", 0.0)) for item in scores)
    node.mismatchScore = round(mismatch, 3)
    node.riskScore = round(max(float(node.riskScore or 0), node.mismatchScore), 3)
    node.expansionPolicy = _policy_for_score(node.mismatchScore)
    node.assignedModel = _model_for_score(node.mismatchScore, node.assignedModel)
    node.status = _status_for_score(node.mismatchScore, node.status)
    node.mismatchDrivers = _merge_drivers([_top_dimensions(item.get("dimensions", {})) for item in scores])


def _policy_for_score(score: float) -> str:
    if score >= DEEP_THRESHOLD:
        return "deep_contradiction_revision"
    if score >= SHALLOW_THRESHOLD:
        return "evidence_adequacy_expansion"
    return "shallow_traceability"


def _status_for_score(score: float, current: str) -> str:
    if score >= DEEP_THRESHOLD:
        return "mismatch_guided_deep_review"
    if score >= SHALLOW_THRESHOLD:
        return "mismatch_guided_evidence_review"
    return current


def _model_for_score(score: float, current: str) -> str:
    if score >= DEEP_THRESHOLD:
        return "qwen-max"
    if score >= SHALLOW_THRESHOLD and current != "qwen-max":
        return "qwen-plus"
    return current or "rules"


def _budget_priority(finding: Finding, mismatch: float, dimensions: Dict[str, Any]) -> float:
    severity_bonus = {
        "blocker": 0.18,
        "major": 0.12,
        "minor": 0.05,
        "info": 0.0,
    }.get(finding.severity, 0.05)
    support_bonus = {
        "contradicted": 0.18,
        "unsupported": 0.12,
        "weakly_supported": 0.05,
    }.get(finding.supportStatus or "", 0.0)
    evidence_gap = max(float(dimensions.get("coverage", 0.0)), float(dimensions.get("baseline", 0.0)))
    contradiction = max(float(dimensions.get("numeric", 0.0)), float(dimensions.get("guardrail", 0.0)))
    priority = (
        0.55 * mismatch
        + 0.25 * float(finding.confidence or 0)
        + severity_bonus
        + 0.08 * evidence_gap
        + 0.10 * contradiction
        + support_bonus
    )
    return round(min(1.0, priority), 3)


def _top_dimensions(dimensions: Dict[str, Any]) -> List[str]:
    ranked = sorted(
        ((name, float(value)) for name, value in dimensions.items()),
        key=lambda item: item[1],
        reverse=True,
    )
    return [name for name, value in ranked[:3] if value > 0]


def _merge_drivers(driver_lists: List[List[str]]) -> List[str]:
    counts: Counter[str] = Counter()
    for drivers in driver_lists:
        counts.update(drivers)
    return [name for name, _ in counts.most_common(4)]
