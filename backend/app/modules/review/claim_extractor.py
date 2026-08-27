"""Heuristic claim extraction for ReviewX.

The first implementation intentionally avoids requiring an LLM. It extracts
high-value claims from FAROS paper briefs and LaTeX text, then marks which
claims need evidence. The output is deterministic and suitable for tests.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

from app.modules.review.reviewx_models import Claim, SourceSpan


_EVIDENCE_ASSERTION_PATTERN = (
    r"\b(?:the\s+|this\s+|existing\s+|prior\s+)?(?:cited\s+)?"
    r"(?:evidence|results?|work|stud(?:y|ies))\b.{0,90}"
    r"\b(?:establish(?:es|ed)?|demonstrate(?:s|d)?|show(?:s|ed)?|prove(?:s|d)?|support(?:s|ed)?)\b"
)
_EVIDENCE_ASSERTION_RE = re.compile(_EVIDENCE_ASSERTION_PATTERN, re.IGNORECASE)
_CLAIM_PATTERNS = [
    r"\bwe (introduce|propose|present|develop|design|show|demonstrate|achieve|outperform|improve|reduce)\b",
    r"\bour (method|approach|framework|system|model|algorithm)\b",
    r"\b(up to|at least|more than|less than|significant|state-of-the-art|sota)\b",
    r"\b\d+(\.\d+)?\s*(%|percent|x|times|k|ms|s|tokens?)(?=\W|$)",
    r"\b(no|without)\s+(degrading|sacrificing|reducing|hurting)\b",
    _EVIDENCE_ASSERTION_PATTERN,
]
_CLAIM_RE = re.compile("|".join(_CLAIM_PATTERNS), re.IGNORECASE)
_NUMERIC_RE = re.compile(
    r"\b\d+(\.\d+)?\s*(%|percent|x|times|k|ms|s|tokens?|accuracy|f1|auc)(?=\W|$)",
    re.IGNORECASE,
)
_CITATION_RE = re.compile(r"\\cite[p|t]?\{[^}]+\}")
_SECTION_RE = re.compile(r"\\section\*?\{([^}]+)\}|\\subsection\*?\{([^}]+)\}", re.IGNORECASE)
_ABBREVIATION_RE = re.compile(r"\b(?:e\.g|i\.e|vs|etc|et al|fig|eq|sec|dr|prof)\.", re.IGNORECASE)
_MAX_CLAIMS = 40
_HIGH_STAKES_RE = re.compile(
    r"\b(clinical|medical|legal|high-stakes|safe deployment|unseen|distribution shift|"
    r"all existing|state-of-the-art|without any loss|eliminates?|guarantees?|proves?)\b",
    re.IGNORECASE,
)


def _clean_latex(text: str) -> str:
    comment = re.search(r"(?<!\\)%", text)
    if comment:
        prefix = text[:comment.start()].rstrip()
        if prefix and prefix[-1] not in ".!?":
            boundary = max(prefix.rfind("."), prefix.rfind("!"), prefix.rfind("?"))
            prefix = prefix[:boundary + 1] if boundary >= 0 else ""
        text = prefix
    text = text.replace(r"\%", "%")
    text = re.sub(r"\\(emph|textbf|textit)\{([^}]*)\}", r"\2", text)
    text = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{[^}]*\})?", " ", text)
    text = text.replace("~", " ")
    return re.sub(r"\s+", " ", text).strip()


def _split_sentences(text: str) -> Iterable[str]:
    protected = _ABBREVIATION_RE.sub(lambda match: match.group(0).replace(".", "\x00"), text)
    for part in re.split(r"(?<=[.!?])\s+", protected):
        sentence = part.replace("\x00", ".").strip()
        if 40 <= len(sentence) <= 450:
            yield sentence


def _claim_type(text: str) -> str:
    lowered = text.lower()
    if _NUMERIC_RE.search(text) or any(w in lowered for w in ["outperform", "improve", "reduce", "achieve"]):
        return "performance"
    if any(w in lowered for w in ["introduce", "propose", "present", "framework", "algorithm"]):
        return "method"
    if "without" in lowered or "preserve" in lowered or "faithful" in lowered:
        return "robustness"
    return "content"


def _requires_evidence(text: str, claim_type: str) -> bool:
    lowered = text.lower()
    return (
        claim_type in {"performance", "robustness"}
        or _NUMERIC_RE.search(text) is not None
        or _EVIDENCE_ASSERTION_RE.search(text) is not None
        or any(w in lowered for w in ["demonstrate", "show", "prove", "outperform", "baseline", "state-of-the-art"])
    )


def _importance(text: str, source: str) -> str:
    if source in {"brief.core_claim", "brief.contribution"}:
        return "high"
    if _NUMERIC_RE.search(text):
        return "high"
    return "medium"


def _claim_priority(claim: Claim) -> float:
    """Rank evidence-sensitive claims without using benchmark-specific markers."""
    score = 0.0
    if claim.sourceSpan.section == "Brief":
        score += 8.0
    if claim.importance == "high":
        score += 4.0
    if claim.requiresEvidence:
        score += 3.0
    if claim.claimType == "robustness":
        score += 2.0
    if "numeric_claim" in claim.riskHints:
        score += 3.0
    if any(hint.startswith("citation_key:") for hint in claim.riskHints):
        score += 2.0
    if _HIGH_STAKES_RE.search(claim.text):
        score += 4.0
    return score


def _select_claims(claims: List[Claim], limit: int = _MAX_CLAIMS) -> List[Claim]:
    """Keep broad section coverage, then fill remaining slots by review risk."""
    if len(claims) <= limit:
        return claims

    indexed = list(enumerate(claims))
    by_section: Dict[tuple[str, str], List[tuple[int, Claim]]] = {}
    for item in indexed:
        claim = item[1]
        section_key = (claim.sourceSpan.file, claim.sourceSpan.section)
        by_section.setdefault(section_key, []).append(item)

    selected_indexes: set[int] = set()
    # Two representatives per section avoid the previous first-40 position bias.
    section_candidates: List[tuple[float, int]] = []
    for section_claims in by_section.values():
        ranked = sorted(section_claims, key=lambda item: (-_claim_priority(item[1]), item[0]))
        section_candidates.extend((_claim_priority(claim), index) for index, claim in ranked[:2])
    for _, index in sorted(section_candidates, key=lambda item: (-item[0], item[1]))[:limit]:
        selected_indexes.add(index)

    for index, claim in sorted(indexed, key=lambda item: (-_claim_priority(item[1]), item[0])):
        if len(selected_indexes) >= limit:
            break
        selected_indexes.add(index)

    return [claim for index, claim in indexed if index in selected_indexes]


def extract_claims(artifacts: Dict[str, Any]) -> List[Claim]:
    paper = artifacts["paper"]
    paper_id = paper["id"]
    claims: List[Claim] = []
    seen: set[str] = set()

    def add_claim(text: str, file: str, section: str, line: int | None, source: str, raw_text: str | None = None):
        normalized = re.sub(r"\s+", " ", text).strip()
        key = normalized.lower()
        if len(normalized) < 25 or key in seen:
            return
        seen.add(key)
        claim_type = _claim_type(normalized)
        risk_hints = []
        if _NUMERIC_RE.search(normalized):
            risk_hints.append("numeric_claim")
        citation_text = raw_text if raw_text is not None else text
        citation_keys = []
        for citation_group in _CITATION_RE.findall(citation_text):
            raw_group = citation_group
            if "{" in raw_group and "}" in raw_group:
                raw_group = raw_group.split("{", 1)[1].rsplit("}", 1)[0]
            citation_keys.extend([key.strip() for key in raw_group.split(",") if key.strip()])
        if citation_keys:
            risk_hints.extend([f"citation_key:{key}" for key in citation_keys[:5]])
        if _EVIDENCE_ASSERTION_RE.search(normalized):
            risk_hints.append("evidence_assertion")
        if not citation_keys and claim_type in {"performance", "method"}:
            risk_hints.append("citation_or_evidence_needed")
        elif not citation_keys and "evidence_assertion" in risk_hints:
            risk_hints.append("citation_or_evidence_needed")
        claims.append(Claim(
            id=f"claim_{len(claims) + 1:03d}",
            paperId=paper_id,
            text=normalized,
            claimType=claim_type,
            importance=_importance(normalized, source),
            requiresEvidence=_requires_evidence(normalized, claim_type),
            sourceSpan=SourceSpan(file=file, section=section, line=line),
            riskHints=risk_hints,
        ))

    latex_files = artifacts.get("latexFiles", [])
    has_manuscript = any(
        isinstance(item, dict) and str(item.get("content") or "").strip()
        for item in latex_files
    )
    brief = paper.get("briefJson") or {}
    # The brief is planning state and can become stale after evidence-driven
    # manuscript rewrites. Review it only when no manuscript exists yet.
    if isinstance(brief, dict) and not has_manuscript:
        if brief.get("core_claim"):
            add_claim(str(brief["core_claim"]), "paper.meta.json", "Brief", None, "brief.core_claim")
        for contribution in brief.get("contributions", []) or []:
            add_claim(str(contribution), "paper.meta.json", "Brief", None, "brief.contribution")

    for latex_file in latex_files:
        section = "Preamble"
        for line_no, raw_line in enumerate(latex_file.get("content", "").splitlines(), 1):
            match = _SECTION_RE.search(raw_line)
            if match:
                section = match.group(1) or match.group(2) or section
                continue
            if not _CLAIM_RE.search(raw_line):
                continue
            for sentence in _split_sentences(_clean_latex(raw_line)):
                if _CLAIM_RE.search(sentence):
                    add_claim(sentence, latex_file["path"], section, line_no, "latex", raw_text=raw_line)

    return _select_claims(claims)
