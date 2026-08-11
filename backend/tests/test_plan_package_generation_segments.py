from app.services.plan_package_generation import (
    PlanSegmentGenerationResult,
    build_stage_prompt,
    generate_plan_segments,
)


def test_one_stage_failure_preserves_other_llm_segments(plan_package):
    calls = []

    def fake_call(segment_name, _prompt, _max_tokens):
        calls.append(segment_name)
        if segment_name == "core":
            return {
                "researchQuestion": (
                    "Can claim verification improve citation-faithful RAG over vanilla RAG?"
                ),
                "hypothesis": (
                    "Claim verification increases citation faithfulness; "
                    "reject if the mean delta is not positive."
                ),
                "constants": {"baseline": "vanilla RAG"},
            }
        if segment_name == "stage-2":
            raise TimeoutError("stage timeout")
        stage_index = int(segment_name.rsplit("-", 1)[-1]) - 1
        stage = plan_package.stages[stage_index].model_dump(mode="json")
        stage["title"] = f"LLM {stage['title']}"
        return {"stage": stage}

    result = generate_plan_segments(
        package=plan_package,
        call_json=fake_call,
        max_steps_per_stage=3,
    )

    assert result.core_used is True
    assert result.llm_stage_ids == ["stage-1", "stage-3"]
    assert result.fallback_stage_ids == ["stage-2"]
    assert [stage.id for stage in result.stages] == ["stage-1", "stage-2", "stage-3"]
    assert result.stages[0].title.startswith("LLM ")
    assert result.stages[1].title == plan_package.stages[1].title
    assert result.stages[2].title.startswith("LLM ")
    assert calls.count("stage-2") == 2


def test_invalid_segment_is_repaired_without_regenerating_other_segments(plan_package):
    stage_one_calls = 0

    def fake_call(segment_name, _prompt, _max_tokens):
        nonlocal stage_one_calls
        if segment_name == "core":
            return {
                "researchQuestion": plan_package.researchQuestion,
                "hypothesis": plan_package.hypothesis,
                "constants": {},
            }
        stage_index = int(segment_name.rsplit("-", 1)[-1]) - 1
        if segment_name == "stage-1":
            stage_one_calls += 1
            if stage_one_calls == 1:
                return {"stage": {"title": "Incomplete", "steps": ["broken"]}}
        return {
            "stage": plan_package.stages[stage_index].model_dump(mode="json")
        }

    result = generate_plan_segments(
        package=plan_package,
        call_json=fake_call,
        max_steps_per_stage=3,
    )

    assert stage_one_calls == 2
    assert result.fallback_stage_ids == []
    assert result.llm_stage_ids == ["stage-1", "stage-2", "stage-3"]


def test_segment_generation_does_not_overwrite_protected_constants(plan_package):
    def fake_call(segment_name, _prompt, _max_tokens):
        if segment_name == "core":
            return {
                "researchQuestion": plan_package.researchQuestion,
                "hypothesis": plan_package.hypothesis,
                "constants": {
                    "seedQuery": "drifted query",
                    "paperType": "survey",
                    "resourceBudget": "2 GPU hours",
                },
            }
        stage_index = int(segment_name.rsplit("-", 1)[-1]) - 1
        return {
            "stage": plan_package.stages[stage_index].model_dump(mode="json")
        }

    result = generate_plan_segments(
        package=plan_package,
        call_json=fake_call,
        max_steps_per_stage=3,
    )

    assert result.constants["seedQuery"] == plan_package.constants["seedQuery"]
    assert result.constants["paperType"] == plan_package.constants["paperType"]
    assert result.constants["resourceBudget"] == "2 GPU hours"


def test_stage_prompt_compacts_long_context_without_dropping_evidence_ids(plan_package):
    paper = plan_package.literatureSurvey.papers[0]
    paper.summary = "long evidence summary " * 4000
    paper.methods = [
        {"name": "vanilla RAG", "description": "long method detail " * 4000}
    ]
    core_result = PlanSegmentGenerationResult(
        research_question=plan_package.researchQuestion,
        hypothesis=plan_package.hypothesis,
        constants=dict(plan_package.constants),
        stages=plan_package.stages,
    )

    prompt = build_stage_prompt(
        plan_package,
        plan_package.stages[0],
        core_result,
    )

    assert len(prompt) < 30000
    assert paper.paperId in prompt
