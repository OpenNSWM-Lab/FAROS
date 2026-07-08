"""Mismatch scoring and claim-evidence graph export for ReviewX.

This module turns ReviewX's local checks into experiment-ready measurements.
The score is intentionally deterministic so local_only, balanced, and deep
runs can be compared without hidden model variance.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List

from app.modules.review.reviewx_models import Claim, Evidence, EvidenceVerification, Finding


_STATUS_SCORE = {
    "supported": 0.12,
    "weakly_supported": 0.48,
    "unsupported": 0.82,
    "contradicted": 1.0,
    "not_applicable": 0.0,
}

_VERIFIER_DIMENSION = {
    "numeric_metric": "numeric",
    "baseline_coverage": "baseline",
    "citation_context": "citation",
    "brief_guardrail": "guardrail",
    "general_evidence": "general",
}


def build_mismatch_report(
    claims: List[Claim],
    evidence: List[Evidence],
    links: Dict[str, List[str]],
    verifications: List[EvidenceVerification],
    findings: List[Finding],
) -> Dict[str, Any]:
    evidence_by_id = {ev.id: ev for ev in evidence}
    verifications_by_claim: Dict[str, List[EvidenceVerification]] = {}
    findings_by_claim: Dict[str, List[Finding]] = {}
    for verification in verifications:
        verifications_by_claim.setdefault(verification.claimId, []).append(verification)
    for finding in findings:
        if finding.claimId:
            findings_by_claim.setdefault(finding.claimId, []).append(finding)

    claim_scores = []
    for claim in claims:
        claim_verifications = verifications_by_claim.get(claim.id, [])
        claim_findings = findings_by_claim.get(claim.id, [])
        linked_ids = [eid for eid in links.get(claim.id, []) if eid in evidence_by_id]
        score, raw_score, dimensions, reasons, calibration = _score_claim(
            claim, linked_ids, claim_verifications, claim_findings
        )
        claim_scores.append({
            "claimId": claim.id,
            "claimType": claim.claimType,
            "importance": claim.importance,
            "requiresEvidence": claim.requiresEvidence,
            "mismatchScore": score,
            "rawMismatchScore": raw_score,
            "supportStatus": _worst_support_status(claim_verifications),
            "linkedEvidenceCount": len(linked_ids),
            "findingIds": [finding.id for finding in claim_findings],
            "verificationIds": [verification.id for verification in claim_verifications],
            "dimensions": dimensions,
            "calibration": calibration,
            "reasons": reasons,
            "text": claim.text,
            "sourceSpan": claim.sourceSpan.to_dict() if hasattr(claim.sourceSpan, "to_dict") else {
                "file": claim.sourceSpan.file,
                "section": claim.sourceSpan.section,
                "line": claim.sourceSpan.line,
            },
        })

    aggregate = _aggregate_scores(claim_scores)
    graph = _build_graph(claims, evidence, links, verifications, findings, claim_scores)
    return {
        "method": {
            "name": "CEM-Review",
            "metric": "Claim-Evidence Mismatch",
            "formula": "M(c,E)=max(coverage_gap,numeric_contradiction,baseline_gap,citation_gap,guardrail_violation,review_risk,importance)",
            "thresholds": {
                "shallowTraceability": 0.3,
                "deepContradictionRevision": 0.72,
            },
        },
        "aggregate": aggregate,
        "claimScores": claim_scores,
        "graph": graph,
    }


def _score_claim(
    claim: Claim,
    linked_ids: List[str],
    verifications: List[EvidenceVerification],
    findings: List[Finding],
) -> tuple[float, float, Dict[str, float], List[str], Dict[str, Any]]:
    dimensions: Dict[str, float] = {}
    reasons: List[str] = []

    if claim.requiresEvidence and not linked_ids:
        dimensions["coverage"] = 0.9
        reasons.append("requires_evidence_but_no_linked_artifact")
    elif claim.requiresEvidence and linked_ids:
        dimensions["coverage"] = 0.2
    else:
        dimensions["coverage"] = 0.0

    for verification in verifications:
        dimension = _VERIFIER_DIMENSION.get(verification.verifierType, verification.verifierType)
        value = _STATUS_SCORE.get(verification.supportStatus, 0.5) * verification.confidence
        dimensions[dimension] = max(dimensions.get(dimension, 0.0), round(value, 3))
        if verification.supportStatus in {"unsupported", "contradicted"}:
            reasons.append(f"{verification.verifierType}:{verification.supportStatus}")

    if findings:
        severity_score = max(_finding_score(finding) for finding in findings)
        dimensions["review_risk"] = severity_score
        reasons.extend([f"finding:{finding.id}:{finding.severity}" for finding in findings[:3]])

    if claim.importance == "high":
        dimensions["importance"] = max(dimensions.get("importance", 0.0), 0.18)

    raw_score = min(1.0, round(max(dimensions.values() or [0.0]), 3))
    score, calibration = _calibrate_score(raw_score, findings)
    return score, raw_score, dimensions, reasons[:8], calibration


def _calibrate_score(score: float, findings: List[Finding]) -> tuple[float, Dict[str, Any]]:
    if not findings:
        return score, {"llmFactor": 1.0, "revisionAdjustment": 0.0}

    decisions = [finding.reviewerDecision for finding in findings if finding.reviewerDecision]
    if "valid" in decisions:
        llm_factor = 1.0
        llm_decision = "valid"
    elif "partially_valid" in decisions:
        llm_factor = 0.9
        llm_decision = "partially_valid"
    elif "overestimated" in decisions:
        llm_factor = 0.65
        llm_decision = "overestimated"
    else:
        llm_factor = 1.0
        llm_decision = None

    revision_adjustment = max(
        (
            float(finding.cemCalibration.get("revisionAdjustment", 0.0))
            for finding in findings
            if finding.cemCalibration
        ),
        default=0.0,
    )
    revision_statuses = [
        finding.revisionStatus
        for finding in findings
        if finding.revisionStatus
    ]
    calibrated = round(max(0.0, min(1.0, score * llm_factor - revision_adjustment)), 3)
    return calibrated, {
        "llmDecision": llm_decision,
        "llmFactor": llm_factor,
        "revisionAdjustment": round(revision_adjustment, 3),
        "revisionStatuses": revision_statuses,
    }


def _finding_score(finding: Finding) -> float:
    severity = {
        "blocker": 0.95,
        "major": 0.72,
        "minor": 0.42,
        "info": 0.18,
    }.get(finding.severity, 0.5)
    return round(max(severity, finding.confidence), 3)


def _worst_support_status(verifications: List[EvidenceVerification]) -> str | None:
    if not verifications:
        return None
    priority = {
        "contradicted": 0,
        "unsupported": 1,
        "weakly_supported": 2,
        "supported": 3,
        "not_applicable": 4,
    }
    return min(verifications, key=lambda item: priority.get(item.supportStatus, 9)).supportStatus


def _aggregate_scores(claim_scores: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not claim_scores:
        return {
            "meanMismatch": 0,
            "maxMismatch": 0,
            "highMismatchClaimCount": 0,
            "claimCount": 0,
            "supportCounts": {},
            "dimensionMax": {},
        }
    values = [float(item["mismatchScore"]) for item in claim_scores]
    support_counts = Counter(item.get("supportStatus") or "not_checked" for item in claim_scores)
    dimension_max: Dict[str, float] = {}
    for item in claim_scores:
        for name, value in item.get("dimensions", {}).items():
            dimension_max[name] = max(dimension_max.get(name, 0.0), float(value))
    return {
        "meanMismatch": round(sum(values) / len(values), 3),
        "maxMismatch": round(max(values), 3),
        "highMismatchClaimCount": len([value for value in values if value >= 0.72]),
        "claimCount": len(claim_scores),
        "supportCounts": dict(support_counts),
        "dimensionMax": {name: round(value, 3) for name, value in sorted(dimension_max.items())},
    }


def _build_graph(
    claims: List[Claim],
    evidence: List[Evidence],
    links: Dict[str, List[str]],
    verifications: List[EvidenceVerification],
    findings: List[Finding],
    claim_scores: List[Dict[str, Any]],
) -> Dict[str, Any]:
    claim_score_by_id = {item["claimId"]: item for item in claim_scores}
    nodes = []
    edges = []

    for claim in claims:
        score = claim_score_by_id.get(claim.id, {})
        nodes.append({
            "id": claim.id,
            "nodeType": "claim",
            "label": claim.text[:120],
            "claimType": claim.claimType,
            "mismatchScore": score.get("mismatchScore", 0),
            "rawMismatchScore": score.get("rawMismatchScore", 0),
            "supportStatus": score.get("supportStatus"),
        })
        for evidence_id in links.get(claim.id, [])[:8]:
            edges.append({
                "id": f"edge_{claim.id}_{evidence_id}",
                "source": claim.id,
                "target": evidence_id,
                "edgeType": "linked_to",
            })

    for ev in evidence:
        nodes.append({
            "id": ev.id,
            "nodeType": "evidence",
            "label": ev.summary[:120],
            "evidenceType": ev.evidenceType,
            "sourceModule": ev.sourceModule,
            "confidence": ev.confidence,
        })

    for verification in verifications:
        nodes.append({
            "id": verification.id,
            "nodeType": "verification",
            "label": verification.verdict[:120],
            "verifierType": verification.verifierType,
            "supportStatus": verification.supportStatus,
            "confidence": verification.confidence,
        })
        edges.append({
            "id": f"edge_{verification.claimId}_{verification.id}",
            "source": verification.claimId,
            "target": verification.id,
            "edgeType": "verified_by",
        })
        for evidence_id in verification.evidenceIds[:5]:
            edges.append({
                "id": f"edge_{verification.id}_{evidence_id}",
                "source": verification.id,
                "target": evidence_id,
                "edgeType": "checks_evidence",
            })

    for finding in findings:
        nodes.append({
            "id": finding.id,
            "nodeType": "finding",
            "label": finding.title,
            "severity": finding.severity,
            "riskType": finding.riskType,
            "supportStatus": finding.supportStatus,
        })
        if finding.claimId:
            edges.append({
                "id": f"edge_{finding.claimId}_{finding.id}",
                "source": finding.claimId,
                "target": finding.id,
                "edgeType": "raises_finding",
            })

    return {
        "nodes": nodes[:240],
        "edges": edges[:420],
        "nodeCount": len(nodes),
        "edgeCount": len(edges),
    }
