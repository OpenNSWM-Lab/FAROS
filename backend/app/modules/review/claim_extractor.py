"""Heuristic claim extraction for ReviewX.

The first implementation intentionally avoids requiring an LLM. It extracts
high-value claims from FAROS paper briefs and LaTeX text, then marks which
claims need evidence. The output is deterministic and suitable for tests.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

from app.modules.review.reviewx_models import Claim, SourceSpan


_CLAIM_PATTERNS = [
    r"\bwe (introduce|propose|present|develop|design|show|demonstrate|achieve|outperform|improve|reduce)\b",
    r"\bour (method|approach|framework|system|model|algorithm)\b",
    r"\b(up to|at least|more than|less than|significant|state-of-the-art|sota)\b",
    r"\b\d+(\.\d+)?\s*(%|percent|x|times|k|ms|s|tokens?)\b",
    r"\b(no|without)\s+(degrading|sacrificing|reducing|hurting)\b",
]
_CLAIM_RE = re.compile("|".join(_CLAIM_PATTERNS), re.IGNORECASE)
_NUMERIC_RE = re.compile(r"\b\d+(\.\d+)?\s*(%|percent|x|times|k|ms|s|tokens?|accuracy|f1|auc)\b", re.IGNORECASE)
_CITATION_RE = re.compile(r"\\cite[p|t]?\{[^}]+\}")
_SECTION_RE = re.compile(r"\\section\*?\{([^}]+)\}|\\subsection\*?\{([^}]+)\}", re.IGNORECASE)


def _clean_latex(text: str) -> str:
    text = re.sub(r"%.*", "", text)
    text = re.sub(r"\\(emph|textbf|textit)\{([^}]*)\}", r"\2", text)
    text = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{[^}]*\})?", " ", text)
    text = text.replace("~", " ")
    return re.sub(r"\s+", " ", text).strip()


def _split_sentences(text: str) -> Iterable[str]:
    for part in re.split(r"(?<=[.!?])\s+", text):
        sentence = part.strip()
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
        or any(w in lowered for w in ["demonstrate", "show", "prove", "outperform", "baseline", "state-of-the-art"])
    )


def _importance(text: str, source: str) -> str:
    if source in {"brief.core_claim", "brief.contribution"}:
        return "high"
    if _NUMERIC_RE.search(text):
        return "high"
    return "medium"


def extract_claims(artifacts: Dict[str, Any]) -> List[Claim]:
    paper = artifacts["paper"]
    paper_id = paper["id"]
    claims: List[Claim] = []
    seen: set[str] = set()

    def add_claim(text: str, file: str, section: str, line: int | None, source: str):
        normalized = re.sub(r"\s+", " ", text).strip()
        key = normalized.lower()
        if len(normalized) < 25 or key in seen:
            return
        seen.add(key)
        claim_type = _claim_type(normalized)
        risk_hints = []
        if _NUMERIC_RE.search(normalized):
            risk_hints.append("numeric_claim")
        if not _CITATION_RE.search(text) and claim_type in {"performance", "method"}:
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

    brief = paper.get("briefJson") or {}
    if isinstance(brief, dict):
        if brief.get("core_claim"):
            add_claim(str(brief["core_claim"]), "paper.meta.json", "Brief", None, "brief.core_claim")
        for contribution in brief.get("contributions", []) or []:
            add_claim(str(contribution), "paper.meta.json", "Brief", None, "brief.contribution")

    for latex_file in artifacts.get("latexFiles", []):
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
                    add_claim(sentence, latex_file["path"], section, line_no, "latex")

    return claims[:40]
