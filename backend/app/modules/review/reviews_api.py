"""
Reviews API — Paper review generation and feedback loop.

Endpoints:
- POST /reviews              (create + generate review for a paper)
- GET  /reviews?paperId=     (list reviews)
- GET  /reviews/{id}         (get review detail)
- POST /reviews/{id}/apply   (apply selected action items as improvement requests)
- GET  /reviews/requests     (list improvement requests)
"""

import json
import logging
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Literal, Optional, List

from fastapi import APIRouter, Header, HTTPException, Query, Response, status
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from app.core.settings import get_settings
from app.core.paths import get_data_dir
from app.core.user_context import call_with_current_context
from app.contracts import ExecutionAssessment, ExperimentEvidence, QualityAssessment, ResearchDossier
from app.modules.review.experiment_feedback import (
    ExperimentFeedbackResult,
    ExperimentIterationDecision,
    experiment_metric_snapshot,
    review_experiment_feedback,
)
from app.modules.review.experiment_series import (
    ExperimentLoopPolicy,
    ExperimentSeriesProgress,
    evaluate_experiment_series,
    iteration_controller_feedback,
)
from app.modules.review.experiment_feedback_storage import (
    create_experiment_feedback,
    get_experiment_feedback,
    list_experiment_feedback,
    update_experiment_feedback,
)
from app.modules.review.human_signoff import (
    decide_human_signoff,
    human_signoff_state,
    publication_ready,
    require_human_signoff,
    signoff_required,
)
from app.modules.review.human_feedback import (
    human_feedback_comment,
    human_feedback_state,
    iteration_decision_with_human_feedback,
    require_human_feedback_applied,
)
from app.modules.review.human_feedback_verification import (
    decide_human_condition_verification,
    human_condition_verification_state,
)
from app.modules.review.audit_chain import record_audit_integrity
from app.modules.review.competition_evidence import (
    OSCILLATOR_PUBLIC_ARTIFACTS,
    build_competition_evidence_dashboard,
    build_oscillator_evidence_view,
)
from app.modules.review.competition_workspace import build_competition_workspace_dashboard
from app.modules.review.reviewer_auth import (
    ReviewAuthenticationError,
    authorize_reviewer,
    ensure_reviewx_write_access,
)
from app.modules.review.signoff_dossier import (
    SignoffDossier,
    build_signoff_dossier,
    render_signoff_dossier_html,
)
from app.modules.review.storage import (
    create_review as _create_review, get_review as _get_review,
    list_reviews as _list_reviews, update_review as _update_review,
    create_improvement_request, list_improvement_requests as _list_improvement_requests,
    update_improvement_request as _update_improvement_request,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reviews", tags=["reviews"])


class CreateReviewRequest(BaseModel):
    paperId: str
    reviewerProfile: str = "senior_reviewer"
    providerName: Optional[str] = None
    model: Optional[str] = None


class ApplyFeedbackRequest(BaseModel):
    actionItemIndices: List[int] = Field(..., description="0-based indices of action items to apply")


class RunReviewXRequest(BaseModel):
    paperId: str
    providerName: Optional[str] = None
    model: Optional[str] = None
    budgetMode: str = "balanced"
    ablationMode: str = "full"
    visualAuditEnabled: bool = False
    visualModel: Optional[str] = None


class UpdateImprovementRequest(BaseModel):
    status: str


class RunExperimentFeedbackRequest(BaseModel):
    dossier: ResearchDossier
    experimentEvidence: ExperimentEvidence
    executionAssessment: Optional[ExecutionAssessment] = None
    previousExperimentEvidence: Optional[ExperimentEvidence] = None
    sameResearchSeries: bool = False
    planPackageId: Optional[str] = None
    applyToPlanPackage: bool = False


class PlanFeedbackApplication(BaseModel):
    requested: bool = False
    applied: bool = False
    packageId: Optional[str] = None
    targetSections: List[str] = Field(default_factory=list)
    reason: str = ""


class RunExperimentFeedbackResponse(BaseModel):
    qualityAssessment: QualityAssessment
    iterationDecision: ExperimentIterationDecision
    planFeedback: PlanFeedbackApplication


class RunStoredExperimentFeedbackRequest(BaseModel):
    dossierArtifactId: Optional[str] = None
    executionAssessmentArtifactId: Optional[str] = None
    experimentEvidenceArtifactId: Optional[str] = None
    previousExperimentArtifactId: Optional[str] = None
    planPackageId: Optional[str] = None
    applyToPlanPackage: bool = False


class RunStoredExperimentFeedbackResponse(RunExperimentFeedbackResponse):
    feedbackId: str
    createdAt: str
    runId: str
    runKind: str = "platform"
    parentRunId: Optional[str] = None
    researchSeriesId: Optional[str] = None
    iterationNumber: int = 1
    sourceArtifacts: Dict[str, str] = Field(default_factory=dict)
    metricSnapshot: List[Dict[str, Any]] = Field(default_factory=list)
    benchmarkFingerprint: Optional[str] = None
    loopPolicy: Optional[ExperimentLoopPolicy] = None
    loopProgress: Optional[ExperimentSeriesProgress] = None
    humanSignoffs: Dict[str, Any] = Field(default_factory=dict)
    humanFeedback: Dict[str, Any] = Field(default_factory=dict)
    humanConditionVerifications: Dict[str, Any] = Field(default_factory=dict)
    sourceArtifactUrls: Dict[str, str] = Field(default_factory=dict)
    closedLoop: Dict[str, Any] = Field(default_factory=dict)
    reviewerPolicy: str = "single_accountable_reviewer"
    publicationReady: bool = False


class ReviseExperimentPlanRequest(BaseModel):
    generationMode: str = "deterministic"
    reviewerMode: str = "deterministic"


class ReviseExperimentPlanResponse(BaseModel):
    feedbackId: str
    packageId: str
    status: str
    revisionId: Optional[str] = None
    changedSections: List[str] = Field(default_factory=list)


class CreateNextExperimentRunResponse(BaseModel):
    feedbackId: str
    runId: str
    planPackageId: Optional[str] = None
    status: str
    reused: bool = False
    runKind: str = "platform"
    researchSeriesId: Optional[str] = None
    iterationNumber: int = 1


class AdvanceExperimentLoopRequest(BaseModel):
    policy: ExperimentLoopPolicy
    applyToPlanPackage: bool = False


class HumanSignoffDecisionRequest(BaseModel):
    status: Literal["approved", "rejected", "changes_requested"]
    reviewerRole: str = Field(min_length=2, max_length=80)
    reviewerId: str = Field(min_length=2, max_length=120)
    reviewerName: Optional[str] = Field(default=None, min_length=2, max_length=120)
    rationale: str = Field(min_length=3, max_length=2000)
    conditions: List[str] = Field(default_factory=list, max_length=20)
    targetSections: List[str] = Field(default_factory=list, max_length=20)
    acknowledgements: List[str] = Field(default_factory=list, max_length=20)


class HumanSignoffBatchDecisionRequest(BaseModel):
    reviewerRole: str = Field(min_length=2, max_length=80)
    reviewerId: str = Field(min_length=2, max_length=120)
    reviewerName: Optional[str] = Field(default=None, min_length=2, max_length=120)
    rationale: str = Field(min_length=3, max_length=2000)
    acknowledgementsByStage: Dict[str, List[str]] = Field(default_factory=dict)


class HumanSignoffResponse(BaseModel):
    feedbackId: str
    humanSignoffs: Dict[str, Any]
    humanFeedback: Dict[str, Any]
    humanConditionVerifications: Dict[str, Any]
    publicationReady: bool


class ApplyHumanFeedbackRequest(BaseModel):
    generationMode: Literal["deterministic", "hybrid"] = "deterministic"
    reviewerMode: Literal["deterministic", "hybrid"] = "deterministic"


class ApplyHumanFeedbackResponse(BaseModel):
    feedbackId: str
    feedbackHash: str
    status: str
    applied: bool
    reused: bool = False
    targetSections: List[str] = Field(default_factory=list)
    requiredActions: List[str] = Field(default_factory=list)
    planRevision: Optional[Dict[str, Any]] = None
    humanSignoffs: Dict[str, Any]
    humanFeedback: Dict[str, Any]
    humanConditionVerifications: Dict[str, Any]


class HumanConditionVerificationRequest(BaseModel):
    status: Literal["passed", "failed", "waived"]
    verifierRole: str = Field(min_length=2, max_length=80)
    verifierId: str = Field(min_length=2, max_length=120)
    rationale: str = Field(min_length=3, max_length=2000)
    evidenceArtifactIds: List[str] = Field(default_factory=list, max_length=20)


class HumanConditionVerificationResponse(BaseModel):
    feedbackId: str
    humanConditionVerifications: Dict[str, Any]
    humanSignoffs: Dict[str, Any]
    publicationReady: bool


class RunSciFactCompetitionCaseRequest(BaseModel):
    reuseLatest: bool = True
    model: Literal["qwen3.7-plus-2026-05-26"] = "qwen3.7-plus-2026-05-26"
    bootstrapSamples: int = Field(default=2000, ge=200, le=10000)


class SciFactCompetitionCaseJob(BaseModel):
    jobId: str
    status: Literal["queued", "running", "completed", "failed"]
    createdAt: str
    updatedAt: str
    model: str
    bootstrapSamples: int
    reused: bool = False
    runId: Optional[str] = None
    qualityGate: Optional[str] = None
    summaryUrl: Optional[str] = None
    reportUrl: Optional[str] = None
    feedbackId: Optional[str] = None
    error: Optional[str] = None
    stage: Optional[
        Literal["queued", "preparing", "executing", "registering", "completed", "failed"]
    ] = None
    progressPercent: Optional[int] = Field(default=None, ge=0, le=100)
    execution: Dict[str, Any] = Field(default_factory=dict)


class ReliabilityBenchmarkSummary(BaseModel):
    runId: str
    qualityGate: str
    datasets: List[str]
    totalCases: int
    faultyCases: int
    cleanCases: int
    scores: Dict[str, Any]
    repairEvaluation: Dict[str, Any]
    qwenModel: Optional[str] = None
    qwenUsage: Dict[str, int] = Field(default_factory=dict)
    qwenMisses: List[Dict[str, Any]] = Field(default_factory=list)
    reportUrl: str


_DATA_ROOT = get_data_dir()
_SCIFACT_CASE_ROOT = _DATA_ROOT / "competition_cases" / "reviewx_scifact"
_SCIFACT_JOB_LOCK = threading.Lock()
_PUBLIC_SCIFACT_ARTIFACTS = {
    "summary.json",
    "competition_report.md",
    "preregistration.json",
    "round_2_plan.json",
    "plan_delta_contract.json",
    "candidate_diagnostics.json",
    "qwen_iteration_plan.json",
    "qwen_trace.json",
    "timeline.json",
    "execution_timing.json",
    "experiment_series.json",
    "reviewx_round_1.json",
    "reviewx_round_2.json",
    "human_signoff.json",
}
_RELIABILITY_RESULT_ROOT = _DATA_ROOT / "experiments" / "reviewx_reliability"
_PLANNING_RESULT_PATH = _DATA_ROOT / "experiments" / "reviewx_planning" / "stability_summary.json"
_MULTIDOMAIN_RESULT_PATH = _DATA_ROOT / "experiments" / "reviewx_multidomain" / "summary.json"
_PEERQA_RESULT_ROOT = _DATA_ROOT / "experiments" / "reviewx_peerqa"
_OSCILLATOR_RESULT_ROOT = _DATA_ROOT / "experiments" / "reviewx_oscillator" / "latest"


def _read_json_object(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _review_loop_target_modules(
    record: Dict[str, Any], changes: List[Dict[str, Any]],
) -> List[str]:
    modules = {
        str(finding.get("targetModule") or "").strip()
        for finding in (record.get("qualityAssessment") or {}).get("findings") or []
        if isinstance(finding, dict) and finding.get("targetModule")
    }
    for change in changes:
        field_path = str(change.get("fieldPath") or "").lower()
        if field_path.startswith(("model.", "code.", "implementation.")):
            modules.add("code")
        elif field_path:
            modules.add("experiments")
    return sorted(modules)


def _build_review_loop_trace(record: Dict[str, Any]) -> Dict[str, Any]:
    """Return a compact, presentation-safe trace of one evidence-driven iteration."""

    decision = dict(record.get("iterationDecision") or {})
    progress = dict(record.get("loopProgress") or {})
    changes: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    from_run_id = str(record.get("runId") or "") or None
    to_run_id = str(record.get("nextRunId") or "") or None
    scientific_decision = str(decision.get("decision") or "needs_human")
    selected_candidate_id: Optional[str] = None
    final_holdout_protected = False
    contract_hash: Optional[str] = None

    job_id = str(record.get("competitionCaseJobId") or "").strip()
    if job_id:
        output_dir = _SCIFACT_CASE_ROOT / "runs" / job_id
        delta_contract = _read_json_object(output_dir / "plan_delta_contract.json")
        timeline = _read_json_object(output_dir / "timeline.json")
        if delta_contract:
            from_run_id = str(delta_contract.get("fromRunId") or from_run_id or "") or None
            to_run_id = str(delta_contract.get("toRunId") or to_run_id or "") or None
            scientific_decision = str(
                delta_contract.get("scientificDecision") or scientific_decision
            )
            selected_candidate_id = str(
                delta_contract.get("selectedCandidateId") or ""
            ) or None
            contract_hash = str(delta_contract.get("contentHash") or "") or None
            changes = [
                {
                    "fieldPath": str(item.get("fieldPath") or ""),
                    "before": item.get("before"),
                    "after": item.get("after"),
                    "rationale": str(item.get("rationale") or ""),
                    "evidenceIds": [str(value) for value in item.get("evidenceIds") or []],
                }
                for item in delta_contract.get("changes") or []
                if isinstance(item, dict) and item.get("fieldPath")
            ]
            qwen_contribution = dict(delta_contract.get("qwenContribution") or {})
            final_holdout_protected = qwen_contribution.get("finalHoldoutExposed") is False
        events = [
            {
                key: value
                for key, value in event.items()
                if key in {
                    "event", "timestamp", "gateStatus", "decision", "changedFields",
                    "selectedCandidateId", "finalHoldoutLoaded", "status",
                }
            }
            for event in timeline.get("events") or []
            if isinstance(event, dict) and event.get("event")
        ]

    if not changes:
        revision = dict(record.get("planRevision") or {})
        changes = [
            {
                "fieldPath": str(section),
                "before": None,
                "after": None,
                "rationale": "ReviewX feedback applied to the next iteration contract.",
                "evidenceIds": [],
            }
            for section in revision.get("changedSections") or []
        ]

    rounds = [
        {
            "runId": str(item.get("runId") or ""),
            "iterationNumber": int(item.get("iterationNumber") or index + 1),
            "value": item.get("value"),
            "delta": item.get("delta"),
            "improved": item.get("improved"),
            "gateStatus": item.get("gateStatus"),
            "decision": item.get("decision"),
            "guardrailsSatisfied": bool(item.get("guardrailsSatisfied", True)),
        }
        for index, item in enumerate(progress.get("rounds") or [])
        if isinstance(item, dict)
    ]
    if len(rounds) >= 2 and progress.get("status") == "completed":
        status_value = "completed"
    elif to_run_id:
        status_value = "iteration_created"
    elif scientific_decision == "accept_results":
        status_value = "accepted"
    else:
        status_value = "needs_iteration"

    iteration_number = int(record.get("iterationNumber") or 1)
    if job_id and to_run_id:
        from_iteration = max(1, iteration_number - 1)
        to_iteration = iteration_number
    elif to_run_id:
        from_iteration = iteration_number
        to_iteration = iteration_number + 1
    else:
        from_iteration = iteration_number
        to_iteration = None

    return {
        "status": status_value,
        "fromRunId": from_run_id,
        "toRunId": to_run_id,
        "researchSeriesId": record.get("researchSeriesId"),
        "fromIteration": from_iteration,
        "toIteration": to_iteration,
        "scientificDecision": scientific_decision,
        "targetModules": _review_loop_target_modules(record, changes),
        "targetSections": [str(value) for value in decision.get("targetSections") or []],
        "changes": changes,
        "rounds": rounds,
        "events": events,
        "primaryMetric": progress.get("primaryMetric"),
        "selectedCandidateId": selected_candidate_id,
        "benchmarkFingerprint": record.get("benchmarkFingerprint"),
        "contractHash": contract_hash,
        "finalHoldoutProtected": final_holdout_protected,
    }


def _scifact_job_path(job_id: str) -> Path:
    return _SCIFACT_CASE_ROOT / "jobs" / f"{job_id}.json"


def _write_scifact_job(job: Dict[str, Any]) -> Dict[str, Any]:
    path = _scifact_job_path(str(job["jobId"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    job["updatedAt"] = datetime.now(UTC).isoformat()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(job, ensure_ascii=True, indent=2), encoding="utf-8")
    temporary.replace(path)
    return job


def _load_scifact_job(job_id: str) -> Optional[Dict[str, Any]]:
    path = _scifact_job_path(job_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _list_scifact_jobs() -> List[Dict[str, Any]]:
    jobs_dir = _SCIFACT_CASE_ROOT / "jobs"
    if not jobs_dir.is_dir():
        return []
    records = [
        record
        for path in jobs_dir.glob("*.json")
        if (record := _load_scifact_job(path.stem)) is not None
    ]
    return sorted(records, key=lambda item: str(item.get("updatedAt") or ""), reverse=True)


def _register_scifact_human_review(job: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Register one completed SciFact case in the common ReviewX approval flow."""

    if job.get("status") != "completed" or not job.get("runId"):
        return None
    feedback_id = str(job.get("feedbackId") or "")
    if feedback_id:
        existing = get_experiment_feedback(feedback_id)
        if existing is not None:
            if existing.get("enforceReviewerSeparation") is not False:
                existing = update_experiment_feedback(
                    existing["id"], {
                        "enforceReviewerSeparation": False,
                        "reviewerPolicy": "single_accountable_reviewer",
                    },
                )
            return existing

    existing_records = list_experiment_feedback(run_id=str(job["runId"]), limit=20)
    existing = next(
        (
            record for record in existing_records
            if record.get("competitionCaseJobId") == job.get("jobId")
        ),
        None,
    )
    if existing is not None:
        if existing.get("enforceReviewerSeparation") is not False:
            existing = update_experiment_feedback(
                existing["id"], {
                    "enforceReviewerSeparation": False,
                    "reviewerPolicy": "single_accountable_reviewer",
                },
            )
        job["feedbackId"] = existing["id"]
        _write_scifact_job(job)
        return existing

    output_dir = _SCIFACT_CASE_ROOT / "runs" / str(job["jobId"])
    try:
        summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
        review_two = json.loads((output_dir / "reviewx_round_2.json").read_text(encoding="utf-8"))
        series = json.loads((output_dir / "experiment_series.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("SciFact human review requires valid summary, round-two review, and series artifacts") from exc

    quality_assessment = QualityAssessment.model_validate(
        review_two.get("qualityAssessment") or {}
    )
    iteration_decision = ExperimentIterationDecision.model_validate(
        review_two.get("iterationDecision") or {}
    )
    artifact_names = sorted(
        filename for filename in _PUBLIC_SCIFACT_ARTIFACTS
        if (output_dir / filename).is_file()
    )
    source_artifacts = {
        filename: f"scifact:{job['jobId']}:{filename}"
        for filename in artifact_names
    }
    artifact_base = f"/api/v1/reviews/reviewx/competition/scifact/jobs/{job['jobId']}/artifacts"
    metric_snapshot = [
        {
            "name": delta.name,
            "value": delta.current,
            "split": "SciFact official dev holdout",
        }
        for delta in iteration_decision.metricDeltas
    ]
    stored = create_experiment_feedback({
        "runId": str(job["runId"]),
        "runKind": "faros",
        "parentRunId": f"{job['runId']}_r1",
        "researchSeriesId": str(job["runId"]),
        "iterationNumber": 2,
        "scientificRunId": quality_assessment.runId,
        "questionId": quality_assessment.questionId,
        "planPackageId": None,
        "sourceArtifacts": source_artifacts,
        "sourceArtifactUrls": {
            filename: f"{artifact_base}/{filename}" for filename in artifact_names
        },
        "qualityAssessment": quality_assessment.model_dump(mode="json"),
        "iterationDecision": iteration_decision.model_dump(mode="json"),
        "metricSnapshot": metric_snapshot,
        "benchmarkFingerprint": summary.get("benchmarkFingerprint"),
        "loopProgress": series,
        "planFeedback": PlanFeedbackApplication(
            reason="Competition case uses its preregistered round-two plan; no PlanPackage write is required.",
        ).model_dump(mode="json"),
        "competitionCaseJobId": str(job["jobId"]),
        "competitionCase": "SciFact",
        "enforceReviewerSeparation": False,
        "reviewerPolicy": "single_accountable_reviewer",
    })
    job["feedbackId"] = stored["id"]
    _write_scifact_job(job)
    return stored


def _run_scifact_case(job_id: str, model: str, bootstrap_samples: int) -> None:
    job = _load_scifact_job(job_id)
    if job is None:
        return
    job.update({"status": "running", "stage": "preparing", "progressPercent": 10})
    _write_scifact_job(job)
    try:
        from experiments.reviewx_scifact.closed_loop import run_closed_loop
        from experiments.reviewx_scifact.run import ensure_dataset

        backend_data = get_data_dir()
        dataset_root = ensure_dataset(backend_data / "external" / "scifact", download=True)
        output_dir = _SCIFACT_CASE_ROOT / "runs" / job_id
        job.update({"stage": "executing", "progressPercent": 25})
        _write_scifact_job(job)
        summary = run_closed_loop(
            dataset_root,
            output_dir,
            model=model,
            bootstrap_samples=bootstrap_samples,
        )
        job.update({"stage": "registering", "progressPercent": 90})
        _write_scifact_job(job)
        job.update({
            "status": "completed",
            "stage": "completed",
            "progressPercent": 100,
            "runId": summary["runId"],
            "qualityGate": summary["qualityGate"]["status"],
            "summaryUrl": (
                f"/api/v1/reviews/reviewx/competition/scifact/jobs/{job_id}"
                "/artifacts/summary.json"
            ),
            "reportUrl": (
                f"/api/v1/reviews/reviewx/competition/scifact/jobs/{job_id}"
                "/artifacts/competition_report.md"
            ),
        })
        registered = _register_scifact_human_review(job)
        if registered is not None:
            job["feedbackId"] = registered["id"]
        _write_scifact_job(job)
    except Exception as exc:
        logger.exception("SciFact competition case %s failed", job_id)
        job.update({
            "status": "failed",
            "stage": "failed",
            "progressPercent": 100,
            "error": (
                f"{type(exc).__name__}: {str(exc)[:260]}. "
                "Check the Qwen account configuration, dataset connectivity, and compute-node status."
            ),
        })
        _write_scifact_job(job)


def _reviewer_policy(record: Dict[str, Any]) -> str:
    return str(
        record.get("reviewerPolicy")
        or (
            "separated_reviewers"
            if record.get("enforceReviewerSeparation")
            else "single_accountable_reviewer"
        )
    )


def _stored_feedback_response(record: Dict[str, Any]) -> RunStoredExperimentFeedbackResponse:
    return RunStoredExperimentFeedbackResponse(
        qualityAssessment=QualityAssessment.model_validate(record.get("qualityAssessment") or {}),
        iterationDecision=ExperimentIterationDecision.model_validate(
            record.get("iterationDecision") or {}
        ),
        planFeedback=PlanFeedbackApplication.model_validate(record.get("planFeedback") or {}),
        feedbackId=str(record["id"]),
        createdAt=str(record.get("createdAt") or ""),
        runId=str(record.get("runId") or ""),
        runKind=str(record.get("runKind") or "platform"),
        parentRunId=record.get("parentRunId"),
        researchSeriesId=record.get("researchSeriesId"),
        iterationNumber=int(record.get("iterationNumber") or 1),
        sourceArtifacts=dict(record.get("sourceArtifacts") or {}),
        metricSnapshot=list(record.get("metricSnapshot") or []),
        benchmarkFingerprint=record.get("benchmarkFingerprint"),
        loopPolicy=(
            ExperimentLoopPolicy.model_validate(record["loopPolicy"])
            if record.get("loopPolicy")
            else None
        ),
        loopProgress=(
            ExperimentSeriesProgress.model_validate(record["loopProgress"])
            if record.get("loopProgress")
            else None
        ),
        humanSignoffs=human_signoff_state(record),
        humanFeedback=human_feedback_state(record),
        humanConditionVerifications=human_condition_verification_state(record),
        sourceArtifactUrls=dict(record.get("sourceArtifactUrls") or {}),
        closedLoop=_build_review_loop_trace(record),
        reviewerPolicy=_reviewer_policy(record),
        publicationReady=publication_ready(record),
    )


def _require_iteration_signoffs(record: Dict[str, Any]) -> None:
    require_human_feedback_applied(record)
    require_human_signoff(record, "plan")
    if signoff_required(record, "repair"):
        require_human_signoff(record, "repair")


class AdvanceExperimentLoopResponse(BaseModel):
    feedbackId: str
    currentRunId: str
    nextRunId: Optional[str] = None
    progress: ExperimentSeriesProgress


def _reviewx_finding_to_dto(finding: Dict[str, Any], review: Dict[str, Any]) -> Dict[str, Any]:
    evidence_by_id = {ev.get("id"): ev for ev in review.get("evidence", [])}
    evidence_lines = []
    for evidence_id in finding.get("evidenceIds", []) or []:
        ev = evidence_by_id.get(evidence_id)
        if not ev:
            continue
        evidence_lines.append(f"{evidence_id}: {ev.get('summary', '')} ({ev.get('sourcePath', '')})")

    return {
        "id": finding.get("id"),
        "paperId": finding.get("paperId") or review.get("paperId"),
        "type": "consistency",
        "severity": str(finding.get("severity", "major")).lower(),
        "title": finding.get("title", "ReviewX finding"),
        "description": finding.get("description", ""),
        "evidence": "\n".join(evidence_lines) if evidence_lines else "No direct evidence linked; this is an evidence gap.",
        "suggestedFix": finding.get("suggestedFix", ""),
        "location": finding.get("location"),
        "riskType": finding.get("riskType"),
        "claimId": finding.get("claimId"),
        "evidenceIds": finding.get("evidenceIds", []),
        "targetModule": finding.get("targetModule", "papers"),
        "confidence": finding.get("confidence"),
        "supportStatus": finding.get("supportStatus"),
        "verifierIds": finding.get("verifierIds", []),
        "reviewerDecision": finding.get("reviewerDecision"),
        "reviewerAssessment": finding.get("reviewerAssessment"),
        "reviewerModel": finding.get("reviewerModel"),
        "cemCalibration": finding.get("cemCalibration", {}),
        "revisionRequestIds": finding.get("revisionRequestIds", []),
        "revisionStatus": finding.get("revisionStatus"),
    }


def _latest_reviewx_for_paper(paper_id: str) -> Optional[Dict[str, Any]]:
    candidates = [
        review for review in _list_reviews(paper_id=paper_id)
        if review.get("reviewKind") == "reviewx" and review.get("status") == "completed"
    ]
    candidates.sort(key=lambda review: review.get("updatedAt") or review.get("createdAt") or "", reverse=True)
    return candidates[0] if candidates else None


def _completed_reviewx_for_paper(paper_id: str) -> List[Dict[str, Any]]:
    reviews = [
        review for review in _list_reviews(paper_id=paper_id)
        if review.get("reviewKind") == "reviewx" and review.get("status") == "completed"
    ]
    reviews.sort(key=lambda review: review.get("updatedAt") or review.get("createdAt") or "", reverse=True)
    return reviews


def _reviewx_history_summary(review: Dict[str, Any]) -> Dict[str, Any]:
    summary = review.get("jsonReport", {}).get("summary", {}) if isinstance(review.get("jsonReport"), dict) else {}
    model_trace = review.get("modelTrace") or {}
    llm_routing = model_trace.get("llmRouting", {}) if isinstance(model_trace, dict) else {}
    support_counts = summary.get("supportCounts", {})
    mismatch = summary.get("mismatch", {}) or {}
    return {
        "id": review.get("id"),
        "paperId": review.get("paperId"),
        "status": review.get("status"),
        "budgetMode": review.get("budgetMode", "balanced"),
        "ablationMode": review.get("ablationMode", "full"),
        "visualAuditEnabled": bool(review.get("visualAuditEnabled", False)),
        "visualModel": review.get("visualModel"),
        "providerName": review.get("providerName"),
        "model": review.get("model"),
        "scoreSuggestion": review.get("scoreSuggestion"),
        "createdAt": review.get("createdAt"),
        "updatedAt": review.get("updatedAt"),
        "claimCount": summary.get("claimCount", len(review.get("claims", []) or [])),
        "evidenceCount": summary.get("evidenceCount", len(review.get("evidence", []) or [])),
        "findingCount": summary.get("findingCount", len(review.get("findings", []) or [])),
        "riskQuestionCount": summary.get("riskQuestionCount", len(review.get("riskTree", []) or [])),
        "severityCounts": summary.get("severityCounts", {}),
        "verificationCount": summary.get("verificationCount", len(review.get("verifications", []) or [])),
        "supportCounts": support_counts,
        "mismatch": mismatch,
        "llmCallCount": len(model_trace.get("llmCalls", []) or []) if isinstance(model_trace, dict) else 0,
        "llmSkipped": llm_routing.get("skipped"),
        "llmSkipReason": llm_routing.get("skipReason"),
        "visualAuditStatus": (model_trace.get("visualEvidenceAudit") or {}).get("status"),
    }


def _reviewx_compare_metrics(review: Dict[str, Any]) -> Dict[str, Any]:
    summary = review.get("jsonReport", {}).get("summary", {}) if isinstance(review.get("jsonReport"), dict) else {}
    severity_counts = summary.get("severityCounts", {}) or {}
    support_counts = summary.get("supportCounts", {}) or {}
    mismatch = summary.get("mismatch", {}) or {}
    requests = _list_improvement_requests(review_id=review.get("id"))
    return {
        "reviewId": review.get("id"),
        "updatedAt": review.get("updatedAt"),
        "score": review.get("scoreSuggestion"),
        "findingCount": summary.get("findingCount", len(review.get("findings", []) or [])),
        "blockerCount": severity_counts.get("blocker", 0),
        "majorCount": severity_counts.get("major", 0),
        "unsupportedCount": support_counts.get("unsupported", 0),
        "contradictedCount": support_counts.get("contradicted", 0),
        "artifactAbsentCount": support_counts.get("artifact_absent", 0),
        "needsHumanVerificationCount": support_counts.get("needs_human_verification", 0),
        "weaklySupportedCount": support_counts.get("weakly_supported", 0),
        "supportedCount": support_counts.get("supported", 0),
        "coverage": summary.get("coverage", 0),
        "verificationCount": summary.get("verificationCount", len(review.get("verifications", []) or [])),
        "riskQuestionCount": summary.get("riskQuestionCount", len(review.get("riskTree", []) or [])),
        "requestCount": len(requests),
        "resolvedRequestCount": len([req for req in requests if req.get("status") in {"resolved", "verified", "completed"}]),
        "meanMismatch": mismatch.get("meanMismatch", 0),
        "maxMismatch": mismatch.get("maxMismatch", 0),
        "highMismatchClaimCount": mismatch.get("highMismatchClaimCount", 0),
    }


def _metric_delta(before: Dict[str, Any], after: Dict[str, Any], key: str) -> Any:
    left = before.get(key)
    right = after.get(key)
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return round(right - left, 3)
    return None


def _finding_signature(finding: Dict[str, Any]) -> str:
    return "|".join([
        str(finding.get("claimId") or ""),
        str(finding.get("riskType") or ""),
        str(finding.get("targetModule") or ""),
        str(finding.get("title") or ""),
    ])


def _finding_summary(finding: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": finding.get("id"),
        "title": finding.get("title"),
        "description": finding.get("description"),
        "severity": finding.get("severity"),
        "riskType": finding.get("riskType"),
        "claimId": finding.get("claimId"),
        "targetModule": finding.get("targetModule"),
        "supportStatus": finding.get("supportStatus"),
        "confidence": finding.get("confidence"),
        "location": finding.get("location"),
        "suggestedFix": finding.get("suggestedFix"),
        "reviewerDecision": finding.get("reviewerDecision"),
        "reviewerAssessment": finding.get("reviewerAssessment"),
        "reviewerModel": finding.get("reviewerModel"),
        "cemCalibration": finding.get("cemCalibration", {}),
        "revisionStatus": finding.get("revisionStatus"),
        "revisionRequestIds": finding.get("revisionRequestIds", []),
    }


def _reviewx_compare_payload(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    before_metrics = _reviewx_compare_metrics(before)
    after_metrics = _reviewx_compare_metrics(after)
    before_findings = {_finding_signature(finding): finding for finding in before.get("findings", []) or []}
    after_findings = {_finding_signature(finding): finding for finding in after.get("findings", []) or []}
    before_keys = set(before_findings)
    after_keys = set(after_findings)
    metric_keys = [
        "score",
        "findingCount",
        "blockerCount",
        "majorCount",
        "unsupportedCount",
        "contradictedCount",
        "artifactAbsentCount",
        "needsHumanVerificationCount",
        "weaklySupportedCount",
        "supportedCount",
        "coverage",
        "requestCount",
        "resolvedRequestCount",
        "meanMismatch",
        "maxMismatch",
        "highMismatchClaimCount",
    ]
    return {
        "paperId": after.get("paperId") or before.get("paperId"),
        "before": before_metrics,
        "after": after_metrics,
        "delta": {key: _metric_delta(before_metrics, after_metrics, key) for key in metric_keys},
        "resolvedFindings": [_finding_summary(before_findings[key]) for key in sorted(before_keys - after_keys)[:20]],
        "newFindings": [_finding_summary(after_findings[key]) for key in sorted(after_keys - before_keys)[:20]],
        "persistentFindings": [_finding_summary(after_findings[key]) for key in sorted(before_keys & after_keys)[:20]],
    }


def _reviewx_eval_record(review: Dict[str, Any]) -> Dict[str, Any]:
    summary = review.get("jsonReport", {}).get("summary", {}) if isinstance(review.get("jsonReport"), dict) else {}
    model_trace = review.get("modelTrace") or {}
    mismatch_report = review.get("mismatchReport") or {}
    evidence_graph = review.get("evidenceGraph") or {}
    llm_routing = model_trace.get("llmRouting", {}) if isinstance(model_trace, dict) else {}
    requests = _list_improvement_requests(review_id=review.get("id"))
    return {
        "schemaVersion": "reviewx_eval_v1",
        "reviewId": review.get("id"),
        "paperId": review.get("paperId"),
        "createdAt": review.get("createdAt"),
        "updatedAt": review.get("updatedAt"),
        "budgetMode": review.get("budgetMode", "balanced"),
        "ablationMode": review.get("ablationMode", "full"),
        "providerName": review.get("providerName"),
        "model": review.get("model"),
        "scoreSuggestion": review.get("scoreSuggestion"),
        "summary": summary,
        "mismatchAggregate": mismatch_report.get("aggregate", summary.get("mismatch", {})),
        "claimScores": mismatch_report.get("claimScores", []),
        "cemMethod": mismatch_report.get("method", {}),
        "graphStats": {
            "nodeCount": evidence_graph.get("nodeCount", 0),
            "edgeCount": evidence_graph.get("edgeCount", 0),
        },
        "findings": [_finding_summary(finding) for finding in review.get("findings", []) or []],
        "actionItems": review.get("actionItems", []) or [],
        "improvementRequests": requests,
        "modelTrace": {
            "routingMode": model_trace.get("routingMode"),
            "localRulePasses": model_trace.get("localRulePasses", []),
            "llmCallCount": len(model_trace.get("llmCalls", []) or []),
            "estimatedTokenCost": model_trace.get("estimatedTokenCost", 0),
            "budgetPolicy": llm_routing.get("budgetPolicy"),
            "routingStrategy": llm_routing.get("routingStrategy"),
            "selectedFindingIds": llm_routing.get("selectedFindingIds", []),
            "selectedFindingCount": len(llm_routing.get("selectedFindingIds", []) or []),
            "llmRouting": llm_routing,
        },
        "metrics": _reviewx_compare_metrics(review),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_review_endpoint(req: CreateReviewRequest):
    """Create a review and immediately start generation in background."""
    from app.modules.review.storage import get_paper
    paper = get_paper(req.paperId)
    if not paper:
        raise HTTPException(status_code=404, detail=f"Paper '{req.paperId}' not found")

    settings = get_settings()
    provider_name = req.providerName or settings.get_active_provider()
    model = req.model or settings.get_active_model(provider_name)
    record = _create_review(req.model_dump() | {"providerName": provider_name, "model": model})

    def _run():
        try:
            from app.modules.review.service import generate_review
            generate_review(record["id"])
        except Exception as e:
            logger.error(f"Review generation failed: {e}", exc_info=True)

    thread = threading.Thread(
        target=call_with_current_context(_run),
        args=(),
        daemon=True,
    )
    thread.start()

    return record


@router.get("")
async def list_reviews_endpoint(paperId: Optional[str] = None):
    reviews = _list_reviews(paper_id=paperId)
    return {"reviews": reviews, "total": len(reviews)}


@router.get("/requests")
async def list_improvement_requests_endpoint(
    reviewId: Optional[str] = None,
    paperId: Optional[str] = None,
    targetModule: Optional[str] = None,
):
    requests = _list_improvement_requests(
        review_id=reviewId, paper_id=paperId, target_module=targetModule,
    )
    return {"requests": requests, "total": len(requests)}


@router.patch("/requests/{request_id}")
async def update_improvement_request_endpoint(request_id: str, req: UpdateImprovementRequest):
    allowed_statuses = {"pending", "in_progress", "resolved", "verified", "dismissed", "completed"}
    if req.status not in allowed_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status '{req.status}'")
    updated = _update_improvement_request(request_id, {"status": req.status})
    if not updated:
        raise HTTPException(status_code=404, detail=f"Improvement request '{request_id}' not found")
    return updated


@router.post("/reviewx/run")
async def run_reviewx_endpoint(req: RunReviewXRequest):
    """Run deterministic evidence-grounded ReviewX and return frontend findings."""
    from app.modules.review.storage import get_paper
    paper = get_paper(req.paperId)
    if not paper:
        raise HTTPException(status_code=404, detail=f"Paper '{req.paperId}' not found")

    settings = get_settings()
    provider_name = req.providerName or settings.get_active_provider()
    model = req.model or settings.get_active_model(provider_name)
    record = _create_review({
        "paperId": req.paperId,
        "reviewerProfile": "reviewx_evidence_auditor",
        "providerName": provider_name,
        "model": model,
        "reviewKind": "reviewx",
        "budgetMode": req.budgetMode,
        "ablationMode": req.ablationMode,
        "visualAuditEnabled": req.visualAuditEnabled,
        "visualModel": req.visualModel,
    })

    try:
        from app.modules.review.service import generate_reviewx
        review = generate_reviewx(record["id"])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"ReviewX failed: {str(exc)[:300]}")

    return [_reviewx_finding_to_dto(finding, review) for finding in review.get("findings", [])]


@router.get("/reviewx/findings")
async def list_reviewx_findings_endpoint(paperId: str):
    review = _latest_reviewx_for_paper(paperId)
    if not review:
        return []
    return [_reviewx_finding_to_dto(finding, review) for finding in review.get("findings", [])]


@router.get("/reviewx/history")
async def list_reviewx_history_endpoint(paperId: str):
    reviews = [
        review for review in _list_reviews(paper_id=paperId)
        if review.get("reviewKind") == "reviewx"
    ]
    reviews.sort(key=lambda review: review.get("updatedAt") or review.get("createdAt") or "", reverse=True)
    return {
        "reviews": [_reviewx_history_summary(review) for review in reviews],
        "total": len(reviews),
    }


@router.get("/reviewx/compare")
async def compare_reviewx_endpoint(
    paperId: str,
    baseReviewId: Optional[str] = None,
    targetReviewId: Optional[str] = None,
):
    reviews = _completed_reviewx_for_paper(paperId)
    if len(reviews) < 2 and not (baseReviewId and targetReviewId):
        raise HTTPException(status_code=404, detail="At least two completed ReviewX runs are required for comparison")

    by_id = {review.get("id"): review for review in reviews}
    target = by_id.get(targetReviewId) if targetReviewId else reviews[0]
    if not target:
        raise HTTPException(status_code=404, detail=f"Target ReviewX run '{targetReviewId}' not found")

    base = by_id.get(baseReviewId) if baseReviewId else None
    if not base:
        target_index = next((idx for idx, review in enumerate(reviews) if review.get("id") == target.get("id")), -1)
        if target_index >= 0 and target_index + 1 < len(reviews):
            base = reviews[target_index + 1]
        elif len(reviews) >= 2:
            base = reviews[1]
    if not base:
        raise HTTPException(status_code=404, detail="No earlier ReviewX run found for comparison")

    return _reviewx_compare_payload(base, target)


@router.get("/reviewx/latest")
async def get_latest_reviewx_endpoint(paperId: str):
    review = _latest_reviewx_for_paper(paperId)
    if not review:
        raise HTTPException(status_code=404, detail=f"No completed ReviewX run for paper '{paperId}'")
    return review


@router.get("/reviewx/eval-record")
async def get_reviewx_eval_record_endpoint(
    paperId: Optional[str] = None,
    reviewId: Optional[str] = None,
):
    if reviewId:
        review = _get_review(reviewId)
        if not review:
            raise HTTPException(status_code=404, detail=f"Review '{reviewId}' not found")
    elif paperId:
        review = _latest_reviewx_for_paper(paperId)
        if not review:
            raise HTTPException(status_code=404, detail=f"No completed ReviewX run for paper '{paperId}'")
    else:
        raise HTTPException(status_code=400, detail="paperId or reviewId is required")

    if review.get("reviewKind") != "reviewx":
        raise HTTPException(status_code=400, detail="Review is not a ReviewX run")
    return _reviewx_eval_record(review)


@router.post(
    "/reviewx/competition/scifact/jobs",
    response_model=SciFactCompetitionCaseJob,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start or reuse the auditable SciFact two-round competition case",
)
async def start_scifact_competition_case_endpoint(
    req: RunSciFactCompetitionCaseRequest,
) -> SciFactCompetitionCaseJob:
    from app.code.execution import execution_is_allowed, get_compute_snapshot

    if not execution_is_allowed():
        raise HTTPException(
            status_code=503,
            detail=(
                "Track 1B verification cannot run on the public control server. "
                "Restore the private compute-node tunnel and retry."
            ),
        )
    compute = get_compute_snapshot()
    with _SCIFACT_JOB_LOCK:
        jobs = _list_scifact_jobs()
        if req.reuseLatest:
            latest = next(
                (
                    item for item in jobs
                    if item.get("status") == "completed"
                    and item.get("qualityGate") == "passed"
                ),
                None,
            )
            if latest is not None:
                registered = _register_scifact_human_review(latest)
                if registered is not None:
                    latest["feedbackId"] = registered["id"]
                return SciFactCompetitionCaseJob.model_validate({
                    **latest,
                    "reused": True,
                })
        active = next(
            (item for item in jobs if item.get("status") in {"queued", "running"}),
            None,
        )
        if active is not None:
            return SciFactCompetitionCaseJob.model_validate({
                **active,
                "reused": True,
            })
        now = datetime.now(UTC).isoformat()
        job_id = f"scifact_case_{uuid.uuid4().hex[:12]}"
        job = _write_scifact_job({
            "jobId": job_id,
            "status": "queued",
            "stage": "queued",
            "progressPercent": 5,
            "createdAt": now,
            "updatedAt": now,
            "model": req.model,
            "bootstrapSamples": req.bootstrapSamples,
            "reused": False,
            "runId": None,
            "qualityGate": None,
            "summaryUrl": None,
            "reportUrl": None,
            "error": None,
            "execution": {
                "nodeName": compute["nodeName"],
                "location": compute["location"],
                "workload": "trusted_builtin_cpu",
                "gpuCount": 0,
                "isolated": False,
            },
        })
        worker = threading.Thread(
            target=call_with_current_context(_run_scifact_case),
            args=(job_id, req.model, req.bootstrapSamples),
            name=f"faros-{job_id}",
            daemon=True,
        )
        worker.start()
        return SciFactCompetitionCaseJob.model_validate(job)


@router.get(
    "/reviewx/competition/scifact/jobs/latest",
    response_model=SciFactCompetitionCaseJob,
    summary="Get the latest SciFact competition case job",
)
async def get_latest_scifact_competition_case_endpoint() -> SciFactCompetitionCaseJob:
    jobs = _list_scifact_jobs()
    if not jobs:
        raise HTTPException(status_code=404, detail="No SciFact competition case has run yet")
    job = jobs[0]
    registered = _register_scifact_human_review(job)
    if registered is not None:
        job["feedbackId"] = registered["id"]
    return SciFactCompetitionCaseJob.model_validate(job)


@router.get(
    "/reviewx/competition/scifact/jobs/{job_id}",
    response_model=SciFactCompetitionCaseJob,
    summary="Get one SciFact competition case job",
)
async def get_scifact_competition_case_endpoint(job_id: str) -> SciFactCompetitionCaseJob:
    job = _load_scifact_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"SciFact competition job '{job_id}' not found")
    registered = _register_scifact_human_review(job)
    if registered is not None:
        job["feedbackId"] = registered["id"]
    return SciFactCompetitionCaseJob.model_validate(job)


@router.get(
    "/reviewx/competition/scifact/jobs/{job_id}/artifacts/{filename}",
    summary="Download a public, non-secret SciFact competition artifact",
)
async def get_scifact_competition_artifact_endpoint(job_id: str, filename: str):
    if filename not in _PUBLIC_SCIFACT_ARTIFACTS:
        raise HTTPException(status_code=404, detail="Artifact is not on the public evidence allowlist")
    job = _load_scifact_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"SciFact competition job '{job_id}' not found")
    path = _SCIFACT_CASE_ROOT / "runs" / job_id / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Artifact '{filename}' is not available")
    media_type = "application/json" if filename.endswith(".json") else "text/markdown"
    return FileResponse(path, media_type=media_type, filename=filename)


@router.get(
    "/reviewx/competition/oscillator",
    summary="Get the verified adaptive-oscillator evidence summary",
)
async def get_oscillator_evidence_endpoint():
    evidence = build_oscillator_evidence_view(_OSCILLATOR_RESULT_ROOT)
    if not evidence.get("available"):
        raise HTTPException(status_code=404, detail=evidence["blockingReasons"][0])
    return evidence


@router.get(
    "/reviewx/competition/oscillator/artifacts/{artifact_path:path}",
    summary="Download an allowlisted adaptive-oscillator artifact",
)
async def get_oscillator_artifact_endpoint(artifact_path: str):
    if artifact_path not in OSCILLATOR_PUBLIC_ARTIFACTS:
        raise HTTPException(status_code=404, detail="Artifact is not on the public evidence allowlist")
    path = _OSCILLATOR_RESULT_ROOT / artifact_path
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Artifact '{artifact_path}' is not available")
    media_type = (
        "application/json" if artifact_path.endswith(".json")
        else "text/csv" if artifact_path.endswith(".csv")
        else "text/markdown" if artifact_path.endswith(".md")
        else "text/plain"
    )
    return FileResponse(
        path,
        media_type=media_type,
        filename=Path(artifact_path).name,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get(
    "/reviewx/competition/dashboard",
    summary="Get the verified track-1B evidence dashboard",
)
async def get_competition_evidence_dashboard_endpoint():
    jobs = _list_scifact_jobs()
    job = next(
        (
            item for item in jobs
            if item.get("status") == "completed"
            and (_SCIFACT_CASE_ROOT / "runs" / str(item.get("jobId"))).is_dir()
        ),
        None,
    )
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="No completed SciFact competition case is available",
        )
    try:
        registered = _register_scifact_human_review(job)
        if registered is not None:
            job["feedbackId"] = registered["id"]
        return build_competition_evidence_dashboard(
            job=job,
            case_dir=_SCIFACT_CASE_ROOT / "runs" / str(job["jobId"]),
            reliability_summary_path=_RELIABILITY_RESULT_ROOT / "summary.json",
            planning_summary_path=_PLANNING_RESULT_PATH,
            multidomain_summary_path=_MULTIDOMAIN_RESULT_PATH,
            peerqa_summary_path=_PEERQA_RESULT_ROOT / "top5_summary.json",
            peerqa_full_audit_summary_path=_PEERQA_RESULT_ROOT / "fullaudit_summary.json",
            feedback_record=registered,
            public_artifacts=_PUBLIC_SCIFACT_ARTIFACTS,
            oscillator_run_dir=_OSCILLATOR_RESULT_ROOT,
        )
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("Competition evidence dashboard is incomplete: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=f"Competition evidence is incomplete or invalid: {exc}",
        ) from exc


@router.get(
    "/reviewx/competition/workspace",
    summary="Get the validated Idea-to-ReviewX representative research chain",
)
async def get_competition_workspace_dashboard_endpoint():
    try:
        return build_competition_workspace_dashboard(get_data_dir())
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("Competition workspace evidence is incomplete: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=f"Competition workspace evidence is incomplete or invalid: {exc}",
        ) from exc


@router.get(
    "/reviewx/competition/peerqa/report",
    summary="Download the frozen fair Top-5 PeerQA proxy report",
)
async def get_peerqa_benchmark_report_endpoint():
    path = _PEERQA_RESULT_ROOT / "top5_report.md"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="PeerQA benchmark report is unavailable")
    return FileResponse(path, media_type="text/markdown", filename="reviewx_peerqa_top5_report.md")


@router.get(
    "/reviewx/competition/peerqa/full-audit-report",
    summary="Download the unequal-output PeerQA full-audit report",
)
async def get_peerqa_full_audit_report_endpoint():
    path = _PEERQA_RESULT_ROOT / "fullaudit_report.md"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="PeerQA full-audit report is unavailable")
    return FileResponse(
        path,
        media_type="text/markdown",
        filename="reviewx_peerqa_full_audit_report.md",
    )


@router.get(
    "/reviewx/competition/reliability/latest",
    response_model=ReliabilityBenchmarkSummary,
    summary="Get the latest public ReviewX reliability benchmark result",
)
async def get_latest_reliability_benchmark_endpoint() -> ReliabilityBenchmarkSummary:
    path = _RELIABILITY_RESULT_ROOT / "summary.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No reliability benchmark result is available")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail="Reliability benchmark result is unreadable") from exc
    return ReliabilityBenchmarkSummary(
        runId=str(payload.get("runId") or ""),
        qualityGate=str((payload.get("qualityGate") or {}).get("status") or "unknown"),
        datasets=[str(item) for item in payload.get("datasets") or []],
        totalCases=int((payload.get("caseAudit") or {}).get("total") or 0),
        faultyCases=int((payload.get("caseAudit") or {}).get("faulty") or 0),
        cleanCases=int((payload.get("caseAudit") or {}).get("clean") or 0),
        scores=dict(payload.get("scores") or {}),
        repairEvaluation=dict(payload.get("repairEvaluation") or {}),
        qwenModel=(payload.get("qwenTrace") or {}).get("model"),
        qwenUsage=dict((payload.get("qwenTrace") or {}).get("totalUsage") or {}),
        qwenMisses=list(payload.get("qwenMisses") or []),
        reportUrl="/api/v1/reviews/reviewx/competition/reliability/report",
    )


@router.get(
    "/reviewx/competition/reliability/report",
    summary="Download the public ReviewX reliability benchmark report",
)
async def get_reliability_benchmark_report_endpoint():
    path = _RELIABILITY_RESULT_ROOT / "experiment_report.md"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Reliability benchmark report is unavailable")
    return FileResponse(path, media_type="text/markdown", filename="reviewx_reliability_report.md")


@router.post(
    "/reviewx/experiment-feedback",
    response_model=RunExperimentFeedbackResponse,
    summary="Audit experiment evidence and route the next research iteration",
)
async def run_experiment_feedback_endpoint(
    req: RunExperimentFeedbackRequest,
) -> RunExperimentFeedbackResponse:
    """Run the Direction-B gate and optionally attach its feedback to a PlanPackage."""

    result: ExperimentFeedbackResult = review_experiment_feedback(
        req.dossier,
        req.experimentEvidence,
        execution_assessment=req.executionAssessment,
        previous_experiment=req.previousExperimentEvidence,
        same_research_series=req.sameResearchSeries,
    )
    application = PlanFeedbackApplication(
        requested=req.applyToPlanPackage,
        packageId=req.planPackageId,
        targetSections=result.iterationDecision.targetSections,
    )

    if req.applyToPlanPackage:
        if not req.planPackageId:
            raise HTTPException(status_code=422, detail="planPackageId is required when applyToPlanPackage is true")
        if result.iterationDecision.decision == "accept_results":
            application.reason = "Accepted results do not require a corrective PlanPackage feedback item."
        else:
            from app.services.plan_package_service import (
                PlanPackageNotFoundError,
                get_plan_package_service,
            )

            try:
                get_plan_package_service().add_feedback(
                    req.planPackageId,
                    section_path="experimentFeedback",
                    display_label="ReviewX experiment feedback",
                    source_view="reviewx",
                    target_sections=result.iterationDecision.targetSections or ["stages", "expectedMetrics"],
                    feedback_type="correction",
                    comment=result.iterationDecision.feedbackComment,
                    severity=(
                        "blocking"
                        if result.qualityAssessment.gateStatus == "fail"
                        else "high"
                    ),
                    requested_action=(
                        "repair"
                        if result.iterationDecision.decision == "rerun_experiment"
                        else "revise"
                    ),
                )
            except PlanPackageNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc))
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            application.applied = True
            application.reason = "ReviewX experiment feedback was attached to the PlanPackage."

    return RunExperimentFeedbackResponse(
        qualityAssessment=result.qualityAssessment,
        iterationDecision=result.iterationDecision,
        planFeedback=application,
    )


def _load_run_contract_artifact(
    run_id: str,
    filename: str,
    model_type,
    artifact_id: Optional[str] = None,
    *,
    require_platform_run_match: bool = True,
):
    from app.faros.runtime.state_store import FarosStateStore
    from app.storage.artifact_storage import get_storage as get_artifact_storage

    artifact_storage = get_artifact_storage()
    artifact = None
    if artifact_id:
        artifact = artifact_storage.get(artifact_id)
        if artifact is not None and require_platform_run_match and artifact.runId != run_id:
            raise HTTPException(
                status_code=422,
                detail=f"Artifact '{artifact_id}' belongs to run '{artifact.runId}', not '{run_id}'",
            )
    else:
        candidates = [
            item
            for item in artifact_storage.list_by_run(run_id)
            if Path(item.filename).name.lower() == filename.lower()
        ]
        artifact = candidates[0] if candidates else None

    if artifact is None:
        state_store = FarosStateStore()
        candidate_run_ids = [run_id]
        if artifact_id and not require_platform_run_match:
            candidate_run_ids.extend(
                item["id"] for item in state_store.list_runs() if item.get("id") != run_id
            )
        faros_candidates = []
        for candidate_run_id in candidate_run_ids:
            for item in state_store.list_artifacts(candidate_run_id):
                uri = str(item.get("uri") or "")
                if not uri.startswith("file://"):
                    continue
                if artifact_id and item.get("id") != artifact_id:
                    continue
                if not artifact_id and Path(uri.removeprefix("file://")).name.lower() != filename.lower():
                    continue
                faros_candidates.append((candidate_run_id, item))
        if faros_candidates:
            artifact_run_id, item = faros_candidates[0]
            if require_platform_run_match and artifact_run_id != run_id:
                raise HTTPException(
                    status_code=422,
                    detail=f"Artifact '{item.get('id')}' belongs to run '{artifact_run_id}', not '{run_id}'",
                )
            raw_path = Path(str(item["uri"]).removeprefix("file://"))
            if not raw_path.is_absolute():
                raw_path = get_data_dir() / raw_path
            artifact = SimpleNamespace(
                id=item["id"],
                runId=artifact_run_id,
                filename=raw_path.name,
                storagePath=str(raw_path),
            )

    if artifact is None:
        if artifact_id:
            raise HTTPException(status_code=404, detail=f"Artifact '{artifact_id}' not found")
        raise HTTPException(
            status_code=422,
            detail={
                "message": f"Run '{run_id}' is missing required ReviewX evidence",
                "requiredFilename": filename,
                "runId": run_id,
            },
        )

    path = Path(artifact.storagePath)
    if not path.is_file():
        raise HTTPException(
            status_code=422,
            detail=f"Artifact '{artifact.id}' has no readable file at '{artifact.storagePath}'",
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        model = model_type.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Artifact '{artifact.id}' is not valid {model_type.__name__}: {exc}",
        )
    return artifact, model


def _faros_run_lineage(run_id: str) -> Optional[Dict[str, Any]]:
    from app.faros.runtime.state_store import FarosStateStore

    store = FarosStateStore()
    get_run = getattr(store, "get_run", None)
    run = get_run(run_id) if callable(get_run) else None
    if run is None:
        return None
    inputs = run.get("inputs") or {}
    iteration_feedback = (
        inputs.get("iterationFeedback")
        if isinstance(inputs.get("iterationFeedback"), dict)
        else {}
    )
    return {
        "runKind": "faros",
        "parentRunId": run.get("parent_run_id"),
        "researchSeriesId": run.get("research_series_id") or run_id,
        "iterationNumber": int(run.get("iteration_number") or 1),
        "inheritedHumanFeedback": dict(
            iteration_feedback.get("humanFeedback") or {}
        ),
    }


def _faros_runs_share_series(current_run_id: str, previous_run_id: str) -> bool:
    current = _faros_run_lineage(current_run_id)
    previous = _faros_run_lineage(previous_run_id)
    return bool(
        current
        and previous
        and current["researchSeriesId"] == previous["researchSeriesId"]
    )


@router.post(
    "/reviewx/runs/{run_id}/experiment-feedback",
    response_model=RunStoredExperimentFeedbackResponse,
    summary="Resolve a run's scientific artifacts and audit its experiment feedback",
)
async def run_stored_experiment_feedback_endpoint(
    run_id: str,
    req: RunStoredExperimentFeedbackRequest,
) -> RunStoredExperimentFeedbackResponse:
    """Resolve contract artifacts produced by the integrated FAROS run."""

    try:
        ensure_reviewx_write_access()
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    default_artifact_selection = not any((
        req.dossierArtifactId,
        req.executionAssessmentArtifactId,
        req.experimentEvidenceArtifactId,
        req.previousExperimentArtifactId,
        req.planPackageId,
        req.applyToPlanPackage,
    ))
    if default_artifact_selection:
        existing = list_experiment_feedback(run_id=run_id, limit=1)
        if existing:
            return _stored_feedback_response(existing[0])

    dossier_artifact, dossier = _load_run_contract_artifact(
        run_id,
        "research_dossier.json",
        ResearchDossier,
        req.dossierArtifactId,
    )
    evidence_artifact, evidence = _load_run_contract_artifact(
        run_id,
        "experiment_evidence.json",
        ExperimentEvidence,
        req.experimentEvidenceArtifactId,
    )
    source_artifacts = {
        "researchDossier": dossier_artifact.id,
        "experimentEvidence": evidence_artifact.id,
    }

    execution_assessment = None
    try:
        assessment_artifact, execution_assessment = _load_run_contract_artifact(
            run_id,
            "execution_assessment.json",
            ExecutionAssessment,
            req.executionAssessmentArtifactId,
        )
        source_artifacts["executionAssessment"] = assessment_artifact.id
    except HTTPException as exc:
        missing_optional = (
            req.executionAssessmentArtifactId is None
            and exc.status_code == 422
            and isinstance(exc.detail, dict)
            and exc.detail.get("requiredFilename") == "execution_assessment.json"
        )
        if not missing_optional:
            raise

    lineage = _faros_run_lineage(run_id)
    previous_experiment = None
    previous_artifact_run_id = None
    if req.previousExperimentArtifactId:
        previous_artifact, previous_experiment = _load_run_contract_artifact(
            run_id,
            "experiment_evidence.json",
            ExperimentEvidence,
            req.previousExperimentArtifactId,
            require_platform_run_match=False,
        )
        source_artifacts["previousExperimentEvidence"] = previous_artifact.id
        previous_artifact_run_id = previous_artifact.runId
    else:
        from app.storage.artifact_storage import get_storage as get_artifact_storage

        earlier_evidence = [
            artifact
            for artifact in get_artifact_storage().list_by_run(run_id)
            if Path(artifact.filename).name.lower() == "experiment_evidence.json"
            and artifact.id != evidence_artifact.id
        ]
        if earlier_evidence:
            previous_artifact, previous_experiment = _load_run_contract_artifact(
                run_id,
                "experiment_evidence.json",
                ExperimentEvidence,
                earlier_evidence[0].id,
            )
            source_artifacts["previousExperimentEvidence"] = previous_artifact.id
            previous_artifact_run_id = previous_artifact.runId
        elif lineage and lineage.get("parentRunId"):
            previous_artifact, previous_experiment = _load_run_contract_artifact(
                lineage["parentRunId"],
                "experiment_evidence.json",
                ExperimentEvidence,
            )
            source_artifacts["previousExperimentEvidence"] = previous_artifact.id
            previous_artifact_run_id = previous_artifact.runId

    same_research_series = bool(
        previous_artifact_run_id
        and _faros_runs_share_series(run_id, previous_artifact_run_id)
    )

    plan_package_id = req.planPackageId or (
        execution_assessment.planPackageId if execution_assessment else None
    )
    response = await run_experiment_feedback_endpoint(
        RunExperimentFeedbackRequest(
            dossier=dossier,
            experimentEvidence=evidence,
            executionAssessment=execution_assessment,
            previousExperimentEvidence=previous_experiment,
            sameResearchSeries=same_research_series,
            planPackageId=plan_package_id,
            applyToPlanPackage=req.applyToPlanPackage,
        )
    )
    if lineage and not plan_package_id:
        response.planFeedback.reason = (
            "This FAROS run has no PlanPackage; ReviewX feedback will be injected "
            "directly into the next iteration run."
        )
    stored = create_experiment_feedback(
        {
            "runId": run_id,
            "runKind": "faros" if lineage else "platform",
            "parentRunId": lineage.get("parentRunId") if lineage else None,
            "researchSeriesId": lineage.get("researchSeriesId") if lineage else run_id,
            "iterationNumber": lineage.get("iterationNumber", 1) if lineage else 1,
            "scientificRunId": dossier.runId,
            "questionId": dossier.questionId,
            "planPackageId": plan_package_id,
            "sourceArtifacts": source_artifacts,
            "qualityAssessment": response.qualityAssessment.model_dump(mode="json"),
            "iterationDecision": response.iterationDecision.model_dump(mode="json"),
            "metricSnapshot": experiment_metric_snapshot(evidence),
            "benchmarkFingerprint": str(
                evidence.dataHashes.get("frozen_benchmark") or ""
            ) or None,
            "planFeedback": response.planFeedback.model_dump(mode="json"),
            "inheritedHumanFeedback": (
                lineage.get("inheritedHumanFeedback") or {}
                if lineage
                else {}
            ),
        }
    )
    return RunStoredExperimentFeedbackResponse(
        **response.model_dump(),
        feedbackId=stored["id"],
        createdAt=stored["createdAt"],
        runId=run_id,
        runKind="faros" if lineage else "platform",
        parentRunId=lineage.get("parentRunId") if lineage else None,
        researchSeriesId=lineage.get("researchSeriesId") if lineage else run_id,
        iterationNumber=lineage.get("iterationNumber", 1) if lineage else 1,
        sourceArtifacts=source_artifacts,
        metricSnapshot=experiment_metric_snapshot(evidence),
        benchmarkFingerprint=str(
            evidence.dataHashes.get("frozen_benchmark") or ""
        ) or None,
        loopPolicy=(
            ExperimentLoopPolicy.model_validate(stored["loopPolicy"])
            if stored.get("loopPolicy")
            else None
        ),
        loopProgress=(
            ExperimentSeriesProgress.model_validate(stored["loopProgress"])
            if stored.get("loopProgress")
            else None
        ),
        humanSignoffs=human_signoff_state(stored),
        humanFeedback=human_feedback_state(stored),
        humanConditionVerifications=human_condition_verification_state(stored),
        sourceArtifactUrls=dict(stored.get("sourceArtifactUrls") or {}),
        closedLoop=_build_review_loop_trace(stored),
        reviewerPolicy=_reviewer_policy(stored),
        publicationReady=publication_ready(stored),
    )


@router.get(
    "/reviewx/experiment-feedback/history",
    summary="List persisted ReviewX experiment feedback iterations",
)
async def list_experiment_feedback_endpoint(
    runId: Optional[str] = None,
    researchSeriesId: Optional[str] = None,
    limit: int = Query(default=20, ge=1, le=100),
):
    records = list_experiment_feedback(
        run_id=runId,
        research_series_id=researchSeriesId,
        limit=limit,
    )
    if researchSeriesId:
        records = _latest_feedback_per_run(records)
    records = [
        {
            **record,
            "humanSignoffs": human_signoff_state(record),
            "humanFeedback": human_feedback_state(record),
            "humanConditionVerifications": human_condition_verification_state(record),
            "sourceArtifactUrls": dict(record.get("sourceArtifactUrls") or {}),
            "closedLoop": _build_review_loop_trace(record),
            "reviewerPolicy": _reviewer_policy(record),
            "publicationReady": publication_ready(record),
        }
        for record in records
    ]
    return {"records": records, "total": len(records)}


@router.get(
    "/reviewx/experiment-feedback/{feedback_id}",
    response_model=RunStoredExperimentFeedbackResponse,
    summary="Get one persisted ReviewX experiment feedback record",
)
async def get_experiment_feedback_endpoint(
    feedback_id: str,
) -> RunStoredExperimentFeedbackResponse:
    record = get_experiment_feedback(feedback_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Experiment feedback '{feedback_id}' not found")
    return _stored_feedback_response(record)


@router.get(
    "/reviewx/experiment-feedback/{feedback_id}/signoffs",
    response_model=HumanSignoffResponse,
    summary="Get hash-bound human signoffs for one ReviewX feedback record",
)
async def get_experiment_signoffs_endpoint(feedback_id: str) -> HumanSignoffResponse:
    record = get_experiment_feedback(feedback_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Experiment feedback '{feedback_id}' not found")
    return HumanSignoffResponse(
        feedbackId=feedback_id,
        humanSignoffs=human_signoff_state(record),
        humanFeedback=human_feedback_state(record),
        humanConditionVerifications=human_condition_verification_state(record),
        publicationReady=publication_ready(record),
    )


@router.get(
    "/reviewx/experiment-feedback/{feedback_id}/signoff-dossier",
    response_model=SignoffDossier,
    summary="Get a source-linked human-readable ReviewX signoff summary",
)
async def get_experiment_signoff_dossier_endpoint(
    feedback_id: str,
    release: Literal["draft", "official"] = "draft",
    response: Response = None,
) -> SignoffDossier:
    record = get_experiment_feedback(feedback_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Experiment feedback '{feedback_id}' not found")
    if response is not None:
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
    try:
        return build_signoff_dossier(record, release=release)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "OFFICIAL_DOSSIER_LOCKED",
                "message": str(exc),
                "nextStep": "Complete all current ReviewX signoffs and resolve every blocker.",
            },
        ) from exc


@router.get(
    "/reviewx/experiment-feedback/{feedback_id}/signoff-dossier.html",
    response_class=HTMLResponse,
    summary="Read or print a draft or officially released ReviewX signoff dossier",
)
async def get_experiment_signoff_dossier_html_endpoint(
    feedback_id: str,
    release: Literal["draft", "official"] = "draft",
) -> HTMLResponse:
    record = get_experiment_feedback(feedback_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Experiment feedback '{feedback_id}' not found")
    try:
        dossier = build_signoff_dossier(record, release=release)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "OFFICIAL_DOSSIER_LOCKED",
                "message": str(exc),
                "nextStep": "Complete all current ReviewX signoffs and resolve every blocker.",
            },
        ) from exc
    return HTMLResponse(
        render_signoff_dossier_html(dossier),
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.put(
    "/reviewx/experiment-feedback/{feedback_id}/signoffs/{stage}",
    response_model=HumanSignoffResponse,
    summary="Record one hash-bound human decision for a ReviewX stage",
)
async def decide_experiment_signoff_endpoint(
    feedback_id: str,
    stage: Literal["plan", "repair", "conclusion"],
    req: HumanSignoffDecisionRequest,
    authorization: Optional[str] = Header(default=None),
) -> HumanSignoffResponse:
    record = get_experiment_feedback(feedback_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Experiment feedback '{feedback_id}' not found")
    try:
        principal = authorize_reviewer(
            stage=stage,
            reviewer_role=req.reviewerRole,
            reviewer_id=req.reviewerId,
            authorization=authorization if isinstance(authorization, str) else None,
            technical_test=record.get("reviewPurpose") == "technical_test",
        )
        signoffs = decide_human_signoff(
            record,
            stage=stage,
            status=req.status,
            reviewer_role=req.reviewerRole,
            reviewer_id=req.reviewerId,
            reviewer_name=req.reviewerName,
            actor_account_id=str(principal.get("actorAccountId") or ""),
            actor_role=str(principal.get("actorRole") or ""),
            auth_assurance=str(principal.get("authAssurance") or principal.get("assurance") or ""),
            acknowledgements=req.acknowledgements,
            require_acknowledgements=True,
            rationale=req.rationale,
            conditions=req.conditions,
            target_sections=req.targetSections,
        )
    except ReviewAuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    updated = update_experiment_feedback(feedback_id, {"humanSignoffs": signoffs})
    return HumanSignoffResponse(
        feedbackId=feedback_id,
        humanSignoffs=human_signoff_state(updated),
        humanFeedback=human_feedback_state(updated),
        humanConditionVerifications=human_condition_verification_state(updated),
        publicationReady=publication_ready(updated),
    )


@router.put(
    "/reviewx/experiment-feedback/{feedback_id}/signoffs",
    response_model=HumanSignoffResponse,
    summary="Approve every currently required ReviewX gate with one accountable reviewer",
)
async def approve_required_experiment_signoffs_endpoint(
    feedback_id: str,
    req: HumanSignoffBatchDecisionRequest,
    authorization: Optional[str] = Header(default=None),
) -> HumanSignoffResponse:
    record = get_experiment_feedback(feedback_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Experiment feedback '{feedback_id}' not found")
    if record.get("enforceReviewerSeparation"):
        raise HTTPException(
            status_code=409,
            detail="This record explicitly requires separate reviewers; approve each stage individually",
        )

    stages = ["plan"]
    if signoff_required(record, "repair"):
        stages.append("repair")
    if str((record.get("iterationDecision") or {}).get("decision") or "") == "accept_results":
        stages.append("conclusion")

    try:
        for stage in stages:
            principal = authorize_reviewer(
                stage=stage,
                reviewer_role=req.reviewerRole,
                reviewer_id=req.reviewerId,
                authorization=authorization if isinstance(authorization, str) else None,
                technical_test=record.get("reviewPurpose") == "technical_test",
            )
            record["humanSignoffs"] = decide_human_signoff(
                record,
                stage=stage,
                status="approved",
                reviewer_role=req.reviewerRole,
                reviewer_id=req.reviewerId,
                reviewer_name=req.reviewerName,
                actor_account_id=str(principal.get("actorAccountId") or ""),
                actor_role=str(principal.get("actorRole") or ""),
                auth_assurance=str(principal.get("authAssurance") or principal.get("assurance") or ""),
                acknowledgements=req.acknowledgementsByStage.get(stage, []),
                require_acknowledgements=True,
                rationale=f"{req.rationale.strip()} [{stage}]",
            )
    except ReviewAuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    updated = update_experiment_feedback(
        feedback_id,
        {
            "humanSignoffs": record["humanSignoffs"],
            "reviewerPolicy": "single_accountable_reviewer",
        },
    )
    return HumanSignoffResponse(
        feedbackId=feedback_id,
        humanSignoffs=human_signoff_state(updated),
        humanFeedback=human_feedback_state(updated),
        humanConditionVerifications=human_condition_verification_state(updated),
        publicationReady=publication_ready(updated),
    )


@router.post(
    "/reviewx/experiment-feedback/{feedback_id}/human-feedback/apply",
    response_model=ApplyHumanFeedbackResponse,
    summary="Apply explicit human change requests to the next experiment contract",
)
async def apply_experiment_human_feedback_endpoint(
    feedback_id: str,
    req: ApplyHumanFeedbackRequest,
) -> ApplyHumanFeedbackResponse:
    try:
        ensure_reviewx_write_access()
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    record = get_experiment_feedback(feedback_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Experiment feedback '{feedback_id}' not found")
    state = human_feedback_state(record)
    if not state["requiresApplication"]:
        raise HTTPException(status_code=409, detail="No human change request is waiting to be applied")
    if state["applied"]:
        return ApplyHumanFeedbackResponse(
            feedbackId=feedback_id,
            feedbackHash=state["feedbackHash"],
            status=str((state["application"] or {}).get("status") or "applied"),
            applied=True,
            reused=True,
            targetSections=state["targetSections"],
            requiredActions=state["requiredActions"],
            planRevision=record.get("planRevision"),
            humanSignoffs=human_signoff_state(record),
            humanFeedback=state,
            humanConditionVerifications=human_condition_verification_state(record),
        )

    package_id = record.get("planPackageId")
    plan_revision = None
    application_status = "queued_for_iteration"
    previous_application = record.get("humanFeedbackApplication") or {}
    previously_applied_ids = set(previous_application.get("appliedDecisionIds") or [])
    new_items = [
        item
        for item in state["items"]
        if item["decisionId"] not in previously_applied_ids
    ]
    incremental_state = {**state, "items": new_items}
    if package_id:
        from app.services.plan_package_service import (
            PlanPackageConflictError,
            PlanPackageNotFoundError,
            get_plan_package_service,
        )

        service = get_plan_package_service()
        try:
            service.add_feedback(
                package_id,
                section_path="humanExperimentFeedback",
                display_label="ReviewX human experiment feedback",
                source_view="reviewx",
                target_sections=state["targetSections"] or None,
                feedback_type="correction",
                comment=human_feedback_comment(incremental_state),
                severity="blocking",
                requested_action="revise",
            )
            package = service.revise(
                package_id,
                generation_mode=req.generationMode,
                target_sections=state["targetSections"] or None,
                reviewer_mode=req.reviewerMode,
            )
        except PlanPackageNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PlanPackageConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        revision = package.revisions[-1] if package.revisions else None
        plan_revision = {
            "revisionId": revision.id if revision else None,
            "changedSections": revision.changedSections if revision else [],
            "generationMode": req.generationMode,
            "source": "human_feedback",
        }
        application_status = "applied_to_plan"

    application = {
        "feedbackHash": state["feedbackHash"],
        "status": application_status,
        "targetSections": state["targetSections"],
        "requiredActions": state["requiredActions"],
        "appliedDecisionIds": [item["decisionId"] for item in state["items"]],
        "appliedAt": datetime.now(UTC).isoformat(),
        "planPackageId": package_id,
    }
    updates: Dict[str, Any] = {"humanFeedbackApplication": application}
    if plan_revision is not None:
        updates["planRevision"] = plan_revision
    updated = update_experiment_feedback(feedback_id, updates)
    updated_state = human_feedback_state(updated)
    return ApplyHumanFeedbackResponse(
        feedbackId=feedback_id,
        feedbackHash=updated_state["feedbackHash"],
        status=application_status,
        applied=updated_state["applied"],
        targetSections=updated_state["targetSections"],
        requiredActions=updated_state["requiredActions"],
        planRevision=plan_revision,
        humanSignoffs=human_signoff_state(updated),
        humanFeedback=updated_state,
        humanConditionVerifications=human_condition_verification_state(updated),
    )


@router.put(
    "/reviewx/experiment-feedback/{feedback_id}/human-feedback/conditions/{condition_id}",
    response_model=HumanConditionVerificationResponse,
    summary="Verify one inherited human acceptance condition against run artifacts",
)
async def decide_human_condition_verification_endpoint(
    feedback_id: str,
    condition_id: str,
    req: HumanConditionVerificationRequest,
    authorization: Optional[str] = Header(default=None),
) -> HumanConditionVerificationResponse:
    record = get_experiment_feedback(feedback_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Experiment feedback '{feedback_id}' not found")
    try:
        principal = authorize_reviewer(
            stage="condition",
            reviewer_role=req.verifierRole,
            reviewer_id=req.verifierId,
            authorization=authorization if isinstance(authorization, str) else None,
            technical_test=record.get("reviewPurpose") == "technical_test",
        )
        verifications = decide_human_condition_verification(
            record,
            condition_id=condition_id,
            status=req.status,
            verifier_role=req.verifierRole,
            verifier_id=req.verifierId,
            rationale=req.rationale,
            evidence_artifact_ids=req.evidenceArtifactIds,
            actor_account_id=str(principal.get("actorAccountId") or ""),
            actor_role=str(principal.get("actorRole") or ""),
            auth_assurance=str(principal.get("authAssurance") or principal.get("assurance") or ""),
        )
    except ReviewAuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    updated = update_experiment_feedback(
        feedback_id,
        {"humanFeedbackVerifications": verifications},
    )
    return HumanConditionVerificationResponse(
        feedbackId=feedback_id,
        humanConditionVerifications=human_condition_verification_state(updated),
        humanSignoffs=human_signoff_state(updated),
        publicationReady=publication_ready(updated),
    )


@router.get(
    "/reviewx/experiment-feedback/{feedback_id}/evidence-bundle",
    summary="Download a draft or human-approved ReviewX evidence bundle",
)
async def get_experiment_evidence_bundle_endpoint(
    feedback_id: str,
    release: Literal["draft", "official"] = "draft",
    response: Response = None,
):
    record = get_experiment_feedback(feedback_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Experiment feedback '{feedback_id}' not found")
    if release == "official" and not publication_ready(record):
        raise HTTPException(
            status_code=409,
            detail="Official evidence bundles require an approved, current conclusion signoff and no ReviewX blockers",
        )
    if response is not None:
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Disposition"] = (
            f'attachment; filename="reviewx-{feedback_id}-{release}-evidence.json"'
        )
    signoffs = human_signoff_state(record)
    return {
        "schemaVersion": "reviewx-human-approved-evidence/v1",
        "release": release,
        "watermark": None if release == "official" else "DRAFT_NOT_HUMAN_APPROVED",
        "feedbackId": feedback_id,
        "runId": record.get("runId"),
        "researchSeriesId": record.get("researchSeriesId"),
        "questionId": record.get("questionId"),
        "sourceArtifacts": record.get("sourceArtifacts") or {},
        "sourceArtifactUrls": record.get("sourceArtifactUrls") or {},
        "benchmarkFingerprint": record.get("benchmarkFingerprint"),
        "metricSnapshot": record.get("metricSnapshot") or [],
        "qualityAssessment": record.get("qualityAssessment") or {},
        "iterationDecision": record.get("iterationDecision") or {},
        "humanSignoffs": signoffs,
        "humanFeedback": human_feedback_state(record),
        "humanConditionVerifications": human_condition_verification_state(record),
        "auditIntegrity": record_audit_integrity(record),
        "publicationReady": publication_ready(record),
        "generatedAt": datetime.now(UTC).isoformat(),
    }


def _latest_feedback_per_run(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep the latest audit per immutable FAROS run while preserving order."""

    seen: set[str] = set()
    unique: List[Dict[str, Any]] = []
    for record in records:
        run_id = str(record.get("runId") or record.get("id") or "")
        if run_id in seen:
            continue
        seen.add(run_id)
        unique.append(record)
    return unique


def _backfill_feedback_metric_snapshots(
    records: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Upgrade feedback written before metric snapshots became persistent."""

    upgraded: List[Dict[str, Any]] = []
    for record in records:
        if (
            record.get("metricSnapshot")
            and record.get("benchmarkFingerprint")
        ) or not record.get("runId"):
            upgraded.append(record)
            continue
        try:
            _artifact, evidence = _load_run_contract_artifact(
                str(record["runId"]),
                "experiment_evidence.json",
                ExperimentEvidence,
            )
        except HTTPException:
            upgraded.append(record)
            continue
        updates: Dict[str, Any] = {}
        if not record.get("metricSnapshot"):
            snapshot = experiment_metric_snapshot(evidence)
            if snapshot:
                updates["metricSnapshot"] = snapshot
        if not record.get("benchmarkFingerprint"):
            fingerprint = str(evidence.dataHashes.get("frozen_benchmark") or "")
            if fingerprint:
                updates["benchmarkFingerprint"] = fingerprint
        if updates:
            record = update_experiment_feedback(record["id"], updates)
        upgraded.append(record)
    return upgraded


@router.post(
    "/reviewx/experiment-series/{research_series_id}/progress",
    response_model=ExperimentSeriesProgress,
    summary="Evaluate controlled progress across a ReviewX experiment series",
)
async def evaluate_experiment_series_endpoint(
    research_series_id: str,
    policy: ExperimentLoopPolicy,
) -> ExperimentSeriesProgress:
    try:
        ensure_reviewx_write_access()
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    records = list_experiment_feedback(
        research_series_id=research_series_id,
        limit=100,
    )
    records = _latest_feedback_per_run(records)
    records = _backfill_feedback_metric_snapshots(records)
    return evaluate_experiment_series(research_series_id, records, policy)


@router.post(
    "/reviewx/runs/{run_id}/experiment-loop/advance",
    response_model=AdvanceExperimentLoopResponse,
    summary="Audit a completed FAROS round and create its next controlled iteration",
)
async def advance_experiment_loop_endpoint(
    run_id: str,
    req: AdvanceExperimentLoopRequest,
) -> AdvanceExperimentLoopResponse:
    try:
        ensure_reviewx_write_access()
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    lineage = _faros_run_lineage(run_id)
    if lineage is None:
        raise HTTPException(status_code=409, detail="Controlled loop advance currently requires a FAROS run")

    existing = list_experiment_feedback(run_id=run_id, limit=1)
    if existing:
        feedback_record = existing[0]
    else:
        feedback = await run_stored_experiment_feedback_endpoint(
            run_id,
            RunStoredExperimentFeedbackRequest(
                applyToPlanPackage=req.applyToPlanPackage,
            ),
        )
        feedback_record = get_experiment_feedback(feedback.feedbackId) or {}

    series_id = str(lineage.get("researchSeriesId") or run_id)
    records = list_experiment_feedback(research_series_id=series_id, limit=100)
    records = _latest_feedback_per_run(records)
    records = _backfill_feedback_metric_snapshots(records)
    feedback_record = next(
        (record for record in records if record.get("id") == feedback_record.get("id")),
        feedback_record,
    )
    progress = evaluate_experiment_series(series_id, records, req.policy)
    update_experiment_feedback(
        str(feedback_record["id"]),
        {
            "loopPolicy": req.policy.model_dump(mode="json"),
            "loopProgress": progress.model_dump(mode="json"),
        },
    )
    if progress.status != "continue":
        return AdvanceExperimentLoopResponse(
            feedbackId=str(feedback_record["id"]),
            currentRunId=run_id,
            progress=progress,
        )

    try:
        _require_iteration_signoffs(feedback_record)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    controller = iteration_controller_feedback(req.policy, progress)
    decision = dict(feedback_record.get("iterationDecision") or {})
    next_actions = list(decision.get("nextActions") or [])
    if controller["nextAction"] not in next_actions:
        next_actions.append(controller["nextAction"])
    decision["nextActions"] = next_actions
    decision["optimizationPolicy"] = controller["policy"]
    decision["guardrailViolations"] = controller["guardrailViolations"]
    decision["referenceRunId"] = controller["referenceRunId"]
    decision = iteration_decision_with_human_feedback(feedback_record, decision)
    update_experiment_feedback(
        str(feedback_record["id"]),
        {"controllerFeedback": controller, "controllerDecision": decision},
    )

    from app.faros.errors import FarosBlockedError, FarosNotFoundError
    from app.faros.runtime.orchestrator import get_orchestrator

    try:
        next_run, _reused = get_orchestrator().create_iteration_run(
            run_id,
            str(feedback_record["id"]),
            decision,
        )
    except FarosNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FarosBlockedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    update_experiment_feedback(str(feedback_record["id"]), {"nextRunId": next_run["id"]})
    return AdvanceExperimentLoopResponse(
        feedbackId=str(feedback_record["id"]),
        currentRunId=run_id,
        nextRunId=next_run["id"],
        progress=progress,
    )


@router.post(
    "/reviewx/experiment-feedback/{feedback_id}/revise-plan",
    response_model=ReviseExperimentPlanResponse,
    summary="Revise the PlanPackage targeted by an experiment feedback decision",
)
async def revise_experiment_plan_endpoint(
    feedback_id: str,
    req: ReviseExperimentPlanRequest,
) -> ReviseExperimentPlanResponse:
    try:
        ensure_reviewx_write_access()
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    record = get_experiment_feedback(feedback_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Experiment feedback '{feedback_id}' not found")
    package_id = record.get("planPackageId")
    if not package_id:
        raise HTTPException(status_code=409, detail="The feedback record is not linked to a PlanPackage")
    if not record.get("planFeedback", {}).get("applied"):
        raise HTTPException(
            status_code=409,
            detail="Attach the ReviewX correction to the PlanPackage before revising it",
        )
    try:
        human_feedback = require_human_feedback_applied(record)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    from app.services.plan_package_service import (
        PlanPackageConflictError,
        PlanPackageNotFoundError,
        get_plan_package_service,
    )

    target_sections = list(dict.fromkeys([
        *(record.get("iterationDecision", {}).get("targetSections") or []),
        *(human_feedback.get("targetSections") or []),
    ])) or None
    try:
        package = get_plan_package_service().revise(
            package_id,
            generation_mode=req.generationMode,
            target_sections=target_sections,
            reviewer_mode=req.reviewerMode,
        )
    except PlanPackageNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PlanPackageConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    revision = package.revisions[-1] if package.revisions else None
    revision_summary = {
        "revisionId": revision.id if revision else None,
        "changedSections": revision.changedSections if revision else [],
        "generationMode": req.generationMode,
    }
    update_experiment_feedback(feedback_id, {"planRevision": revision_summary})
    return ReviseExperimentPlanResponse(
        feedbackId=feedback_id,
        packageId=package.packageId,
        status=package.status.value,
        revisionId=revision_summary["revisionId"],
        changedSections=revision_summary["changedSections"],
    )


@router.post(
    "/reviewx/experiment-feedback/{feedback_id}/next-run",
    response_model=CreateNextExperimentRunResponse,
    summary="Create the next experiment run from reviewed or revised plan state",
)
async def create_next_experiment_run_endpoint(
    feedback_id: str,
) -> CreateNextExperimentRunResponse:
    try:
        ensure_reviewx_write_access()
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    record = get_experiment_feedback(feedback_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Experiment feedback '{feedback_id}' not found")
    decision = record.get("iterationDecision", {}).get("decision")
    try:
        _require_iteration_signoffs(record)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    run_kind = record.get("runKind") or (
        "faros" if str(record.get("runId") or "").startswith("faros_") else "platform"
    )
    package_id = record.get("planPackageId")
    if run_kind == "faros":
        from app.faros.errors import FarosBlockedError, FarosNotFoundError
        from app.faros.runtime.orchestrator import get_orchestrator

        orchestrator = get_orchestrator()
        existing_next_run_id = record.get("nextRunId")
        if existing_next_run_id:
            existing = orchestrator.get_run(existing_next_run_id)
            if existing is not None:
                return CreateNextExperimentRunResponse(
                    feedbackId=feedback_id,
                    runId=existing["id"],
                    planPackageId=package_id,
                    status=existing["status"],
                    reused=True,
                    runKind="faros",
                    researchSeriesId=existing.get("research_series_id"),
                    iterationNumber=int(existing.get("iteration_number") or 1),
                )
        try:
            iteration_decision = iteration_decision_with_human_feedback(
                record,
                record.get("iterationDecision") or {},
            )
            next_run, reused = orchestrator.create_iteration_run(
                record["runId"],
                feedback_id,
                iteration_decision,
            )
        except FarosNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FarosBlockedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        update_experiment_feedback(
            feedback_id,
            {
                "nextRunId": next_run["id"],
                "nextIterationNumber": int(next_run.get("iteration_number") or 1),
            },
        )
        return CreateNextExperimentRunResponse(
            feedbackId=feedback_id,
            runId=next_run["id"],
            planPackageId=package_id,
            status=next_run["status"],
            reused=reused,
            runKind="faros",
            researchSeriesId=next_run.get("research_series_id"),
            iterationNumber=int(next_run.get("iteration_number") or 1),
        )

    if not package_id:
        raise HTTPException(status_code=409, detail="The feedback record is not linked to a PlanPackage")
    if decision == "revise_plan" and not record.get("planRevision"):
        raise HTTPException(status_code=409, detail="Revise the PlanPackage before creating the next run")

    from app.models.run import RunType
    from app.schemas.run import RunCreate
    from app.services.run_service import get_service as get_run_service

    run_service = get_run_service()
    existing_next_run_id = record.get("nextRunId")
    if existing_next_run_id:
        existing = run_service.get_run(existing_next_run_id)
        if existing is not None:
            return CreateNextExperimentRunResponse(
                feedbackId=feedback_id,
                runId=existing.id,
                planPackageId=package_id,
                status=existing.status.value,
                reused=True,
            )

    source_run = run_service.get_run(record["runId"])
    if source_run is None:
        raise HTTPException(status_code=404, detail=f"Source run '{record['runId']}' not found")
    iteration_number = len(list_experiment_feedback(run_id=record["runId"], limit=100)) + 1
    note = f"ReviewX next iteration from {feedback_id}"
    ideas = f"{source_run.config.ideas}\n{note}" if source_run.config.ideas else note
    next_config = source_run.config.model_copy(
        update={
            "ideas": ideas,
            "workplaceName": f"{source_run.config.workplaceName}_iter_{iteration_number}",
        }
    )
    try:
        next_run = run_service.create_run(
            RunCreate(
                planId=package_id,
                type=RunType.PLAN,
                config=next_config,
                isMock=source_run.isMock,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    update_experiment_feedback(feedback_id, {"nextRunId": next_run.id})
    return CreateNextExperimentRunResponse(
        feedbackId=feedback_id,
        runId=next_run.id,
        planPackageId=package_id,
        status=next_run.status.value,
        runKind="platform",
    )


@router.get("/reviewx/{review_id}")
async def get_reviewx_endpoint(review_id: str):
    review = _get_review(review_id)
    if not review:
        raise HTTPException(status_code=404, detail=f"Review '{review_id}' not found")
    if review.get("reviewKind") != "reviewx":
        raise HTTPException(status_code=400, detail="Review is not a ReviewX run")
    return review


@router.get("/reviewx/{review_id}/findings")
async def get_reviewx_findings_endpoint(review_id: str):
    review = _get_review(review_id)
    if not review:
        raise HTTPException(status_code=404, detail=f"Review '{review_id}' not found")
    if review.get("reviewKind") != "reviewx":
        raise HTTPException(status_code=400, detail="Review is not a ReviewX run")
    return [_reviewx_finding_to_dto(finding, review) for finding in review.get("findings", [])]


@router.get("/{review_id}")
async def get_review_endpoint(review_id: str):
    record = _get_review(review_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Review '{review_id}' not found")
    return record


@router.post("/{review_id}/apply")
async def apply_feedback_endpoint(review_id: str, req: ApplyFeedbackRequest):
    """Apply selected action items as improvement requests for target modules."""
    review = _get_review(review_id)
    if not review:
        raise HTTPException(status_code=404, detail=f"Review '{review_id}' not found")
    if review.get("status") != "completed":
        raise HTTPException(status_code=400, detail="Review not completed yet")

    action_items = review.get("actionItems", [])
    if not action_items:
        raise HTTPException(status_code=400, detail="No action items in review")

    existing_requests = _list_improvement_requests(review_id=review_id)
    existing_action_indices = {
        req.get("actionItemIndex")
        for req in existing_requests
        if req.get("actionItemIndex") is not None
    }
    existing_finding_ids = {
        req.get("sourceFindingId")
        for req in existing_requests
        if req.get("sourceFindingId")
    }
    created = []
    skipped = []
    for idx in req.actionItemIndices:
        if idx < 0 or idx >= len(action_items):
            continue
        item = action_items[idx]
        if idx in existing_action_indices or item.get("sourceFindingId") in existing_finding_ids:
            skipped.append(idx)
            continue
        ir = create_improvement_request({
            "reviewId": review_id,
            "paperId": review.get("paperId"),
            "targetModule": item.get("targetModule", "papers"),
            "actionItemIndex": idx,
            "description": item.get("description", ""),
            "severity": item.get("severity", "MAJOR"),
            "sectionPointer": item.get("section", ""),
            "suggestedEdit": item.get("suggestedEdit", ""),
            "sourceFindingId": item.get("sourceFindingId"),
            "claimId": item.get("claimId"),
            "evidenceIds": item.get("evidenceIds", []),
            "riskType": item.get("riskType"),
            "confidence": item.get("confidence"),
            "supportStatus": item.get("supportStatus"),
            "verifierIds": item.get("verifierIds", []),
            "reviewerDecision": item.get("reviewerDecision"),
            "reviewerAssessment": item.get("reviewerAssessment"),
            "reviewerModel": item.get("reviewerModel"),
            "cemCalibration": item.get("cemCalibration", {}),
            "revisionRequestIds": item.get("revisionRequestIds", []),
            "revisionStatus": item.get("revisionStatus"),
            "acceptanceCriteria": item.get("acceptanceCriteria", []),
        })
        created.append(ir)
        existing_action_indices.add(idx)
        if item.get("sourceFindingId"):
            existing_finding_ids.add(item.get("sourceFindingId"))

    return {
        "reviewId": review_id,
        "appliedCount": len(created),
        "skippedCount": len(skipped),
        "requests": created,
    }
