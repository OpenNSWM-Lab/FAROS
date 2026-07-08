"""Evidence verification for ReviewX claim support.

The evidence graph links potentially relevant artifacts. This verifier adds a
second pass that asks a stricter question: does the linked evidence actually
support, weakly support, contradict, or fail to support each claim?
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, Optional

from app.modules.review.reviewx_models import Claim, Evidence, EvidenceVerification


_NUMERIC_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(%|percent|x|times|k|ms|s|tokens?|accuracy|f1|auc)?\b", re.IGNORECASE)
_BASELINE_RE = re.compile(r"\b(baseline|ablation|compare|comparison|outperform|state-of-the-art|sota)\b", re.IGNORECASE)
_CITATION_RE = re.compile(r"\\cite[p|t]?\{[^}]+\}")


def verify_claim_evidence(
    paper: Dict[str, Any],
    claims: List[Claim],
    evidence: List[Evidence],
    links: Dict[str, List[str]],
) -> List[EvidenceVerification]:
    evidence_by_id = {ev.id: ev for ev in evidence}
    brief = paper.get("briefJson") or {}
    avoid_claims = brief.get("avoid_claims", []) if isinstance(brief, dict) else []
    verifications: List[EvidenceVerification] = []

    for claim in claims:
        linked = [evidence_by_id[eid] for eid in links.get(claim.id, []) if eid in evidence_by_id]
        linked_ids = [ev.id for ev in linked]
        claim_numbers = _extract_numbers(claim.text)

        if claim_numbers:
            verifications.append(_verify_numeric_claim(paper["id"], claim, linked, claim_numbers, len(verifications)))

        if _BASELINE_RE.search(claim.text):
            verifications.append(_verify_baseline_claim(paper["id"], claim, linked, len(verifications)))

        if claim.claimType in {"method", "performance"} and not _CITATION_RE.search(claim.text):
            verifications.append(_verify_citation_context(paper["id"], claim, linked, len(verifications)))

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
    claim_text = claim.text.lower()
    for avoid in avoid_claims:
        tokens = _keywords(str(avoid))
        if tokens and sum(1 for token in tokens if token in claim_text) >= max(1, min(3, len(tokens) // 2)):
            return EvidenceVerification(
                id=_verification_id(index),
                paperId=paper_id,
                claimId=claim.id,
                verifierType="brief_guardrail",
                supportStatus="contradicted",
                verdict=f"The claim overlaps with an avoid_claim guardrail: {avoid}",
                evidenceIds=[],
                confidence=0.88,
                expectedEvidence=["claim wording consistent with paper brief guardrails"],
                observedEvidence=[str(avoid)],
            )
    return None


def _keywords(text: str) -> List[str]:
    return [
        token for token in re.findall(r"[a-z][a-z0-9_-]{4,}", text.lower())
        if token not in {"claim", "without", "evidence", "should"}
    ]


def _evidence_label(ev: Evidence) -> str:
    return f"{ev.id} [{ev.sourceModule}/{ev.evidenceType}] {ev.summary[:160]}"
