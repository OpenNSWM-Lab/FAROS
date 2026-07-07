"""Risk analysis and actionable finding generation for ReviewX."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List

from app.modules.review.reviewx_models import Claim, Evidence, Finding, RiskNode


_NUMERIC_RE = re.compile(r"\b\d+(\.\d+)?\s*(%|percent|x|times|k|ms|s|tokens?|accuracy|f1|auc)\b", re.IGNORECASE)
_BASELINE_RE = re.compile(r"\b(baseline|ablation|compare|comparison|outperform|state-of-the-art|sota)\b", re.IGNORECASE)
_OVERCLAIM_RE = re.compile(r"\b(always|guarantee|guarantees|prove|proves|state-of-the-art|sota|without degrading|no degradation)\b", re.IGNORECASE)


def _finding_id(findings: List[Finding]) -> str:
    return f"finding_{len(findings) + 1:03d}"


def _severity(score: float) -> str:
    if score >= 0.88:
        return "blocker"
    if score >= 0.62:
        return "major"
    if score >= 0.35:
        return "minor"
    return "info"


def _has_metric_evidence(claim_id: str, links: Dict[str, List[str]], evidence_by_id: Dict[str, Evidence]) -> bool:
    return any(evidence_by_id[eid].evidenceType == "metric" for eid in links.get(claim_id, []) if eid in evidence_by_id)


def _has_experiment_evidence(claim_id: str, links: Dict[str, List[str]], evidence_by_id: Dict[str, Evidence]) -> bool:
    return any(
        evidence_by_id[eid].sourceModule == "experiment"
        for eid in links.get(claim_id, [])
        if eid in evidence_by_id
    )


def _avoid_claim_hits(claim: Claim, avoid_claims: List[str]) -> List[str]:
    text = claim.text.lower()
    hits = []
    for avoid in avoid_claims:
        avoid_text = str(avoid).lower()
        tokens = [t for t in re.findall(r"[a-z][a-z0-9_-]{4,}", avoid_text) if t not in {"claim", "without"}]
        if tokens and sum(1 for t in tokens if t in text) >= max(1, min(3, len(tokens) // 2)):
            hits.append(str(avoid))
    return hits


def analyze_reviewx_risks(
    paper: Dict[str, Any],
    claims: List[Claim],
    evidence: List[Evidence],
    links: Dict[str, List[str]],
) -> tuple[List[Finding], List[RiskNode]]:
    evidence_by_id = {ev.id: ev for ev in evidence}
    brief = paper.get("briefJson") or {}
    avoid_claims = brief.get("avoid_claims", []) if isinstance(brief, dict) else []
    findings: List[Finding] = []
    risk_nodes: List[RiskNode] = []

    for claim in claims:
        claim_links = links.get(claim.id, [])
        score = 0.15
        reasons: List[str] = []

        if claim.importance == "high":
            score += 0.18
        if claim.requiresEvidence:
            score += 0.12
        if _NUMERIC_RE.search(claim.text):
            score += 0.28
            if not _has_metric_evidence(claim.id, links, evidence_by_id):
                score += 0.24
                reasons.append("The claim contains a quantitative result but no linked metric evidence was found.")
        if claim.claimType == "performance" and not _has_experiment_evidence(claim.id, links, evidence_by_id):
            score += 0.20
            reasons.append("The performance claim is not grounded in an experiment artifact.")
        if _BASELINE_RE.search(claim.text) and not any("baseline" in evidence_by_id[eid].summary.lower() for eid in claim_links if eid in evidence_by_id):
            score += 0.16
            reasons.append("The claim implies a baseline comparison, but no baseline evidence is linked.")
        if _OVERCLAIM_RE.search(claim.text):
            score += 0.12
            reasons.append("The wording is strong and should be backed by explicit evidence or softened.")

        avoid_hits = _avoid_claim_hits(claim, avoid_claims)
        if avoid_hits:
            score += 0.32
            reasons.append(f"The claim conflicts with the paper brief guardrail: {avoid_hits[0]}")

        score = min(score, 1.0)
        if score >= 0.35 or reasons:
            severity = _severity(score)
            if not reasons:
                reasons.append("This claim is important enough to require explicit evidence traceability.")
            target = "experiments" if claim.claimType == "performance" else "papers"
            if avoid_hits:
                target = "papers"
            if claim.claimType == "method" and not claim_links:
                target = "code"
            location = {
                "section": claim.sourceSpan.section,
                **({"line": claim.sourceSpan.line} if claim.sourceSpan.line else {}),
            }
            findings.append(Finding(
                id=_finding_id(findings),
                paperId=paper["id"],
                claimId=claim.id,
                severity=severity,
                riskType="unsupported_claim" if claim.requiresEvidence else "traceability_gap",
                title=f"{claim.claimType.title()} claim needs stronger evidence",
                description=f"{claim.text}\n\n" + " ".join(reasons),
                evidenceIds=claim_links,
                targetModule=target,
                suggestedFix=_suggest_fix(claim, reasons, target),
                confidence=round(score, 2),
                location=location,
            ))

            risk_nodes.append(RiskNode(
                id=f"risk_{len(risk_nodes) + 1:03d}",
                question=_risk_question(claim),
                claimIds=[claim.id],
                riskScore=round(score, 2),
                status="needs_deep_review" if score >= 0.62 else "needs_traceability",
                assignedModel="qwen-max" if score >= 0.8 else "qwen-plus" if score >= 0.5 else "rules",
            ))

    findings.extend(_global_findings(paper, claims, evidence, findings))
    return findings[:60], risk_nodes[:60]


def _suggest_fix(claim: Claim, reasons: List[str], target: str) -> str:
    if target == "experiments":
        return (
            "Add a structured experiment artifact with baseline metrics, then update the paper table/claim "
            "to cite the exact metric name and run ID."
        )
    if target == "code":
        return "Attach the implementation artifact or reproducibility manifest that supports this method claim."
    if any("guardrail" in r for r in reasons):
        return "Rewrite or weaken the claim to respect the paper brief guardrail, unless new evidence is added first."
    return "Add a nearby citation, evidence pointer, or explicit limitation statement for this claim."


def _risk_question(claim: Claim) -> str:
    if claim.claimType == "performance":
        return "Does the linked experiment evidence quantitatively support this performance claim?"
    if claim.claimType == "method":
        return "Is the proposed method claim backed by implementation details or reproducible artifacts?"
    return "Is this claim supported by explicit paper or workflow evidence?"


def _global_findings(
    paper: Dict[str, Any],
    claims: List[Claim],
    evidence: List[Evidence],
    current_findings: List[Finding],
) -> List[Finding]:
    extra: List[Finding] = []
    evidence_counts = Counter(ev.evidenceType for ev in evidence)
    performance_claims = [c for c in claims if c.claimType == "performance"]

    if performance_claims and evidence_counts["metric"] == 0:
        extra.append(Finding(
            id=f"finding_{len(current_findings) + len(extra) + 1:03d}",
            paperId=paper["id"],
            claimId=None,
            severity="blocker",
            riskType="missing_metrics",
            title="Performance claims exist but no metric evidence is available",
            description=(
                f"{len(performance_claims)} performance-oriented claims were extracted, "
                "but ReviewX found no metrics.json entries linked to this paper."
            ),
            evidenceIds=[],
            targetModule="experiments",
            suggestedFix="Generate or attach experiment metrics before making quantitative performance claims.",
            confidence=0.93,
            location={"section": "Experiments"},
        ))

    if claims and evidence_counts["citation"] == 0:
        extra.append(Finding(
            id=f"finding_{len(current_findings) + len(extra) + 1:03d}",
            paperId=paper["id"],
            claimId=None,
            severity="major",
            riskType="missing_citations",
            title="No citation commands found in reviewed LaTeX",
            description="The paper contains research claims, but ReviewX did not find LaTeX citation commands.",
            evidenceIds=[],
            targetModule="papers",
            suggestedFix="Add related-work citations near motivation, baseline, and method comparison claims.",
            confidence=0.78,
            location={"section": "Related Work"},
        ))

    if not evidence_counts["code_artifact"]:
        extra.append(Finding(
            id=f"finding_{len(current_findings) + len(extra) + 1:03d}",
            paperId=paper["id"],
            claimId=None,
            severity="minor",
            riskType="missing_code_artifact",
            title="No exported code artifact was found for reproducibility review",
            description="ReviewX could not inspect code exports for this paper's project.",
            evidenceIds=[],
            targetModule="code",
            suggestedFix="Export a reproducibility package or attach a code manifest for review.",
            confidence=0.61,
            location={"section": "Reproducibility"},
        ))

    return extra
