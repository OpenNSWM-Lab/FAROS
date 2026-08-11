from types import SimpleNamespace

import scripts.run_idea_plan_eval as eval_script


def test_quality_loop_summary_reads_rank_output_and_session_fallback():
    rank_summary = {
        "targetFinalCandidateCount": 2,
        "finalCandidateCount": 1,
        "qualityStatus": "insufficient_final_candidates",
        "requiresRegeneration": True,
    }
    session = SimpleNamespace(qualityLoopSummary={"qualityStatus": "session_fallback"})

    assert eval_script.quality_loop_summary(
        {"qualityLoopSummary": rank_summary},
        session,
    ) == rank_summary
    assert eval_script.quality_loop_summary({}, session) == session.qualityLoopSummary


def test_reviewer_usage_counts_only_real_llm_reports():
    rank_output = {
        "ideaReviewGate": [
            {
                "reviewerReports": [
                    {"reviewer": "Evidence", "mode": "llm+rule", "llmLatencyMs": None, "cacheHit": True},
                    {"reviewer": "Topic", "mode": "rule", "llmLatencyMs": 0},
                ]
            },
            {
                "reviewerReports": [
                    {"reviewer": "Novelty", "mode": "llm+rule", "llmLatencyMs": None},
                ]
            },
        ]
    }

    assert eval_script.reviewer_usage(rank_output) == {
        "reportCount": 3,
        "llmReportCount": 1,
        "cachedLlmReportCount": 1,
        "llmLatencyMs": 0,
        "llmUsed": True,
    }


def test_null_paths_reports_nested_plan_owned_nulls():
    value = {
        "researchQuestion": "Question",
        "hypothesis": None,
        "stages": [{"steps": [{"outputs": None, "expected": []}]}],
    }

    assert eval_script.null_paths(value) == [
        "hypothesis",
        "stages[0].steps[0].outputs",
    ]


def test_step_map_uses_recorded_duration_seconds_when_milliseconds_are_absent():
    session = SimpleNamespace(
        trace=SimpleNamespace(
            steps=[
                SimpleNamespace(
                    name="noveltyCheck",
                    status="ok",
                    durationSeconds=12.5,
                    outputs={},
                    error=None,
                )
            ]
        )
    )

    mapped = eval_script.step_map(session)

    assert mapped["noveltyCheck"]["durationMs"] == 12500
    assert mapped["noveltyCheck"]["durationSeconds"] == 12.5


def test_plan_quality_summary_reports_segment_and_content_quality(plan_package):
    plan_package.generation.fallbackUsed = True
    plan_package.generation.llmUsedSections = ["implementationPlan"]
    plan_package.generation.llmReviewerUsed = True
    plan_package.generation.warnings = [
        "segment_fallback:stage-2:TimeoutError"
    ]
    plan_package.stages[2].steps[0].expected[0].target = (
        "specified before implementation"
    )

    summary = eval_script.plan_quality_summary(plan_package, 12.3456)

    assert summary["elapsedSeconds"] == 12.346
    assert summary["segmentFallbacks"] == [
        "segment_fallback:stage-2:TimeoutError"
    ]
    assert summary["placeholderValues"] == ["specified before implementation"]
    assert summary["expectedMetricCount"] == 4
    assert summary["llmReviewerUsed"] is True
    assert summary["criticalNullPaths"] == []
