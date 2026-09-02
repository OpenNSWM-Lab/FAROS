import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.modules.review import experiment_feedback_storage, reviews_api
from app.modules.review.human_signoff import (
    SIGNOFF_ACKNOWLEDGEMENTS,
    decide_human_signoff,
    human_signoff_state,
    initialize_human_signoffs,
    publication_ready,
    require_human_signoff,
)
from app.modules.review.human_feedback import (
    human_feedback_state,
    iteration_decision_with_human_feedback,
)
from app.modules.review.human_feedback_verification import (
    decide_human_condition_verification,
    human_condition_verification_state,
)
from app.modules.review.audit_chain import record_audit_integrity


def _record(*, decision: str = "revise_plan", blockers: int = 0):
    return {
        "id": "exprev_signoff",
        "runId": "run-1",
        "questionId": "question-1",
        "benchmarkFingerprint": "sha256:benchmark",
        "sourceArtifacts": {"experimentEvidence": "artifact-1"},
        "planPackageId": "plan-1",
        "planRevision": {"revisionId": "revision-1"},
        "metricSnapshot": [{"name": "method:F1", "value": 0.8}],
        "qualityAssessment": {
            "gateStatus": "fail" if blockers else "pass",
            "findings": [
                {"id": f"finding-{index}", "severity": "blocker"}
                for index in range(blockers)
            ],
        },
        "iterationDecision": {"decision": decision, "nextActions": ["rerun"]},
        "createdAt": "2026-08-26T00:00:00+00:00",
    }


def _approve(record, stage: str):
    record["humanSignoffs"] = decide_human_signoff(
        record,
        stage=stage,
        status="approved",
        reviewer_role="team_lead",
        reviewer_id="reviewer@example.com",
        rationale=f"Approved {stage} evidence and constraints.",
    )


def test_plan_approval_is_hash_bound_and_becomes_stale_after_revision():
    record = _record()
    _approve(record, "plan")

    assert require_human_signoff(record, "plan")["status"] == "approved"

    record["planRevision"] = {"revisionId": "revision-2"}
    state = human_signoff_state(record)["plan"]
    assert state["status"] == "pending"
    assert state["storedStatus"] == "approved"
    assert state["stale"] is True
    with pytest.raises(ValueError, match="stale"):
        require_human_signoff(record, "plan")


def test_risky_iteration_requires_plan_and_repair_signoffs():
    record = _record(decision="rerun_experiment")
    _approve(record, "plan")

    with pytest.raises(ValueError, match="repair signoff"):
        reviews_api._require_iteration_signoffs(record)

    _approve(record, "repair")
    reviews_api._require_iteration_signoffs(record)


def test_blockers_prevent_conclusion_approval():
    record = _record(decision="accept_results", blockers=1)
    _approve(record, "plan")
    _approve(record, "repair")

    with pytest.raises(ValueError, match="Resolve ReviewX blockers"):
        _approve(record, "conclusion")
    assert publication_ready(record) is False


def test_conclusion_cannot_skip_required_preceding_signoffs():
    record = _record(decision="rerun_experiment")

    with pytest.raises(ValueError, match="preceding human signoffs"):
        _approve(record, "conclusion")

    _approve(record, "plan")
    with pytest.raises(ValueError, match="repair signoff"):
        _approve(record, "conclusion")

    _approve(record, "repair")
    with pytest.raises(ValueError, match="accepted final experiment result"):
        _approve(record, "conclusion")

    final_record = _record(decision="accept_results")
    _approve(final_record, "plan")
    _approve(final_record, "conclusion")
    assert publication_ready(final_record) is True


def test_inherited_human_conditions_require_current_evidence_before_conclusion():
    record = _record(decision="accept_results")
    record["inheritedHumanFeedback"] = {
        "feedbackHash": "sha256:human-feedback",
        "items": [{
            "decisionId": "hsd_parent",
            "stage": "plan",
            "status": "changes_requested",
            "rationale": "Add leakage checks.",
            "conditions": ["Record the leakage check as an artifact"],
            "targetSections": ["evaluation"],
        }],
    }
    _approve(record, "plan")

    state = human_condition_verification_state(record)
    assert state["required"] is True
    assert state["unresolved"] == 1
    with pytest.raises(ValueError, match="acceptance conditions"):
        _approve(record, "conclusion")

    condition_id = state["conditions"][0]["conditionId"]
    with pytest.raises(ValueError, match="at least one source artifact"):
        decide_human_condition_verification(
            record,
            condition_id=condition_id,
            status="passed",
            verifier_role="domain_expert",
            verifier_id="expert@example.com",
            rationale="Leakage report checked.",
        )
    record["humanFeedbackVerifications"] = decide_human_condition_verification(
        record,
        condition_id=condition_id,
        status="passed",
        verifier_role="domain_expert",
        verifier_id="expert@example.com",
        rationale="Leakage report checked against the experiment evidence.",
        evidence_artifact_ids=["artifact-1"],
    )
    assert human_condition_verification_state(record)["allResolved"] is True
    _approve(record, "conclusion")
    assert publication_ready(record) is True

    record["sourceArtifacts"] = {"experimentEvidence": "artifact-2"}
    stale = human_condition_verification_state(record)["conditions"][0]
    assert stale["status"] == "pending"
    assert stale["stale"] is True
    assert publication_ready(record) is False


def test_technical_test_record_can_never_become_publication_ready():
    record = _record(decision="accept_results")
    record["reviewPurpose"] = "technical_test"
    # The purpose itself is a hard gate even if an upstream producer sets the
    # eligibility flag incorrectly.
    record["publicationEligible"] = True
    record["humanSignoffs"] = initialize_human_signoffs(record)

    for stage in ("plan", "conclusion"):
        record["humanSignoffs"] = decide_human_signoff(
            record,
            stage=stage,
            status="approved",
            reviewer_role="technical_tester",
            reviewer_id="codex-technical-test",
            rationale="Technical workflow validation only; this is not scientific approval.",
        )

    assert human_signoff_state(record)["conclusion"]["status"] == "approved"
    assert publication_ready(record) is False


def test_condition_state_tampering_revokes_publication():
    record = _record(decision="accept_results")
    record["inheritedHumanFeedback"] = {
        "feedbackHash": "sha256:human-feedback",
        "items": [{
            "decisionId": "hsd_parent",
            "stage": "plan",
            "status": "changes_requested",
            "conditions": ["Record the leakage check as an artifact"],
        }],
    }
    _approve(record, "plan")
    condition_id = human_condition_verification_state(record)["conditions"][0]["conditionId"]
    record["humanFeedbackVerifications"] = decide_human_condition_verification(
        record,
        condition_id=condition_id,
        status="passed",
        verifier_role="domain_expert",
        verifier_id="expert@example.com",
        rationale="Leakage report checked against the experiment evidence.",
        evidence_artifact_ids=["artifact-1"],
    )
    _approve(record, "conclusion")
    assert publication_ready(record) is True

    record["humanFeedbackVerifications"][condition_id]["rationale"] = "tampered"
    integrity = record_audit_integrity(record)
    assert integrity["valid"] is False
    assert integrity["invalidStreams"] == [f"condition:{condition_id}"]
    assert publication_ready(record) is False


def test_formal_conclusion_requires_a_different_reviewer_from_plan():
    record = _record(decision="accept_results")
    record["enforceReviewerSeparation"] = True
    _approve(record, "plan")

    with pytest.raises(ValueError, match="must differ"):
        _approve(record, "conclusion")

    record["humanSignoffs"] = decide_human_signoff(
        record,
        stage="conclusion",
        status="approved",
        reviewer_role="domain_expert",
        reviewer_id="independent@example.com",
        rationale="Independently checked the final metrics and limitations.",
    )
    assert publication_ready(record) is True


def test_signoff_hash_chain_detects_tampering_and_revokes_publication():
    record = _record(decision="accept_results")
    _approve(record, "plan")
    _approve(record, "conclusion")
    assert record_audit_integrity(record)["valid"] is True
    assert publication_ready(record) is True

    record["humanSignoffs"]["conclusion"]["history"][0]["rationale"] = "tampered"

    integrity = record_audit_integrity(record)
    assert integrity["valid"] is False
    assert integrity["invalidStreams"] == ["signoff:conclusion"]
    assert publication_ready(record) is False


def test_official_bundle_requires_conclusion_signoff(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(experiment_feedback_storage, "_STORAGE_DIR", tmp_path)
    stored = experiment_feedback_storage.create_experiment_feedback(_record(decision="accept_results"))

    draft = asyncio.run(reviews_api.get_experiment_evidence_bundle_endpoint(stored["id"], "draft"))
    assert draft["watermark"] == "DRAFT_NOT_HUMAN_APPROVED"
    with pytest.raises(HTTPException) as blocked:
        asyncio.run(reviews_api.get_experiment_evidence_bundle_endpoint(stored["id"], "official"))
    assert blocked.value.status_code == 409

    _approve(stored, "plan")
    experiment_feedback_storage.update_experiment_feedback(
        stored["id"], {"humanSignoffs": stored["humanSignoffs"]},
    )
    decision = reviews_api.HumanSignoffDecisionRequest(
        status="approved",
        reviewerRole="domain_expert",
        reviewerId="expert@example.com",
        rationale="Evidence and conclusion boundaries were checked.",
        acknowledgements=list(SIGNOFF_ACKNOWLEDGEMENTS["conclusion"]),
    )
    asyncio.run(reviews_api.decide_experiment_signoff_endpoint(
        stored["id"], "conclusion", decision,
    ))
    official = asyncio.run(
        reviews_api.get_experiment_evidence_bundle_endpoint(stored["id"], "official")
    )
    assert official["publicationReady"] is True
    assert official["watermark"] is None


def test_signoff_api_persists_identity_rationale_and_history(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(experiment_feedback_storage, "_STORAGE_DIR", tmp_path)
    stored = experiment_feedback_storage.create_experiment_feedback(_record())
    request = reviews_api.HumanSignoffDecisionRequest(
        status="changes_requested",
        reviewerRole="safety_reviewer",
        reviewerId="safety@example.com",
        rationale="Recheck the split before execution.",
        conditions=["Use a claim-grouped split"],
        acknowledgements=list(SIGNOFF_ACKNOWLEDGEMENTS["plan"]),
    )

    response = asyncio.run(reviews_api.decide_experiment_signoff_endpoint(
        stored["id"], "plan", request,
    ))

    plan = response.humanSignoffs["plan"]
    assert plan["status"] == "changes_requested"
    assert plan["reviewerId"] == "safety@example.com"
    assert plan["conditions"] == ["Use a claim-grouped split"]
    assert len(plan["history"]) == 1


def test_single_accountable_reviewer_can_approve_all_required_stages(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(experiment_feedback_storage, "_STORAGE_DIR", tmp_path)
    record = _record(decision="accept_results")
    record["enforceReviewerSeparation"] = False
    record["reviewerPolicy"] = "single_accountable_reviewer"
    stored = experiment_feedback_storage.create_experiment_feedback(record)
    request = reviews_api.HumanSignoffBatchDecisionRequest(
        reviewerRole="team_lead",
        reviewerId="competition-reviewer",
        rationale="Checked the frozen protocol, recomputed metrics, and accepted the stated limitations.",
        acknowledgementsByStage={
            stage: list(items) for stage, items in SIGNOFF_ACKNOWLEDGEMENTS.items()
        },
    )

    response = asyncio.run(reviews_api.approve_required_experiment_signoffs_endpoint(
        stored["id"], request,
    ))

    assert response.humanSignoffs["plan"]["status"] == "approved"
    assert response.humanSignoffs["conclusion"]["status"] == "approved"
    assert response.humanSignoffs["repair"]["required"] is False
    assert response.humanSignoffs["repair"]["status"] == "pending"
    assert response.humanSignoffs["plan"]["reviewerId"] == "competition-reviewer"
    assert response.humanSignoffs["conclusion"]["reviewerId"] == "competition-reviewer"
    assert response.publicationReady is True

    history = asyncio.run(reviews_api.list_experiment_feedback_endpoint(limit=20))
    assert history["records"][0]["reviewerPolicy"] == "single_accountable_reviewer"
    assert history["records"][0]["publicationReady"] is True


def test_batch_signoff_respects_explicit_reviewer_separation(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(experiment_feedback_storage, "_STORAGE_DIR", tmp_path)
    record = _record(decision="accept_results")
    record["enforceReviewerSeparation"] = True
    stored = experiment_feedback_storage.create_experiment_feedback(record)
    request = reviews_api.HumanSignoffBatchDecisionRequest(
        reviewerRole="team_lead",
        reviewerId="competition-reviewer",
        rationale="Reviewed the complete evidence package.",
    )

    with pytest.raises(HTTPException) as conflict:
        asyncio.run(reviews_api.approve_required_experiment_signoffs_endpoint(
            stored["id"], request,
        ))
    assert conflict.value.status_code == 409


def test_signoff_api_can_require_authenticated_matching_identity(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(experiment_feedback_storage, "_STORAGE_DIR", tmp_path)
    monkeypatch.setenv("FAROS_REVIEWX_REQUIRE_AUTH", "true")
    monkeypatch.setenv("FAROS_REVIEWX_AUTH_TOKENS", json.dumps({
        "plan-secret": {"id": "lead@example.com", "roles": ["team_lead"]},
    }))
    stored = experiment_feedback_storage.create_experiment_feedback(_record())
    request = reviews_api.HumanSignoffDecisionRequest(
        status="approved",
        reviewerRole="team_lead",
        reviewerId="lead@example.com",
        rationale="Authenticated review of the frozen experiment plan.",
        acknowledgements=list(SIGNOFF_ACKNOWLEDGEMENTS["plan"]),
    )

    with pytest.raises(HTTPException) as missing:
        asyncio.run(reviews_api.decide_experiment_signoff_endpoint(
            stored["id"], "plan", request,
        ))
    assert missing.value.status_code == 401

    response = asyncio.run(reviews_api.decide_experiment_signoff_endpoint(
        stored["id"], "plan", request, authorization="Bearer plan-secret",
    ))
    assert response.humanSignoffs["plan"]["status"] == "approved"


def test_human_change_request_must_be_applied_before_reapproval():
    record = _record(decision="rerun_experiment")
    record["humanSignoffs"] = decide_human_signoff(
        record,
        stage="plan",
        status="changes_requested",
        reviewer_role="domain_expert",
        reviewer_id="expert@example.com",
        rationale="Replace the random split with a claim-grouped split.",
        conditions=["Report leakage checks", "Preserve the frozen benchmark hash"],
        target_sections=["stages", "expectedMetrics"],
    )

    state = human_feedback_state(record)
    assert state["requiresApplication"] is True
    assert state["applied"] is False
    assert state["targetSections"] == ["stages", "expectedMetrics"]
    with pytest.raises(ValueError, match="Apply the current human feedback"):
        _approve(record, "plan")

    record["humanFeedbackApplication"] = {
        "feedbackHash": state["feedbackHash"],
        "status": "queued_for_iteration",
    }
    _approve(record, "plan")
    merged = iteration_decision_with_human_feedback(
        record,
        record["iterationDecision"],
    )
    assert merged["humanFeedback"]["feedbackHash"] == state["feedbackHash"]
    assert "Report leakage checks" in merged["nextActions"]
    assert "expectedMetrics" in merged["targetSections"]


def test_far_os_human_loop_reaches_child_iteration(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(experiment_feedback_storage, "_STORAGE_DIR", tmp_path)
    source = _record(decision="rerun_experiment")
    source.update({
        "runId": "faros_parent",
        "runKind": "faros",
        "planPackageId": None,
    })
    stored = experiment_feedback_storage.create_experiment_feedback(source)
    request = reviews_api.HumanSignoffDecisionRequest(
        status="changes_requested",
        reviewerRole="domain_expert",
        reviewerId="expert@example.com",
        rationale="Use a claim-grouped split before rerunning.",
        conditions=["Record the leakage check as an artifact"],
        targetSections=["evaluation"],
        acknowledgements=list(SIGNOFF_ACKNOWLEDGEMENTS["plan"]),
    )
    asyncio.run(reviews_api.decide_experiment_signoff_endpoint(
        stored["id"], "plan", request,
    ))

    application = asyncio.run(reviews_api.apply_experiment_human_feedback_endpoint(
        stored["id"], reviews_api.ApplyHumanFeedbackRequest(),
    ))
    assert application.status == "queued_for_iteration"
    assert application.applied is True
    assert application.humanSignoffs["plan"]["stale"] is True

    for stage in ("plan", "repair"):
        asyncio.run(reviews_api.decide_experiment_signoff_endpoint(
            stored["id"],
            stage,
            reviews_api.HumanSignoffDecisionRequest(
                status="approved",
                reviewerRole="team_lead",
                reviewerId="lead@example.com",
                rationale=f"Verified applied {stage} constraints.",
                acknowledgements=list(SIGNOFF_ACKNOWLEDGEMENTS[stage]),
            ),
        ))

    captured = {}

    class FakeOrchestrator:
        def create_iteration_run(self, parent_run_id, feedback_id, decision):
            captured.update({
                "parentRunId": parent_run_id,
                "feedbackId": feedback_id,
                "decision": decision,
            })
            return {
                "id": "faros_child",
                "status": "created",
                "research_series_id": "faros_parent",
                "iteration_number": 2,
            }, False

    from app.faros.runtime import orchestrator as orchestrator_module

    monkeypatch.setattr(orchestrator_module, "get_orchestrator", lambda: FakeOrchestrator())
    response = asyncio.run(reviews_api.create_next_experiment_run_endpoint(stored["id"]))

    assert response.runId == "faros_child"
    human_feedback = captured["decision"]["humanFeedback"]
    assert human_feedback["targetSections"] == ["evaluation"]
    assert human_feedback["requiredActions"] == [
        "Use a claim-grouped split before rerunning.",
        "Record the leakage check as an artifact",
    ]


def test_platform_human_feedback_is_written_and_revised(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(experiment_feedback_storage, "_STORAGE_DIR", tmp_path)
    stored = experiment_feedback_storage.create_experiment_feedback(_record())
    decision = decide_human_signoff(
        stored,
        stage="plan",
        status="changes_requested",
        reviewer_role="domain_expert",
        reviewer_id="expert@example.com",
        rationale="Tighten the evaluation protocol.",
        conditions=["Add a leakage audit"],
        target_sections=["expectedMetrics"],
    )
    experiment_feedback_storage.update_experiment_feedback(
        stored["id"], {"humanSignoffs": decision},
    )

    calls = []

    class FakePlanService:
        def add_feedback(self, package_id, **kwargs):
            calls.append(("feedback", package_id, kwargs))

        def revise(self, package_id, **kwargs):
            calls.append(("revise", package_id, kwargs))
            revision = SimpleNamespace(
                id="prev_human",
                changedSections=["expectedMetrics", "qualityGate"],
            )
            return SimpleNamespace(revisions=[revision])

    import app.services.plan_package_service as plan_package_module

    monkeypatch.setattr(
        plan_package_module,
        "get_plan_package_service",
        lambda: FakePlanService(),
    )
    response = asyncio.run(reviews_api.apply_experiment_human_feedback_endpoint(
        stored["id"], reviews_api.ApplyHumanFeedbackRequest(),
    ))

    assert response.status == "applied_to_plan"
    assert response.planRevision["revisionId"] == "prev_human"
    assert calls[0][2]["target_sections"] == ["expectedMetrics"]
    assert "Add a leakage audit" in calls[0][2]["comment"]

    asyncio.run(reviews_api.decide_experiment_signoff_endpoint(
        stored["id"],
        "plan",
            reviews_api.HumanSignoffDecisionRequest(
            status="changes_requested",
            reviewerRole="domain_expert",
            reviewerId="expert@example.com",
            rationale="Add a confidence interval.",
            conditions=["Use paired bootstrap"],
                targetSections=["expectedMetrics"],
                acknowledgements=list(SIGNOFF_ACKNOWLEDGEMENTS["plan"]),
        ),
    ))
    second = asyncio.run(reviews_api.apply_experiment_human_feedback_endpoint(
        stored["id"], reviews_api.ApplyHumanFeedbackRequest(),
    ))

    assert second.feedbackHash != response.feedbackHash
    assert "Use paired bootstrap" in calls[2][2]["comment"]
    assert "Add a leakage audit" not in calls[2][2]["comment"]
