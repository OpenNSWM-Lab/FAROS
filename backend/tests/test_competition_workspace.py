import json
from pathlib import Path

from app.modules.review.competition_workspace import build_competition_workspace_dashboard
from app.modules.review.human_signoff import decide_human_signoff, initialize_human_signoffs


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _workspace(root: Path) -> None:
    _write(root / "competition_workspace_manifest.json", {
        "generatedAt": "2026-08-30T00:00:00Z",
        "researchDesign": {
            "ideaSessionId": "idea-1",
            "ideaCandidateId": "candidate-1",
            "planPackageId": "plan-1",
        },
        "verifiedClosedLoop": {
            "jobId": "job-1",
            "feedbackId": "feedback-1",
        },
    })
    _write(root / "ideas/candidates/candidate-1.json", {
        "id": "candidate-1",
        "title": "Evidence-grounded review",
        "hypothesisStatement": "The intervention improves grounded review quality.",
        "scoringMethod": "llm+idea_review_gate",
        "graphEvidence": {"supportingPaperIds": ["p1", "p2", "p3", "p4"]},
    })
    _write(root / "plan_packages/plan-1.json", {
        "packageId": "plan-1",
        "status": "approved",
        "qualityGate": {"overallScore": 0.96},
    })
    _write(root / "codegen_sessions/cgs_1.json", {
        "id": "cgs_1",
        "projectId": "project-1",
        "planLinkId": "plan-1",
        "status": "completed",
        "providerName": "qwen",
        "model": "qwen3.7-plus",
        "completedAt": "2026-08-30T01:00:00Z",
        "memory": {
            "generatedFileCount": 24,
            "verificationSummary": {"qualityScore": 100, "errorCount": 0},
            "executionStatus": "passed",
            "executionTestStatus": "passed",
            "executionCommand": "python scripts/smoke_test.py && python -m pytest -q",
        },
    })
    experiment_root = root / "experiments/exp_1"
    _write(experiment_root / "experiment.json", {
        "id": "exp_1",
        "projectId": "project-1",
        "planLinkId": "plan-1",
        "status": "completed",
        "updatedAt": "2026-08-30T02:00:00Z",
    })
    _write(experiment_root / "execution_evidence.json", {
        "status": "verified",
        "predictionRows": 300,
        "ingestedMetrics": 4,
        "bundleSha256": "abc123",
        "checks": {
            "executedStatus": True,
            "projectBoundary": True,
            "inputHashesPresent": True,
        },
    })
    _write(experiment_root / "metrics.json", [
        {"key": "holdout_baseline.f1", "value": 0.7},
        {"key": "holdout_method.f1", "value": 0.75},
        {"key": "holdout_f1_delta", "value": 0.05},
    ])
    _write(root / "papers/paper_1/meta.json", {
        "id": "paper_1",
        "projectId": "project-1",
        "experimentIds": ["exp_1"],
        "runIds": ["run-1"],
        "authors": [],
        "evidenceStatus": "collected",
        "selectedFigures": [{"figureId": "figure-1"}],
        "updatedAt": "2026-08-30T03:00:00Z",
    })
    _write(root / "competition_cases/reviewx_scifact/jobs/job-1.json", {
        "jobId": "job-1",
        "runId": "run-1",
        "model": "qwen3.7-plus",
    })
    _write(root / "competition_cases/reviewx_scifact/runs/job-1/summary.json", {
        "qualityGate": {"status": "passed"},
    })
    feedback = {
        "id": "feedback-1",
        "runId": "run-1",
        "createdAt": "2026-08-30T04:00:00Z",
        "qualityAssessment": {"gateStatus": "pass", "findings": []},
        "iterationDecision": {"decision": "accept_results"},
        "publicationEligible": True,
        "reviewPurpose": "scientific_review",
    }
    feedback["humanSignoffs"] = initialize_human_signoffs(feedback)
    for stage in ("plan", "conclusion"):
        feedback["humanSignoffs"] = decide_human_signoff(
            feedback,
            stage=stage,
            status="approved",
            reviewer_role="team_lead",
            reviewer_id="demo-reviewer",
            reviewer_name="Demo Reviewer",
            actor_account_id="faros-signer-demo",
            actor_role="reviewer",
            auth_assurance="trusted_proxy_basic_auth",
            rationale=f"Reviewed and approved the {stage} evidence.",
        )
    _write(root / "reviewx_experiment_feedback/feedback-1.json", feedback)


def test_workspace_dashboard_validates_complete_single_reviewer_chain(tmp_path: Path):
    _workspace(tmp_path)

    result = build_competition_workspace_dashboard(tmp_path)

    assert result["status"] == {
        "ready": True,
        "passedStages": 6,
        "totalStages": 6,
        "blockers": [],
        "integrity": "verified",
    }
    assert result["governance"] == {
        "reviewerPolicy": "single_accountable_reviewer",
        "responsibleReviewerCount": 1,
        "signoffMode": "trusted_proxy_basic_auth",
        "publicationReady": True,
        "auditIntegrityValid": True,
    }
    assert result["stages"][2]["facts"]["offlineSmoke"] == "passed"
    assert result["stages"][3]["facts"]["predictionRows"] == 300
    assert result["stages"][4]["facts"]["anonymous"] is True
    serialized = json.dumps(result).lower()
    assert "apikey" not in serialized
    assert '"secret":' not in serialized


def test_workspace_dashboard_blocks_failed_dynamic_test(tmp_path: Path):
    _workspace(tmp_path)
    session_path = tmp_path / "codegen_sessions/cgs_1.json"
    session = json.loads(session_path.read_text(encoding="utf-8"))
    session["memory"]["executionTestStatus"] = "failed"
    _write(session_path, session)

    result = build_competition_workspace_dashboard(tmp_path)

    assert result["status"]["ready"] is False
    assert result["status"]["blockers"] == ["code"]
    assert result["status"]["integrity"] == "incomplete"
