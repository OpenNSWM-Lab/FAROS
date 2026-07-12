"""Deterministic topic relevance utilities for Idea evidence retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Iterable, Mapping, Sequence

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
