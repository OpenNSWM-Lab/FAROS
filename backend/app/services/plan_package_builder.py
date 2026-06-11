"""Deterministic builders for PlanPackage context and fallback plan fields."""

from __future__ import annotations

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
    raw_papers: List[RawPaper],
    structured_papers: List[StructuredPaper],
    literature_map: Optional[LiteratureMap],
    probe_results: List[LiteratureProbeResult],
) -> PlanLiteratureSurvey:
    raw_by_id = {paper.id: paper for paper in raw_papers}
    papers: List[PlanLiteraturePaperSummary] = []
    structured_raw_ids: set[str] = set()

    for sp in structured_papers:
        raw = raw_by_id.get(sp.rawPaperId) or raw_by_id.get(sp.id)
        paper_id = sp.rawPaperId or sp.id
        structured_raw_ids.add(paper_id)
        role = sp.graph1Roles[0] if sp.graph1Roles else "supporting_evidence"
        summary = sp.summary or sp.abstract or (raw.abstract if raw else "") or f"Structured summary for {sp.title}"
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
                summary=summary,
                methods=[_method_dict(method) for method in sp.methods],
                findings=[_finding_dict(finding) for finding in sp.findings],
                limitations=sp.limitations,
                claims=[_claim_dict(claim) for claim in sp.claims],
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
    critique: Dict[str, Any],
    graph_evidence: Dict[str, Any],
) -> PlanGap:
    items: List[PlanGapItem] = []
    for index, gap in enumerate(literature_map.gaps if literature_map else [], start=1):
        statement = gap.direction or gap.evidence or f"Literature gap {index}"
        items.append(
            PlanGapItem(
                id=f"gap-{index}",
                statement=statement,
                severity="high" if gap.confidence >= 0.7 else "medium",
                whyUnsolved=gap.evidence,
                supportedByPaperIds=gap.paperIds,
                linkedGraphSignalIds=gap.clusterIds + gap.entityHints,
            )
        )

    limitation_papers = [sp for sp in structured_papers if sp.limitations]
    if limitation_papers:
        statement = limitation_papers[0].limitations[0]
        items.append(
            PlanGapItem(
                id=f"gap-{len(items) + 1}",
                statement=statement,
                severity="medium",
                whyUnsolved="Reported limitation from investigated literature.",
                supportedByPaperIds=[limitation_papers[0].rawPaperId],
                supportedByClaimIds=[claim.claimId for claim in limitation_papers[0].claims[:3]],
            )
        )

    weakness = ""
    weaknesses = critique.get("weaknesses") if isinstance(critique, dict) else None
    if isinstance(weaknesses, list) and weaknesses:
        weakness = str(weaknesses[0])

    contradiction_ids = [
        patch.id
        for patch in graph_patches
        if patch.patchType == "contradiction"
    ]

    if not items:
        support_ids = graph_evidence.get("supportingPaperIds", []) if isinstance(graph_evidence, dict) else []
        items.append(
            PlanGapItem(
                id="gap-1",
                statement=candidate.problem or weakness or "The selected idea addresses a gap identified by the idea pipeline.",
                severity="medium",
                whyUnsolved=weakness or "Requires implementation planning before downstream validation.",
                supportedByPaperIds=support_ids,
                linkedGraphSignalIds=contradiction_ids,
            )
        )

    return PlanGap(
        summary=items[0].statement,
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
    path_seed_ids = _unique(
        list(graph_evidence.get("supportingPathSeedIds", []))
        + [candidate.pathSeedId]
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
        summary=candidate.proposedMethod or candidate.keyInsight or candidate.title,
        mechanism=candidate.proposedMethod or candidate.keyInsight,
        noveltyClaim=novelty_claim,
        assumptions=_unique(assumptions),
        risks=_unique(risks),
        reasoningPath=reasoning_path,
        graphGrounding=PlanGraphGrounding(
            entityIds=_unique(entity_ids),
            relationIds=_unique(relation_ids),
            pathSeedIds=path_seed_ids,
            searchNodeIds=_unique([candidate.searchNodeId]),
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

    return PlanBackground(
        summary=map_summary or cluster_summary or literature_survey.summary,
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
        searchNodeId=candidate.searchNodeId,
        pathSeedId=candidate.pathSeedId,
        reasoningKgId=reasoning_kg.id if reasoning_kg else None,
        literatureMapId=literature_map.id if literature_map else None,
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
    max_stages: int = 4,
    max_steps_per_stage: int = 5,
) -> List[PlanStage]:
    metrics = candidate.expectedMetrics or []
    if not metrics:
        for paper in literature_survey.papers:
            for method_metric in paper.claims[:2]:
                text = str(method_metric.get("text", "") or method_metric.get("claimType", ""))
                if "metric" in text.lower():
                    metrics.append(text[:80])
        metrics = metrics or ["primary_metric"]

    evidence_refs = [
        PlanEvidenceRef(type="paper", id=paper.paperId, source=paper.source)
        for paper in literature_survey.papers[:3]
    ]
    primary_output_prefix = candidate.title.lower().replace(" ", "_")[:40] or "plan"

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
                    title="Select implementation gap",
                    desc="Confirm the selected GAP and record which papers or graph signals support it.",
                    method="Use gap.items[] and evidenceTrace to bind the gap to evidence IDs.",
                    inputFrom=["step-1-1"],
                    outputs=[PlanOutput(type="checkpoint", name="selected_gap.json", desc="Selected gap and supporting evidence", requiredFor=["review"])],
                    expected=[PlanExpectedMetric(metric="selected_gap_count", target="1", desc="Exactly one primary gap is selected.")],
                    evidenceRefs=[PlanEvidenceRef(type="gap", id=gap.selectedGapId, source="literature_map")],
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
                    title="Plan result tables and charts",
                    desc="Define table and chart artifacts expected from downstream validation.",
                    method="Map expected metrics to paper-ready tables and charts.",
                    inputFrom=["step-3-1"],
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
            "hypothesisStatement": candidate.hypothesisStatement,
            "keyInsight": candidate.keyInsight,
            "proposedMethod": candidate.proposedMethod,
            "expectedOutcome": candidate.expectedOutcome,
            "searchNodeId": candidate.searchNodeId,
            "pathSeedId": candidate.pathSeedId,
            "reasoningPathId": candidate.reasoningPathId,
            "expectedMetrics": candidate.expectedMetrics,
        },
        "candidateGraphEvidence": graph_evidence,
        "rankedOutput": {
            "id": ranked_output.id if ranked_output else None,
            "topCandidateId": ranked_output.topCandidateId if ranked_output else None,
            "priorWorkComparisons": prior_work,
            "critique": critique,
        },
        "literatureMap": {
            "id": literature_map.id if literature_map else None,
            "selectedPaperIds": literature_map.selectedPaperIds if literature_map else [],
            "gaps": [gap.model_dump() for gap in literature_map.gaps] if literature_map else [],
            "frontiers": [frontier.model_dump() for frontier in literature_map.frontiers] if literature_map else [],
            "clusters": [cluster.model_dump() for cluster in literature_map.clusters] if literature_map else [],
            "selectionReport": literature_map.selectionReport if literature_map else {},
        },
        "reasoningKg": {
            "id": reasoning_kg.id if reasoning_kg else None,
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
    plan_session_id: Optional[str] = None,
    user_notes: Optional[str] = None,
    max_stages: int = 4,
    max_steps_per_stage: int = 5,
) -> PlanPackage:
    graph_evidence = _graph_evidence(candidate, ranked_output)
    prior_work = _candidate_prior_work(candidate, ranked_output)
    critique = _candidate_critique(candidate, ranked_output)

    literature_survey = build_literature_survey(
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
    hypothesis = hypothesis or candidate.hypothesisStatement or candidate.keyInsight

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
        max_stages=max_stages,
        max_steps_per_stage=max_steps_per_stage,
    )

    source = PlanSource(
        ideaSessionId=idea_session_id,
        planSessionId=plan_session_id,
        ideaCandidateId=candidate.id,
        rankedOutputId=ranked_output.id if ranked_output else None,
        searchTreeId=search_tree.id if search_tree else None,
        searchNodeId=candidate.searchNodeId,
        pathSeedId=candidate.pathSeedId,
        reasoningKgId=reasoning_kg.id if reasoning_kg else None,
        literatureMapId=literature_map.id if literature_map else None,
        bftsHandoffId=handoff.id if handoff else None,
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
            hypothesisStatement=candidate.hypothesisStatement,
            keyInsight=candidate.keyInsight,
            proposedMethod=candidate.proposedMethod,
            expectedOutcome=candidate.expectedOutcome,
            scores=candidate.scores.model_dump(),
            critiqueSummary=critique_summary,
            closestPriorWork=prior_work,
        ),
        background=background,
        literatureSurvey=literature_survey,
        gap=gap,
        principle=principle,
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
