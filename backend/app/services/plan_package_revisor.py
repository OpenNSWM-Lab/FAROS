"""Plan-owned revision routing for PlanPackage repair loops."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.models.plan_package import PlanPackage


class PlanRevisionPatch(BaseModel):
    changedSections: List[str] = Field(default_factory=list)
    reason: str = ""
    reviewerIssueIds: List[str] = Field(default_factory=list)
    fieldPatches: List[Dict[str, Any]] = Field(default_factory=list)
    unresolvedIssues: List[str] = Field(default_factory=list)
    upstreamBlocked: bool = False


_ALLOWED_SECTIONS = {
    "researchQuestion",
    "hypothesis",
    "constants",
    "stages",
    "expectedMetrics",
    "background",
    "gap",
    "principle",
}


def _contains(text: str, terms: list[str]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def _add(target: list[str], *sections: str) -> None:
    for section in sections:
        if section in _ALLOWED_SECTIONS and section not in target:
            target.append(section)


def _section_targets(section_path: str, *, allow_narrative: bool) -> list[str]:
    path = (section_path or "").lower()
    targets: list[str] = []
    if "researchquestion" in path or "research_question" in path:
        _add(targets, "researchQuestion")
    if "hypothesis" in path:
        _add(targets, "hypothesis")
    if "constant" in path:
        _add(targets, "constants")
    if "stage" in path or "step" in path or "output" in path or "expected" in path:
        _add(targets, "stages")
        if "expected" in path or "metric" in path:
            _add(targets, "expectedMetrics")
    if allow_narrative:
        if "background" in path:
            _add(targets, "background")
        if "gap" in path:
            _add(targets, "gap")
        if "principle" in path or "method" in path or "novelty" in path:
            _add(targets, "principle")
    return targets


def _message_targets(message: str, *, allow_narrative: bool) -> list[str]:
    targets: list[str] = []
    text = message.lower()
    if _contains(text, ["research question", "researchquestion", "problem", "scope", "boundary", "主题", "问题", "边界"]):
        _add(targets, "researchQuestion")
    if _contains(text, ["hypothesis", "expected outcome", "assumption", "假设", "预期"]):
        _add(targets, "hypothesis")
    if _contains(text, ["constant", "dataset", "model", "hardware", "baseline", "parameter", "config", "常量", "数据集", "模型", "基线"]):
        _add(targets, "constants")
    if _contains(text, ["stage", "step", "plan", "implementation", "artifact", "module", "workflow", "downstream", "code", "阶段", "步骤", "计划", "实现", "产物"]):
        _add(targets, "stages")
    if _contains(text, ["metric", "target", "evaluation", "validation", "ablation", "sensitivity", "robustness", "experiment", "quality", "指标", "评估", "验证", "消融"]):
        _add(targets, "stages", "expectedMetrics")
    if allow_narrative and _contains(text, ["background", "motivation", "context", "背景", "动机"]):
        _add(targets, "background")
    if allow_narrative and _contains(text, ["gap", "selected gap", "unresolved", "coverage", "缺口", "未解决"]):
        _add(targets, "gap")
    if allow_narrative and _contains(text, ["principle", "mechanism", "novelty", "contribution", "原理", "机制", "创新", "贡献"]):
        _add(targets, "principle")
    return targets


def _is_upstream_issue(message: str) -> bool:
    return _contains(message, [
        "searchnodeid",
        "pathseedid",
        "reasoningtrace",
        "literaturesurvey.papers[] has no papers",
        "missing structured papers",
        "missing probe papers",
        "source.searchnodeid",
        "source.pathseedid",
        "evidencetrace.searchnodeid",
        "evidencetrace.pathseedid",
        "no investigated paper",
        "selected gap lacks paper",
        "selected gap must reference paper",
        "external search",
        "paper pool",
        "论文池",
        "检索",
        "证据池",
    ])


def build_plan_revision_patch(
    package: PlanPackage,
    *,
    target_sections: Optional[List[str]] = None,
    include_feedback: bool = True,
    allow_narrative: bool = False,
) -> PlanRevisionPatch:
    """Route issues to plan-owned fields and identify upstream blockers."""

    changed: list[str] = []
    field_patches: list[dict[str, Any]] = []
    issue_ids: list[str] = []
    unresolved: list[str] = []

    if target_sections is not None:
        for section in target_sections:
            _add(changed, str(section))

    if include_feedback:
        for feedback in package.humanFeedback:
            if feedback.resolved:
                continue
            targets = [section for section in feedback.targetSections if section in _ALLOWED_SECTIONS]
            targets.extend(_section_targets(feedback.sectionPath, allow_narrative=True))
            targets.extend(_message_targets(feedback.comment, allow_narrative=True))
            if not targets:
                targets = ["researchQuestion", "hypothesis", "stages"]
            for section in targets:
                _add(changed, section)
            field_patches.append({
                "source": "humanFeedback",
                "feedbackId": feedback.id,
                "sectionPath": feedback.sectionPath,
                "targets": sorted(set(targets)),
                "message": feedback.comment,
            })

    review_issues = []
    if package.metaReview:
        review_issues.extend(package.metaReview.blockingIssues)
        review_issues.extend(package.metaReview.warnings)
    for issue in review_issues:
        issue_ids.append(issue.id)
        message = f"{issue.sectionPath} {issue.message}".strip()
        if _is_upstream_issue(message):
            unresolved.append(message)
            continue
        targets = _section_targets(issue.sectionPath, allow_narrative=allow_narrative)
        targets.extend(_message_targets(issue.message, allow_narrative=allow_narrative))
        for section in targets:
            _add(changed, section)
        if targets:
            field_patches.append({
                "source": "reviewer",
                "issueId": issue.id,
                "sectionPath": issue.sectionPath,
                "targets": sorted(set(targets)),
                "message": issue.message,
            })

    for message in [*package.qualityGate.errors, *package.qualityGate.warnings]:
        if _is_upstream_issue(message):
            unresolved.append(message)
            continue
        targets = _message_targets(message, allow_narrative=allow_narrative)
        for section in targets:
            _add(changed, section)
        if targets:
            field_patches.append({
                "source": "qualityGate",
                "targets": sorted(set(targets)),
                "message": message,
            })

    if "expectedMetrics" in changed and "stages" not in changed:
        _add(changed, "stages")

    return PlanRevisionPatch(
        changedSections=changed,
        reason=(
            "Human feedback and reviewer/readiness findings were routed to plan-owned fields."
            if changed
            else "No plan-owned repair target was found."
        ),
        reviewerIssueIds=issue_ids,
        fieldPatches=field_patches[:24],
        unresolvedIssues=unresolved[:24],
        upstreamBlocked=bool(unresolved and not changed),
    )
