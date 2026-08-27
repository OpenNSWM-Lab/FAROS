"""Conservative semantic matching for paper-brief avoid-claim guardrails."""

from __future__ import annotations

import re
from typing import Iterable


_LIMITATION_RE = re.compile(
    r"\b(?:no|not|never|without|lack(?:s|ed|ing)?|cannot|can't|doesn't|does not|"
    r"didn't|did not|hasn't|has not|have not|unproven|unknown|only synthetic|"
    r"requires? further|needs? further|future validation|future work|must extend|"
    r"to ascertain|remains? (?:an )?(?:open|unvalidated)|will require)\b",
    re.IGNORECASE,
)
_STRONG_ASSERTION_RE = re.compile(
    r"\b(?:prove[sd]?|guarantee[sd]?|demonstrate[sd]?|establish(?:es|ed)?|"
    r"validate[sd]?|generalize[sd]?|outperform(?:s|ed)?|improve[sd]?|reduce[sd]?|"
    r"achieve[sd]?)\b",
    re.IGNORECASE,
)
_STOPWORDS = {
    "about", "anything", "claim", "claims", "could", "from", "other",
    "should", "than", "that", "their", "these", "this", "those", "without",
}


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z][a-z0-9_-]{3,}", text.lower())
        if token not in _STOPWORDS
    }


def _states_limitation(text: str) -> bool:
    return bool(_LIMITATION_RE.search(text))


def _conflicts_with_guardrail(claim_text: str, avoid_text: str) -> bool:
    claim = " ".join(claim_text.lower().split())
    avoid = " ".join(avoid_text.lower().split())

    if "fixed-seed synthetic" in avoid or "fixed seed synthetic" in avoid:
        if "synthetic" in claim and ("fixed-seed" in claim or "fixed seed" in claim):
            return False
        conflicting_benchmark = bool(re.search(
            r"\b(?:real-world|external|human-collected|production|naturalistic)\b.{0,45}"
            r"\b(?:benchmark|dataset|evaluation|validation)\b|"
            r"\b(?:benchmark|dataset)\b.{0,45}\b(?:real-world|external|production)\b",
            claim,
        ))
        return conflicting_benchmark and not _states_limitation(claim)

    if "unsupported claim rate" in avoid and re.search(r"\b(?:improv|reduc|outperform)", avoid):
        return bool(
            re.search(r"\bunsupported claim rate\b", claim)
            and re.search(r"\b(?:improv|reduc|decreas|lower|outperform)", claim)
            and not _states_limitation(claim)
        )

    if "eliminate" in avoid and "hallucination" in avoid:
        elimination_claim = bool(re.search(
            r"\b(?:eliminat\w*|eradicate\w*|remove[sd]? all|zero)\b.{0,45}"
            r"\bhallucination\w*\b|"
            r"\bhallucination\w*\b.{0,45}\b(?:eliminat\w*|eradicate\w*|zero)\b",
            claim,
        ))
        return elimination_claim and not _states_limitation(claim)

    if "all metrics" in avoid or ("acknowledge" in avoid and "f1" in avoid):
        blanket_outperformance = bool(re.search(
            r"\b(?:all|every) metrics?\b|"
            r"\b(?:uniformly|consistently) (?:better|improv\w*|outperform\w*)\b|"
            r"\b(?:overall|across (?:all )?metrics?)\b.{0,40}"
            r"\b(?:better|improv\w*|outperform\w*|superior)\b|"
            r"\b(?:outperform\w*|superior)\b.{0,40}\b(?:overall|across (?:all )?metrics?)\b",
            claim,
        ))
        return blanket_outperformance and not _states_limitation(claim)

    if "ece" in avoid or "calibration error" in avoid:
        numeric_values = [
            float(value)
            for value in re.findall(r"(?<![\w.])\d+(?:\.\d+)?", claim)
        ]
        explicitly_dismisses_error = bool(re.search(
            r"\b(?:negligible|perfect(?:ly)?)\b.{0,35}\b(?:ece|calibration error|calibrat)\b|"
            r"\b(?:ece|calibration error|calibrat\w*)\b.{0,35}\b(?:negligible|perfect(?:ly)?)\b",
            claim,
        ))
        if any(value > 0 for value in numeric_values) and not explicitly_dismisses_error:
            return False
        hides_error = bool(re.search(
            r"\b(?:zero|no|negligible|perfect(?:ly)?)\b.{0,35}\b(?:ece|calibration error|calibrat)",
            claim,
        ) or re.search(
            r"\b(?:ece|calibration error)\b.{0,25}\b(?:is|was|of)?\s*"
            r"(?:0(?:\.0+)?(?![\d.])|zero|negligible)",
            claim,
        ))
        return hides_error and "non-zero" not in claim and "nonzero" not in claim

    if "generaliz" in avoid and "real-world" in avoid:
        generalization = bool(re.search(
            r"\bgeneraliz\w*\b.{0,60}\b(?:real-world|deployment|unseen domain)\b|"
            r"\b(?:real-world|deployment|unseen domain)\b.{0,60}\bgeneraliz\w*\b",
            claim,
        ))
        return generalization and not _states_limitation(claim)

    if any(term in avoid for term in ("external validation", "real-world validation", "human validation")):
        validation = bool(re.search(
            r"\b(?:external|real-world|human(?:-subject|-rated)?)\b.{0,45}"
            r"\b(?:validat\w*|evaluat\w*|study|benchmark)\b|"
            r"\b(?:validat\w*|evaluat\w*)\b.{0,45}\b(?:external|real-world|human)\b",
            claim,
        ))
        return validation and not _states_limitation(claim)

    if "state-of-the-art" in avoid or re.search(r"\bsota\b", avoid):
        superlative_claim = bool(re.search(
            r"\b(?:state-of-the-art|sota|best(?:-in-class)?|leading|"
            r"outperform(?:s|ed)?|surpass(?:es|ed)?)\b",
            claim,
        ))
        return superlative_claim and not _states_limitation(claim)

    if not _STRONG_ASSERTION_RE.search(claim) or _states_limitation(claim):
        return False
    overlap = _tokens(claim) & _tokens(avoid)
    return len(overlap) >= 2


def find_guardrail_conflicts(claim_text: str, avoid_claims: Iterable[str]) -> list[str]:
    """Return only explicit semantic conflicts, not mere topic overlap."""
    return [
        str(avoid)
        for avoid in avoid_claims
        if _conflicts_with_guardrail(claim_text, str(avoid))
    ]
