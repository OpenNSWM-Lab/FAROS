import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException, Response

from app.modules.review import experiment_feedback_storage, reviews_api
from app.modules.review.human_signoff import decide_human_signoff
from app.modules.review.signoff_dossier import (
    NOT_PROVIDED,
    build_signoff_dossier,
    render_signoff_dossier_html,
)


def _record():
    return {
        "id": "exprev_dossier",
        "runId": "run-dossier",
        "researchSeriesId": "series-dossier",
        "iterationNumber": 2,
        "questionId": "Can adaptive sampling reduce error?",
        "planPackageId": "plan-dossier",
        "reviewPurpose": "scientific_review",
        "publicationEligible": True,
        "sourceArtifacts": {"experimentEvidence": "artifact-evidence"},
        "benchmarkFingerprint": "sha256:benchmark",
        "metricSnapshot": [{
            "name": "trajectory_nrmse",
            "role": "primary",
            "direction": "minimize",
            "baseline": 0.24,
            "current": 0.17,
            "delta": -0.07,
            "ciLower": -0.10,
            "ciUpper": -0.04,
            "decision": "UPDATE",
            "split": "final_holdout",
        }],
        "qualityAssessment": {
            "gateStatus": "pass",
            "findings": [],
            "uncertainty": "Valid only for the frozen protocol.",
            "llmTrace": [],
        },
        "iterationDecision": {"decision": "accept_results"},
        "plan": {
            "hypothesis": "Adaptive excitation improves identifiability.",
            "baseline": "Uniform samples and one off-resonance excitation.",
            "intervention": "Allocate samples to transient and near-resonance regions.",
            "primaryMetric": "trajectory_nrmse",
            "guardrails": ["matched observation budget"],
            "stopConditions": ["CI must exclude zero"],
        },
        "planDelta": {
            "changedSections": ["sampling", "excitation"],
            "parameterChanges": [{
                "field": "excitation_frequency",
                "oldValue": 0.5,
                "newValue": 1.0,
                "rationale": "Fisher information condition number",
                "targetNode": "experiment",
            }],
            "evidenceReferences": ["artifact-evidence"],
        },
        "dataSource": ["deterministic ODE simulation"],
        "dataSplitPolicy": "development/calibration/final holdout",
        "createdAt": "2026-09-02T00:00:00+00:00",
    }


def _approve(record, stage):
    record["humanSignoffs"] = decide_human_signoff(
        record,
        stage=stage,
        status="approved",
        reviewer_role="team_lead",
        reviewer_id="reviewer",
        rationale=f"Reviewed {stage} evidence and claim boundaries.",
    )


def test_dossier_is_deterministic_and_source_linked():
    first = build_signoff_dossier(_record()).model_dump()
    second = build_signoff_dossier(_record()).model_dump()
    assert first["contentHash"] == second["contentHash"]
    first.pop("generatedAt")
    second.pop("generatedAt")
    assert first == second
    metric = first["evidence"]["metrics"][0]
    assert metric["sourceArtifactId"] == "artifact-evidence"
    assert metric["source"] == "record.metricSnapshot"


def test_dossier_marks_missing_fields_without_invention():
    record = _record()
    record["metricSnapshot"] = [{"name": "accuracy", "value": 0.8}]
    dossier = build_signoff_dossier(record)
    assert dossier.plan["hypothesis"] != NOT_PROVIDED
    metric = dossier.evidence["metrics"][0]
    assert metric["baseline"] is None
    assert metric["ciLower"] is None
    assert metric["direction"] == NOT_PROVIDED


def test_dossier_redacts_credentials_from_qwen_trace():
    record = _record()
    record["qualityAssessment"]["llmTrace"] = [{
        "model": "qwen",
        "headers": {"Authorization": "Bearer secret", "X-Request-Id": "req-1"},
        "api_key": "secret-key",
    }]
    serialized = build_signoff_dossier(record).model_dump_json().lower()
    assert "bearer secret" not in serialized
    assert "secret-key" not in serialized
    assert "req-1" in serialized


def test_draft_html_is_human_readable_watermarked_and_escaped():
    record = _record()
    record["limitations"] = ["<script>alert('xss')</script>"]
    html = render_signoff_dossier_html(build_signoff_dossier(record))
    assert "ReviewX 人工签核档案" in html
    assert "DRAFT_NOT_HUMAN_APPROVED" in html
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_official_html_requires_publication_ready(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(experiment_feedback_storage, "_STORAGE_DIR", tmp_path)
    stored = experiment_feedback_storage.create_experiment_feedback(_record())
    with pytest.raises(HTTPException) as blocked:
        asyncio.run(reviews_api.get_experiment_signoff_dossier_html_endpoint(
            stored["id"], "official"
        ))
    assert blocked.value.status_code == 409

    _approve(stored, "plan")
    _approve(stored, "conclusion")
    experiment_feedback_storage.update_experiment_feedback(
        stored["id"], {"humanSignoffs": stored["humanSignoffs"]}
    )
    response = asyncio.run(reviews_api.get_experiment_signoff_dossier_html_endpoint(
        stored["id"], "official"
    ))
    assert response.status_code == 200
    assert b"OFFICIAL_HUMAN_APPROVED" in response.body
    assert response.headers["cache-control"] == "no-store"


def test_official_json_requires_publication_ready_and_preserves_release(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(experiment_feedback_storage, "_STORAGE_DIR", tmp_path)
    stored = experiment_feedback_storage.create_experiment_feedback(_record())
    with pytest.raises(HTTPException) as blocked:
        asyncio.run(reviews_api.get_experiment_signoff_dossier_endpoint(
            stored["id"], "official"
        ))
    assert blocked.value.status_code == 409

    _approve(stored, "plan")
    _approve(stored, "conclusion")
    experiment_feedback_storage.update_experiment_feedback(
        stored["id"], {"humanSignoffs": stored["humanSignoffs"]}
    )
    response = Response()
    dossier = asyncio.run(reviews_api.get_experiment_signoff_dossier_endpoint(
        stored["id"], "official", response
    ))
    assert dossier.release == "official"
    assert dossier.watermark is None
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_raw_bundle_remains_backward_compatible(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(experiment_feedback_storage, "_STORAGE_DIR", tmp_path)
    stored = experiment_feedback_storage.create_experiment_feedback(_record())
    response = Response()
    draft = asyncio.run(reviews_api.get_experiment_evidence_bundle_endpoint(
        stored["id"], "draft", response
    ))
    assert draft["schemaVersion"] == "reviewx-human-approved-evidence/v1"
    assert draft["feedbackId"] == stored["id"]
    assert draft["watermark"] == "DRAFT_NOT_HUMAN_APPROVED"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
