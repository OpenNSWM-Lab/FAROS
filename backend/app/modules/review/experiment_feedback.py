"""Deterministic ReviewX gate for experiment-driven research iterations."""

from __future__ import annotations

import hashlib
import math
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.contracts import (
    ExecutionAssessment,
    ExecutionStatus,
    ExperimentEvidence,
    GateStatus,
    QualityAssessment,
    QualityFinding,
    ResearchDossier,
    Severity,
    TargetModule,
)


IterationDecision = Literal[
    "accept_results",
    "revise_plan",
    "rerun_experiment",
    "needs_human",
]


class MetricDelta(BaseModel):
    name: str
    previous: float
    current: float
    delta: float
    relativeChange: Optional[float] = None


class ExperimentIterationDecision(BaseModel):
    decision: IterationDecision
    rationale: str
    targetSections: List[str] = Field(default_factory=list)
    metricDeltas: List[MetricDelta] = Field(default_factory=list)
    nextActions: List[str] = Field(default_factory=list)
    feedbackComment: str = ""


class ExperimentFeedbackResult(BaseModel):
    qualityAssessment: QualityAssessment
    iterationDecision: ExperimentIterationDecision


def review_experiment_feedback(
    dossier: ResearchDossier,
    experiment: ExperimentEvidence,
    *,
    execution_assessment: Optional[ExecutionAssessment] = None,
    previous_experiment: Optional[ExperimentEvidence] = None,
) -> ExperimentFeedbackResult:
    """Audit one experiment round and decide how the research plan should proceed.

    The gate is intentionally deterministic. It verifies provenance and plan-to-
    result alignment before any optional LLM semantic review is considered.
    """

    findings: List[QualityFinding] = []
    rule_trace: List[Dict[str, Any]] = []
    artifact_ids = [ref.id for ref in experiment.artifactRefs]

    def check(rule: str, passed: bool, detail: str = "") -> None:
        rule_trace.append({"rule": rule, "status": "pass" if passed else "fail", "detail": detail})

    def add_finding(
        code: str,
        severity: Severity,
        target: TargetModule,
        field_path: str,
        description: str,
        suggested_fix: str,
    ) -> None:
        digest = hashlib.sha256(
            f"{experiment.runId}|{experiment.codeRunId}|{code}|{field_path}".encode("utf-8")
        ).hexdigest()[:12]
        findings.append(
            QualityFinding(
                id=f"expfb_{digest}",
                code=code,
                severity=severity,
                targetModule=target,
                fieldPath=field_path,
                evidenceIds=artifact_ids,
                description=description,
                suggestedFix=suggested_fix,
            )
        )

    ids_match = experiment.runId == dossier.runId and experiment.questionId == dossier.questionId
    check("run_and_question_ids_match", ids_match)
    if not ids_match:
        add_finding(
            "EXPERIMENT_ID_MISMATCH",
            Severity.BLOCKER,
            TargetModule.PLATFORM,
            "runId",
            "Experiment evidence does not belong to the reviewed research dossier.",
            "Select artifacts from the same run and question before continuing the iteration.",
        )

    if execution_assessment is not None:
        assessment_matches = (
            execution_assessment.runId == dossier.runId
            and execution_assessment.questionId == dossier.questionId
        )
        check("execution_assessment_ids_match", assessment_matches)
        if not assessment_matches:
            add_finding(
                "EXECUTION_ASSESSMENT_ID_MISMATCH",
                Severity.BLOCKER,
                TargetModule.PLATFORM,
                "executionAssessment.runId",
                "Execution assessment and research dossier refer to different runs.",
                "Regenerate the execution assessment for this research run.",
            )

    executed = experiment.status == ExecutionStatus.EXECUTED
    check("experiment_executed", executed, experiment.status.value)
    if experiment.status == ExecutionStatus.FAILED:
        add_finding(
            "EXPERIMENT_FAILED",
            Severity.BLOCKER,
            TargetModule.CODE,
            "experimentEvidence.status",
            "The experiment failed and cannot support a scientific iteration decision.",
            "Inspect the recorded failure and logs, repair the execution, and rerun with the same frozen inputs.",
        )
    elif not executed:
        add_finding(
            "EXPERIMENT_NOT_EXECUTED",
            Severity.MAJOR,
            TargetModule.CODE,
            "experimentEvidence.status",
            "No completed experiment result is available for feedback iteration.",
            "Run the approved experiment or explicitly stop with a documented execution constraint.",
        )

    design_checks = {
        "method_present": bool(experiment.method.strip()),
        "baseline_present": bool(experiment.baseline.strip()),
        "data_provenance_present": bool(experiment.dataHashes),
        "logs_present": bool(experiment.logRefs),
        "reproducibility_hashes_present": bool(experiment.codeHash and experiment.environmentHash),
    }
    for rule, passed in design_checks.items():
        check(rule, passed)

    if not design_checks["method_present"]:
        add_finding(
            "EXPERIMENT_METHOD_MISSING",
            Severity.MAJOR,
            TargetModule.CODE,
            "experimentEvidence.method",
            "The executed result does not state the experimental method.",
            "Record the procedure, fixed settings, and comparison protocol used by this run.",
        )
    if not design_checks["baseline_present"]:
        add_finding(
            "EXPERIMENT_BASELINE_MISSING",
            Severity.MAJOR,
            TargetModule.IDEA,
            "researchPlan.steps",
            "The result has no baseline, so the observed metric cannot test the hypothesis comparatively.",
            "Revise the plan to define a baseline or control condition before rerunning.",
        )
    if not design_checks["data_provenance_present"]:
        add_finding(
            "EXPERIMENT_DATA_PROVENANCE_MISSING",
            Severity.MAJOR,
            TargetModule.CODE,
            "experimentEvidence.dataHashes",
            "Input data are not content-addressed, so the result is not reproducible.",
            "Record dataset identifiers and hashes for every material input.",
        )
    if not design_checks["logs_present"]:
        add_finding(
            "EXPERIMENT_LOGS_MISSING",
            Severity.MINOR,
            TargetModule.CODE,
            "experimentEvidence.logRefs",
            "The experiment has no linked execution log.",
            "Attach stdout, stderr, or a structured run log to the experiment evidence.",
        )

    observed = {metric.name: metric for metric in experiment.metrics}
    expected = _expected_metric_names(dossier, execution_assessment)
    check("metrics_present", bool(observed), f"observed={sorted(observed)}")
    if not observed:
        add_finding(
            "EXPERIMENT_METRICS_MISSING",
            Severity.BLOCKER,
            TargetModule.CODE,
            "experimentEvidence.metrics",
            "The experiment produced no structured metric evidence.",
            "Export metrics with names, definitions, values, splits, and source paths.",
        )

    missing_expected = sorted(expected - set(observed))
    check("expected_metrics_observed", not missing_expected, f"missing={missing_expected}")
    if missing_expected:
        add_finding(
            "EXPECTED_METRICS_MISSING",
            Severity.BLOCKER,
            TargetModule.CODE,
            "experimentEvidence.metrics",
            f"The run omitted plan metrics: {', '.join(missing_expected)}.",
            "Produce the missing metrics or revise the plan to explain why they are no longer valid.",
        )

    for metric in experiment.metrics:
        if not metric.definition.strip():
            add_finding(
                "METRIC_DEFINITION_MISSING",
                Severity.MAJOR,
                TargetModule.CODE,
                f"experimentEvidence.metrics.{metric.name}.definition",
                f"Metric '{metric.name}' has no operational definition.",
                "Define exactly how the metric is computed and interpreted.",
            )
        if not metric.sourcePath.strip():
            add_finding(
                "METRIC_SOURCE_MISSING",
                Severity.MAJOR,
                TargetModule.CODE,
                f"experimentEvidence.metrics.{metric.name}.sourcePath",
                f"Metric '{metric.name}' is not linked to a result artifact.",
                "Link the metric to the exact metrics file, table, or result record.",
            )
        if _numeric(metric.value) and not math.isfinite(float(metric.value)):
            add_finding(
                "METRIC_NON_FINITE",
                Severity.BLOCKER,
                TargetModule.CODE,
                f"experimentEvidence.metrics.{metric.name}.value",
                f"Metric '{metric.name}' is not finite.",
                "Repair the computation and rerun before using this result.",
            )

    if experiment.unsupportedClaims:
        add_finding(
            "EXPERIMENT_UNSUPPORTED_CLAIMS",
            Severity.MAJOR,
            TargetModule.IDEA,
            "hypotheses",
            "The experiment explicitly leaves claims unsupported: "
            + "; ".join(experiment.unsupportedClaims[:3]),
            "Narrow or revise the hypothesis and update the next experiment to discriminate the remaining claims.",
        )

    metric_deltas = _metric_deltas(previous_experiment, experiment)
    if previous_experiment is not None:
        previous_matches = (
            previous_experiment.runId == experiment.runId
            and previous_experiment.questionId == experiment.questionId
        )
        check("previous_experiment_ids_match", previous_matches)
        if not previous_matches:
            add_finding(
                "PREVIOUS_EXPERIMENT_ID_MISMATCH",
                Severity.BLOCKER,
                TargetModule.PLATFORM,
                "previousExperimentEvidence.runId",
                "The comparison round belongs to a different research run.",
                "Compare only iterations of the same run and question.",
            )
        comparable = bool(metric_deltas)
        check("iteration_has_comparable_metrics", comparable)
        if not comparable:
            add_finding(
                "ITERATION_METRICS_NOT_COMPARABLE",
                Severity.MAJOR,
                TargetModule.IDEA,
                "researchPlan.expectedMetrics",
                "The two experiment rounds have no comparable numeric metrics.",
                "Keep at least one metric definition stable across iterations.",
            )
        elif all(abs(delta.delta) <= 1e-12 for delta in metric_deltas):
            add_finding(
                "ITERATION_NO_METRIC_CHANGE",
                Severity.MAJOR,
                TargetModule.IDEA,
                "researchPlan.steps",
                "The new experiment round did not change any comparable metric.",
                "Review whether the intervention changed the plan, code, data, or parameters; revise before another run.",
            )

    scores = _dimension_scores(
        ids_match=ids_match,
        design_checks=design_checks,
        expected_count=len(expected),
        observed_expected_count=len(expected & set(observed)),
        metric_count=len(observed),
        metric_deltas=metric_deltas,
        has_previous=previous_experiment is not None,
    )
    gate_status = _gate_status(findings)
    assessment = QualityAssessment(
        runId=dossier.runId,
        questionId=dossier.questionId,
        gateStatus=gate_status,
        dimensionScores=scores,
        findings=findings,
        ruleTrace=rule_trace,
        llmTrace=[],
        uncertainty=(
            "This gate verifies reproducibility, plan alignment, and iteration evidence; "
            "it does not establish scientific truth or metric directionality."
        ),
        configVersion="reviewx-experiment-feedback/v1",
    )
    decision = _iteration_decision(assessment, experiment, metric_deltas)
    return ExperimentFeedbackResult(qualityAssessment=assessment, iterationDecision=decision)


def _expected_metric_names(
    dossier: ResearchDossier,
    execution_assessment: Optional[ExecutionAssessment],
) -> set[str]:
    names = {
        metric.strip()
        for step in dossier.researchPlan.steps
        for metric in step.metrics
        if metric.strip()
    }
    if execution_assessment is not None:
        names.update(metric.strip() for metric in execution_assessment.validationMetrics if metric.strip())
    return names


def _numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _metric_deltas(
    previous: Optional[ExperimentEvidence],
    current: ExperimentEvidence,
) -> List[MetricDelta]:
    if previous is None:
        return []
    previous_metrics = {metric.name: metric for metric in previous.metrics if _numeric(metric.value)}
    current_metrics = {metric.name: metric for metric in current.metrics if _numeric(metric.value)}
    deltas: List[MetricDelta] = []
    for name in sorted(set(previous_metrics) & set(current_metrics)):
        before = float(previous_metrics[name].value)
        after = float(current_metrics[name].value)
        if not (math.isfinite(before) and math.isfinite(after)):
            continue
        delta = after - before
        relative = None if before == 0 else delta / abs(before)
        deltas.append(
            MetricDelta(
                name=name,
                previous=before,
                current=after,
                delta=round(delta, 12),
                relativeChange=None if relative is None else round(relative, 12),
            )
        )
    return deltas


def _dimension_scores(
    *,
    ids_match: bool,
    design_checks: Dict[str, bool],
    expected_count: int,
    observed_expected_count: int,
    metric_count: int,
    metric_deltas: List[MetricDelta],
    has_previous: bool,
) -> Dict[str, float]:
    reproducibility = sum(bool(value) for value in design_checks.values()) / len(design_checks)
    metric_completeness = (
        observed_expected_count / expected_count if expected_count else (1.0 if metric_count else 0.0)
    )
    iteration_readiness = 1.0 if metric_deltas else (0.5 if not has_previous and metric_count else 0.0)
    return {
        "artifactTraceability": round((1.0 if ids_match else 0.0) * reproducibility, 3),
        "metricCompleteness": round(metric_completeness, 3),
        "planAlignment": round((1.0 if ids_match else 0.0) * metric_completeness, 3),
        "iterationReadiness": round(iteration_readiness, 3),
    }


def _gate_status(findings: List[QualityFinding]) -> GateStatus:
    if any(finding.severity == Severity.BLOCKER for finding in findings):
        return GateStatus.FAIL
    if findings:
        return GateStatus.WARN
    return GateStatus.PASS


def _iteration_decision(
    assessment: QualityAssessment,
    experiment: ExperimentEvidence,
    metric_deltas: List[MetricDelta],
) -> ExperimentIterationDecision:
    codes = {finding.code for finding in assessment.findings}
    if any(code.endswith("ID_MISMATCH") for code in codes):
        decision: IterationDecision = "needs_human"
        rationale = "Run identity is inconsistent, so automatic feedback would target the wrong research state."
    elif experiment.status == ExecutionStatus.FAILED or codes & {
        "EXPERIMENT_FAILED",
        "EXPERIMENT_NOT_EXECUTED",
        "EXPERIMENT_METRICS_MISSING",
        "METRIC_NON_FINITE",
    }:
        decision = "rerun_experiment"
        rationale = "The execution or its primary result artifacts are incomplete and must be repaired before replanning."
    elif assessment.gateStatus != GateStatus.PASS:
        decision = "revise_plan"
        rationale = "The result is traceable enough to diagnose, but the plan or evidence must change before the next run."
    else:
        decision = "accept_results"
        rationale = "The experiment is reproducible, aligned with the plan, and ready for scientific interpretation."

    target_sections = _target_sections(assessment.findings)
    next_actions = [finding.suggestedFix for finding in assessment.findings[:5] if finding.suggestedFix]
    if not next_actions and decision == "accept_results":
        next_actions = ["Record the result and decide whether the research stop conditions have been met."]
    feedback_comment = _feedback_comment(decision, assessment.findings, metric_deltas)
    return ExperimentIterationDecision(
        decision=decision,
        rationale=rationale,
        targetSections=target_sections,
        metricDeltas=metric_deltas,
        nextActions=next_actions,
        feedbackComment=feedback_comment,
    )


def _target_sections(findings: List[QualityFinding]) -> List[str]:
    sections: List[str] = []

    def add(*values: str) -> None:
        for value in values:
            if value not in sections:
                sections.append(value)

    for finding in findings:
        if finding.code in {"EXPERIMENT_BASELINE_MISSING", "EXPERIMENT_DATA_PROVENANCE_MISSING"}:
            add("constants", "stages")
        elif finding.code in {
            "EXPECTED_METRICS_MISSING",
            "EXPERIMENT_METRICS_MISSING",
            "METRIC_DEFINITION_MISSING",
            "METRIC_SOURCE_MISSING",
            "ITERATION_METRICS_NOT_COMPARABLE",
            "ITERATION_NO_METRIC_CHANGE",
        }:
            add("expectedMetrics", "stages")
        elif finding.code == "EXPERIMENT_UNSUPPORTED_CLAIMS":
            add("hypothesis", "stages", "expectedMetrics")
        elif finding.targetModule in {TargetModule.CODE, TargetModule.IDEA}:
            add("stages")
    return sections


def _feedback_comment(
    decision: IterationDecision,
    findings: List[QualityFinding],
    metric_deltas: List[MetricDelta],
) -> str:
    parts = [f"ReviewX experiment decision: {decision}."]
    if metric_deltas:
        delta_text = ", ".join(
            f"{item.name} {item.previous:g}->{item.current:g} (delta {item.delta:+g})"
            for item in metric_deltas[:5]
        )
        parts.append(f"Observed metric changes: {delta_text}.")
    if findings:
        parts.append("Required corrections: " + "; ".join(finding.description for finding in findings[:5]))
    else:
        parts.append("No deterministic reproducibility or plan-alignment blocker was found.")
    return " ".join(parts)
