"""Deterministic builders for PlanPackage context and fallback plan fields."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from app.models.idea import (
    BFTSHandoff,
    GraphPatch,
    IdeaCandidate,
    IdeaSearchTree,
    LiteratureMap,
    LiteratureProbeResult,
    RawPaper,
    RankedIdeaOutput,
    ReasoningKG,
    ReasoningPathSeed,
    StructuredPaper,
)
from app.models.plan_package import (
    PlanBackground,
    PlanContributionStatement,
    PlanEvidenceRef,
    PlanEvidenceTrace,
    PlanExpectedMetric,
    PlanGap,
    PlanGapItem,
    PlanGenerationMetadata,
    PlanGraphGrounding,
    PlanIdeaSummary,
    PlanLiteratureCoverage,
    PlanLiteraturePaperSummary,
    PlanLiteratureSurvey,
    PlanOutput,
    PlanPackage,
    PlanPrinciple,
    PlanProbeGrounding,
    PlanSource,
    PlanSourceFieldMap,
    PlanStage,
    PlanStep,
)
from app.services.plan_package_templates import get_plan_template
from app.storage.plan_package_storage import generate_plan_package_id


def _dump(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value


def _get(value: Any, key: str, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _unique(values: Iterable[Optional[str]]) -> List[str]:
    seen: set[str] = set()
    result: List[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _text_join(values: Iterable[str], fallback: str = "") -> str:
    filtered = [v.strip() for v in values if isinstance(v, str) and v.strip()]
    return " ".join(filtered) if filtered else fallback


def _safe_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


_RELEVANCE_STOPWORDS = {
    "about", "against", "also", "among", "and", "based", "between", "can",
    "could", "does", "for", "from", "how", "into", "large", "language",
    "learning", "method", "methods", "model", "models", "paper", "plan",
    "research", "should", "study", "than", "that", "the", "their", "this",
    "through", "using", "what", "when", "where", "with", "within", "would",
    "是否", "如何", "研究", "方法", "模型", "系统",
}


def _topic_anchors(candidate: IdeaCandidate, literature_map: Optional[LiteratureMap]) -> List[str]:
    text_parts = [
        candidate.title,
        candidate.problem,
        candidate.hypothesisStatement,
        candidate.keyInsight,
        candidate.proposedMethod,
        candidate.expectedOutcome,
    ]
    if candidate.draftPlan:
        text_parts.extend([
            candidate.draftPlan.researchQuestion,
            candidate.draftPlan.hypothesis,
            candidate.draftPlan.methodology,
        ])
    if literature_map:
        text_parts.extend(gap.direction for gap in literature_map.gaps[:4])
        text_parts.extend(gap.evidence for gap in literature_map.gaps[:4])
    text = " ".join(text_parts).lower().replace("-", " ")
    if "rag" in text:
        text = f"{text} retrieval augmented generation"
    anchors: List[str] = []
    for token in re.findall(r"[a-zA-Z][a-zA-Z0-9]{2,}|[\u4e00-\u9fff]{2,}", text):
        if token in _RELEVANCE_STOPWORDS:
            continue
        if token not in anchors:
            anchors.append(token)
    priority = [
        "citation", "faithfulness", "faithful", "uncertainty", "gating",
        "confidence", "retrieval", "augmented", "rag", "hallucination",
        "attribution", "provenance", "graph", "multi", "hop", "verification",
    ]
    original_order = {term: index for index, term in enumerate(anchors)}
    anchors.sort(key=lambda term: (
        priority.index(term) if term in priority else len(priority),
        original_order[term],
    ))
    return anchors[:28]


def _paper_relevance(
    *,
    title: str,
    summary: str,
    methods: List[Dict[str, Any]],
    findings: List[Dict[str, Any]],
    limitations: List[str],
    claims: List[Dict[str, Any]],
    anchors: List[str],
) -> tuple[float, List[str], str]:
    if not anchors:
        return 0.0, [], "No topic anchors were available for relevance scoring."
    text = " ".join([
        title,
        summary,
        " ".join(str(item) for item in methods),
        " ".join(str(item) for item in findings),
        " ".join(limitations),
        " ".join(str(item) for item in claims),
    ]).lower().replace("-", " ")
    signals = [anchor for anchor in anchors if anchor in text]
    weighted_hits = len(signals)
    if title:
        title_text = title.lower().replace("-", " ")
        weighted_hits += sum(1 for anchor in anchors[:10] if anchor in title_text)
    score = min(1.0, weighted_hits / max(4, min(10, len(anchors) // 2 or 4)))
    if score >= 0.75:
        reason = "Strong overlap with the selected idea topic anchors."
    elif score >= 0.45:
        reason = "Moderate overlap with the selected idea topic anchors."
    elif signals:
        reason = "Weak overlap; keep as context but avoid treating it as core support without human confirmation."
    else:
        reason = "No visible overlap with the selected idea topic anchors."
    return round(score, 3), signals[:12], reason


def _safe_id(value: Any) -> str:
    return str(value).strip() if value else ""


def _first_text_value(*values: Any) -> str:
    for value in values:
        text = _safe_text(value)
        if text:
            return text
    return ""


def _sanitize_plan_only_text(text: str) -> str:
    """Rewrite accidental executed-result wording into plan-stage wording."""
    if not text:
        return ""
    cleaned = text.strip()
    rewrites = [
        (
            r"\bExperiments on ([^.]+?) show that ([^.]+?)\.",
            r"Planned experiments on \1 should test whether \2.",
        ),
        (
            r"\bExperiments on ([^.]+?) demonstrate that ([^.]+?)\.",
            r"Planned experiments on \1 should test whether \2.",
        ),
        (
            r"\bResults show that ([^.]+?)\.",
            r"Planned evaluation should test whether \1.",
        ),
        (
            r"\bWe achieved ([^.]+?)\.",
            r"Planned evaluation should measure whether the method can achieve \1.",
        ),
    ]
    for pattern, replacement in rewrites:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    return cleaned


def _candidate_hypothesis(candidate: IdeaCandidate) -> str:
    draft = candidate.draftPlan
    return _first_text_value(
        candidate.hypothesisStatement,
        draft.hypothesis if draft else "",
        candidate.keyInsight,
        candidate.problem,
    )


def _candidate_method(candidate: IdeaCandidate, critique: Dict[str, Any]) -> str:
    draft = candidate.draftPlan
    method = _first_text_value(
        candidate.proposedMethod,
        draft.methodology if draft else "",
        candidate.keyInsight,
    )
    if method:
        return _sanitize_plan_only_text(method)
    if isinstance(critique, dict):
        suggestions = critique.get("suggestions") or critique.get("improvementSuggestions") or []
        if suggestions:
            return _sanitize_plan_only_text(str(suggestions[0]).strip())
    return _sanitize_plan_only_text(candidate.title)


def _candidate_expected_outcome(candidate: IdeaCandidate, critique: Dict[str, Any]) -> str:
    draft = candidate.draftPlan
    if candidate.expectedOutcome:
        return _sanitize_plan_only_text(candidate.expectedOutcome)
    if draft and draft.expectedOutcomes:
        return _sanitize_plan_only_text("; ".join(str(item) for item in draft.expectedOutcomes if str(item).strip()))
    if candidate.expectedMetrics:
        return "Expected to improve or validate: " + ", ".join(candidate.expectedMetrics[:4])
    if isinstance(critique, dict):
        strengths = critique.get("strengths") or []
        if strengths:
            return _sanitize_plan_only_text(str(strengths[0]).strip())
    return "Expected outcome should be validated by the planned implementation and evaluation metrics."


def _method_dict(method: Any) -> Dict[str, Any]:
    data = _dump(method) or {}
    if isinstance(data, dict):
        return data
    return {"name": str(data)}


def _finding_dict(finding: Any) -> Dict[str, Any]:
    data = _dump(finding) or {}
    if isinstance(data, dict):
        return data
    return {"description": str(data)}


def _claim_dict(claim: Any) -> Dict[str, Any]:
    data = _dump(claim) or {}
    if isinstance(data, dict):
        return data
    return {"text": str(data)}


def _graph_evidence(candidate: IdeaCandidate, ranked_output: Optional[RankedIdeaOutput]) -> Dict[str, Any]:
    embedded = _dump(getattr(candidate, "graphEvidence", None))
    if isinstance(embedded, dict) and embedded:
        return embedded
    if ranked_output:
        for evidence in ranked_output.evidence:
            if evidence.candidateId == candidate.id:
                return evidence.model_dump()
    return {}


def _candidate_prior_work(candidate: IdeaCandidate, ranked_output: Optional[RankedIdeaOutput]) -> List[Dict[str, Any]]:
    embedded = [_dump(item) for item in (getattr(candidate, "closestPriorWork", None) or [])]
    embedded = [item for item in embedded if isinstance(item, dict)]
    if embedded:
        return embedded
    if ranked_output:
        return [
            item.model_dump()
            for item in ranked_output.priorWorkComparisons
            if item.candidateId == candidate.id
        ]
    return []


def _candidate_critique(candidate: IdeaCandidate, ranked_output: Optional[RankedIdeaOutput]) -> Dict[str, Any]:
    embedded = _dump(getattr(candidate, "critique", None))
    if isinstance(embedded, dict) and embedded:
        return embedded
    if ranked_output:
        for critique in ranked_output.critiques:
            if critique.candidateId == candidate.id:
                return critique.model_dump()
    return {}


def _paper_ref(paper_id: str, source: str = "structured") -> PlanEvidenceRef:
    return PlanEvidenceRef(type="paper", id=paper_id, source=source)


def build_literature_survey(
    *,
    candidate: IdeaCandidate,
    raw_papers: List[RawPaper],
    structured_papers: List[StructuredPaper],
    literature_map: Optional[LiteratureMap],
    probe_results: List[LiteratureProbeResult],
) -> PlanLiteratureSurvey:
    raw_by_id = {paper.id: paper for paper in raw_papers}
    papers: List[PlanLiteraturePaperSummary] = []
    structured_raw_ids: set[str] = set()
    anchors = _topic_anchors(candidate, literature_map)

    for sp in structured_papers:
        raw = raw_by_id.get(sp.rawPaperId) or raw_by_id.get(sp.id)
        paper_id = sp.rawPaperId or sp.id
        structured_raw_ids.add(paper_id)
        role = sp.graph1Roles[0] if sp.graph1Roles else "supporting_evidence"
        summary = sp.summary or sp.abstract or (raw.abstract if raw else "") or f"Structured summary for {sp.title}"
        methods = [_method_dict(method) for method in sp.methods]
        findings = [_finding_dict(finding) for finding in sp.findings]
        claims = [_claim_dict(claim) for claim in sp.claims]
        relevance_score, relevance_signals, relevance_reason = _paper_relevance(
            title=sp.title,
            summary=summary,
            methods=methods,
            findings=findings,
            limitations=sp.limitations,
            claims=claims,
            anchors=anchors,
        )
        papers.append(
            PlanLiteraturePaperSummary(
                paperId=paper_id,
                structuredPaperId=sp.id,
                source="structured",
                title=sp.title,
                authors=sp.authors,
                year=sp.year,
                venue=sp.venue or (raw.venue if raw else "") or "",
                url=(raw.url if raw else "") or "",
                role=role,
                relevanceScore=relevance_score,
                relevanceSignals=relevance_signals,
                relevanceReason=relevance_reason,
                summary=summary,
                methods=methods,
                findings=findings,
                limitations=sp.limitations,
                claims=claims,
                evidenceRefs=[_paper_ref(paper_id, "structured")],
            )
        )

    probe_seen: set[str] = set()
    for result in probe_results:
        for paper in result.papers:
            if paper.id in structured_raw_ids or paper.id in probe_seen:
                continue
            probe_seen.add(paper.id)
            role = "prior_work"
            if paper.id in result.contradictionPaperIds:
                role = "contradiction"
            elif paper.id in result.baselinePaperIds:
                role = "baseline"
            summary = result.summary or paper.abstract or f"Probe-discovered paper for query: {result.query.query}"
            relevance_score, relevance_signals, relevance_reason = _paper_relevance(
                title=paper.title,
                summary=summary,
                methods=[],
                findings=[],
                limitations=[],
                claims=[],
                anchors=anchors,
            )
            papers.append(
                PlanLiteraturePaperSummary(
                    paperId=paper.id,
                    structuredPaperId=None,
                    source="probe",
                    title=paper.title,
                    authors=paper.authors,
                    year=paper.year,
                    venue=paper.venue or "",
                    url=paper.url or "",
                    role=role,
                    relevanceScore=relevance_score,
                    relevanceSignals=relevance_signals,
                    relevanceReason=relevance_reason,
                    summary=summary,
                    methods=[],
                    findings=[],
                    limitations=[],
                    claims=[],
                    evidenceRefs=[
                        _paper_ref(paper.id, "probe"),
                        PlanEvidenceRef(type="probe", id=result.id, source="probe"),
                    ],
                )
            )

    clusters = [cluster.model_dump() for cluster in literature_map.clusters] if literature_map else []
    selected_count = len(literature_map.selectedPaperIds) if literature_map else len(structured_papers)
    summary = "Investigated literature covers selected deep-read papers"
    if probe_seen:
        summary += " plus targeted literature-probe papers"
    summary += "."

    return PlanLiteratureSurvey(
        summary=summary,
        coverage=PlanLiteratureCoverage(
            rawPaperCount=len(raw_papers),
            selectedPaperCount=selected_count,
            structuredPaperCount=len(structured_papers),
            probePaperCount=len(probe_seen),
            clusterCount=len(clusters),
        ),
        clusters=clusters,
        papers=papers,
    )


def build_gap(
    *,
    candidate: IdeaCandidate,
    literature_map: Optional[LiteratureMap],
    structured_papers: List[StructuredPaper],
    graph_patches: List[GraphPatch],
    prior_work: List[Dict[str, Any]],
    critique: Dict[str, Any],
    graph_evidence: Dict[str, Any],
) -> PlanGap:
    support_ids = (
        list(graph_evidence.get("supportingPaperIds", []))
        if isinstance(graph_evidence, dict)
        else []
    )
    if not support_ids:
        support_ids = [paper.rawPaperId for paper in structured_papers if paper.rawPaperId]

    method_names: List[str] = []
    for paper in structured_papers:
        if paper.methods:
            method_names.append(paper.methods[0].name)
        elif paper.title:
            method_names.append(paper.title)
    existing_coverage = (
        "Investigated work already covers "
        + ", ".join(_unique(method_names)[:5])
        + "."
        if method_names
        else "Investigated work covers related retrieval and reasoning approaches."
    )

    comparison_differences: List[str] = []
    for comparison in prior_work[:2]:
        comparison_differences.extend(
            str(item).strip()
            for item in comparison.get("differences", [])[:3]
            if str(item).strip()
        )

    weaknesses: List[str] = []
    weaknesses = critique.get("weaknesses") if isinstance(critique, dict) else None
    if isinstance(weaknesses, list):
        weaknesses = [
            str(item).strip()
            for item in weaknesses
            if str(item).strip() and "truncat" not in str(item).lower()
        ]
    else:
        weaknesses = []

    contradiction_ids = [
        patch.id
        for patch in graph_patches
        if patch.patchType == "contradiction"
    ]

    limitation_claim_ids: List[str] = []
    for paper in structured_papers:
        for claim in paper.claims:
            if claim.claimType in {"limitation", "premise_conclusion", "cause_effect"}:
                limitation_claim_ids.append(claim.claimId)

    hypothesis = _candidate_hypothesis(candidate).rstrip(".")
    hypothesis_clause = (
        hypothesis[:1].lower() + hypothesis[1:]
        if hypothesis
        else candidate.problem.rstrip(".")
    )
    candidate_text = " ".join([
        candidate.title,
        candidate.problem,
        candidate.keyInsight,
        candidate.proposedMethod,
    ]).lower()
    is_dense_graph_retrieval = (
        "dense retrieval" in candidate_text
        and ("graph traversal" in candidate_text or "graph retrieval" in candidate_text)
    )
    if is_dense_graph_retrieval:
        unresolved_issue = (
            "Existing approaches do not yet jointly establish whether "
            f"{hypothesis_clause} while preserving inspectable evidence paths, robustness to graph noise, "
            "and a favorable accuracy-efficiency trade-off."
        )
        selected_statement = (
            "Current multi-hop RAG methods provide graph retrieval, hybrid retrieval, or adaptive graph refinement, "
            "but lack a validated query-level policy that reliably decides when to use dense retrieval versus graph "
            "traversal while keeping the resulting evidence path verifiable and computationally efficient."
        )
    else:
        unresolved_issue = (
            "Existing approaches do not yet jointly establish whether "
            f"{hypothesis_clause} under shared effectiveness, robustness, efficiency, and evidence-traceability criteria."
        )
        selected_statement = (
            f"Existing approaches related to {candidate.title} cover individual components of the problem, "
            "but do not yet provide a validated end-to-end mechanism that realizes the candidate's core hypothesis "
            "under explicit effectiveness, robustness, efficiency, and evidence-traceability constraints."
        )

    proposed_method = _candidate_method(candidate, critique)
    method_sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", proposed_method)
        if sentence.strip()
    ]
    proposed_entry = " ".join(method_sentences[:2]) or candidate.keyInsight

    validation_metrics = list(candidate.expectedMetrics)
    datasets: List[str] = []
    for experiment in candidate.experimentSpecs or candidate.requiredExperiments:
        validation_metrics.extend(experiment.metrics)
        datasets.extend(experiment.datasets)
    validation_metrics = _unique(validation_metrics)[:8] or [
        "answer accuracy",
        "evidence-path completeness",
        "retrieval cost",
        "robustness under graph noise",
    ]
    datasets = _unique(datasets)
    boundary = (
        "The study is bounded to "
        + (", ".join(datasets) if datasets else "the declared multi-hop QA evaluation setting")
        + " and compares the proposed mechanism with dense-only, graph-only, and related hybrid retrieval baselines. "
        "It does not claim cross-domain generalization or completed experimental gains without downstream evidence."
    )

    why_parts = comparison_differences[:3] + weaknesses[:2]
    why_unsolved = " ".join(why_parts) or (
        "Prior work addresses individual retrieval components, but the combined decision policy, "
        "evidence-path verification, and robustness/efficiency trade-off remain unvalidated."
    )

    items: List[PlanGapItem] = [
        PlanGapItem(
            id="gap-1",
            kind="selected",
            statement=selected_statement,
            severity="high",
            existingCoverage=existing_coverage,
            unresolvedIssue=unresolved_issue,
            proposedEntry=proposed_entry,
            boundary=boundary,
            validationNeeds=validation_metrics,
            whyUnsolved=why_unsolved,
            supportedByPaperIds=_unique(support_ids),
            supportedByClaimIds=_unique(limitation_claim_ids)[:10],
            linkedGraphSignalIds=contradiction_ids,
        )
    ]

    for gap in (literature_map.gaps if literature_map else [])[:4]:
        direction = (gap.direction or gap.evidence or "Related literature direction").strip().rstrip(".")
        items.append(
            PlanGapItem(
                id=f"gap-{len(items) + 1}",
                kind="supporting_signal",
                statement=f"Supporting literature signal: {direction}.",
                severity="medium",
                existingCoverage=gap.evidence,
                unresolvedIssue="This signal motivates the selected GAP but is not itself the paper's primary unresolved problem.",
                whyUnsolved=gap.evidence,
                supportedByPaperIds=gap.paperIds,
                linkedGraphSignalIds=gap.clusterIds + gap.entityHints,
            )
        )

    limitation_papers = [paper for paper in structured_papers if paper.limitations]
    if limitation_papers:
        paper = limitation_papers[0]
        items.append(
            PlanGapItem(
                id=f"gap-{len(items) + 1}",
                kind="literature_limitation",
                statement=paper.limitations[0],
                severity="medium",
                existingCoverage=f"Reported by {paper.title}.",
                unresolvedIssue=paper.limitations[0],
                whyUnsolved="Explicit limitation reported by investigated literature.",
                supportedByPaperIds=[paper.rawPaperId],
                supportedByClaimIds=[claim.claimId for claim in paper.claims[:3]],
            )
        )

    summary = (
        f"{selected_statement} This work addresses the gap through the following entry point: "
        f"{proposed_entry.rstrip('.')}. "
        f"Validation is scoped to {', '.join(validation_metrics[:4])}."
    )

    return PlanGap(
        summary=summary,
        items=items,
        selectedGapId=items[0].id,
    )


def build_principle(
    *,
    candidate: IdeaCandidate,
    reasoning_kg: Optional[ReasoningKG],
    path_seeds: List[ReasoningPathSeed],
    graph_evidence: Dict[str, Any],
    prior_work: List[Dict[str, Any]],
    critique: Dict[str, Any],
    probe_results: List[LiteratureProbeResult],
    graph_patches: List[GraphPatch],
) -> PlanPrinciple:
    proposed_method = _candidate_method(candidate, critique)
    path_seed_ids = _unique(
        list(graph_evidence.get("supportingPathSeedIds", []))
        + [_safe_id(candidate.pathSeedId)]
        + [seed.seedId for seed in path_seeds[:3]]
    ) if isinstance(graph_evidence, dict) else _unique([candidate.pathSeedId])

    reasoning_path = []
    if isinstance(graph_evidence, dict):
        reasoning_path.extend(graph_evidence.get("reasoningTrace", []))
    for seed in path_seeds:
        if seed.seedId in path_seed_ids:
            for step in seed.steps[:6]:
                reasoning_path.append({
                    "step": step.stepType,
                    "id": step.entityId,
                    "text": step.text or step.description,
                    "pathSeedId": seed.seedId,
                })

    novelty_bits: List[str] = []
    for comparison in prior_work[:2]:
        novelty_bits.extend([str(diff) for diff in comparison.get("differences", [])[:2]])
    novelty_claim = _text_join(novelty_bits, candidate.keyInsight)

    assumptions = []
    if isinstance(critique, dict):
        assumptions = [str(item) for item in critique.get("assumptions", [])]
    risks = [item.risk for item in candidate.risks]
    if isinstance(critique, dict):
        risks.extend(str(item) for item in critique.get("failureModes", []))

    entity_ids = graph_evidence.get("supportingEntityIds", []) if isinstance(graph_evidence, dict) else []
    relation_ids = [relation.relationId for relation in reasoning_kg.relations[:8]] if reasoning_kg else []
    probe_paper_ids = []
    for result in probe_results:
        probe_paper_ids.extend(paper.id for paper in result.papers)
    if isinstance(graph_evidence, dict):
        probe_paper_ids.extend(graph_evidence.get("probePaperIds", []))

    return PlanPrinciple(
        summary=proposed_method,
        mechanism=proposed_method,
        noveltyClaim=novelty_claim,
        assumptions=_unique(assumptions),
        risks=_unique(risks),
        reasoningPath=reasoning_path,
        graphGrounding=PlanGraphGrounding(
            entityIds=_unique(entity_ids),
            relationIds=_unique(relation_ids),
            pathSeedIds=path_seed_ids,
            searchNodeIds=_unique([_safe_id(candidate.searchNodeId)]),
        ),
        probeGrounding=PlanProbeGrounding(
            probeResultIds=[result.id for result in probe_results],
            graphPatchIds=[patch.id for patch in graph_patches],
            probePaperIds=_unique(probe_paper_ids),
        ),
    )


def build_background(
    *,
    candidate: IdeaCandidate,
    literature_survey: PlanLiteratureSurvey,
    literature_map: Optional[LiteratureMap],
    critique: Dict[str, Any],
) -> PlanBackground:
    limitations = []
    for paper in literature_survey.papers:
        limitations.extend(paper.limitations[:2])
    if isinstance(critique, dict):
        limitations.extend(str(item) for item in critique.get("weaknesses", []))
    domain_context = []
    if literature_map:
        domain_context.extend(cluster.label for cluster in literature_map.clusters if cluster.label)

    evidence_refs = [
        _paper_ref(paper.paperId, paper.source)
        for paper in literature_survey.papers[:5]
    ]

    map_summary = ""
    if literature_map and isinstance(literature_map.selectionReport, dict):
        map_summary = str(
            literature_map.selectionReport.get("summary")
            or literature_map.selectionReport.get("rationale")
            or ""
        )
    cluster_summary = _text_join(
        [
            str(cluster.get("summary", "") or cluster.get("label", ""))
            for cluster in literature_survey.clusters[:3]
        ],
        "",
    )

    summary = map_summary or cluster_summary or literature_survey.summary
    thin_summary = len(summary.split()) < 10 or summary.lower().startswith("cluster:")
    if thin_summary:
        paper_bits = []
        for paper in literature_survey.papers[:3]:
            paper_summary = paper.summary.strip()
            if paper_summary:
                paper_bits.append(f"{paper.title}: {paper_summary[:220]}")
            else:
                paper_bits.append(paper.title)
        evidence_context = " ".join(paper_bits)
        summary = (
            f"The selected idea is grounded in {len(literature_survey.papers)} investigated papers "
            f"covering graph-based multi-hop RAG, evidence path retrieval, and answer verification. "
            f"{evidence_context} "
            f"The plan is motivated by: {candidate.problem}"
        ).strip()

    return PlanBackground(
        summary=summary,
        motivation=candidate.problem,
        currentLimitations=_unique(limitations),
        domainContext=_unique(domain_context),
        evidenceRefs=evidence_refs,
    )


def build_evidence_trace(
    *,
    candidate: IdeaCandidate,
    literature_map: Optional[LiteratureMap],
    reasoning_kg: Optional[ReasoningKG],
    structured_papers: List[StructuredPaper],
    probe_results: List[LiteratureProbeResult],
    graph_patches: List[GraphPatch],
    graph_evidence: Dict[str, Any],
) -> PlanEvidenceTrace:
    probe_paper_ids: List[str] = []
    for result in probe_results:
        probe_paper_ids.extend(paper.id for paper in result.papers)
    if isinstance(graph_evidence, dict):
        probe_paper_ids.extend(graph_evidence.get("probePaperIds", []))

    selected_ids = literature_map.selectedPaperIds if literature_map else [sp.rawPaperId for sp in structured_papers]
    return PlanEvidenceTrace(
        ideaCandidateId=candidate.id,
        searchNodeId=_safe_id(candidate.searchNodeId),
        pathSeedId=_safe_id(candidate.pathSeedId),
        reasoningKgId=reasoning_kg.id if reasoning_kg else "",
        literatureMapId=literature_map.id if literature_map else "",
        selectedPaperIds=_unique(selected_ids),
        structuredPaperIds=[sp.id for sp in structured_papers],
        probeResultIds=[result.id for result in probe_results],
        graphPatchIds=[patch.id for patch in graph_patches],
        probePaperIds=_unique(probe_paper_ids),
        candidateGraphEvidence=graph_evidence if isinstance(graph_evidence, dict) else {},
        reasoningTrace=graph_evidence.get("reasoningTrace", []) if isinstance(graph_evidence, dict) else [],
    )


def build_default_stages(
    *,
    candidate: IdeaCandidate,
    literature_survey: PlanLiteratureSurvey,
    gap: PlanGap,
    principle: PlanPrinciple,
    paper_type: str = "generic",
    max_stages: int = 3,
    max_steps_per_stage: int = 3,
) -> List[PlanStage]:
    template = get_plan_template(paper_type)
    metrics = candidate.expectedMetrics or []
    if not metrics:
        for paper in literature_survey.papers:
            for method_metric in paper.claims[:2]:
                text = str(method_metric.get("text", "") or method_metric.get("claimType", ""))
                if "metric" in text.lower():
                    metrics.append(text[:80])
        metrics = metrics or template.recommendedMetrics or ["primary_metric"]

    evidence_refs = [
        PlanEvidenceRef(type="paper", id=paper.paperId, source=paper.source)
        for paper in literature_survey.papers[:3]
    ]
    primary_output_prefix = candidate.title.lower().replace(" ", "_")[:40] or "plan"

    if template.paperType == "survey":
        stages = [
            PlanStage(
                id="stage-1",
                order=1,
                title="Literature Taxonomy",
                goal="Build a taxonomy over investigated papers and define comparison dimensions.",
                method="Cluster literatureSurvey.papers[] by method family, claim type, assumptions, and limitations.",
                dependsOn=[],
                steps=[
                    PlanStep(
                        id="step-1-1",
                        order=1,
                        title="Construct literature taxonomy",
                        desc="Group investigated papers into method or application categories and record the rationale for each category.",
                        method="Use literatureSurvey.papers[], LiteratureMap clusters, and paper claims to create taxonomy categories.",
                        inputFrom=[],
                        outputs=[PlanOutput(type="table", name="taxonomy_table.csv", desc="Paper taxonomy with category rationale", requiredFor=["paper", "review"])],
                        expected=[PlanExpectedMetric(metric="paper_coverage", target=f">= {len(literature_survey.papers)}", desc="All investigated papers are assigned to taxonomy categories.")],
                        evidenceRefs=evidence_refs,
                    ),
                    PlanStep(
                        id="step-1-2",
                        order=2,
                        title="Define comparison dimensions",
                        desc="Define comparison axes such as method, evidence type, assumptions, limitations, and application scope.",
                        method="Extract recurring dimensions from claims, limitations, and gap signals.",
                        inputFrom=["step-1-1"],
                        outputs=[PlanOutput(type="table", name="comparison_matrix.csv", desc="Survey comparison dimensions and paper coverage", requiredFor=["paper"])],
                        expected=[PlanExpectedMetric(metric="comparison_dimension_count", target=">= 3", desc="At least three useful comparison dimensions are defined.")],
                        evidenceRefs=[PlanEvidenceRef(type="gap", id=gap.selectedGapId, source="idea_gap")],
                    ),
                ][:max_steps_per_stage],
            ),
            PlanStage(
                id="stage-2",
                order=2,
                title="GAP and Trend Synthesis",
                goal="Synthesize unresolved problems and trends from the taxonomy.",
                method="Compare categories, limitations, and evidence signals to identify selected and supporting gaps.",
                dependsOn=["stage-1"],
                steps=[
                    PlanStep(
                        id="step-2-1",
                        order=1,
                        title="Synthesize selected GAP",
                        desc="Explain what existing literature covers, what remains unresolved, and how the selected idea frames the contribution.",
                        method="Use gap.items[], paper limitations, and principle.noveltyClaim to write the synthesis.",
                        inputFrom=["step-1-2"],
                        outputs=[PlanOutput(type="report", name="gap_synthesis.md", desc="Selected GAP and future-direction synthesis", requiredFor=["paper", "review"])],
                        expected=[PlanExpectedMetric(metric="supported_gap_count", target=">= 1", desc="At least one selected or supporting GAP is backed by paper or graph evidence.")],
                        evidenceRefs=[PlanEvidenceRef(type="gap", id=gap.selectedGapId, source="idea_gap")],
                    ),
                    PlanStep(
                        id="step-2-2",
                        order=2,
                        title="Plan survey figures",
                        desc="Define taxonomy and trend figures that downstream paper generation should create.",
                        method="Map taxonomy categories and comparison dimensions into paper-ready visual artifacts.",
                        inputFrom=["step-2-1"],
                        outputs=[PlanOutput(type="chart", name="taxonomy_and_gap_figure.png", desc="Planned survey taxonomy or trend figure", requiredFor=["paper"])],
                        expected=[PlanExpectedMetric(metric="survey_artifact_count", target=">= 2", desc="Taxonomy and comparison artifacts are planned.")],
                    ),
                ][:max_steps_per_stage],
            ),
            PlanStage(
                id="stage-3",
                order=3,
                title="Survey Handoff Readiness",
                goal="Prepare the survey package for downstream paper and review modules.",
                method="Check that taxonomy, comparison matrix, GAP synthesis, and evidence trace are complete.",
                dependsOn=["stage-2"],
                steps=[
                    PlanStep(
                        id="step-3-1",
                        order=1,
                        title="Validate survey evidence trace",
                        desc="Verify that taxonomy, comparison dimensions, and GAP synthesis can be traced to investigated papers.",
                        method="Run PlanPackage validator and inspect qualityGate before downstream handoff.",
                        inputFrom=["step-2-2"],
                        outputs=[PlanOutput(type="checkpoint", name="survey_quality_gate.json", desc="Survey handoff validation status", requiredFor=["review"])],
                        expected=[PlanExpectedMetric(metric="schema_valid", target="true", desc="Package satisfies survey handoff requirements.")],
                    )
                ][:max_steps_per_stage],
            ),
        ]
        return stages[:max_stages]

    if template.paperType == "benchmark":
        stages = [
            PlanStage(
                id="stage-1",
                order=1,
                title="Task and Dataset Protocol",
                goal="Define the benchmark task, data source, annotation protocol, and split policy.",
                method="Use the selected GAP and literature limitations to scope benchmark examples and labels.",
                dependsOn=[],
                steps=[
                    PlanStep(
                        id="step-1-1",
                        order=1,
                        title="Define benchmark task",
                        desc="Specify the task boundary, input/output format, intended evaluation setting, and exclusion rules.",
                        method="Ground task definition in gap.items[] and investigated paper limitations.",
                        inputFrom=[],
                        outputs=[PlanOutput(type="report", name="benchmark_task_spec.md", desc="Benchmark task definition and scope", requiredFor=["paper", "validation"])],
                        expected=[PlanExpectedMetric(metric="task_boundary_defined", target="true", desc="Benchmark scope is explicit before downstream data construction.")],
                        evidenceRefs=[PlanEvidenceRef(type="gap", id=gap.selectedGapId, source="idea_gap")],
                    ),
                    PlanStep(
                        id="step-1-2",
                        order=2,
                        title="Plan data and annotation protocol",
                        desc="Define data source, sampling, annotation labels, quality checks, and split policy.",
                        method="Translate benchmark requirements into dataset_card and benchmark_schema artifacts.",
                        inputFrom=["step-1-1"],
                        outputs=[
                            PlanOutput(type="report", name="dataset_card.md", desc="Planned dataset card", requiredFor=["paper", "review"]),
                            PlanOutput(type="code", name="benchmark_schema.json", desc="Benchmark schema for downstream implementation", requiredFor=["code", "validation"]),
                        ],
                        expected=[
                            PlanExpectedMetric(metric="annotation_protocol_defined", target="true", desc="Annotation and split policy are specified."),
                            PlanExpectedMetric(metric="quality_check_count", target=">= 2", desc="Leakage, bias, or agreement checks are planned."),
                        ],
                        evidenceRefs=evidence_refs,
                    ),
                ][:max_steps_per_stage],
            ),
            PlanStage(
                id="stage-2",
                order=2,
                title="Baseline Coverage and Evaluation Protocol",
                goal="Define baseline model families, scoring metrics, and benchmark evaluation protocol.",
                method="Create a baseline matrix and metric definitions without executing benchmark runs.",
                dependsOn=["stage-1"],
                steps=[
                    PlanStep(
                        id="step-2-1",
                        order=1,
                        title="Define baseline coverage",
                        desc="List baseline families, simple controls, and minimum coverage criteria for benchmark comparison.",
                        method="Use closest prior work and literature roles to define fair baselines.",
                        inputFrom=["step-1-2"],
                        outputs=[PlanOutput(type="table", name="baseline_matrix.csv", desc="Baseline families and coverage rationale", requiredFor=["validation", "paper"])],
                        expected=[PlanExpectedMetric(metric="baseline_family_count", target=">= 2", desc="Benchmark includes more than one baseline family or control.")],
                    ),
                    PlanStep(
                        id="step-2-2",
                        order=2,
                        title="Specify scoring protocol",
                        desc="Define metrics, scoring script expectations, and result table schema.",
                        method="Map benchmark task outputs to measurable scoring rules and planned artifacts.",
                        inputFrom=["step-2-1"],
                        outputs=[PlanOutput(type="metrics", name="evaluation_protocol.json", desc="Metric definitions and scoring protocol", requiredFor=["validation", "paper"])],
                        expected=[PlanExpectedMetric(metric="metric_count", target=">= 2", desc="Benchmark has at least two complementary metrics or checks.")],
                    ),
                ][:max_steps_per_stage],
            ),
            PlanStage(
                id="stage-3",
                order=3,
                title="Quality and Handoff Checks",
                goal="Plan benchmark quality, bias, leakage, and downstream handoff artifacts.",
                method="Check dataset quality and define final paper/review outputs.",
                dependsOn=["stage-2"],
                steps=[
                    PlanStep(
                        id="step-3-1",
                        order=1,
                        title="Plan quality and slice checks",
                        desc="Define leakage, annotation agreement, bias, and slice-analysis checks for downstream validation.",
                        method="Create quality gate artifacts tied to benchmark schema and evaluation protocol.",
                        inputFrom=["step-2-2"],
                        outputs=[PlanOutput(type="table", name="quality_slice_checks.csv", desc="Quality, leakage, bias, and slice checks", requiredFor=["validation", "review"])],
                        expected=[PlanExpectedMetric(metric="quality_check_count", target=">= 2", desc="At least two quality or bias checks are planned.")],
                    )
                ][:max_steps_per_stage],
            ),
        ]
        return stages[:max_stages]

    stages = [
        PlanStage(
            id="stage-1",
            order=1,
            title="Evidence and Gap Grounding",
            goal="Ground the idea in investigated literature and select the concrete research gap.",
            method="Summarize selected and probe literature, then map evidence to the selected gap.",
            dependsOn=[],
            steps=[
                PlanStep(
                    id="step-1-1",
                    order=1,
                    title="Summarize investigated papers",
                    desc="Create a structured summary of all deep-read and probe-discovered papers used by the plan.",
                    method="Use literatureSurvey.papers[] as the evidence inventory.",
                    inputFrom=[],
                    outputs=[PlanOutput(type="report", name="literature_survey.md", desc="All investigated paper summaries", requiredFor=["paper", "review"])],
                    expected=[PlanExpectedMetric(metric="covered_papers", target=f">= {len(literature_survey.papers)}", desc="Every investigated paper has a summary.")],
                    evidenceRefs=evidence_refs,
                ),
                PlanStep(
                    id="step-1-2",
                    order=2,
                    title="Select implementation gap and baseline scope",
                    desc="Confirm the selected GAP, record supporting papers or graph signals, and define the baseline/control methods for downstream comparison.",
                    method="Use gap.items[], evidenceTrace, closestPriorWork, and literatureSurvey roles to bind the gap to evidence IDs and fair baselines.",
                    inputFrom=["step-1-1"],
                    outputs=[
                        PlanOutput(type="checkpoint", name="selected_gap.json", desc="Selected gap and supporting evidence", requiredFor=["review"]),
                        PlanOutput(type="table", name="baseline_scope.csv", desc="Baseline/control methods for comparison", requiredFor=["validation", "paper"]),
                    ],
                    expected=[
                        PlanExpectedMetric(metric="selected_gap_count", target="1", desc="Exactly one primary gap is selected."),
                        PlanExpectedMetric(metric="baseline_count", target=">= 1", desc="At least one baseline or control comparison is declared."),
                    ],
                    evidenceRefs=[PlanEvidenceRef(type="gap", id=gap.selectedGapId, source="literature_map")],
                ),
                PlanStep(
                    id="step-1-3",
                    order=3,
                    title="Define baseline comparison scope",
                    desc="List the baseline or control methods that downstream validation should compare against the proposed idea.",
                    method="Use closestPriorWork, literatureSurvey roles, and selected GAP evidence to define fair comparison groups.",
                    inputFrom=["step-1-2"],
                    outputs=[PlanOutput(type="table", name="baseline_comparison_scope.csv", desc="Baseline/control methods and comparison rationale", requiredFor=["validation", "paper", "review"])],
                    expected=[PlanExpectedMetric(metric="baseline_count", target=">= 1", desc="At least one baseline or control comparison is declared.")],
                    evidenceRefs=evidence_refs or [PlanEvidenceRef(type="gap", id=gap.selectedGapId, source="idea_gap")],
                ),
            ][:max_steps_per_stage],
        ),
        PlanStage(
            id="stage-2",
            order=2,
            title="Method and Principle Specification",
            goal="Turn the selected idea into an implementation-ready method specification.",
            method="Use the candidate proposed method, reasoning path, and prior-work differences to specify the principle.",
            dependsOn=["stage-1"],
            steps=[
                PlanStep(
                    id="step-2-1",
                    order=1,
                    title="Write mechanism specification",
                    desc="Describe how the proposed method works and why it addresses the selected gap.",
                    method="Use principle.mechanism, principle.reasoningPath, and graph grounding IDs.",
                    inputFrom=["step-1-2"],
                    outputs=[PlanOutput(type="report", name="method_principle.md", desc="Mechanism and novelty claim", requiredFor=["paper", "code"])],
                    expected=[PlanExpectedMetric(metric="reasoning_path_steps", target=f">= {max(1, len(principle.reasoningPath))}", desc="Principle is grounded in a reasoning path or explicit fallback.")],
                    evidenceRefs=[PlanEvidenceRef(type="candidate", id=candidate.id, source="idea")],
                ),
                PlanStep(
                    id="step-2-2",
                    order=2,
                    title="Define implementation artifacts",
                    desc="List the planned artifacts that downstream code and validation modules should produce.",
                    method="Translate stages, outputs, and constants into code-facing artifact names.",
                    inputFrom=["step-2-1"],
                    outputs=[PlanOutput(type="code", name=f"{primary_output_prefix}_implementation_plan.json", desc="Implementation artifact specification", requiredFor=["code"])],
                    expected=[PlanExpectedMetric(metric="artifact_contracts", target=">= 1", desc="At least one code-facing artifact is specified.")],
                    codeHints={"source": "PlanPackage.stages"},
                ),
            ][:max_steps_per_stage],
        ),
        PlanStage(
            id="stage-3",
            order=3,
            title="Validation Design",
            goal="Define how later modules should validate the idea without executing validation in the plan stage.",
            method="Declare expected metrics, tables, and logs for downstream validation.",
            dependsOn=["stage-2"],
            steps=[
                PlanStep(
                    id="step-3-1",
                    order=1,
                    title="Specify validation metrics",
                    desc="Define planned metrics and target criteria for downstream validation.",
                    method="Use candidate expected metrics and literature-derived baselines when available.",
                    inputFrom=["step-2-2"],
                    outputs=[PlanOutput(type="metrics", name="validation_metrics.json", desc="Planned metrics and target values", requiredFor=["validation", "paper"])],
                    expected=[
                        PlanExpectedMetric(metric=str(metric), target="specified before implementation", desc="Pre-registered expected metric.")
                        for metric in metrics[:5]
                    ],
                ),
                PlanStep(
                    id="step-3-2",
                    order=2,
                    title="Plan ablation and robustness checks",
                    desc="Define component ablations, sensitivity checks, or failure cases that test whether the proposed mechanism is responsible for the expected improvement.",
                    method="Remove or vary core method components and compare against the baseline scope under the same metrics.",
                    inputFrom=["step-3-1"],
                    outputs=[
                        PlanOutput(type="table", name="ablation_plan.csv", desc="Planned ablation and sensitivity settings", requiredFor=["validation", "review"]),
                        PlanOutput(type="report", name="failure_analysis_plan.md", desc="Planned robustness or failure-analysis checklist", requiredFor=["paper", "review"]),
                    ],
                    expected=[
                        PlanExpectedMetric(metric="ablation_coverage", target=">= 1 core component", desc="At least one core mechanism is ablated or stress-tested."),
                        PlanExpectedMetric(metric="robustness_check_count", target=">= 1", desc="At least one robustness, sensitivity, or failure case is planned."),
                    ],
                    evidenceRefs=[PlanEvidenceRef(type="candidate", id=candidate.id, source="idea")],
                ),
                PlanStep(
                    id="step-3-3",
                    order=3,
                    title="Plan result tables and charts",
                    desc="Define table and chart artifacts expected from downstream validation.",
                    method="Map expected metrics to paper-ready tables and charts.",
                    inputFrom=["step-3-2"],
                    outputs=[
                        PlanOutput(type="table", name="planned_results_table.csv", desc="Planned paper table schema", requiredFor=["paper"]),
                        PlanOutput(type="chart", name="planned_results_chart.png", desc="Planned chart artifact", requiredFor=["paper"]),
                    ],
                    expected=[PlanExpectedMetric(metric="reportable_artifacts", target=">= 2", desc="At least one table and one chart are planned.")],
                ),
            ][:max_steps_per_stage],
        ),
        PlanStage(
            id="stage-4",
            order=4,
            title="Review and Handoff",
            goal="Prepare the package for downstream code, paper, review, and validation modules.",
            method="Check schema, evidence trace, and downstream contract completeness.",
            dependsOn=["stage-3"],
            steps=[
                PlanStep(
                    id="step-4-1",
                    order=1,
                    title="Validate evidence trace",
                    desc="Verify that claims, gaps, and planned steps can be traced to candidate and literature evidence.",
                    method="Run PlanPackage validator and inspect qualityGate.",
                    inputFrom=["step-3-2"],
                    outputs=[PlanOutput(type="checkpoint", name="quality_gate.json", desc="PlanPackage validation status", requiredFor=["review"])],
                    expected=[PlanExpectedMetric(metric="schema_valid", target="true", desc="Package satisfies the hard output schema.")],
                )
            ][:max_steps_per_stage],
        ),
    ]
    return stages[:max_stages]


def build_contribution_statements(
    *,
    candidate: IdeaCandidate,
    gap: PlanGap,
    principle: PlanPrinciple,
    stages: List[PlanStage],
) -> List[PlanContributionStatement]:
    """Build paper-facing contribution claims with planned validation links."""
    stage_ids = [stage.id for stage in stages]
    step_ids = [step.id for stage in stages for step in stage.steps]

    def collect_refs(selected_stages: List[PlanStage]) -> List[PlanEvidenceRef]:
        refs: List[PlanEvidenceRef] = []
        seen: set[tuple[str, str]] = set()
        for stage in selected_stages:
            for step in stage.steps:
                for ref in step.evidenceRefs:
                    key = (ref.type, ref.id)
                    if ref.type and ref.id and key not in seen:
                        seen.add(key)
                        refs.append(ref)
        for ref in [
            PlanEvidenceRef(type="candidate", id=candidate.id, source="idea"),
            PlanEvidenceRef(type="principle", id="principle", source="idea_principle"),
            PlanEvidenceRef(type="gap", id=gap.selectedGapId, source="idea_gap"),
        ]:
            key = (ref.type, ref.id)
            if key not in seen:
                seen.add(key)
                refs.append(ref)
        return refs

    method_stages = stages[:2] or stages
    evaluation_stages = stages[1:] if len(stages) > 1 else stages
    method_steps = [step.id for stage in method_stages for step in stage.steps]
    evaluation_steps = [step.id for stage in evaluation_stages for step in stage.steps]

    mechanism_sentence = (principle.mechanism or candidate.proposedMethod or candidate.keyInsight).split(".")[0].strip()
    novelty_basis = principle.noveltyClaim or candidate.keyInsight
    selected_gap = next(
        (item.statement for item in gap.items if item.id == gap.selectedGapId),
        gap.summary,
    )

    output_names = [
        output.name
        for stage in stages[:2]
        for step in stage.steps
        for output in step.outputs
    ][:6]
    metric_names = _unique(
        expected.metric
        for stage in evaluation_stages
        for step in stage.steps
        for expected in step.expected
    )[:8]

    contributions = [
        PlanContributionStatement(
            id="contribution-1",
            type="method",
            statement=(
                f"A method contribution centered on {candidate.title}: "
                f"{mechanism_sentence.rstrip('.')}. "
                f"It targets the selected gap: {selected_gap.rstrip('.')}."
            ),
            noveltyBasis=novelty_basis,
            validationStageIds=[stage.id for stage in method_stages],
            validationStepIds=method_steps,
            evidenceRefs=collect_refs(method_stages),
        ),
        PlanContributionStatement(
            id="contribution-2",
            type="system",
            statement=(
                "A reproducible implementation contribution that decomposes the proposed method into "
                f"{', '.join(stage.title for stage in method_stages)}"
                + (f", producing artifacts such as {', '.join(output_names)}." if output_names else ".")
            ),
            noveltyBasis="Operationalizes the proposed mechanism as explicit, traceable downstream artifacts.",
            validationStageIds=[stage.id for stage in method_stages],
            validationStepIds=method_steps,
            evidenceRefs=collect_refs(method_stages),
        ),
        PlanContributionStatement(
            id="contribution-3",
            type="evaluation",
            statement=(
                "An evaluation contribution that tests the central hypothesis through "
                f"{', '.join(stage.title for stage in evaluation_stages)}"
                + (f", using planned metrics including {', '.join(metric_names)}." if metric_names else ".")
            ),
            noveltyBasis="Connects each claimed improvement to planned benchmark, ablation, or robustness evidence.",
            validationStageIds=[stage.id for stage in evaluation_stages] or stage_ids,
            validationStepIds=evaluation_steps or step_ids,
            evidenceRefs=collect_refs(evaluation_stages),
        ),
    ]
    return contributions


def build_source_field_map(*, plan_source: str) -> PlanSourceFieldMap:
    """Document how new PlanPackage fields align to existing idea v5 outputs."""
    return PlanSourceFieldMap(
        idea=[
            "IdeaCandidate.title",
            "IdeaCandidate.problem",
            "IdeaCandidate.hypothesisStatement",
            "IdeaCandidate.keyInsight",
            "IdeaCandidate.proposedMethod",
            "IdeaCandidate.expectedOutcome",
            "IdeaCandidate.scores",
            "RankedIdeaOutput.priorWorkComparisons",
            "RankedIdeaOutput.critiques",
        ],
        background=[
            "StructuredPaper.summary",
            "StructuredPaper.findings",
            "StructuredPaper.limitations",
            "LiteratureMap.clusters",
            "LiteratureMap.selectionReport",
            "IdeaCritique.weaknesses",
        ],
        literatureSurvey=[
            "StructuredPaper[]",
            "RawPaper[]",
            "LiteratureProbeResult[].papers",
            "LiteratureMap.clusters",
        ],
        gap=[
            "LiteratureMap.gaps",
            "StructuredPaper.limitations",
            "IdeaCritique.weaknesses",
            "GraphPatch.patchType=contradiction",
            "CandidateGraphEvidence.supportingPaperIds",
        ],
        principle=[
            "IdeaCandidate.proposedMethod",
            "IdeaCandidate.keyInsight",
            "IdeaCandidate.hypothesisStatement",
            "IdeaCandidate.expectedOutcome",
            "CandidateGraphEvidence.reasoningTrace",
            "ReasoningPathSeed[]",
            "ReasoningKG.entities",
            "ReasoningKG.relations",
            "RankedIdeaOutput.priorWorkComparisons",
        ],
        contributionStatement=[
            "IdeaCandidate.title",
            "IdeaCandidate.proposedMethod",
            "PlanPackage.gap.selectedGapId",
            "PlanPackage.principle.noveltyClaim",
            "PlanPackage.stages[].steps[].outputs",
            "PlanPackage.stages[].steps[].expected",
            "PlanPackage.stages[].steps[].evidenceRefs",
        ],
        evidenceTrace=[
            "IdeaCandidate.searchNodeId",
            "IdeaCandidate.pathSeedId",
            "CandidateGraphEvidence",
            "IdeaSearchTree.id",
            "LiteratureMap.id",
            "ReasoningKG.id",
            "LiteratureProbeResult[].id",
            "GraphPatch[].id",
        ],
        implementationPlan=[
            plan_source,
            "PlanPackage.idea",
            "PlanPackage.background",
            "PlanPackage.gap",
            "PlanPackage.principle",
            "PlanPackage.literatureSurvey",
        ],
    )


def build_raw_idea_outputs(
    *,
    candidate: IdeaCandidate,
    ranked_output: Optional[RankedIdeaOutput],
    literature_map: Optional[LiteratureMap],
    reasoning_kg: Optional[ReasoningKG],
    path_seeds: List[ReasoningPathSeed],
    graph_evidence: Dict[str, Any],
    prior_work: List[Dict[str, Any]],
    critique: Dict[str, Any],
    structured_papers: List[StructuredPaper],
    probe_results: List[LiteratureProbeResult],
    graph_patches: List[GraphPatch],
) -> Dict[str, Any]:
    """Keep a compact adapter view of old idea outputs for downstream migration."""
    return {
        "ideaCandidate": {
            "id": candidate.id,
            "title": candidate.title,
            "problem": candidate.problem,
            "hypothesisStatement": _candidate_hypothesis(candidate),
            "keyInsight": candidate.keyInsight,
            "proposedMethod": _candidate_method(candidate, critique),
            "expectedOutcome": _candidate_expected_outcome(candidate, critique),
            "searchNodeId": _safe_id(candidate.searchNodeId),
            "pathSeedId": _safe_id(candidate.pathSeedId),
            "reasoningPathId": _safe_id(candidate.reasoningPathId),
            "expectedMetrics": candidate.expectedMetrics,
        },
        "candidateGraphEvidence": graph_evidence,
        "rankedOutput": {
            "id": ranked_output.id if ranked_output else "",
            "topCandidateId": ranked_output.topCandidateId if ranked_output else "",
            "priorWorkComparisons": prior_work,
            "critique": critique,
        },
        "literatureMap": {
            "id": literature_map.id if literature_map else "",
            "selectedPaperIds": literature_map.selectedPaperIds if literature_map else [],
            "gaps": [gap.model_dump() for gap in literature_map.gaps] if literature_map else [],
            "frontiers": [frontier.model_dump() for frontier in literature_map.frontiers] if literature_map else [],
            "clusters": [cluster.model_dump() for cluster in literature_map.clusters] if literature_map else [],
            "selectionReport": literature_map.selectionReport if literature_map else {},
        },
        "reasoningKg": {
            "id": reasoning_kg.id if reasoning_kg else "",
            "entityIds": [entity.entityId for entity in reasoning_kg.entities] if reasoning_kg else [],
            "relationIds": [relation.relationId for relation in reasoning_kg.relations] if reasoning_kg else [],
        },
        "reasoningPathSeeds": [
            {
                "seedId": seed.seedId,
                "sourcePaperIds": seed.sourcePaperIds,
                "sourceClaimIds": seed.sourceClaimIds,
                "linkedGapIds": seed.linkedGapIds,
                "rationale": seed.rationale,
            }
            for seed in path_seeds
        ],
        "structuredPaperIds": [paper.id for paper in structured_papers],
        "probeResultIds": [result.id for result in probe_results],
        "graphPatchIds": [patch.id for patch in graph_patches],
    }


def build_plan_package(
    *,
    idea_session_id: str,
    candidate: IdeaCandidate,
    ranked_output: Optional[RankedIdeaOutput],
    search_tree: Optional[IdeaSearchTree],
    literature_map: Optional[LiteratureMap],
    reasoning_kg: Optional[ReasoningKG],
    path_seeds: List[ReasoningPathSeed],
    raw_papers: List[RawPaper],
    structured_papers: List[StructuredPaper],
    probe_results: List[LiteratureProbeResult],
    graph_patches: List[GraphPatch],
    handoff: Optional[BFTSHandoff],
    user_notes: Optional[str] = None,
    paper_type: str = "generic",
    max_stages: int = 3,
    max_steps_per_stage: int = 3,
) -> PlanPackage:
    graph_evidence = _graph_evidence(candidate, ranked_output)
    prior_work = _candidate_prior_work(candidate, ranked_output)
    critique = _candidate_critique(candidate, ranked_output)

    literature_survey = build_literature_survey(
        candidate=candidate,
        raw_papers=raw_papers,
        structured_papers=structured_papers,
        literature_map=literature_map,
        probe_results=probe_results,
    )
    gap = build_gap(
        candidate=candidate,
        literature_map=literature_map,
        structured_papers=structured_papers,
        graph_patches=graph_patches,
        prior_work=prior_work,
        critique=critique,
        graph_evidence=graph_evidence,
    )
    principle = build_principle(
        candidate=candidate,
        reasoning_kg=reasoning_kg,
        path_seeds=path_seeds,
        graph_evidence=graph_evidence,
        prior_work=prior_work,
        critique=critique,
        probe_results=probe_results,
        graph_patches=graph_patches,
    )
    background = build_background(
        candidate=candidate,
        literature_survey=literature_survey,
        literature_map=literature_map,
        critique=critique,
    )
    evidence_trace = build_evidence_trace(
        candidate=candidate,
        literature_map=literature_map,
        reasoning_kg=reasoning_kg,
        structured_papers=structured_papers,
        probe_results=probe_results,
        graph_patches=graph_patches,
        graph_evidence=graph_evidence,
    )

    research_question = ""
    hypothesis = ""
    if candidate.draftPlan:
        research_question = candidate.draftPlan.researchQuestion
        hypothesis = candidate.draftPlan.hypothesis
    research_question = research_question or (
        f"How can {candidate.title} address: {candidate.problem}?"
    )
    hypothesis = hypothesis or _candidate_hypothesis(candidate)
    proposed_method = _candidate_method(candidate, critique)
    expected_outcome = _candidate_expected_outcome(candidate, critique)

    constants: Dict[str, Any] = {
        "ideaSessionId": idea_session_id,
        "ideaCandidateId": candidate.id,
        "planStage": "idea_implementation_plan",
    }
    if user_notes:
        constants["userNotes"] = user_notes

    stages = build_default_stages(
        candidate=candidate,
        literature_survey=literature_survey,
        gap=gap,
        principle=principle,
        paper_type=paper_type,
        max_stages=max_stages,
        max_steps_per_stage=max_steps_per_stage,
    )

    source = PlanSource(
        ideaSessionId=idea_session_id,
        ideaCandidateId=candidate.id,
        rankedOutputId=ranked_output.id if ranked_output else "",
        searchTreeId=search_tree.id if search_tree else "",
        searchNodeId=_safe_id(candidate.searchNodeId),
        pathSeedId=_safe_id(candidate.pathSeedId),
        reasoningKgId=reasoning_kg.id if reasoning_kg else "",
        literatureMapId=literature_map.id if literature_map else "",
        bftsHandoffId=handoff.id if handoff else "",
    )

    critique_summary = ""
    if isinstance(critique, dict):
        critique_summary = critique.get("overallCritique", "") or critique.get("overallAssessment", "")

    return PlanPackage(
        packageId=generate_plan_package_id(),
        source=source,
        idea=PlanIdeaSummary(
            id=candidate.id,
            title=candidate.title,
            problem=candidate.problem,
            hypothesisStatement=hypothesis,
            keyInsight=candidate.keyInsight,
            proposedMethod=proposed_method,
            expectedOutcome=expected_outcome,
            scores=candidate.scores.model_dump(),
            critiqueSummary=critique_summary,
            closestPriorWork=prior_work,
        ),
        background=background,
        literatureSurvey=literature_survey,
        gap=gap,
        principle=principle,
        contributionStatement=build_contribution_statements(
            candidate=candidate,
            gap=gap,
            principle=principle,
            stages=stages,
        ),
        researchQuestion=research_question,
        hypothesis=hypothesis,
        constants=constants,
        stages=stages,
        evidenceTrace=evidence_trace,
        generation=PlanGenerationMetadata(
            mode="deterministic",
            promptVersion="plan-package-adapter-v1",
            llmUsedSections=[],
            fallbackUsed=False,
        ),
        sourceFields=build_source_field_map(plan_source="deterministic fallback stage builder"),
        rawIdeaOutputs=build_raw_idea_outputs(
            candidate=candidate,
            ranked_output=ranked_output,
            literature_map=literature_map,
            reasoning_kg=reasoning_kg,
            path_seeds=path_seeds,
            graph_evidence=graph_evidence,
            prior_work=prior_work,
            critique=critique,
            structured_papers=structured_papers,
            probe_results=probe_results,
            graph_patches=graph_patches,
        ),
    )
