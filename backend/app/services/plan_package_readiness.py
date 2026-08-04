"""Downstream readiness simulation for PlanPackage handoff."""

from __future__ import annotations

from typing import Iterable, List

from app.models.plan_package import PlanDownstreamReadiness, PlanPackage, PlanReadinessIssue


def _issue(module: str, section_path: str, message: str, *, severity: str = "blocking") -> PlanReadinessIssue:
    return PlanReadinessIssue(
        module=module,
        sectionPath=section_path,
        message=message,
        severity=severity,
    )


def _text(package: PlanPackage) -> str:
    chunks: List[str] = [
        package.researchQuestion,
        package.hypothesis,
        package.idea.title,
        package.idea.proposedMethod,
        package.background.summary,
        package.gap.summary,
        package.principle.summary,
        package.principle.mechanism,
        package.principle.noveltyClaim,
    ]
    for contribution in package.contributionStatement:
        chunks.extend([contribution.statement, contribution.noveltyBasis])
    for stage in package.stages:
        chunks.extend([stage.title, stage.goal, stage.method])
        for step in stage.steps:
            chunks.extend([step.title, step.desc, step.method])
            chunks.extend(f"{output.type} {output.name} {output.desc}" for output in step.outputs)
            chunks.extend(f"{expected.metric} {expected.target} {expected.desc}" for expected in step.expected)
    return " ".join(str(chunk or "") for chunk in chunks).lower().replace("-", " ")


def _has_any(text: str, terms: Iterable[str]) -> bool:
    return any(term.lower() in text for term in terms)


def _outputs(package: PlanPackage, *types: str) -> list[str]:
    wanted = set(types)
    values: list[str] = []
    for stage in package.stages:
        for step in stage.steps:
            for output in step.outputs:
                output_type = str(output.type.value if hasattr(output.type, "value") else output.type)
                if not wanted or output_type in wanted:
                    values.append(output.name)
    return values


def _expected_items(package: PlanPackage) -> list[tuple[str, str, str]]:
    return [
        (expected.metric, expected.target, expected.desc)
        for stage in package.stages
        for step in stage.steps
        for expected in step.expected
    ]


def _stage_step_count(package: PlanPackage) -> tuple[int, int]:
    return len(package.stages), sum(len(stage.steps) for stage in package.stages)


def evaluate_downstream_readiness(package: PlanPackage) -> PlanDownstreamReadiness:
    """Check whether code/experiment/paper/review modules can consume the package."""

    blocking: list[PlanReadinessIssue] = []
    warnings: list[PlanReadinessIssue] = []
    text = _text(package)
    stage_count, step_count = _stage_step_count(package)
    expected = _expected_items(package)

    code_ready = True
    if not _outputs(package, "code", "checkpoint", "report"):
        code_ready = False
        blocking.append(_issue("code", "stages[].steps[].outputs", "No code/checkpoint/report artifacts are available for code handoff."))
    if not _has_any(text, ["module", "implementation", "artifact", "pipeline", "algorithm", "api", "workflow", "实现", "模块", "流程"]):
        code_ready = False
        blocking.append(_issue("code", "stages", "Implementation modules, workflow, or artifacts are not visible enough for code generation."))
    if not package.constants:
        warnings.append(_issue("code", "constants", "No constants are declared for code/config generation.", severity="warning"))

    experiment_ready = True
    if not expected:
        experiment_ready = False
        blocking.append(_issue("experiment", "stages[].steps[].expected", "No expected metrics are available for validation."))
    if not _has_any(text, ["baseline", "control", "comparison", "compare", "基线", "对比"]):
        experiment_ready = False
        blocking.append(_issue("experiment", "stages", "Missing baseline or control comparison for validation."))
    if not _has_any(text, ["ablation", "sensitivity", "robustness", "failure", "slice", "quality", "消融", "敏感性", "鲁棒", "失败", "质量"]):
        experiment_ready = False
        blocking.append(_issue("experiment", "stages", "Missing ablation, sensitivity, robustness, quality, or failure-analysis plan."))
    concrete_targets = [
        target
        for _, target, _ in expected
        if target and target.strip().lower() not in {"specified before implementation", "primary_metric", "readiness"}
    ]
    if expected and len(concrete_targets) / len(expected) < 0.5:
        warnings.append(_issue("experiment", "stages[].steps[].expected", "Many expected metric targets are generic.", severity="warning"))

    paper_ready = True
    if not package.background.summary:
        paper_ready = False
        blocking.append(_issue("paper", "background.summary", "Missing background summary for paper introduction."))
    if not package.literatureSurvey.papers:
        paper_ready = False
        blocking.append(_issue("paper", "literatureSurvey.papers", "Missing investigated paper summaries for related work."))
    if not package.contributionStatement:
        paper_ready = False
        blocking.append(_issue("paper", "contributionStatement", "Missing contribution statements for paper writing."))
    if not _outputs(package, "table", "chart", "report"):
        paper_ready = False
        blocking.append(_issue("paper", "stages[].steps[].outputs", "Missing paper-facing table/chart/report artifacts."))
    if not package.principle.noveltyClaim:
        warnings.append(_issue("paper", "principle.noveltyClaim", "Novelty claim is thin for paper framing.", severity="warning"))

    review_ready = True
    if not package.evidenceTrace.ideaCandidateId:
        review_ready = False
        blocking.append(_issue("review", "evidenceTrace.ideaCandidateId", "Missing idea candidate trace."))
    if not package.evidenceTrace.searchNodeId or not package.evidenceTrace.pathSeedId:
        review_ready = False
        blocking.append(_issue("review", "evidenceTrace", "Missing search node or path seed trace for review audit."))
    selected_gap = next((item for item in package.gap.items if item.id == package.gap.selectedGapId), None)
    if not selected_gap or (not selected_gap.supportedByPaperIds and not selected_gap.supportedByClaimIds):
        review_ready = False
        blocking.append(_issue("review", "gap.selectedGapId", "Selected GAP lacks paper or claim support for review."))
    if not package.principle.risks:
        warnings.append(_issue("review", "principle.risks", "No risks are available for reviewer checklist.", severity="warning"))

    if stage_count == 0 or step_count == 0:
        blocking.append(_issue("package", "stages", "No stages or steps are available for downstream handoff."))

    overall = code_ready and experiment_ready and paper_ready and review_ready and not blocking
    return PlanDownstreamReadiness(
        codeReady=code_ready and not any(issue.module == "code" for issue in blocking),
        experimentReady=experiment_ready and not any(issue.module == "experiment" for issue in blocking),
        paperReady=paper_ready and not any(issue.module == "paper" for issue in blocking),
        reviewReady=review_ready and not any(issue.module == "review" for issue in blocking),
        overallReady=overall,
        blockingIssues=blocking,
        warnings=warnings,
    )
