"""Single-plan quality helpers for PlanPackage generation.

The plan stage should produce one coherent PlanPackage. Quality is improved by
pinning required planning roles and repairing the same package, not by
generating multiple plan candidates.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from pydantic import BaseModel, Field

from app.models.plan_package import PlanPackage
from app.services.plan_package_templates import PlanTemplate, get_plan_template


class PlanBlueprint(BaseModel):
    """Internal single-plan blueprint used to guide PlanPackage generation."""

    version: str = "plan-blueprint/v1"
    packageId: str = ""
    templateId: str = ""
    paperType: str = "generic"
    topicAnchors: List[str] = Field(default_factory=list)
    requiredRoles: List[Dict[str, Any]] = Field(default_factory=list)
    recommendedStageShape: List[Dict[str, str]] = Field(default_factory=list)
    baselineRequirements: List[str] = Field(default_factory=list)
    metricRequirements: List[str] = Field(default_factory=list)
    ablationRequirements: List[str] = Field(default_factory=list)
    artifactRequirements: List[str] = Field(default_factory=list)
    evidenceConstraints: Dict[str, Any] = Field(default_factory=dict)
    downstreamReadinessChecks: List[str] = Field(default_factory=list)
    topRelevantPapers: List[Dict[str, Any]] = Field(default_factory=list)
    maxStages: int = 3
    maxStepsPerStage: int = 3


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _flatten_strings(values: Iterable[Any]) -> List[str]:
    result: List[str] = []
    for value in values:
        text = _normalize_text(value)
        if text and text.lower() not in {"none", "null", "undefined"}:
            result.append(text)
    return result


def plan_text(package: PlanPackage) -> str:
    chunks: List[str] = [
        package.researchQuestion,
        package.hypothesis,
        package.idea.title,
        package.idea.problem,
        package.idea.proposedMethod,
        package.gap.summary,
        package.principle.summary,
        package.principle.mechanism,
        package.principle.noveltyClaim,
    ]
    for contribution in package.contributionStatement:
        chunks.extend([contribution.type, contribution.statement, contribution.noveltyBasis])
    for stage in package.stages:
        chunks.extend([stage.title, stage.goal, stage.method])
        for step in stage.steps:
            chunks.extend([step.title, step.desc, step.method])
            chunks.extend(f"{output.type} {output.name} {output.desc}" for output in step.outputs)
            chunks.extend(f"{expected.metric} {expected.target} {expected.desc}" for expected in step.expected)
    return " ".join(_flatten_strings(chunks)).lower().replace("-", " ")


def _template_for_package(package: PlanPackage) -> PlanTemplate:
    return get_plan_template(str(package.constants.get("paperType", "")))


def missing_plan_roles(package: PlanPackage) -> List[Dict[str, str]]:
    """Return required single-plan roles not visible in the current stages."""

    text = plan_text(package)
    template = _template_for_package(package)
    missing: List[Dict[str, str]] = []
    for role in template.requiredRoles:
        if not any(keyword.lower() in text for keyword in role.keywords):
            missing.append({
                "id": role.id,
                "label": role.label,
                "repairHint": role.repairHint,
            })
    return missing


def _fit_stage_shape(template: PlanTemplate, max_stages: int) -> List[Dict[str, str]]:
    shape = list(template.stageShape)
    if max_stages <= 0:
        return []
    if len(shape) <= max_stages:
        return shape[:max_stages]
    if max_stages == 1:
        return [{
            "title": "Single-package implementation plan",
            "mustCover": "; ".join(item.get("mustCover", item.get("title", "")) for item in shape if item),
        }]
    if max_stages == 2:
        split = max(1, len(shape) // 2)
        first = shape[:split]
        second = shape[split:]
        return [
            {
                "title": "Grounding and specification",
                "mustCover": "; ".join(item.get("mustCover", item.get("title", "")) for item in first),
            },
            {
                "title": "Validation and handoff",
                "mustCover": "; ".join(item.get("mustCover", item.get("title", "")) for item in second),
            },
        ]
    return shape[:max_stages]


def _topic_anchors(package: PlanPackage) -> List[str]:
    text = " ".join([
        str(package.constants.get("seedQuery", "")),
        str(package.constants.get("domain", "")),
        str(package.constants.get("paperType", "")),
        package.researchQuestion,
        package.hypothesis,
        package.idea.title,
        package.idea.problem,
        package.idea.proposedMethod,
        package.gap.summary,
        package.principle.summary,
        package.principle.noveltyClaim,
    ]).lower().replace("-", " ")
    anchors: List[str] = []
    for token in text.replace("/", " ").replace("_", " ").split():
        cleaned = token.strip(".,:;()[]{}\"'")
        if len(cleaned) < 3 or cleaned in {"and", "the", "for", "with", "from", "method", "model", "paper", "research"}:
            continue
        if cleaned not in anchors:
            anchors.append(cleaned)
    return anchors[:24]


def build_plan_blueprint(
    package: PlanPackage,
    *,
    max_stages: int,
    max_steps_per_stage: int,
) -> PlanBlueprint:
    template = _template_for_package(package)
    key_papers = sorted(
        package.literatureSurvey.papers,
        key=lambda paper: paper.relevanceScore,
        reverse=True,
    )[:6]
    selected_gap = next(
        (item for item in package.gap.items if item.id == package.gap.selectedGapId),
        None,
    )
    return PlanBlueprint(
        packageId=package.packageId,
        templateId=template.templateId,
        paperType=template.paperType,
        topicAnchors=_topic_anchors(package),
        requiredRoles=[role.model_dump() for role in template.requiredRoles],
        recommendedStageShape=_fit_stage_shape(template, max_stages),
        baselineRequirements=template.requiredComparisons,
        metricRequirements=template.recommendedMetrics,
        ablationRequirements=template.requiredAblations,
        artifactRequirements=template.recommendedOutputs,
        evidenceConstraints={
            "selectedGapId": package.gap.selectedGapId,
            "selectedGap": selected_gap.model_dump() if selected_gap else {},
            "allowedPaperIds": [paper.paperId for paper in package.literatureSurvey.papers],
            "candidateId": package.idea.id,
        },
        downstreamReadinessChecks=[
            "code: modules, inputs, outputs, constants, and artifacts are visible",
            "experiment/validation: baseline, metric, target, ablation, and dependency order are visible",
            "paper: background, related work, gap, principle, contribution, and table/chart plan are visible",
            "review: evidence trace, citation refs, novelty claim, risks, and limitations are visible",
        ],
        topRelevantPapers=[
            {
                "paperId": paper.paperId,
                "title": paper.title,
                "source": paper.source,
                "relevanceScore": paper.relevanceScore,
            }
            for paper in key_papers
        ],
        maxStages=max_stages,
        maxStepsPerStage=max_steps_per_stage,
    )


def build_single_plan_design_brief(
    package: PlanPackage,
    *,
    max_stages: int,
    max_steps_per_stage: int,
) -> Dict[str, Any]:
    blueprint = build_plan_blueprint(
        package,
        max_stages=max_stages,
        max_steps_per_stage=max_steps_per_stage,
    )
    return {
        "singlePlanOnly": True,
        "doNotGenerate": [
            "multiple plan candidates",
            "alternative plan options",
            "ranking between plans",
            "exploratory brainstorm lists",
        ],
        **blueprint.model_dump(),
        "qualityBar": [
            "Every stage must have concrete steps with non-empty desc/method/outputs/expected.",
            "At least one step must define baselines or control comparisons.",
            (
                "At least one step must define ablation, sensitivity, robustness, or failure analysis."
                if blueprint.ablationRequirements
                else "For survey-style packages, use taxonomy consistency, comparison dimensions, and GAP synthesis instead of experiment ablations."
            ),
            "Expected metrics must test the hypothesis and selected GAP; avoid generic readiness metrics.",
            "Outputs must be planned artifacts, not executed results.",
            "Use empty arrays/objects or concise placeholder strings instead of null values.",
        ],
    }


def sanitize_constant_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        for key, item in value.items():
            text_key = _normalize_text(key)
            if not text_key:
                continue
            cleaned_item = sanitize_constant_value(item)
            if cleaned_item is not None:
                cleaned[text_key] = cleaned_item
        return cleaned
    if isinstance(value, list):
        cleaned_list = [
            sanitize_constant_value(item)
            for item in value
        ]
        return [item for item in cleaned_list if item is not None]
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() in {"none", "null", "undefined"}:
            return None
        return text
    return value


def sanitize_constants(raw: Dict[str, Any]) -> Dict[str, Any]:
    cleaned: Dict[str, Any] = {}
    for key, value in raw.items():
        text_key = _normalize_text(key)
        if not text_key:
            continue
        cleaned_value = sanitize_constant_value(value)
        if cleaned_value is not None:
            cleaned[text_key] = cleaned_value
    return cleaned
