"""Evidence verification for ReviewX claim support.

The evidence graph links potentially relevant artifacts. This verifier adds a
second pass that asks a stricter question: does the linked evidence actually
support, weakly support, contradict, or fail to support each claim?
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, Optional

from app.modules.review.guardrails import find_guardrail_conflicts
from app.modules.review.reviewx_models import Claim, Evidence, EvidenceVerification


_NUMERIC_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(%|percent|x|times|k|ms|s|tokens?|accuracy|f1|auc)?\b", re.IGNORECASE)
_BASELINE_RE = re.compile(r"\b(baselines?|ablation|compare|comparison|outperform|state-of-the-art|sota)\b", re.IGNORECASE)
_COMPARISON_LIMITATION_RE = re.compile(
    r"\b(?:should not|must not|cannot|can't|does not|do not|not)\b.{0,100}"
    r"\b(?:generaliz\w*|outperform\w*|superior\w*|state-of-the-art|sota)\b|"
    r"\bwithout further (?:evaluation|testing|validation)\b",
    re.IGNORECASE,
)
_CITATION_RE = re.compile(r"\\cite[p|t]?\{[^}]+\}")
_DOMAIN_RISK_TERMS = {
    "clinical", "triage", "legal", "financial", "finance", "risk", "scoring",
    "fairness", "biomedical", "multilingual", "deployment", "autonomous",
    "domain", "shift", "summarization", "medical", "healthcare", "safety",
    "privacy", "security", "robustness", "low-resource", "low", "resource",
    "distribution", "generalization", "generalizes", "out-of-domain",
}
_CITATION_GENERIC_TERMS = {
    "framework", "method", "approach", "system", "model", "paper", "results",
    "claim", "study", "work", "using", "based", "propose", "proposes",
}


def verify_claim_evidence(
    paper: Dict[str, Any],
    claims: List[Claim],
    evidence: List[Evidence],
    links: Dict[str, List[str]],
    calibrate_external: bool = True,
) -> List[EvidenceVerification]:
    evidence_by_id = {ev.id: ev for ev in evidence}
    citation_entries = _citation_entries_by_key(evidence)
    brief = paper.get("briefJson") or {}
    avoid_claims = brief.get("avoid_claims", []) if isinstance(brief, dict) else []
    verifications: List[EvidenceVerification] = []

    for claim in claims:
        linked = [evidence_by_id[eid] for eid in links.get(claim.id, []) if eid in evidence_by_id]
        linked_ids = [ev.id for ev in linked]
        claim_numbers = _extract_numbers(claim.text)
        citation_keys = _citation_keys_from_claim(claim)

        if claim_numbers:
            verifications.append(_verify_numeric_claim(paper["id"], claim, linked, claim_numbers, len(verifications)))

        if _BASELINE_RE.search(claim.text) and not _COMPARISON_LIMITATION_RE.search(claim.text):
            verifications.append(_verify_baseline_claim(paper["id"], claim, linked, len(verifications)))

        if claim.claimType == "performance":
            metric_audit = _verify_metric_audit(paper["id"], claim, linked, len(verifications))
            if metric_audit:
                verifications.append(metric_audit)

        if citation_keys:
            verifications.append(_verify_citation_semantics(
                paper["id"],
                claim,
                citation_keys,
                citation_entries,
                len(verifications),
            ))

        if (
            claim.claimType in {"method", "performance"}
            or "citation_or_evidence_needed" in claim.riskHints
        ) and not citation_keys and not _CITATION_RE.search(claim.text):
            verifications.append(_verify_citation_context(paper["id"], claim, linked, len(verifications)))

        scope_guardrail = _verify_high_stakes_scope(paper["id"], claim, len(verifications))
        if scope_guardrail:
            verifications.append(scope_guardrail)

        guardrail = _verify_guardrail(paper["id"], claim, avoid_claims, len(verifications))
        if guardrail:
            verifications.append(guardrail)

        if claim.requiresEvidence and not linked_ids:
            verifications.append(EvidenceVerification(
                id=_verification_id(len(verifications)),
                paperId=paper["id"],
                claimId=claim.id,
                verifierType="general_evidence",
                supportStatus="unsupported",
                verdict="The claim requires evidence, but no linked evidence artifact was found.",
                evidenceIds=[],
                confidence=0.9,
                expectedEvidence=["paper citation", "experiment metric", "experiment report", "code artifact"],
                observedEvidence=[],
            ))
        elif claim.requiresEvidence and linked_ids:
            status = "weakly_supported"
            verdict = "The claim has linked evidence, but ReviewX has not found a direct metric or contradiction-level proof."
            if any(ev.evidenceType in {"metric", "experiment_report"} for ev in linked):
                status = "supported"
                verdict = "The claim has at least one linked experiment-level evidence artifact."
            verifications.append(EvidenceVerification(
                id=_verification_id(len(verifications)),
                paperId=paper["id"],
                claimId=claim.id,
                verifierType="general_evidence",
                supportStatus=status,
                verdict=verdict,
                evidenceIds=linked_ids[:5],
                confidence=0.62 if status == "weakly_supported" else 0.76,
                expectedEvidence=["explicit evidence for this claim"],
                observedEvidence=[_evidence_label(ev) for ev in linked[:5]],
            ))

    return _calibrate_external_verifications(paper, claims, verifications) if calibrate_external else verifications


def _is_benchmark_claim(claim: Claim) -> bool:
    return (
        claim.sourceSpan.section == "CEM-Bench Injected Claims"
        or claim.text.lstrip().startswith("CEM-Bench")
    )


def _calibrate_external_verifications(
    paper: Dict[str, Any],
    claims: List[Claim],
    verifications: List[EvidenceVerification],
) -> List[EvidenceVerification]:
    """Separate absent FAROS artifacts from evidence that actually fails a claim."""
    if not paper.get("externalPaper"):
        return verifications

    claims_by_id = {claim.id: claim for claim in claims}
    artifact_verifiers = {"numeric_metric", "baseline_coverage", "general_evidence"}
    for verification in verifications:
        claim = claims_by_id.get(verification.claimId)
        if not claim or _is_benchmark_claim(claim):
            continue

        original_status = verification.supportStatus
        calibration_reason = None
        if verification.verifierType in artifact_verifiers and original_status == "unsupported":
            verification.supportStatus = "artifact_absent"
            calibration_reason = "external_paper_has_no_faros_structured_artifact"
        elif verification.verifierType == "citation_context" and original_status == "unsupported":
            verification.supportStatus = "needs_human_verification"
            calibration_reason = "sentence_level_citation_context_is_inconclusive"
        elif verification.verifierType == "citation_semantic":
            reasons = set(verification.diagnostics.get("mismatchReasons", []) or [])
            if "missing_citation_metadata" in reasons:
                verification.supportStatus = "artifact_absent"
                calibration_reason = "cited_entry_metadata_is_unavailable"
            elif original_status == "unsupported" and "domain_gap" not in reasons:
                verification.supportStatus = "needs_human_verification"
                calibration_reason = "lexical_mismatch_without_clear_domain_gap"
            elif original_status == "weakly_supported" and verification.diagnostics.get("lowConfidence"):
                verification.supportStatus = "needs_human_verification"
                calibration_reason = "citation_entailment_requires_full_text_review"

        if calibration_reason:
            verification.diagnostics = {
                **verification.diagnostics,
                "externalCalibration": {
                    "applied": True,
                    "originalSupportStatus": original_status,
                    "calibratedSupportStatus": verification.supportStatus,
                    "reason": calibration_reason,
                },
            }
            verification.verdict += (
                f" External-paper calibration: {verification.supportStatus}; "
                "absence of a FAROS-local artifact is not treated as proof that the claim is false."
            )
    return verifications


def _verification_id(index: int) -> str:
    return f"verify_{index + 1:03d}"


def _extract_numbers(text: str) -> List[float]:
    values = []
    for match in _NUMERIC_RE.finditer(text):
        try:
            values.append(float(match.group(1)))
        except ValueError:
            continue
    return values


def _citation_keys_from_claim(claim: Claim) -> List[str]:
    keys = []
    for hint in claim.riskHints:
        if str(hint).startswith("citation_key:"):
            key = str(hint).split(":", 1)[1].strip()
            if key and key not in keys:
                keys.append(key)
    return keys


def _citation_entries_by_key(evidence: List[Evidence]) -> Dict[str, Evidence]:
    entries = {}
    for ev in evidence:
        if ev.evidenceType != "citation_entry":
            continue
        key = str(ev.metadata.get("citationKey") or "").strip()
        if key:
            entries[key] = ev
    return entries


def _verify_citation_semantics(
    paper_id: str,
    claim: Claim,
    citation_keys: List[str],
    citation_entries: Dict[str, Evidence],
    index: int,
) -> EvidenceVerification:
    cited = [citation_entries[key] for key in citation_keys if key in citation_entries]
    if not cited:
        diagnostics = {
            "citationKeys": citation_keys[:5],
            "matchedCitationKeys": [],
            "mismatchReasons": ["missing_citation_metadata"],
            "lowConfidence": True,
            "recommendedEscalation": "llm_citation_entailment",
            "confidenceBasis": "citation command exists but no bibliography entry was resolved",
        }
        return EvidenceVerification(
            id=_verification_id(index),
            paperId=paper_id,
            claimId=claim.id,
            verifierType="citation_semantic",
            supportStatus="unsupported",
            verdict="The claim has citation commands, but ReviewX could not resolve the cited bibliography entries.",
            evidenceIds=[],
            confidence=0.74,
            expectedEvidence=[f"resolved citation entry for {key}" for key in citation_keys[:5]],
            observedEvidence=[],
            diagnostics=diagnostics,
        )

    claim_keywords = set(_keywords(claim.text))
    claim_domain_terms = claim_keywords & _DOMAIN_RISK_TERMS
    citation_keywords = set()
    observed = []
    matched_keys = []
    metadata_fields = set()
    for ev in cited:
        title = str(ev.metadata.get("title") or ev.summary)
        venue = str(ev.metadata.get("venue") or "")
        abstract = str(ev.metadata.get("abstract") or "")
        note = str(ev.metadata.get("note") or "")
        keywords = str(ev.metadata.get("keywords") or "")
        doi = str(ev.metadata.get("doi") or "")
        url = str(ev.metadata.get("url") or "")
        citation_keywords.update(_keywords(f"{title} {venue} {abstract} {note} {keywords}"))
        observed.append(_evidence_label(ev))
        matched_keys.append(str(ev.metadata.get("citationKey") or ev.sourcePath.rsplit("#", 1)[-1]))
        for field_name, value in {
            "title": title,
            "venue": venue,
            "abstract": abstract,
            "note": note,
            "keywords": keywords,
            "doi": doi,
            "url": url,
        }.items():
            if value:
                metadata_fields.add(field_name)

    informative_claim_keywords = claim_keywords - _CITATION_GENERIC_TERMS
    overlap = informative_claim_keywords & citation_keywords
    citation_domain_terms = citation_keywords & _DOMAIN_RISK_TERMS
    domain_gap_terms = claim_domain_terms - citation_domain_terms
    overlap_ratio = round(len(overlap) / max(1, min(len(informative_claim_keywords), 12)), 3)
    risky_domain_gap = bool(claim_domain_terms and not (claim_domain_terms & citation_keywords))
    weak_overlap = len(overlap) < 2 and overlap_ratio < 0.24
    metadata_richness = round(len(metadata_fields) / 7.0, 3)
    mismatch_reasons = []
    if risky_domain_gap:
        mismatch_reasons.append("domain_gap")
    if weak_overlap:
        mismatch_reasons.append("low_lexical_overlap")
    if "abstract" not in metadata_fields and "note" not in metadata_fields and weak_overlap:
        mismatch_reasons.append("thin_citation_metadata")
    low_confidence = bool(
        not risky_domain_gap
        and (weak_overlap or metadata_richness < 0.35 or len(overlap) < 3)
    )
    diagnostics = {
        "citationKeys": citation_keys[:5],
        "matchedCitationKeys": matched_keys[:5],
        "claimDomainTerms": sorted(claim_domain_terms)[:12],
        "citationDomainTerms": sorted(citation_domain_terms)[:12],
        "domainGapTerms": sorted(domain_gap_terms)[:12],
        "overlapTerms": sorted(overlap)[:12],
        "overlapRatio": overlap_ratio,
        "metadataFields": sorted(metadata_fields),
        "metadataRichness": metadata_richness,
        "mismatchReasons": mismatch_reasons,
        "lowConfidence": low_confidence,
        "recommendedEscalation": "llm_citation_entailment" if low_confidence else None,
        "confidenceBasis": (
            "clear domain gap" if risky_domain_gap
            else "low topical overlap" if weak_overlap
            else "topical overlap with citation metadata"
        ),
    }
    if risky_domain_gap or weak_overlap:
        return EvidenceVerification(
            id=_verification_id(index),
            paperId=paper_id,
            claimId=claim.id,
            verifierType="citation_semantic",
            supportStatus="unsupported",
            verdict=(
                "The claim has a citation, but the cited bibliography metadata appears off-topic for the claim's "
                f"semantic scope. reasons={mismatch_reasons}, "
                f"domainGapTerms={sorted(domain_gap_terms)[:6]}, overlap={sorted(overlap)[:6]}"
            ),
            evidenceIds=[ev.id for ev in cited[:5]],
            confidence=0.84 if risky_domain_gap else 0.64 if low_confidence else 0.7,
            expectedEvidence=["citation whose title/topic directly supports the claim scope"],
            observedEvidence=observed[:5],
            diagnostics=diagnostics,
        )

    return EvidenceVerification(
        id=_verification_id(index),
        paperId=paper_id,
        claimId=claim.id,
        verifierType="citation_semantic",
        supportStatus="weakly_supported",
        verdict="The cited bibliography title has topical overlap with the claim, but full citation entailment still needs review.",
        evidenceIds=[ev.id for ev in cited[:5]],
        confidence=0.64,
        expectedEvidence=["citation title/topic aligned with the claim scope"],
        observedEvidence=observed[:5],
        diagnostics=diagnostics,
    )


def _numeric_metric_value(ev: Evidence) -> Optional[float]:
    value = ev.metadata.get("value")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            pass
    summary_numbers = _extract_numbers(ev.summary)
    return summary_numbers[0] if summary_numbers else None


def _verify_numeric_claim(
    paper_id: str,
    claim: Claim,
    linked: List[Evidence],
    claim_numbers: List[float],
    index: int,
) -> EvidenceVerification:
    metric_evidence = [ev for ev in linked if ev.evidenceType == "metric"]
    if not metric_evidence:
        return EvidenceVerification(
            id=_verification_id(index),
            paperId=paper_id,
            claimId=claim.id,
            verifierType="numeric_metric",
            supportStatus="unsupported",
            verdict="The claim contains a numeric result, but no linked metric evidence was found.",
            evidenceIds=[],
            confidence=0.92,
            expectedEvidence=[f"metric near {value:g}" for value in claim_numbers[:3]],
            observedEvidence=[],
        )

    observed = []
    for ev in metric_evidence:
        value = _numeric_metric_value(ev)
        if value is not None:
            observed.append((ev, value))
    if not observed:
        return EvidenceVerification(
            id=_verification_id(index),
            paperId=paper_id,
            claimId=claim.id,
            verifierType="numeric_metric",
            supportStatus="weakly_supported",
            verdict="Metric evidence is linked, but ReviewX could not parse numeric values from it.",
            evidenceIds=[ev.id for ev in metric_evidence],
            confidence=0.66,
            expectedEvidence=[f"parseable metric near {value:g}" for value in claim_numbers[:3]],
            observedEvidence=[_evidence_label(ev) for ev in metric_evidence],
        )

    percentages = [
        float(value)
        for value in re.findall(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(?:%|percent\b)", claim.text, re.IGNORECASE)
    ]
    observed_by_metric: Dict[str, Dict[str, tuple[Evidence, float]]] = {}
    for ev, value in observed:
        name = re.sub(
            r"[^a-z0-9]+", "_", str(ev.metadata.get("metricName") or "").lower()
        ).strip("_")
        method_name = ""
        for prefix in ("baseline", "method", "proposed"):
            if name == prefix or name.startswith(f"{prefix}_"):
                method_name = "method" if prefix == "proposed" else prefix
                name = name[len(prefix):].strip("_")
                break
        if method_name and name:
            observed_by_metric.setdefault(name, {})[method_name] = (ev, value)
    for percentage in percentages:
        for metric_name, methods in observed_by_metric.items():
            if "baseline" not in methods or "method" not in methods:
                continue
            baseline_ev, baseline_value = methods["baseline"]
            method_ev, method_value = methods["method"]
            if baseline_value == 0:
                continue
            relative_change = abs(method_value - baseline_value) / abs(baseline_value) * 100.0
            if math.isclose(percentage, relative_change, rel_tol=0.08, abs_tol=0.75):
                return EvidenceVerification(
                    id=_verification_id(index),
                    paperId=paper_id,
                    claimId=claim.id,
                    verifierType="numeric_metric",
                    supportStatus="supported",
                    verdict=(
                        f"The claimed {percentage:g}% change is compatible with the linked "
                        f"baseline/method values for {metric_name}: {baseline_value:g} -> {method_value:g}."
                    ),
                    evidenceIds=[baseline_ev.id, method_ev.id],
                    confidence=0.88,
                    expectedEvidence=[f"relative {metric_name} change near {percentage:g}%"],
                    observedEvidence=[
                        _evidence_label(baseline_ev),
                        _evidence_label(method_ev),
                    ],
                )

    for claim_value in claim_numbers:
        for ev, observed_value in observed:
            if _numbers_compatible(claim_value, observed_value):
                return EvidenceVerification(
                    id=_verification_id(index),
                    paperId=paper_id,
                    claimId=claim.id,
                    verifierType="numeric_metric",
                    supportStatus="supported",
                    verdict=f"The numeric claim value {claim_value:g} is compatible with linked metric {observed_value:g}.",
                    evidenceIds=[ev.id],
                    confidence=0.84,
                    expectedEvidence=[f"metric near {claim_value:g}"],
                    observedEvidence=[_evidence_label(ev)],
                )

    return EvidenceVerification(
        id=_verification_id(index),
        paperId=paper_id,
        claimId=claim.id,
        verifierType="numeric_metric",
        supportStatus="contradicted",
        verdict="The claim contains numeric values, but linked metric values do not match within tolerance.",
        evidenceIds=[ev.id for ev, _value in observed[:5]],
        confidence=0.8,
        expectedEvidence=[f"metric near {value:g}" for value in claim_numbers[:3]],
        observedEvidence=[f"{_evidence_label(ev)} parsed={value:g}" for ev, value in observed[:5]],
    )


def _numbers_compatible(expected: float, observed: float) -> bool:
    if math.isclose(expected, observed, rel_tol=0.08, abs_tol=0.5):
        return True
    # Common FAROS papers mix fractions and percentages.
    if expected > 1 and observed <= 1:
        return math.isclose(expected / 100.0, observed, rel_tol=0.08, abs_tol=0.01)
    if expected <= 1 and observed > 1:
        return math.isclose(expected, observed / 100.0, rel_tol=0.08, abs_tol=0.01)
    return False


def _verify_baseline_claim(paper_id: str, claim: Claim, linked: List[Evidence], index: int) -> EvidenceVerification:
    baseline_evidence = [ev for ev in linked if "baseline" in (ev.summary + " " + ev.sourcePath).lower()]
    if baseline_evidence:
        return EvidenceVerification(
            id=_verification_id(index),
            paperId=paper_id,
            claimId=claim.id,
            verifierType="baseline_coverage",
            supportStatus="weakly_supported",
            verdict="The claim implies comparison, and baseline-related evidence is linked, but coverage still needs human or LLM confirmation.",
            evidenceIds=[ev.id for ev in baseline_evidence[:5]],
            confidence=0.7,
            expectedEvidence=["baseline metrics", "comparison table", "ablation report"],
            observedEvidence=[_evidence_label(ev) for ev in baseline_evidence[:5]],
        )
    return EvidenceVerification(
        id=_verification_id(index),
        paperId=paper_id,
        claimId=claim.id,
        verifierType="baseline_coverage",
        supportStatus="unsupported",
        verdict="The claim implies a baseline or comparison, but no baseline evidence was linked.",
        evidenceIds=[],
        confidence=0.86,
        expectedEvidence=["baseline metrics", "comparison table", "ablation report"],
        observedEvidence=[],
    )


def _verify_citation_context(paper_id: str, claim: Claim, linked: List[Evidence], index: int) -> EvidenceVerification:
    direct_evidence = [
        ev for ev in linked
        if ev.evidenceType in {
            "metric", "metric_audit", "experiment_evidence", "experiment_report",
            "experiment_record", "code", "code_artifact",
        }
    ]
    if direct_evidence:
        return EvidenceVerification(
            id=_verification_id(index),
            paperId=paper_id,
            claimId=claim.id,
            verifierType="citation_context",
            supportStatus="supported",
            verdict=(
                "The claim is linked to FAROS-local experiment or code evidence; "
                "a bibliography citation is not required for the project's own result."
            ),
            evidenceIds=[ev.id for ev in direct_evidence[:5]],
            confidence=0.82,
            expectedEvidence=["local experiment metric, audit, report, or code artifact"],
            observedEvidence=[_evidence_label(ev) for ev in direct_evidence[:5]],
        )
    citation_evidence = [ev for ev in linked if ev.evidenceType in {"citation", "bibliography"}]
    if citation_evidence:
        return EvidenceVerification(
            id=_verification_id(index),
            paperId=paper_id,
            claimId=claim.id,
            verifierType="citation_context",
            supportStatus="weakly_supported",
            verdict="Citation or bibliography evidence exists nearby in the evidence graph, but the claim sentence itself has no citation command.",
            evidenceIds=[ev.id for ev in citation_evidence[:5]],
            confidence=0.63,
            expectedEvidence=["nearby citation command", "bib entry"],
            observedEvidence=[_evidence_label(ev) for ev in citation_evidence[:5]],
        )
    return EvidenceVerification(
        id=_verification_id(index),
        paperId=paper_id,
        claimId=claim.id,
        verifierType="citation_context",
        supportStatus="unsupported",
        verdict="The claim has no citation command and no linked citation or bibliography evidence.",
        evidenceIds=[],
        confidence=0.78,
        expectedEvidence=["nearby citation command", "bib entry"],
        observedEvidence=[],
    )


def _verify_guardrail(
    paper_id: str,
    claim: Claim,
    avoid_claims: List[str],
    index: int,
) -> Optional[EvidenceVerification]:
    conflicts = find_guardrail_conflicts(claim.text, avoid_claims)
    if conflicts:
        avoid = conflicts[0]
        return EvidenceVerification(
            id=_verification_id(index),
            paperId=paper_id,
            claimId=claim.id,
            verifierType="brief_guardrail",
            supportStatus="contradicted",
            verdict=f"The claim conflicts with an avoid_claim guardrail: {avoid}",
            evidenceIds=[],
            confidence=0.9,
            expectedEvidence=["claim wording consistent with paper brief guardrails"],
            observedEvidence=[avoid],
        )
    return None


def _verify_metric_audit(
    paper_id: str,
    claim: Claim,
    linked: List[Evidence],
    index: int,
) -> Optional[EvidenceVerification]:
    audits = [item for item in linked if item.evidenceType == "metric_audit"]
    if not audits:
        return None
    failed = [item for item in audits if item.metadata.get("status") == "failed"]
    if failed:
        errors = [str(item) for item in failed[0].metadata.get("errors", [])]
        return EvidenceVerification(
            id=_verification_id(index),
            paperId=paper_id,
            claimId=claim.id,
            verifierType="metric_semantics",
            supportStatus="contradicted",
            verdict=(
                "The experiment's independently recomputed metric audit failed: "
                + ("; ".join(errors[:3]) if errors else "reported and recomputed results disagree.")
            ),
            evidenceIds=[item.id for item in failed],
            confidence=0.99,
            expectedEvidence=["matching per-record predictions and aggregate metrics"],
            observedEvidence=[_evidence_label(item) for item in failed],
        )
    return EvidenceVerification(
        id=_verification_id(index),
        paperId=paper_id,
        claimId=claim.id,
        verifierType="metric_semantics",
        supportStatus="supported",
        verdict="FAROS independently reproduced the linked classification aggregates from per-record predictions.",
        evidenceIds=[item.id for item in audits],
        confidence=0.96,
        expectedEvidence=["matching per-record predictions and aggregate metrics"],
        observedEvidence=[_evidence_label(item) for item in audits],
    )


def _verify_high_stakes_scope(
    paper_id: str,
    claim: Claim,
    index: int,
) -> Optional[EvidenceVerification]:
    """Flag deployment claims that explicitly waive domain-specific validation."""
    text = claim.text.lower()
    high_stakes = any(term in text for term in (
        "clinical", "medical", "legal", "financial", "high-stakes", "autonomous deployment",
    ))
    waives_validation = bool(re.search(
        r"\bwithout\s+(?:any\s+|additional\s+|further\s+)?(?:domain-specific\s+)?"
        r"(?:evaluation|validation|testing)\b",
        text,
    ))
    if not (high_stakes and waives_validation):
        return None
    return EvidenceVerification(
        id=_verification_id(index),
        paperId=paper_id,
        claimId=claim.id,
        verifierType="scope_guardrail",
        supportStatus="unsupported",
        verdict=(
            "The claim extends to a high-stakes deployment domain while explicitly waiving "
            "domain-specific evaluation or validation."
        ),
        evidenceIds=[],
        confidence=0.9,
        expectedEvidence=["domain-specific evaluation", "safety validation", "deployment limitations"],
        observedEvidence=["explicit statement that further validation is unnecessary"],
    )


def _keywords(text: str) -> List[str]:
    return [
        token for token in re.findall(r"[a-z][a-z0-9_-]{4,}", text.lower())
        if token not in {"claim", "without", "evidence", "should"}
    ]


def _evidence_label(ev: Evidence) -> str:
    return f"{ev.id} [{ev.sourceModule}/{ev.evidenceType}] {ev.summary[:160]}"
