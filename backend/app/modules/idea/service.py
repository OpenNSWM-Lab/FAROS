"""
Idea Generation Service

Orchestrates the idea generation pipeline with step-based tracing.
"""

import logging
import os
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Optional, List, Dict, Any

from app.modules.idea.contracts import (
    IdeaSession,
    IdeaSessionStatus,
    IdeaSessionConfig,
    IdeaCandidate,
    LiteratureItem,
    WorkflowTrace,
    StepResult,
    DraftPlan,
    RiskItem,
    ExperimentSpec,
    # Dual-Graph models
    BFTSConfig,
    BFTSHandoff,
    QueryFamily,
    QueryPlan,
    RawPaper,
    LiteratureGraph,
    LiteratureMap,
    StructuredPaper,
    PaperNode,
    # Phase 2 models
    ReasoningKG,
    GraphEvidenceLink,
    ReasoningPathSeed,
    # Step 6 models
    CandidateGraphEvidence,
    IdeaCritique,
    PriorWorkComparison,
    RankedIdeaOutput,
)
from app.modules.idea.storage import (
    get_session_storage,
    get_literature_storage,
    get_candidate_storage,
    generate_session_id,
    generate_literature_id,
    generate_candidate_id,
    # Dual-Graph storage
    get_raw_paper_storage,
    get_literature_graph_storage,
    get_structured_paper_storage,
    get_literature_map_storage,
    get_handoff_storage,
    get_structured_paper_cache_storage,
    get_llm_task_cache_storage,
    generate_raw_paper_id,
    generate_graph_id,
    generate_map_id,
    generate_handoff_id,
    # Phase 2 storage
    get_reasoning_kg_storage,
    get_evidence_link_storage,
    get_path_seed_storage,
    generate_reasoning_kg_id,
    generate_evidence_link_id,
    generate_path_seed_id,
    # Step 6 storage
    get_ranked_output_storage,
    generate_ranked_output_id,
)
from app.models.idea import _compute_title_hash
from app.modules.idea.literature_graph import LiteratureGraphBuilder
from app.modules.idea.deep_reading import DeepReader
from app.modules.idea.reasoning_kg import ReasoningKGBuilder
from app.modules.idea.graph_linker import GraphLinker
from app.modules.idea.path_seed import PathSeedGenerator
from app.modules.idea.evidence_relevance import (
    EvidenceTier,
    assess_search_result,
    better_evidence_tier,
    build_topic_intent_profile,
    deduplicate_search_results,
    evidence_tier_allows_dimension,
    raw_paper_identity_keys,
    role_requirements_for_paper_type,
    search_result_identity_keys,
    semantically_eligible_roles,
)
from app.llm.provider_client import get_provider_client, ChatMessage, ProviderError
from app.llm.task_scheduler import get_llm_task_scheduler
from app.services.search_service import get_search_service, SearchResult, tokenize_topic_text
from app.services.ranking_service import get_ranking_service
from app.services import prompts
import json
import re

logger = logging.getLogger(__name__)

STRUCTURED_PAPER_CACHE_SCHEMA_VERSION = "structured-paper-v2"
STRUCTURED_PAPER_CACHE_PROMPT_VERSION = "deep-reader-cutin-v1"
IDEA_REVIEWER_CACHE_PROMPT_VERSION = "idea-reviewer-v1"


class StepContextError(ValueError):
    """Step failure that carries trace inputs/outputs for UI diagnostics."""

    def __init__(
        self,
        message: str,
        *,
        inputs: Optional[Dict[str, Any]] = None,
        outputs: Optional[Dict[str, Any]] = None,
        artifacts: Optional[List[str]] = None,
    ):
        super().__init__(message)
        self.step_inputs = inputs or {}
        self.step_outputs = outputs or {}
        self.step_artifacts = artifacts or []


class RecoverableIdeaError(StepContextError):
    """Pipeline stop that preserves work and can be resumed later."""

    waiting_status: IdeaSessionStatus
    resume_from: str


class AwaitingEvidenceError(RecoverableIdeaError):
    waiting_status = IdeaSessionStatus.AWAITING_EVIDENCE
    resume_from = "evidenceGate"


class AwaitingLiteratureEvidenceError(RecoverableIdeaError):
    waiting_status = IdeaSessionStatus.AWAITING_EVIDENCE
    resume_from = "literatureSearch"


class AwaitingTranslationError(RecoverableIdeaError):
    waiting_status = IdeaSessionStatus.AWAITING_EVIDENCE
    resume_from = "expandQuery"


class AwaitingIdeasError(RecoverableIdeaError):
    waiting_status = IdeaSessionStatus.AWAITING_IDEAS
    resume_from = "ideaBrainstorm"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    raw = (text or "").strip()
    if "```json" in raw:
        raw = raw.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in raw:
        parts = raw.split("```")
        if len(parts) >= 3:
            raw = parts[1]
    raw = raw.strip()

    def _loads(candidate: str) -> Optional[Dict[str, Any]]:
        normalized = (
            candidate.strip()
            .removeprefix("json")
            .strip()
            .replace("\u201c", '"')
            .replace("\u201d", '"')
            .replace("\u2018", "'")
            .replace("\u2019", "'")
        )
        normalized = re.sub(r",\s*([}\]])", r"\1", normalized)
        try:
            data = json.loads(normalized)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None

    parsed = _loads(raw)
    if parsed:
        return parsed

    start = raw.find("{")
    if start >= 0:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(raw)):
            char = raw[index]
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    parsed = _loads(raw[start:index + 1])
                    if parsed:
                        return parsed
                    break

    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        return _loads(match.group())
    return None


def _clean_query_terms(values: Any, seed: str, *, limit: int = 5) -> List[str]:
    raw_values = values if isinstance(values, list) else []
    cleaned: List[str] = []
    for value in [seed, *raw_values]:
        if not isinstance(value, str):
            continue
        query = value.strip().strip(",").strip().strip('"').strip("'").strip()
        if not query:
            continue
        if any(marker in query for marker in ["{", "}", "[", "]", '":']):
            continue
        if not re.search(r"[\w\u4e00-\u9fff]", query):
            continue
        if query not in cleaned:
            cleaned.append(query)
        if len(cleaned) >= limit:
            break
    return cleaned or [seed]


def _topic_relevance_score(result: SearchResult, topic_terms: List[str], signal_terms: List[str]) -> float:
    text = f"{result.title} {result.abstract}".lower()
    if not text.strip():
        return 0.0

    topic_hits = sum(1 for term in topic_terms if term in text)
    signal_hits = sum(1 for term in signal_terms if term in text)
    # Only apply RAG phrase bonus when the topic itself is about RAG/faithfulness,
    # so non-RAG domains are not unfairly penalised.  signal_terms is derived from
    # the topic text upstream, so a non-empty list means the topic mentions RAG.
    phrase_bonus = 0.0
    if signal_terms:
        for phrase in [
            "citation faithfulness",
            "citation faithful",
            "uncertainty estimation",
            "retrieval augmented generation",
            "retrieval-augmented generation",
        ]:
            if phrase in text:
                phrase_bonus += 0.2

    source_bonus = 0.1 if result.source in {"arxiv", "semantic_scholar"} else 0.0
    base = min(0.5, topic_hits * 0.04)
    signal = min(0.6, signal_hits * 0.12)
    return min(1.0, base + signal + phrase_bonus + source_bonus + result.relevance_score * 0.2)


def _rank_results_for_topic(
    results: List[SearchResult],
    *,
    seed: str,
    domain: str,
    search_queries: List[str],
) -> List[SearchResult]:
    topic_text = " ".join([seed, domain, *search_queries]).lower()
    tokens = tokenize_topic_text(topic_text)
    stopwords = {
        "and", "the", "for", "with", "from", "that", "this", "into",
        "using", "based", "how", "can", "are", "what", "when", "where",
        "does", "retrieval", "augmented", "generation",
    }
    topic_terms = []
    for token in tokens:
        if token not in stopwords and token not in topic_terms:
            topic_terms.append(token)
    signal_terms = [
        term for term in [
            "citation", "faithfulness", "faithful", "uncertainty", "gating",
            "confidence", "hallucination", "attribution", "provenance",
            "trustworthy", "factuality", "grounding",
        ]
        if term in topic_text
    ]

    scored = [
        (_topic_relevance_score(result, topic_terms, signal_terms), index, result)
        for index, result in enumerate(results)
    ]
    for score, _, result in scored:
        result.relevance_score = max(result.relevance_score, score)

    scored.sort(key=lambda item: (item[0], item[2].source != "local", -item[1]), reverse=True)
    return [result for _, _, result in scored]


def _filter_results_for_topic(results: List[SearchResult]) -> tuple[List[SearchResult], int]:
    """Drop low-relevance results before they become RawPaper evidence."""
    external_threshold = float(os.getenv("FAROS_MIN_EXTERNAL_RELEVANCE", "0.12"))
    local_threshold = float(os.getenv("FAROS_MIN_LOCAL_RELEVANCE", "0.28"))
    filtered: List[SearchResult] = []
    dropped = 0
    for result in results:
        threshold = local_threshold if result.source == "local" else external_threshold
        if result.relevance_score >= threshold:
            filtered.append(result)
        else:
            dropped += 1
    return filtered, dropped


def _repair_result_priority(result: SearchResult, paper_type: str) -> tuple:
    text = f"{result.title} {result.abstract}".lower()
    survey_like = any(
        marker in text
        for marker in [
            " survey",
            "a survey",
            "comprehensive survey",
            "systematic review",
            "literature review",
            "overview of",
        ]
    )
    method_markers = [
        "we propose",
        "propose",
        "method",
        "framework",
        "model",
        "algorithm",
        "evaluation",
        "evaluate",
        "detector",
        "verification",
        "abstention",
        "refusal",
        "attribution",
        "provenance",
        "benchmarking",
    ]
    method_hits = sum(1 for marker in method_markers if marker in text)
    survey_penalty = 1 if paper_type == "algorithm" and survey_like else 0
    return (-survey_penalty, method_hits, float(result.relevance_score or 0.0))


def _topic_terms_from_seed(seed: Any, domain: Any = "", extra_terms: Optional[List[Any]] = None) -> List[str]:
    topic_text = " ".join([
        str(seed or ""),
        str(domain or ""),
        *(str(term) for term in (extra_terms or []) if term is not None),
    ]).lower().replace("-", " ")
    stopwords = {
        "about", "against", "also", "among", "and", "are", "based", "between",
        "can", "could", "does", "for", "from", "how", "into", "large", "language",
        "learning", "method", "methods", "model", "models", "paper", "research",
        "should", "study", "than", "that", "the", "their", "this", "through",
        "using", "what", "when", "where", "with", "within", "would",
        "是否", "如何", "研究", "方法", "模型", "系统",
    }
    terms: List[str] = []
    for token in tokenize_topic_text(topic_text):
        if token in stopwords:
            continue
        if token not in terms:
            terms.append(token)
    return terms[:32]


def _seed_mentions_rag_safety(seed: str) -> bool:
    text = (seed or "").lower().replace("-", " ")
    return "rag" in text and any(
        marker in text
        for marker in [
            "citation",
            "faithful",
            "faithfulness",
            "attribution",
            "provenance",
            "traceability",
            "abstention",
            "refusal",
            "引用",
            "忠实",
            "拒答",
            "可追踪",
            "证据",
        ]
    )


def _paper_text_for_quality(paper: Any) -> str:
    parts = [
        getattr(paper, "title", ""),
        getattr(paper, "abstract", ""),
        getattr(paper, "summary", ""),
        " ".join(getattr(paper, "limitations", []) or []),
        " ".join(getattr(paper, "openQuestions", []) or []),
        " ".join(getattr(paper, "failedAssumptions", []) or []),
        " ".join(getattr(paper, "methodWeaknesses", []) or []),
        " ".join(getattr(paper, "missingEvaluation", []) or []),
        " ".join(getattr(paper, "baselineMethods", []) or []),
        " ".join(getattr(paper, "recommendedMetrics", []) or []),
        " ".join(getattr(paper, "datasets", []) or []),
        " ".join(getattr(paper, "metrics", []) or []),
    ]
    claims = getattr(paper, "claims", []) or []
    parts.extend(str(getattr(claim, "text", "")) for claim in claims[:8])
    methods = getattr(paper, "methods", []) or []
    parts.extend(str(getattr(method, "name", "")) for method in methods[:6])
    parts.extend(str(getattr(method, "description", "")) for method in methods[:6])
    return " ".join(part for part in parts if part).lower().replace("-", " ")


def _paper_alignment_score(paper: Any, topic_terms: List[str]) -> float:
    text = _paper_text_for_quality(paper)
    if not text or not topic_terms:
        return 0.0
    hits = sum(1 for term in topic_terms if term and term.lower() in text)
    phrase_bonus = 0.0
    for phrase in [
        "citation faithfulness",
        "citation faithful",
        "retrieval augmented generation",
        "retrieval-augmented generation",
        "uncertainty estimation",
        "evidence traceability",
        "attribution",
        "hallucination",
    ]:
        if phrase in text:
            phrase_bonus += 0.12
    relevance_score = float(
        getattr(paper, "relevanceScore", None)
        or getattr(paper, "relevance_score", 0.0)
        or 0.0
    )
    return min(1.0, hits / max(4, min(10, len(topic_terms))) + phrase_bonus + relevance_score * 0.25)


def _paper_sources(paper: Any) -> List[str]:
    source = getattr(paper, "source", [])
    if isinstance(source, str):
        return [source]
    if isinstance(source, list):
        return [str(item) for item in source if str(item)]
    return []


def _evaluate_paper_quality_gate(
    *,
    seed: str,
    domain: str,
    papers: List[Any],
    stage: str,
    extra_terms: Optional[List[str]] = None,
    paper_roles: Optional[Dict[str, List[str]]] = None,
    paper_type: str = "algorithm",
) -> Dict[str, Any]:
    """Lightweight paper relevance gate used before idea generation."""

    topic_terms = _topic_terms_from_seed(seed, domain, extra_terms)
    total = len(papers)
    scored: List[Dict[str, Any]] = []
    for paper in papers:
        paper_id = getattr(paper, "rawPaperId", getattr(paper, "id", ""))
        raw_roles = list(dict.fromkeys(
            (paper_roles or {}).get(paper_id, [])
            or getattr(paper, "retrievalRoles", None)
            or getattr(paper, "retrieval_roles", [])
            or []
        ))
        tier = str(
            getattr(paper, "evidenceTier", None)
            or getattr(paper, "evidence_tier", "unclassified")
        )
        eligible_roles = list(semantically_eligible_roles(tier, raw_roles))
        score = 0.0 if tier == EvidenceTier.REJECTED.value else _paper_alignment_score(
            paper,
            topic_terms,
        )
        scored.append({
            "paperId": paper_id,
            "title": getattr(paper, "title", ""),
            "score": round(score, 3),
            "sources": _paper_sources(paper),
            "roles": eligible_roles,
            "retrievalRoles": raw_roles,
            "evidenceTier": tier,
        })
    scored.sort(key=lambda item: item["score"], reverse=True)
    aligned = [item for item in scored if item["score"] >= 0.32]
    external = [
        item for item in scored
        if any(source and source != "local" for source in item["sources"])
    ]
    sources_used = sorted({
        source
        for item in scored
        for source in item["sources"]
        if source
    })
    local_count = sum(
        1 for item in scored if item["sources"] and all(source == "local" for source in item["sources"])
    )
    local_only = bool(total and local_count == total)
    if local_only:
        source_quality = "local_only"
        provider_fallback_risk = "high"
    elif local_count and external:
        source_quality = "mixed_external_local"
        provider_fallback_risk = "medium"
    elif external:
        source_quality = "external"
        provider_fallback_risk = "low"
    else:
        source_quality = "empty"
        provider_fallback_risk = "high"
    avg_top_score = (
        sum(item["score"] for item in scored[: min(5, len(scored))]) / max(1, min(5, len(scored)))
        if scored else 0.0
    )

    min_papers = int(os.getenv("FAROS_PAPER_GATE_MIN_PAPERS", "4"))
    min_aligned = int(os.getenv("FAROS_PAPER_GATE_MIN_ALIGNED", "3"))
    role_names = ("domain", "task", "method", "evaluation")
    role_counts = {
        role: sum(
            1
            for item in scored
            if role in item["roles"] and item["score"] >= 0.12
        )
        for role in role_names
    }
    role_aware = any(item["retrievalRoles"] for item in scored)
    role_requirements = role_requirements_for_paper_type(paper_type)
    role_issues: List[str] = []
    if role_aware:
        if role_counts["domain"] + role_counts["task"] < role_requirements["domainOrTask"]:
            role_issues.append("insufficient domain/task evidence")
        if role_requirements["method"] and role_counts["method"] < role_requirements["method"]:
            role_issues.append("insufficient method evidence")
        if role_requirements["evaluation"] and role_counts["evaluation"] < role_requirements["evaluation"]:
            role_issues.append("insufficient evaluation evidence")
    role_coverage_passed = role_aware and not role_issues
    errors: List[str] = []
    warnings: List[str] = []
    if total < min_papers:
        errors.append(f"{stage}: paper pool is too small ({total} < {min_papers})")
    if len(aligned) < min_aligned:
        errors.append(f"{stage}: too few papers are semantically aligned with the seed query ({len(aligned)} < {min_aligned})")
    if scored and avg_top_score < 0.30:
        errors.append(f"{stage}: top papers have weak topic alignment (avg={avg_top_score:.2f})")
    if role_aware and role_issues:
        errors.append(f"{stage}: role-aware evidence coverage failed: {', '.join(role_issues)}")
    if total and not external:
        warnings.append(f"{stage}: all retrieved papers are from local fallback sources")
    if total and len(aligned) < max(2, total // 4):
        warnings.append(f"{stage}: most papers have weak overlap with the seed topic")

    return {
        "stage": stage,
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "paperCount": total,
        "alignedPaperCount": len(aligned),
        "externalPaperCount": len(external),
        "localPaperCount": local_count,
        "localOnly": local_only,
        "sourcesUsed": sources_used,
        "sourceQuality": source_quality,
        "providerFallbackRisk": provider_fallback_risk,
        "avgTopAlignment": round(avg_top_score, 3),
        "roleCoverage": {
            "enabled": role_aware,
            "passed": role_coverage_passed,
            "counts": role_counts,
            "requirements": role_requirements,
            "issues": role_issues,
        },
        "topicTerms": topic_terms[:12],
        "topPapers": scored[:8],
    }


def _paper_type_coverage_requirements(paper_type: str) -> List[str]:
    normalized = (paper_type or "algorithm").lower()
    if normalized in {"benchmark", "evaluation", "reproducibility"}:
        return ["dataset", "evaluation", "limitation"]
    if normalized in {"system", "application", "safety"}:
        return ["method", "evaluation", "limitation"]
    if normalized in {"survey", "position", "theory"}:
        return ["claim", "limitation", "gap"]
    return ["method", "evaluation", "limitation"]


def _paper_type_coverage(papers: List[Any], literature_map: Optional[LiteratureMap]) -> Dict[str, bool]:
    has_method = False
    has_dataset = False
    has_metric = False
    has_baseline = False
    has_claim = False
    has_limitation = False
    has_gap = bool(getattr(literature_map, "gaps", []) or [])
    has_evaluation_text = False

    gap_evidence_types = {
        "contradiction",
        "missing_evaluation",
        "sparse_combination",
        "underexplored_dataset",
        "weak_baseline",
    }
    metric_markers = [
        "metric",
        "measure",
        "score",
        "accuracy",
        "precision",
        "recall",
        "f1",
        "auc",
        "auroc",
        "exact match",
        "rouge",
        "bleu",
        "ndcg",
        "latency",
        "throughput",
        "faithfulness",
        "factuality",
        "calibration",
        "refusal accuracy",
        "traceability",
        "attribution",
    ]
    evaluation_markers = [
        "evaluation",
        "evaluate",
        "evaluating",
        "benchmark",
        "experiment",
        "ablation",
        "comparison",
        "comparative",
        "human evaluation",
        "user study",
    ]
    dataset_markers = ["dataset", "corpus", "benchmark", "test set", "validation set"]
    baseline_markers = ["baseline", "outperform", "compare", "comparison", "state of the art", "sota"]

    for paper in papers:
        claims = getattr(paper, "claims", []) or []
        novelty_evidence = getattr(paper, "noveltyEvidence", []) or []
        paper_text = _paper_text_for_quality(paper)
        has_method = has_method or bool(getattr(paper, "methods", []) or [])
        has_dataset = has_dataset or bool(getattr(paper, "datasets", []) or []) or any(
            marker in paper_text for marker in dataset_markers
        )
        has_metric = has_metric or bool(getattr(paper, "metrics", []) or []) or any(
            marker in paper_text for marker in metric_markers
        )
        has_baseline = (
            has_baseline
            or bool(getattr(paper, "baselines", []) or [])
            or bool(getattr(paper, "baselineMethods", []) or [])
            or any(
            marker in paper_text for marker in baseline_markers
            )
        )
        has_evaluation_text = has_evaluation_text or any(
            marker in paper_text for marker in evaluation_markers
        )
        has_claim = has_claim or bool(claims)
        has_limitation = has_limitation or bool(getattr(paper, "limitations", []) or [])
        has_limitation = has_limitation or any(
            getattr(paper, field, []) or []
            for field in ["openQuestions", "failedAssumptions", "methodWeaknesses", "missingEvaluation"]
        )
        has_metric = has_metric or bool(getattr(paper, "recommendedMetrics", []) or [])
        has_evaluation_text = has_evaluation_text or bool(getattr(paper, "missingEvaluation", []) or [])
        if any(getattr(paper, field, []) or [] for field in ["openQuestions", "failedAssumptions", "methodWeaknesses", "missingEvaluation"]):
            has_gap = True

        for claim in claims:
            claim_type = str(getattr(claim, "claimType", "") or "").lower()
            claim_text = str(getattr(claim, "text", "") or "").lower()
            if claim_type in {"limitation", "assumption"} or any(
                marker in claim_text
                for marker in ["limitation", "limited", "fails", "gap", "missing", "lack"]
            ):
                has_limitation = True
            if claim_type == "gap" or "gap" in claim_text:
                has_gap = True

        for evidence in novelty_evidence:
            evidence_type = str(getattr(evidence, "evidenceType", "") or "").lower()
            description = str(getattr(evidence, "description", "") or "").lower()
            if evidence_type in gap_evidence_types or any(
                marker in description
                for marker in ["gap", "missing", "lack", "underexplored", "weak baseline"]
            ):
                has_gap = True

    return {
        "method": has_method,
        "dataset": has_dataset,
        "metric": has_metric,
        "baseline": has_baseline,
        "claim": has_claim,
        "limitation": has_limitation,
        "gap": has_gap,
        "evaluation": has_dataset or has_metric or has_baseline or has_evaluation_text,
    }


def _count_gap_signals(
    papers: List[Any],
    literature_map: Optional[LiteratureMap],
    gap_outputs: Optional[Dict[str, Any]],
) -> int:
    count = len(getattr(literature_map, "gaps", []) or [])
    gap_outputs = gap_outputs or {}
    for key in ["gaps", "gapAnalysis", "prioritizedGaps", "researchOpportunities"]:
        values = gap_outputs.get(key, [])
        if isinstance(values, list):
            count += len([item for item in values if str(item).strip()])
    for paper in papers:
        count += len(getattr(paper, "limitations", []) or [])
        for field in ["openQuestions", "failedAssumptions", "methodWeaknesses", "missingEvaluation"]:
            count += len(getattr(paper, field, []) or [])
        for evidence in getattr(paper, "noveltyEvidence", []) or []:
            evidence_type = str(getattr(evidence, "evidenceType", "") or "").lower()
            if evidence_type in {
                "contradiction",
                "missing_evaluation",
                "sparse_combination",
                "underexplored_dataset",
                "weak_baseline",
            }:
                count += 1
    return count


def _evaluate_evidence_gate_v2(
    *,
    seed: str,
    domain: str,
    paper_type: str,
    structured_papers: List[StructuredPaper],
    literature_map: Optional[LiteratureMap],
    gap_outputs: Optional[Dict[str, Any]],
    stage: str,
    extra_terms: Optional[List[str]] = None,
    paper_roles: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Any]:
    """Hard pre-brainstorm evidence gate.

    This gate runs after deep reading/gap analysis and before idea generation.
    It prevents low-quality or local-only evidence pools from seeding ideas.
    """

    base_gate = _evaluate_paper_quality_gate(
        seed=seed,
        domain=domain,
        papers=structured_papers,
        stage=stage,
        extra_terms=extra_terms,
        paper_roles=paper_roles,
        paper_type=paper_type,
    )
    coverage = _paper_type_coverage(structured_papers, literature_map)
    required_coverage = _paper_type_coverage_requirements(paper_type)
    missing_coverage = [key for key in required_coverage if not coverage.get(key)]
    gap_signal_count = _count_gap_signals(structured_papers, literature_map, gap_outputs)

    min_external = int(os.getenv("FAROS_EVIDENCE_GATE_MIN_EXTERNAL", "2"))
    min_gap_signals = int(os.getenv("FAROS_EVIDENCE_GATE_MIN_GAP_SIGNALS", "1"))
    min_aligned = int(os.getenv("FAROS_EVIDENCE_GATE_MIN_ALIGNED", "3"))

    errors = list(base_gate.get("errors", []))
    warnings = list(base_gate.get("warnings", []))
    external_count = int(base_gate.get("externalPaperCount", 0) or 0)
    aligned_count = int(base_gate.get("alignedPaperCount", 0) or 0)

    if external_count < min_external:
        errors.append(
            f"{stage}: insufficient external evidence papers ({external_count} < {min_external})"
        )
    role_coverage_passed = bool(
        (base_gate.get("roleCoverage") or {}).get("passed", False)
    )
    if aligned_count < min_aligned and not role_coverage_passed:
        errors.append(
            f"{stage}: insufficient topic-aligned structured papers ({aligned_count} < {min_aligned})"
        )
    if gap_signal_count < min_gap_signals:
        errors.append(
            f"{stage}: no explicit gap or limitation signal found in structured evidence"
        )
    if missing_coverage:
        errors.append(
            f"{stage}: missing paper-type evidence coverage for {paper_type}: {', '.join(missing_coverage)}"
        )
    if coverage.get("gap") and not coverage.get("limitation"):
        warnings.append(f"{stage}: gap signals exist but paper limitation extraction is weak")

    passed = not errors
    return {
        **base_gate,
        "stage": stage,
        "passed": passed,
        "hardBlocked": not passed,
        "errors": errors,
        "warnings": warnings,
        "gateVersion": "evidence_gate_v2",
        "paperType": paper_type,
        "requiredCoverage": required_coverage,
        "missingCoverage": missing_coverage,
        "coverage": coverage,
        "gapSignalCount": gap_signal_count,
        "minExternalPaperCount": min_external,
        "minGapSignalCount": min_gap_signals,
    }


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _deep_read_max_papers() -> int:
    try:
        configured = int(os.getenv("FAROS_IDEA_DEEP_READ_MAX_PAPERS", "24"))
    except ValueError:
        configured = 24
    return max(4, min(40, configured))


def _limit_deep_read_selection(
    selected_paper_ids: List[str],
    raw_by_id: Dict[str, RawPaper],
    *,
    limit: int,
) -> List[str]:
    direct: List[str] = []
    transferable: List[str] = []
    must_cite: List[str] = []
    for paper_id in dict.fromkeys(selected_paper_ids):
        paper = raw_by_id.get(paper_id)
        if not paper:
            continue
        if paper.mustCiteOverride:
            must_cite.append(paper_id)
        elif paper.evidenceTier == EvidenceTier.TRANSFERABLE.value:
            transferable.append(paper_id)
        elif paper.evidenceTier != EvidenceTier.REJECTED.value:
            direct.append(paper_id)

    transfer_cap = max(0, limit // 3)
    regular = [
        *direct[: max(0, limit - transfer_cap)],
        *transferable[:transfer_cap],
    ]
    return list(dict.fromkeys([*regular, *must_cite]))


def _tag_repair_results(results: List[SearchResult], query: str) -> None:
    lowered_query = query.lower()
    roles = ["repair"]
    for role in ("domain", "task", "method", "evaluation"):
        if role in lowered_query:
            roles.append(role)
    for result in results:
        for role in roles:
            if role not in result.retrieval_roles:
                result.retrieval_roles.append(role)
        if query not in result.matched_queries:
            result.matched_queries.append(query)


def _record_step_result(trace: WorkflowTrace, result: StepResult) -> None:
    trace.steps.append(result)
    trace.totalSteps = len(trace.steps)
    trace.successfulSteps = sum(step.status == "ok" for step in trace.steps)
    trace.failedSteps = sum(step.status == "failed" for step in trace.steps)


def _as_string_list(value: Any, *, limit: int = 8) -> List[str]:
    if isinstance(value, str):
        if not value.strip():
            return []
        raw_values = re.split(r"[\n,;，；]+|\s+(?=raw_|lit_|cl_|ke_|rps_|gp_|lpr_)", value)
    elif isinstance(value, list):
        raw_values = value
    else:
        return []
    items: List[str] = []
    for item in raw_values:
        text = str(item).strip()
        if text and text not in items:
            items.append(text)
        if len(items) >= limit:
            break
    return items


def _score_0_1(value: Any, default: float = 0.0) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = default
    if score > 1.0:
        score = score / 10.0
    return max(0.0, min(1.0, score))


def _coverage_dimension_key(value: Any, fallback: str) -> str:
    key = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "").strip().lower()).strip("_")
    return key or fallback


def _verify_coverage_dimension_support(
    *,
    dimension: str,
    supporting_paper_ids: List[str],
    paper_tiers: Dict[str, str],
) -> List[str]:
    return [
        paper_id
        for paper_id in dict.fromkeys(supporting_paper_ids)
        if paper_id in paper_tiers
        and evidence_tier_allows_dimension(paper_tiers[paper_id], dimension)
    ]


def _normalize_coverage_report(
    data: Optional[Dict[str, Any]],
    *,
    available_paper_ids: set[str],
    paper_tiers: Optional[Dict[str, str]] = None,
    source: str = "llm",
) -> Dict[str, Any]:
    """Normalize LLM-first coverage output and enforce citation integrity."""

    data = data or {}
    raw_dimensions = (
        data.get("dimensions")
        or data.get("coverageDimensions")
        or data.get("dimensionCoverage")
        or []
    )
    dimensions: List[Dict[str, Any]] = []
    warnings = _as_string_list(data.get("warnings", []), limit=8)
    repair_queries = _as_string_list(data.get("repairQueries", []), limit=8)
    effective_tiers = {
        paper_id: (paper_tiers or {}).get(paper_id, "unclassified")
        for paper_id in available_paper_ids
    }

    for index, raw in enumerate(raw_dimensions if isinstance(raw_dimensions, list) else []):
        if not isinstance(raw, dict):
            continue
        key = _coverage_dimension_key(raw.get("key", raw.get("name")), f"dimension_{index + 1}")
        required = bool(raw.get("required", True))
        score = _score_0_1(raw.get("score", raw.get("coverageScore", 0.0)))
        raw_paper_ids = _as_string_list(
            raw.get("supportingPaperIds", raw.get("paperIds", [])),
            limit=12,
        )
        known_paper_ids = [
            paper_id for paper_id in raw_paper_ids
            if paper_id in available_paper_ids
        ]
        verified_paper_ids = _verify_coverage_dimension_support(
            dimension=key,
            supporting_paper_ids=known_paper_ids,
            paper_tiers=effective_tiers,
        )
        dropped_ids = [
            paper_id for paper_id in raw_paper_ids
            if paper_id not in available_paper_ids
        ]
        if dropped_ids:
            warnings.append(
                f"{key}: ignored unknown paper IDs: {', '.join(dropped_ids[:4])}"
            )
        disallowed_ids = [
            paper_id for paper_id in known_paper_ids
            if paper_id not in verified_paper_ids
        ]
        if disallowed_ids:
            warnings.append(
                f"{key}: ignored paper IDs whose evidence tier cannot support "
                f"this dimension: {', '.join(disallowed_ids[:4])}"
            )
        status = str(raw.get("status", "") or "").strip().lower()
        if required and not verified_paper_ids:
            status = "missing"
        elif not status:
            if score >= 0.75 and verified_paper_ids:
                status = "strong"
            elif score >= 0.45 and verified_paper_ids:
                status = "partial"
            elif verified_paper_ids:
                status = "weak"
            else:
                status = "missing"
        dimension_queries = _as_string_list(raw.get("repairQueries", []), limit=4)
        repair_queries.extend(query for query in dimension_queries if query not in repair_queries)
        dimensions.append({
            "key": key,
            "label": str(raw.get("label", raw.get("name", key.replace("_", " ").title())) or ""),
            "required": required,
            "status": status,
            "score": round(score, 3),
            "supportingPaperIds": verified_paper_ids,
            "supportedClaims": _as_string_list(raw.get("supportedClaims", raw.get("claims", [])), limit=5),
            "remainingGap": str(raw.get("remainingGap", raw.get("gap", "")) or ""),
            "whyRequired": str(raw.get("whyRequired", "") or ""),
            "repairQueries": dimension_queries,
        })

    missing_required = [
        item["key"] for item in dimensions
        if item.get("required") and not item.get("supportingPaperIds")
    ]
    blocking_issues = _as_string_list(data.get("blockingIssues", data.get("issues", [])), limit=8)
    if not dimensions:
        blocking_issues.append("Coverage report has no evidence dimensions.")
    for key in missing_required:
        issue = f"Required evidence dimension '{key}' has no verified supporting paper IDs."
        if issue not in blocking_issues:
            blocking_issues.append(issue)

    if dimensions:
        average_dimension_score = sum(item["score"] for item in dimensions) / len(dimensions)
    else:
        average_dimension_score = 0.0
    overall_score = _score_0_1(
        data.get("overallEvidenceScore", data.get("score", average_dimension_score)),
        average_dimension_score,
    )
    min_score = float(os.getenv("FAROS_EVIDENCE_COVERAGE_MIN_SCORE", "0.62"))
    raw_passed = data.get("passed")
    passed = bool(raw_passed) if isinstance(raw_passed, bool) else overall_score >= min_score
    passed = passed and not missing_required and not blocking_issues

    return {
        "version": "evidence_coverage_v2_1",
        "source": source,
        "passed": passed,
        "hardBlocked": not passed,
        "overallEvidenceScore": round(overall_score, 3),
        "minScore": min_score,
        "dimensions": dimensions,
        "missingRequiredDimensions": missing_required,
        "blockingIssues": blocking_issues,
        "warnings": warnings,
        "repairQueries": repair_queries[:8],
        "scientistJudgment": str(data.get("scientistJudgment", data.get("recommendation", "")) or ""),
    }


def _default_coverage_dimensions(seed: str, paper_type: str) -> List[Dict[str, Any]]:
    if _seed_mentions_rag_safety(seed):
        return [
            {
                "key": "citation_faithfulness",
                "label": "Citation faithfulness",
                "required": True,
                "whyRequired": "The seed query asks to improve citation fidelity.",
                "keywords": ["citation", "faithfulness", "faithful", "attribution", "support"],
                "repairQueries": ["RAG citation faithfulness verification attribution"],
            },
            {
                "key": "refusal_abstention",
                "label": "Refusal / abstention",
                "required": True,
                "whyRequired": "The seed query asks for refusal capability in high-risk QA.",
                "keywords": ["refusal", "refuse", "abstention", "abstain", "insufficient evidence"],
                "repairQueries": ["RAG abstention refusal insufficient evidence high-risk QA"],
            },
            {
                "key": "evidence_traceability",
                "label": "Evidence traceability",
                "required": True,
                "whyRequired": "The seed query asks for traceable evidence chains.",
                "keywords": ["traceability", "traceable", "provenance", "evidence", "grounding"],
                "repairQueries": ["RAG evidence traceability provenance grounding"],
            },
            {
                "key": "high_risk_qa",
                "label": "High-risk QA context",
                "required": False,
                "whyRequired": "Domain evidence helps keep the idea aligned with high-risk use cases.",
                "keywords": ["high-risk", "high risk", "safety", "medical", "legal", "finance", "question answering", "qa"],
                "repairQueries": ["high-risk question answering RAG safety evaluation"],
            },
            {
                "key": "evaluation_protocol",
                "label": "Evaluation protocol",
                "required": True,
                "whyRequired": "Downstream planning needs measurable validation criteria.",
                "keywords": ["evaluation", "benchmark", "metric", "dataset", "measure", "score"],
                "repairQueries": ["RAG citation refusal traceability benchmark metric evaluation"],
            },
        ]

    required = ["method", "evaluation", "limitation"]
    if paper_type in {"survey", "position", "theory"}:
        required = ["claim", "gap", "limitation"]
    return [
        {
            "key": item,
            "label": item.replace("_", " ").title(),
            "required": True,
            "whyRequired": f"Required evidence coverage for {paper_type} paper type.",
            "keywords": [item, "evaluation" if item == "method" else item],
            "repairQueries": [f"{seed} {item} evidence"],
        }
        for item in required
    ]


def _rule_seed_coverage_report(
    *,
    seed: str,
    paper_type: str,
    structured_papers: List[StructuredPaper],
    literature_map: Optional[LiteratureMap],
    gap_outputs: Dict[str, Any],
    paper_tiers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Rule fallback for LLM-first evidence coverage."""

    dimensions = []
    for dimension in _default_coverage_dimensions(seed, paper_type):
        keywords = [str(item).lower() for item in dimension.get("keywords", [])]
        matched_paper_ids: List[str] = []
        supported_claims: List[str] = []
        for paper in structured_papers:
            text = _paper_text_for_quality(paper)
            if any(keyword and keyword in text for keyword in keywords):
                matched_paper_ids.append(paper.id)
                if getattr(paper, "summary", ""):
                    supported_claims.append(str(paper.summary)[:220])
                elif getattr(paper, "abstract", ""):
                    supported_claims.append(str(paper.abstract)[:220])
            if len(matched_paper_ids) >= 4:
                break

        score = min(1.0, 0.25 + 0.25 * len(matched_paper_ids)) if matched_paper_ids else 0.0
        dimensions.append({
            "key": dimension["key"],
            "label": dimension["label"],
            "required": bool(dimension.get("required", True)),
            "score": score,
            "supportingPaperIds": matched_paper_ids,
            "supportedClaims": supported_claims[:3],
            "remainingGap": "",
            "whyRequired": dimension.get("whyRequired", ""),
            "repairQueries": dimension.get("repairQueries", []),
        })

    report = _normalize_coverage_report(
        {
            "passed": True,
            "dimensions": dimensions,
            "scientistJudgment": (
                "Rule fallback coverage was used because LLM coverage analysis was unavailable. "
                "Treat this as a conservative evidence sanity check, not a full scientific review."
            ),
        },
        available_paper_ids={paper.id for paper in structured_papers},
        paper_tiers=paper_tiers,
        source="rule_fallback",
    )
    if gap_outputs:
        report["gapContextAvailable"] = True
    if literature_map and getattr(literature_map, "gaps", None):
        report["literatureGapCount"] = len(literature_map.gaps)
    return report


def _merge_coverage_report_with_gate(
    gate: Dict[str, Any],
    coverage_report: Dict[str, Any],
) -> Dict[str, Any]:
    merged = dict(gate)
    errors = list(merged.get("errors", []))
    warnings = list(merged.get("warnings", []))
    merged["coverageReport"] = coverage_report

    coverage_warnings = _as_string_list(coverage_report.get("warnings", []), limit=8)
    warnings.extend(item for item in coverage_warnings if item not in warnings)

    if not coverage_report.get("passed", False):
        missing = _as_string_list(coverage_report.get("missingRequiredDimensions", []), limit=6)
        blocking = _as_string_list(coverage_report.get("blockingIssues", []), limit=3)
        if missing:
            errors.append(
                "Evidence Coverage 2.1 failed: missing required dimensions "
                + ", ".join(missing)
            )
        elif blocking:
            errors.append("Evidence Coverage 2.1 failed: " + "; ".join(blocking))
        else:
            errors.append("Evidence Coverage 2.1 failed")

    merged["errors"] = errors
    merged["warnings"] = warnings
    merged["passed"] = bool(merged.get("passed", False)) and not errors
    merged["hardBlocked"] = not merged["passed"]
    return merged


def _coverage_repair_queries(quality_gate: Dict[str, Any], *, limit: int = 5) -> List[str]:
    coverage = quality_gate.get("coverageReport") if isinstance(quality_gate, dict) else {}
    if not isinstance(coverage, dict):
        return []
    queries: List[str] = []
    for query in _as_string_list(coverage.get("repairQueries", []), limit=limit):
        if query not in queries:
            queries.append(query)
    dimensions = coverage.get("dimensions", [])
    if isinstance(dimensions, list):
        for dimension in dimensions:
            if not isinstance(dimension, dict):
                continue
            if dimension.get("status") not in {"missing", "weak", "partial"}:
                continue
            for query in _as_string_list(dimension.get("repairQueries", []), limit=3):
                if query not in queries:
                    queries.append(query)
                if len(queries) >= limit:
                    return queries[:limit]
    return queries[:limit]


def _ensure_gap_outputs(
    *,
    gap_analysis: Any,
    prioritized_gaps: Any,
    opportunities: Any,
    novelty_gaps: Any,
    literature_map: Optional[LiteratureMap],
    seed_query: str,
) -> tuple[List[Any], List[str], List[str]]:
    """Ensure Step 4 exposes non-empty gap fields when upstream evidence exists."""

    clean_gap_analysis = gap_analysis if isinstance(gap_analysis, list) else []
    clean_prioritized = _as_string_list(prioritized_gaps, limit=5)
    clean_opportunities = _as_string_list(opportunities, limit=5)
    novelty_gap_items = _as_string_list(novelty_gaps, limit=5)

    focus_terms = _topic_terms_from_seed(
        seed_query,
        "",
        novelty_gap_items,
    )
    all_map_gaps = list(getattr(literature_map, "gaps", []) or [])
    map_gaps = sorted(
        all_map_gaps,
        key=lambda gap: _gap_relevance_score(gap, focus_terms),
        reverse=True,
    )[:5]
    if not clean_gap_analysis and map_gaps:
        clean_gap_analysis = [
            {
                "gap": str(getattr(gap, "direction", "") or "").strip(),
                "evidence": str(getattr(gap, "evidence", "") or "").strip(),
                "paperIds": list(getattr(gap, "paperIds", []) or []),
                "confidence": float(getattr(gap, "confidence", 0.5) or 0.5),
            }
            for gap in map_gaps
            if str(getattr(gap, "direction", "") or "").strip()
        ]

    if not clean_prioritized:
        clean_prioritized = novelty_gap_items or [
            str(getattr(gap, "direction", "") or "").strip()
            for gap in map_gaps
            if str(getattr(gap, "direction", "") or "").strip()
        ][:5]

    if not clean_opportunities and clean_prioritized:
        clean_opportunities = [
            f"Develop a method for {seed_query} that directly addresses: {gap}"
            for gap in clean_prioritized[:3]
        ]

    return clean_gap_analysis[:5], clean_prioritized[:5], clean_opportunities[:5]


def _gap_relevance_score(gap: Any, focus_terms: List[str]) -> float:
    text = " ".join([
        str(getattr(gap, "direction", "") or ""),
        str(getattr(gap, "evidence", "") or ""),
    ]).lower().replace("-", " ")
    if not text:
        return 0.0
    score = sum(1.0 for term in focus_terms if term and term.lower() in text)
    for phrase in [
        "citation faithfulness",
        "citation correctness",
        "answer attribution",
        "evidence traceability",
        "provenance",
        "refusal",
        "abstention",
        "hallucination",
    ]:
        if phrase in text:
            score += 2.0
    return score


def _normalize_evidence_llm_review(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    data = data or {}
    raw_score = data.get("score", data.get("overallScore", 1.0))
    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        score = 1.0
    if score > 1.0:
        score = score / 10.0
    score = max(0.0, min(1.0, score))

    blocking_issues = _as_string_list(
        data.get("blockingIssues", data.get("issues", [])),
        limit=8,
    )
    warnings = _as_string_list(data.get("warnings", []), limit=8)
    repair_queries = _as_string_list(data.get("repairQueries", []), limit=5)
    passed_value = data.get("passed", data.get("pass", None))
    if isinstance(passed_value, bool):
        passed = passed_value
    else:
        passed = score >= float(os.getenv("FAROS_EVIDENCE_GATE_LLM_MIN_SCORE", "0.62")) and not blocking_issues

    return {
        "status": str(data.get("status", "completed")),
        "passed": passed,
        "score": round(score, 3),
        "confidence": max(0.0, min(1.0, float(data.get("confidence", 0.5) or 0.5))),
        "themeAligned": bool(data.get("themeAligned", passed)),
        "evidenceFaithful": bool(data.get("evidenceFaithful", passed)),
        "gapGrounded": bool(data.get("gapGrounded", passed)),
        "blockingIssues": blocking_issues,
        "warnings": warnings,
        "repairQueries": repair_queries,
        "recommendation": str(data.get("recommendation", "") or ""),
    }


def _merge_evidence_gate_with_llm_review(
    gate: Dict[str, Any],
    review: Dict[str, Any],
) -> Dict[str, Any]:
    merged = dict(gate)
    errors = list(merged.get("errors", []))
    warnings = list(merged.get("warnings", []))
    status = review.get("status", "completed")
    merged["llmReviewer"] = review

    if status == "skipped":
        merged["reviewMode"] = "rule"
        merged["passed"] = bool(merged.get("passed", False))
        merged["hardBlocked"] = not merged["passed"]
        return merged

    if status == "error":
        merged["reviewMode"] = "rule+llm_error"
        error = review.get("error") or "LLM Evidence Reviewer failed"
        errors.append(str(error))
        warnings.extend(review.get("warnings", []))
        merged["errors"] = errors
        merged["warnings"] = warnings
        merged["passed"] = False
        merged["hardBlocked"] = not merged["passed"]
        return merged

    merged["reviewMode"] = "rule+llm"
    min_score = float(os.getenv("FAROS_EVIDENCE_GATE_LLM_MIN_SCORE", "0.62"))
    if not review.get("passed", False) or float(review.get("score", 0.0) or 0.0) < min_score:
        issues = "; ".join(review.get("blockingIssues", [])[:3])
        errors.append(
            "LLM Evidence Reviewer failed"
            + (f": {issues}" if issues else f": score below threshold ({review.get('score')})")
        )
    warnings.extend(review.get("warnings", []))
    merged["errors"] = errors
    merged["warnings"] = warnings
    merged["passed"] = bool(merged.get("passed", False)) and not errors
    merged["hardBlocked"] = not merged["passed"]
    return merged


def _candidate_similarity_key(candidate: IdeaCandidate) -> set[str]:
    text = " ".join([
        candidate.title,
        candidate.problem,
        candidate.keyInsight,
        candidate.proposedMethod,
        candidate.hypothesisStatement,
    ]).lower().replace("-", " ")
    stopwords = {
        "and", "are", "for", "from", "that", "the", "this", "with", "using",
        "method", "model", "paper", "research", "study", "approach", "system",
    }
    tokens = {
        token
        for token in tokenize_topic_text(text)
        if token not in stopwords
    }
    alias_groups = [
        (
            ("引用忠实", "忠实性", "citation fidelity", "citation faithfulness", "faithful citation", "citation faithful"),
            ("citation", "fidelity", "faithfulness", "faithful"),
        ),
        (
            ("拒答", "拒绝回答", "abstention", "abstain", "refusal", "refuse"),
            ("refusal", "refuse", "abstention", "abstain"),
        ),
        (
            ("证据可追踪", "可追踪性", "traceable evidence", "evidence traceability", "provenance"),
            ("evidence", "traceability", "traceable", "provenance"),
        ),
        (
            ("高风险", "高危", "high risk", "high stakes", "safety critical", "critical"),
            ("high", "risk", "stakes", "safety", "critical"),
        ),
        (
            ("问答", "question answering", "qa"),
            ("qa", "question", "answering"),
        ),
        (
            ("检索增强", "retrieval augmented generation", "rag"),
            ("rag", "retrieval", "augmented", "generation"),
        ),
    ]
    for triggers, aliases in alias_groups:
        if any(trigger in text for trigger in triggers):
            tokens.update(aliases)
    return tokens


def _normalized_topic_text(*parts: Any) -> str:
    return " ".join(str(part or "") for part in parts if part is not None).lower().replace("-", " ")


_APPLICATION_DOMAIN_DRIFT_GROUPS = [
    {
        "label": "carbon market / climate finance",
        "phrases": [
            "carbon market",
            "carbon trading",
            "carbon credit",
            "carbon markets",
            "emissions trading",
            "climate finance",
        ],
        "terms": {"carbon", "emission", "emissions", "trading", "finance", "financial", "market", "markets"},
    },
    {
        "label": "financial market analysis",
        "phrases": [
            "financial market",
            "stock market",
            "market analysis",
            "investment analysis",
            "trading strategy",
        ],
        "terms": {"financial", "finance", "stock", "investment", "trading", "market", "markets"},
    },
    {
        "label": "clinical or medical deployment",
        "phrases": [
            "clinical",
            "medical",
            "healthcare",
            "biomedical",
            "radiology",
            "electronic health record",
            "ehr",
        ],
        "terms": {"clinical", "medical", "healthcare", "biomedical", "radiology", "patient", "ehr"},
    },
    {
        "label": "legal domain",
        "phrases": ["legal", "law firm", "court", "contract review", "legal reasoning"],
        "terms": {"legal", "law", "court", "contract", "judicial"},
    },
]


_APPLICATION_PHRASE_SUFFIXES = {
    "analysis",
    "analytics",
    "optimization",
    "optimisation",
    "report",
    "reports",
    "reporting",
    "deployment",
    "forecasting",
    "routing",
    "trading",
    "diagnosis",
    "simulation",
    "recommendation",
    "tutoring",
    "screening",
    "monitoring",
}


_APPLICATION_PHRASE_STOPWORDS = {
    "and", "are", "based", "for", "from", "high", "into", "main", "multi",
    "real", "reliable", "research", "self", "system", "systems", "that",
    "the", "this", "use", "using", "with", "world",
}


def _candidate_direction_tag_text(candidate: IdeaCandidate) -> str:
    if not getattr(candidate, "draftPlan", None):
        return ""
    return " ".join(str(tag or "") for tag in (candidate.draftPlan.tags or []))


def _topic_phrase_tokens(text: str) -> List[str]:
    return [
        token
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9]{2,}", text.lower().replace("-", " "))
        if token not in _APPLICATION_PHRASE_STOPWORDS
    ]


def _candidate_unrequested_application_phrase_issues(
    seed_query: str,
    candidate: IdeaCandidate,
    english_search_queries: Optional[List[str]] = None,
) -> List[str]:
    seed_text = seed_query
    if english_search_queries and re.search(r'[\u4e00-\u9fff]', seed_query or ""):
        seed_text = " ".join([seed_query, *english_search_queries])
    seed_tokens = set(_topic_phrase_tokens(seed_text))
    if not seed_tokens:
        return []
    phrase_candidates: List[str] = []
    for source_text in [
        _normalized_topic_text(candidate.title),
        _normalized_topic_text(candidate.problem),
        _normalized_topic_text(candidate.keyInsight),
    ]:
        for match in re.finditer(
            r"\b(?:for|in|on|within|across)\s+([a-z][a-z0-9\s]{4,80}?)(?:[.;,:]|\bwith\b|\busing\b|\buse\b|\buses\b|\bneeds\b|\bshould\b|\bcan\b|$)",
            source_text,
        ):
            phrase = " ".join(match.group(1).split())
            if phrase:
                phrase_candidates.append(phrase)

    issues: List[str] = []
    for phrase in phrase_candidates[:8]:
        tokens = _topic_phrase_tokens(phrase)
        if len(tokens) < 2:
            continue
        novel_tokens = [token for token in tokens if token not in seed_tokens]
        if len(novel_tokens) < 2:
            continue
        has_application_shape = (
            any(token in _APPLICATION_PHRASE_SUFFIXES for token in tokens)
            or tokens[-1] in _APPLICATION_PHRASE_SUFFIXES
        )
        if not has_application_shape:
            continue
        novelty_ratio = len(novel_tokens) / max(1, len(tokens))
        if novelty_ratio >= 0.6:
            issues.append(
                "Candidate shows topic drift: unrequested application phrase "
                f"'{phrase}' is central but absent from the seed query."
            )
    return issues


def _candidate_topic_drift_issues(
    seed_query: str,
    candidate: IdeaCandidate,
    english_search_queries: Optional[List[str]] = None,
) -> List[str]:
    """Detect strong unrequested application-domain anchors in an idea.

    Coarse token overlap catches fully unrelated candidates, but it misses
    cases where an idea keeps the seed's generic words while making a new
    application domain the primary object (for example, turning "multi-agent
    research automation" into "carbon market analysis"). This guard only
    fires for strong domain phrases absent from the seed.
    """

    seed_text = _normalized_topic_text(seed_query)
    # Bug 9 fix: When the seed is CJK, English domain phrases (e.g. "clinical",
    # "medical") won't appear in the Chinese text, causing false positives.
    # Augment seed_text with English search queries from expandQuery so that
    # domains implied by the translated queries are not flagged as drift.
    if english_search_queries and re.search(r'[\u4e00-\u9fff]', seed_query or ""):
        seed_text = seed_text + " " + _normalized_topic_text(*english_search_queries)
    if not seed_text:
        return []
    candidate_text = _normalized_topic_text(
        candidate.title,
        candidate.problem,
        candidate.keyInsight,
        candidate.proposedMethod,
        candidate.hypothesisStatement,
        _candidate_direction_tag_text(candidate),
    )
    if not candidate_text:
        return []

    issues: List[str] = []
    for group in _APPLICATION_DOMAIN_DRIFT_GROUPS:
        label = str(group["label"])
        phrases = [str(phrase) for phrase in group["phrases"]]
        terms = {str(term) for term in group["terms"]}
        seed_has_group = any(phrase in seed_text for phrase in phrases) or any(
            re.search(rf"\b{re.escape(term)}\b", seed_text)
            for term in terms
        )
        if seed_has_group:
            continue
        matched_phrases = [phrase for phrase in phrases if phrase in candidate_text]
        matched_terms = [
            term
            for term in terms
            if re.search(rf"\b{re.escape(term)}\b", candidate_text)
        ]
        if matched_phrases or len(matched_terms) >= 2:
            marker = matched_phrases[0] if matched_phrases else ", ".join(matched_terms[:3])
            issues.append(
                f"Candidate shows topic drift: unrequested {label} anchor '{marker}' is central but absent from the seed query."
            )
    issues.extend(_candidate_unrequested_application_phrase_issues(
        seed_query,
        candidate,
        english_search_queries=english_search_queries,
    ))
    return list(dict.fromkeys(issues))


def _candidate_jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def _coerce_text_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    return [str(value).strip()] if str(value).strip() else []


def _coerce_dict_list(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _coerce_strict_text_list(value: Any) -> List[str]:
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _is_inline_evidence_format_issue(issue: str) -> bool:
    """Return whether an evidence issue is only asking for inline raw IDs."""

    normalized = str(issue or "").strip().lower()
    if not normalized:
        return False
    text_mentions = (
        "candidate text" in normalized
        or "candidate's text" in normalized
        or "prose" in normalized
        or "inline" in normalized
    )
    citation_mentions = (
        "citation" in normalized
        or "citations" in normalized
        or "evidencerefs" in normalized
        or "evidence refs" in normalized
        or "evidence references" in normalized
        or "raw id" in normalized
        or "paper id" in normalized
        or "references" in normalized
    )
    weak_binding_mentions = (
        "lack of concrete evidencerefs" in normalized
        or "lack of specific evidence references" in normalized
        or "no explicit citations" in normalized
        or "does not cite any specific references" in normalized
    )
    return (text_mentions and citation_mentions) or weak_binding_mentions


def _idea_reviewer_concurrency() -> int:
    try:
        configured = int(os.getenv("FAROS_IDEA_REVIEWER_CONCURRENCY", "3"))
    except ValueError:
        configured = 3
    return max(1, min(len(IDEA_REVIEWER_SPECS), configured))


IDEA_REVIEWER_SPECS: List[Dict[str, str]] = [
    {
        "name": "IdeaEvidenceReviewer",
        "focus": "Judge whether the idea is faithfully grounded in cited papers, KG entities, path seeds, and explicit evidence IDs.",
        "rubric": "High score requires concrete evidenceRefs and no invented citations.",
    },
    {
        "name": "IdeaNoveltyReviewer",
        "focus": "Judge whether the idea has a concrete difference from closest prior work and addresses a real gap.",
        "rubric": "High score requires a precise novelty claim, not a generic combination of known methods.",
    },
    {
        "name": "IdeaFeasibilityReviewer",
        "focus": "Judge whether the method can plausibly be implemented and evaluated by downstream modules.",
        "rubric": "High score requires implementable modules, clear inputs/outputs, and manageable risks.",
    },
    {
        "name": "IdeaSpecificityReviewer",
        "focus": "Judge whether the hypothesis, method, variables, metrics, and expected validation are specific enough.",
        "rubric": "High score requires measurable expected outcomes and concrete evaluation handles.",
    },
    {
        "name": "IdeaImpactReviewer",
        "focus": "Judge whether the idea would matter scientifically if validated and whether contributions are publishable.",
        "rubric": "High score requires clear research value, meaningful scope, and credible downstream contribution claims.",
    },
]


def _score_0_10(value: Any, default: float = 0.0) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = default
    if 0.0 <= score <= 1.0:
        score *= 10.0
    return round(max(0.0, min(10.0, score)), 2)


def _confidence_0_1(value: Any, default: float = 0.5) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = default
    if score > 1.0:
        score /= 10.0
    return round(max(0.0, min(1.0, score)), 3)


def _bool_from_review(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "pass", "passed", "1"}
    return default


class IdeaGenerationService:
    """Service for managing idea generation sessions."""

    def __init__(self):
        self.session_storage = get_session_storage()
        self.literature_storage = get_literature_storage()
        self.candidate_storage = get_candidate_storage()
        # Dual-Graph builders
        self.graph_builder = LiteratureGraphBuilder()
        self.deep_reader = DeepReader()
        # Dual-Graph storage
        self.raw_paper_storage = get_raw_paper_storage()
        self.graph_storage = get_literature_graph_storage()
        self.structured_storage = get_structured_paper_storage()
        self.structured_cache_storage = get_structured_paper_cache_storage()
        self.llm_task_cache_storage = get_llm_task_cache_storage()
        self.llm_task_scheduler = get_llm_task_scheduler()
        self.map_storage = get_literature_map_storage()
        self.handoff_storage = get_handoff_storage()
        # Phase 2 builders
        self.reasoning_builder = ReasoningKGBuilder()
        self.graph_linker = GraphLinker()
        self.path_seed_gen = PathSeedGenerator()
        # Phase 2 storage
        self.reasoning_kg_storage = get_reasoning_kg_storage()
        self.evidence_link_storage = get_evidence_link_storage()
        self.path_seed_storage = get_path_seed_storage()
        # Step 6 storage
        self.ranked_output_storage = get_ranked_output_storage()
        self._pipeline_lock_guard = threading.Lock()
        self._pipeline_locks: Dict[str, threading.Lock] = {}

    def _get_step_output(self, session: IdeaSession, step_name: str, key: str, default=None):
        """Read a specific output key from a pipeline step's trace."""
        if not session.trace:
            return default
        for step in session.trace.steps:
            if step.name == step_name:
                return step.outputs.get(key, default)
        return default

    def _retarget_structured_paper(
        self,
        structured_paper: StructuredPaper,
        *,
        session: IdeaSession,
        raw_paper: RawPaper,
    ) -> StructuredPaper:
        """Reuse global paper understanding while binding IDs to this session."""

        return structured_paper.model_copy(update={
            "id": raw_paper.id,
            "sessionId": session.id,
            "rawPaperId": raw_paper.id,
            "title": raw_paper.title or structured_paper.title,
            "abstract": raw_paper.abstract or structured_paper.abstract,
            "authors": raw_paper.authors or structured_paper.authors,
            "year": raw_paper.year or structured_paper.year,
            "venue": raw_paper.venue or structured_paper.venue,
            "citationCount": raw_paper.citationCount or structured_paper.citationCount,
            "source": list(raw_paper.source or structured_paper.source),
            "claims": [
                claim.model_copy(update={"paperId": raw_paper.id})
                for claim in structured_paper.claims
            ],
            "findings": [
                finding.model_copy(update={"paperId": raw_paper.id})
                for finding in structured_paper.findings
            ],
            "methods": [
                method.model_copy(update={"paperId": raw_paper.id})
                for method in structured_paper.methods
            ],
            "contradictions": [
                contradiction.model_copy(update={"paperId": raw_paper.id})
                for contradiction in structured_paper.contradictions
            ],
            "noveltyEvidence": [
                evidence.model_copy(update={"paperId": raw_paper.id})
                for evidence in structured_paper.noveltyEvidence
            ],
            "extractionMethod": (
                structured_paper.extractionMethod
                if structured_paper.extractionMethod.endswith("+cache")
                else f"{structured_paper.extractionMethod}+cache"
            ),
        })

    def _load_trusted_structured_paper_cache(
        self,
        *,
        session: IdeaSession,
        raw_paper: RawPaper,
        schema_version: str = STRUCTURED_PAPER_CACHE_SCHEMA_VERSION,
        prompt_version: str = STRUCTURED_PAPER_CACHE_PROMPT_VERSION,
    ) -> Optional[StructuredPaper]:
        """Load a version-matched, non-suspect global StructuredPaper cache hit."""

        cache_storage = getattr(self, "structured_cache_storage", None)
        if not cache_storage:
            return None
        entry = cache_storage.get_valid(
            raw_paper=raw_paper,
            schema_version=schema_version,
            prompt_version=prompt_version,
            model=session.config.model or "",
        )
        if not entry:
            return None
        try:
            structured = StructuredPaper(**entry.get("structuredPaper", {}))
        except Exception as exc:
            logger.warning("Structured paper cache entry is unreadable for %s: %s", raw_paper.id, exc)
            cache_storage.mark_status(
                entry.get("cacheKey", ""),
                "invalidated",
                reason="unreadable_structured_paper_payload",
            )
            return None
        return self._retarget_structured_paper(
            structured,
            session=session,
            raw_paper=raw_paper,
        )

    def _store_trusted_structured_paper_cache(
        self,
        *,
        session: IdeaSession,
        raw_paper: RawPaper,
        structured_paper: StructuredPaper,
    ) -> Optional[str]:
        cache_storage = getattr(self, "structured_cache_storage", None)
        if not cache_storage:
            return None
        try:
            return cache_storage.put(
                raw_paper=raw_paper,
                structured_paper=structured_paper,
                schema_version=STRUCTURED_PAPER_CACHE_SCHEMA_VERSION,
                prompt_version=STRUCTURED_PAPER_CACHE_PROMPT_VERSION,
                model=session.config.model or "",
            )
        except Exception as exc:
            logger.warning("Failed to store structured paper cache for %s: %s", raw_paper.id, exc)
            return None

    def _mark_structured_cache_suspect_from_reviewer_reports(
        self,
        *,
        evidence: Optional[CandidateGraphEvidence],
        reviewer_reports: List[Dict[str, Any]],
    ) -> List[str]:
        """Let explicit evidence-review failures invalidate cached paper understanding."""

        cache_storage = getattr(self, "structured_cache_storage", None)
        raw_storage = getattr(self, "raw_paper_storage", None)
        if not cache_storage or not raw_storage or not evidence:
            return []

        suspect_terms = [
            "does not support",
            "not support",
            "unsupported",
            "invalid evidence",
            "unrelated evidence",
            "citation missing",
            "missing citation",
            "claim not supported",
            "evidence grounding",
            "证据不支持",
            "引用不成立",
            "证据不相关",
        ]
        suspect_refs: set[str] = set()
        for report in reviewer_reports:
            if report.get("passed", False):
                continue
            reviewer = str(report.get("reviewer", ""))
            if reviewer and reviewer != "IdeaEvidenceReviewer":
                continue
            text = " ".join(
                str(item)
                for key in ["blockingIssues", "warnings", "repairInstructions", "summary"]
                for item in (
                    report.get(key, [])
                    if isinstance(report.get(key), list)
                    else [report.get(key, "")]
                )
            ).lower()
            if not any(term in text for term in suspect_terms):
                continue
            suspect_refs.update(str(ref) for ref in report.get("evidenceRefs", []) if str(ref).strip())

        supporting_paper_ids = set(str(item) for item in evidence.supportingPaperIds)
        target_paper_ids = sorted((suspect_refs & supporting_paper_ids) or supporting_paper_ids)
        marked: List[str] = []
        for paper_id in target_paper_ids:
            try:
                raw_paper = raw_storage.get(paper_id)
            except Exception as exc:
                logger.warning("Could not load raw paper %s for cache suspect marking: %s", paper_id, exc)
                continue
            if not raw_paper:
                continue
            try:
                cache_key = cache_storage.cache_key_for_raw_paper(raw_paper)
                cache_storage.mark_status(
                    cache_key,
                    "suspect",
                    reason="idea_evidence_reviewer_reported_unsupported_claim",
                )
                marked.append(cache_key)
            except Exception as exc:
                logger.warning("Could not mark structured cache suspect for %s: %s", paper_id, exc)
        return marked

    def _build_literature_repair_queries(
        self,
        session: IdeaSession,
        quality_gate: Dict[str, Any],
        *,
        existing_queries: List[str],
        limit: int = 4,
    ) -> List[str]:
        """Build targeted search queries when the paper quality gate fails."""

        seed = session.config.seedQuery.strip()
        domain = (session.config.domain or "").strip()
        topic_terms = [
            str(term).strip()
            for term in quality_gate.get("topicTerms", [])
            if str(term).strip()
        ]
        query_plan = self._get_step_output(session, "expandQuery", "queryPlan", {}) or {}
        expanded_terms = [
            str(term).strip()
            for term in query_plan.get("expandedTerms", [])
            if str(term).strip()
        ]
        key_concepts = [
            str(term).strip()
            for term in query_plan.get("keyConcepts", [])
            if str(term).strip()
        ]
        anchors = topic_terms[:4] or expanded_terms[:4] or [seed]
        focus_terms = [
            "evaluation",
            "limitations",
            "dataset",
            "method",
        ]
        targeted_queries: List[str] = []
        if _seed_mentions_rag_safety(seed):
            targeted_queries = [
                "RAG citation faithfulness abstention evidence traceability high-risk question answering",
                "retrieval augmented generation citation attribution refusal abstention provenance QA",
                "RAG answer verification citation support evidence provenance hallucination detection",
                "high-risk question answering RAG refusal mechanism citation verification",
                "retrieval augmented generation evidence traceability attribution evaluation benchmark",
            ]
            focus_terms = [
                "citation faithfulness",
                "attribution evaluation",
                "abstention refusal",
                "hallucination detection",
                "evidence provenance",
            ]
        elif session.config.paperType in {"survey", "position", "theory"}:
            focus_terms = [
                "survey",
                "benchmark",
                "evaluation",
                "limitations",
                "dataset",
                "method",
            ]

        candidates: List[str] = []
        candidates.extend(_coverage_repair_queries(quality_gate, limit=5))
        candidates.extend(targeted_queries)
        candidates.append(seed)
        if domain:
            candidates.append(f"{seed} {domain}")
        for focus in focus_terms:
            candidates.append(f"{seed} {focus}")
        if key_concepts:
            candidates.append(" ".join([seed, *key_concepts[:3]]))
        if anchors:
            candidates.append(" ".join(anchors[:5]))
        candidates.extend(expanded_terms[:3])

        normalized_existing = {query.lower().strip() for query in existing_queries}
        queries: List[str] = []
        for query in candidates:
            query = re.sub(r"\s+", " ", query).strip()
            if not query or query.lower() in normalized_existing or query in queries:
                continue
            queries.append(query)
            if len(queries) >= limit:
                break
        return self._anchor_repair_queries(session, queries or [seed])

    def _anchor_repair_queries(
        self,
        session: IdeaSession,
        queries: List[str],
    ) -> List[str]:
        """Keep CJK coverage repair queries anchored to the translated topic."""
        seed = session.config.seedQuery.strip()
        if not re.search(r"[\u4e00-\u9fff]", seed):
            return list(dict.fromkeys(query for query in queries if query.strip()))

        role_queries = self._get_step_output(
            session,
            "expandQuery",
            "searchQueriesByRole",
            {},
        ) or {}
        english_queries = self._get_step_output(
            session,
            "expandQuery",
            "englishSearchQueries",
            [],
        ) or []
        anchors = [
            str(query).strip()
            for role in ("domain", "task")
            for query in (role_queries.get(role, []) if isinstance(role_queries, dict) else [])
            if str(query).strip()
        ]
        anchors.extend(str(query).strip() for query in english_queries if str(query).strip())
        anchor = next(iter(dict.fromkeys(anchors)), "")
        if not anchor:
            return list(dict.fromkeys(query for query in queries if query.strip()))

        anchored: List[str] = []
        for raw_query in queries:
            query = re.sub(r"\s+", " ", str(raw_query or "")).strip()
            if not query:
                continue
            suffix = re.sub(re.escape(seed), " ", query, flags=re.IGNORECASE)
            suffix = re.sub(r"\s+", " ", suffix).strip()
            if anchor.lower() in query.lower():
                combined = query
            else:
                combined = " ".join(part for part in [anchor, suffix] if part).strip()
            if combined and combined not in anchored:
                anchored.append(combined)
        return anchored

    def _core_search_queries(self, session: IdeaSession) -> List[str]:
        """Return domain/task queries used as immutable topic anchors."""
        role_queries = self._get_step_output(
            session,
            "expandQuery",
            "searchQueriesByRole",
            {},
        ) or {}
        core: List[str] = []
        if isinstance(role_queries, dict):
            for role in ("domain", "task"):
                for query in _as_string_list(role_queries.get(role, []), limit=3):
                    if query not in core:
                        core.append(query)
        if core:
            return core
        english = _as_string_list(
            self._get_step_output(session, "expandQuery", "englishSearchQueries", []),
            limit=4,
        )
        if english:
            return english
        expanded = _as_string_list(
            self._get_step_output(session, "expandQuery", "expandedTerms", []),
            limit=4,
        )
        return expanded or [session.config.seedQuery]

    @staticmethod
    def _cjk_query_roles(data: Dict[str, Any]) -> Dict[str, List[str]]:
        roles: Dict[str, List[str]] = {
            "domain": [],
            "task": [],
            "method": [],
            "evaluation": [],
        }
        nested = data.get("englishQueryRoles", {})
        if not isinstance(nested, dict):
            nested = {}
        field_names = {
            "domain": "domainQueries",
            "task": "taskQueries",
            "method": "methodQueries",
            "evaluation": "evaluationQueries",
        }
        for role, field_name in field_names.items():
            values = nested.get(role, data.get(field_name, []))
            roles[role] = _as_string_list(values, limit=2)
        return roles

    def _translate_cjk_query_roles(
        self,
        session: IdeaSession,
        client: Any,
    ) -> tuple[Dict[str, List[str]], int]:
        messages = [
            ChatMessage(
                role="system",
                content=(
                    "Translate a Chinese research topic into role-specific English academic "
                    "queries. Preserve named works, entities, domain, and research intent."
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    f"Chinese topic: {session.config.seedQuery}\n"
                    f"Paper type: {session.config.paperType}\n\n"
                    "Return JSON with domainQueries, taskQueries, methodQueries, and "
                    "evaluationQueries. Each field must contain one or two English queries."
                ),
            ),
        ]
        response = client.chat(messages, model=session.config.model, max_tokens=350)
        return self._cjk_query_roles(_extract_json_object(response.text) or {}), int(
            getattr(response, "latency_ms", 0) or 0
        )

    def _load_evidence_gate_inputs(self, session: IdeaSession) -> tuple[List[StructuredPaper], Optional[LiteratureMap], Dict[str, Any]]:
        structured_papers = self.structured_storage.list_by_session(session.id)
        literature_map = self.map_storage.get_by_session(session.id)
        gap_outputs = {}
        if session.trace:
            for step in session.trace.steps:
                if step.name == "gapAnalysis":
                    gap_outputs = dict(step.outputs or {})
                    break
        return structured_papers, literature_map, gap_outputs

    def _paper_roles_for_session(self, session_id: str) -> Dict[str, List[str]]:
        roles: Dict[str, List[str]] = {}
        for paper in self.raw_paper_storage.list_by_session(session_id):
            paper_roles = list(getattr(paper, "retrievalRoles", []) or [])
            if paper_roles:
                roles[paper.id] = paper_roles
        return roles

    def _evaluate_evidence_gate_for_session(
        self,
        session: IdeaSession,
        *,
        stage: str,
    ) -> tuple[Dict[str, Any], List[StructuredPaper], Optional[LiteratureMap], Dict[str, Any]]:
        seed = session.config.seedQuery
        domain = session.config.domain or ""
        paper_type = session.config.paperType
        extra_terms = self._core_search_queries(session)
        structured_papers, literature_map, gap_outputs = self._load_evidence_gate_inputs(session)
        paper_roles = self._paper_roles_for_session(session.id)
        rule_gate = _evaluate_evidence_gate_v2(
            seed=seed,
            domain=domain,
            paper_type=paper_type,
            structured_papers=structured_papers,
            literature_map=literature_map,
            gap_outputs=gap_outputs,
            stage=stage,
            extra_terms=extra_terms,
            paper_roles=paper_roles,
        )
        coverage_report = self._run_evidence_coverage_llm_reviewer(
            session=session,
            rule_gate=rule_gate,
            structured_papers=structured_papers,
            literature_map=literature_map,
            gap_outputs=gap_outputs,
        )
        rule_gate = _merge_coverage_report_with_gate(rule_gate, coverage_report)
        if not rule_gate.get("passed", False):
            rule_gate["reviewMode"] = "rule"
            return rule_gate, structured_papers, literature_map, gap_outputs

        llm_review = self._run_evidence_llm_reviewer(
            session=session,
            rule_gate=rule_gate,
            structured_papers=structured_papers,
            literature_map=literature_map,
            gap_outputs=gap_outputs,
        )
        return (
            _merge_evidence_gate_with_llm_review(rule_gate, llm_review),
            structured_papers,
            literature_map,
            gap_outputs,
        )

    def _step_evidence_gate(self, session: IdeaSession) -> tuple:
        """Evidence Gate 2.0: hard gate before idea generation.

        If the structured evidence pool is weak, repair the literature pool and
        rebuild downstream artifacts once before allowing idea generation.
        """

        seed = session.config.seedQuery
        paper_type = session.config.paperType
        evidence_gate, structured_papers, _, _ = self._evaluate_evidence_gate_for_session(
            session,
            stage="ideaBrainstorm.preflight",
        )

        repair_attempted = False
        repair_report: Dict[str, Any] = {}
        final_gate = evidence_gate

        if not evidence_gate.get("passed", False):
            repair_attempted = True
            repair_report = self._repair_evidence_pool_before_idea_brainstorm(session, evidence_gate)
            final_gate, structured_papers, _, _ = self._evaluate_evidence_gate_for_session(
                session,
                stage="ideaBrainstorm.preflight.repaired",
            )

        inputs = {
            "stage": "ideaBrainstorm.preflight",
            "topic": seed,
            "paperType": paper_type,
            "structuredPaperCount": len(structured_papers),
        }
        outputs = {
            "evidenceGate": final_gate,
            "initialEvidenceGate": evidence_gate,
            "repairAttempted": repair_attempted,
            "repairReport": repair_report,
            "allowedToBrainstorm": bool(final_gate.get("passed", False)),
        }

        if not final_gate.get("passed", False):
            errors = "; ".join(final_gate.get("errors", [])[:4])
            raise AwaitingEvidenceError(
                f"Evidence Gate 2.0 failed before idea generation: {errors}",
                inputs=inputs,
                outputs=outputs,
            )

        return inputs, outputs, []

    def _repair_evidence_pool_before_idea_brainstorm(
        self,
        session: IdeaSession,
        evidence_gate: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Repair weak evidence before any idea candidate is generated."""

        search_service = get_search_service()
        rule_queries = self._build_literature_repair_queries(
            session,
            evidence_gate,
            existing_queries=self._get_step_output(
                session,
                "literatureSearch",
                "searchQueries",
                [session.config.seedQuery],
            ),
            limit=5,
        )
        reviewer_queries = _as_string_list(
            (evidence_gate.get("llmReviewer") or {}).get("repairQueries", []),
            limit=5,
        )
        queries: List[str] = []
        for query in [*rule_queries, *reviewer_queries]:
            query = re.sub(r"\s+", " ", query).strip()
            if query and query not in queries:
                queries.append(query)
            if len(queries) >= 5:
                break
        results: List[SearchResult] = []
        for query in queries:
            try:
                batch = search_service.search(
                    query,
                    limit=max(8, min(24, session.config.maxPapers // max(1, len(queries)))),
                )
                _tag_repair_results(batch, query)
                results.extend(batch)
            except Exception as exc:
                logger.warning("Pre-idea evidence repair search failed for '%s': %s", query, exc)

        persist_report = self._persist_repair_search_results(
            session,
            results,
            search_queries=queries,
        )
        novelty_outputs: Dict[str, Any] = {}
        gap_outputs: Dict[str, Any] = {}
        rebuild_error = None
        try:
            _, novelty_outputs, _ = self._step_novelty_check(
                session,
                forced_raw_paper_ids=persist_report.get("createdRawPaperIds", []),
            )
            _, gap_outputs, _ = self._step_gap_analysis(session)
        except Exception as exc:
            rebuild_error = str(exc)
            logger.warning("Pre-idea evidence repair could not rebuild artifacts: %s", exc, exc_info=True)

        return {
            "attempted": True,
            "queries": queries,
            "initialEvidenceGate": evidence_gate,
            "persistReport": persist_report,
            "noveltyOutputs": {
                "selectedPaperIds": novelty_outputs.get("selectedPaperIds", []),
                "forcedRepairPaperIds": novelty_outputs.get("forcedRepairPaperIds", []),
                "forcedRepairPaperCount": novelty_outputs.get("forcedRepairPaperCount", 0),
                "structuredPaperCount": novelty_outputs.get("structuredPaperCount", 0),
                "structuredCacheHitCount": novelty_outputs.get("structuredCacheHitCount", 0),
                "deepReadRequestedCount": novelty_outputs.get("deepReadRequestedCount", 0),
                "literatureMapId": novelty_outputs.get("literatureMapId"),
            },
            "gapOutputs": {
                "reasoningKgId": gap_outputs.get("reasoningKgId"),
                "pathSeedCount": gap_outputs.get("pathSeedCount", 0),
            },
            "error": rebuild_error,
        }

    def _run_evidence_coverage_llm_reviewer(
        self,
        *,
        session: IdeaSession,
        rule_gate: Dict[str, Any],
        structured_papers: List[StructuredPaper],
        literature_map: Optional[LiteratureMap],
        gap_outputs: Dict[str, Any],
    ) -> Dict[str, Any]:
        """LLM-first coverage planner/mapper with rule verification fallback."""

        paper_tiers = {
            paper.id: paper.evidenceTier
            for paper in self.raw_paper_storage.list_by_session(session.id)
        }
        rule_fallback = _rule_seed_coverage_report(
            seed=session.config.seedQuery,
            paper_type=session.config.paperType,
            structured_papers=structured_papers,
            literature_map=literature_map,
            gap_outputs=gap_outputs,
            paper_tiers=paper_tiers,
        )
        if not _env_bool("FAROS_EVIDENCE_COVERAGE_LLM_ENABLED", True):
            rule_fallback["warnings"] = [
                *rule_fallback.get("warnings", []),
                "LLM Evidence Coverage disabled by FAROS_EVIDENCE_COVERAGE_LLM_ENABLED",
            ]
            return rule_fallback
        if not session.config.providerName or not session.config.model:
            rule_fallback["warnings"] = [
                *rule_fallback.get("warnings", []),
                "No provider/model configured for LLM Evidence Coverage",
            ]
            return rule_fallback

        paper_summaries = []
        for index, paper in enumerate(structured_papers[:12], 1):
            paper_summaries.append({
                "index": index,
                "paperId": paper.id,
                "title": paper.title,
                "source": paper.source,
                "evidenceTier": paper_tiers.get(paper.id, "unclassified"),
                "summary": paper.summary[:650],
                "abstract": paper.abstract[:450],
                "claims": [claim.text[:240] for claim in paper.claims[:4]],
                "limitations": paper.limitations[:4],
                "openQuestions": paper.openQuestions[:4],
                "failedAssumptions": paper.failedAssumptions[:4],
                "methodWeaknesses": paper.methodWeaknesses[:4],
                "missingEvaluation": paper.missingEvaluation[:4],
                "methods": [
                    f"{method.name}: {method.description[:180]}"
                    for method in paper.methods[:4]
                ],
                "datasets": paper.datasets[:5],
                "metrics": paper.metrics[:5],
                "baselineMethods": paper.baselineMethods[:5],
                "recommendedMetrics": paper.recommendedMetrics[:5],
                "noveltyEvidence": [
                    {
                        "type": evidence.evidenceType,
                        "direction": evidence.direction,
                        "description": evidence.description[:240],
                    }
                    for evidence in paper.noveltyEvidence[:4]
                ],
            })

        literature_gaps = [
            {
                "direction": gap.direction,
                "evidence": gap.evidence,
                "paperIds": gap.paperIds,
                "confidence": gap.confidence,
            }
            for gap in (getattr(literature_map, "gaps", []) or [])[:8]
        ]
        prompt = {
            "task": (
                "Act as an LLM-Scientist evidence coverage reviewer. "
                "First infer the evidence dimensions required by the seed query, "
                "then map the provided papers and gaps to those dimensions."
            ),
            "seedQuery": session.config.seedQuery,
            "domain": session.config.domain or "",
            "paperType": session.config.paperType,
            "ruleGate": rule_gate,
            "dimensionHints": _default_coverage_dimensions(
                session.config.seedQuery,
                session.config.paperType,
            ),
            "papers": paper_summaries,
            "gapContext": {
                "literatureMapGaps": literature_gaps,
                "gapAnalysis": gap_outputs.get("gapAnalysis", [])[:5],
                "prioritizedGaps": gap_outputs.get("prioritizedGaps", [])[:5],
                "researchOpportunities": gap_outputs.get("researchOpportunities", [])[:5],
            },
            "constraints": [
                "Use only paperId values that appear in the provided papers.",
                "Do not fabricate paper IDs, paper titles, gaps, or claims.",
                "Required dimensions with no direct paper support must be marked missing or weak.",
                "Generate targeted repairQueries for each missing or weak required dimension.",
                "Prefer scientific judgment over keyword matching, but explain evidence gaps concretely.",
            ],
            "responseSchema": {
                "passed": "boolean",
                "overallEvidenceScore": "number from 0 to 1",
                "scientistJudgment": "short natural-language judgment",
                "dimensions": [
                    {
                        "key": "snake_case string",
                        "label": "human readable label",
                        "required": "boolean",
                        "whyRequired": "string",
                        "status": "strong | partial | weak | missing",
                        "score": "number from 0 to 1",
                        "supportingPaperIds": ["raw paper IDs from provided papers only"],
                        "supportedClaims": ["strings grounded in those papers"],
                        "remainingGap": "string",
                        "repairQueries": ["targeted search queries"],
                    }
                ],
                "blockingIssues": ["string"],
                "warnings": ["string"],
                "repairQueries": ["string"],
            },
        }

        try:
            client = get_provider_client(session.config.providerName)
            response = client.chat(
                [
                    ChatMessage(
                        role="system",
                        content=(
                            "You are an LLM-Scientist evidence coverage reviewer. "
                            "Return only valid JSON. Make nuanced scientific judgments, "
                            "but never invent evidence IDs."
                        ),
                    ),
                    ChatMessage(role="user", content=json.dumps(prompt, ensure_ascii=False)),
                ],
                model=session.config.model,
                temperature=0.0,
                max_tokens=2200,
                response_format={"type": "json_object"},
            )
            data = _extract_json_object(response.text)
            if not data:
                repair_response = client.chat(
                    [
                        ChatMessage(
                            role="system",
                            content=(
                                "Repair the following evidence coverage review into strict JSON only. "
                                "Preserve the scientific judgment and all paper IDs exactly. "
                                "Do not add new evidence, claims, or IDs. "
                                "Required top-level keys: passed, overallEvidenceScore, scientistJudgment, "
                                "dimensions, blockingIssues, warnings, repairQueries. "
                                "Each dimension must include key, label, required, whyRequired, status, score, "
                                "supportingPaperIds, supportedClaims, remainingGap, repairQueries."
                            ),
                        ),
                        ChatMessage(role="user", content=response.text[:6000]),
                    ],
                    model=session.config.model,
                    temperature=0.0,
                    max_tokens=1800,
                    response_format={"type": "json_object"},
                )
                data = _extract_json_object(repair_response.text)
                if not data:
                    rule_fallback["warnings"] = [
                        *rule_fallback.get("warnings", []),
                        "LLM Evidence Coverage returned non-JSON output; repair failed; rule fallback used.",
                    ]
                    rule_fallback["llmStatus"] = "non_json"
                    rule_fallback["llmLatencyMs"] = response.latency_ms + repair_response.latency_ms
                    return rule_fallback
            report = _normalize_coverage_report(
                data,
                available_paper_ids={paper.id for paper in structured_papers},
                paper_tiers=paper_tiers,
                source="llm",
            )
            report["latencyMs"] = response.latency_ms
            if "repair_response" in locals():
                report["latencyMs"] = response.latency_ms + repair_response.latency_ms
                report["llmStatus"] = "json_repaired"
                report["warnings"] = [
                    *report.get("warnings", []),
                    "LLM Evidence Coverage JSON was repaired before validation.",
                ]
            return report
        except Exception as exc:
            logger.warning("LLM Evidence Coverage failed: %s", exc)
            rule_fallback["warnings"] = [
                *rule_fallback.get("warnings", []),
                f"LLM Evidence Coverage failed; rule fallback used: {exc}",
            ]
            rule_fallback["llmStatus"] = "error"
            return rule_fallback

    def _run_evidence_llm_reviewer(
        self,
        *,
        session: IdeaSession,
        rule_gate: Dict[str, Any],
        structured_papers: List[StructuredPaper],
        literature_map: Optional[LiteratureMap],
        gap_outputs: Dict[str, Any],
    ) -> Dict[str, Any]:
        """LLM reviewer for semantic evidence quality before idea generation."""

        if not _env_bool("FAROS_EVIDENCE_GATE_LLM_ENABLED", True):
            return {
                "status": "skipped",
                "passed": True,
                "score": 1.0,
                "warnings": ["LLM Evidence Reviewer disabled by FAROS_EVIDENCE_GATE_LLM_ENABLED"],
            }
        if not session.config.providerName or not session.config.model:
            return {
                "status": "skipped",
                "passed": True,
                "score": 1.0,
                "warnings": ["No provider/model configured for LLM Evidence Reviewer"],
            }

        paper_summaries = []
        for index, paper in enumerate(structured_papers[:8], 1):
            paper_summaries.append({
                "index": index,
                "paperId": paper.id,
                "title": paper.title,
                "source": paper.source,
                "summary": paper.summary[:500],
                "claims": [claim.text[:240] for claim in paper.claims[:3]],
                "methods": [
                    f"{method.name}: {method.description[:160]}"
                    for method in paper.methods[:3]
                ],
                "limitations": paper.limitations[:3],
                "openQuestions": paper.openQuestions[:3],
                "failedAssumptions": paper.failedAssumptions[:3],
                "methodWeaknesses": paper.methodWeaknesses[:3],
                "missingEvaluation": paper.missingEvaluation[:3],
                "noveltyEvidence": [
                    {
                        "type": evidence.evidenceType,
                        "direction": evidence.direction,
                        "description": evidence.description[:220],
                    }
                    for evidence in paper.noveltyEvidence[:3]
                ],
                "datasets": paper.datasets[:4],
                "metrics": paper.metrics[:4],
                "baselineMethods": paper.baselineMethods[:4],
                "recommendedMetrics": paper.recommendedMetrics[:4],
            })

        literature_gaps = [
            {
                "direction": gap.direction,
                "evidence": gap.evidence,
                "paperIds": gap.paperIds,
                "confidence": gap.confidence,
            }
            for gap in (getattr(literature_map, "gaps", []) or [])[:6]
        ]
        gap_context = {
            "literatureMapGaps": literature_gaps,
            "gapAnalysis": gap_outputs.get("gapAnalysis", [])[:5],
            "prioritizedGaps": gap_outputs.get("prioritizedGaps", [])[:5],
            "researchOpportunities": gap_outputs.get("researchOpportunities", [])[:5],
        }
        prompt = {
            "task": "Review whether this evidence pool is strong enough to generate scientific research ideas.",
            "seedQuery": session.config.seedQuery,
            "domain": session.config.domain or "",
            "paperType": session.config.paperType,
            "ruleGate": rule_gate,
            "papers": paper_summaries,
            "gapContext": gap_context,
            "criteria": [
                "Papers must be semantically about the seed query, not just generic related ML topics.",
                "At least one clear gap or limitation must be supported by paper evidence.",
                "The evidence pool must support the requested paper type and downstream idea generation.",
                "Flag theme drift, generic local-corpus evidence, unsupported gaps, or missing evaluation signals.",
            ],
            "responseSchema": {
                "passed": "boolean",
                "score": "number from 0 to 1",
                "confidence": "number from 0 to 1",
                "themeAligned": "boolean",
                "evidenceFaithful": "boolean",
                "gapGrounded": "boolean",
                "blockingIssues": ["string"],
                "warnings": ["string"],
                "repairQueries": ["string"],
                "recommendation": "string",
            },
        }

        try:
            client = get_provider_client(session.config.providerName)
            response = client.chat(
                [
                    ChatMessage(
                        role="system",
                        content=(
                            "You are a strict scientific evidence reviewer. "
                            "Return only valid JSON. Do not invent paper IDs. "
                            "Reject evidence pools that are off-topic, generic, or lack explicit gaps."
                        ),
                    ),
                    ChatMessage(role="user", content=json.dumps(prompt, ensure_ascii=False)),
                ],
                model=session.config.model,
                max_tokens=900,
            )
            data = _extract_json_object(response.text)
            if not data:
                return {
                    "status": "error",
                    "passed": False,
                    "score": 0.0,
                    "error": "LLM Evidence Reviewer returned non-JSON output",
                }
            review = _normalize_evidence_llm_review(data)
            review["latencyMs"] = response.latency_ms
            return review
        except Exception as exc:
            logger.warning("LLM Evidence Reviewer failed: %s", exc)
            return {
                "status": "error",
                "passed": False,
                "score": 0.0,
                "error": str(exc),
            }

    def _dedupe_candidates(
        self,
        candidates: List[IdeaCandidate],
        *,
        max_count: Optional[int] = None,
        threshold: float = 0.72,
    ) -> tuple[List[IdeaCandidate], List[str]]:
        """Remove near-duplicate ideas before expensive ranking/review."""

        kept: List[IdeaCandidate] = []
        kept_keys: List[set[str]] = []
        removed_ids: List[str] = []
        for index, candidate in enumerate(candidates):
            key = _candidate_similarity_key(candidate)
            is_duplicate = any(
                _candidate_jaccard(key, existing_key) >= threshold
                for existing_key in kept_keys
            )
            if is_duplicate:
                removed_ids.append(candidate.id)
                continue
            kept.append(candidate)
            kept_keys.append(key)
            if max_count is not None and len(kept) >= max_count:
                removed_ids.extend(item.id for item in candidates[index + 1:])
                break
        return kept, removed_ids
    
    def create_session(self, config: IdeaSessionConfig) -> IdeaSession:
        """Create a new idea generation session."""
        session = IdeaSession(
            id=generate_session_id(),
            config=config,
            status=IdeaSessionStatus.PENDING,
            createdAt=_utcnow(),
        )
        return self.session_storage.create(session)
    
    def get_session(self, session_id: str) -> Optional[IdeaSession]:
        """Get session by ID."""
        return self.session_storage.get(session_id)
    
    def list_sessions(self, status: Optional[IdeaSessionStatus] = None) -> List[IdeaSession]:
        """List all sessions."""
        return self.session_storage.list_all(status)
    
    def start_session(self, session_id: str) -> IdeaSession:
        """Start a session (pending -> running)."""
        session = self.session_storage.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        
        if session.status != IdeaSessionStatus.PENDING:
            raise ValueError(f"Cannot start session in {session.status} state")
        
        session.status = IdeaSessionStatus.RUNNING
        session.startedAt = _utcnow()
        session.trace = WorkflowTrace(
            sessionId=session_id,
            startedAt=_utcnow(),
        )
        
        return self.session_storage.update(session)

    def resume_session(self, session_id: str) -> IdeaSession:
        """Resume a session paused for evidence or candidate regeneration."""
        session = self.session_storage.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        if session.status not in {
            IdeaSessionStatus.AWAITING_EVIDENCE,
            IdeaSessionStatus.AWAITING_IDEAS,
        }:
            raise ValueError(f"Cannot resume session in {session.status} state")
        session.status = IdeaSessionStatus.RUNNING
        session.endedAt = None
        session.errorMessage = None
        if session.trace:
            session.trace.endedAt = None
        return self.session_storage.update(session)
    
    def cancel_session(self, session_id: str) -> IdeaSession:
        """Cancel a running session."""
        session = self.session_storage.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        
        if session.is_terminal():
            raise ValueError(f"Cannot cancel session in {session.status} state")
        
        session.status = IdeaSessionStatus.CANCELLED
        session.endedAt = _utcnow()
        if session.trace:
            session.trace.endedAt = _utcnow()
        
        return self.session_storage.update(session)
    
    def get_literature(self, session_id: str) -> List[LiteratureItem]:
        """Get literature items for a session."""
        return self.literature_storage.list_by_session(session_id)
    
    def get_candidates(self, session_id: str, view: str = "final") -> List[IdeaCandidate]:
        """Get candidates for a session.

        The default view is product-facing: only candidates that survived the
        internal review/repair loop are returned. Use view="debug" to inspect
        every generated candidate.
        """
        candidates = self.candidate_storage.list_by_session(session_id)
        if view in {"debug", "all"}:
            return candidates

        session = self.session_storage.get(session_id)
        final_ids = list(getattr(session, "finalCandidateIds", []) or []) if session else []
        if not final_ids:
            return []

        by_id = {candidate.id: candidate for candidate in candidates}
        return [by_id[candidate_id] for candidate_id in final_ids if candidate_id in by_id]

    def _candidate_embedded_evidence(self, candidate: IdeaCandidate) -> Optional[CandidateGraphEvidence]:
        value = getattr(candidate, "graphEvidence", None)
        if isinstance(value, CandidateGraphEvidence):
            return value
        if isinstance(value, dict):
            try:
                return CandidateGraphEvidence(**value)
            except Exception:
                return None
        return None

    def _candidate_embedded_prior_work(self, candidate: IdeaCandidate) -> List[PriorWorkComparison]:
        items: List[PriorWorkComparison] = []
        for value in getattr(candidate, "closestPriorWork", []) or []:
            if isinstance(value, PriorWorkComparison):
                items.append(value)
            elif isinstance(value, dict):
                try:
                    items.append(PriorWorkComparison(**value))
                except Exception:
                    continue
        return items

    def _candidate_embedded_critique(self, candidate: IdeaCandidate) -> Optional[IdeaCritique]:
        value = getattr(candidate, "critique", None)
        if isinstance(value, IdeaCritique):
            return value
        if isinstance(value, dict):
            try:
                return IdeaCritique(**value)
            except Exception:
                return None
        return None

    def _rule_only_idea_review_gate(
        self,
        *,
        candidate: IdeaCandidate,
        seed_query: str,
    ) -> Dict[str, Any]:
        evidence = self._candidate_embedded_evidence(candidate)
        comparisons = self._candidate_embedded_prior_work(candidate)
        critique = self._candidate_embedded_critique(candidate)
        allowed_evidence_refs = self._allowed_idea_evidence_refs(
            candidate=candidate,
            evidence=evidence,
            comparisons=comparisons,
        )
        reviewer_reports = [
            self._rule_idea_reviewer_report(
                spec=spec,
                candidate=candidate,
                evidence=evidence,
                comparisons=comparisons,
                critique=critique,
                seed_query=seed_query,
                allowed_evidence_refs=allowed_evidence_refs,
            )
            for spec in IDEA_REVIEWER_SPECS
        ]
        blocking = [
            issue
            for report in reviewer_reports
            for issue in report.get("blockingIssues", [])
        ]
        repairs = [
            instruction
            for report in reviewer_reports
            for instruction in report.get("repairInstructions", [])
        ]
        passed = not blocking and all(report.get("passed", False) for report in reviewer_reports)
        return {
            "candidateId": candidate.id,
            "passed": passed,
            "scoreAfterGate": candidate.overallScore,
            "blockingIssues": list(dict.fromkeys(str(item) for item in blocking if str(item).strip())),
            "warnings": [],
            "suggestedImprovements": list(dict.fromkeys(str(item) for item in repairs if str(item).strip())),
            "reviewerReports": reviewer_reports,
            "revalidationMode": "rule_only",
        }

    def revalidate_final_candidates(self, session_id: str) -> IdeaSession:
        """Re-run current hard gates on an existing session's candidate pool.

        This is intentionally rule-only: it lets old sessions benefit from new
        safety gates without spending LLM calls. If every candidate fails, no
        final candidate is exposed; the session remains available for debug view
        and can be regenerated by the normal pipeline.
        """

        session = self.session_storage.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        candidates = self.candidate_storage.list_by_session(session_id)
        if not candidates:
            summary = dict(session.qualityLoopSummary or {})
            summary["revalidation"] = {
                "status": "no_candidates",
                "removedFinalCandidateIds": [],
                "finalCandidateIds": [],
                "checkedCandidateCount": 0,
            }
            session.qualityLoopSummary = summary
            return self.session_storage.update(session)

        previous_final_ids = list(getattr(session, "finalCandidateIds", []) or [])
        gate_reports = {
            candidate.id: self._rule_only_idea_review_gate(
                candidate=candidate,
                seed_query=session.config.seedQuery,
            )
            for candidate in candidates
        }
        shortlist = self._select_final_candidates(
            candidates,
            gate_reports,
            max_count=self._target_final_candidate_count(session, candidates),
            allow_failed_fallback=False,
        )
        removed_final_ids = [
            candidate_id
            for candidate_id in previous_final_ids
            if candidate_id not in shortlist["finalCandidateIds"]
        ]
        session.finalCandidateIds = shortlist["finalCandidateIds"]
        session.hiddenCandidateIds = shortlist["hiddenCandidateIds"]
        session.rejectedCandidateIds = shortlist["rejectedCandidateIds"]
        summary = dict(session.qualityLoopSummary or {})
        summary.update(shortlist["summary"])
        summary["revalidation"] = {
            "status": "updated" if removed_final_ids else "unchanged",
            "mode": "rule_only",
            "removedFinalCandidateIds": removed_final_ids,
            "finalCandidateIds": list(session.finalCandidateIds),
            "checkedCandidateCount": len(candidates),
            "blockingByCandidate": {
                candidate_id: report.get("blockingIssues", [])
                for candidate_id, report in gate_reports.items()
                if report.get("blockingIssues")
            },
        }
        session.qualityLoopSummary = summary
        return self.session_storage.update(session)
    
    def select_candidate(self, session_id: str, candidate_id: str) -> IdeaSession:
        """Select a candidate for the session."""
        session = self.session_storage.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        
        candidate = self.candidate_storage.get(candidate_id)
        if not candidate:
            raise ValueError(f"Candidate {candidate_id} not found")
        
        if candidate.sessionId != session_id:
            raise ValueError(f"Candidate {candidate_id} does not belong to session {session_id}")
        
        session.selectedCandidateId = candidate_id
        return self.session_storage.update(session)

    def _idea_candidate_quality_score(
        self,
        candidate: IdeaCandidate,
        review_gate: Optional[Dict[str, Any]] = None,
    ) -> float:
        """Score candidates for the product-facing shortlist after review."""
        score = float(getattr(candidate, "overallScore", 0.0) or 0.0)
        if review_gate:
            score = max(score, float(review_gate.get("scoreAfterGate", score) or score))
            if review_gate.get("passed"):
                score += 0.35
            score -= len(review_gate.get("blockingIssues", []) or []) * 1.6
            score -= min(0.8, len(review_gate.get("warnings", []) or []) * 0.12)
        score += max(0.0, float(getattr(candidate, "scoringConfidence", 0.5) or 0.5) - 0.5) * 0.5
        return round(max(0.0, min(10.0, score)), 3)

    def _passes_final_candidate_quality(
        self,
        candidate: IdeaCandidate,
        review_gate: Optional[Dict[str, Any]] = None,
        *,
        strict: bool = True,
    ) -> bool:
        """Return whether a candidate is strong enough for the default UI."""
        if review_gate:
            if review_gate.get("blockingIssues"):
                return False
            if not review_gate.get("passed", False):
                return False

        if strict:
            return (
                candidate.overallScore >= 6.8
                and candidate.alignment >= 5.8
                and candidate.referenceSupport >= 4.8
                and candidate.experimentSpecificity >= 5.0
            )
        return (
            candidate.overallScore >= 6.0
            and candidate.referenceSupport >= 4.3
        )

    def _candidate_direction_type(self, candidate: Optional[IdeaCandidate]) -> str:
        if not candidate or not getattr(candidate, "draftPlan", None):
            return "unknown"
        for tag in getattr(candidate.draftPlan, "tags", []) or []:
            text = str(tag or "").strip()
            if text.lower().startswith("directiontype:"):
                value = text.split(":", 1)[1].strip()
                return value or "unknown"
        return "unknown"

    def _candidate_direction_id(self, candidate: Optional[IdeaCandidate]) -> str:
        if not candidate or not getattr(candidate, "draftPlan", None):
            return ""
        for tag in getattr(candidate.draftPlan, "tags", []) or []:
            text = str(tag or "").strip()
            if text.lower().startswith("direction:"):
                return text.split(":", 1)[1].strip()
        return ""

    def _candidate_direction_title(self, candidate: Optional[IdeaCandidate]) -> str:
        if not candidate or not getattr(candidate, "draftPlan", None):
            return ""
        for tag in getattr(candidate.draftPlan, "tags", []) or []:
            text = str(tag or "").strip()
            if text.lower().startswith("directiontitle:"):
                return text.split(":", 1)[1].strip()
        return ""

    def _copy_candidate_direction_metadata(
        self,
        *,
        source: IdeaCandidate,
        target: IdeaCandidate,
    ) -> None:
        """Keep direction routing stable after idea repair/regeneration."""

        if not getattr(source, "draftPlan", None):
            return
        source_tags = [
            str(tag or "").strip()
            for tag in (source.draftPlan.tags or [])
            if str(tag or "").strip().lower().startswith(
                ("direction:", "directiontype:", "directiontitle:")
            )
        ]
        if not source_tags:
            return
        if not target.draftPlan:
            target.draftPlan = DraftPlan(
                researchQuestion=target.problem,
                hypothesis=target.hypothesisStatement or target.keyInsight,
                methodology=target.proposedMethod,
                expectedOutcomes=target.expectedMetrics,
            )
        target_tags = list(target.draftPlan.tags or [])
        for tag in source_tags:
            if tag not in target_tags:
                target_tags.append(tag)
        target.draftPlan.tags = target_tags

    def _candidate_direction_types(
        self,
        candidates: List[IdeaCandidate],
        *,
        known_only: bool = False,
    ) -> List[str]:
        values: List[str] = []
        for candidate in candidates:
            direction_type = self._candidate_direction_type(candidate)
            if known_only and direction_type == "unknown":
                continue
            values.append(direction_type)
        return values

    def _direction_diversity_satisfied(self, candidates: List[IdeaCandidate]) -> bool:
        known_types = self._candidate_direction_types(candidates, known_only=True)
        return len(candidates) < 2 or len(set(known_types)) >= 2

    def _direction_aware_quality_score(
        self,
        candidate: IdeaCandidate,
        review_gate: Optional[Dict[str, Any]] = None,
    ) -> float:
        """Stable tie-breaker score for reviewed candidates.

        The base score still carries the scientific quality signal. Small
        evidence/reviewer/direction nudges keep equal-looking candidates from
        collapsing to identical shortlist scores.
        """

        score = self._idea_candidate_quality_score(candidate, review_gate)
        if review_gate:
            try:
                prior_confidence = float(review_gate.get("priorWorkComparisonConfidence", 0.0) or 0.0)
            except (TypeError, ValueError):
                prior_confidence = 0.0
            score += max(0.0, min(1.0, prior_confidence)) * 0.08
            reviewer_reports = review_gate.get("reviewerReports", []) or []
            reviewer_scores: List[float] = []
            reviewer_confidences: List[float] = []
            for report in reviewer_reports:
                if not isinstance(report, dict):
                    continue
                try:
                    reviewer_scores.append(float(report.get("score", 0.0) or 0.0))
                except (TypeError, ValueError):
                    pass
                try:
                    reviewer_confidences.append(float(report.get("confidence", 0.0) or 0.0))
                except (TypeError, ValueError):
                    pass
            if reviewer_scores:
                avg_reviewer_score = sum(reviewer_scores) / len(reviewer_scores)
                score += max(-0.12, min(0.12, (avg_reviewer_score - 7.0) * 0.03))
            if reviewer_confidences:
                avg_confidence = sum(reviewer_confidences) / len(reviewer_confidences)
                score += max(0.0, min(1.0, avg_confidence)) * 0.04
        direction_type = self._candidate_direction_type(candidate)
        direction_priority = {
            "method": 0.050,
            "benchmark": 0.045,
            "safety_reliability": 0.040,
            "system": 0.035,
            "application": 0.030,
        }
        score += direction_priority.get(direction_type, 0.0)
        return round(max(0.0, min(10.0, score)), 3)

    def _select_final_candidates(
        self,
        ranked: List[IdeaCandidate],
        gate_reports: Dict[str, Dict[str, Any]],
        *,
        max_count: int = 3,
        allow_failed_fallback: bool = True,
    ) -> Dict[str, Any]:
        """Select the small candidate set shown to users.

        Internal generation may keep many candidates for exploration and repair.
        This selector exposes only diverse, review-passed ideas by default.
        """
        target_count = 2
        scored = sorted(
            ranked,
            key=lambda candidate: self._direction_aware_quality_score(
                candidate,
                gate_reports.get(candidate.id),
            ),
            reverse=True,
        )
        final: List[IdeaCandidate] = []
        final_keys: List[set[str]] = []
        warnings: List[str] = []

        def _try_add(pool: List[IdeaCandidate], *, similarity_limit: float) -> None:
            for candidate in pool:
                if len(final) >= target_count:
                    return
                if any(existing.id == candidate.id for existing in final):
                    continue
                candidate_key = _candidate_similarity_key(candidate)
                max_similarity = max(
                    (_candidate_jaccard(candidate_key, key) for key in final_keys),
                    default=0.0,
                )
                if max_similarity > similarity_limit:
                    continue
                final.append(candidate)
                final_keys.append(candidate_key)

        def _try_add_new_direction(pool: List[IdeaCandidate], *, similarity_limit: float) -> None:
            known_final_types = set(self._candidate_direction_types(final, known_only=True))
            if not known_final_types:
                return
            for candidate in pool:
                if len(final) >= target_count:
                    return
                if any(existing.id == candidate.id for existing in final):
                    continue
                direction_type = self._candidate_direction_type(candidate)
                if direction_type == "unknown" or direction_type in known_final_types:
                    continue
                candidate_key = _candidate_similarity_key(candidate)
                max_similarity = max(
                    (_candidate_jaccard(candidate_key, key) for key in final_keys),
                    default=0.0,
                )
                if max_similarity > similarity_limit:
                    continue
                final.append(candidate)
                final_keys.append(candidate_key)
                known_final_types.add(direction_type)

        strict_pool = [
            candidate for candidate in scored
            if self._passes_final_candidate_quality(candidate, gate_reports.get(candidate.id), strict=True)
        ]
        _try_add(strict_pool[:1], similarity_limit=0.72)
        if len(final) < target_count:
            _try_add_new_direction(strict_pool, similarity_limit=0.84)
        if len(final) < target_count:
            _try_add(strict_pool, similarity_limit=0.72)

        relaxed_pool = [
            candidate for candidate in scored
            if self._passes_final_candidate_quality(candidate, gate_reports.get(candidate.id), strict=False)
        ]
        if len(final) < min(2, target_count):
            _try_add_new_direction(relaxed_pool, similarity_limit=0.88)
            _try_add(relaxed_pool, similarity_limit=0.84)
            warnings.append("Strict shortlist produced fewer than two candidates; relaxed reviewed candidates were considered.")

        if not final and scored and allow_failed_fallback:
            final.append(scored[0])
            warnings.append("No candidate passed final shortlist thresholds; exposing the highest-scored candidate for manual inspection.")
        elif not final and scored:
            warnings.append("No candidate passed final shortlist thresholds; final handoff is empty until regeneration.")

        final_ids = [candidate.id for candidate in final]
        rejected_ids = [
            candidate.id for candidate in ranked
            if candidate.id not in final_ids
            and (
                bool((gate_reports.get(candidate.id) or {}).get("blockingIssues"))
                or gate_reports.get(candidate.id, {}).get("passed") is False
            )
        ]
        hidden_ids = [
            candidate.id for candidate in ranked
            if candidate.id not in final_ids and candidate.id not in rejected_ids
        ]
        final_scores = {
            candidate.id: self._direction_aware_quality_score(candidate, gate_reports.get(candidate.id))
            for candidate in ranked
        }
        final_direction_types = self._candidate_direction_types(final)
        passing_direction_types = set(
            self._candidate_direction_type(candidate)
            for candidate in relaxed_pool
            if self._candidate_direction_type(candidate) != "unknown"
        )
        direction_diversity_satisfied = self._direction_diversity_satisfied(final)
        requires_regeneration = len(final_ids) < target_count
        quality_status = "ready" if not requires_regeneration else "insufficient_final_candidates"
        if requires_regeneration:
            warnings.append(
                f"Final shortlist has {len(final_ids)} candidate(s), below target {target_count}; run another repair/regeneration pass before downstream handoff."
            )
        if (
            target_count >= 2
            and len(final) >= 2
            and not direction_diversity_satisfied
            and passing_direction_types
        ):
            warnings.append(
                "Final shortlist lacks direction diversity because only one direction passed review."
            )
        return {
            "finalCandidateIds": final_ids,
            "hiddenCandidateIds": hidden_ids,
            "rejectedCandidateIds": rejected_ids,
            "summary": {
                "mode": "internal_review_shortlist",
                "generatedCandidateCount": len(ranked),
                "finalCandidateCount": len(final_ids),
                "hiddenCandidateCount": len(hidden_ids),
                "rejectedCandidateCount": len(rejected_ids),
                "targetFinalCandidateCount": target_count,
                "finalCandidateIds": final_ids,
                "finalDirectionTypes": final_direction_types,
                "directionDiversitySatisfied": direction_diversity_satisfied,
                "qualityStatus": quality_status,
                "requiresRegeneration": requires_regeneration,
                "warnings": warnings,
                "qualityScores": final_scores,
            },
        }

    def _target_final_candidate_count(
        self,
        session: IdeaSession,
        ranked: List[IdeaCandidate],
    ) -> int:
        """Target at least two user-facing ideas when the pool can support it."""

        return 2

    def _current_final_ready_candidates(
        self,
        ranked: List[IdeaCandidate],
        gate_reports: Dict[str, Dict[str, Any]],
        target_count: int,
    ) -> List[IdeaCandidate]:
        """Return candidates already strong enough for the reviewed shortlist."""

        scored = sorted(
            ranked,
            key=lambda candidate: self._direction_aware_quality_score(
                candidate,
                gate_reports.get(candidate.id),
            ),
            reverse=True,
        )
        ready: List[IdeaCandidate] = []
        ready_keys: List[set[str]] = []

        def _try_add(pool: List[IdeaCandidate], *, strict: bool, similarity_limit: float) -> None:
            for candidate in pool:
                if len(ready) >= target_count:
                    return
                if any(existing.id == candidate.id for existing in ready):
                    continue
                gate = gate_reports.get(candidate.id)
                if not self._passes_final_candidate_quality(candidate, gate, strict=strict):
                    continue
                candidate_key = _candidate_similarity_key(candidate)
                max_similarity = max(
                    (_candidate_jaccard(candidate_key, key) for key in ready_keys),
                    default=0.0,
                )
                if max_similarity > similarity_limit:
                    continue
                ready.append(candidate)
                ready_keys.append(candidate_key)

        _try_add(scored, strict=True, similarity_limit=0.72)
        if len(ready) < target_count:
            _try_add(scored, strict=False, similarity_limit=0.84)
        return ready

    def _candidate_pool_failure_text(
        self,
        review_gate: Optional[Dict[str, Any]],
        paper_quality_gate: Optional[Dict[str, Any]],
    ) -> str:
        values: List[str] = []

        def _collect_from_gate(gate: Optional[Dict[str, Any]]) -> None:
            if not isinstance(gate, dict):
                return
            for key in [
                "blockingIssues",
                "warnings",
                "suggestedImprovements",
                "repairInstructions",
                "errors",
                "missingCoverage",
            ]:
                value = gate.get(key)
                if isinstance(value, list):
                    values.extend(str(item) for item in value)
                elif isinstance(value, str):
                    values.append(value)

        _collect_from_gate(review_gate)
        _collect_from_gate(paper_quality_gate)

        if isinstance(review_gate, dict):
            for report in review_gate.get("reviewerReports", []) or []:
                if isinstance(report, dict):
                    _collect_from_gate(report)

        return " ".join(item for item in values if item).lower()

    def _paper_quality_gate_indicates_bad_pool(
        self,
        paper_quality_gate: Optional[Dict[str, Any]],
    ) -> bool:
        if not isinstance(paper_quality_gate, dict):
            return False
        if not paper_quality_gate.get("passed", True):
            return True
        if paper_quality_gate.get("hardBlocked"):
            return True

        try:
            external_count = int(paper_quality_gate.get("externalPaperCount", 0) or 0)
            min_external = int(paper_quality_gate.get("minExternalPaperCount", 0) or 0)
        except (TypeError, ValueError):
            external_count = 0
            min_external = 0
        if min_external and external_count < min_external:
            return True

        try:
            gap_signal_count = int(paper_quality_gate.get("gapSignalCount", 0) or 0)
            min_gap_signal_count = int(paper_quality_gate.get("minGapSignalCount", 0) or 0)
        except (TypeError, ValueError):
            gap_signal_count = 0
            min_gap_signal_count = 0
        if min_gap_signal_count and gap_signal_count < min_gap_signal_count:
            return True

        gate_text = self._candidate_pool_failure_text(None, paper_quality_gate)
        bad_pool_terms = [
            "all retrieved papers are from local fallback sources",
            "insufficient external evidence",
            "too few papers",
            "paper pool is too small",
            "weak topic alignment",
            "weak overlap with the seed topic",
            "missing paper-type evidence coverage",
            "no explicit gap or limitation signal",
            "local fallback",
            "论文池",
            "外部证据",
            "主题相关",
            "缺口信号",
        ]
        return any(term in gate_text for term in bad_pool_terms)

    def _candidate_pool_failure_route(
        self,
        candidate: Optional[IdeaCandidate],
        review_gate: Optional[Dict[str, Any]],
        paper_quality_gate: Optional[Dict[str, Any]],
    ) -> str:
        """Diagnose the root failure so repair loops can choose the right path."""

        if self._paper_quality_gate_indicates_bad_pool(paper_quality_gate):
            return "evidence_pool_bad"
        if not isinstance(review_gate, dict):
            return "none"
        if review_gate.get("passed") and not review_gate.get("blockingIssues"):
            return "none"

        text = self._candidate_pool_failure_text(review_gate, paper_quality_gate)

        off_topic_terms = [
            "weak topic overlap",
            "off-topic",
            "off topic",
            "topic drift",
            "drifted away",
            "seed query",
            "unrelated to the seed",
            "主题漂移",
            "跑题",
            "不相关",
        ]
        novelty_terms = [
            "closest prior",
            "closest-prior",
            "prior-work",
            "prior work",
            "novelty comparison",
            "comparison confidence",
            "baseline search",
            "contradiction search",
            "novelty unclear",
            "prior art",
            "已有工作",
            "相关工作对比",
            "创新性不清",
        ]
        ungrounded_terms = [
            "no valid evidence",
            "not sufficiently grounded",
            "ungrounded",
            "evidence id",
            "evidence ids",
            "evidencerefs",
            "evidence refs",
            "graph evidence",
            "supporting evidence",
            "claim to evidence",
            "claims to evidence",
            "unsupported claim",
            "citation missing",
            "missing citation",
            "没有证据",
            "未引用证据",
            "证据引用",
        ]
        method_terms = [
            "specificity",
            "too vague",
            "vague",
            "method",
            "hypothesis",
            "metric",
            "dataset",
            "experiment",
            "variable",
            "implementation",
            "contribution",
            "unclear",
            "不具体",
            "太泛",
            "方法",
            "假设",
            "实验",
            "指标",
            "变量",
        ]

        if any(term in text for term in off_topic_terms):
            return "off_topic"
        try:
            prior_work_confidence = float(
                review_gate.get("priorWorkComparisonConfidence", 1.0) or 1.0
            )
        except (TypeError, ValueError):
            prior_work_confidence = 1.0
        if any(term in text for term in novelty_terms) or prior_work_confidence < 0.35:
            return "novelty_unclear"
        if any(term in text for term in ungrounded_terms):
            return "candidate_ungrounded"
        if any(term in text for term in method_terms):
            return "method_vague"
        if review_gate.get("blockingIssues") or review_gate.get("suggestedImprovements"):
            return "method_vague"
        return "none"

    def _candidate_pool_repair_action(
        self,
        candidate: IdeaCandidate,
        review_gate: Optional[Dict[str, Any]],
        paper_quality_gate: Dict[str, Any],
    ) -> str:
        """Classify how a non-final candidate should be repaired, if at all."""

        if not review_gate:
            return "none"
        if "llm_regenerated_from_idea_review" in (candidate.scoringMethod or ""):
            return "drop"
        failure_route = self._candidate_pool_failure_route(
            candidate,
            review_gate,
            paper_quality_gate,
        )
        if failure_route == "evidence_pool_bad":
            return "literature_repair"
        if (
            candidate.overallScore < 5.8
            or candidate.alignment < 4.5
            or candidate.novelty < 4.8
        ):
            return "drop"
        if review_gate.get("passed") and self._passes_final_candidate_quality(
            candidate,
            review_gate,
            strict=False,
        ):
            return "none"
        if failure_route == "novelty_unclear":
            return "literature_repair"
        if failure_route in {"candidate_ungrounded", "method_vague", "off_topic"}:
            return "regenerate_idea"
        if (
            review_gate.get("blockingIssues")
            or review_gate.get("suggestedImprovements")
        ):
            return "regenerate_idea"
        return "none"

    def _candidate_pool_repair_reason(
        self,
        candidate: IdeaCandidate,
        review_gate: Optional[Dict[str, Any]],
        action: str,
        failure_route: Optional[str] = None,
    ) -> str:
        route_reasons = {
            "evidence_pool_bad": (
                "The paper pool failed quality checks; run targeted external/gap literature repair "
                "before trusting this candidate."
            ),
            "candidate_ungrounded": (
                "The candidate is not bound to available evidence IDs; rewrite the idea against "
                "existing graph/literature evidence."
            ),
            "novelty_unclear": (
                "The closest-prior-work comparison is too thin; supplement novelty probe evidence "
                "before final ranking."
            ),
            "method_vague": (
                "The method or hypothesis is too vague; rewrite it with concrete variables, metrics, "
                "datasets, and validation steps."
            ),
            "off_topic": (
                "The candidate drifted away from the seed query; regenerate it from the original "
                "research intent."
            ),
        }
        if failure_route in route_reasons:
            return route_reasons[failure_route]
        if action == "literature_repair":
            return "Evidence grounding is insufficient for a high-quality shortlist candidate."
        if action == "regenerate_idea":
            return "The candidate is promising but needs idea-level revision before shortlist handoff."
        if action == "drop":
            return "The candidate is too weak, duplicated, or already regenerated unsuccessfully."
        if review_gate and review_gate.get("warnings"):
            return str(review_gate.get("warnings", ["Candidate does not need repair."])[0])
        return "Candidate does not need repair."

    def _pick_pool_repair_targets(
        self,
        ranked: List[IdeaCandidate],
        gate_reports: Dict[str, Dict[str, Any]],
        *,
        final_ready_ids: set[str],
        paper_quality_gate: Dict[str, Any],
        max_targets: int = 1,
        skipped_candidate_ids: Optional[set[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Pick high-ranked non-final candidates worth repairing."""

        skipped_candidate_ids = skipped_candidate_ids or set()
        ready_direction_types = {
            self._candidate_direction_type(candidate)
            for candidate in ranked
            if candidate.id in final_ready_ids
            and self._candidate_direction_type(candidate) != "unknown"
        }
        scored = sorted(
            ranked,
            key=lambda candidate: self._direction_aware_quality_score(
                candidate,
                gate_reports.get(candidate.id),
            ),
            reverse=True,
        )
        targets: List[Dict[str, Any]] = []
        for candidate in scored:
            if candidate.id in final_ready_ids or candidate.id in skipped_candidate_ids:
                continue
            gate = gate_reports.get(candidate.id)
            failure_route = self._candidate_pool_failure_route(
                candidate,
                gate,
                paper_quality_gate,
            )
            action = self._candidate_pool_repair_action(candidate, gate, paper_quality_gate)
            if action not in {"literature_repair", "regenerate_idea"}:
                continue
            direction_type = self._candidate_direction_type(candidate)
            direction_id = self._candidate_direction_id(candidate)
            fills_missing_direction = (
                direction_type != "unknown"
                and direction_type not in ready_direction_types
            )
            quality_score = self._direction_aware_quality_score(candidate, gate)
            targets.append({
                "candidate": candidate,
                "candidateId": candidate.id,
                "action": action,
                "failureRoute": failure_route,
                "reason": self._candidate_pool_repair_reason(
                    candidate,
                    gate,
                    action,
                    failure_route,
                ),
                "qualityScore": quality_score,
                "directionType": direction_type,
                "directionId": direction_id,
                "fillsMissingDirection": fills_missing_direction,
            })
        targets = sorted(
            targets,
            key=lambda target: (
                0 if target.get("fillsMissingDirection") else 1,
                -float(target.get("qualityScore") or 0.0),
            ),
        )
        return targets[:max_targets]
    
    def run_pipeline(self, session_id: str) -> IdeaSession:
        """
        Run the complete idea generation pipeline.
        
        Steps:
        1. expandQuery - Expand the seed query
        2. literatureSearch - Search for relevant papers
        3. noveltyCheck - Check novelty of potential ideas
        4. gapAnalysis - Analyze research gaps
        5. evidenceGate - Verify evidence quality before idea generation
        6. ideaBrainstorm - Generate candidate ideas
        7. rankCandidates - Rank and score candidates
        8. finalizeSession - Finalize the session
        """
        with self._pipeline_lock_guard:
            pipeline_lock = self._pipeline_locks.setdefault(session_id, threading.Lock())

        if not pipeline_lock.acquire(blocking=False):
            logger.warning("Pipeline already running for session %s; duplicate start ignored", session_id)
            session = self.session_storage.get(session_id)
            if not session:
                raise ValueError(f"Session {session_id} not found")
            return session

        try:
            session = self.session_storage.get(session_id)
            if not session:
                raise ValueError(f"Session {session_id} not found")

            if session.status != IdeaSessionStatus.RUNNING:
                raise ValueError(f"Session must be in RUNNING state, got {session.status}")

            try:
                pipeline_steps = [
                    ("expandQuery", self._step_expand_query),
                    ("literatureSearch", self._step_literature_search),
                    ("noveltyCheck", self._step_novelty_check),
                    ("gapAnalysis", self._step_gap_analysis),
                    ("evidenceGate", self._step_evidence_gate),
                    ("ideaBrainstorm", self._step_idea_brainstorm),
                    ("rankCandidates", self._step_rank_candidates),
                    ("finalizeSession", self._step_finalize),
                ]
                resume_from = str((session.qualityLoopSummary or {}).get("resumeFrom", ""))
                start_index = next(
                    (
                        index
                        for index, (step_name, _) in enumerate(pipeline_steps)
                        if step_name == resume_from
                    ),
                    0,
                )
                for step_name, step_func in pipeline_steps[start_index:]:
                    session = self._run_step(session, step_name, step_func)

                if len(session.finalCandidateIds) < 2:
                    raise AwaitingIdeasError(
                        "Idea session cannot complete with fewer than two approved candidates"
                    )

                # Mark completed
                session.status = IdeaSessionStatus.COMPLETED
                session.qualityLoopSummary = {
                    **(session.qualityLoopSummary or {}),
                    "qualityStatus": "ready",
                    "requiresRegeneration": False,
                }
                session.endedAt = _utcnow()
                if session.trace:
                    session.trace.endedAt = _utcnow()

                return self.session_storage.update(session)

            except RecoverableIdeaError as e:
                logger.warning("Pipeline paused for session %s: %s", session_id, e)
                session = self.session_storage.get(session_id) or session
                session.status = e.waiting_status
                session.errorMessage = str(e)
                session.endedAt = None
                session.qualityLoopSummary = {
                    **(session.qualityLoopSummary or {}),
                    "qualityStatus": e.waiting_status.value,
                    "requiresRegeneration": True,
                    "resumeFrom": e.resume_from,
                    "blockingReason": str(e),
                }
                if session.trace:
                    session.trace.endedAt = None
                return self.session_storage.update(session)
            except Exception as e:
                logger.error(f"Pipeline failed for session {session_id}: {e}")
                session.status = IdeaSessionStatus.FAILED
                session.errorMessage = str(e)
                session.endedAt = _utcnow()
                if session.trace:
                    session.trace.endedAt = _utcnow()
                return self.session_storage.update(session)
        finally:
            pipeline_lock.release()
    
    def _run_step(
        self,
        session: IdeaSession,
        step_name: str,
        step_func,
    ) -> IdeaSession:
        """Run a single pipeline step with tracing."""
        start_time = _utcnow()
        
        try:
            inputs, outputs, artifacts = step_func(session)
            
            end_time = _utcnow()
            duration = (end_time - start_time).total_seconds()
            
            step_result = StepResult(
                name=step_name,
                status="ok",
                inputs=inputs,
                outputs=outputs,
                artifacts=artifacts,
                startedAt=start_time,
                endedAt=end_time,
                durationSeconds=duration,
            )
            
            if session.trace:
                _record_step_result(session.trace, step_result)
            
            return self.session_storage.update(session)
            
        except Exception as e:
            end_time = _utcnow()
            duration = (end_time - start_time).total_seconds()
            failed_inputs = getattr(e, "step_inputs", {})
            failed_outputs = getattr(e, "step_outputs", {})
            failed_artifacts = getattr(e, "step_artifacts", [])
            
            step_result = StepResult(
                name=step_name,
                status="failed",
                inputs=failed_inputs if isinstance(failed_inputs, dict) else {},
                outputs=failed_outputs if isinstance(failed_outputs, dict) else {},
                artifacts=failed_artifacts if isinstance(failed_artifacts, list) else [],
                startedAt=start_time,
                endedAt=end_time,
                durationSeconds=duration,
                error=str(e),
            )
            
            if session.trace:
                _record_step_result(session.trace, step_result)
            
            self.session_storage.update(session)
            raise
    
    def _step_expand_query(self, session: IdeaSession) -> tuple:
        """Expand the seed query into search terms and build QueryPlan."""
        seed = session.config.seedQuery
        paper_type = session.config.paperType
        domain = session.config.domain or "general"

        # Build BFTS config from session settings
        search_budget = session.config.searchBudget or session.config.maxPapers
        bfts_config = BFTSConfig(maxLiteratureProbes=min(search_budget, 100))

        try:
            client = get_provider_client(session.config.providerName)

            user_prompt = prompts.EXPAND_QUERY_USER.format(
                seed_query=seed,
                paper_type=paper_type,
                domain=domain
            )
            is_cjk = bool(re.search(r'[\u4e00-\u9fff]', seed))
            if is_cjk:
                user_prompt += prompts.EXPAND_QUERY_CJK_SUFFIX

            messages = [
                ChatMessage(role="system", content=prompts.EXPAND_QUERY_SYSTEM),
                ChatMessage(role="user", content=user_prompt)
            ]

            response = client.chat(messages, model=session.config.model, max_tokens=500)

            # Parse JSON response. If the model wraps JSON in text/fences, recover
            # the object; if parsing still fails, use deterministic clean queries.
            data = _extract_json_object(response.text) or {}
            domain_terms = [
                term.strip()
                for term in domain.split(",")
                if term.strip() and domain != "general"
            ]
            combined_domain = " ".join(domain_terms[:3])
            raw_queries = (
                list(data.get("searchQueries", []) or [])
                + ([combined_domain] if combined_domain else [])
                + domain_terms
            )
            expanded_terms = _clean_query_terms(raw_queries, seed)
            english_search_queries: list[str] = []
            search_queries_by_role: Dict[str, List[str]] = {
                "domain": [], "task": [], "method": [], "evaluation": [],
            }
            translation_status = "not_required"
            translation_latency_ms = 0
            if is_cjk:
                english_search_queries = [
                    str(q).strip()
                    for q in data.get("englishSearchQueries", [])
                    if isinstance(q, str) and q.strip()
                ][:5]
                search_queries_by_role = self._cjk_query_roles(data)
                if not english_search_queries and any(search_queries_by_role.values()):
                    english_search_queries = list(dict.fromkeys(
                        query
                        for role in ("domain", "task", "method", "evaluation")
                        for query in search_queries_by_role[role]
                    ))[:6]
                if not any(search_queries_by_role.values()) and english_search_queries:
                    search_queries_by_role["domain"] = english_search_queries[:2]
                    search_queries_by_role["task"] = english_search_queries[2:4]
                if not english_search_queries:
                    search_queries_by_role, translation_latency_ms = self._translate_cjk_query_roles(
                        session,
                        client,
                    )
                    english_search_queries = list(dict.fromkeys(
                        query
                        for role in ("domain", "task", "method", "evaluation")
                        for query in search_queries_by_role[role]
                    ))[:6]
                    translation_status = "fallback" if english_search_queries else "missing"
                else:
                    translation_status = "primary"
                # Prepend English queries to expanded terms so they get
                # searched first against international databases
                if english_search_queries:
                    expanded_terms = english_search_queries + expanded_terms
            key_concepts = [
                str(item).strip()
                for item in data.get("keyConcepts", [])
                if isinstance(item, str) and item.strip()
            ][:10]
            refined_question = data.get("refinedQuestion", seed)
            if not isinstance(refined_question, str) or not refined_question.strip():
                refined_question = seed
            related_areas = [
                str(item).strip()
                for item in data.get("relatedAreas", [])
                if isinstance(item, str) and item.strip()
            ]
            raw_families = data.get("queryFamilies", [])
            path_templates = [
                str(item).strip()
                for item in data.get("pathTemplates", [])
                if isinstance(item, str) and item.strip()
            ]

            # Build QueryPlan
            query_families = []
            if raw_families:
                for fam in raw_families:
                    if isinstance(fam, dict):
                        family_queries = _clean_query_terms(fam.get("queries", []), seed, limit=3)
                        query_families.append(QueryFamily(
                            name=fam.get("name", "core"),
                            queries=family_queries,
                            keyConcepts=fam.get("keyConcepts", []),
                        ))
            else:
                # Auto-create families from related areas
                if related_areas:
                    for area in related_areas:
                        query_families.append(QueryFamily(
                            name=area.lower().replace(" ", "_"),
                            queries=[f"{seed} {area}"],
                            keyConcepts=[],
                        ))
                # Always have a core family
                query_families.insert(0, QueryFamily(
                    name="core",
                    queries=expanded_terms[:3],
                    keyConcepts=key_concepts[:5],
                ))

            query_plan = QueryPlan(
                refinedQuestion=refined_question,
                queryFamilies=query_families,
                expandedTerms=expanded_terms[:5],
                keyConcepts=key_concepts[:10],
                pathTemplates=path_templates,
                bftsConfig=bfts_config,
            )

            inputs = {"seedQuery": seed, "paperType": paper_type}
            outputs = {
                "refinedQuestion": refined_question,
                "expandedTerms": expanded_terms[:5],
                "keyConcepts": key_concepts[:10],
                "queryPlan": query_plan.model_dump(),
                "llmLatencyMs": response.latency_ms,
                "englishSearchQueries": english_search_queries if is_cjk else [],
                "searchQueriesByRole": search_queries_by_role,
                "translationStatus": translation_status,
                "translationLatencyMs": translation_latency_ms,
            }

        except Exception as e:
            logger.warning(f"LLM query expansion failed: {e}, using fallback")
            expanded_terms = [seed]
            if domain != "general" and domain:
                expanded_terms.append(f"{seed} {domain}")

            # CJK fallback: attempt a lightweight translation call so that
            # international databases can still be searched. If this also
            # fails, the seed is used as-is (OpenAlex may still find results).
            is_cjk = bool(re.search(r'[\u4e00-\u9fff]', seed))
            english_search_queries: list[str] = []
            if is_cjk:
                try:
                    client = get_provider_client(session.config.providerName)
                    search_queries_by_role, translation_latency_ms = self._translate_cjk_query_roles(
                        session,
                        client,
                    )
                    english_search_queries = list(dict.fromkeys(
                        query
                        for role in ("domain", "task", "method", "evaluation")
                        for query in search_queries_by_role[role]
                    ))[:6]
                    if english_search_queries:
                        expanded_terms = english_search_queries + expanded_terms
                        logger.info(f"CJK fallback translation succeeded: {english_search_queries}")
                except Exception as trans_e:
                    logger.warning(f"CJK fallback translation also failed: {trans_e}")
                    search_queries_by_role = {
                        "domain": [], "task": [], "method": [], "evaluation": [],
                    }
                    translation_latency_ms = 0

            query_plan = QueryPlan(
                refinedQuestion=seed,
                queryFamilies=[
                    QueryFamily(
                        name="core",
                        queries=expanded_terms[:3],
                        keyConcepts=[],
                    )
                ],
                expandedTerms=expanded_terms,
                keyConcepts=[],
                bftsConfig=bfts_config,
            )

            inputs = {"seedQuery": seed}
            outputs = {
                "expandedTerms": expanded_terms,
                "queryPlan": query_plan.model_dump(),
                "error": str(e),
                "cjkTranslationApplied": bool(english_search_queries),
                "englishSearchQueries": english_search_queries,
                "searchQueriesByRole": search_queries_by_role if is_cjk else {},
                "translationStatus": (
                    "fallback" if english_search_queries else "missing"
                ) if is_cjk else "not_required",
                "translationLatencyMs": translation_latency_ms if is_cjk else 0,
            }

        if re.search(r"[\u4e00-\u9fff]", seed) and not outputs.get("englishSearchQueries"):
            raise AwaitingTranslationError(
                "CJK query expansion did not produce usable English academic queries",
                inputs=inputs,
                outputs=outputs,
            )

        return inputs, outputs, []
    
    def _step_literature_search(self, session: IdeaSession) -> tuple:
        """Search for literature and build LiteratureGraph v0.

        Uses multi-source search (Semantic Scholar, arXiv, local corpus),
        deduplicates by doi > arxivId > semanticScholarId > title hash,
        creates RawPaper[] + LiteratureGraph v0.
        Also creates LiteratureItem[] for backward compatibility.
        """
        seed = session.config.seedQuery
        max_papers = session.config.maxPapers

        role_queries = self._get_step_output(
            session,
            "expandQuery",
            "searchQueriesByRole",
            {},
        ) or {}
        query_specs: List[tuple[str, str]] = []
        if isinstance(role_queries, dict):
            for role in ("domain", "task", "method", "evaluation"):
                for query in _as_string_list(role_queries.get(role, []), limit=2):
                    if all(existing_query != query for _, existing_query in query_specs):
                        query_specs.append((role, query))
        if not query_specs:
            expanded = _as_string_list(
                self._get_step_output(session, "expandQuery", "expandedTerms", []),
                limit=3,
            )
            query_specs = [("core", query) for query in (expanded or [seed])]
        search_queries = [query for _, query in query_specs]
        core_queries = self._core_search_queries(session)
        profile = build_topic_intent_profile(
            seed=seed,
            domain=session.config.domain or "",
            role_queries=role_queries if isinstance(role_queries, dict) else {},
        )
        must_cite_refs = [
            str(value).lower().strip()
            for value in (session.config.mustCiteList or [])
            if str(value).strip()
        ]

        # Search across sources
        search_service = get_search_service()
        all_results: List[SearchResult] = []

        per_query_limit = max(5, min(20, max_papers // max(1, len(query_specs))))
        query_result_counts: Dict[str, int] = {}
        for role, query in query_specs:
            try:
                results = search_service.search(query, limit=per_query_limit)
                for result in results:
                    if role not in result.retrieval_roles:
                        result.retrieval_roles.append(role)
                    if query not in result.matched_queries:
                        result.matched_queries.append(query)
                all_results.extend(results)
                query_result_counts[f"{role}:{query}"] = len(results)
                logger.info(f"Search for '{query}' returned {len(results)} results")
            except Exception as e:
                query_result_counts[f"{role}:{query}"] = 0
                logger.warning(f"Search failed for '{query}': {e}")

        def _matches_must_cite(result: SearchResult) -> bool:
            haystack = " ".join(
                str(value)
                for value in [result.doi, result.arxiv_id, result.url, result.title]
                if value
            ).lower()
            return any(reference in haystack for reference in must_cite_refs)

        def _dedupe_assess_rank(results: List[SearchResult]) -> tuple:
            dedupe = deduplicate_search_results(results)
            persistable: List[SearchResult] = []
            gate_eligible: List[SearchResult] = []
            rejected: List[SearchResult] = []
            for result in dedupe.results:
                assessment = assess_search_result(result, profile)
                result.evidence_tier = assessment.tier.value
                result.decisive_anchors = list(assessment.decisive_anchors)
                result.relevance_components = dict(assessment.score_components)
                result.rejection_reason = assessment.rejection_reason
                result.relevance_score = assessment.score
                if assessment.tier is not EvidenceTier.REJECTED:
                    persistable.append(result)
                    gate_eligible.append(result)
                else:
                    result.must_cite_override = _matches_must_cite(result)
                    rejected.append(result)
                    if result.must_cite_override:
                        persistable.append(result)
            persistable.sort(key=lambda item: item.relevance_score, reverse=True)
            gate_eligible.sort(key=lambda item: item.relevance_score, reverse=True)
            return (
                persistable,
                gate_eligible,
                rejected,
                dedupe.merge_count,
                len(dedupe.results),
            )

        (
            unique_results,
            gate_eligible_results,
            rejected_results,
            duplicate_merge_count,
            ranked_count,
        ) = _dedupe_assess_rank(all_results)
        filtered_out_count = len([
            result for result in rejected_results if not result.must_cite_override
        ])
        raw_quality_gate = _evaluate_paper_quality_gate(
            seed=seed,
            domain=session.config.domain or "",
            papers=gate_eligible_results,
            stage="literatureSearch.initial",
            extra_terms=core_queries,
            paper_type=session.config.paperType,
        )
        repair_queries: List[str] = []
        repair_attempted = False
        if not raw_quality_gate["passed"]:
            repair_attempted = True
            repair_queries = self._build_literature_repair_queries(
                session,
                raw_quality_gate,
                existing_queries=search_queries,
            )
            for query in repair_queries:
                try:
                    results = search_service.search(query, limit=max(8, max_papers // max(1, len(repair_queries))))
                    _tag_repair_results(results, query)
                    all_results.extend(results)
                    query_result_counts[f"repair:{query}"] = len(results)
                    logger.info(f"Repair search for '{query}' returned {len(results)} results")
                except Exception as e:
                    query_result_counts[f"repair:{query}"] = 0
                    logger.warning(f"Repair search failed for '{query}': {e}")
            (
                unique_results,
                gate_eligible_results,
                rejected_results,
                duplicate_merge_count,
                ranked_count,
            ) = _dedupe_assess_rank(all_results)
            filtered_out_count = len([
                result for result in rejected_results if not result.must_cite_override
            ])
            raw_quality_gate = _evaluate_paper_quality_gate(
                seed=seed,
                domain=session.config.domain or "",
                papers=gate_eligible_results,
                stage="literatureSearch.repaired",
                extra_terms=core_queries,
                paper_type=session.config.paperType,
            )

        if not raw_quality_gate.get("passed", False):
            diagnostic_outputs = {
                "searchQueries": search_queries,
                "coreSearchQueries": core_queries,
                "searchQueriesByRole": role_queries,
                "queryResultCounts": query_result_counts,
                "topicIntentProfile": profile.to_dict(),
                "resultCountBeforeDedup": len(all_results),
                "uniqueResultCount": ranked_count,
                "duplicateMergeCount": duplicate_merge_count,
                "evidenceTierCounts": {
                    "direct": sum(
                        result.evidence_tier == "direct" for result in unique_results
                    ),
                    "transferable": sum(
                        result.evidence_tier == "transferable" for result in unique_results
                    ),
                    "rejected": len(rejected_results),
                },
                "rejectionReasonCounts": dict(Counter(
                    result.rejection_reason for result in rejected_results
                )),
                "filteredOutCount": filtered_out_count,
                "paperQualityGate": raw_quality_gate,
                "repairAttempted": repair_attempted,
                "repairQueries": repair_queries,
            }
            errors = "; ".join(raw_quality_gate.get("errors", [])[:4])
            raise AwaitingLiteratureEvidenceError(
                f"Literature evidence is insufficient before deep reading: {errors}",
                inputs={
                    "seedQuery": seed,
                    "maxPapers": max_papers,
                    "searchQueries": search_queries,
                },
                outputs=diagnostic_outputs,
            )

        if ranked_count and not unique_results:
            logger.warning(
                "All literature search results were filtered out as low relevance for seed '%s'",
                seed,
            )

        # Limit results
        unique_results = unique_results[:max_papers]
        sources_used: List[str] = []
        for result in unique_results:
            for source in result.retrieval_sources or [result.source]:
                if source and source not in sources_used:
                    sources_used.append(source)

        # Create RawPaper objects
        raw_papers: List[RawPaper] = []
        literature_items: List[LiteratureItem] = []
        literature_ids: List[str] = []

        for i, result in enumerate(unique_results):
            base_score = result.relevance_score if result.relevance_score > 0 else (1.0 - (i * 0.05))
            title_hash = _compute_title_hash(result.title)

            # Extract Semantic Scholar ID
            s2_id = None
            if result.source == "semantic_scholar" and result.url:
                s2_match = re.search(r'SemanticScholarID:(\w+)', result.url)
                if s2_match:
                    s2_id = s2_match.group(1)

            raw_paper = RawPaper(
                id=generate_raw_paper_id(),
                sessionId=session.id,
                title=result.title,
                authors=result.authors,
                year=result.year,
                venue=result.venue,
                url=result.url,
                doi=result.doi,
                arxivId=result.arxiv_id,
                semanticScholarId=s2_id,
                citationCount=result.citation_count or 0,
                abstract=result.abstract or "",
                source=list(result.retrieval_sources or ([result.source] if result.source else [])),
                retrievalRoles=list(result.retrieval_roles),
                matchedQueries=list(result.matched_queries),
                evidenceTier=result.evidence_tier,
                decisiveAnchors=list(result.decisive_anchors),
                relevanceComponents=dict(result.relevance_components),
                rejectionReason=result.rejection_reason,
                mustCiteOverride=result.must_cite_override,
                normalizedTitleHash=title_hash,
                relevanceScore=min(1.0, max(0.0, base_score)),
            )
            self.raw_paper_storage.create(raw_paper)
            raw_papers.append(raw_paper)

            # Also create LiteratureItem for backward compatibility
            lit_item = LiteratureItem(
                id=generate_literature_id(),
                sessionId=session.id,
                title=result.title,
                authors=result.authors,
                venue=result.venue,
                year=result.year,
                url=result.url,
                doi=result.doi,
                arxivId=result.arxiv_id,
                snippet=(result.abstract or "")[:500],
                relevanceScore=min(1.0, max(0.0, base_score)),
                source=result.source,
            )
            self.literature_storage.create(lit_item)
            literature_items.append(lit_item)
            literature_ids.append(lit_item.id)

        # Build LiteratureGraph v0
        graph = self.graph_builder.build_graph_v0(
            session_id=session.id,
            raw_papers=raw_papers,
        )
        self.graph_storage.create(graph)

        retrieval_role_counts = {
            role: sum(1 for result in unique_results if role in result.retrieval_roles)
            for role in ("domain", "task", "method", "evaluation", "repair")
        }
        inputs = {"seedQuery": seed, "maxPapers": max_papers, "searchQueries": search_queries}
        outputs = {
            "paperCount": len(raw_papers),
            "rawPaperIds": [p.id for p in raw_papers],
            "graphId": graph.id,
            "sourcesUsed": sources_used,
            "searchQueries": search_queries,
            "coreSearchQueries": core_queries,
            "searchQueriesByRole": role_queries,
            "queryResultCounts": query_result_counts,
            "retrievalRoleCounts": retrieval_role_counts,
            "topicIntentProfile": profile.to_dict(),
            "resultCountBeforeDedup": len(all_results),
            "uniqueResultCount": ranked_count,
            "duplicateMergeCount": duplicate_merge_count,
            "evidenceTierCounts": {
                "direct": sum(result.evidence_tier == "direct" for result in unique_results),
                "transferable": sum(result.evidence_tier == "transferable" for result in unique_results),
                "rejected": len(rejected_results),
            },
            "rejectionReasonCounts": dict(Counter(
                result.rejection_reason for result in rejected_results
            )),
            "filteredOutCount": filtered_out_count,
            "minExternalRelevance": float(os.getenv("FAROS_MIN_EXTERNAL_RELEVANCE", "0.12")),
            "minLocalRelevance": float(os.getenv("FAROS_MIN_LOCAL_RELEVANCE", "0.28")),
            "paperQualityGate": raw_quality_gate,
            "repairAttempted": repair_attempted,
            "repairQueries": repair_queries,
            # Backward-compat fields
            "paperIds": literature_ids,
        }

        return inputs, outputs, []
    
    def _step_novelty_check(
        self,
        session: IdeaSession,
        forced_raw_paper_ids: Optional[List[str]] = None,
    ) -> tuple:
        """Check novelty: cluster papers, select for deep reading, extract structured info.

        1. Load LiteratureGraph v0 from storage
        2. Cluster papers and select by role distribution
        3. Deep-read selected papers (LLM structured extraction)
        4. Build LiteratureMap
        5. Upgrade graph to v1
        6. Create preliminary BFTSHandoff
        """
        seed = session.config.seedQuery
        paper_type = session.config.paperType
        literature = self.get_literature(session.id)

        # Load LiteratureGraph v0 from storage
        graph = self.graph_storage.get_by_session(session.id)
        if not graph:
            logger.warning("No LiteratureGraph v0 found for session %s, using fallback", session.id)
            return self._fallback_novelty_check(session, literature, seed)

        # Get raw papers
        raw_papers = self.raw_paper_storage.list_by_session(session.id)

        # Load QueryPlan for must-cite list
        must_cite_list = session.config.mustCiteList

        # Step 3a: Cluster papers
        graph = self.graph_builder.cluster_papers(graph)

        # Step 3b: Select papers by role
        num_select = min(_deep_read_max_papers(), len(raw_papers))
        graph, selected_paper_ids = self.graph_builder.select_papers(
            graph, num_select=num_select, must_cite_list=must_cite_list
        )
        raw_by_id = {paper.id: paper for paper in raw_papers}
        selected_paper_ids = _limit_deep_read_selection(
            selected_paper_ids,
            raw_by_id,
            limit=num_select,
        )
        selected_set = set(selected_paper_ids)
        graph = graph.model_copy(update={
            "nodes": [
                node.model_copy(update={"isSelected": node.paperId in selected_set})
                for node in graph.nodes
            ]
        })
        forced_selected_ids: List[str] = []
        if forced_raw_paper_ids:
            topic_terms = _topic_terms_from_seed(
                seed,
                session.config.domain or "",
                self._core_search_queries(session),
            )
            forced_candidates = [
                paper for paper_id in forced_raw_paper_ids
                if (paper := raw_by_id.get(paper_id)) is not None
            ]
            forced_candidates.sort(
                key=lambda paper: _paper_alignment_score(paper, topic_terms),
                reverse=True,
            )
            forced_cap = min(len(forced_candidates), min(12, max(4, num_select // 4)))
            forced_selected_ids = [paper.id for paper in forced_candidates[:forced_cap]]
            forced_set = set(forced_selected_ids)

            deduped_selected: List[str] = []
            for paper_id in selected_paper_ids:
                if paper_id not in deduped_selected:
                    deduped_selected.append(paper_id)

            base_slots = max(0, num_select - len(forced_selected_ids))
            selected_paper_ids = [
                paper_id for paper_id in deduped_selected
                if paper_id not in forced_set
            ][:base_slots]
            for paper_id in forced_selected_ids:
                if paper_id not in selected_paper_ids:
                    selected_paper_ids.append(paper_id)

            if len(selected_paper_ids) < num_select:
                for paper_id in deduped_selected:
                    if paper_id not in selected_paper_ids:
                        selected_paper_ids.append(paper_id)
                    if len(selected_paper_ids) >= num_select:
                        break

            selected_paper_ids = selected_paper_ids[:num_select]
            selected_set = set(selected_paper_ids)
            graph = graph.model_copy(update={
                "nodes": [
                    node.model_copy(update={
                        "role": (
                            "repair_focus"
                            if node.paperId in forced_set
                            else node.role
                        ),
                        "isSelected": node.paperId in selected_set,
                        "metadata": {
                            **node.metadata,
                            **({"repairForced": True} if node.paperId in forced_set else {}),
                        },
                    })
                    for node in graph.nodes
                ]
            })
        selected_raw = [paper for paper in raw_papers if paper.id in selected_paper_ids]
        selected_quality_gate = _evaluate_paper_quality_gate(
            seed=seed,
            domain=session.config.domain or "",
            papers=selected_raw,
            stage="noveltyCheck.selectedRaw",
            extra_terms=self._core_search_queries(session),
            paper_type=paper_type,
        )
        if not selected_quality_gate["passed"] and raw_papers:
            topic_terms = _topic_terms_from_seed(
                seed,
                session.config.domain or "",
                self._core_search_queries(session),
            )
            aligned_raw = sorted(
                raw_papers,
                key=lambda paper: _paper_alignment_score(paper, topic_terms),
                reverse=True,
            )
            for paper in aligned_raw[:num_select]:
                if paper.id not in selected_paper_ids:
                    selected_paper_ids.append(paper.id)
                if len(selected_paper_ids) >= num_select:
                    break
            selected_raw = [paper for paper in raw_papers if paper.id in selected_paper_ids]
            selected_quality_gate = _evaluate_paper_quality_gate(
                seed=seed,
                domain=session.config.domain or "",
                papers=selected_raw,
                stage="noveltyCheck.selectedRaw.repaired",
                extra_terms=self._core_search_queries(session),
                paper_type=paper_type,
            )

        # Step 3c: Deep-read only papers that are not already structured.
        raw_by_id = {paper.id: paper for paper in raw_papers}
        cached_structured: Dict[str, StructuredPaper] = {}
        session_structured_cache_hit_count = 0
        global_structured_cache_hit_count = 0
        missing_paper_ids: List[str] = []
        for paper_id in selected_paper_ids:
            cached = None
            try:
                cached = self.structured_storage.get(paper_id)
            except Exception as e:
                logger.warning("Structured paper cache lookup failed for %s: %s", paper_id, e)
            if cached and cached.sessionId == session.id:
                cached_structured[paper_id] = cached
                session_structured_cache_hit_count += 1
            else:
                raw_paper = raw_by_id.get(paper_id)
                global_cached = (
                    self._load_trusted_structured_paper_cache(
                        session=session,
                        raw_paper=raw_paper,
                    )
                    if raw_paper
                    else None
                )
                if global_cached:
                    cached_structured[paper_id] = global_cached
                    global_structured_cache_hit_count += 1
                    try:
                        if not self.structured_storage.get(global_cached.id):
                            self.structured_storage.create(global_cached)
                    except Exception as e:
                        logger.warning("Failed to persist global structured cache hit %s: %s", paper_id, e)
                else:
                    missing_paper_ids.append(paper_id)

        new_structured_papers: List[StructuredPaper] = []
        if missing_paper_ids:
            new_structured_papers = self.deep_reader.extract_structured_papers(
                session=session,
                selected_paper_ids=missing_paper_ids,
                raw_papers=raw_papers,
            )
        structured_cache_store_count = 0
        for sp in new_structured_papers:
            try:
                if not self.structured_storage.get(sp.id):
                    self.structured_storage.create(sp)
            except Exception as e:
                logger.warning(f"Failed to persist structured paper {sp.id}: {e}")
            raw_paper = raw_by_id.get(sp.rawPaperId or sp.id)
            if raw_paper and self._store_trusted_structured_paper_cache(
                session=session,
                raw_paper=raw_paper,
                structured_paper=sp,
            ):
                structured_cache_store_count += 1
        structured_by_id = {
            **cached_structured,
            **{sp.id: sp for sp in new_structured_papers},
            **{sp.rawPaperId: sp for sp in new_structured_papers if sp.rawPaperId},
        }
        structured_papers = [
            structured_by_id[paper_id]
            for paper_id in selected_paper_ids
            if paper_id in structured_by_id
        ]
        structured_quality_gate = _evaluate_paper_quality_gate(
            seed=seed,
            domain=session.config.domain or "",
            papers=structured_papers,
            stage="noveltyCheck.structured",
            extra_terms=self._core_search_queries(session),
            paper_type=paper_type,
        )

        # Step 3d: Build LiteratureMap
        literature_map = self.deep_reader.build_literature_map(
            session_id=session.id,
            selected_paper_ids=selected_paper_ids,
            structured_papers=structured_papers,
            graph=graph,
        )
        self.map_storage.create(literature_map)

        # Step 3e: Upgrade graph to v1
        graph = self.graph_builder.upgrade_to_v1(graph)
        self.graph_storage.update(graph)

        # Step 3f: Create preliminary BFTSHandoff
        query_plan_dict = self._get_step_output(session, "expandQuery", "queryPlan")
        bfts_config = BFTSConfig()
        if query_plan_dict:
            try:
                bfts_config = BFTSConfig(**query_plan_dict.get("bftsConfig", {}))
            except Exception:
                pass

        handoff = BFTSHandoff(
            id=generate_handoff_id(),
            sessionId=session.id,
            reasoningKgId=None,  # Phase 2
            literatureMapId=literature_map.id,
            pathSeedIds=[],  # Phase 2
            selectedPaperIds=selected_paper_ids,
            bftsConfig=bfts_config,
        )
        self.handoff_storage.create(handoff)

        # Also run the original LLM novelty check for backward compatibility
        if structured_papers:
            lit_summary = "\n".join([
                (
                    f"- {sp.title} ({sp.year or 'N/A'}): {sp.summary[:180] or sp.abstract[:180]}\n"
                    f"  limitations: {'; '.join(sp.limitations[:2]) or 'N/A'}\n"
                    f"  openQuestions: {'; '.join(sp.openQuestions[:2]) or 'N/A'}\n"
                    f"  failedAssumptions: {'; '.join(sp.failedAssumptions[:2]) or 'N/A'}\n"
                    f"  methodWeaknesses: {'; '.join(sp.methodWeaknesses[:2]) or 'N/A'}\n"
                    f"  missingEvaluation: {'; '.join(sp.missingEvaluation[:2]) or 'N/A'}\n"
                    f"  baselines/metrics: {'; '.join([*sp.baselineMethods[:2], *sp.recommendedMetrics[:2]]) or 'N/A'}"
                )
                for sp in structured_papers[:8]
            ])
        else:
            lit_summary = "\n".join([
                f"- {item.title} ({item.year or 'N/A'}): {item.snippet[:150]}..."
                for item in literature[:8]
            ])

        covered_areas: List[str] = []
        gaps: List[str] = []
        novel_directions: List[str] = []
        assessment = ""

        try:
            client = get_provider_client(session.config.providerName)
            user_prompt = prompts.NOVELTY_CHECK_USER.format(
                seed_query=seed,
                paper_type=paper_type,
                literature_summary=lit_summary
            )
            messages = [
                ChatMessage(role="system", content=prompts.NOVELTY_CHECK_SYSTEM),
                ChatMessage(role="user", content=user_prompt)
            ]
            response = client.chat(messages, model=session.config.model, max_tokens=800)
            try:
                data = json.loads(response.text)
                covered_areas = data.get("coveredAreas", [])
                gaps = data.get("gaps", [])
                novel_directions = data.get("novelDirections", [])
                assessment = data.get("noveltyAssessment", "")
            except json.JSONDecodeError:
                for line in response.text.split("\n"):
                    line = line.strip()
                    if "gap" in line.lower() or "missing" in line.lower():
                        gaps.append(line.strip("-").strip())
                    elif "covered" in line.lower() or "existing" in line.lower():
                        covered_areas.append(line.strip("-").strip())
                assessment = response.text[:300]
        except Exception as e:
            logger.warning(f"LLM novelty check failed: {e}")
            covered_topics = set()
            for item in literature:
                words = item.title.lower().split()
                covered_topics.update(w for w in words if len(w) > 4)
            covered_areas = list(covered_topics)[:15]
            gaps = [
                f"Scalability of {seed} methods",
                f"Interpretability in {seed}",
                f"Theoretical foundations of {seed}",
            ]
            novel_directions = [
                f"Novel architectures for {seed}",
                f"Efficient training methods for {seed}",
            ]

        inputs = {
            "literatureCount": len(literature),
            "topic": seed,
            "forcedRawPaperIds": forced_raw_paper_ids or [],
        }
        outputs = {
            # New dual-graph outputs
            "graphId": graph.id,
            "graphVersion": graph.version,
            "selectedPaperIds": selected_paper_ids,
            "forcedRepairPaperIds": forced_selected_ids,
            "forcedRepairPaperCount": len(forced_selected_ids),
            "structuredPaperCount": len(structured_papers),
            "structuredCacheHitCount": len(cached_structured),
            "structuredSessionCacheHitCount": session_structured_cache_hit_count,
            "structuredGlobalCacheHitCount": global_structured_cache_hit_count,
            "structuredCacheStoredCount": structured_cache_store_count,
            "deepReadRequestedCount": len(missing_paper_ids),
            "selectedPaperQualityGate": selected_quality_gate,
            "structuredPaperQualityGate": structured_quality_gate,
            "literatureMapId": literature_map.id,
            "handoffId": handoff.id,
            "clusterCount": len(graph.clusters),
            # Backward-compat outputs for Step 4
            "coveredAreas": covered_areas[:10],
            "gaps": gaps[:5],
            "novelDirections": novel_directions[:5],
            "noveltyAssessment": assessment,
        }

        return inputs, outputs, []

    def _fallback_novelty_check(self, session: IdeaSession, literature: List[LiteratureItem], seed: str) -> tuple:
        """Fallback novelty check when Graph v0 is unavailable."""
        covered_topics = set()
        for item in literature:
            words = item.title.lower().split()
            covered_topics.update(w for w in words if len(w) > 4)

        inputs = {"literatureCount": len(literature)}
        outputs = {
            "coveredAreas": list(covered_topics)[:15],
            "gaps": [
                f"Scalability of {seed} methods",
                f"Interpretability in {seed}",
                f"Theoretical foundations of {seed}",
                f"Real-world deployment of {seed}",
            ],
            "novelDirections": [],
            "noveltyAssessment": f"Fallback novelty assessment for {seed}: literature corpus contains {len(literature)} papers covering {len(covered_topics)} topic areas.",
            "graphId": None,
            "graphVersion": 0,
            "selectedPaperIds": [],
            "structuredPaperCount": 0,
        }
        return inputs, outputs, []
    
    def _step_gap_analysis(self, session: IdeaSession) -> tuple:
        """Build ReasoningKG (Graph 2), link Graph 1 signals, generate path seeds.

        Contract: reads only Step 3 outputs (StructuredPaper[] + LiteratureMap).
        Does NOT read RawPaper[] or LiteratureGraph directly.
        """
        seed = session.config.seedQuery
        paper_type = session.config.paperType
        literature = self.get_literature(session.id)

        # Load Step 3 outputs
        literature_map = self.map_storage.get_by_session(session.id)
        structured_papers = self.structured_storage.list_by_session(session.id)

        if not literature_map or not structured_papers:
            logger.warning(
                "Step 3 outputs not available for session %s, using fallback gap analysis",
                session.id,
            )
            return self._fallback_gap_analysis(session, seed, literature)

        try:
            # Step 4a: Build ReasoningKG
            reasoning_kg = self.reasoning_builder.build_reasoning_kg(
                session=session,
                structured_papers=structured_papers,
                literature_map=literature_map,
            )
            self.reasoning_kg_storage.create(reasoning_kg)

            # Step 4b: Link Graph 1 signals to Graph 2 entities/relations
            evidence_links = self.graph_linker.link_graphs(
                literature_map=literature_map,
                reasoning_kg=reasoning_kg,
            )
            for link in evidence_links:
                self.evidence_link_storage.create(link)

            # Step 4c: Generate reasoning path seeds
            path_seeds = self.path_seed_gen.generate_seeds(
                session_id=session.id,
                reasoning_kg=reasoning_kg,
                evidence_links=evidence_links,
                structured_papers=structured_papers,
                literature_map=literature_map,
                seed_query=seed,
            )
            for path_seed in path_seeds:
                self.path_seed_storage.create(path_seed)

            # Step 4d: Update BFTSHandoff with Phase 2 data
            existing_handoff = self.handoff_storage.get_by_session(session.id)
            if existing_handoff:
                # Delete preliminary handoff, create final version with new ID
                self.handoff_storage.delete(existing_handoff.id)
            final_handoff = BFTSHandoff(
                id=generate_handoff_id(),
                sessionId=session.id,
                reasoningKgId=reasoning_kg.id,
                literatureMapId=literature_map.id,
                pathSeedIds=[s.seedId for s in path_seeds],
                selectedPaperIds=(existing_handoff.selectedPaperIds if existing_handoff
                                  else literature_map.selectedPaperIds),
                bftsConfig=existing_handoff.bftsConfig if existing_handoff else BFTSConfig(),
            )
            self.handoff_storage.create(final_handoff)

        except Exception as e:
            logger.error(f"Dual-graph Step 4 failed: {e}, using fallback")
            return self._fallback_gap_analysis(session, seed, literature)

        # Also run LLM gap analysis for backward-compat outputs
        novelty_assessment = self._get_step_output(session, "noveltyCheck", "noveltyAssessment", "")
        gaps_from_novelty = self._get_step_output(session, "noveltyCheck", "gaps", [])

        lit_summary = "\n".join([
            f"- {item.title} ({item.year or 'N/A'}): {item.snippet[:150]}..."
            for item in literature[:8]
        ])
        gaps_text = "\n".join([f"- {g}" for g in gaps_from_novelty[:5]])

        gap_analysis = []
        prioritized_gaps = []
        opportunities = []

        try:
            client = get_provider_client(session.config.providerName)
            user_prompt = prompts.GAP_ANALYSIS_USER.format(
                seed_query=seed,
                paper_type=paper_type,
                literature_summary=lit_summary,
                novelty_assessment=novelty_assessment or "Not available",
                gaps=gaps_text or "None identified yet",
            )
            messages = [
                ChatMessage(role="system", content=prompts.GAP_ANALYSIS_SYSTEM),
                ChatMessage(role="user", content=user_prompt),
            ]
            response = client.chat(messages, model=session.config.model, max_tokens=1000)
            try:
                data = json.loads(response.text)
                gap_analysis = data.get("gapAnalysis", [])
                prioritized_gaps = data.get("prioritizedGaps", [])
                opportunities = data.get("researchOpportunities", [])
            except json.JSONDecodeError:
                for line in response.text.split("\n"):
                    line = line.strip()
                    if line.startswith("-") or line.startswith("*"):
                        content = line.strip("-*").strip()
                        if "opportunity" in line.lower():
                            opportunities.append(content)
                        else:
                            prioritized_gaps.append(content)
        except Exception as e:
            logger.warning(f"LLM gap analysis failed: {e}")

        gap_analysis, prioritized_gaps, opportunities = _ensure_gap_outputs(
            gap_analysis=gap_analysis,
            prioritized_gaps=prioritized_gaps,
            opportunities=opportunities,
            novelty_gaps=gaps_from_novelty,
            literature_map=literature_map,
            seed_query=seed,
        )

        inputs = {"topic": seed, "literatureCount": len(literature)}
        outputs = {
            # Phase 2 outputs
            "reasoningKgId": reasoning_kg.id,
            "evidenceLinkCount": len(evidence_links),
            "pathSeedIds": [s.seedId for s in path_seeds],
            "pathSeedCount": len(path_seeds),
            # Backward-compat outputs
            "gapAnalysis": gap_analysis[:5],
            "prioritizedGaps": prioritized_gaps[:5],
            "researchOpportunities": opportunities[:5],
        }

        return inputs, outputs, []

    def _fallback_gap_analysis(self, session: IdeaSession, seed: str, literature) -> tuple:
        """Fallback gap analysis when Step 3 outputs are unavailable."""
        gaps_from_novelty = self._get_step_output(session, "noveltyCheck", "gaps", [])
        inputs = {"topic": seed}
        outputs = {
            "reasoningKgId": None,
            "evidenceLinkCount": 0,
            "pathSeedIds": [],
            "pathSeedCount": 0,
            "gapAnalysis": [],
            "prioritizedGaps": gaps_from_novelty[:5] if gaps_from_novelty else [
                f"Scalability of {seed} methods",
                f"Interpretability in {seed}",
                f"Theoretical foundations of {seed}",
            ],
            "researchOpportunities": [
                f"Novel architectures for {seed}",
                f"Efficient training methods for {seed}",
            ],
        }
        return inputs, outputs, []

    def _step_idea_brainstorm_bfts(self, session: IdeaSession) -> tuple:
        """Generate candidate ideas using BFTS tree search + reflection loop.

        Replaces the single-shot LLM call with:
          1. Load BFTSHandoff from Step 4
          2. Initialize BFTSSearchTree with path seeds
          3. Run tree search (each node gets reflection loop)
          4. Convert terminal nodes to IdeaCandidate[]

        Falls back to _step_idea_brainstorm_legacy() when BFTSHandoff is unavailable.
        """
        seed = session.config.seedQuery
        paper_type = session.config.paperType
        max_candidates = session.config.maxCandidates

        # Try to load BFTSHandoff (output of Step 4)
        handoff = None
        try:
            handoff = self.handoff_storage.get_by_session(session.id)
        except Exception as e:
            logger.warning(f"BFTSHandoff not available: {e}, using legacy brainstorm")

        if not handoff:
            logger.info("No BFTSHandoff found, falling back to legacy brainstorm")
            return self._step_idea_brainstorm_legacy(session)

        # Load BFTS config
        bfts_config = handoff.bftsConfig or BFTSConfig(
            maxNodes=min(20, max(10, max_candidates * 2)),
            beamWidth=min(2, max(1, max_candidates // 2)),
            maxReflectionRounds=2,
        )

        # Override with conservative defaults (user chose conservative)
        bfts_config = BFTSConfig(
            maxNodes=min(bfts_config.maxNodes, 20),
            maxIterations=min(bfts_config.maxIterations, 4),
            beamWidth=max(1, min(bfts_config.beamWidth, 2)),
            expansionWidth=max(1, min(bfts_config.expansionWidth, 2)),
            maxLiteratureProbes=min(bfts_config.maxLiteratureProbes, 12),
            maxReflectionRounds=max(1, min(bfts_config.maxReflectionRounds, 2)),
            minEvidenceSupport=bfts_config.minEvidenceSupport,
            minGraphGrounding=bfts_config.minGraphGrounding,
            pruneDuplicateThreshold=bfts_config.pruneDuplicateThreshold,
            scoreWeights=bfts_config.scoreWeights,
        )

        # Load path seeds
        path_seeds: List[ReasoningPathSeed] = []
        if handoff.pathSeedIds:
            for sid in handoff.pathSeedIds[:bfts_config.beamWidth]:
                try:
                    seed_obj = self.path_seed_storage.get(sid)
                    if seed_obj:
                        path_seeds.append(seed_obj)
                except Exception:
                    pass

        if not path_seeds:
            logger.info("No path seeds available, falling back to legacy brainstorm")
            return self._step_idea_brainstorm_legacy(session)

        # Load structured papers for literature context
        structured_papers: List[StructuredPaper] = []
        try:
            structured_papers = self.structured_storage.list_by_session(session.id)
        except Exception:
            pass

        # Build literature context string
        literature_context = self._build_bfts_literature_context(structured_papers)

        logger.info(
            f"BFTS: {len(path_seeds)} seeds, "
            f"maxNodes={bfts_config.maxNodes}, "
            f"beamWidth={bfts_config.beamWidth}, "
            f"maxReflectionRounds={bfts_config.maxReflectionRounds}"
        )

        gap_analysis = self._get_step_output(session, "gapAnalysis", "gapAnalysis", [])
        prioritized_gaps = self._get_step_output(session, "gapAnalysis", "prioritizedGaps", [])
        opportunities = self._get_step_output(session, "gapAnalysis", "researchOpportunities", [])
        gap_text = (
            json.dumps(gap_analysis[:3], indent=2)
            if isinstance(gap_analysis, list) and gap_analysis
            else "\n".join([f"- {gap}" for gap in prioritized_gaps[:3]])
        )
        opp_text = (
            "\n".join([f"- {item}" for item in opportunities[:3]])
            if isinstance(opportunities, list) and opportunities
            else "Based on identified gaps"
        )
        research_directions: List[Dict[str, Any]] = []
        direction_latency_ms = 0
        try:
            direction_client = get_provider_client(session.config.providerName)
            research_directions, direction_latency_ms = self._decompose_seed_query_for_brainstorm(
                session=session,
                client=direction_client,
                gap_text=gap_text,
                opp_text=opp_text,
                key_papers=literature_context,
            )
        except Exception as e:
            logger.warning("BFTS seed direction decomposition unavailable: %s", e)

        try:
            from app.modules.idea.bfts_search import BFTSSearchTree

            candidates: List[IdeaCandidate] = []
            direction_summaries: List[Dict[str, Any]] = []
            used_directional_bfts = False

            if research_directions:
                direction_config = BFTSConfig(
                    maxNodes=max(5, min(8, bfts_config.maxNodes)),
                    maxIterations=max(1, min(2, bfts_config.maxIterations)),
                    beamWidth=1,
                    expansionWidth=1,
                    maxLiteratureProbes=min(bfts_config.maxLiteratureProbes, 4),
                    maxReflectionRounds=max(1, min(bfts_config.maxReflectionRounds, 2)),
                    minEvidenceSupport=bfts_config.minEvidenceSupport,
                    minGraphGrounding=bfts_config.minGraphGrounding,
                    pruneDuplicateThreshold=bfts_config.pruneDuplicateThreshold,
                    scoreWeights=bfts_config.scoreWeights,
                )
                for index, direction in enumerate(research_directions):
                    direction_seed_query = self._direction_seed_query(seed, direction)
                    direction_literature_context = (
                        f"{literature_context}\n\n"
                        f"Research Direction: {direction.get('title', '')}\n"
                        f"Type: {direction.get('type', '')}\n"
                        f"Focus: {direction.get('focus', '')}\n"
                        f"Rationale: {direction.get('rationale', '')}"
                    )
                    direction_path_seed = path_seeds[index % len(path_seeds)]
                    try:
                        tree = BFTSSearchTree(
                            session_id=session.id,
                            bfts_config=direction_config,
                            provider_name=session.config.providerName,
                            model=session.config.model,
                            path_seeds=[direction_path_seed],
                            structured_papers=structured_papers,
                            literature_context=direction_literature_context,
                            seed_query=direction_seed_query,
                            paper_type=f"{paper_type} / {direction.get('type', '')}",
                        )
                        direction_candidates = tree.run()
                        self._tag_candidates_with_research_direction(direction_candidates, direction)
                        candidates.extend(direction_candidates)
                        direction_summaries.append({
                            **direction,
                            "pathSeedId": direction_path_seed.seedId,
                            "generatedCandidateCount": len(direction_candidates),
                        })
                    except Exception as direction_error:
                        logger.warning(
                            "BFTS directional search failed for %s: %s",
                            direction.get("id"),
                            direction_error,
                        )
                        direction_summaries.append({
                            **direction,
                            "pathSeedId": direction_path_seed.seedId,
                            "generatedCandidateCount": 0,
                            "error": str(direction_error),
                        })

            used_directional_bfts = bool(candidates and research_directions)
            if not candidates:
                tree = BFTSSearchTree(
                    session_id=session.id,
                    bfts_config=bfts_config,
                    provider_name=session.config.providerName,
                    model=session.config.model,
                    path_seeds=path_seeds,
                    structured_papers=structured_papers,
                    literature_context=literature_context,
                    seed_query=seed,
                    paper_type=paper_type,
                )

                candidates = tree.run()

            if not candidates:
                logger.warning("BFTS produced no candidates, falling back to legacy")
                return self._step_idea_brainstorm_legacy(session)
            candidates, deduped_candidate_ids = self._dedupe_candidates(
                candidates,
                max_count=min(20, max(max_candidates, len(candidates))),
            )

            # Store candidates
            candidate_ids = []
            for candidate in candidates:
                self.candidate_storage.create(candidate)
                candidate_ids.append(candidate.id)
                if candidate.id not in session.candidateIds:
                    session.candidateIds.append(candidate.id)

            inputs = {
                "seedQuery": seed,
                "paperType": paper_type,
                "method": "bfts_directional_tree_search" if used_directional_bfts else "bfts_tree_search",
                "seedCount": len(path_seeds),
                "maxNodes": bfts_config.maxNodes,
                "beamWidth": bfts_config.beamWidth,
                "maxReflectionRounds": bfts_config.maxReflectionRounds,
            }
            outputs = {
                "candidateCount": len(candidates),
                "candidateIds": candidate_ids,
                "method": "bfts_directional_tree_search" if used_directional_bfts else "bfts_tree_search",
                "bftsConfig": bfts_config.model_dump(),
                "directionalBftsConfig": direction_config.model_dump() if used_directional_bfts else None,
                "researchDirections": direction_summaries,
                "researchDirectionCount": len(research_directions),
                "directionDecompositionLatencyMs": direction_latency_ms,
                "dedupedCandidateIds": deduped_candidate_ids,
            }

            logger.info(f"BFTS: generated {len(candidates)} candidates from tree search")

        except Exception as e:
            logger.error(f"BFTS search failed: {e}, falling back to legacy brainstorm")
            import traceback
            logger.error(traceback.format_exc())
            return self._step_idea_brainstorm_legacy(session)

        return inputs, outputs, []

    def _build_bfts_literature_context(
        self, structured_papers: List[StructuredPaper], limit: int = 8
    ) -> str:
        """Build literature context string for BFTS reflection loops."""
        if not structured_papers:
            return "(No structured literature available yet)"

        lines = []
        for i, sp in enumerate(structured_papers[:limit]):
            title = sp.title or "(untitled)"
            year = sp.year or "N/A"
            claims_str = ""
            if sp.claims:
                claims_str = ". ".join(c.text[:100] for c in sp.claims[:2])
            cut_ins = [
                *sp.openQuestions[:2],
                *sp.failedAssumptions[:2],
                *sp.methodWeaknesses[:2],
                *sp.missingEvaluation[:2],
            ]
            cut_in_str = "; ".join(cut_ins[:4]) or "N/A"
            eval_str = "; ".join([
                *sp.baselineMethods[:2],
                *sp.recommendedMetrics[:2],
            ]) or "N/A"
            lines.append(
                f"[{i+1}] {title} ({year})\n"
                f"    Key claims: {claims_str}\n"
                f"    Idea cut-ins: {cut_in_str}\n"
                f"    Baselines/metrics: {eval_str}"
            )
        return "\n\n".join(lines)

    def _direction_decomposition_enabled(self) -> bool:
        return _env_bool("FAROS_IDEA_DIRECTION_DECOMPOSITION", True)

    def _direction_count_target(self) -> int:
        try:
            configured = int(os.getenv("FAROS_IDEA_DIRECTION_COUNT", "5"))
        except ValueError:
            configured = 5
        return max(3, min(5, configured))

    def _fallback_seed_research_directions(
        self,
        seed: str,
        paper_type: str,
        *,
        max_directions: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        target = max_directions or self._direction_count_target()
        base = [
            {
                "id": "dir-method",
                "type": "method",
                "title": "Method direction",
                "focus": f"Design a concrete method or algorithmic mechanism for {seed}.",
                "rationale": "Method-focused ideas usually produce a clear technical contribution.",
                "evidenceAnchors": [],
            },
            {
                "id": "dir-benchmark",
                "type": "benchmark",
                "title": "Benchmark direction",
                "focus": f"Build evaluation tasks, metrics, or baselines for {seed}.",
                "rationale": "Benchmark-focused ideas reduce ambiguity and make later experiments easier to verify.",
                "evidenceAnchors": [],
            },
            {
                "id": "dir-system",
                "type": "system",
                "title": "System direction",
                "focus": f"Turn {seed} into an end-to-end system with clear inputs, modules, and outputs.",
                "rationale": "System-focused ideas expose integration and deployment gaps.",
                "evidenceAnchors": [],
            },
            {
                "id": "dir-safety-reliability",
                "type": "safety_reliability",
                "title": "Safety and reliability direction",
                "focus": f"Improve robustness, faithfulness, reliability, or safety for {seed}.",
                "rationale": "Reliability-focused ideas are valuable when scientific claims need strong evidence.",
                "evidenceAnchors": [],
            },
            {
                "id": "dir-application",
                "type": "application",
                "title": "Application direction",
                "focus": f"Apply {seed} to a concrete high-value scenario with measurable outcomes.",
                "rationale": "Application-focused ideas turn broad topics into domain-grounded research questions.",
                "evidenceAnchors": [],
            },
        ]
        if paper_type in {"benchmark", "evaluation", "reproducibility"}:
            base = [base[1], base[0], base[3], base[2], base[4]]
        elif paper_type in {"system", "application"}:
            base = [base[2], base[4], base[0], base[3], base[1]]
        elif paper_type in {"safety"}:
            base = [base[3], base[1], base[0], base[2], base[4]]
        return base[:target]

    def _normalize_seed_research_directions(
        self,
        raw: Any,
        *,
        seed: str,
        paper_type: str,
        max_directions: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        target = max_directions or self._direction_count_target()
        allowed_types = {
            "method",
            "benchmark",
            "system",
            "safety_reliability",
            "application",
        }
        if isinstance(raw, dict):
            raw_directions = raw.get("researchDirections") or raw.get("directions") or []
        elif isinstance(raw, list):
            raw_directions = raw
        else:
            raw_directions = []

        directions: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for index, item in enumerate(raw_directions):
            if not isinstance(item, dict):
                continue
            direction_type = str(item.get("type", "") or "").strip().lower().replace("-", "_")
            if direction_type == "safety":
                direction_type = "safety_reliability"
            if direction_type not in allowed_types:
                direction_type = self._fallback_seed_research_directions(
                    seed,
                    paper_type,
                    max_directions=target,
                )[min(index, target - 1)]["type"]
            title = str(item.get("title", "") or "").strip()
            focus = str(item.get("focus", item.get("description", "")) or "").strip()
            rationale = str(item.get("rationale", item.get("why", "")) or "").strip()
            if not title and not focus:
                continue
            direction_id = str(item.get("id", "") or "").strip()
            if not direction_id:
                direction_id = f"dir-{direction_type}"
            direction_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", direction_id).strip("-").lower()
            key = f"{direction_type}:{title.lower()}:{focus.lower()[:80]}"
            if key in seen:
                continue
            seen.add(key)
            directions.append({
                "id": direction_id or f"dir-{direction_type}-{index + 1}",
                "type": direction_type,
                "title": title or f"{direction_type.replace('_', ' ').title()} direction",
                "focus": focus or f"Explore {seed} from a {direction_type} perspective.",
                "rationale": rationale or "Selected to diversify candidate idea generation.",
                "evidenceAnchors": _coerce_text_list(item.get("evidenceAnchors", []))[:5],
            })
            if len(directions) >= target:
                break

        if len(directions) < min(3, target):
            for fallback in self._fallback_seed_research_directions(
                seed,
                paper_type,
                max_directions=target,
            ):
                if len(directions) >= target:
                    break
                if fallback["type"] in {direction["type"] for direction in directions}:
                    continue
                directions.append(fallback)
        return directions[:target]

    def _decompose_seed_query_for_brainstorm(
        self,
        *,
        session: IdeaSession,
        client: Any,
        gap_text: str,
        opp_text: str,
        key_papers: str,
    ) -> tuple[List[Dict[str, Any]], int]:
        if not self._direction_decomposition_enabled():
            return [], 0
        seed = session.config.seedQuery
        paper_type = session.config.paperType
        target = self._direction_count_target()
        fallback = self._fallback_seed_research_directions(
            seed,
            paper_type,
            max_directions=target,
        )
        try:
            messages = [
                ChatMessage(role="system", content=prompts.SEED_DIRECTION_DECOMPOSITION_SYSTEM),
                ChatMessage(
                    role="user",
                    content=prompts.SEED_DIRECTION_DECOMPOSITION_USER.format(
                        seed_query=seed,
                        paper_type=paper_type,
                        domain=session.config.domain or "unspecified",
                        gap_analysis=gap_text or "N/A",
                        opportunities=opp_text or "N/A",
                        key_papers=key_papers or "N/A",
                    ),
                ),
            ]
            response = client.chat(messages, model=session.config.model, max_tokens=1400)
            data = _extract_json_object(getattr(response, "text", "") or "")
            directions = self._normalize_seed_research_directions(
                data,
                seed=seed,
                paper_type=paper_type,
                max_directions=target,
            )
            return directions or fallback, int(getattr(response, "latency_ms", 0) or 0)
        except Exception as e:
            logger.warning("Seed query direction decomposition failed; using fallback directions: %s", e)
            return fallback, 0

    def _direction_seed_query(self, seed: str, direction: Dict[str, Any]) -> str:
        return (
            f"{seed}\n"
            f"Research Direction ID: {direction.get('id', '')}\n"
            f"Research Direction Type: {direction.get('type', '')}\n"
            f"Research Direction Title: {direction.get('title', '')}\n"
            f"Direction Focus: {direction.get('focus', '')}\n"
            f"Direction Rationale: {direction.get('rationale', '')}\n"
            "Generate ideas only for this direction while staying faithful to the seed topic."
        )

    def _tag_candidates_with_research_direction(
        self,
        candidates: List[IdeaCandidate],
        direction: Dict[str, Any],
    ) -> None:
        direction_id = str(direction.get("id", "") or "")
        direction_type = str(direction.get("type", "") or "")
        direction_title = str(direction.get("title", "") or "")
        tags = [
            f"direction:{direction_id}",
            f"directionType:{direction_type}",
            f"directionTitle:{direction_title}",
        ]
        for candidate in candidates:
            if not candidate.draftPlan:
                candidate.draftPlan = DraftPlan(
                    researchQuestion=candidate.problem,
                    hypothesis=candidate.hypothesisStatement or candidate.keyInsight,
                    methodology=candidate.proposedMethod,
                    expectedOutcomes=candidate.expectedMetrics,
                )
            existing_tags = list(candidate.draftPlan.tags or [])
            for tag in tags:
                if tag and tag not in existing_tags:
                    existing_tags.append(tag)
            candidate.draftPlan.tags = existing_tags
            note = (
                f"Generated from {direction_type} research direction: "
                f"{direction.get('focus', '')}"
            ).strip()
            if note and note not in (candidate.draftPlan.notes or ""):
                candidate.draftPlan.notes = (
                    f"{candidate.draftPlan.notes}\n{note}".strip()
                    if candidate.draftPlan.notes
                    else note
                )

    def _step_idea_brainstorm(self, session: IdeaSession) -> tuple:
        """Generate candidate ideas — routes to BFTS or legacy based on availability."""
        # Use BFTS if handoff is available
        try:
            handoff = self.handoff_storage.get_by_session(session.id)
            if handoff and handoff.pathSeedIds:
                return self._step_idea_brainstorm_bfts(session)
        except Exception:
            pass

        # Fallback: legacy single-shot LLM
        return self._step_idea_brainstorm_legacy(session)

    def _step_idea_brainstorm_legacy(self, session: IdeaSession) -> tuple:
        """Original single-shot LLM brainstorm (fallback)."""
        seed = session.config.seedQuery
        paper_type = session.config.paperType
        max_candidates = session.config.maxCandidates
        generation_count = min(20, max(max_candidates, max_candidates * 3))
        literature = self.get_literature(session.id)
        structured_papers: List[StructuredPaper] = []
        try:
            structured_papers = self.structured_storage.list_by_session(session.id)
        except Exception:
            structured_papers = []

        # Get gap analysis results
        gap_analysis = []
        opportunities = []
        prioritized_gaps = []
        if session.trace:
            for step in session.trace.steps:
                if step.name == "gapAnalysis":
                    gap_analysis = step.outputs.get("gapAnalysis", [])
                    opportunities = step.outputs.get("researchOpportunities", [])
                    prioritized_gaps = step.outputs.get("prioritizedGaps", [])
                    break

        # Build context
        if structured_papers:
            key_papers = "\n".join([
                (
                    f"- {sp.title} ({sp.year or 'N/A'})\n"
                    f"  cut-ins: {'; '.join([*sp.openQuestions[:1], *sp.failedAssumptions[:1], *sp.methodWeaknesses[:1], *sp.missingEvaluation[:1]]) or 'N/A'}\n"
                    f"  baselines/metrics: {'; '.join([*sp.baselineMethods[:2], *sp.recommendedMetrics[:2]]) or 'N/A'}"
                )
                for sp in structured_papers[:5]
            ])
        else:
            key_papers = "\n".join([
                f"- {item.title} ({item.year or 'N/A'})"
                for item in literature[:5]
            ])

        gap_text = json.dumps(gap_analysis[:3], indent=2) if gap_analysis else "\n".join([f"- {g}" for g in prioritized_gaps[:3]])
        opp_text = "\n".join([f"- {o}" for o in opportunities[:3]]) if opportunities else "Based on identified gaps"

        try:
            client = get_provider_client(session.config.providerName)
            research_directions, direction_latency_ms = self._decompose_seed_query_for_brainstorm(
                session=session,
                client=client,
                gap_text=gap_text,
                opp_text=opp_text,
                key_papers=key_papers,
            )
            candidates: List[IdeaCandidate] = []
            direction_summaries: List[Dict[str, Any]] = []
            total_latency_ms = direction_latency_ms

            if research_directions:
                ideas_per_direction = max(
                    1,
                    min(5, (generation_count + len(research_directions) - 1) // len(research_directions)),
                )
                for direction in research_directions:
                    user_prompt = prompts.IDEA_BRAINSTORM_USER.format(
                        seed_query=self._direction_seed_query(seed, direction),
                        paper_type=f"{paper_type} / {direction.get('type', '')}",
                        max_candidates=ideas_per_direction,
                        gap_analysis=gap_text,
                        opportunities=opp_text,
                        key_papers=key_papers,
                    )

                    messages = [
                        ChatMessage(role="system", content=prompts.IDEA_BRAINSTORM_SYSTEM),
                        ChatMessage(role="user", content=user_prompt),
                    ]

                    try:
                        response = client.chat(messages, model=session.config.model, max_tokens=2200)
                        total_latency_ms += int(getattr(response, "latency_ms", 0) or 0)
                        direction_candidates = self._parse_ideas_json(
                            session.id,
                            response.text,
                            ideas_per_direction,
                        )
                        if not direction_candidates:
                            direction_candidates = self._parse_ideas(
                                session.id,
                                response.text,
                                ideas_per_direction,
                            )
                        self._tag_candidates_with_research_direction(direction_candidates, direction)
                        candidates.extend(direction_candidates)
                        direction_summaries.append({
                            **direction,
                            "requestedCandidateCount": ideas_per_direction,
                            "generatedCandidateCount": len(direction_candidates),
                        })
                    except Exception as direction_error:
                        logger.warning(
                            "Directional idea generation failed for %s: %s",
                            direction.get("id"),
                            direction_error,
                        )
                        direction_summaries.append({
                            **direction,
                            "requestedCandidateCount": ideas_per_direction,
                            "generatedCandidateCount": 0,
                            "error": str(direction_error),
                        })
            else:
                user_prompt = prompts.IDEA_BRAINSTORM_USER.format(
                    seed_query=seed,
                    paper_type=paper_type,
                    max_candidates=generation_count,
                    gap_analysis=gap_text,
                    opportunities=opp_text,
                    key_papers=key_papers
                )

                messages = [
                    ChatMessage(role="system", content=prompts.IDEA_BRAINSTORM_SYSTEM),
                    ChatMessage(role="user", content=user_prompt)
                ]

                response = client.chat(messages, model=session.config.model, max_tokens=3000)
                total_latency_ms += int(getattr(response, "latency_ms", 0) or 0)

                # Parse ideas from response
                candidates = self._parse_ideas_json(session.id, response.text, generation_count)

                if not candidates:
                    # Fallback to text parsing
                    candidates = self._parse_ideas(session.id, response.text, generation_count)

            if not candidates:
                # Generate fallback
                fallback_directions = research_directions or self._fallback_seed_research_directions(
                    seed,
                    paper_type,
                    max_directions=min(3, self._direction_count_target()),
                )
                candidates = self._generate_fallback_candidates(session.id, seed, min(3, max_candidates))
                for index, candidate in enumerate(candidates):
                    self._tag_candidates_with_research_direction(
                        [candidate],
                        fallback_directions[index % len(fallback_directions)],
                    )
            candidates, deduped_candidate_ids = self._dedupe_candidates(
                candidates,
                max_count=min(20, max(max_candidates, len(candidates))),
            )

            # Store candidates
            candidate_ids = []
            for candidate in candidates:
                self.candidate_storage.create(candidate)
                candidate_ids.append(candidate.id)
                session.candidateIds.append(candidate.id)

            inputs = {"topic": seed, "maxCandidates": max_candidates, "paperType": paper_type}
            outputs = {
                "candidateCount": len(candidates),
                "candidateIds": candidate_ids,
                "llmLatencyMs": total_latency_ms,
                "method": "legacy_directional_brainstorm" if research_directions else "legacy_single_shot",
                "requestedGenerationCount": generation_count,
                "researchDirections": direction_summaries,
                "researchDirectionCount": len(research_directions),
                "dedupedCandidateIds": deduped_candidate_ids,
            }

        except Exception as e:
            logger.error(f"LLM brainstorm failed: {e}")
            # Generate fallback candidates
            candidates = self._generate_fallback_candidates(session.id, seed, min(3, max_candidates))
            candidates, deduped_candidate_ids = self._dedupe_candidates(candidates)

            candidate_ids = []
            for candidate in candidates:
                self.candidate_storage.create(candidate)
                candidate_ids.append(candidate.id)
                session.candidateIds.append(candidate.id)

            inputs = {"topic": seed}
            outputs = {
                "candidateCount": len(candidates),
                "candidateIds": candidate_ids,
                "error": str(e),
                "method": "legacy_fallback",
                "dedupedCandidateIds": deduped_candidate_ids,
            }

        return inputs, outputs, []
    
    def _parse_ideas_json(self, session_id: str, text: str, max_count: int) -> List[IdeaCandidate]:
        """Parse ideas from JSON response."""
        candidates = []
        
        # Try to extract JSON from response
        try:
            # Find JSON block
            json_match = re.search(r'\{[\s\S]*"ideas"[\s\S]*\}', text)
            if json_match:
                data = json.loads(json_match.group())
                ideas = _coerce_dict_list(data.get("ideas", []))
                
                for idea in ideas[:max_count]:
                    expected_outcomes = _coerce_text_list(idea.get("expectedOutcomes", []))
                    hypothesis = (
                        idea.get("hypothesisStatement")
                        or idea.get("hypothesis")
                        or idea.get("keyInsight", "")
                    )
                    proposed_method = (
                        idea.get("proposedMethod")
                        or idea.get("method")
                        or idea.get("approach", "To be defined")
                    )
                    expected_outcome = (
                        idea.get("expectedOutcome")
                        or "; ".join(expected_outcomes)
                    )
                    # Parse experiments
                    experiments = []
                    for exp in _coerce_dict_list(idea.get("requiredExperiments", [])):
                        experiments.append(ExperimentSpec(
                            name=exp.get("name", "Experiment"),
                            description=exp.get("description", ""),
                            metrics=_coerce_strict_text_list(exp.get("metrics", [])),
                            datasets=_coerce_strict_text_list(exp.get("datasets", [])),
                        ))
                    
                    # Parse risks
                    risks = []
                    for risk in _coerce_dict_list(idea.get("risks", [])):
                        risks.append(RiskItem(
                            risk=risk.get("risk", ""),
                            mitigation=risk.get("mitigation", ""),
                        ))
                    
                    candidate = IdeaCandidate(
                        id=generate_candidate_id(),
                        sessionId=session_id,
                        title=idea.get("title", "Untitled Idea"),
                        problem=idea.get("problem", "Problem statement pending."),
                        hypothesisStatement=hypothesis,
                        keyInsight=idea.get("keyInsight", idea.get("approach", "Key insight pending.")),
                        proposedMethod=proposed_method,
                        expectedOutcome=expected_outcome,
                        novelty=5.0,
                        noveltyRationale="Pending ranking",
                        feasibility=5.0,
                        feasibilityRationale="Pending ranking",
                        impact=5.0,
                        impactRationale="Pending ranking",
                        scoringMethod="pending",
                        risks=risks,
                        requiredExperiments=experiments,
                        expectedMetrics=expected_outcomes,
                        draftPlan=DraftPlan(
                            researchQuestion=idea.get("problem", ""),
                            hypothesis=hypothesis,
                            methodology=proposed_method,
                            expectedOutcomes=expected_outcomes,
                        ),
                    )
                    candidates.append(candidate)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"JSON parsing failed: {e}")
        
        return candidates
    
    def _parse_ideas(self, session_id: str, text: str, max_count: int) -> List[IdeaCandidate]:
        """Parse ideas from LLM response."""
        candidates = []
        
        # Simple parsing - split by numbered ideas
        sections = text.split("\n\n")
        current_idea = {}
        
        for section in sections:
            lines = section.strip().split("\n")
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                lower = line.lower()
                if "title:" in lower or line.startswith("1."):
                    # Save previous idea if exists
                    if current_idea.get("title"):
                        candidates.append(self._create_candidate(session_id, current_idea))
                        if len(candidates) >= max_count:
                            return candidates
                    current_idea = {"title": line.split(":", 1)[-1].strip() if ":" in line else line[2:].strip()}
                elif "problem" in lower:
                    current_idea["problem"] = line.split(":", 1)[-1].strip() if ":" in line else line
                elif "insight" in lower:
                    current_idea["insight"] = line.split(":", 1)[-1].strip() if ":" in line else line
                elif "novelty" in lower and "score" in lower:
                    try:
                        score = float(''.join(c for c in line if c.isdigit() or c == '.'))
                        current_idea["novelty"] = min(10, max(0, score))
                    except:
                        current_idea["novelty"] = 7.0
                elif "feasibility" in lower and "score" in lower:
                    try:
                        score = float(''.join(c for c in line if c.isdigit() or c == '.'))
                        current_idea["feasibility"] = min(10, max(0, score))
                    except:
                        current_idea["feasibility"] = 7.0
                elif "impact" in lower and "score" in lower:
                    try:
                        score = float(''.join(c for c in line if c.isdigit() or c == '.'))
                        current_idea["impact"] = min(10, max(0, score))
                    except:
                        current_idea["impact"] = 7.0
        
        # Don't forget the last idea
        if current_idea.get("title") and len(candidates) < max_count:
            candidates.append(self._create_candidate(session_id, current_idea))
        
        return candidates
    
    def _create_candidate(self, session_id: str, data: Dict[str, Any]) -> IdeaCandidate:
        """Create a candidate from parsed data."""
        return IdeaCandidate(
            id=generate_candidate_id(),
            sessionId=session_id,
            title=data.get("title", "Untitled Idea"),
            problem=data.get("problem", "Problem statement pending."),
            keyInsight=data.get("insight", "Key insight pending."),
            novelty=data.get("novelty", 5.0),
            noveltyRationale="Pending ranking",
            feasibility=data.get("feasibility", 5.0),
            feasibilityRationale="Pending ranking",
            impact=data.get("impact", 5.0),
            impactRationale="Pending ranking",
            scoringMethod="pending",
            draftPlan=DraftPlan(
                researchQuestion=data.get("problem", ""),
                hypothesis=data.get("insight", ""),
                methodology="To be defined",
                expectedOutcomes=["Improved performance", "Novel insights"],
            ),
        )
    
    def _generate_fallback_candidates(self, session_id: str, seed: str, count: int) -> List[IdeaCandidate]:
        """Generate fallback candidates when LLM fails."""
        templates = [
            {
                "title": f"Scalable {seed} with Efficient Attention",
                "problem": f"Current {seed} methods do not scale to large datasets.",
                "insight": "Using sparse attention patterns can reduce complexity.",
                "novelty": 7.5,
                "feasibility": 8.0,
                "impact": 7.0,
            },
            {
                "title": f"Interpretable {seed} via Concept Bottlenecks",
                "problem": f"{seed} models lack interpretability.",
                "insight": "Concept bottleneck layers provide human-understandable explanations.",
                "novelty": 8.0,
                "feasibility": 7.0,
                "impact": 8.5,
            },
            {
                "title": f"Self-Supervised {seed} for Low-Resource Settings",
                "problem": f"{seed} requires large labeled datasets.",
                "insight": "Self-supervised pretraining can reduce label requirements.",
                "novelty": 7.0,
                "feasibility": 8.5,
                "impact": 7.5,
            },
        ]
        
        candidates = []
        for i, template in enumerate(templates[:count]):
            candidates.append(self._create_candidate(session_id, template))
        
        return candidates
    
    def _step_rank_candidates(self, session: IdeaSession) -> tuple:
        """Rank candidates with multi-criteria scoring, evidence binding, and critique.

        Step 6 delivers:
          1. Numeric multi-criteria scoring (via ranking_service)
          2. CandidateGraphEvidence — rule-based dual-graph mapping
          3. PriorWorkComparison — LLM comparison vs selected papers
          4. IdeaCritique — LLM structured critique
          5. RankedIdeaOutput — persisted to storage

        Falls back gracefully when dual-graph artifacts are unavailable.
        """
        seed = session.config.seedQuery
        paper_type = session.config.paperType
        domain = session.config.domain or "general"
        candidates = self.get_candidates(session.id, view="debug")

        if not candidates:
            return {"candidateCount": 0}, {"rankings": [], "error": "No candidates to rank"}, []

        # --- Phase 1: Load dual-graph artifacts for evidence binding ---
        structured_papers = []
        reasoning_kg = None
        path_seeds = []
        evidence_links = []
        handoff = None
        try:
            structured_papers = self.structured_storage.list_by_session(session.id)
            reasoning_kg = self.reasoning_kg_storage.get_by_session(session.id)
            path_seeds = self.path_seed_storage.list_by_session(session.id)
            evidence_links = self.evidence_link_storage.list_by_session(session.id)
        except Exception as e:
            logger.warning(f"Dual-graph artifact loading failed: {e}, evidence binding will be partial")

        try:
            handoff = self.handoff_storage.get_by_session(session.id)
        except Exception:
            pass

        valid_paper_ids = {
            paper_id
            for sp in structured_papers
            for paper_id in [sp.rawPaperId, sp.id]
            if paper_id
        }

        # --- Phase 2: Build and attach CandidateGraphEvidence before ranking ---
        evidence_list: List[CandidateGraphEvidence] = []
        for candidate in candidates:
            evidence = self._build_candidate_evidence(
                candidate=candidate,
                structured_papers=structured_papers,
                reasoning_kg=reasoning_kg,
                path_seeds=path_seeds,
                evidence_links=evidence_links,
            )
            evidence_list.append(evidence)
            candidate.graphEvidence = evidence
            evidence_refs = [
                paper_id for paper_id in evidence.supportingPaperIds
                if not valid_paper_ids or paper_id in valid_paper_ids
            ][:8]
            if evidence_refs:
                candidate.references = list(dict.fromkeys([
                    *candidate.references,
                    *evidence_refs,
                ]))

        # --- Phase 3: Numeric multi-criteria scoring ---
        ranking_service = get_ranking_service()
        try:
            updated_candidates, ranking_results = ranking_service.rank_candidates(
                candidates=candidates,
                seed_query=seed,
                paper_type=paper_type,
                domain=domain,
                provider_name=session.config.providerName,
                model=session.config.model,
                session_id=session.id,
            )
        except Exception as e:
            logger.error(f"Ranking service failed: {e}")
            updated_candidates = candidates
            ranking_results = []

        # Sort by overallScore descending
        ranked = sorted(updated_candidates, key=lambda c: c.overallScore, reverse=True)

        # --- Phase 4: LLM-driven PriorWorkComparison + IdeaCritique ---
        prior_work_comparisons: List[PriorWorkComparison] = []
        critiques: List[IdeaCritique] = []
        top_k = min(5, len(ranked))  # Deep analysis on top 5 candidates
        literature_context = ""

        if structured_papers and ranked:
            literature_context = self._build_ranking_literature_context(
                structured_papers, reasoning_kg, path_seeds
            )
            try:
                for candidate in ranked[:top_k]:
                    comparison, critique = self._llm_analyze_candidate(
                        candidate=candidate,
                        seed_query=seed,
                        paper_type=paper_type,
                        domain=domain,
                        literature_context=literature_context,
                        provider_name=session.config.providerName,
                        model=session.config.model,
                    )
                    prior_work_comparisons.append(comparison)
                    critiques.append(critique)
            except Exception as e:
                logger.warning(f"LLM candidate analysis failed: {e}")

        # --- Phase 5: Idea-stage review gate + optional regeneration ---
        english_search_queries = self._get_step_output(
            session, "expandQuery", "englishSearchQueries", [],
        )
        gate_reports = self._apply_idea_review_gate(
            ranked=ranked,
            evidence_list=evidence_list,
            prior_work_comparisons=prior_work_comparisons,
            critiques=critiques,
            seed_query=seed,
            provider_name=session.config.providerName,
            model=session.config.model,
            literature_context=literature_context,
            english_search_queries=english_search_queries,
        )
        ranked = sorted(ranked, key=lambda c: c.overallScore, reverse=True)
        regenerated_candidate_ids: List[str] = []
        literature_repair_reports: List[Dict[str, Any]] = []
        paper_quality_gate = _evaluate_paper_quality_gate(
            seed=seed,
            domain=domain,
            papers=structured_papers,
            stage="ideaReview.structuredPapers",
            extra_terms=self._core_search_queries(session),
            paper_type=session.config.paperType,
        )
        max_review_iterations = max(1, min(5, getattr(session.config, "maxReviewIterations", 2)))
        target_final_count = self._target_final_candidate_count(session, ranked)
        review_iteration_summaries: List[Dict[str, Any]] = []
        repaired_candidate_ids: set[str] = set()

        def _replace_candidate_analysis(
            comparison: PriorWorkComparison,
            critique: IdeaCritique,
        ) -> None:
            prior_work_comparisons[:] = [
                item for item in prior_work_comparisons
                if item.candidateId != comparison.candidateId
            ]
            critiques[:] = [
                item for item in critiques
                if item.candidateId != critique.candidateId
            ]
            prior_work_comparisons.append(comparison)
            critiques.append(critique)

        for review_iteration in range(max_review_iterations):
            ranked = sorted(ranked, key=lambda c: c.overallScore, reverse=True)
            final_ready = self._current_final_ready_candidates(
                ranked,
                gate_reports,
                target_final_count,
            )
            final_ready_ids = {candidate.id for candidate in final_ready}
            top_candidate = ranked[0] if ranked else None
            top_gate = gate_reports.get(top_candidate.id) if top_candidate else None
            top_passed = bool(
                top_gate
                and top_gate.get("passed")
                and paper_quality_gate.get("passed", False)
            )
            summary = {
                "iteration": review_iteration + 1,
                "targetFinalCount": target_final_count,
                "finalReadyCountBefore": len(final_ready),
                "finalReadyCandidateIdsBefore": [candidate.id for candidate in final_ready],
                "topCandidateId": top_candidate.id if top_candidate else None,
                "topPassed": top_passed,
                "paperGatePassed": bool(paper_quality_gate.get("passed", False)),
                "blockingIssueCount": len(top_gate.get("blockingIssues", [])) if top_gate else 0,
                "warningCount": len(top_gate.get("warnings", [])) if top_gate else 0,
                "repairTargets": [],
                "action": "none",
            }
            if len(final_ready) >= target_final_count or not top_gate:
                review_iteration_summaries.append(summary)
                break

            repair_targets = self._pick_pool_repair_targets(
                ranked,
                gate_reports,
                final_ready_ids=final_ready_ids,
                paper_quality_gate=paper_quality_gate,
                max_targets=max(1, target_final_count - len(final_ready)),
                skipped_candidate_ids=repaired_candidate_ids,
            )
            if not repair_targets:
                review_iteration_summaries.append(summary)
                break

            for repair_target in repair_targets:
                target_candidate = repair_target["candidate"]
                target_gate = gate_reports.get(target_candidate.id, {})
                action = repair_target["action"]
                target_summary = {
                    "candidateId": target_candidate.id,
                    "action": action,
                    "failureRoute": repair_target.get("failureRoute", "none"),
                    "reason": repair_target.get("reason", ""),
                    "qualityScore": repair_target.get("qualityScore"),
                    "directionType": repair_target.get("directionType"),
                    "directionId": repair_target.get("directionId"),
                    "fillsMissingDirection": bool(repair_target.get("fillsMissingDirection")),
                }
                summary["repairTargets"].append(target_summary)
                summary["action"] = (
                    "pool_repair"
                    if summary.get("action") == "none"
                    else f"{summary.get('action')}+pool_repair"
                )

                if action == "literature_repair" and literature_repair_reports:
                    action = "regenerate_idea"
                    target_summary["action"] = action
                    target_summary["reason"] = (
                        f"{target_summary['reason']} Literature repair was already attempted; "
                        "regenerating the candidate against the repaired evidence pool."
                    )

                if (
                    action == "literature_repair"
                    and session.config.providerName
                    and session.config.model
                ):
                    repair_report = self._repair_literature_pool_for_idea_quality(
                        session,
                        review_gate=target_gate,
                        paper_quality_gate=paper_quality_gate,
                    )
                    literature_repair_reports.append(repair_report)
                    target_summary["createdRawPaperCount"] = len(
                        repair_report.get("persistReport", {}).get("createdRawPaperIds", [])
                    )
                    target_summary["paperGateAfterRepair"] = bool(
                        repair_report.get("paperQualityGateAfter", {}).get("passed", False)
                    )
                    try:
                        structured_papers = self.structured_storage.list_by_session(session.id)
                        reasoning_kg = self.reasoning_kg_storage.get_by_session(session.id)
                        path_seeds = self.path_seed_storage.list_by_session(session.id)
                        evidence_links = self.evidence_link_storage.list_by_session(session.id)
                        literature_context = self._build_ranking_literature_context(
                            structured_papers, reasoning_kg, path_seeds
                        ) if structured_papers else literature_context
                        evidence_list = [
                            self._build_candidate_evidence(
                                candidate=candidate,
                                structured_papers=structured_papers,
                                reasoning_kg=reasoning_kg,
                                path_seeds=path_seeds,
                                evidence_links=evidence_links,
                            )
                            for candidate in ranked
                        ]
                        paper_quality_gate = _evaluate_paper_quality_gate(
                            seed=seed,
                            domain=domain,
                            papers=structured_papers,
                            stage=f"ideaReview.iteration{review_iteration + 1}.afterLiteratureRepair",
                            extra_terms=self._core_search_queries(session),
                            paper_type=session.config.paperType,
                        )
                        if structured_papers:
                            for candidate in ranked[:min(5, len(ranked))]:
                                try:
                                    comparison, critique = self._llm_analyze_candidate(
                                        candidate=candidate,
                                        seed_query=seed,
                                        paper_type=paper_type,
                                        domain=domain,
                                        literature_context=literature_context,
                                        provider_name=session.config.providerName,
                                        model=session.config.model,
                                    )
                                except Exception as e:
                                    logger.warning(
                                        "LLM candidate re-analysis after literature repair failed: %s",
                                        e,
                                    )
                                    comparison, critique = self._fallback_analysis(candidate)
                                _replace_candidate_analysis(comparison, critique)
                        gate_reports = self._apply_idea_review_gate(
                            ranked=ranked,
                            evidence_list=evidence_list,
                            prior_work_comparisons=prior_work_comparisons,
                            critiques=critiques,
                            seed_query=seed,
                            provider_name=session.config.providerName,
                            model=session.config.model,
                            literature_context=literature_context,
                            english_search_queries=english_search_queries,
                        )
                    except Exception as e:
                        logger.warning("Reloading repaired idea-stage evidence failed: %s", e, exc_info=True)
                        target_summary["error"] = str(e)
                    continue

                if action != "regenerate_idea":
                    target_summary["skipped"] = True
                    target_summary["skipReason"] = f"Unsupported repair action: {action}"
                    repaired_candidate_ids.add(target_candidate.id)
                    continue

                repaired_candidate_ids.add(target_candidate.id)
                if not (structured_papers and session.config.providerName and session.config.model):
                    target_summary["skipped"] = True
                    target_summary["skipReason"] = "Regeneration requires structured evidence and an active LLM provider."
                    continue

                try:
                    regenerated = self._regenerate_candidate_from_review(
                        session=session,
                        base_candidate=target_candidate,
                        review_gate=target_gate,
                        critique=next((item for item in critiques if item.candidateId == target_candidate.id), None),
                        prior_work=[
                            item for item in prior_work_comparisons
                            if item.candidateId == target_candidate.id
                        ],
                        literature_context=literature_context,
                    )
                except Exception as e:
                    logger.warning(f"Idea review regeneration failed: {e}")
                    target_summary["error"] = str(e)
                    regenerated = None
                if not regenerated:
                    target_summary["skipped"] = True
                    target_summary["skipReason"] = "LLM regeneration returned no candidate."
                    continue
                self._copy_candidate_direction_metadata(
                    source=target_candidate,
                    target=regenerated,
                )

                try:
                    scored_candidates, _ = ranking_service.rank_candidates(
                        candidates=[regenerated],
                        seed_query=seed,
                        paper_type=paper_type,
                        domain=domain,
                        provider_name=session.config.providerName,
                        model=session.config.model,
                        session_id=session.id,
                    )
                    regenerated = scored_candidates[0] if scored_candidates else regenerated
                    self._copy_candidate_direction_metadata(
                        source=target_candidate,
                        target=regenerated,
                    )
                except Exception as e:
                    logger.warning(f"Ranking regenerated candidate failed: {e}")
                regenerated_evidence = self._build_candidate_evidence(
                    candidate=regenerated,
                    structured_papers=structured_papers,
                    reasoning_kg=reasoning_kg,
                    path_seeds=path_seeds,
                    evidence_links=evidence_links,
                )
                evidence_list.append(regenerated_evidence)
                try:
                    comparison, critique = self._llm_analyze_candidate(
                        candidate=regenerated,
                        seed_query=seed,
                        paper_type=paper_type,
                        domain=domain,
                        literature_context=literature_context,
                        provider_name=session.config.providerName,
                        model=session.config.model,
                    )
                except Exception as e:
                    logger.warning(f"LLM analysis for regenerated candidate failed: {e}")
                    comparison, critique = self._fallback_analysis(regenerated)
                _replace_candidate_analysis(comparison, critique)
                gate_reports.update(self._apply_idea_review_gate(
                    ranked=[regenerated],
                    evidence_list=[regenerated_evidence],
                    prior_work_comparisons=[comparison],
                    critiques=[critique],
                    seed_query=seed,
                    provider_name=session.config.providerName,
                    model=session.config.model,
                    literature_context=literature_context,
                    english_search_queries=english_search_queries,
                ))
                ranked.append(regenerated)
                regenerated_candidate_ids.append(regenerated.id)
                target_summary["regeneratedCandidateId"] = regenerated.id
                if regenerated.id not in session.candidateIds:
                    session.candidateIds.append(regenerated.id)
                try:
                    self.candidate_storage.create(regenerated)
                except Exception as e:
                    logger.warning(f"Failed to persist regenerated candidate {regenerated.id}: {e}")
                ranked = sorted(
                    ranked,
                    key=lambda c: self._direction_aware_quality_score(
                        c,
                        gate_reports.get(c.id),
                    ),
                    reverse=True,
                )

            final_ready_after = self._current_final_ready_candidates(
                ranked,
                gate_reports,
                target_final_count,
            )
            summary["finalReadyCountAfter"] = len(final_ready_after)
            summary["finalReadyCandidateIdsAfter"] = [candidate.id for candidate in final_ready_after]
            review_iteration_summaries.append(summary)
            if len(final_ready_after) >= target_final_count:
                break

        # --- Phase 6: Backfill evidence/critique into final candidates (PDF v5) ---
        evidence_by_candidate = {e.candidateId: e for e in evidence_list}
        valid_paper_ids = {
            paper_id
            for sp in structured_papers
            for paper_id in [sp.rawPaperId, sp.id]
            if paper_id
        }
        paper_index_map = {
            str(index + 1): (sp.rawPaperId or sp.id)
            for index, sp in enumerate(structured_papers)
            if (sp.rawPaperId or sp.id)
        }
        sanitized_comparisons: List[PriorWorkComparison] = []
        for pc in prior_work_comparisons:
            compared_ids: List[str] = []
            for raw_id in pc.comparedPaperIds:
                paper_id = str(raw_id).strip().strip("[]")
                if paper_id in valid_paper_ids:
                    compared_ids.append(paper_id)
                elif paper_id in paper_index_map:
                    compared_ids.append(paper_index_map[paper_id])
            if not compared_ids:
                ev = evidence_by_candidate.get(pc.candidateId)
                if ev:
                    compared_ids = [
                        paper_id for paper_id in ev.supportingPaperIds
                        if not valid_paper_ids or paper_id in valid_paper_ids
                    ][:5]
            sanitized_comparisons.append(
                pc.model_copy(update={"comparedPaperIds": list(dict.fromkeys(compared_ids))})
            )
        prior_work_comparisons = sanitized_comparisons

        comparison_by_candidate: Dict[str, list] = {}
        for pc in prior_work_comparisons:
            comparison_by_candidate.setdefault(pc.candidateId, []).append(pc)
        critique_by_candidate = {c.candidateId: c for c in critiques}

        for candidate in ranked:
            # Embed graphEvidence into the candidate itself
            ev = evidence_by_candidate.get(candidate.id)
            if ev:
                candidate.graphEvidence = ev
                evidence_refs = [
                    paper_id for paper_id in ev.supportingPaperIds
                    if not valid_paper_ids or paper_id in valid_paper_ids
                ][:8]
                candidate.references = list(dict.fromkeys([
                    *candidate.references,
                    *evidence_refs,
                ]))

            # Embed closest prior work comparisons
            comparisons = comparison_by_candidate.get(candidate.id, [])
            if comparisons and not candidate.closestPriorWork:
                candidate.closestPriorWork = comparisons

            # Embed critique
            crit = critique_by_candidate.get(candidate.id)
            if crit and not candidate.critique:
                candidate.critique = crit

            # Keep GET /ideas/sessions/{id}/candidates aligned with the final Step 6 candidate.
            try:
                self.candidate_storage.create(candidate)
            except Exception as e:
                logger.warning(f"Failed to persist final candidate {candidate.id}: {e}")

        # --- Phase 7: Assemble and persist RankedIdeaOutput ---
        scores_list = [c.overallScore for c in ranked]
        variance = 0.0
        if len(scores_list) > 1:
            mean = sum(scores_list) / len(scores_list)
            variance = sum((s - mean) ** 2 for s in scores_list) / len(scores_list)

        ranked_output = RankedIdeaOutput(
            id=generate_ranked_output_id(),
            sessionId=session.id,
            rankedCandidates=ranked,
            evidence=evidence_list,
            priorWorkComparisons=prior_work_comparisons,
            critiques=critiques,
            scoreVariance=round(variance, 3),
            minScore=round(min(scores_list), 2) if scores_list else 0.0,
            maxScore=round(max(scores_list), 2) if scores_list else 0.0,
            rankedCount=len(ranked),
            topCandidateId=ranked[0].id if ranked else None,
            rankingMethod=("llm_multi_criteria+idea_review_gate" if ranking_results else "heuristic+idea_review_gate"),
        )
        if literature_repair_reports:
            ranked_output = RankedIdeaOutput(
                id=ranked_output.id,
                sessionId=ranked_output.sessionId,
                rankedCandidates=ranked_output.rankedCandidates,
                evidence=ranked_output.evidence,
                priorWorkComparisons=ranked_output.priorWorkComparisons,
                critiques=ranked_output.critiques,
                scoreVariance=ranked_output.scoreVariance,
                minScore=ranked_output.minScore,
                maxScore=ranked_output.maxScore,
                rankedCount=ranked_output.rankedCount,
                topCandidateId=ranked_output.topCandidateId,
                rankingMethod=f"{ranked_output.rankingMethod}+literature_repair",
                createdAt=ranked_output.createdAt,
            )
        try:
            self.ranked_output_storage.create(ranked_output)
        except Exception as e:
            logger.warning(f"Failed to persist RankedIdeaOutput: {e}")

        # --- Build output for trace ---
        rankings = []
        for rank_idx, candidate in enumerate(ranked, 1):
            rankings.append({
                "id": candidate.id,
                "title": candidate.title,
                "totalScore": round(candidate.overallScore, 2),
                "rank": rank_idx,
                "breakdown": candidate.scoreBreakdown,
                "overallRationale": getattr(candidate, 'overallRationale', ''),
                "scoringConfidence": getattr(candidate, 'scoringConfidence', 0.5),
                "scoringMethod": getattr(candidate, 'scoringMethod', 'heuristic'),
            })

        idea_review_passed_count = sum(1 for item in gate_reports.values() if item.get("passed"))
        shortlist = self._select_final_candidates(
            ranked,
            gate_reports,
            max_count=min(3, max(1, session.config.maxCandidates)),
        )
        session.finalCandidateIds = shortlist["finalCandidateIds"]
        session.hiddenCandidateIds = shortlist["hiddenCandidateIds"]
        session.rejectedCandidateIds = shortlist["rejectedCandidateIds"]
        session.qualityLoopSummary = shortlist["summary"]
        inputs = {"candidateCount": len(candidates)}
        outputs = {
            "rankings": rankings,
            "scoreVariance": round(variance, 3),
            "minScore": round(min(scores_list), 2) if scores_list else 0,
            "maxScore": round(max(scores_list), 2) if scores_list else 0,
            "rankedOutputId": ranked_output.id,
            "evidenceCount": len(evidence_list),
            "comparisonCount": len(prior_work_comparisons),
            "critiqueCount": len(critiques),
            "ideaReviewGate": list(gate_reports.values()),
            "ideaReviewPassedCount": idea_review_passed_count,
            "paperQualityGate": paper_quality_gate,
            "maxReviewIterations": max_review_iterations,
            "reviewIterationsUsed": len(review_iteration_summaries),
            "internalReviewIterations": review_iteration_summaries,
            "literatureRepairCount": len(literature_repair_reports),
            "regeneratedCandidateIds": regenerated_candidate_ids,
            "feedbackOptimizedCandidateIds": regenerated_candidate_ids,
            "finalCandidateIds": session.finalCandidateIds,
            "hiddenCandidateIds": session.hiddenCandidateIds,
            "rejectedCandidateIds": session.rejectedCandidateIds,
            "qualityLoopSummary": session.qualityLoopSummary,
        }

        if len(session.finalCandidateIds) < 2:
            raise AwaitingIdeasError(
                "Idea review gate requires at least two approved final candidates",
                inputs=inputs,
                outputs=outputs,
            )

        return inputs, outputs, []

    # =========================================================================
    # Step 6 Helpers
    # =========================================================================

    def _idea_gate_requires_literature_repair(
        self,
        review_gate: Dict[str, Any],
        paper_quality_gate: Dict[str, Any],
    ) -> bool:
        """Route idea failures back to literature repair when evidence is the root cause."""

        route = self._candidate_pool_failure_route(
            candidate=None,
            review_gate=review_gate,
            paper_quality_gate=paper_quality_gate,
        )
        return route in {"evidence_pool_bad", "novelty_unclear"}

    def _persist_repair_search_results(
        self,
        session: IdeaSession,
        results: List[SearchResult],
        *,
        search_queries: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        existing_raw = self.raw_paper_storage.list_by_session(session.id)
        existing_by_identity: Dict[str, RawPaper] = {}
        for paper in existing_raw:
            for identity in raw_paper_identity_keys(paper):
                existing_by_identity[identity] = paper
        created_raw_ids: List[str] = []
        updated_raw_ids: List[str] = []
        created_literature_ids: List[str] = []
        queries = search_queries or [session.config.seedQuery]
        role_queries = self._get_step_output(
            session,
            "expandQuery",
            "searchQueriesByRole",
            {},
        ) or {}
        if not isinstance(role_queries, dict) or not any(role_queries.values()):
            role_queries = {
                "domain": [session.config.seedQuery, session.config.domain or ""],
                "task": [session.config.seedQuery],
                "method": queries,
                "evaluation": queries,
            }
        profile = build_topic_intent_profile(
            seed=session.config.seedQuery,
            domain=session.config.domain or "",
            role_queries=role_queries,
        )
        dedupe = deduplicate_search_results(results)
        eligible: List[SearchResult] = []
        rejected: List[SearchResult] = []
        for result in dedupe.results:
            assessment = assess_search_result(result, profile)
            result.evidence_tier = assessment.tier.value
            result.decisive_anchors = list(assessment.decisive_anchors)
            result.relevance_components = dict(assessment.score_components)
            result.rejection_reason = assessment.rejection_reason
            result.relevance_score = assessment.score
            if assessment.tier is EvidenceTier.REJECTED:
                rejected.append(result)
            else:
                eligible.append(result)
        eligible.sort(
            key=lambda result: _repair_result_priority(result, session.config.paperType),
            reverse=True,
        )
        matched_existing_count = 0
        for result in eligible:
            title_hash = _compute_title_hash(result.title)
            s2_id = None
            if result.source == "semantic_scholar" and result.url:
                s2_match = re.search(r'SemanticScholarID:(\w+)', result.url)
                if s2_match:
                    s2_id = s2_match.group(1)
            existing = next(
                (
                    existing_by_identity[identity]
                    for identity in search_result_identity_keys(result)
                    if identity in existing_by_identity
                ),
                None,
            )
            if existing:
                updated = existing.model_copy(update={
                    "authors": existing.authors or result.authors,
                    "year": existing.year or result.year,
                    "venue": existing.venue or result.venue,
                    "url": existing.url or result.url or "",
                    "doi": existing.doi or result.doi,
                    "arxivId": existing.arxivId or result.arxiv_id,
                    "semanticScholarId": existing.semanticScholarId or s2_id,
                    "citationCount": max(existing.citationCount, result.citation_count or 0),
                    "abstract": (
                        result.abstract
                        if len(result.abstract or "") > len(existing.abstract or "")
                        else existing.abstract
                    ),
                    "source": list(dict.fromkeys([
                        *existing.source,
                        *(result.retrieval_sources or [result.source]),
                    ])),
                    "retrievalRoles": list(dict.fromkeys([
                        *existing.retrievalRoles,
                        *result.retrieval_roles,
                    ])),
                    "matchedQueries": list(dict.fromkeys([
                        *existing.matchedQueries,
                        *result.matched_queries,
                    ])),
                    "evidenceTier": better_evidence_tier(
                        existing.evidenceTier,
                        result.evidence_tier,
                    ),
                    "decisiveAnchors": list(dict.fromkeys([
                        *existing.decisiveAnchors,
                        *result.decisive_anchors,
                    ])),
                    "relevanceComponents": dict(result.relevance_components),
                    "rejectionReason": result.rejection_reason,
                    "mustCiteOverride": existing.mustCiteOverride or result.must_cite_override,
                    "relevanceScore": max(existing.relevanceScore, result.relevance_score),
                })
                self.raw_paper_storage.update(updated)
                updated_raw_ids.append(updated.id)
                matched_existing_count += 1
                for identity in raw_paper_identity_keys(updated):
                    existing_by_identity[identity] = updated
                continue

            raw_paper = RawPaper(
                id=generate_raw_paper_id(),
                sessionId=session.id,
                title=result.title,
                authors=result.authors,
                year=result.year,
                venue=result.venue,
                url=result.url or "",
                doi=result.doi,
                arxivId=result.arxiv_id,
                semanticScholarId=s2_id,
                citationCount=result.citation_count or 0,
                abstract=result.abstract or "",
                source=list(result.retrieval_sources or ([result.source] if result.source else [])),
                retrievalRoles=list(result.retrieval_roles),
                matchedQueries=list(result.matched_queries),
                evidenceTier=result.evidence_tier,
                decisiveAnchors=list(result.decisive_anchors),
                relevanceComponents=dict(result.relevance_components),
                rejectionReason=result.rejection_reason,
                mustCiteOverride=result.must_cite_override,
                normalizedTitleHash=title_hash,
                relevanceScore=min(1.0, max(0.0, result.relevance_score)),
            )
            self.raw_paper_storage.create(raw_paper)
            created_raw_ids.append(raw_paper.id)
            for identity in raw_paper_identity_keys(raw_paper):
                existing_by_identity[identity] = raw_paper

            lit_item = LiteratureItem(
                id=generate_literature_id(),
                sessionId=session.id,
                title=result.title,
                authors=result.authors,
                venue=result.venue,
                year=result.year,
                url=result.url or "",
                doi=result.doi,
                arxivId=result.arxiv_id,
                snippet=(result.abstract or "")[:500],
                relevanceScore=min(1.0, max(0.0, result.relevance_score)),
                source=result.source,
            )
            self.literature_storage.create(lit_item)
            created_literature_ids.append(lit_item.id)

        all_raw = self.raw_paper_storage.list_by_session(session.id)
        if all_raw:
            graph = self.graph_builder.build_graph_v0(session_id=session.id, raw_papers=all_raw)
            existing_graph = self.graph_storage.get_by_session(session.id)
            if existing_graph:
                graph = graph.model_copy(update={"id": existing_graph.id})
                self.graph_storage.update(graph)
            else:
                self.graph_storage.create(graph)
        return {
            "createdRawPaperIds": created_raw_ids,
            "updatedRawPaperIds": updated_raw_ids,
            "createdLiteratureIds": created_literature_ids,
            "filteredOutCount": len(rejected),
            "duplicateMergeCount": dedupe.merge_count + matched_existing_count,
            "evidenceTierCounts": dict(Counter(
                result.evidence_tier for result in [*eligible, *rejected]
            )),
            "rawPaperCountAfterRepair": len(all_raw),
        }

    def _repair_literature_pool_for_idea_quality(
        self,
        session: IdeaSession,
        *,
        review_gate: Dict[str, Any],
        paper_quality_gate: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Targeted literature repair used by Step 6 before regenerating an idea."""

        search_service = get_search_service()
        queries = self._build_literature_repair_queries(
            session,
            paper_quality_gate,
            existing_queries=self._get_step_output(session, "literatureSearch", "searchQueries", [session.config.seedQuery]),
        )
        results: List[SearchResult] = []
        for query in queries:
            try:
                batch = search_service.search(query, limit=max(8, min(24, session.config.maxPapers // max(1, len(queries)))))
                _tag_repair_results(batch, query)
                results.extend(batch)
            except Exception as exc:
                logger.warning("Idea-stage literature repair search failed for '%s': %s", query, exc)

        persist_report = self._persist_repair_search_results(
            session,
            results,
            search_queries=queries,
        )
        novelty_outputs: Dict[str, Any] = {}
        gap_outputs: Dict[str, Any] = {}
        try:
            _, novelty_outputs, _ = self._step_novelty_check(
                session,
                forced_raw_paper_ids=persist_report.get("createdRawPaperIds", []),
            )
            _, gap_outputs, _ = self._step_gap_analysis(session)
        except Exception as exc:
            logger.warning("Idea-stage literature repair could not rebuild downstream evidence: %s", exc, exc_info=True)
            return {
                "attempted": True,
                "queries": queries,
                "reviewGate": review_gate,
                "paperQualityGateBefore": paper_quality_gate,
                "persistReport": persist_report,
                "error": str(exc),
            }

        repaired_structured = self.structured_storage.list_by_session(session.id)
        repaired_gate = _evaluate_paper_quality_gate(
            seed=session.config.seedQuery,
            domain=session.config.domain or "",
            papers=repaired_structured,
            stage="ideaReview.literatureRepair.structured",
            extra_terms=self._core_search_queries(session),
            paper_type=session.config.paperType,
        )
        return {
            "attempted": True,
            "queries": queries,
            "reviewGate": review_gate,
            "paperQualityGateBefore": paper_quality_gate,
            "paperQualityGateAfter": repaired_gate,
            "persistReport": persist_report,
            "noveltyOutputs": {
                "selectedPaperIds": novelty_outputs.get("selectedPaperIds", []),
                "forcedRepairPaperIds": novelty_outputs.get("forcedRepairPaperIds", []),
                "forcedRepairPaperCount": novelty_outputs.get("forcedRepairPaperCount", 0),
                "structuredPaperCount": novelty_outputs.get("structuredPaperCount", 0),
                "structuredCacheHitCount": novelty_outputs.get("structuredCacheHitCount", 0),
                "deepReadRequestedCount": novelty_outputs.get("deepReadRequestedCount", 0),
                "literatureMapId": novelty_outputs.get("literatureMapId"),
            },
            "gapOutputs": {
                "reasoningKgId": gap_outputs.get("reasoningKgId"),
                "pathSeedCount": gap_outputs.get("pathSeedCount", 0),
            },
        }

    def _build_candidate_evidence(
        self,
        candidate: IdeaCandidate,
        structured_papers: List[StructuredPaper],
        reasoning_kg: ReasoningKG | None,
        path_seeds: List[ReasoningPathSeed],
        evidence_links: List[GraphEvidenceLink],
    ) -> CandidateGraphEvidence:
        """Build CandidateGraphEvidence by matching candidate to dual-graph artifacts."""
        existing_evidence = getattr(candidate, 'graphEvidence', None)

        def _existing_list(field: str) -> List[Any]:
            if isinstance(existing_evidence, dict):
                value = existing_evidence.get(field, [])
            else:
                value = getattr(existing_evidence, field, []) if existing_evidence else []
            return list(value or [])

        supporting_paper_ids: List[str] = _existing_list("supportingPaperIds")
        supporting_claim_ids: List[str] = _existing_list("supportingClaimIds")
        supporting_entity_ids: List[str] = _existing_list("supportingEntityIds")
        supporting_path_seed_ids: List[str] = _existing_list("supportingPathSeedIds")
        evidence_link_ids: List[str] = _existing_list("evidenceLinkIds")
        probe_paper_ids: List[str] = _existing_list("probePaperIds")
        reasoning_trace: List[Dict[str, Any]] = _existing_list("reasoningTrace")

        if getattr(candidate, "pathSeedId", None) and candidate.pathSeedId not in supporting_path_seed_ids:
            supporting_path_seed_ids.append(candidate.pathSeedId)

        trace_entries = []
        if getattr(candidate, "searchNodeId", None):
            trace_entries.append({"step": "IdeaSearchNode", "id": candidate.searchNodeId})
        if getattr(candidate, "pathSeedId", None):
            trace_entries.append({"step": "ReasoningPathSeed", "id": candidate.pathSeedId})
        trace_entries.append({"step": "IdeaCandidate", "id": candidate.id})
        for entry in trace_entries:
            if entry not in reasoning_trace:
                reasoning_trace.append(entry)

        # Extract keywords from candidate
        candidate_text = f"{candidate.title} {candidate.problem} {candidate.keyInsight}".lower()
        keywords = set(
            w for w in candidate_text.replace(',', ' ').split()
            if len(w) > 3 and w not in ('this', 'that', 'the', 'and', 'for', 'with', 'from')
        )

        # Match keywords against structured paper titles and claims
        for sp in structured_papers:
            paper_text = f"{sp.title} {' '.join(c.text for c in sp.claims)}".lower()
            if any(kw in paper_text for kw in keywords):
                if sp.id not in supporting_paper_ids:
                    supporting_paper_ids.append(sp.id)
                for claim in sp.claims:
                    if any(kw in claim.text.lower() for kw in keywords):
                        if claim.claimId not in supporting_claim_ids:
                            supporting_claim_ids.append(claim.claimId)

        # Match against ReasoningKG entities
        if reasoning_kg:
            for entity in reasoning_kg.entities:
                entity_text = f"{entity.name} {entity.normalizedName}".lower()
                if any(kw in entity_text for kw in keywords):
                    if entity.entityId not in supporting_entity_ids:
                        supporting_entity_ids.append(entity.entityId)

        # Match against PathSeeds via candidate reference fields
        candidate_refs = getattr(candidate, 'references', []) or []
        for ps in path_seeds:
            # Path seeds with overlapping paper IDs or anchor entities
            if any(pid in ps.sourcePaperIds for pid in supporting_paper_ids):
                supporting_path_seed_ids.append(ps.seedId)
                evidence_link_ids.extend(ps.evidenceLinkIds)

        # Dedup
        supporting_paper_ids = list(dict.fromkeys(supporting_paper_ids))
        supporting_claim_ids = list(dict.fromkeys(supporting_claim_ids))
        supporting_entity_ids = list(dict.fromkeys(supporting_entity_ids))
        evidence_link_ids = list(dict.fromkeys(evidence_link_ids))
        supporting_path_seed_ids = list(dict.fromkeys(supporting_path_seed_ids))
        probe_paper_ids = list(dict.fromkeys(probe_paper_ids))

        # Build evidence summary
        parts = []
        if supporting_paper_ids:
            parts.append(f"Supported by {len(supporting_paper_ids)} papers")
        if supporting_entity_ids:
            parts.append(f"Linked to {len(supporting_entity_ids)} KG entities")
        if supporting_path_seed_ids:
            parts.append(f"Derived from {len(supporting_path_seed_ids)} path seeds")

        return CandidateGraphEvidence(
            candidateId=candidate.id,
            supportingPaperIds=supporting_paper_ids,
            supportingClaimIds=supporting_claim_ids,
            supportingEntityIds=supporting_entity_ids,
            supportingPathSeedIds=supporting_path_seed_ids,
            evidenceLinkIds=evidence_link_ids,
            probePaperIds=probe_paper_ids,
            reasoningTrace=reasoning_trace,
            evidenceSummary=". ".join(parts) if parts else "No direct dual-graph evidence found",
        )

    def _build_ranking_literature_context(
        self,
        structured_papers: List[StructuredPaper],
        reasoning_kg: ReasoningKG | None,
        path_seeds: List[ReasoningPathSeed],
        limit: int = 8,
    ) -> str:
        """Build literature context string for LLM ranking analysis."""
        lines = []
        for i, sp in enumerate(structured_papers[:limit]):
            title = sp.title or "(untitled)"
            year = sp.year or "N/A"
            paper_id = sp.rawPaperId or sp.id
            claims_str = ". ".join(
                c.text[:120] for c in (sp.claims or [])[:2]
            ) or "N/A"
            cut_ins = [
                *sp.openQuestions[:2],
                *sp.failedAssumptions[:2],
                *sp.methodWeaknesses[:2],
                *sp.missingEvaluation[:2],
            ]
            cut_in_str = "; ".join(cut_ins[:4]) or "N/A"
            eval_str = "; ".join([
                *sp.baselineMethods[:2],
                *sp.recommendedMetrics[:2],
            ]) or "N/A"
            lines.append(
                f"[{paper_id}] {title} ({year})\n"
                f"    Claims: {claims_str}\n"
                f"    Idea cut-ins: {cut_in_str}\n"
                f"    Baselines/metrics: {eval_str}"
            )

        # Add key gaps / path seeds summary
        if path_seeds:
            seed_summaries = []
            for ps in path_seeds[:3]:
                seed_summaries.append(
                    f"  - Path: {ps.templateType}, rationale: {ps.rationale[:150]}"
                )
            if seed_summaries:
                lines.append("\nReasoning Path Seeds:")
                lines.extend(seed_summaries)

        return "\n\n".join(lines) if lines else "(No literature available)"

    def _allowed_idea_evidence_refs(
        self,
        *,
        candidate: IdeaCandidate,
        evidence: Optional[CandidateGraphEvidence],
        comparisons: List[PriorWorkComparison],
    ) -> List[str]:
        """Collect evidence IDs a reviewer is allowed to cite."""
        refs: List[str] = []
        if evidence:
            refs.extend(evidence.supportingPaperIds)
            refs.extend(evidence.supportingClaimIds)
            refs.extend(evidence.supportingEntityIds)
            refs.extend(evidence.supportingPathSeedIds)
            refs.extend(evidence.evidenceLinkIds)
            refs.extend(evidence.probePaperIds)
        for comparison in comparisons:
            refs.extend(comparison.comparedPaperIds)
        refs.extend(str(ref) for ref in (candidate.references or []))
        return [ref for ref in dict.fromkeys(str(ref).strip() for ref in refs) if ref]

    def _rule_idea_reviewer_report(
        self,
        *,
        spec: Dict[str, str],
        candidate: IdeaCandidate,
        evidence: Optional[CandidateGraphEvidence],
        comparisons: List[PriorWorkComparison],
        critique: Optional[IdeaCritique],
        seed_query: str,
        allowed_evidence_refs: List[str],
        english_search_queries: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Hard validation for one idea reviewer.

        Rules only check facts the system can verify: required fields, available
        evidence IDs, thresholds, and coarse topic alignment. Scientific judgment
        is left to the LLM reviewer.
        """
        reviewer = spec["name"]
        blocking: List[str] = []
        repair: List[str] = []
        score = 8.0

        missing_fields = [
            field for field, value in {
                "title": candidate.title,
                "problem": candidate.problem,
                "keyInsight": candidate.keyInsight,
            }.items()
            if not str(value or "").strip()
        ]
        if missing_fields:
            blocking.append(f"Missing required candidate fields: {', '.join(missing_fields)}.")
            repair.append("Regenerate the candidate with complete title, problem, and keyInsight fields.")
            score -= 2.0

        seed_tokens = _candidate_similarity_key(IdeaCandidate(
            id="cand_seed_alignment_check",
            sessionId=candidate.sessionId,
            title=seed_query or "",
            problem=seed_query or "",
            keyInsight=seed_query or "",
        ))
        # Bug 8 fix: When the seed query contains CJK characters, the seed tokens
        # are Chinese while candidate tokens are English (because candidates are
        # generated from English literature). This causes Jaccard similarity to
        # always be 0, triggering a false "weak topic overlap" blocking issue.
        # Fix: augment seed tokens with English search queries from expandQuery.
        if english_search_queries and re.search(r'[\u4e00-\u9fff]', seed_query or ""):
            for eq in english_search_queries:
                seed_tokens |= _candidate_similarity_key(IdeaCandidate(
                    id="cand_seed_english",
                    sessionId=candidate.sessionId,
                    title=eq,
                    problem=eq,
                    keyInsight=eq,
                ))
        candidate_tokens = _candidate_similarity_key(candidate)
        # Bug 8b fix: For CJK seeds, candidates with long text fields can dilute
        # the Jaccard below 0.04 even when they are on-topic. Add a containment
        # fallback: if any significant English query token appears in the
        # candidate tokens, the candidate is considered on-topic.
        _cjk_seed_has_english_overlap = False
        if english_search_queries and re.search(r'[\u4e00-\u9fff]', seed_query or ""):
            _english_query_tokens = set()
            for eq in english_search_queries:
                _english_query_tokens |= _candidate_similarity_key(IdeaCandidate(
                    id="cand_eq",
                    sessionId=candidate.sessionId,
                    title=eq,
                    problem=eq,
                    keyInsight=eq,
                ))
            # Remove generic tokens that are not discriminative
            _generic_tokens = {"analysis", "network", "social", "study", "empirical"}
            _discriminative_overlap = (candidate_tokens & _english_query_tokens) - _generic_tokens
            _cjk_seed_has_english_overlap = len(_discriminative_overlap) >= 2
        if (seed_query and seed_tokens and candidate_tokens
                and not _cjk_seed_has_english_overlap
                and _candidate_jaccard(seed_tokens, candidate_tokens) < 0.04):
            blocking.append("Candidate has weak topic overlap with the seed query.")
            repair.append("Rewrite the idea so the problem, method, and hypothesis directly answer the seed query.")
            score -= 1.0
        drift_issues = _candidate_topic_drift_issues(seed_query, candidate, english_search_queries=english_search_queries)
        if drift_issues:
            blocking.extend(drift_issues)
            repair.append(
                "Regenerate the candidate from the original seed query and remove unrequested application-domain anchors."
            )
            score -= min(1.5, 0.75 * len(drift_issues))

        if reviewer == "IdeaEvidenceReviewer":
            if not allowed_evidence_refs:
                blocking.append("No valid evidence IDs are available for this candidate.")
                repair.append("Bind the candidate to supporting papers, claims, KG entities, path seeds, or probe papers.")
                score -= 2.0
            if candidate.referenceSupport < 4.5:
                blocking.append("Reference support score is below the handoff threshold.")
                repair.append("Strengthen the evidence grounding before exposing this candidate.")
                score -= 1.0

        if reviewer == "IdeaNoveltyReviewer":
            if candidate.novelty < 5.5:
                blocking.append("Novelty score is below the handoff threshold.")
                repair.append("Clarify the concrete mechanism, setting, or evaluation difference from prior work.")
                score -= 1.0
            if not any(item.differences for item in comparisons):
                blocking.append("No concrete prior-work difference is recorded.")
                repair.append("Add an explicit closest-prior-work contrast with evidence-backed differences.")
                score -= 1.0

        if reviewer == "IdeaFeasibilityReviewer":
            if candidate.feasibility < 5.5:
                blocking.append("Feasibility score is below the handoff threshold.")
                repair.append("Make the method implementable with clear modules, inputs, outputs, and likely resources.")
                score -= 1.0
            if not (candidate.proposedMethod or (candidate.draftPlan and candidate.draftPlan.methodology)):
                blocking.append("Candidate has no proposed method.")
                repair.append("Add a concrete method sketch before planning.")
                score -= 1.0

        if reviewer == "IdeaSpecificityReviewer":
            if candidate.clarity < 5.0 or candidate.experimentSpecificity < 5.0:
                blocking.append("Specificity scores are below the handoff threshold.")
                repair.append("Specify variables, expected metrics, datasets, and validation steps.")
                score -= 1.0
            if not candidate.hypothesisStatement:
                blocking.append("Candidate has no explicit hypothesisStatement.")
                repair.append("Write a testable hypothesis that links method to expected outcome.")
                score -= 0.8
            if not candidate.expectedOutcome:
                blocking.append("Candidate has no expectedOutcome.")
                repair.append("State what measurable success should look like.")
                score -= 0.8

        if reviewer == "IdeaImpactReviewer":
            if candidate.impact < 5.5:
                blocking.append("Impact score is below the handoff threshold.")
                repair.append("Sharpen the contribution statement and target scientific value.")
                score -= 1.0
            if critique and len(critique.weaknesses) >= 4:
                blocking.append("Critique contains too many unresolved weaknesses for impact handoff.")
                repair.append("Resolve or narrow the weakest claims before exposing this idea.")
                score -= 0.8

        passed = not blocking
        return {
            "reviewer": reviewer,
            "mode": "rule",
            "score": round(max(0.0, min(10.0, score)), 2),
            "pass": passed,
            "passed": passed,
            "blockingIssues": blocking,
            "repairInstructions": list(dict.fromkeys(repair)),
            "evidenceRefs": allowed_evidence_refs[:8],
            "confidence": 0.9,
            "summary": "Rule hard validation passed." if passed else "Rule hard validation failed.",
        }

    def _run_llm_idea_reviewer(
        self,
        *,
        spec: Dict[str, str],
        candidate: IdeaCandidate,
        evidence: Optional[CandidateGraphEvidence],
        comparisons: List[PriorWorkComparison],
        critique: Optional[IdeaCritique],
        seed_query: str,
        provider_name: Optional[str],
        model: Optional[str],
        literature_context: str,
        allowed_evidence_refs: List[str],
    ) -> Optional[Dict[str, Any]]:
        """Run one independent LLM reviewer and normalize its JSON response."""
        if not provider_name or not model:
            return None

        reviewer = spec["name"]
        payload = {
            "reviewer": reviewer,
            "reviewFocus": spec["focus"],
            "rubric": spec["rubric"],
            "seedQuery": seed_query,
            "candidate": {
                "id": candidate.id,
                "title": candidate.title,
                "problem": candidate.problem,
                "hypothesisStatement": candidate.hypothesisStatement,
                "keyInsight": candidate.keyInsight,
                "proposedMethod": candidate.proposedMethod,
                "expectedOutcome": candidate.expectedOutcome,
                "scores": candidate.scoreBreakdown,
                "expectedMetrics": candidate.expectedMetrics,
                "experimentSpecs": [
                    spec_item.model_dump() if hasattr(spec_item, "model_dump") else spec_item
                    for spec_item in candidate.experimentSpecs[:5]
                ],
            },
            "allowedEvidenceRefs": allowed_evidence_refs,
            "candidateGraphEvidence": evidence.model_dump() if evidence else {},
            "priorWorkComparisons": [item.model_dump() for item in comparisons[:3]],
            "critique": critique.model_dump() if critique else {},
            "literatureContext": literature_context[:6000],
        }
        cache_storage = getattr(self, "llm_task_cache_storage", None)
        cache_task_type = f"idea_reviewer:{reviewer}"
        if cache_storage:
            try:
                cached = cache_storage.get_valid(
                    task_type=cache_task_type,
                    prompt_version=IDEA_REVIEWER_CACHE_PROMPT_VERSION,
                    model=model,
                    input_payload=payload,
                )
                if cached and isinstance(cached.get("result"), dict):
                    result = dict(cached["result"])
                    result["cacheHit"] = True
                    result["cacheKey"] = cached.get("cacheKey")
                    return result
            except Exception as exc:
                logger.warning("%s LLM idea reviewer cache lookup failed: %s", reviewer, exc)

        messages = [
            ChatMessage(
                role="system",
                content=(
                    f"You are {reviewer}, an independent LLM scientist reviewer. "
                    "Return JSON only. Judge only your assigned dimension. "
                    "Do not invent evidence IDs. evidenceRefs must be chosen from allowedEvidenceRefs. "
                    "The candidate prose may omit raw evidence IDs; candidateGraphEvidence and "
                    "allowedEvidenceRefs are the machine-readable citation binding. Do not fail solely "
                    "because IDs are not inline in the prose. Fail evidence only when the provided IDs "
                    "are missing, invalid, unrelated, or insufficient for the core claims. "
                    "Use this schema exactly: score:number 0-10, pass:boolean, "
                    "blockingIssues:string[], repairInstructions:string[], evidenceRefs:string[], "
                    "confidence:number 0-1, summary:string."
                ),
            ),
            ChatMessage(
                role="user",
                content=json.dumps(payload, ensure_ascii=False, default=str),
            ),
        ]
        try:
            scheduler = getattr(self, "llm_task_scheduler", None) or get_llm_task_scheduler()
            response = scheduler.run(
                cache_task_type,
                lambda: get_provider_client(provider_name).chat(
                    messages,
                    model=model,
                    temperature=0.0,
                    max_tokens=1000,
                    response_format={"type": "json_object"},
                ),
            )
        except Exception as exc:
            logger.warning("%s LLM idea reviewer failed: %s", reviewer, exc)
            return None

        data = _extract_json_object(response.text or "")
        if not data:
            logger.warning("%s LLM idea reviewer returned non-JSON output", reviewer)
            return None

        score = _score_0_10(data.get("score", data.get("rating", 0.0)))
        passed = _bool_from_review(data.get("pass", data.get("passed")), default=score >= 6.0)
        report = {
            "reviewer": reviewer,
            "mode": "llm",
            "score": score,
            "pass": passed,
            "passed": passed,
            "blockingIssues": _as_string_list(data.get("blockingIssues", data.get("issues", [])), limit=8),
            "repairInstructions": _as_string_list(
                data.get("repairInstructions", data.get("suggestedImprovements", [])),
                limit=8,
            ),
            "evidenceRefs": _as_string_list(data.get("evidenceRefs", data.get("evidenceRefIds", [])), limit=12),
            "confidence": _confidence_0_1(data.get("confidence", data.get("reviewConfidence", 0.5))),
            "summary": str(data.get("summary", data.get("rationale", "")) or ""),
            "llmLatencyMs": getattr(response, "latency_ms", None),
            "cacheHit": False,
        }
        if cache_storage:
            try:
                cache_key = cache_storage.put(
                    task_type=cache_task_type,
                    prompt_version=IDEA_REVIEWER_CACHE_PROMPT_VERSION,
                    model=model,
                    input_payload=payload,
                    result=report,
                )
                report["cacheKey"] = cache_key
            except Exception as exc:
                logger.warning("%s LLM idea reviewer cache store failed: %s", reviewer, exc)
        return report

    def _merge_idea_reviewer_reports(
        self,
        *,
        spec: Dict[str, str],
        rule_report: Dict[str, Any],
        llm_report: Optional[Dict[str, Any]],
        allowed_evidence_refs: List[str],
    ) -> Dict[str, Any]:
        """Merge LLM scientific judgment with rule hard validation."""
        reviewer = spec["name"]
        if not llm_report:
            return rule_report

        allowed = set(allowed_evidence_refs)
        raw_refs = [str(ref).strip() for ref in llm_report.get("evidenceRefs", []) if str(ref).strip()]
        verified_refs = [ref for ref in raw_refs if ref in allowed]
        unknown_refs = [ref for ref in raw_refs if ref not in allowed]
        rule_blocking = list(rule_report.get("blockingIssues", []))
        llm_blocking = list(llm_report.get("blockingIssues", []))
        downgraded_format_issues: List[str] = []
        if (
            reviewer == "IdeaEvidenceReviewer"
            and verified_refs
            and not rule_blocking
        ):
            hard_llm_blocking: List[str] = []
            for issue in llm_blocking:
                if _is_inline_evidence_format_issue(str(issue)):
                    downgraded_format_issues.append(str(issue))
                else:
                    hard_llm_blocking.append(issue)
            llm_blocking = hard_llm_blocking

        blocking = [
            *rule_blocking,
            *llm_blocking,
        ]
        if unknown_refs:
            blocking.append(
                "LLM reviewer referenced unknown evidence IDs: "
                + ", ".join(unknown_refs[:5])
            )

        repair = [
            *rule_report.get("repairInstructions", []),
            *llm_report.get("repairInstructions", []),
            *downgraded_format_issues,
        ]
        score = _score_0_10(llm_report.get("score", 0.0))
        if downgraded_format_issues and not blocking and not unknown_refs:
            score = max(score, _score_0_10(rule_report.get("score", 0.0)), 6.2)
        llm_passed = bool(llm_report.get("pass", llm_report.get("passed", False)))
        if downgraded_format_issues and not blocking and not unknown_refs:
            llm_passed = True
        passed = (
            llm_passed
            and score >= 6.0
            and not blocking
        )
        evidence_refs = verified_refs or list(rule_report.get("evidenceRefs", []))[:8]
        return {
            "reviewer": reviewer,
            "mode": "llm+rule",
            "score": score,
            "pass": passed,
            "passed": passed,
            "blockingIssues": list(dict.fromkeys(str(item) for item in blocking if str(item).strip())),
            "repairInstructions": list(dict.fromkeys(str(item) for item in repair if str(item).strip())),
            "evidenceRefs": evidence_refs,
            "confidence": _confidence_0_1(llm_report.get("confidence", 0.5)),
            "summary": llm_report.get("summary") or rule_report.get("summary", ""),
            "llmLatencyMs": llm_report.get("llmLatencyMs"),
            "cacheHit": bool(llm_report.get("cacheHit", False)),
            "cacheKey": llm_report.get("cacheKey"),
        }

    def _apply_idea_review_gate(
        self,
        *,
        ranked: List[IdeaCandidate],
        evidence_list: List[CandidateGraphEvidence],
        prior_work_comparisons: List[PriorWorkComparison],
        critiques: List[IdeaCritique],
        seed_query: str = "",
        provider_name: Optional[str] = None,
        model: Optional[str] = None,
        literature_context: str = "",
        english_search_queries: Optional[List[str]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Apply idea-stage review findings before PlanPackage generation."""

        evidence_by_candidate = {item.candidateId: item for item in evidence_list}
        critique_by_candidate = {item.candidateId: item for item in critiques}
        comparisons_by_candidate: Dict[str, List[PriorWorkComparison]] = {}
        for comparison in prior_work_comparisons:
            comparisons_by_candidate.setdefault(comparison.candidateId, []).append(comparison)

        reports: Dict[str, Dict[str, Any]] = {}
        for candidate in ranked:
            evidence = evidence_by_candidate.get(candidate.id)
            critique = critique_by_candidate.get(candidate.id)
            comparisons = comparisons_by_candidate.get(candidate.id, [])
            blocking: List[str] = []
            warnings: List[str] = []
            suggestions: List[str] = []
            penalty = 0.0
            support_count = 0
            has_difference = False
            avg_comparison_confidence = 0.0
            critique_weakness_count = 0
            critique_failure_count = 0

            if evidence:
                support_count = (
                    len(evidence.supportingPaperIds)
                    + len(evidence.supportingEntityIds)
                    + len(evidence.supportingPathSeedIds)
                )
                if support_count == 0:
                    blocking.append("No paper, KG entity, or path seed supports this idea candidate.")
                    suggestions.append("Regenerate the idea so its core mechanism is directly grounded in cited papers, KG entities, or reasoning path seeds.")
                    penalty += 1.2
                elif not evidence.supportingPaperIds:
                    warnings.append("Candidate lacks direct supporting paper IDs.")
                    suggestions.append("Add a clearer connection between the idea and specific supporting papers or paper limitations.")
                    penalty += 0.4
            else:
                blocking.append("Candidate has no graph evidence binding.")
                suggestions.append("Regenerate the idea around evidence that can be bound to the literature graph.")
                penalty += 1.2

            if critique:
                critique_weakness_count = len(critique.weaknesses)
                critique_failure_count = len(critique.failureModes)
                if critique.critiqueConfidence < 0.45:
                    warnings.append("LLM critique confidence is low.")
                    penalty += 0.3
                if len(critique.weaknesses) >= 3:
                    warnings.append("Candidate has multiple critique weaknesses.")
                    penalty += 0.5
                if len(critique.failureModes) >= 3:
                    warnings.append("Candidate has multiple failure modes.")
                    penalty += 0.4
                suggestions.extend(critique.suggestedImprovements[:4])
            else:
                warnings.append("Candidate was not reviewed by IdeaCritique.")
                penalty += 0.4

            if comparisons:
                has_difference = any(item.differences for item in comparisons)
                avg_comparison_confidence = sum(item.comparisonConfidence for item in comparisons) / max(1, len(comparisons))
                if not has_difference:
                    warnings.append("Prior-work comparison does not state concrete differences.")
                    suggestions.append("Sharpen the novelty claim by stating exactly how the idea differs from closest prior work.")
                    penalty += 0.5
                if avg_comparison_confidence < 0.45:
                    warnings.append("Prior-work comparison confidence is low.")
                    suggestions.append("Ground the idea in more explicit prior-work contrasts and avoid vague novelty claims.")
                    penalty += 0.3
            else:
                warnings.append("Candidate has no prior-work comparison.")
                suggestions.append("Regenerate with an explicit closest-prior-work comparison and concrete difference.")
                penalty += 0.4

            if candidate.novelty < 5.5:
                warnings.append("Novelty score is below the idea-stage handoff threshold.")
                suggestions.append("Increase research value by making the new mechanism, setting, or evaluation contribution more explicit.")
            if candidate.referenceSupport < 5.0:
                warnings.append("Reference support score is below the idea-stage handoff threshold.")
                suggestions.append("Tie the hypothesis and method to stronger supporting literature evidence.")

            allowed_evidence_refs = self._allowed_idea_evidence_refs(
                candidate=candidate,
                evidence=evidence,
                comparisons=comparisons,
            )
            rule_reports: Dict[str, Dict[str, Any]] = {}
            for spec in IDEA_REVIEWER_SPECS:
                rule_reports[spec["name"]] = self._rule_idea_reviewer_report(
                    spec=spec,
                    candidate=candidate,
                    evidence=evidence,
                    comparisons=comparisons,
                    critique=critique,
                    seed_query=seed_query,
                    allowed_evidence_refs=allowed_evidence_refs,
                    english_search_queries=english_search_queries or [],
                )

            llm_reports: Dict[str, Optional[Dict[str, Any]]] = {}
            reviewer_concurrency = _idea_reviewer_concurrency()

            def _run_one_llm_reviewer(spec: Dict[str, str]) -> tuple[str, Optional[Dict[str, Any]]]:
                return spec["name"], self._run_llm_idea_reviewer(
                    spec=spec,
                    candidate=candidate,
                    evidence=evidence,
                    comparisons=comparisons,
                    critique=critique,
                    seed_query=seed_query,
                    provider_name=provider_name,
                    model=model,
                    literature_context=literature_context,
                    allowed_evidence_refs=allowed_evidence_refs,
                )

            if provider_name and model and reviewer_concurrency > 1:
                with ThreadPoolExecutor(
                    max_workers=reviewer_concurrency,
                    thread_name_prefix="idea-reviewer",
                ) as executor:
                    future_to_spec = {
                        executor.submit(_run_one_llm_reviewer, spec): spec
                        for spec in IDEA_REVIEWER_SPECS
                    }
                    for future in as_completed(future_to_spec):
                        spec = future_to_spec[future]
                        try:
                            reviewer_name, llm_report = future.result()
                        except Exception as exc:
                            logger.warning("%s LLM idea reviewer crashed: %s", spec["name"], exc)
                            reviewer_name, llm_report = spec["name"], None
                        llm_reports[reviewer_name] = llm_report
            else:
                for spec in IDEA_REVIEWER_SPECS:
                    reviewer_name, llm_report = _run_one_llm_reviewer(spec)
                    llm_reports[reviewer_name] = llm_report

            reviewer_reports: List[Dict[str, Any]] = []
            for spec in IDEA_REVIEWER_SPECS:
                reviewer_name = spec["name"]
                reviewer_reports.append(self._merge_idea_reviewer_reports(
                    spec=spec,
                    rule_report=rule_reports[reviewer_name],
                    llm_report=llm_reports.get(reviewer_name),
                    allowed_evidence_refs=allowed_evidence_refs,
                ))
            suspect_cache_keys = self._mark_structured_cache_suspect_from_reviewer_reports(
                evidence=evidence,
                reviewer_reports=reviewer_reports,
            )

            reviewer_blocking = [
                issue
                for report in reviewer_reports
                for issue in report.get("blockingIssues", [])
            ]
            reviewer_repairs = [
                instruction
                for report in reviewer_reports
                for instruction in report.get("repairInstructions", [])
            ]
            if reviewer_blocking:
                blocking.extend(reviewer_blocking)
                penalty += min(1.5, len(reviewer_blocking) * 0.25)
            suggestions.extend(reviewer_repairs)
            failed_reviewer_count = sum(1 for report in reviewer_reports if not report.get("passed", False))
            if failed_reviewer_count:
                penalty += min(1.0, failed_reviewer_count * 0.2)

            if penalty and "idea_review_gate" not in (candidate.scoringMethod or ""):
                candidate.referenceSupport = max(0.0, candidate.referenceSupport - penalty)
                candidate.feasibility = max(0.0, candidate.feasibility - min(0.8, penalty * 0.35))
                candidate.risk = max(0.0, candidate.risk - min(0.8, penalty * 0.30))
                if any("difference" in item.lower() or "novelty" in item.lower() for item in warnings):
                    candidate.novelty = max(0.0, candidate.novelty - min(0.8, penalty * 0.35))

            passed = (
                not blocking
                and all(report.get("passed", False) for report in reviewer_reports)
                and candidate.overallScore >= 6.0
                and candidate.referenceSupport >= 4.5
            )
            summary = "Idea review gate passed." if passed else "Idea review gate requires regeneration or another candidate."
            if warnings:
                summary += " Warnings: " + "; ".join(warnings[:3])
            if blocking:
                summary += " Blocking: " + "; ".join(blocking[:3])
            rationale_lines = [
                line
                for line in (candidate.overallRationale or "").splitlines()
                if not line.startswith("Idea review gate ")
            ]
            base_rationale = "\n".join(line for line in rationale_lines if line.strip()).strip()
            candidate.overallRationale = f"{base_rationale}\n{summary}".strip() if base_rationale else summary
            candidate.scoringMethod = (
                candidate.scoringMethod
                if "idea_review_gate" in candidate.scoringMethod
                else f"{candidate.scoringMethod}+idea_review_gate"
            )
            reports[candidate.id] = {
                "candidateId": candidate.id,
                "passed": passed,
                "scoreAfterGate": round(candidate.overallScore, 2),
                "blockingIssues": blocking,
                "warnings": warnings,
                "suggestedImprovements": list(dict.fromkeys(suggestions)),
                "reviewerReports": reviewer_reports,
                "suspectStructuredCacheKeys": suspect_cache_keys,
                "priorWorkComparisonConfidence": round(avg_comparison_confidence, 3),
                "needsFeedbackOptimization": self._should_optimize_candidate_from_gate(candidate, {
                    "passed": passed,
                    "scoreAfterGate": round(candidate.overallScore, 2),
                    "blockingIssues": blocking,
                    "warnings": warnings,
                    "suggestedImprovements": suggestions,
                }),
            }

        return reports

    def _should_optimize_candidate_from_gate(
        self,
        candidate: IdeaCandidate,
        review_gate: Dict[str, Any],
    ) -> bool:
        """Decide whether idea-stage feedback should create an improved candidate."""

        if "llm_regenerated_from_idea_review" in (candidate.scoringMethod or ""):
            return False
        if review_gate.get("blockingIssues"):
            return True
        if review_gate.get("suggestedImprovements"):
            return True
        warnings = " ".join(str(item) for item in review_gate.get("warnings", [])).lower()
        idea_warning_terms = [
            "supporting paper",
            "prior-work",
            "novelty",
            "reference support",
            "critique",
            "failure mode",
            "evidence",
        ]
        return any(term in warnings for term in idea_warning_terms)

    def _regenerate_candidate_from_review(
        self,
        *,
        session: IdeaSession,
        base_candidate: IdeaCandidate,
        review_gate: Dict[str, Any],
        critique: Optional[IdeaCritique],
        prior_work: List[PriorWorkComparison],
        literature_context: str,
    ) -> Optional[IdeaCandidate]:
        """Generate one improved candidate from idea-stage review feedback."""

        client = get_provider_client(session.config.providerName)
        review_context = {
            "sourceCandidate": {
                "title": base_candidate.title,
                "problem": base_candidate.problem,
                "hypothesisStatement": base_candidate.hypothesisStatement,
                "keyInsight": base_candidate.keyInsight,
                "proposedMethod": base_candidate.proposedMethod,
                "expectedOutcome": base_candidate.expectedOutcome,
                "scores": base_candidate.scoreBreakdown,
            },
            "reviewGate": review_gate,
            "critique": critique.model_dump() if critique else {},
            "priorWork": [item.model_dump() for item in prior_work[:3]],
            "seedQuery": session.config.seedQuery,
            "domain": session.config.domain,
            "paperType": session.config.paperType,
            "researchDirection": {
                "id": self._candidate_direction_id(base_candidate),
                "type": self._candidate_direction_type(base_candidate),
                "title": self._candidate_direction_title(base_candidate),
                "notes": base_candidate.draftPlan.notes if base_candidate.draftPlan else "",
            },
            "literatureContext": literature_context[:8000],
        }
        messages = [
            ChatMessage(
                role="system",
                content=(
                    "You regenerate one stronger research idea from idea-stage review findings. "
                    "Return JSON only. Do not claim executed experiments. Do not invent paper IDs. "
                    "The new idea must preserve useful parts of the source candidate while directly addressing "
                    "reviewGate warnings, blocking issues, and suggested improvements. "
                    "If researchDirection is provided, keep the regenerated idea inside that direction; "
                    "for example, a benchmark direction should remain a benchmark idea."
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    "Return exactly this JSON shape:\n"
                    '{"ideas":[{"title":"","problem":"","keyInsight":"","approach":"","expectedOutcomes":[],"risks":[],'
                    '"requiredExperiments":[]}]}'
                    "\nContext:\n"
                    f"{json.dumps(review_context, ensure_ascii=False, default=str)}"
                ),
            ),
        ]
        response = client.chat(
            messages,
            model=session.config.model,
            temperature=0.25,
            max_tokens=2400,
            response_format={"type": "json_object"},
        )
        candidates = self._parse_ideas_json(session.id, response.text or "", 1)
        if not candidates:
            candidates = self._parse_ideas(session.id, response.text or "", 1)
        if not candidates:
            return None
        candidate = candidates[0]
        candidate.references = list(base_candidate.references)
        self._copy_candidate_direction_metadata(source=base_candidate, target=candidate)
        candidate.overallRationale = "Regenerated automatically from idea-stage review feedback."
        candidate.scoringMethod = "llm_regenerated_from_idea_review"
        return candidate

    def _llm_analyze_candidate(
        self,
        candidate: IdeaCandidate,
        seed_query: str,
        paper_type: str,
        domain: str,
        literature_context: str,
        provider_name: str,
        model: str,
    ) -> tuple[PriorWorkComparison, IdeaCritique]:
        """Run LLM analysis for PriorWorkComparison + IdeaCritique in one call."""
        user_prompt = RANK_CANDIDATE_ANALYSIS_USER.format(
            seed_query=seed_query,
            paper_type=paper_type,
            domain=domain,
            title=candidate.title,
            problem=candidate.problem,
            key_insight=candidate.keyInsight,
            approach=candidate.draftPlan.methodology if candidate.draftPlan else "Not specified",
            literature_context=literature_context,
        )

        messages = [
            ChatMessage(role="system", content=RANK_CANDIDATE_ANALYSIS_SYSTEM),
            ChatMessage(role="user", content=user_prompt),
        ]

        client = get_provider_client(provider_name)
        response = client.chat(messages, model=model, max_tokens=1200)

        # Parse JSON response
        try:
            text = response.text.strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]
            data = json.loads(text)
        except (json.JSONDecodeError, AttributeError, IndexError):
            # Try regex fallback
            json_match = re.search(r'\{[\s\S]*\}', response.text)
            if json_match:
                try:
                    data = json.loads(json_match.group())
                except json.JSONDecodeError:
                    return self._fallback_analysis(candidate)
            else:
                return self._fallback_analysis(candidate)

        prior_work = PriorWorkComparison(
            candidateId=candidate.id,
            comparedPaperIds=data.get("comparedPaperIds", []),
            differences=data.get("differences", []),
            advantages=data.get("advantages", []),
            risks=data.get("risks", []),
            overallAssessment=data.get("overallAssessment", ""),
            comparisonConfidence=float(data.get("comparisonConfidence", 0.7)),
        )

        critique = IdeaCritique(
            candidateId=candidate.id,
            strengths=data.get("strengths", []),
            weaknesses=data.get("weaknesses", []),
            assumptions=data.get("assumptions", []),
            failureModes=data.get("failureModes", []),
            suggestedImprovements=data.get("suggestedImprovements", []),
            overallCritique=data.get("overallCritique", ""),
            critiqueConfidence=float(data.get("critiqueConfidence", 0.7)),
        )

        fallback_prior, fallback_critique = self._fallback_analysis(candidate)
        prior_updates: Dict[str, Any] = {}
        if not prior_work.comparedPaperIds:
            prior_updates["comparedPaperIds"] = fallback_prior.comparedPaperIds
        if not prior_work.differences:
            prior_updates["differences"] = fallback_prior.differences
        if not prior_work.advantages:
            prior_updates["advantages"] = fallback_prior.advantages
        if prior_work.comparisonConfidence < 0.45:
            prior_updates["comparisonConfidence"] = max(
                prior_work.comparisonConfidence,
                fallback_prior.comparisonConfidence,
            )
        if prior_updates:
            prior_work = prior_work.model_copy(update=prior_updates)

        critique_updates: Dict[str, Any] = {}
        if not critique.strengths:
            critique_updates["strengths"] = fallback_critique.strengths
        if not critique.weaknesses:
            critique_updates["weaknesses"] = fallback_critique.weaknesses
        if not critique.suggestedImprovements:
            critique_updates["suggestedImprovements"] = fallback_critique.suggestedImprovements
        if critique.critiqueConfidence < 0.45:
            critique_updates["critiqueConfidence"] = max(
                critique.critiqueConfidence,
                fallback_critique.critiqueConfidence,
            )
        if critique_updates:
            critique = critique.model_copy(update=critique_updates)

        return prior_work, critique

    def _fallback_analysis(
        self, candidate: IdeaCandidate
    ) -> tuple[PriorWorkComparison, IdeaCritique]:
        """Generate fallback analysis when LLM fails."""
        graph_evidence = getattr(candidate, "graphEvidence", None)
        evidence_paper_ids = []
        if graph_evidence:
            evidence_paper_ids = list(getattr(graph_evidence, "supportingPaperIds", []) or [])
        compared_ids: List[str] = []
        for paper_id in [*(candidate.references or []), *evidence_paper_ids]:
            text = str(paper_id).strip()
            if text and text not in compared_ids:
                compared_ids.append(text)
            if len(compared_ids) >= 5:
                break

        method = (
            candidate.proposedMethod
            or candidate.keyInsight
            or (candidate.draftPlan.methodology if candidate.draftPlan else "")
            or candidate.title
        )
        has_evidence = bool(compared_ids)
        prior_work = PriorWorkComparison(
            candidateId=candidate.id,
            comparedPaperIds=compared_ids,
            differences=[
                (
                    f"The candidate proposes {method[:160]} for the stated problem; "
                    "this needs to be contrasted against the cited evidence papers."
                )
            ] if has_evidence else [],
            advantages=[
                "The candidate is at least grounded to explicit evidence IDs from the idea graph."
            ] if has_evidence else [],
            risks=[
                "Closest-prior-work contrast was generated heuristically because LLM analysis was unavailable."
            ],
            overallAssessment=(
                "LLM analysis unavailable; fallback used candidate evidence links for a minimal prior-work comparison."
                if has_evidence
                else "LLM analysis unavailable; no evidence references were available for fallback comparison."
            ),
            comparisonConfidence=0.55 if has_evidence else 0.3,
        )
        critique = IdeaCritique(
            candidateId=candidate.id,
            strengths=[
                f"Addresses {candidate.problem[:100]}",
                "Carries explicit evidence links that downstream modules can inspect.",
            ] if has_evidence else [f"Addresses {candidate.problem[:100]}"],
            weaknesses=[
                "Heuristic review only; rerun LLM candidate analysis for a stronger novelty critique."
            ],
            assumptions=[
                "Referenced evidence papers are relevant to the core mechanism."
            ] if has_evidence else [],
            failureModes=[
                "The idea may still be incremental if the cited papers already implement the same mechanism."
            ] if has_evidence else [],
            suggestedImprovements=[
                "State the exact mechanism difference from each cited prior-work paper.",
                "Tie evaluation metrics to the cited evidence gaps.",
            ] if has_evidence else [
                "Add evidence references before handing the idea to plan generation."
            ],
            overallCritique=(
                "LLM critique unavailable; fallback critique is evidence-grounded but should be reviewed."
                if has_evidence
                else "LLM critique unavailable."
            ),
            critiqueConfidence=0.55 if has_evidence else 0.3,
        )
        return prior_work, critique
    
    def _step_finalize(self, session: IdeaSession) -> tuple:
        """Finalize the session."""
        candidates = self.get_candidates(session.id)
        literature = self.get_literature(session.id)
        
        inputs = {}
        outputs = {
            "totalCandidates": len(candidates),
            "totalLiterature": len(literature),
            "topCandidate": candidates[0].title if candidates else None,
        }
        
        return inputs, outputs, []


# Global service instance
_service: Optional[IdeaGenerationService] = None


def get_idea_service() -> IdeaGenerationService:
    global _service
    if _service is None:
        _service = IdeaGenerationService()
    return _service


# =============================================================================
# Step 6 Prompt Templates: Candidate Analysis (PriorWorkComparison + IdeaCritique)
# =============================================================================

RANK_CANDIDATE_ANALYSIS_SYSTEM = """You are a research reviewer evaluating a candidate research idea against existing literature.

Your task is to:
1. Compare this idea with the provided literature context — identify key differences and advantages
2. Critique the idea — identify strengths, weaknesses, assumptions, and failure modes
3. Suggest concrete improvements

Be specific and evidence-based. Reference the exact raw paper IDs shown in square brackets
from the literature context when relevant.
Do NOT fabricate paper IDs or evidence links — only reference what is explicitly provided.

Respond ONLY with valid JSON in this exact format:
{
  "comparedPaperIds": ["raw paper IDs from context, e.g. raw_abcd1234"],
  "differences": ["how this idea differs from prior work — be specific"],
  "advantages": ["advantages over existing approaches"],
  "risks": ["risks relative to established methods"],
  "overallAssessment": "Brief overall comparison assessment",
  "comparisonConfidence": 0.85,
  "strengths": ["key strengths of the idea"],
  "weaknesses": ["identified weaknesses or gaps"],
  "assumptions": ["implicit or explicit assumptions the idea makes"],
  "failureModes": ["ways the idea could fail or be disproven"],
  "suggestedImprovements": ["concrete suggestions for strengthening the idea"],
  "overallCritique": "Brief overall critique summary",
  "critiqueConfidence": 0.85
}"""

RANK_CANDIDATE_ANALYSIS_USER = """Evaluate this candidate research idea:

**Research Domain:** {domain}
**Seed Topic:** {seed_query}
**Paper Type:** {paper_type}

**Idea Title:** {title}

**Problem Statement:** {problem}

**Key Insight:** {key_insight}

**Proposed Approach:** {approach}

**Existing Literature Context:**
{literature_context}

Provide a thorough comparison with existing work and a structured critique of this idea.
Be specific — reference the exact raw paper IDs from the literature context in your comparison."""
