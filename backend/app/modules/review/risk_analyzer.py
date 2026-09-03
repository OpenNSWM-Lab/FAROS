"""Risk analysis and actionable finding generation for ReviewX."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional

from app.modules.review.guardrails import find_guardrail_conflicts
from app.modules.review.reviewx_models import Claim, Evidence, EvidenceVerification, Finding, RiskNode


_NUMERIC_RE = re.compile(
    r"(?:\b\d+(?:\.\d+)?\s*(?:%|percent(?:age points?)?|x|times|k|ms|s|tokens?|accuracy|f1|auc)\b|"
    r"\b(?:macro\s+f1|f1(?:-score)?|accuracy|precision|recall|auc|ece)\b.{0,24}\d+(?:\.\d+)?|"
    r"(?<![A-Za-z0-9_.])-?\d+\.\d{2,}(?=$|[^A-Za-z0-9_]))",
    re.IGNORECASE,
)
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


def _is_benchmark_claim(claim: Claim) -> bool:
    return (
        claim.sourceSpan.section == "CEM-Bench Injected Claims"
        or claim.text.lstrip().startswith("CEM-Bench")
    )


def _is_external_claim(paper: Dict[str, Any], claim: Claim) -> bool:
    return bool(paper.get("externalPaper")) and not _is_benchmark_claim(claim)


def _avoid_claim_hits(claim: Claim, avoid_claims: List[str]) -> List[str]:
    return find_guardrail_conflicts(claim.text, avoid_claims)


def analyze_reviewx_risks(
    paper: Dict[str, Any],
    claims: List[Claim],
    evidence: List[Evidence],
    links: Dict[str, List[str]],
    verifications: List[EvidenceVerification] | None = None,
) -> tuple[List[Finding], List[RiskNode]]:
    evidence_by_id = {ev.id: ev for ev in evidence}
    verifications_by_claim: Dict[str, List[EvidenceVerification]] = {}
    for verification in verifications or []:
        verifications_by_claim.setdefault(verification.claimId, []).append(verification)
    brief = paper.get("briefJson") or {}
    avoid_claims = brief.get("avoid_claims", []) if isinstance(brief, dict) else []
    findings: List[Finding] = []
    risk_nodes: List[RiskNode] = []

    for claim in claims:
        external_claim = _is_external_claim(paper, claim)
        claim_links = links.get(claim.id, [])
        score = 0.08 if external_claim else 0.15
        reasons: List[str] = []

        if claim.importance == "high":
            score += 0.10 if external_claim else 0.18
        if claim.requiresEvidence:
            score += 0.05 if external_claim else 0.12
        if _NUMERIC_RE.search(claim.text):
            score += 0.14 if external_claim else 0.28
            if not _has_metric_evidence(claim.id, links, evidence_by_id):
                score += 0.04 if external_claim else 0.24
                reasons.append(
                    "The external paper has no imported FAROS metric artifact for this quantitative claim."
                    if external_claim else
                    "The claim contains a quantitative result but no linked metric evidence was found."
                )
        if claim.claimType == "performance" and not _has_experiment_evidence(claim.id, links, evidence_by_id):
            score += 0.04 if external_claim else 0.20
            reasons.append(
                "The external paper's experiment artifacts were not imported into FAROS."
                if external_claim else
                "The performance claim is not grounded in an experiment artifact."
            )
        if _BASELINE_RE.search(claim.text) and not any("baseline" in evidence_by_id[eid].summary.lower() for eid in claim_links if eid in evidence_by_id):
            score += 0.04 if external_claim else 0.16
            reasons.append(
                "No structured baseline artifact was imported for this external paper."
                if external_claim else
                "The claim implies a baseline comparison, but no baseline evidence is linked."
            )
        if _OVERCLAIM_RE.search(claim.text):
            score += 0.12
            reasons.append("The wording is strong and should be backed by explicit evidence or softened.")

        claim_verifications = verifications_by_claim.get(claim.id, [])
        support_status = _claim_support_status(claim_verifications)
        verifier_ids = [verification.id for verification in claim_verifications]
        for verification in claim_verifications:
            if verification.supportStatus == "contradicted":
                score += 0.30
                reasons.append(f"Verifier contradiction ({verification.verifierType}): {verification.verdict}")
            elif verification.supportStatus == "unsupported":
                score += 0.20
                reasons.append(f"Verifier unsupported ({verification.verifierType}): {verification.verdict}")
            elif verification.supportStatus == "weakly_supported":
                score += 0.08
                reasons.append(f"Verifier weak support ({verification.verifierType}): {verification.verdict}")
            elif verification.supportStatus == "needs_human_verification":
                score += 0.08
                reasons.append(f"Verifier requests human review ({verification.verifierType}): {verification.verdict}")
            elif verification.supportStatus == "artifact_absent":
                score += 0.04
                reasons.append(f"Artifact unavailable ({verification.verifierType}): {verification.verdict}")
            elif verification.supportStatus == "supported":
                score -= 0.08

        avoid_hits = _avoid_claim_hits(claim, avoid_claims)
        if avoid_hits:
            score += 0.32
            reasons.append(f"The claim conflicts with the paper brief guardrail: {avoid_hits[0]}")

        statuses = {verification.supportStatus for verification in claim_verifications}
        if statuses and statuses <= {"supported"} and not avoid_hits and not _OVERCLAIM_RE.search(claim.text):
            score = min(score, 0.28)
        artifact_only = bool(statuses) and statuses <= {"artifact_absent", "weakly_supported", "supported"}
        if external_claim and artifact_only and not avoid_hits and not _OVERCLAIM_RE.search(claim.text):
            score = min(score, 0.28)
        score = min(score, 1.0)
        actionable_verdict = bool(statuses & {"unsupported", "contradicted"})
        needs_human = "needs_human_verification" in statuses
        should_emit = (
            score >= 0.35
            or actionable_verdict
            or bool(avoid_hits)
            or (needs_human and score >= 0.24)
            or (not external_claim and bool(reasons))
        )
        if should_emit:
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
            title = f"{claim.claimType.title()} claim needs stronger evidence"
            suggested_fix = _suggest_fix(claim, reasons, target)
            if support_status == "needs_human_verification":
                title = f"{claim.claimType.title()} claim needs human verification"
                suggested_fix = "Inspect the cited full text or surrounding section before accepting or rejecting this claim."
            elif support_status == "artifact_absent":
                title = f"Structured artifact is unavailable for this {claim.claimType} claim"
                suggested_fix = "Import the external experiment, metric, or provenance artifact before making an automated support judgment."
            findings.append(Finding(
                id=_finding_id(findings),
                paperId=paper["id"],
                claimId=claim.id,
                severity=severity,
                riskType=_risk_type_for_claim(claim, claim_verifications),
                title=title,
                description=f"{claim.text}\n\n" + " ".join(reasons),
                evidenceIds=claim_links,
                targetModule=target,
                suggestedFix=suggested_fix,
                confidence=round(score, 2),
                location=location,
                supportStatus=support_status,
                verifierIds=verifier_ids,
                cemCalibration=_finding_cem_calibration(claim_verifications),
            ))

            finding = findings[-1]
            risk_nodes.append(RiskNode(
                id=f"risk_leaf_{len(risk_nodes) + 1:03d}",
                question=_risk_question(claim),
                claimIds=[claim.id],
                riskScore=round(score, 2),
                status="needs_deep_review" if score >= 0.62 else "needs_traceability",
                assignedModel="qwen-max" if score >= 0.8 else "qwen-plus" if score >= 0.5 else "rules",
                level=2,
                category=_finding_category(finding),
                findingIds=[finding.id],
                evidenceIds=claim_links,
                supportCounts=_support_counts(claim_verifications),
            ))

    global_findings = _global_findings(paper, claims, evidence, findings)
    findings.extend(global_findings)
    risk_nodes.extend(_global_risk_nodes(global_findings, len(risk_nodes)))
    risk_tree = _build_question_tree(claims, evidence, findings, risk_nodes)
    return findings[:60], risk_tree[:80]


def _claim_support_status(verifications: List[EvidenceVerification]) -> str | None:
    if not verifications:
        return None
    priority = {
        "contradicted": 0,
        "unsupported": 1,
        "needs_human_verification": 2,
        "artifact_absent": 3,
        "weakly_supported": 4,
        "supported": 5,
        "not_applicable": 6,
    }
    return min(verifications, key=lambda v: priority.get(v.supportStatus, 9)).supportStatus


def _risk_type_for_claim(claim: Claim, verifications: List[EvidenceVerification]) -> str:
    if any(v.verifierType == "citation_semantic" and v.supportStatus in {"unsupported", "contradicted"} for v in verifications):
        return "citation_mismatch"
    if any(
        v.verifierType in {"numeric_metric", "metric_semantics"}
        and v.supportStatus == "contradicted"
        for v in verifications
    ):
        return "metric_mismatch"
    if any(v.supportStatus == "needs_human_verification" for v in verifications):
        return "citation_uncertainty"
    if any(v.supportStatus == "artifact_absent" for v in verifications):
        return "artifact_gap"
    return "unsupported_claim" if claim.requiresEvidence else "traceability_gap"


def _support_counts(verifications: List[EvidenceVerification]) -> Dict[str, int]:
    counts: Counter[str] = Counter()
    for verification in verifications:
        counts[verification.supportStatus] += 1
    return dict(counts)


def _finding_cem_calibration(verifications: List[EvidenceVerification]) -> Dict[str, Any]:
    citation_checks = [
        verification for verification in verifications
        if verification.verifierType == "citation_semantic" and verification.diagnostics
    ]
    if not citation_checks:
        return {}
    worst = max(citation_checks, key=lambda item: float(item.confidence or 0))
    diagnostics = dict(worst.diagnostics)
    return {
        "citationSemantic": diagnostics,
        "lowConfidenceCitation": bool(diagnostics.get("lowConfidence")),
        "recommendedEscalation": diagnostics.get("recommendedEscalation"),
    }


def _finding_category(finding: Finding) -> str:
    if finding.riskType == "no_auditable_claims":
        return "evidence_support"
    if finding.riskType == "artifact_gap":
        return "evidence_support"
    if finding.riskType == "citation_uncertainty":
        return "writing_citations"
    if finding.riskType in {"missing_metrics"}:
        return "experimental_validity"
    if finding.riskType in {"missing_citations"}:
        return "writing_citations"
    if finding.riskType in {"missing_code_artifact"} or finding.targetModule == "code":
        return "method_reproducibility"
    if finding.targetModule == "experiments":
        return "experimental_validity"
    if finding.riskType in {"unsupported_claim", "traceability_gap"}:
        return "evidence_support"
    return "workflow_feedback"


def _status_from_score(score: float) -> str:
    if score >= 0.88:
        return "blocking"
    if score >= 0.62:
        return "needs_deep_review"
    if score >= 0.35:
        return "needs_traceability"
    return "passed"


def _assigned_model_from_score(score: float) -> str:
    if score >= 0.8:
        return "qwen-max"
    if score >= 0.5:
        return "qwen-plus"
    return "rules"


def _merge_support_counts(nodes: List[RiskNode]) -> Dict[str, int]:
    counts: Counter[str] = Counter()
    for node in nodes:
        counts.update(node.supportCounts)
    return dict(counts)


def _max_score(nodes: List[RiskNode], default: float = 0.0) -> float:
    return max((node.riskScore for node in nodes), default=default)


def _unique(values: List[Optional[str]]) -> List[str]:
    seen = set()
    results = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        results.append(value)
    return results


def _global_risk_nodes(findings: List[Finding], offset: int) -> List[RiskNode]:
    nodes: List[RiskNode] = []
    for index, finding in enumerate(findings, start=offset + 1):
        nodes.append(RiskNode(
            id=f"risk_leaf_{index:03d}",
            question=_global_risk_question(finding),
            claimIds=[],
            riskScore=round(finding.confidence, 2),
            status=_status_from_score(finding.confidence),
            assignedModel=_assigned_model_from_score(finding.confidence),
            level=2,
            category=_finding_category(finding),
            findingIds=[finding.id],
            evidenceIds=finding.evidenceIds,
            supportCounts={finding.supportStatus: 1} if finding.supportStatus else {},
        ))
    return nodes


def _global_risk_question(finding: Finding) -> str:
    if finding.riskType == "no_auditable_claims":
        return "Did ReviewX extract enough substantive claims to perform an audit?"
    if finding.riskType == "missing_metrics":
        return "Are experiment metrics available before performance claims are accepted?"
    if finding.riskType == "missing_citations":
        return "Does the paper cite related work near its major research claims?"
    if finding.riskType == "missing_code_artifact":
        return "Can the method be reproduced from exported code artifacts?"
    return "Can this global review finding be converted into a concrete FAROS revision task?"


def _build_question_tree(
    claims: List[Claim],
    evidence: List[Evidence],
    findings: List[Finding],
    leaves: List[RiskNode],
) -> List[RiskNode]:
    category_questions = {
        "evidence_support": "Q1: Are the main paper claims grounded in explicit FAROS artifacts?",
        "experimental_validity": "Q2: Do experiments and metrics justify the reported performance claims?",
        "method_reproducibility": "Q3: Is the proposed method backed by implementation and reproducibility artifacts?",
        "writing_citations": "Q4: Are citations, related work, and paper text aligned with the claims?",
        "workflow_feedback": "Q5: Can review findings become actionable FAROS revision tasks?",
    }
    ordered_categories = list(category_questions.keys())
    leaves_by_category: Dict[str, List[RiskNode]] = {category: [] for category in ordered_categories}
    for leaf in leaves:
        leaves_by_category.setdefault(leaf.category, []).append(leaf)

    category_nodes: List[RiskNode] = []
    for index, category in enumerate(ordered_categories, start=1):
        category_leaves = leaves_by_category.get(category, [])
        score = round(_max_score(category_leaves), 2)
        category_nodes.append(RiskNode(
            id=f"risk_q{index}",
            question=category_questions[category],
            claimIds=_unique([claim_id for node in category_leaves for claim_id in node.claimIds]),
            riskScore=score,
            status=_status_from_score(score),
            assignedModel=_assigned_model_from_score(score),
            children=[node.id for node in category_leaves],
            parentId="risk_root",
            level=1,
            category=category,
            findingIds=_unique([finding_id for node in category_leaves for finding_id in node.findingIds]),
            evidenceIds=_unique([evidence_id for node in category_leaves for evidence_id in node.evidenceIds]),
            supportCounts=_merge_support_counts(category_leaves),
        ))
        for leaf in category_leaves:
            leaf.parentId = f"risk_q{index}"

    root_score = round(_max_score(category_nodes), 2)
    root = RiskNode(
        id="risk_root",
        question="Q0: Is this paper trustworthy enough to feed back into the FAROS research loop?",
        claimIds=[claim.id for claim in claims],
        riskScore=root_score,
        status=_status_from_score(root_score),
        assignedModel=_assigned_model_from_score(root_score),
        children=[node.id for node in category_nodes],
        level=0,
        category="root",
        findingIds=[finding.id for finding in findings],
        evidenceIds=[ev.id for ev in evidence],
        supportCounts=_merge_support_counts(category_nodes),
    )
    return [root, *category_nodes, *leaves]


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
    external_paper = bool(paper.get("externalPaper"))

    if not claims:
        extra.append(Finding(
            id=f"finding_{len(current_findings) + len(extra) + 1:03d}",
            paperId=paper["id"],
            claimId=None,
            severity="major" if external_paper else "blocker",
            riskType="no_auditable_claims",
            title="No auditable research claims were extracted",
            description=(
                "ReviewX could not identify a substantive method, performance, robustness, or evidence assertion "
                "in the available manuscript. Zero findings would therefore mean that the audit had no usable "
                "claim input, not that the paper passed review."
            ),
            evidenceIds=[],
            targetModule="papers",
            suggestedFix=(
                "Open the paper workspace and verify that the complete LaTeX manuscript is present. State the "
                "main method and result claims as complete sentences, then rerun ReviewX."
            ),
            confidence=0.99,
            location={"section": "Manuscript input"},
            supportStatus="artifact_absent",
        ))

    if external_paper and (evidence_counts["metric"] == 0 or not evidence_counts["code_artifact"]):
        missing = []
        if evidence_counts["metric"] == 0:
            missing.append("metrics")
        if not evidence_counts["code_artifact"]:
            missing.append("code/provenance")
        extra.append(Finding(
            id=f"finding_{len(current_findings) + len(extra) + 1:03d}",
            paperId=paper["id"],
            claimId=None,
            severity="info",
            riskType="artifact_gap",
            title="External FAROS provenance artifacts were not imported",
            description=(
                f"The paper text and bibliography are available, but structured {', '.join(missing)} "
                "artifacts are absent. ReviewX will not treat that absence as proof that claims are unsupported."
            ),
            evidenceIds=[],
            targetModule="papers",
            suggestedFix="Import structured experiment/provenance artifacts for artifact-level verification, or use human review.",
            confidence=0.35,
            location={"section": "External Evidence"},
            supportStatus="artifact_absent",
            cemCalibration={"externalPaperCalibration": True},
        ))

    if not external_paper and performance_claims and evidence_counts["metric"] == 0:
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

    if not external_paper and not evidence_counts["code_artifact"]:
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
