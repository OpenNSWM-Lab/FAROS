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
    "accuracy",
    "and",
    "analysis",
    "application",
    "approach",
    "based",
    "benchmark",
    "benchmarks",
    "chamber",
    "dream",
    "evaluation",
    "evaluations",
    "evaluated",
    "evaluating",
    "expert",
    "experts",
    "exploration",
    "framework",
    "for",
    "from",
    "generation",
    "method",
    "metric",
    "metrics",
    "model",
    "models",
    "novelty",
    "outcome",
    "outcomes",
    "potential",
    "predict",
    "predicting",
    "prediction",
    "red",
    "research",
    "score",
    "scores",
    "study",
    "system",
    "the",
    "using",
    "validation",
    "with",
})

CONNECTOR_FACET_WEAK_TERMS = frozenset({"answering", "qa", "question"})

LITERATURE_QUERY_TEMPLATE = (
    "[specific method] for [specific task] in [research domain], "
    "evaluated by [dataset or metric]"
)


@dataclass(frozen=True)
class TopicIntentProfile:
    seed_anchors: tuple[str, ...]
    required_seed_facets: tuple[tuple[str, ...], ...]
    core_anchors: tuple[str, ...]
    task_anchors: tuple[str, ...]
    method_anchors: tuple[str, ...]
    evaluation_anchors: tuple[str, ...]
    generic_terms: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "seedAnchors": list(self.seed_anchors),
            "requiredSeedFacets": [list(facet) for facet in self.required_seed_facets],
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


def _hyphen_phrases(values: Iterable[str]) -> tuple[str, ...]:
    phrases: list[str] = []
    for value in values:
        phrases.extend(
            match.lower().replace("-", " ")
            for match in re.findall(
                r"\b[a-zA-Z0-9]+(?:-[a-zA-Z0-9]+)+\b",
                value or "",
            )
        )
    return _unique(phrases)


def _discriminative_tokens(values: Iterable[str]) -> tuple[str, ...]:
    return _unique(
        token
        for value in values
        for token in tokenize_topic_text(value)
        if token not in GENERIC_TERMS and len(token) >= 3
    )


def _required_seed_facets(seed: str) -> tuple[tuple[str, ...], ...]:
    parts = re.split(r"\s+for\s+", seed or "", maxsplit=1, flags=re.IGNORECASE)
    if len(parts) != 2 or not all(part.strip() for part in parts):
        return ()
    facets = []
    for part in parts:
        anchors = _unique([
            *_hyphen_phrases([part]),
            *(
                token
                for token in _discriminative_tokens([part])
                if token not in CONNECTOR_FACET_WEAK_TERMS
            ),
        ])
        if anchors:
            facets.append(anchors)
    return tuple(facets) if len(facets) == 2 else ()


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
    hyphen_phrases = _hyphen_phrases([seed, domain, *domain_queries, *task_queries])
    seed_anchors = _unique([
        *_hyphen_phrases([seed]),
        *_discriminative_tokens([seed]),
    ])
    core_tokens = _discriminative_tokens([seed, domain, *domain_queries])
    return TopicIntentProfile(
        seed_anchors=seed_anchors,
        required_seed_facets=_required_seed_facets(seed),
        core_anchors=_unique([*core_phrases, *hyphen_phrases, *core_tokens]),
        task_anchors=_discriminative_tokens(task_queries),
        method_anchors=_discriminative_tokens(method_queries),
        evaluation_anchors=_discriminative_tokens(evaluation_queries),
        generic_terms=tuple(sorted(GENERIC_TERMS)),
    )


def diagnose_literature_failure(
    *,
    seed: str,
    raw_result_count: int,
    unique_result_count: int,
    gate: Mapping[str, Any],
    rejection_reason_counts: Mapping[str, Any],
    seed_anchors: Sequence[str],
    repair_queries: Sequence[str] = (),
) -> dict[str, Any]:
    """Turn retrieval telemetry into stable, user-actionable recovery data."""

    requirements = gate.get("requirements")
    if not isinstance(requirements, Mapping):
        requirements = {}

    def _count(value: Any, default: int = 0) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return default

    min_papers = _count(requirements.get("minPaperCount"), 4)
    min_aligned = _count(requirements.get("minAlignedPaperCount"), 3)
    eligible_count = _count(gate.get("paperCount"))
    aligned_count = _count(gate.get("alignedPaperCount"))
    rejected_count = sum(_count(value) for value in rejection_reason_counts.values())
    dominant_reason = ""
    dominant_count = 0
    for reason, value in rejection_reason_counts.items():
        count = _count(value)
        if count > dominant_count:
            dominant_reason = str(reason)
            dominant_count = count

    role_coverage = gate.get("roleCoverage")
    role_issues = []
    if isinstance(role_coverage, Mapping):
        raw_role_issues = role_coverage.get("issues")
        if isinstance(raw_role_issues, Sequence) and not isinstance(raw_role_issues, (str, bytes)):
            role_issues = [str(item) for item in raw_role_issues if str(item).strip()]

    if raw_result_count <= 0:
        code = "no_search_results"
        recommended_action = "retry_search"
        resume_recommended = True
        action_codes = [
            "use_english_academic_terms",
            "wait_for_search_cooldown",
            "resume_after_retry",
        ]
    elif eligible_count == 0 and dominant_reason == "generic_overlap_only":
        code = "seed_too_broad"
        recommended_action = "rewrite_seed"
        resume_recommended = False
        action_codes = [
            "add_task_method_evaluation",
            "use_multiple_discriminative_terms",
            "create_new_session",
        ]
    elif eligible_count < min_papers:
        code = "eligible_pool_too_small"
        recommended_action = "rewrite_seed"
        resume_recommended = False
        action_codes = [
            "broaden_niche_terms",
            "keep_core_task_and_method",
            "create_new_session",
        ]
    elif aligned_count < min_aligned:
        code = "weak_topic_alignment"
        recommended_action = "rewrite_seed"
        resume_recommended = False
        action_codes = [
            "add_domain_and_task_anchors",
            "name_method_or_evaluation_target",
            "create_new_session",
        ]
    elif role_issues:
        code = "missing_evidence_roles"
        recommended_action = "rewrite_seed"
        resume_recommended = False
        action_codes = [
            "cover_missing_evidence_roles",
            "name_method_or_evaluation_target",
            "create_new_session",
        ]
    else:
        code = "evidence_quality_failed"
        recommended_action = "inspect_and_rewrite"
        resume_recommended = False
        action_codes = [
            "inspect_repair_queries",
            "add_task_method_evaluation",
            "create_new_session",
        ]

    return {
        "code": code,
        "seedQuery": seed,
        "rawResultCount": _count(raw_result_count),
        "uniqueResultCount": _count(unique_result_count),
        "eligiblePaperCount": eligible_count,
        "alignedPaperCount": aligned_count,
        "rejectedPaperCount": rejected_count,
        "dominantRejectionReason": dominant_reason or None,
        "dominantRejectionCount": dominant_count,
        "seedAnchors": list(dict.fromkeys(str(anchor) for anchor in seed_anchors if str(anchor).strip()))[:12],
        "roleIssues": role_issues,
        "requirements": {
            "minPaperCount": min_papers,
            "minAlignedPaperCount": min_aligned,
            "minAlignmentScore": requirements.get("minAlignmentScore", 0.32),
        },
        "recommendedAction": recommended_action,
        "resumeRecommended": resume_recommended,
        "actionCodes": action_codes,
        "queryTemplate": LITERATURE_QUERY_TEMPLATE,
        "repairQueries": list(dict.fromkeys(str(query) for query in repair_queries if str(query).strip()))[:6],
    }


def _anchor_in_text(anchor: str, text: str) -> bool:
    if not anchor:
        return False
    if re.search(r"[\u4e00-\u9fff]", anchor) or " " in anchor:
        return anchor in text
    return re.search(rf"(?<![a-z0-9]){re.escape(anchor)}(?![a-z0-9])", text) is not None


def _hits(text: str, anchors: Sequence[str]) -> tuple[str, ...]:
    return tuple(anchor for anchor in anchors if _anchor_in_text(anchor, text))


def _covers_required_seed_facets(
    text: str,
    facets: Sequence[Sequence[str]],
) -> bool:
    for facet in facets:
        phrase_hits = _hits(text, tuple(anchor for anchor in facet if " " in anchor))
        token_anchors = tuple(anchor for anchor in facet if " " not in anchor)
        token_hits = _hits(text, token_anchors)
        required_tokens = min(2, len(token_anchors))
        if not phrase_hits and len(token_hits) < required_tokens:
            return False
    return True


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
    seed_hits = _hits(text, profile.seed_anchors)

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
    has_strong_seed_signal = any(
        " " in anchor or len(anchor) >= 7
        for anchor in seed_hits
    )
    seed_phrase_anchors = tuple(
        anchor for anchor in profile.seed_anchors if " " in anchor
    )
    if profile.required_seed_facets:
        seed_facet_coverage = _covers_required_seed_facets(
            text,
            profile.required_seed_facets,
        )
    elif len(seed_phrase_anchors) >= 2:
        matched_seed_phrases = _hits(text, seed_phrase_anchors)
        phrase_component_tokens = {
            token
            for phrase in matched_seed_phrases
            for token in phrase.split()
        }
        independent_seed_hits = {
            anchor
            for anchor in seed_hits
            if " " not in anchor and anchor not in phrase_component_tokens
        }
        seed_facet_coverage = (
            len(matched_seed_phrases) >= 2
            or (
                bool(matched_seed_phrases)
                and len(independent_seed_hits) >= 2
            )
        )
    else:
        seed_facet_coverage = True

    if (
        phrase_hits or (len(core_hits) >= 2 and has_strong_seed_signal)
    ) and has_supporting_signal and seed_facet_coverage:
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
