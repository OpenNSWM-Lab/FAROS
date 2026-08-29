from types import SimpleNamespace
import json

import pytest

from app.models.plan_package import (
    PlanBackground,
    PlanEvidenceRef,
    PlanEvidenceTrace,
    PlanExpectedMetric,
    PlanGap,
    PlanGapItem,
    PlanIdeaSummary,
    PlanLiteraturePaperSummary,
    PlanLiteratureSurvey,
    PlanOutput,
    PlanPackage,
    PlanPrinciple,
    PlanSource,
    PlanStage,
    PlanStep,
)
from app.services.plan_package_service import (
    PlanPackageService,
    _plan_llm_timeout_seconds,
    _plan_reviewer_concurrency,
)


def test_plan_llm_timeout_seconds_is_bounded(monkeypatch):
    monkeypatch.setenv("FAROS_PLAN_PACKAGE_LLM_TIMEOUT_SECONDS", "0")
    assert _plan_llm_timeout_seconds() == 5.0

    monkeypatch.setenv("FAROS_PLAN_PACKAGE_LLM_TIMEOUT_SECONDS", "9999")
    assert _plan_llm_timeout_seconds() == 600.0

    monkeypatch.setenv("FAROS_PLAN_PACKAGE_LLM_TIMEOUT_SECONDS", "45")
    assert _plan_llm_timeout_seconds() == 45.0


def test_plan_reviewer_concurrency_is_bounded(monkeypatch):
    monkeypatch.setenv("FAROS_PLAN_PACKAGE_REVIEWER_CONCURRENCY", "0")
    assert _plan_reviewer_concurrency() == 1

    monkeypatch.setenv("FAROS_PLAN_PACKAGE_REVIEWER_CONCURRENCY", "999")
    assert _plan_reviewer_concurrency() == 5

    monkeypatch.setenv("FAROS_PLAN_PACKAGE_REVIEWER_CONCURRENCY", "3")
    assert _plan_reviewer_concurrency() == 3


def _large_plan_package() -> PlanPackage:
    papers = [
        PlanLiteraturePaperSummary(
            paperId=f"raw_{index:02d}",
            source="structured",
            title=f"Evidence-grounded multi-agent research paper {index}",
            summary=("reliable planning self-review evidence " * 120) + f"summary-tail-{index}",
            relevanceScore=0.9,
            relevanceSignals=["multi-agent", "self-review"],
            relevanceReason="Directly studies evidence-grounded research automation.",
            methods=[{"name": "iterative verification", "description": "method detail " * 80}],
            findings=[{"text": "self-review improves recovery " * 80}],
            limitations=["long-horizon reliability remains unresolved " * 80],
        )
        for index in range(20)
    ]
    return PlanPackage(
        packageId="ppkg_prompt_test",
        source=PlanSource(ideaSessionId="idea_prompt", ideaCandidateId="cand_prompt"),
        idea=PlanIdeaSummary(
            id="cand_prompt",
            title="Evidence-grounded multi-agent self-review",
            problem="Research agents need reliable long-horizon planning.",
            hypothesisStatement="Self-review reduces unrecovered planning errors.",
            proposedMethod=("Use evidence checks and dynamic task allocation. " * 100),
            expectedOutcome="Higher recovery rate with bounded review cost.",
        ),
        background=PlanBackground(
            summary=("Multi-agent research automation needs reliable evidence-grounded planning. " * 80),
            currentLimitations=["Long-horizon reliability remains unresolved. " * 80 for _ in range(8)],
            evidenceRefs=[PlanEvidenceRef(type="paper", id="raw_00")],
        ),
        literatureSurvey=PlanLiteratureSurvey(summary="Relevant evidence", papers=papers),
        gap=PlanGap(
            summary="Long-horizon reliability is under-evaluated.",
            selectedGapId="gap-1",
            items=[
                PlanGapItem(
                    id="gap-1",
                    kind="selected",
                    statement=("Long-horizon recovery remains under-evaluated. " * 80),
                    unresolvedIssue=("Recovery evidence is incomplete. " * 80),
                    supportedByPaperIds=["raw_00"],
                )
            ],
        ),
        principle=PlanPrinciple(
            summary=("Evidence-grounded self-review. " * 80),
            mechanism=("Check evidence before dynamic task reassignment. " * 80),
            assumptions=["Evidence checks remain calibrated. " * 80 for _ in range(8)],
        ),
        researchQuestion="Can evidence-grounded self-review improve research-agent reliability?",
        hypothesis="Evidence checks reduce unrecovered planning errors.",
        constants={"seedQuery": "reliable multi-agent research automation with evidence-grounded planning and self-review", "paperType": "system"},
        stages=[
            PlanStage(
                id="stage-1",
                order=1,
                title="Reliability evaluation",
                goal="Measure recovery and cost.",
                method="Compare self-review with no-review baselines.",
                steps=[
                    PlanStep(
                        id="step-1-1",
                        order=1,
                        title="Define evaluation",
                        desc="Specify the benchmark and failure cases.",
                        method="Map failures to evidence checks.",
                        outputs=[PlanOutput(type="metrics", name="metrics.json")],
                        expected=[PlanExpectedMetric(metric="recovery rate", target="specified before implementation")],
                    )
                ],
            )
        ],
        evidenceTrace=PlanEvidenceTrace(
            ideaCandidateId="cand_prompt",
            selectedPaperIds=[paper.paperId for paper in papers],
            structuredPaperIds=[paper.paperId for paper in papers],
        ),
    )


def test_plan_prompt_compacts_long_paper_context_without_dropping_evidence_ids():
    service = PlanPackageService()
    prompt = service._build_llm_prompt(
        _large_plan_package(),
        max_stages=3,
        max_steps_per_stage=3,
    )

    assert len(prompt) < 35000
    assert all(f"raw_{index:02d}" in prompt for index in range(20))
    assert "summary-tail-0" not in prompt
    context = json.loads(prompt.split("Context JSON:\n", 1)[1])
    assert set(context["planBlueprint"]) >= {
        "requiredRoles",
        "artifactRequirements",
        "evidenceConstraints",
        "downstreamReadinessChecks",
        "maxStages",
        "maxStepsPerStage",
    }


def test_plan_schema_repair_regenerates_without_truncated_assistant_json(monkeypatch):
    service = PlanPackageService()
    package = _large_plan_package()
    calls = []

    class FakeClient:
        def chat(self, *, messages, **kwargs):
            calls.append((messages, kwargs))
            return SimpleNamespace(text='{"stages": ["broken scalar step"]}')

    monkeypatch.setattr("app.services.plan_package_service.get_provider_client", lambda _name: FakeClient())
    monkeypatch.setattr(
        "app.services.plan_package_service.get_llm_task_scheduler",
        lambda: SimpleNamespace(run=lambda _task, fn, **_kwargs: fn()),
    )
    session = SimpleNamespace(config=SimpleNamespace(providerName="fake", model="fake-model"))

    with pytest.raises(ValueError):
        service._apply_llm_plan_fields(
            package,
            session,
            max_stages=3,
            max_steps_per_stage=3,
            max_repair_rounds=1,
        )

    assert len(calls) == 2
    assert [message.role for message in calls[1][0]] == ["system", "user", "user"]
    assert all(call_kwargs["max_tokens"] <= 4096 for _, call_kwargs in calls)


def test_segmented_service_keeps_successful_sections_when_one_stage_times_out(
    monkeypatch,
    plan_package,
):
    stage_two_calls = 0

    class FakeClient:
        def chat(self, *, messages, **_kwargs):
            nonlocal stage_two_calls
            prompt = messages[-1].content
            if prompt.startswith("Strengthen only"):
                return SimpleNamespace(
                    text=json.dumps(
                        {
                            "researchQuestion": plan_package.researchQuestion,
                            "hypothesis": plan_package.hypothesis,
                            "constants": {"resourceBudget": "2 GPU hours"},
                        }
                    )
                )
            context_json = prompt.split("\n", 1)[1].split("\nRepair", 1)[0]
            context = json.loads(context_json)
            stage = context["stageRole"]
            if stage["id"] == "stage-2":
                stage_two_calls += 1
                raise TimeoutError("stage timeout")
            return SimpleNamespace(text=json.dumps({"stage": stage}))

    monkeypatch.setattr(
        "app.services.plan_package_service.get_provider_client",
        lambda _name: FakeClient(),
    )
    monkeypatch.setattr(
        "app.services.plan_package_service.get_llm_task_scheduler",
        lambda: SimpleNamespace(run=lambda _task, fn, **_kwargs: fn()),
    )
    session = SimpleNamespace(
        config=SimpleNamespace(providerName="fake", model="fake-model")
    )
    service = PlanPackageService()

    service._apply_segmented_llm_plan_fields(
        plan_package,
        session,
        max_steps_per_stage=3,
    )

    assert plan_package.constants["resourceBudget"] == "2 GPU hours"
    assert plan_package.generation.llmUsedSections == ["implementationPlan"]
    assert plan_package.generation.fallbackUsed is True
    assert "segment_fallback:stage-2:TimeoutError" in plan_package.generation.warnings
    assert stage_two_calls == 2
