import importlib.util
from pathlib import Path
from types import SimpleNamespace


def _load_eval_module():
    path = Path(__file__).parents[1] / "scripts" / "run_idea_plan_eval.py"
    spec = importlib.util.spec_from_file_location("run_idea_plan_eval", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_positive_seed_freeze_checks_require_completion_and_two_ideas():
    module = _load_eval_module()
    session = SimpleNamespace(
        status=SimpleNamespace(value="completed"),
        finalCandidateIds=["cand_1", "cand_2"],
    )

    checks = module.freeze_checks(
        session=session,
        spec={"negativeStress": False},
        literature_outputs={
            "paperQualityGate": {"roleCoverage": {"enabled": True}},
        },
        novelty_outputs={"deepReadRequestedCount": 12},
        evidence_gate={"roleCoverage": {"enabled": True}},
        deep_read_limit=24,
    )

    assert checks == {
        "positiveSeedCompleted": True,
        "completionHasTwoIdeas": True,
        "waitingStateIsRecoverable": True,
        "rawRoleCoverageEnabled": True,
        "structuredRoleCoverageEnabled": True,
        "deepReadBounded": True,
    }


def test_negative_stress_seed_may_wait_for_evidence_before_structured_gate():
    module = _load_eval_module()
    session = SimpleNamespace(
        status=SimpleNamespace(value="awaiting_evidence"),
        finalCandidateIds=[],
    )

    checks = module.freeze_checks(
        session=session,
        spec={"negativeStress": True},
        literature_outputs={
            "paperQualityGate": {"roleCoverage": {"enabled": True}},
        },
        novelty_outputs={},
        evidence_gate={},
        deep_read_limit=24,
    )

    assert all(checks.values())


def test_closure_report_continues_when_any_hard_check_fails():
    module = _load_eval_module()
    summary = {
        "seeds": [
            {
                "label": "A",
                "sessionId": "idea_a",
                "status": "completed",
                "finalCandidateIds": ["cand_1"],
                "performance": {},
                "retrievalQuality": {},
                "freezeChecks": {
                    "positiveSeedCompleted": True,
                    "completionHasTwoIdeas": False,
                },
            }
        ]
    }

    report = module.build_closure_report(summary)

    assert report["decision"] == "continue_closure"
    assert report["remainingRisks"] == ["A:completionHasTwoIdeas"]
