"""
Idea Generation Service

Orchestrates the idea generation pipeline with step-based tracing.
"""

import logging
import os
import threading
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
from app.llm.provider_client import get_provider_client, ChatMessage, ProviderError
from app.services.search_service import get_search_service, SearchResult
from app.services.ranking_service import get_ranking_service
from app.services import prompts
import json
import re

logger = logging.getLogger(__name__)


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
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw)
        if match:
            try:
                data = json.loads(match.group())
                return data if isinstance(data, dict) else None
            except json.JSONDecodeError:
                return None
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
    phrase_bonus = 0.0
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
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9-]{2,}", topic_text)
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


def _topic_terms_from_seed(seed: str, domain: str = "", extra_terms: Optional[List[str]] = None) -> List[str]:
    topic_text = " ".join([seed or "", domain or "", *(extra_terms or [])]).lower().replace("-", " ")
    stopwords = {
        "about", "against", "also", "among", "and", "are", "based", "between",
        "can", "could", "does", "for", "from", "how", "into", "large", "language",
        "learning", "method", "methods", "model", "models", "paper", "research",
        "should", "study", "than", "that", "the", "their", "this", "through",
        "using", "what", "when", "where", "with", "within", "would",
        "是否", "如何", "研究", "方法", "模型", "系统",
    }
    terms: List[str] = []
    for token in re.findall(r"[a-zA-Z][a-zA-Z0-9]{2,}|[\u4e00-\u9fff]{2,}", topic_text):
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
    relevance_score = float(getattr(paper, "relevanceScore", 0.0) or 0.0)
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
) -> Dict[str, Any]:
    """Lightweight paper relevance gate used before idea generation."""

    topic_terms = _topic_terms_from_seed(seed, domain, extra_terms)
    total = len(papers)
    scored = [
        {
            "paperId": getattr(paper, "id", ""),
            "title": getattr(paper, "title", ""),
            "score": round(_paper_alignment_score(paper, topic_terms), 3),
            "sources": _paper_sources(paper),
        }
        for paper in papers
    ]
    scored.sort(key=lambda item: item["score"], reverse=True)
    aligned = [item for item in scored if item["score"] >= 0.32]
    external = [
        item for item in scored
        if any(source and source != "local" for source in item["sources"])
    ]
    avg_top_score = (
        sum(item["score"] for item in scored[: min(5, len(scored))]) / max(1, min(5, len(scored)))
        if scored else 0.0
    )

    min_papers = int(os.getenv("FAROS_PAPER_GATE_MIN_PAPERS", "4"))
    min_aligned = int(os.getenv("FAROS_PAPER_GATE_MIN_ALIGNED", "3"))
    errors: List[str] = []
    warnings: List[str] = []
    if total < min_papers:
        errors.append(f"{stage}: paper pool is too small ({total} < {min_papers})")
    if len(aligned) < min_aligned:
        errors.append(f"{stage}: too few papers are semantically aligned with the seed query ({len(aligned)} < {min_aligned})")
    if scored and avg_top_score < 0.30:
        errors.append(f"{stage}: top papers have weak topic alignment (avg={avg_top_score:.2f})")
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
        "avgTopAlignment": round(avg_top_score, 3),
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
        has_baseline = has_baseline or bool(getattr(paper, "baselines", []) or []) or any(
            marker in paper_text for marker in baseline_markers
        )
        has_evaluation_text = has_evaluation_text or any(
            marker in paper_text for marker in evaluation_markers
        )
        has_claim = has_claim or bool(claims)
        has_limitation = has_limitation or bool(getattr(paper, "limitations", []) or [])

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
    if aligned_count < min_aligned:
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


def _as_string_list(value: Any, *, limit: int = 8) -> List[str]:
    if not isinstance(value, list):
        return []
    items: List[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in items:
            items.append(text)
        if len(items) >= limit:
            break
    return items


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
    return {
        token
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9]{2,}|[\u4e00-\u9fff]{2,}", text)
        if token not in stopwords
    }


def _candidate_jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


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
        return queries or [seed]

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

    def _evaluate_evidence_gate_for_session(
        self,
        session: IdeaSession,
        *,
        stage: str,
    ) -> tuple[Dict[str, Any], List[StructuredPaper], Optional[LiteratureMap], Dict[str, Any]]:
        seed = session.config.seedQuery
        domain = session.config.domain or ""
        paper_type = session.config.paperType
        extra_terms = self._get_step_output(session, "expandQuery", "expandedTerms", [])
        structured_papers, literature_map, gap_outputs = self._load_evidence_gate_inputs(session)
        rule_gate = _evaluate_evidence_gate_v2(
            seed=seed,
            domain=domain,
            paper_type=paper_type,
            structured_papers=structured_papers,
            literature_map=literature_map,
            gap_outputs=gap_outputs,
            stage=stage,
            extra_terms=extra_terms,
        )
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
            raise StepContextError(
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
                "literatureMapId": novelty_outputs.get("literatureMapId"),
            },
            "gapOutputs": {
                "reasoningKgId": gap_outputs.get("reasoningKgId"),
                "pathSeedCount": gap_outputs.get("pathSeedCount", 0),
            },
            "error": rebuild_error,
        }

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
    
    def get_candidates(self, session_id: str) -> List[IdeaCandidate]:
        """Get candidates for a session."""
        return self.candidate_storage.list_by_session(session_id)
    
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
                # Step 1: Expand Query
                session = self._run_step(session, "expandQuery", self._step_expand_query)

                # Step 2: Literature Search
                session = self._run_step(session, "literatureSearch", self._step_literature_search)

                # Step 3: Novelty Check
                session = self._run_step(session, "noveltyCheck", self._step_novelty_check)

                # Step 4: Gap Analysis
                session = self._run_step(session, "gapAnalysis", self._step_gap_analysis)

                # Step 5: Evidence Gate 2.0 (repair literature before idea generation)
                session = self._run_step(session, "evidenceGate", self._step_evidence_gate)

                # Step 6: Idea Brainstorm (uses LLM)
                session = self._run_step(session, "ideaBrainstorm", self._step_idea_brainstorm)

                # Step 7: Rank Candidates
                session = self._run_step(session, "rankCandidates", self._step_rank_candidates)

                # Step 8: Finalize
                session = self._run_step(session, "finalizeSession", self._step_finalize)

                # Mark completed
                session.status = IdeaSessionStatus.COMPLETED
                session.endedAt = _utcnow()
                if session.trace:
                    session.trace.endedAt = _utcnow()

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
                session.trace.steps.append(step_result)
                session.trace.totalSteps += 1
                session.trace.successfulSteps += 1
            
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
                session.trace.steps.append(step_result)
                session.trace.totalSteps += 1
                session.trace.failedSteps += 1
            
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
            }

        except Exception as e:
            logger.warning(f"LLM query expansion failed: {e}, using fallback")
            expanded_terms = [
                seed,
                f"{seed} machine learning",
                f"{seed} deep learning",
                f"{seed} neural network",
            ]
            if domain != "general":
                expanded_terms.append(f"{seed} {domain}")

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
            }

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

        # Get expanded terms from Step 1
        search_queries = [seed]
        if session.trace:
            for step in session.trace.steps:
                if step.name == "expandQuery" and step.outputs.get("expandedTerms"):
                    search_queries = step.outputs["expandedTerms"]
                    break

        # Search across sources
        search_service = get_search_service()
        all_results: List[SearchResult] = []

        for query in search_queries[:3]:
            try:
                results = search_service.search(query, limit=max_papers)
                all_results.extend(results)
                logger.info(f"Search for '{query}' returned {len(results)} results")
            except Exception as e:
                logger.warning(f"Search failed for '{query}': {e}")

        def _dedupe_rank_filter(results: List[SearchResult], queries: List[str]) -> tuple[List[SearchResult], int, int]:
            # Dedup chain: doi > arxivId > semanticScholarId > normalized title hash
            seen_dois: set = set()
            seen_arxiv_ids: set = set()
            seen_s2_ids: set = set()
            seen_title_hashes: set = set()
            unique: List[SearchResult] = []

            for result in results:
                if result.doi and result.doi in seen_dois:
                    continue
                if result.arxiv_id and result.arxiv_id in seen_arxiv_ids:
                    continue
                s2_id = None
                if result.source == "semantic_scholar" and result.url:
                    s2_match = re.search(r'SemanticScholarID:(\w+)', result.url)
                    if s2_match:
                        s2_id = s2_match.group(1)
                if s2_id and s2_id in seen_s2_ids:
                    continue
                title_hash = _compute_title_hash(result.title)
                if title_hash in seen_title_hashes:
                    continue

                if result.doi:
                    seen_dois.add(result.doi)
                if result.arxiv_id:
                    seen_arxiv_ids.add(result.arxiv_id)
                if s2_id:
                    seen_s2_ids.add(s2_id)
                seen_title_hashes.add(title_hash)
                unique.append(result)

            unique = _rank_results_for_topic(
                unique,
                seed=seed,
                domain=session.config.domain or "",
                search_queries=queries,
            )
            ranked = len(unique)
            filtered, dropped = _filter_results_for_topic(unique)
            return filtered, dropped, ranked

        unique_results, filtered_out_count, ranked_count = _dedupe_rank_filter(all_results, search_queries)
        raw_quality_gate = _evaluate_paper_quality_gate(
            seed=seed,
            domain=session.config.domain or "",
            papers=unique_results,
            stage="literatureSearch.initial",
            extra_terms=search_queries,
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
                    all_results.extend(results)
                    logger.info(f"Repair search for '{query}' returned {len(results)} results")
                except Exception as e:
                    logger.warning(f"Repair search failed for '{query}': {e}")
            unique_results, filtered_out_count, ranked_count = _dedupe_rank_filter(
                all_results,
                [*search_queries, *repair_queries],
            )
            raw_quality_gate = _evaluate_paper_quality_gate(
                seed=seed,
                domain=session.config.domain or "",
                papers=unique_results,
                stage="literatureSearch.repaired",
                extra_terms=[*search_queries, *repair_queries],
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
            if result.source not in sources_used:
                sources_used.append(result.source)

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
                source=[result.source] if result.source else [],
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

        inputs = {"seedQuery": seed, "maxPapers": max_papers, "searchQueries": search_queries[:3]}
        outputs = {
            "paperCount": len(raw_papers),
            "rawPaperIds": [p.id for p in raw_papers],
            "graphId": graph.id,
            "sourcesUsed": sources_used,
            "searchQueries": search_queries[:3],
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
        num_select = min(40, max(5, len(raw_papers) // 2))
        graph, selected_paper_ids = self.graph_builder.select_papers(
            graph, num_select=num_select, must_cite_list=must_cite_list
        )
        forced_selected_ids: List[str] = []
        if forced_raw_paper_ids:
            raw_by_id = {paper.id: paper for paper in raw_papers}
            topic_terms = _topic_terms_from_seed(
                seed,
                session.config.domain or "",
                self._get_step_output(session, "expandQuery", "expandedTerms", []),
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
            extra_terms=self._get_step_output(session, "expandQuery", "expandedTerms", []),
        )
        if not selected_quality_gate["passed"] and raw_papers:
            topic_terms = _topic_terms_from_seed(
                seed,
                session.config.domain or "",
                self._get_step_output(session, "expandQuery", "expandedTerms", []),
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
                extra_terms=self._get_step_output(session, "expandQuery", "expandedTerms", []),
            )

        # Step 3c: Deep-read selected papers
        structured_papers = self.deep_reader.extract_structured_papers(
            session=session,
            selected_paper_ids=selected_paper_ids,
            raw_papers=raw_papers,
        )
        for sp in structured_papers:
            try:
                if not self.structured_storage.get(sp.id):
                    self.structured_storage.create(sp)
            except Exception as e:
                logger.warning(f"Failed to persist structured paper {sp.id}: {e}")
        structured_quality_gate = _evaluate_paper_quality_gate(
            seed=seed,
            domain=session.config.domain or "",
            papers=structured_papers,
            stage="noveltyCheck.structured",
            extra_terms=self._get_step_output(session, "expandQuery", "expandedTerms", []),
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
            for seed in path_seeds:
                self.path_seed_storage.create(seed)

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

        try:
            from app.modules.idea.bfts_search import BFTSSearchTree

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
                "method": "bfts_tree_search",
                "seedCount": len(path_seeds),
                "maxNodes": bfts_config.maxNodes,
                "beamWidth": bfts_config.beamWidth,
                "maxReflectionRounds": bfts_config.maxReflectionRounds,
            }
            outputs = {
                "candidateCount": len(candidates),
                "candidateIds": candidate_ids,
                "method": "bfts_tree_search",
                "bftsConfig": bfts_config.model_dump(),
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
            lines.append(
                f"[{i+1}] {title} ({year})\n"
                f"    Key claims: {claims_str}"
            )
        return "\n\n".join(lines)

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
        key_papers = "\n".join([
            f"- {item.title} ({item.year or 'N/A'})"
            for item in literature[:5]
        ])

        gap_text = json.dumps(gap_analysis[:3], indent=2) if gap_analysis else "\n".join([f"- {g}" for g in prioritized_gaps[:3]])
        opp_text = "\n".join([f"- {o}" for o in opportunities[:3]]) if opportunities else "Based on identified gaps"

        try:
            client = get_provider_client(session.config.providerName)

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

            # Parse ideas from response
            candidates = self._parse_ideas_json(session.id, response.text, generation_count)

            if not candidates:
                # Fallback to text parsing
                candidates = self._parse_ideas(session.id, response.text, generation_count)

            if not candidates:
                # Generate fallback
                candidates = self._generate_fallback_candidates(session.id, seed, min(3, max_candidates))
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
                "llmLatencyMs": response.latency_ms,
                "method": "legacy_single_shot",
                "requestedGenerationCount": generation_count,
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
                ideas = data.get("ideas", [])
                
                for idea in ideas[:max_count]:
                    # Parse experiments
                    experiments = []
                    for exp in idea.get("requiredExperiments", []):
                        if isinstance(exp, dict):
                            experiments.append(ExperimentSpec(
                                name=exp.get("name", "Experiment"),
                                description=exp.get("description", ""),
                                metrics=exp.get("metrics", []),
                                datasets=exp.get("datasets", [])
                            ))
                    
                    # Parse risks
                    risks = []
                    for risk in idea.get("risks", []):
                        if isinstance(risk, dict):
                            risks.append(RiskItem(
                                risk=risk.get("risk", ""),
                                mitigation=risk.get("mitigation", "")
                            ))
                    
                    candidate = IdeaCandidate(
                        id=generate_candidate_id(),
                        sessionId=session_id,
                        title=idea.get("title", "Untitled Idea"),
                        problem=idea.get("problem", "Problem statement pending."),
                        keyInsight=idea.get("keyInsight", idea.get("approach", "Key insight pending.")),
                        novelty=5.0,
                        noveltyRationale="Pending ranking",
                        feasibility=5.0,
                        feasibilityRationale="Pending ranking",
                        impact=5.0,
                        impactRationale="Pending ranking",
                        scoringMethod="pending",
                        risks=risks,
                        requiredExperiments=experiments,
                        expectedMetrics=idea.get("expectedOutcomes", []),
                        draftPlan=DraftPlan(
                            researchQuestion=idea.get("problem", ""),
                            hypothesis=idea.get("keyInsight", ""),
                            methodology=idea.get("approach", "To be defined"),
                            expectedOutcomes=idea.get("expectedOutcomes", []),
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
        candidates = self.get_candidates(session.id)

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
        gate_reports = self._apply_idea_review_gate(
            ranked=ranked,
            evidence_list=evidence_list,
            prior_work_comparisons=prior_work_comparisons,
            critiques=critiques,
        )
        ranked = sorted(ranked, key=lambda c: c.overallScore, reverse=True)
        regenerated_candidate_ids: List[str] = []
        literature_repair_reports: List[Dict[str, Any]] = []
        paper_quality_gate = _evaluate_paper_quality_gate(
            seed=seed,
            domain=domain,
            papers=structured_papers,
            stage="ideaReview.structuredPapers",
            extra_terms=self._get_step_output(session, "expandQuery", "expandedTerms", []),
        )
        max_review_iterations = max(1, min(5, getattr(session.config, "maxReviewIterations", 2)))
        review_iteration_summaries: List[Dict[str, Any]] = []

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
            top_candidate = ranked[0] if ranked else None
            top_gate = gate_reports.get(top_candidate.id) if top_candidate else None
            top_passed = bool(
                top_gate
                and top_gate.get("passed")
                and paper_quality_gate.get("passed", False)
            )
            summary = {
                "iteration": review_iteration + 1,
                "topCandidateId": top_candidate.id if top_candidate else None,
                "topPassed": top_passed,
                "paperGatePassed": bool(paper_quality_gate.get("passed", False)),
                "blockingIssueCount": len(top_gate.get("blockingIssues", [])) if top_gate else 0,
                "warningCount": len(top_gate.get("warnings", [])) if top_gate else 0,
                "action": "none",
            }
            if top_passed or not top_gate:
                review_iteration_summaries.append(summary)
                break

            if (
                self._idea_gate_requires_literature_repair(top_gate, paper_quality_gate)
                and not literature_repair_reports
                and session.config.providerName
                and session.config.model
            ):
                repair_report = self._repair_literature_pool_for_idea_quality(
                    session,
                    review_gate=top_gate,
                    paper_quality_gate=paper_quality_gate,
                )
                literature_repair_reports.append(repair_report)
                summary["action"] = "rerun_literature_search"
                summary["createdRawPaperCount"] = len(
                    repair_report.get("persistReport", {}).get("createdRawPaperIds", [])
                )
                summary["paperGateAfterRepair"] = bool(
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
                        extra_terms=self._get_step_output(session, "expandQuery", "expandedTerms", []),
                    )
                    if structured_papers:
                        for candidate in ranked[:top_k]:
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
                    )
                    top_gate = gate_reports.get(top_candidate.id) if top_candidate else None
                    top_passed = bool(
                        top_gate
                        and top_gate.get("passed")
                        and paper_quality_gate.get("passed", False)
                    )
                    summary["topPassedAfterRepair"] = top_passed
                    summary["warningCountAfterRepair"] = (
                        len(top_gate.get("warnings", [])) if top_gate else 0
                    )
                    summary["blockingIssueCountAfterRepair"] = (
                        len(top_gate.get("blockingIssues", [])) if top_gate else 0
                    )
                except Exception as e:
                    logger.warning("Reloading repaired idea-stage evidence failed: %s", e, exc_info=True)
                    summary["error"] = str(e)
                if top_passed:
                    review_iteration_summaries.append(summary)
                    break

            if (
                top_gate
                and top_candidate
                and self._should_optimize_candidate_from_gate(top_candidate, top_gate)
                and structured_papers
                and session.config.providerName
                and session.config.model
            ):
                summary["action"] = (
                    "rerun_literature_search+regenerate_idea"
                    if summary.get("action") == "rerun_literature_search"
                    else "regenerate_idea"
                )
                try:
                    regenerated = self._regenerate_candidate_from_review(
                        session=session,
                        base_candidate=top_candidate,
                        review_gate=top_gate,
                        critique=next((item for item in critiques if item.candidateId == top_candidate.id), None),
                        prior_work=[
                            item for item in prior_work_comparisons
                            if item.candidateId == top_candidate.id
                        ],
                        literature_context=literature_context,
                    )
                except Exception as e:
                    logger.warning(f"Idea review regeneration failed: {e}")
                    summary["error"] = str(e)
                    regenerated = None
                if regenerated:
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
                    ))
                    ranked.append(regenerated)
                    regenerated_candidate_ids.append(regenerated.id)
                    summary["regeneratedCandidateId"] = regenerated.id
                    if regenerated.id not in session.candidateIds:
                        session.candidateIds.append(regenerated.id)
                    try:
                        self.candidate_storage.create(regenerated)
                    except Exception as e:
                        logger.warning(f"Failed to persist regenerated candidate {regenerated.id}: {e}")
                    ranked = sorted(ranked, key=lambda c: c.overallScore, reverse=True)
                review_iteration_summaries.append(summary)
                continue

            review_iteration_summaries.append(summary)
            if review_iteration == max_review_iterations - 1:
                break
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
        }

        if gate_reports and idea_review_passed_count == 0:
            raise StepContextError(
                "Idea review gate failed: no candidate passed after internal repair iterations",
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

        if not paper_quality_gate.get("passed", False):
            return True
        text = " ".join(
            str(item)
            for item in [
                *review_gate.get("blockingIssues", []),
                *review_gate.get("warnings", []),
                *review_gate.get("suggestedImprovements", []),
            ]
        ).lower()
        evidence_terms = [
            "no paper",
            "supporting paper",
            "reference support",
            "prior-work",
            "prior work",
            "evidence",
            "literature",
            "citation",
            "closest prior",
            "证据",
            "论文",
            "引用",
            "相关工作",
        ]
        return any(term in text for term in evidence_terms)

    def _persist_repair_search_results(
        self,
        session: IdeaSession,
        results: List[SearchResult],
        *,
        search_queries: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        existing_raw = self.raw_paper_storage.list_by_session(session.id)
        seen_dois = {paper.doi for paper in existing_raw if paper.doi}
        seen_arxiv_ids = {paper.arxivId for paper in existing_raw if paper.arxivId}
        seen_title_hashes = {paper.normalizedTitleHash for paper in existing_raw if paper.normalizedTitleHash}
        created_raw_ids: List[str] = []
        created_literature_ids: List[str] = []

        ranked = _rank_results_for_topic(
            results,
            seed=session.config.seedQuery,
            domain=session.config.domain or "",
            search_queries=search_queries or [session.config.seedQuery],
        )
        filtered, filtered_out_count = _filter_results_for_topic(ranked)
        filtered.sort(
            key=lambda result: _repair_result_priority(result, session.config.paperType),
            reverse=True,
        )
        for result in filtered:
            title_hash = _compute_title_hash(result.title)
            if result.doi and result.doi in seen_dois:
                continue
            if result.arxiv_id and result.arxiv_id in seen_arxiv_ids:
                continue
            if title_hash in seen_title_hashes:
                continue
            seen_title_hashes.add(title_hash)
            if result.doi:
                seen_dois.add(result.doi)
            if result.arxiv_id:
                seen_arxiv_ids.add(result.arxiv_id)

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
                source=[result.source] if result.source else [],
                normalizedTitleHash=title_hash,
                relevanceScore=min(1.0, max(0.0, result.relevance_score)),
            )
            self.raw_paper_storage.create(raw_paper)
            created_raw_ids.append(raw_paper.id)

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
                relevanceScore=min(1.0, max(0.0, result.relevance_score)),
                source=result.source,
            )
            self.literature_storage.create(lit_item)
            created_literature_ids.append(lit_item.id)

        all_raw = self.raw_paper_storage.list_by_session(session.id)
        if all_raw:
            graph = self.graph_builder.build_graph_v0(session_id=session.id, raw_papers=all_raw)
            self.graph_storage.create(graph)
        return {
            "createdRawPaperIds": created_raw_ids,
            "createdLiteratureIds": created_literature_ids,
            "filteredOutCount": filtered_out_count,
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
            extra_terms=self._get_step_output(session, "expandQuery", "expandedTerms", []),
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
            lines.append(
                f"[{paper_id}] {title} ({year})\n"
                f"    Claims: {claims_str}"
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

    def _apply_idea_review_gate(
        self,
        *,
        ranked: List[IdeaCandidate],
        evidence_list: List[CandidateGraphEvidence],
        prior_work_comparisons: List[PriorWorkComparison],
        critiques: List[IdeaCritique],
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

            if penalty and "idea_review_gate" not in (candidate.scoringMethod or ""):
                candidate.referenceSupport = max(0.0, candidate.referenceSupport - penalty)
                candidate.feasibility = max(0.0, candidate.feasibility - min(0.8, penalty * 0.35))
                candidate.risk = max(0.0, candidate.risk - min(0.8, penalty * 0.30))
                if any("difference" in item.lower() or "novelty" in item.lower() for item in warnings):
                    candidate.novelty = max(0.0, candidate.novelty - min(0.8, penalty * 0.35))

            passed = not blocking and candidate.overallScore >= 6.0 and candidate.referenceSupport >= 4.5
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
                "reviewerReports": [
                    {
                        "reviewer": "IdeaEvidenceReviewer",
                        "mode": "rule+llm_context",
                        "score": round(min(10.0, support_count * 2.2 + candidate.referenceSupport * 0.45), 2),
                        "passed": support_count > 0 and candidate.referenceSupport >= 4.5,
                        "summary": "Checks whether the idea is grounded in papers, KG entities, or path seeds.",
                    },
                    {
                        "reviewer": "IdeaNoveltyReviewer",
                        "mode": "rule+prior_work_llm",
                        "score": round(min(10.0, candidate.novelty * 0.75 + (1.5 if has_difference else 0.0)), 2),
                        "passed": candidate.novelty >= 5.5 and has_difference,
                        "summary": "Checks concrete difference from closest prior work.",
                    },
                    {
                        "reviewer": "IdeaFeasibilityReviewer",
                        "mode": "rule+critique_llm",
                        "score": round(max(0.0, candidate.feasibility - critique_failure_count * 0.4), 2),
                        "passed": candidate.feasibility >= 5.5 and critique_failure_count < 3,
                        "summary": "Checks implementability and likely failure modes.",
                    },
                    {
                        "reviewer": "IdeaSpecificityReviewer",
                        "mode": "rule+critique_llm",
                        "score": round((candidate.clarity + candidate.experimentSpecificity) / 2, 2),
                        "passed": candidate.clarity >= 5.0 and candidate.experimentSpecificity >= 5.0,
                        "summary": "Checks whether method, variables, and validation requirements are concrete.",
                    },
                    {
                        "reviewer": "IdeaImpactReviewer",
                        "mode": "rule+critique_llm",
                        "score": round(max(0.0, candidate.impact - critique_weakness_count * 0.2), 2),
                        "passed": candidate.impact >= 5.5,
                        "summary": "Checks expected research value and downstream contribution potential.",
                    },
                ],
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
            "literatureContext": literature_context[:8000],
        }
        messages = [
            ChatMessage(
                role="system",
                content=(
                    "You regenerate one stronger research idea from idea-stage review findings. "
                    "Return JSON only. Do not claim executed experiments. Do not invent paper IDs. "
                    "The new idea must preserve useful parts of the source candidate while directly addressing "
                    "reviewGate warnings, blocking issues, and suggested improvements."
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
