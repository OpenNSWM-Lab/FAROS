"""Deterministic topic relevance utilities for Idea evidence retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import re
from typing import Any, Iterable, Mapping, Sequence

from app.services.search_service import SearchResult, tokenize_topic_text


class EvidenceTier(str, Enum):
    DIRECT = "direct"
    TRANSFERABLE = "transferable"
    REJECTED = "rejected"


GENERIC_TERMS = frozenset({
    "analysis",
    "application",
    "approach",
    "chamber",
    "dream",
    "evaluation",
    "exploration",
    "framework",
    "generation",
    "method",
    "model",
    "outcome",
    "outcomes",
    "potential",
    "predict",
    "predicting",
    "prediction",
    "red",
    "research",
    "study",
    "system",
    "using",
})


@dataclass(frozen=True)
class TopicIntentProfile:
    core_anchors: tuple[str, ...]
    task_anchors: tuple[str, ...]
    method_anchors: tuple[str, ...]
    evaluation_anchors: tuple[str, ...]
    generic_terms: tuple[str, ...]

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "coreAnchors": list(self.core_anchors),
            "taskAnchors": list(self.task_anchors),
            "methodAnchors": list(self.method_anchors),
            "evaluationAnchors": list(self.evaluation_anchors),
            "genericTerms": list(self.generic_terms),
        }


@dataclass(frozen=True)
class EvidenceAssessment:
    tier: EvidenceTier
    score: float
    decisive_anchors: tuple[str, ...]
    score_components: Mapping[str, float]
    rejection_reason: str = ""


@dataclass(frozen=True)
class DedupeOutcome:
    results: tuple[SearchResult, ...]
    merge_count: int


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _quoted_phrases(values: Iterable[str]) -> tuple[str, ...]:
    phrases: list[str] = []
    for value in values:
        phrases.extend(
            match.strip().lower()
            for match in re.findall(r"[\"']([^\"']{4,100})[\"']", value or "")
        )
    return _unique(phrases)


def _discriminative_tokens(values: Iterable[str]) -> tuple[str, ...]:
    return _unique(
        token
        for value in values
        for token in tokenize_topic_text(value)
        if token not in GENERIC_TERMS and len(token) >= 3
    )


def build_topic_intent_profile(
    *,
    seed: str,
    domain: str,
    role_queries: Mapping[str, Sequence[str]],
) -> TopicIntentProfile:
    domain_queries = list(role_queries.get("domain", ()))
    task_queries = list(role_queries.get("task", ()))
    method_queries = list(role_queries.get("method", ()))
    evaluation_queries = list(role_queries.get("evaluation", ()))
    core_phrases = _quoted_phrases([seed, domain, *domain_queries, *task_queries])
    core_tokens = _discriminative_tokens([seed, domain, *domain_queries])
    return TopicIntentProfile(
        core_anchors=_unique([*core_phrases, *core_tokens]),
        task_anchors=_discriminative_tokens(task_queries),
        method_anchors=_discriminative_tokens(method_queries),
        evaluation_anchors=_discriminative_tokens(evaluation_queries),
        generic_terms=tuple(sorted(GENERIC_TERMS)),
    )


def _anchor_in_text(anchor: str, text: str) -> bool:
    if not anchor:
        return False
    if re.search(r"[\u4e00-\u9fff]", anchor) or " " in anchor:
        return anchor in text
    return re.search(rf"(?<![a-z0-9]){re.escape(anchor)}(?![a-z0-9])", text) is not None


def _hits(text: str, anchors: Sequence[str]) -> tuple[str, ...]:
    return tuple(anchor for anchor in anchors if _anchor_in_text(anchor, text))


def assess_search_result(
    result: SearchResult,
    profile: TopicIntentProfile,
) -> EvidenceAssessment:
    text = f"{result.title} {result.abstract}".lower().replace("-", " ")
    phrase_hits = tuple(
        anchor
        for anchor in profile.core_anchors
        if " " in anchor and _anchor_in_text(anchor, text)
    )
    core_hits = _hits(
        text,
        tuple(anchor for anchor in profile.core_anchors if " " not in anchor),
    )
    task_hits = _hits(text, profile.task_anchors)
    method_hits = _hits(text, profile.method_anchors)
    evaluation_hits = _hits(text, profile.evaluation_anchors)

    components = {
        "corePhrase": min(0.55, 0.55 * len(phrase_hits)),
        "coreTerms": min(0.35, 0.12 * len(core_hits)),
        "task": min(0.25, 0.10 * len(task_hits)),
        "methodEvaluation": min(
            0.20,
            0.06 * (len(method_hits) + len(evaluation_hits)),
        ),
        "provider": min(
            0.10,
            max(0.0, float(result.relevance_score or 0.0)) * 0.10,
        ),
    }
    score = min(1.0, sum(components.values()))
    decisive = _unique([
        *phrase_hits,
        *core_hits,
        *task_hits,
        *method_hits,
        *evaluation_hits,
    ])
    has_supporting_signal = bool(task_hits or method_hits or evaluation_hits)

    if (phrase_hits or len(core_hits) >= 2) and has_supporting_signal:
        tier = EvidenceTier.DIRECT
        rejection_reason = ""
    elif len(task_hits) >= 2 and bool(method_hits or evaluation_hits):
        tier = EvidenceTier.TRANSFERABLE
        rejection_reason = ""
    else:
        tier = EvidenceTier.REJECTED
        rejection_reason = "generic_overlap_only" if text.strip() else "missing_text"

    return EvidenceAssessment(
        tier=tier,
        score=round(score, 4),
        decisive_anchors=decisive,
        score_components=components,
        rejection_reason=rejection_reason,
    )


def _normalized_title_key(title: str) -> str:
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", (title or "").lower())
    return "title:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def search_result_identity_keys(result: SearchResult) -> tuple[str, ...]:
    keys: list[str] = []
    if result.doi:
        keys.append(f"doi:{result.doi.lower().strip()}")
    if result.arxiv_id:
        keys.append(f"arxiv:{result.arxiv_id.lower().strip()}")
    semantic_id = re.search(r"SemanticScholarID:(\w+)", result.url or "")
    if semantic_id:
        keys.append(f"s2:{semantic_id.group(1).lower()}")
    keys.append(_normalized_title_key(result.title))
    return tuple(keys)


def raw_paper_identity_keys(paper: Any) -> tuple[str, ...]:
    keys: list[str] = []
    doi = getattr(paper, "doi", None)
    arxiv_id = getattr(paper, "arxivId", None)
    semantic_id = getattr(paper, "semanticScholarId", None)
    if doi:
        keys.append(f"doi:{str(doi).lower().strip()}")
    if arxiv_id:
        keys.append(f"arxiv:{str(arxiv_id).lower().strip()}")
    if semantic_id:
        keys.append(f"s2:{str(semantic_id).lower().strip()}")
    keys.append(_normalized_title_key(getattr(paper, "title", "")))
    return tuple(keys)


def _append_unique(current: list[str], incoming: Iterable[str]) -> None:
    for value in incoming:
        if value and value not in current:
            current.append(value)


def _merge_result(target: SearchResult, incoming: SearchResult) -> None:
    _append_unique(target.retrieval_roles, incoming.retrieval_roles)
    _append_unique(target.matched_queries, incoming.matched_queries)
    _append_unique(
        target.retrieval_sources,
        incoming.retrieval_sources or [incoming.source],
    )
    if len(incoming.abstract or "") > len(target.abstract or ""):
        target.abstract = incoming.abstract
    if not target.doi and incoming.doi:
        target.doi = incoming.doi
    if not target.arxiv_id and incoming.arxiv_id:
        target.arxiv_id = incoming.arxiv_id
    if not target.url and incoming.url:
        target.url = incoming.url
    target.relevance_score = max(target.relevance_score, incoming.relevance_score)
    target.citation_count = max(target.citation_count or 0, incoming.citation_count or 0)


def deduplicate_search_results(results: Sequence[SearchResult]) -> DedupeOutcome:
    unique: list[SearchResult] = []
    index: dict[str, SearchResult] = {}
    merge_count = 0
    for result in results:
        keys = search_result_identity_keys(result)
        target = next((index[key] for key in keys if key in index), None)
        if target is not None:
            _merge_result(target, result)
            merge_count += 1
        else:
            target = result
            unique.append(target)
        for key in (*search_result_identity_keys(target), *keys):
            index[key] = target
    return DedupeOutcome(tuple(unique), merge_count)


_TIER_PRIORITY = {
    EvidenceTier.REJECTED.value: 0,
    "unclassified": 1,
    EvidenceTier.TRANSFERABLE.value: 2,
    EvidenceTier.DIRECT.value: 3,
}


def better_evidence_tier(current: str, incoming: str) -> str:
    return max(
        (current, incoming),
        key=lambda tier: _TIER_PRIORITY.get(tier, 0),
    )


def role_requirements_for_paper_type(paper_type: str) -> dict[str, int]:
    normalized = (paper_type or "algorithm").lower()
    if normalized in {"survey", "position", "theory"}:
        return {"domainOrTask": 2, "method": 0, "evaluation": 0}
    if normalized in {"benchmark", "evaluation", "reproducibility"}:
        return {"domainOrTask": 2, "method": 0, "evaluation": 2}
    return {"domainOrTask": 2, "method": 1, "evaluation": 1}


def semantically_eligible_roles(
    tier: str,
    roles: Sequence[str],
) -> tuple[str, ...]:
    if tier == EvidenceTier.DIRECT.value:
        return _unique(roles)
    if tier == EvidenceTier.TRANSFERABLE.value:
        return _unique(role for role in roles if role in {"method", "evaluation"})
    if tier == "unclassified":
        return _unique(roles)
    return ()


def evidence_tier_allows_dimension(tier: str, dimension: str) -> bool:
    if tier in {EvidenceTier.DIRECT.value, "unclassified"}:
        return True
    if tier != EvidenceTier.TRANSFERABLE.value:
        return False
    direct_only = {
        "background",
        "claim",
        "domain",
        "gap",
        "high_risk_qa",
        "novelty",
    }
    return dimension not in direct_only
