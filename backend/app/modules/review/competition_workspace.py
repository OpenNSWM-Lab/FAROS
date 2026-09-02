"""Build a public, read-only view of the verified competition research chain."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Iterable

from app.modules.review.audit_chain import record_audit_integrity
from app.modules.review.competition_evidence import build_oscillator_evidence_view
from app.modules.review.human_signoff import publication_ready as is_publication_ready


def _read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _latest(
    paths: Iterable[Path],
    predicate: Callable[[dict[str, Any]], bool],
) -> tuple[Path, dict[str, Any]] | None:
    candidates: list[tuple[str, Path, dict[str, Any]]] = []
    for path in paths:
        if not path.is_file():
            continue
        try:
            payload = _read_object(path)
        except (json.JSONDecodeError, ValueError):
            continue
        if not predicate(payload):
            continue
        timestamp = str(
            payload.get("completedAt")
            or payload.get("updatedAt")
            or payload.get("createdAt")
            or ""
        )
        candidates.append((timestamp, path, payload))
    if not candidates:
        return None
    _, path, payload = max(candidates, key=lambda item: item[0])
    return path, payload


def _stage(stage_id: str, status: bool, entity_id: str, facts: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": stage_id,
        "status": "passed" if status else "blocked",
        "entityId": entity_id,
        "facts": facts,
    }


def _metric_map(path: Path) -> dict[str, float]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        return {}
    metrics: dict[str, float] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "")
        value = item.get("value")
        if key and isinstance(value, (int, float)):
            metrics[key] = float(value)
    return metrics


def _required_signoffs(feedback: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    signoffs = feedback.get("humanSignoffs") or {}
    required: list[str] = []
    approved: list[dict[str, Any]] = []
    for stage, value in signoffs.items():
        if not isinstance(value, dict) or not value.get("required"):
            continue
        required.append(str(stage))
        if str(value.get("status") or "") == "approved" and not value.get("stale"):
            approved.append(value)
    return required, approved


def build_competition_workspace_dashboard(data_dir: Path) -> dict[str, Any]:
    """Validate and summarize one linked Idea-to-ReviewX representative case."""

    root = data_dir.resolve()
    base_path = root / "competition_workspace_manifest.json"
    base = _read_object(base_path)
    research = base.get("researchDesign") or {}
    closed_loop = base.get("verifiedClosedLoop") or {}

    idea_id = str(research.get("ideaCandidateId") or "")
    plan_id = str(research.get("planPackageId") or "")
    review_job_id = str(closed_loop.get("jobId") or "")
    feedback_id = str(closed_loop.get("feedbackId") or "")
    if not all((idea_id, plan_id, review_job_id, feedback_id)):
        raise ValueError("Competition manifest does not identify the representative research chain")

    idea_path = root / "ideas" / "candidates" / f"{idea_id}.json"
    plan_path = root / "plan_packages" / f"{plan_id}.json"
    review_job_path = root / "competition_cases" / "reviewx_scifact" / "jobs" / f"{review_job_id}.json"
    review_summary_path = (
        root / "competition_cases" / "reviewx_scifact" / "runs" / review_job_id / "summary.json"
    )
    feedback_path = root / "reviewx_experiment_feedback" / f"{feedback_id}.json"
    idea = _read_object(idea_path)
    plan = _read_object(plan_path)
    review_job = _read_object(review_job_path)
    review_summary = _read_object(review_summary_path)
    feedback = _read_object(feedback_path)

    code_match = _latest(
        (root / "codegen_sessions").glob("cgs_*.json"),
        lambda item: str(item.get("planLinkId") or "") == plan_id
        and str(item.get("status") or "") == "completed",
    )
    code_path, code = code_match if code_match else (Path(), {})
    project_id = str(code.get("projectId") or "")

    experiment_match = _latest(
        (root / "experiments").glob("exp_*/experiment.json"),
        lambda item: str(item.get("projectId") or "") == project_id
        and str(item.get("planLinkId") or "") == plan_id,
    )
    experiment_path, experiment = experiment_match if experiment_match else (Path(), {})
    experiment_id = str(experiment.get("id") or "")
    evidence_path = (
        experiment_path.parent / "execution_evidence.json" if experiment_match else Path()
    )
    evidence = _read_object(evidence_path) if experiment_match and evidence_path.is_file() else {}
    metrics = _metric_map(experiment_path.parent / "metrics.json") if experiment_match else {}

    paper_match = _latest(
        (root / "papers").glob("paper_*/meta.json"),
        lambda item: str(item.get("projectId") or "") == project_id
        and experiment_id in [str(value) for value in (item.get("experimentIds") or [])]
        and str(review_job.get("runId") or "")
        in [str(value) for value in (item.get("runIds") or [])],
    )
    paper_path, paper = paper_match if paper_match else (Path(), {})
    paper_id = str(paper.get("id") or "")

    graph_evidence = idea.get("graphEvidence") or {}
    supporting_papers = graph_evidence.get("supportingPaperIds") or []
    idea_passed = bool(idea_id and len(supporting_papers) >= 4 and idea.get("hypothesisStatement"))

    plan_quality = float((plan.get("qualityGate") or {}).get("overallScore") or 0.0)
    plan_passed = str(plan.get("status") or "") == "approved" and plan_quality >= 0.9

    code_memory = code.get("memory") or {}
    verification = code_memory.get("verificationSummary") or {}
    code_passed = bool(
        code
        and float(verification.get("qualityScore") or 0) >= 90
        and int(verification.get("errorCount") or 0) == 0
        and str(code_memory.get("executionStatus") or "") == "passed"
        and str(code_memory.get("executionTestStatus") or "") == "passed"
    )

    evidence_checks = evidence.get("checks") or {}
    experiment_passed = bool(
        experiment
        and str(experiment.get("status") or "") == "completed"
        and str(evidence.get("status") or "") == "verified"
        and evidence_checks
        and all(value is True for value in evidence_checks.values())
    )

    paper_anonymous = paper.get("authors") == []
    paper_passed = bool(
        paper
        and str(paper.get("evidenceStatus") or "") == "collected"
        and paper_anonymous
        and paper.get("selectedFigures")
    )

    quality_gate = str((review_summary.get("qualityGate") or {}).get("status") or "").lower()
    required_signoffs, approved_signoffs = _required_signoffs(feedback)
    reviewer_ids = {
        str(item.get("actorAccountId") or "")
        for item in approved_signoffs
        if item.get("actorAccountId")
    }
    publication_ready = is_publication_ready(feedback)
    reviewer_policy = str(
        feedback.get("reviewerPolicy")
        or ("separated_reviewers" if feedback.get("enforceReviewerSeparation") else "single_accountable_reviewer")
    )
    reviewer_policy_passed = (
        len(reviewer_ids) >= 2 if reviewer_policy == "separated_reviewers" else len(reviewer_ids) == 1
    )
    auth_assurances = {
        str(item.get("authAssurance") or "unverified") for item in approved_signoffs
    }
    signoff_mode = next(iter(auth_assurances)) if len(auth_assurances) == 1 else "mixed_or_unverified"
    audit_valid = record_audit_integrity(feedback)["valid"]
    qwen_model = str(review_job.get("model") or "")
    review_passed = bool(
        quality_gate == "passed"
        and qwen_model.lower().startswith("qwen")
        and publication_ready
        and reviewer_policy_passed
        and audit_valid
    )

    stages = [
        _stage(
            "idea",
            idea_passed,
            idea_id,
            {
                "title": idea.get("title"),
                "supportingPaperCount": len(supporting_papers),
                "scoringMethod": idea.get("scoringMethod"),
            },
        ),
        _stage(
            "plan",
            plan_passed,
            plan_id,
            {"approval": plan.get("status"), "qualityScore": plan_quality},
        ),
        _stage(
            "code",
            code_passed,
            str(code.get("id") or ""),
            {
                "projectId": project_id,
                "provider": code.get("providerName"),
                "model": code.get("model"),
                "generatedFiles": code_memory.get("generatedFileCount"),
                "staticQualityScore": verification.get("qualityScore"),
                "offlineSmoke": code_memory.get("executionStatus"),
                "tests": code_memory.get("executionTestStatus"),
                "command": code_memory.get("executionCommand"),
            },
        ),
        _stage(
            "experiment",
            experiment_passed,
            experiment_id,
            {
                "dataset": "SciFact official train/dev",
                "predictionRows": evidence.get("predictionRows"),
                "ingestedMetrics": evidence.get("ingestedMetrics"),
                "bundleSha256": evidence.get("bundleSha256"),
                "holdoutBaselineF1": metrics.get("holdout_baseline.f1"),
                "holdoutMethodF1": metrics.get("holdout_method.f1"),
                "holdoutF1Delta": metrics.get("holdout_f1_delta"),
                "bootstrapCi95": [
                    metrics.get("bootstrap_f1_delta_ci95.0"),
                    metrics.get("bootstrap_f1_delta_ci95.1"),
                ],
                "interpretation": "transparent_control_not_primary_effect_claim",
            },
        ),
        _stage(
            "paper",
            paper_passed,
            paper_id,
            {
                "evidenceStatus": paper.get("evidenceStatus"),
                "anonymous": paper_anonymous,
                "figureCount": len(paper.get("selectedFigures") or []),
                "scope": "evidence_packet_only",
            },
        ),
        _stage(
            "reviewx",
            review_passed,
            str(review_job.get("runId") or ""),
            {
                "jobId": review_job_id,
                "qualityGate": quality_gate,
                "model": qwen_model,
                "publicationReady": publication_ready,
                "reviewerPolicy": reviewer_policy,
                "responsibleReviewerCount": len(reviewer_ids),
                "signoffMode": signoff_mode,
                "auditIntegrityValid": audit_valid,
                "requiredStages": required_signoffs,
                "approvedStages": len(approved_signoffs),
            },
        ),
    ]
    blockers = [stage["id"] for stage in stages if stage["status"] != "passed"]

    hashes = {
        "idea": _sha256(idea_path),
        "plan": _sha256(plan_path),
        "codeSession": _sha256(code_path) if code_match and code_path.is_file() else None,
        "experimentEvidence": (
            _sha256(evidence_path) if experiment_match and evidence_path.is_file() else None
        ),
        "paperMetadata": _sha256(paper_path) if paper_match and paper_path.is_file() else None,
        "reviewSummary": _sha256(review_summary_path),
        "humanFeedback": _sha256(feedback_path),
    }
    chain_digest = hashlib.sha256(
        json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    oscillator = build_oscillator_evidence_view(
        root / "experiments" / "reviewx_oscillator" / "latest"
    )

    return {
        "schemaVersion": "faros-competition-workspace/v2",
        "generatedAt": base.get("generatedAt"),
        "credentialPolicy": "No API keys, provider secrets, or private user settings are exposed.",
        "researchDesign": {
            "ideaSessionId": research.get("ideaSessionId"),
            "ideaCandidateId": idea_id,
            "planPackageId": plan_id,
        },
        "verifiedClosedLoop": {
            "jobId": review_job_id,
            "runId": review_job.get("runId"),
            "feedbackId": feedback_id,
        },
        "status": {
            "ready": not blockers,
            "passedStages": len(stages) - len(blockers),
            "totalStages": len(stages),
            "blockers": blockers,
            "integrity": "verified" if not blockers and all(hashes.values()) else "incomplete",
        },
        "stages": stages,
        "governance": {
            "reviewerPolicy": reviewer_policy,
            "responsibleReviewerCount": len(reviewer_ids),
            "signoffMode": signoff_mode,
            "publicationReady": publication_ready,
            "auditIntegrityValid": audit_valid,
        },
        "adaptiveOscillator": oscillator,
        "integrity": {**hashes, "chainSha256": f"sha256:{chain_digest}"},
    }
