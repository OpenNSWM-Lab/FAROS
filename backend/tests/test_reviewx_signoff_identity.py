import asyncio
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.core.user_context import use_user
from app.modules.review import experiment_feedback_storage, reviews_api
from app.modules.review.audit_chain import record_audit_integrity
from app.modules.review.human_signoff import SIGNOFF_ACKNOWLEDGEMENTS, publication_ready


def _record():
    return {
        "id": "exprev_identity",
        "runId": "run-identity",
        "questionId": "question-identity",
        "reviewPurpose": "scientific_review",
        "publicationEligible": True,
        "sourceArtifacts": {"experimentEvidence": "artifact-1"},
        "metricSnapshot": [{"name": "f1", "value": 0.8}],
        "qualityAssessment": {"gateStatus": "pass", "findings": []},
        "iterationDecision": {"decision": "accept_results"},
        "createdAt": "2026-09-02T00:00:00+00:00",
    }


def _request(stage: str, **updates):
    values = {
        "status": "approved",
        "reviewerRole": "team_lead",
        "reviewerId": "untrusted-body-identity",
        "reviewerName": "王子嘉",
        "rationale": f"Reviewed the {stage} evidence and responsibility boundary.",
        "acknowledgements": list(SIGNOFF_ACKNOWLEDGEMENTS[stage]),
    }
    values.update(updates)
    return reviews_api.HumanSignoffDecisionRequest(**values)


@pytest.fixture
def proxy_auth(monkeypatch):
    monkeypatch.setenv("FAROS_REVIEWX_AUTH_MODE", "proxy")
    monkeypatch.setenv("FAROS_REVIEWX_SIGNER_USERS", "faros-signer-wzj")
    monkeypatch.setenv("FAROS_REVIEWER_USERS", "faros-signer-wzj")


def test_proxy_actor_identity_cannot_be_spoofed(monkeypatch, tmp_path: Path, proxy_auth):
    monkeypatch.setattr(experiment_feedback_storage, "_STORAGE_DIR", tmp_path)
    stored = experiment_feedback_storage.create_experiment_feedback(_record())
    with use_user("faros-signer-wzj"):
        response = asyncio.run(reviews_api.decide_experiment_signoff_endpoint(
            stored["id"], "plan", _request("plan")
        ))
    plan = response.humanSignoffs["plan"]
    assert plan["actorAccountId"] == "faros-signer-wzj"
    assert plan["actorRole"] == "reviewer"
    assert plan["reviewerId"] == "untrusted-body-identity"
    assert plan["reviewerName"] == "王子嘉"
    assert plan["authAssurance"] == "trusted_proxy_basic_auth"


def test_judge_account_cannot_sign_and_data_does_not_change(
    monkeypatch, tmp_path: Path, proxy_auth
):
    monkeypatch.setattr(experiment_feedback_storage, "_STORAGE_DIR", tmp_path)
    stored = experiment_feedback_storage.create_experiment_feedback(_record())
    before = json.dumps(stored, sort_keys=True)
    with use_user("faros-judge"):
        with pytest.raises(HTTPException) as denied:
            asyncio.run(reviews_api.decide_experiment_signoff_endpoint(
                stored["id"], "plan", _request("plan")
            ))
    assert denied.value.status_code == 403
    after = experiment_feedback_storage.get_experiment_feedback(stored["id"])
    assert json.dumps(after, sort_keys=True) == before

    with use_user("faros-judge"):
        with pytest.raises(HTTPException) as denied_write:
            asyncio.run(reviews_api.apply_experiment_human_feedback_endpoint(
                stored["id"], reviews_api.ApplyHumanFeedbackRequest()
            ))
    assert denied_write.value.status_code == 403
    after_write = experiment_feedback_storage.get_experiment_feedback(stored["id"])
    assert json.dumps(after_write, sort_keys=True) == before


def test_team_can_prepare_evidence_but_cannot_sign(monkeypatch, tmp_path: Path, proxy_auth):
    monkeypatch.setattr(experiment_feedback_storage, "_STORAGE_DIR", tmp_path)
    stored = experiment_feedback_storage.create_experiment_feedback(_record())
    with use_user("faros-team"):
        with pytest.raises(HTTPException) as denied:
            asyncio.run(reviews_api.decide_experiment_signoff_endpoint(
                stored["id"], "plan", _request("plan")
            ))
    assert denied.value.status_code == 403
    assert "SIGNER_USERS" in str(denied.value.detail)


def test_signer_acknowledgements_are_required(monkeypatch, tmp_path: Path, proxy_auth):
    monkeypatch.setattr(experiment_feedback_storage, "_STORAGE_DIR", tmp_path)
    stored = experiment_feedback_storage.create_experiment_feedback(_record())
    with use_user("faros-signer-wzj"):
        with pytest.raises(HTTPException) as blocked:
            asyncio.run(reviews_api.decide_experiment_signoff_endpoint(
                stored["id"], "plan", _request("plan", acknowledgements=[])
            ))
    assert blocked.value.status_code == 409
    assert "required acknowledgements" in str(blocked.value.detail)


def test_signoff_history_tampering_revokes_release(
    monkeypatch, tmp_path: Path, proxy_auth
):
    monkeypatch.setattr(experiment_feedback_storage, "_STORAGE_DIR", tmp_path)
    stored = experiment_feedback_storage.create_experiment_feedback(_record())
    with use_user("faros-signer-wzj"):
        for stage in ("plan", "conclusion"):
            response = asyncio.run(reviews_api.decide_experiment_signoff_endpoint(
                stored["id"], stage, _request(stage)
            ))
    current = experiment_feedback_storage.get_experiment_feedback(stored["id"])
    assert response.publicationReady is True
    assert publication_ready(current) is True

    current["humanSignoffs"]["conclusion"]["acknowledgements"] = []
    assert record_audit_integrity(current)["valid"] is False
    assert publication_ready(current) is False


def test_local_test_assurance_cannot_unlock_official_release(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("FAROS_REVIEWX_AUTH_MODE", "local")
    monkeypatch.setattr(experiment_feedback_storage, "_STORAGE_DIR", tmp_path)
    stored = experiment_feedback_storage.create_experiment_feedback(_record())
    with use_user("local"):
        for stage in ("plan", "conclusion"):
            response = asyncio.run(reviews_api.decide_experiment_signoff_endpoint(
                stored["id"], stage, _request(stage)
            ))
    assert response.humanSignoffs["conclusion"]["authAssurance"] == "local_test"
    assert response.publicationReady is False
