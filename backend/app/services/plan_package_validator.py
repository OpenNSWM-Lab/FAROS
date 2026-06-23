"""Validation for PlanPackage contracts."""

import re

from app.models.plan_package import PlanOutputType, PlanPackage, PlanQualityGate
from app.services.plan_package_plan_quality import missing_plan_roles


def _has_text(value: str | None) -> bool:
    return bool(value and value.strip())


def _allowed_evidence_ids(package: PlanPackage) -> dict[str, set[str]]:
    return {
        "candidate": {package.idea.id},
        "gap": {item.id for item in package.gap.items},
        "paper": {paper.paperId for paper in package.literatureSurvey.papers},
        "probe": set(package.evidenceTrace.probeResultIds),
        "graph_patch": set(package.evidenceTrace.graphPatchIds),
        "path_seed": set(package.principle.graphGrounding.pathSeedIds)
        | ({package.source.pathSeedId} if package.source.pathSeedId else set()),
        "kg_entity": set(package.principle.graphGrounding.entityIds),
        "kg_relation": set(package.principle.graphGrounding.relationIds),
        "literature_map": {package.evidenceTrace.literatureMapId} if package.evidenceTrace.literatureMapId else set(),
        "reasoning_kg": {package.evidenceTrace.reasoningKgId} if package.evidenceTrace.reasoningKgId else set(),
        "principle": {"principle"},
    }


def _contains_executed_result_language(text: str) -> bool:
    lowered = text.lower()
    phrases = [
        "results show",
        "experiments show",
        "we observed",
        "we achieved",
        "实验结果表明",
        "结果显示",
    ]
    return any(phrase in lowered for phrase in phrases)


def _is_placeholder_text(text: str) -> bool:
    placeholders = {
        "Plan step description.",
        "Implementation planning method.",
        "Default plan step",
        "Implementation planning goal.",
        "Structured implementation planning.",
    }
    return text.strip() in placeholders


_TOPIC_STOPWORDS = {
    "about", "above", "after", "again", "against", "also", "among", "and",
    "are", "based", "between", "can", "could", "does", "for", "from", "how",
    "into", "its", "may", "method", "methods", "model", "models", "more",
    "paper", "plan", "research", "should", "study", "than", "that", "the",
    "their", "this", "through", "using", "what", "when", "where", "with",
    "within", "would", "是否", "如何", "研究", "方法", "模型", "系统",
}


def _topic_terms(package: PlanPackage) -> list[str]:
    """Extract topic anchors used to catch evidence and plan drift."""
    text = " ".join([
        str(package.constants.get("seedQuery", "")),
        str(package.constants.get("domain", "")),
        package.researchQuestion,
        package.hypothesis,
        package.idea.title,
        package.idea.problem,
        package.idea.hypothesisStatement,
        package.idea.proposedMethod,
        package.idea.expectedOutcome,
        package.gap.summary,
        package.principle.summary,
        package.principle.mechanism,
        package.principle.noveltyClaim,
    ]).lower().replace("-", " ")
    if "rag" in text:
        text = f"{text} retrieval augmented generation"
    terms: list[str] = []
    for token in re.findall(r"[a-zA-Z][a-zA-Z0-9]{2,}|[\u4e00-\u9fff]{2,}", text):
        if token in _TOPIC_STOPWORDS:
            continue
        if token not in terms:
            terms.append(token)
    priority = [
        "citation", "faithfulness", "faithful", "uncertainty", "gating",
        "confidence", "retrieval", "augmented", "rag", "hallucination",
        "attribution", "provenance", "negative", "sampling", "verification",
    ]
    original_order = {term: index for index, term in enumerate(terms)}
    terms.sort(key=lambda term: (
        priority.index(term) if term in priority else len(priority),
        original_order[term],
    ))
    return terms[:24]


def _hit_count(text: str, terms: list[str]) -> int:
    lowered = text.lower().replace("-", " ")
    return sum(1 for term in terms if term and term.lower() in lowered)


def _semantic_text_for_paper(paper) -> str:
    return " ".join([
        paper.title,
        paper.summary,
        " ".join(str(item) for item in paper.methods),
        " ".join(str(item) for item in paper.findings),
        " ".join(str(item) for item in paper.limitations),
        " ".join(str(item) for item in paper.claims),
    ])


def validate_plan_package(package: PlanPackage) -> PlanQualityGate:
    """Validate hard implementation fields and evidence coverage."""
    schema_errors: list[str] = []
    evidence_errors: list[str] = []
    warnings: list[str] = []

    if not _has_text(package.researchQuestion):
        schema_errors.append("researchQuestion is required")
    if not _has_text(package.hypothesis):
        schema_errors.append("hypothesis is required for a complete PlanPackage")
    if not _has_text(package.idea.hypothesisStatement):
        schema_errors.append("idea.hypothesisStatement is required for a complete PlanPackage")
    if not _has_text(package.idea.proposedMethod):
        schema_errors.append("idea.proposedMethod is required for a complete PlanPackage")
    if not _has_text(package.idea.expectedOutcome):
        schema_errors.append("idea.expectedOutcome is required for a complete PlanPackage")
    for field_name, value in [
        ("idea.proposedMethod", package.idea.proposedMethod),
        ("idea.expectedOutcome", package.idea.expectedOutcome),
        ("principle.summary", package.principle.summary if package.principle else ""),
        ("principle.mechanism", package.principle.mechanism if package.principle else ""),
    ]:
        if _contains_executed_result_language(value or ""):
            evidence_errors.append(f"{field_name} appears to claim executed results")

    if not package.background or not _has_text(package.background.summary):
        schema_errors.append("background.summary is required")
    if not package.literatureSurvey:
        schema_errors.append("literatureSurvey is required")
    if not package.gap or not package.gap.items:
        schema_errors.append("gap.items must contain at least one gap")
    if not package.principle or not _has_text(package.principle.summary):
        schema_errors.append("principle.summary is required")

    stage_ids: set[str] = set()
    step_ids: set[str] = set()
    input_refs: list[tuple[str, str]] = []
    all_step_evidence_refs: list[tuple[str, str, str]] = []
    allowed_evidence_ids = _allowed_evidence_ids(package)

    if not package.stages:
        schema_errors.append("stages must contain at least one stage")
    if len(package.stages) > 5:
        schema_errors.append("stages must contain at most 5 stages for idea+plan handoff")
    elif len(package.stages) > 3:
        warnings.append("PlanPackage has more than 3 stages; consider a shorter handoff plan")

    total_steps = sum(len(stage.steps) for stage in package.stages)
    if total_steps > 12:
        warnings.append("PlanPackage has more than 12 steps; downstream handoff may be too broad")

    missing_roles = missing_plan_roles(package)
    if missing_roles:
        schema_errors.append(
            "stages missing required single-plan roles: "
            + "; ".join(f"{role['label']} ({role['repairHint']})" for role in missing_roles)
        )

    for stage in package.stages:
        if stage.id in stage_ids:
            schema_errors.append(f"duplicate stage id: {stage.id}")
        stage_ids.add(stage.id)
        if not _has_text(stage.id):
            schema_errors.append("stages[].id is required")
        if not _has_text(stage.title):
            schema_errors.append(f"{stage.id}.title is required")
        if not _has_text(stage.goal):
            schema_errors.append(f"{stage.id}.goal is required")
        if not _has_text(stage.method):
            schema_errors.append(f"{stage.id}.method is required")
        if not stage.steps:
            schema_errors.append(f"{stage.id}.steps must contain at least one step")

        for step in stage.steps:
            if step.id in step_ids:
                schema_errors.append(f"duplicate step id: {step.id}")
            step_ids.add(step.id)
            if not _has_text(step.id):
                schema_errors.append(f"{stage.id}.steps[].id is required")
            if not _has_text(step.title):
                schema_errors.append(f"{step.id}.title is required")
            if not _has_text(step.desc):
                schema_errors.append(f"{step.id}.desc is required")
            if not _has_text(step.method):
                schema_errors.append(f"{step.id}.method is required")
            if _is_placeholder_text(step.title) or _is_placeholder_text(step.desc) or _is_placeholder_text(step.method):
                schema_errors.append(f"{step.id} contains placeholder implementation text")
            if not step.outputs:
                schema_errors.append(f"{step.id}.outputs must contain at least one output")
            if not step.expected:
                schema_errors.append(f"{step.id}.expected must contain at least one expected metric")

            for ref in step.inputFrom:
                input_refs.append((step.id, ref))

            for ref in step.evidenceRefs:
                if ref.type or ref.id:
                    all_step_evidence_refs.append((step.id, ref.type, ref.id))
                    if ref.type not in allowed_evidence_ids:
                        evidence_errors.append(f"{step.id}.evidenceRefs has unsupported type: {ref.type}")
                    elif ref.id not in allowed_evidence_ids[ref.type]:
                        evidence_errors.append(f"{step.id}.evidenceRefs references unknown {ref.type}: {ref.id}")

            for output in step.outputs:
                try:
                    PlanOutputType(output.type)
                except ValueError:
                    schema_errors.append(f"{step.id}.outputs[].type is invalid: {output.type}")
                if not _has_text(output.name):
                    schema_errors.append(f"{step.id}.outputs[].name is required")

            for expected in step.expected:
                if not _has_text(expected.metric):
                    schema_errors.append(f"{step.id}.expected[].metric is required")
                if not _has_text(expected.target):
                    schema_errors.append(f"{step.id}.expected[].target is required")
                if _contains_executed_result_language(expected.target) or _contains_executed_result_language(expected.desc):
                    evidence_errors.append(f"{step.id}.expected[] appears to claim executed results")

    for stage in package.stages:
        for dep in stage.dependsOn:
            if dep not in stage_ids:
                schema_errors.append(f"{stage.id}.dependsOn references unknown stage: {dep}")

    for step_id, ref in input_refs:
        if ref not in step_ids:
            schema_errors.append(f"{step_id}.inputFrom references unknown step: {ref}")

    if not package.contributionStatement:
        schema_errors.append("contributionStatement must contain at least one contribution")
    contribution_ids: set[str] = set()
    allowed_contribution_types = {"method", "system", "evaluation", "analysis", "application"}
    for contribution in package.contributionStatement:
        if contribution.id in contribution_ids:
            schema_errors.append(f"duplicate contribution id: {contribution.id}")
        contribution_ids.add(contribution.id)
        if not _has_text(contribution.id):
            schema_errors.append("contributionStatement[].id is required")
        if contribution.type not in allowed_contribution_types:
            schema_errors.append(
                f"{contribution.id}.type is invalid: {contribution.type}"
            )
        if not _has_text(contribution.statement):
            schema_errors.append(f"{contribution.id}.statement is required")
        if _contains_executed_result_language(contribution.statement):
            evidence_errors.append(f"{contribution.id}.statement appears to claim executed results")
        if not contribution.validationStageIds:
            schema_errors.append(f"{contribution.id}.validationStageIds must not be empty")
        if not contribution.validationStepIds:
            schema_errors.append(f"{contribution.id}.validationStepIds must not be empty")
        for stage_id in contribution.validationStageIds:
            if stage_id not in stage_ids:
                schema_errors.append(
                    f"{contribution.id}.validationStageIds references unknown stage: {stage_id}"
                )
        for step_id in contribution.validationStepIds:
            if step_id not in step_ids:
                schema_errors.append(
                    f"{contribution.id}.validationStepIds references unknown step: {step_id}"
                )
        if not contribution.evidenceRefs:
            warnings.append(f"{contribution.id} has no evidenceRefs")
        for ref in contribution.evidenceRefs:
            if ref.type not in allowed_evidence_ids:
                evidence_errors.append(
                    f"{contribution.id}.evidenceRefs has unsupported type: {ref.type}"
                )
            elif ref.id not in allowed_evidence_ids[ref.type]:
                evidence_errors.append(
                    f"{contribution.id}.evidenceRefs references unknown {ref.type}: {ref.id}"
                )

    gap_ids = {item.id for item in package.gap.items}
    if package.gap.selectedGapId not in gap_ids:
        schema_errors.append("gap.selectedGapId must reference gap.items[].id")
    selected_gap = next(
        (item for item in package.gap.items if item.id == package.gap.selectedGapId),
        None,
    )
    if selected_gap:
        if selected_gap.kind != "selected":
            schema_errors.append("gap.selectedGapId must reference an item with kind=selected")
        if not _has_text(selected_gap.existingCoverage):
            schema_errors.append("selected GAP must explain existingCoverage")
        if not _has_text(selected_gap.unresolvedIssue):
            schema_errors.append("selected GAP must explain unresolvedIssue")
        if not _has_text(selected_gap.proposedEntry):
            schema_errors.append("selected GAP must explain proposedEntry")
        if not _has_text(selected_gap.boundary):
            schema_errors.append("selected GAP must define its boundary")
        if not selected_gap.validationNeeds:
            schema_errors.append("selected GAP must define validationNeeds")
        if not selected_gap.supportedByPaperIds and not selected_gap.supportedByClaimIds:
            evidence_errors.append("selected GAP must reference paper or claim evidence")

    paper_ids = {paper.paperId for paper in package.literatureSurvey.papers}
    structured_ids = {
        paper.structuredPaperId
        for paper in package.literatureSurvey.papers
        if paper.structuredPaperId
    }
    structured_trace_ids = set(package.evidenceTrace.structuredPaperIds)
    missing_structured = structured_trace_ids - structured_ids - paper_ids
    if missing_structured:
        evidence_errors.append(
            "literatureSurvey.papers[] is missing structured papers: "
            + ", ".join(sorted(missing_structured))
        )

    probe_ids = set(package.evidenceTrace.probePaperIds)
    selected_ids = set(package.evidenceTrace.selectedPaperIds)
    mixed_probe_ids = probe_ids & selected_ids
    if mixed_probe_ids:
        evidence_errors.append(
            "probePaperIds must not be mixed into selectedPaperIds: "
            + ", ".join(sorted(mixed_probe_ids))
        )

    probe_papers = {
        paper.paperId
        for paper in package.literatureSurvey.papers
        if paper.source == "probe"
    }
    missing_probe = probe_ids - probe_papers
    if missing_probe:
        evidence_errors.append(
            "literatureSurvey.papers[] is missing probe papers: "
            + ", ".join(sorted(missing_probe))
        )

    if package.source.ideaCandidateId != package.evidenceTrace.ideaCandidateId:
        evidence_errors.append("source.ideaCandidateId must match evidenceTrace.ideaCandidateId")
    if not _has_text(package.source.searchTreeId):
        evidence_errors.append("source.searchTreeId is required for new v5 idea sessions")
    if not _has_text(package.source.searchNodeId):
        evidence_errors.append("source.searchNodeId is required for complete evidence trace")
    if not _has_text(package.source.pathSeedId):
        evidence_errors.append("source.pathSeedId is required for complete evidence trace")
    if not _has_text(package.evidenceTrace.searchNodeId):
        evidence_errors.append("evidenceTrace.searchNodeId is required")
    if not _has_text(package.evidenceTrace.pathSeedId):
        evidence_errors.append("evidenceTrace.pathSeedId is required")
    if package.source.searchNodeId and package.evidenceTrace.searchNodeId:
        if package.source.searchNodeId != package.evidenceTrace.searchNodeId:
            evidence_errors.append("source.searchNodeId must match evidenceTrace.searchNodeId")
    if package.source.pathSeedId and package.evidenceTrace.pathSeedId:
        if package.source.pathSeedId != package.evidenceTrace.pathSeedId:
            evidence_errors.append("source.pathSeedId must match evidenceTrace.pathSeedId")

    if not package.literatureSurvey.papers:
        evidence_errors.append("literatureSurvey.papers[] must contain investigated paper summaries")
    else:
        for paper in package.literatureSurvey.papers:
            if not _has_text(paper.summary):
                evidence_errors.append(f"literatureSurvey.papers[{paper.paperId}].summary is required")
            if paper.relevanceReason and paper.relevanceScore < 0.25:
                warnings.append(
                    f"literatureSurvey.papers[{paper.paperId}] has low topic relevance: {paper.relevanceReason}"
                )

    topic_terms = _topic_terms(package)
    if len(topic_terms) >= 4:
        paper_hit_counts = [
            (paper.paperId, _hit_count(_semantic_text_for_paper(paper), topic_terms))
            for paper in package.literatureSurvey.papers
        ]
        relevant_papers = [paper_id for paper_id, hits in paper_hit_counts if hits >= 2]
        strong_papers = [paper_id for paper_id, hits in paper_hit_counts if hits >= 3]
        if package.literatureSurvey.papers and not relevant_papers:
            evidence_errors.append(
                "literatureSurvey.papers[] has no papers semantically aligned with the PlanPackage topic anchors"
            )
        elif package.literatureSurvey.papers and not strong_papers:
            warnings.append(
                "literatureSurvey.papers[] has weak topic alignment; verify the upstream search corpus"
            )

        if selected_gap and selected_gap.supportedByPaperIds:
            paper_by_id = {paper.paperId: paper for paper in package.literatureSurvey.papers}
            support_scores = [
                paper_by_id[paper_id].relevanceScore
                for paper_id in selected_gap.supportedByPaperIds
                if paper_id in paper_by_id
                and (
                    paper_by_id[paper_id].relevanceReason
                    or paper_by_id[paper_id].relevanceSignals
                    or paper_by_id[paper_id].relevanceScore > 0
                )
            ]
            if support_scores and max(support_scores) < 0.35:
                evidence_errors.append(
                    "selected GAP is supported only by low-relevance literatureSurvey papers"
                )
            elif support_scores and max(support_scores) < 0.55:
                warnings.append(
                    "selected GAP literature support is weakly relevant; ask for human confirmation"
                )

        plan_chunks: list[str] = []
        for stage in package.stages:
            plan_chunks.extend([stage.title, stage.goal, stage.method])
            for step in stage.steps:
                plan_chunks.extend([
                    step.title,
                    step.desc,
                    step.method,
                    " ".join(output.name + " " + output.desc for output in step.outputs),
                    " ".join(expected.metric + " " + expected.target + " " + expected.desc for expected in step.expected),
                ])
        plan_text = " ".join(plan_chunks)
        plan_hits = _hit_count(plan_text, topic_terms)
        min_plan_hits = max(2, min(4, len(topic_terms) // 4))
        if package.stages and plan_hits < min_plan_hits:
            evidence_errors.append(
                "stages/steps appear semantically detached from the research question and selected idea"
            )
        for stage in package.stages:
            stage_text = " ".join([
                stage.title,
                stage.goal,
                stage.method,
                " ".join(step.title + " " + step.desc + " " + step.method for step in stage.steps),
            ])
            if _hit_count(stage_text, topic_terms) == 0:
                warnings.append(f"{stage.id} has no visible overlap with the PlanPackage topic anchors")

    if not package.evidenceTrace.reasoningTrace:
        warnings.append("evidenceTrace.reasoningTrace is empty")
    if package.gap.items and not package.gap.items[0].supportedByPaperIds and not package.gap.items[0].linkedGraphSignalIds:
        warnings.append("primary gap has no paper or graph signal support")

    if not package.sourceFields.background:
        warnings.append("sourceFields.background is empty")
    if not package.sourceFields.gap:
        warnings.append("sourceFields.gap is empty")
    if not package.sourceFields.principle:
        warnings.append("sourceFields.principle is empty")
    if not package.rawIdeaOutputs:
        warnings.append("rawIdeaOutputs is empty")

    if package.generation.mode == "hybrid":
        illegal_sections = [
            section
            for section in package.generation.llmUsedSections
            if section not in {"implementationPlan", "feedbackNarrative"}
        ]
        if illegal_sections:
            evidence_errors.append(
                "LLM generation may only write implementationPlan or feedbackNarrative fields, got: "
                + ", ".join(illegal_sections)
            )
        if not any(ref_type == "gap" and ref_id == package.gap.selectedGapId for _, ref_type, ref_id in all_step_evidence_refs):
            warnings.append("hybrid implementation plan does not explicitly reference the selected idea GAP")
        if not any(ref_type in {"candidate", "principle"} for _, ref_type, _ in all_step_evidence_refs):
            warnings.append("hybrid implementation plan does not explicitly reference the idea/principle")

    raw_candidate = package.rawIdeaOutputs.get("ideaCandidate") if isinstance(package.rawIdeaOutputs, dict) else None
    if isinstance(raw_candidate, dict):
        raw_method = str(raw_candidate.get("proposedMethod") or "").strip()
        if raw_method and package.principle.mechanism and raw_method not in package.principle.mechanism:
            warnings.append("principle.mechanism differs from IdeaCandidate.proposedMethod adapter source")

    schema_valid = not schema_errors
    evidence_valid = not evidence_errors
    return PlanQualityGate(
        schemaValid=schema_valid,
        evidenceValid=evidence_valid,
        implementationReady=schema_valid and evidence_valid,
        warnings=warnings,
        errors=schema_errors + evidence_errors,
    )
