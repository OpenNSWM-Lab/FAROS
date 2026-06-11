"""Validation for PlanPackage contracts."""

from app.models.plan_package import PlanOutputType, PlanPackage, PlanQualityGate


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


def validate_plan_package(package: PlanPackage) -> PlanQualityGate:
    """Validate hard implementation fields and evidence coverage."""
    schema_errors: list[str] = []
    evidence_errors: list[str] = []
    warnings: list[str] = []

    if not _has_text(package.researchQuestion):
        schema_errors.append("researchQuestion is required")

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

    gap_ids = {item.id for item in package.gap.items}
    if package.gap.selectedGapId not in gap_ids:
        schema_errors.append("gap.selectedGapId must reference gap.items[].id")

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
            if section != "implementationPlan"
        ]
        if illegal_sections:
            evidence_errors.append(
                "LLM generation may only write implementationPlan fields, got: "
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
