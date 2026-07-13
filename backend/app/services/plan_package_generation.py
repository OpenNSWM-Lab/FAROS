"""Internal segmented generation for the unchanged PlanPackage contract."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from app.models.plan_package import PlanPackage, PlanStage
from app.services.plan_package_llm_schema import (
    validate_llm_plan_core_output,
    validate_llm_plan_stage_output,
)
from app.services.plan_package_plan_quality import build_single_plan_design_brief


JsonCall = Callable[[str, str, int], dict[str, Any]]
SegmentValidator = Callable[
    [Any],
    tuple[dict[str, Any] | None, list[str]],
]

_PROTECTED_CONSTANTS = {
    "ideaSessionId",
    "ideaCandidateId",
    "planStage",
    "seedQuery",
    "domain",
    "paperType",
}


@dataclass
class PlanSegmentGenerationResult:
    research_question: str
    hypothesis: str
    constants: dict[str, Any]
    stages: list[PlanStage]
    core_used: bool = False
    llm_stage_ids: list[str] = field(default_factory=list)
    fallback_stage_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _call_with_one_repair(
    *,
    segment_name: str,
    prompt: str,
    max_tokens: int,
    call_json: JsonCall,
    validator: SegmentValidator,
) -> dict[str, Any]:
    issues: list[str] = []
    last_exception: Exception | None = None
    for attempt in range(2):
        repair = ""
        if attempt:
            repair = (
                "\nRepair these validation issues and return the complete segment JSON: "
                + "; ".join(issues)
            )
        try:
            raw = call_json(segment_name, prompt + repair, max_tokens)
        except Exception as exc:
            last_exception = exc
            issues = [f"{type(exc).__name__}: {exc}"]
            continue
        last_exception = None
        parsed, issues = validator(raw)
        if parsed is not None and not issues:
            return parsed
    if last_exception is not None:
        raise last_exception
    raise ValueError("; ".join(issues) or "segment validation failed")


def _selected_gap(package: PlanPackage) -> dict[str, Any]:
    selected = next(
        (item for item in package.gap.items if item.id == package.gap.selectedGapId),
        None,
    )
    if selected:
        return selected.model_dump()
    return {"id": package.gap.selectedGapId, "statement": package.gap.summary}


def _allowed_evidence(package: PlanPackage) -> dict[str, list[str]]:
    return {
        "candidate": [package.idea.id],
        "gap": [item.id for item in package.gap.items],
        "paper": [paper.paperId for paper in package.literatureSurvey.papers],
        "principle": ["principle"],
    }


def _compact_prompt_value(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if len(text) <= 1800:
            return text
        return text[:1797].rstrip() + "..."
    if isinstance(value, dict):
        return {
            str(key): _compact_prompt_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        if all(
            isinstance(item, (str, int, float, bool)) or item is None
            for item in value
        ):
            return [_compact_prompt_value(item) for item in value]
        return [_compact_prompt_value(item) for item in value[:12]]
    return value


def build_core_prompt(package: PlanPackage) -> str:
    context = {
        "seedQuery": package.constants.get("seedQuery", ""),
        "paperType": package.constants.get("paperType", "generic"),
        "idea": package.idea.model_dump(),
        "selectedGap": _selected_gap(package),
        "principle": package.principle.model_dump(),
        "currentConstants": package.constants,
        "allowedEvidence": _allowed_evidence(package),
        "returnShape": {
            "researchQuestion": "string",
            "hypothesis": "falsifiable string",
            "constants": {},
        },
    }
    return (
        "Strengthen only the researchQuestion, hypothesis, and constants of one "
        "PlanPackage. The question must name the object, method, comparison, "
        "outcome, and boundary. The hypothesis must name a mechanism, expected "
        "direction, metric, and rejection condition. Do not invent datasets, "
        "benchmark values, evidence IDs, or executed results. Return JSON only.\n"
        + json.dumps(
            _compact_prompt_value(context),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


def build_stage_prompt(
    package: PlanPackage,
    fallback_stage: PlanStage,
    result: PlanSegmentGenerationResult,
) -> str:
    blueprint = build_single_plan_design_brief(
        package,
        max_stages=len(package.stages),
        max_steps_per_stage=max(len(fallback_stage.steps), 1),
    )
    context = {
        "researchQuestion": result.research_question,
        "hypothesis": result.hypothesis,
        "constants": result.constants,
        "idea": package.idea.model_dump(),
        "selectedGap": _selected_gap(package),
        "principle": package.principle.model_dump(),
        "stageRole": fallback_stage.model_dump(),
        "planBlueprint": blueprint,
        "allowedEvidence": _allowed_evidence(package),
        "topPapers": [
            {
                "paperId": paper.paperId,
                "title": paper.title,
                "summary": paper.summary,
                "methods": paper.methods[:4],
                "limitations": paper.limitations[:4],
            }
            for paper in sorted(
                package.literatureSurvey.papers,
                key=lambda item: item.relevanceScore,
                reverse=True,
            )[:6]
        ],
        "returnShape": {"stage": fallback_stage.model_dump()},
    }
    return (
        "Generate exactly one stage for the supplied stageRole. Preserve its "
        "scientific purpose. Every step needs a concrete method, planned "
        "artifacts, measurable expected targets, and valid evidence IDs. Use "
        "numeric, baseline-relative, or statistical targets; never use readiness "
        "placeholders. Do not claim executed results and do not invent datasets "
        "or evidence. Return JSON only.\n"
        + json.dumps(
            _compact_prompt_value(context),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


def _materialize_stage(
    parsed_stage: dict[str, Any],
    fallback_stage: PlanStage,
) -> PlanStage:
    stage_data = dict(parsed_stage)
    stage_data["id"] = fallback_stage.id
    stage_data["order"] = fallback_stage.order
    stage_data["dependsOn"] = list(fallback_stage.dependsOn)
    steps: list[dict[str, Any]] = []
    for index, raw_step in enumerate(stage_data.get("steps", []), start=1):
        step_data = dict(raw_step)
        step_data["id"] = f"step-{fallback_stage.order}-{index}"
        step_data["order"] = index
        steps.append(step_data)
    stage_data["steps"] = steps
    return PlanStage.model_validate(stage_data)


def generate_stage_segment(
    *,
    package: PlanPackage,
    fallback_stage: PlanStage,
    core_result: PlanSegmentGenerationResult,
    call_json: JsonCall,
    max_steps_per_stage: int,
) -> PlanStage:
    parsed = _call_with_one_repair(
        segment_name=fallback_stage.id,
        prompt=build_stage_prompt(package, fallback_stage, core_result),
        max_tokens=1600,
        call_json=call_json,
        validator=validate_llm_plan_stage_output,
    )
    stage = _materialize_stage(parsed["stage"], fallback_stage)
    stage.steps = stage.steps[:max_steps_per_stage]
    return stage


def normalize_stage_graph(stages: list[PlanStage]) -> list[PlanStage]:
    step_id_map: dict[str, str] = {}
    normalized = [
        stage.model_copy(deep=True)
        for stage in sorted(stages, key=lambda item: item.order)
    ]
    for stage_index, stage in enumerate(normalized, start=1):
        stage.id = f"stage-{stage_index}"
        stage.order = stage_index
        stage.dependsOn = [] if stage_index == 1 else [f"stage-{stage_index - 1}"]
        for step_index, step in enumerate(stage.steps, start=1):
            old_id = step.id
            new_id = f"step-{stage_index}-{step_index}"
            if old_id:
                step_id_map[old_id] = new_id
            step.id = new_id
            step.order = step_index

    known_ids = {step.id for stage in normalized for step in stage.steps}
    previous_step_id = ""
    for stage in normalized:
        for step in stage.steps:
            mapped = [step_id_map.get(item, item) for item in step.inputFrom]
            step.inputFrom = [
                item for item in mapped if item in known_ids and item != step.id
            ]
            if not step.inputFrom and previous_step_id:
                step.inputFrom = [previous_step_id]
            previous_step_id = step.id
    return normalized


def generate_plan_segments(
    *,
    package: PlanPackage,
    call_json: JsonCall,
    max_steps_per_stage: int,
) -> PlanSegmentGenerationResult:
    result = PlanSegmentGenerationResult(
        research_question=package.researchQuestion,
        hypothesis=package.hypothesis,
        constants=dict(package.constants),
        stages=[stage.model_copy(deep=True) for stage in package.stages],
    )
    try:
        core = _call_with_one_repair(
            segment_name="core",
            prompt=build_core_prompt(package),
            max_tokens=1400,
            call_json=call_json,
            validator=validate_llm_plan_core_output,
        )
        result.research_question = core["researchQuestion"].strip()
        result.hypothesis = core["hypothesis"].strip()
        result.constants.update(
            (key, value)
            for key, value in core.get("constants", {}).items()
            if key not in _PROTECTED_CONSTANTS
        )
        result.core_used = True
    except Exception as exc:
        result.warnings.append(f"segment_fallback:core:{type(exc).__name__}")

    merged_stages: list[PlanStage] = []
    for fallback_stage in package.stages:
        try:
            stage = generate_stage_segment(
                package=package,
                fallback_stage=fallback_stage,
                core_result=result,
                call_json=call_json,
                max_steps_per_stage=max_steps_per_stage,
            )
            merged_stages.append(stage)
            result.llm_stage_ids.append(stage.id)
        except Exception as exc:
            merged_stages.append(fallback_stage.model_copy(deep=True))
            result.fallback_stage_ids.append(fallback_stage.id)
            result.warnings.append(
                f"segment_fallback:{fallback_stage.id}:{type(exc).__name__}"
            )
    result.stages = normalize_stage_graph(merged_stages)
    return result
