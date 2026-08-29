"""Series-level policy for controlled FAROS experiment iterations."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


MetricDirection = Literal["maximize", "minimize"]
SeriesStatus = Literal["continue", "completed", "blocked"]


class MetricGuardrail(BaseModel):
    metric: str = Field(min_length=1)
    direction: MetricDirection
    threshold: float


class ExperimentLoopPolicy(BaseModel):
    primaryMetric: str = Field(min_length=1)
    direction: MetricDirection
    minIterations: int = Field(default=3, ge=1, le=20)
    maxIterations: int = Field(default=5, ge=1, le=20)
    minAbsoluteImprovement: float = Field(default=0.0, ge=0.0)
    patience: int = Field(default=2, ge=1, le=10)
    targetValue: Optional[float] = None
    guardrails: List[MetricGuardrail] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_iteration_bounds(self) -> "ExperimentLoopPolicy":
        if self.maxIterations < self.minIterations:
            raise ValueError("maxIterations must be greater than or equal to minIterations")
        guardrail_keys = [_metric_key(item.metric) for item in self.guardrails]
        if len(guardrail_keys) != len(set(guardrail_keys)):
            raise ValueError("guardrail metrics must be unique")
        if _metric_key(self.primaryMetric) in guardrail_keys:
            raise ValueError("primaryMetric must not also be a guardrail metric")
        return self


class GuardrailEvaluation(BaseModel):
    metric: str
    direction: MetricDirection
    threshold: float
    value: Optional[float] = None
    satisfied: bool = False


class ExperimentSeriesRound(BaseModel):
    runId: str
    feedbackId: str
    iterationNumber: int
    value: Optional[float] = None
    delta: Optional[float] = None
    improved: Optional[bool] = None
    gateStatus: Optional[str] = None
    decision: Optional[str] = None
    benchmarkFingerprint: Optional[str] = None
    comparableBenchmark: bool = True
    guardrailsSatisfied: bool = True
    guardrails: List[GuardrailEvaluation] = Field(default_factory=list)


class ExperimentSeriesProgress(BaseModel):
    researchSeriesId: str
    status: SeriesStatus
    stopReason: str
    primaryMetric: str
    direction: MetricDirection
    roundsObserved: int
    currentIteration: int
    bestIteration: Optional[int] = None
    bestValue: Optional[float] = None
    bestFeasibleIteration: Optional[int] = None
    bestFeasibleValue: Optional[float] = None
    consecutiveNoImprovement: int = 0
    guardrailsSatisfied: bool = True
    guardrailViolations: List[GuardrailEvaluation] = Field(default_factory=list)
    availableMetrics: List[str] = Field(default_factory=list)
    rounds: List[ExperimentSeriesRound] = Field(default_factory=list)


def evaluate_experiment_series(
    research_series_id: str,
    records: List[Dict[str, Any]],
    policy: ExperimentLoopPolicy,
) -> ExperimentSeriesProgress:
    """Evaluate a persisted feedback series without inferring metric direction."""

    ordered = sorted(
        records,
        key=lambda item: (
            int(item.get("iterationNumber") or 1),
            str(item.get("createdAt") or ""),
        ),
    )
    available_metrics = sorted({
        str(metric.get("name"))
        for record in ordered
        for metric in record.get("metricSnapshot", [])
        if isinstance(metric, dict) and metric.get("name")
    })
    target_key = _metric_key(policy.primaryMetric)
    reference_fingerprint = next(
        (
            str(record.get("benchmarkFingerprint"))
            for record in reversed(ordered)
            if record.get("benchmarkFingerprint")
        ),
        "",
    )
    rounds: List[ExperimentSeriesRound] = []
    previous_value: Optional[float] = None
    consecutive_no_improvement = 0
    best_value: Optional[float] = None
    best_iteration: Optional[int] = None
    best_feasible_value: Optional[float] = None
    best_feasible_iteration: Optional[int] = None

    for record in ordered:
        iteration = int(record.get("iterationNumber") or 1)
        benchmark_fingerprint = str(record.get("benchmarkFingerprint") or "")
        comparable_benchmark = bool(
            not reference_fingerprint
            or benchmark_fingerprint == reference_fingerprint
        )
        value = _metric_value(record.get("metricSnapshot"), target_key)
        delta = (
            None
            if value is None or previous_value is None or not comparable_benchmark
            else value - previous_value
        )
        improved = None
        if delta is not None:
            signed_delta = delta if policy.direction == "maximize" else -delta
            improved = signed_delta > policy.minAbsoluteImprovement
            consecutive_no_improvement = 0 if improved else consecutive_no_improvement + 1
        if value is not None and comparable_benchmark and (
            best_value is None or _is_better(value, best_value, policy.direction)
        ):
            best_value = value
            best_iteration = iteration
        guardrails = [
            _evaluate_guardrail(record.get("metricSnapshot"), guardrail)
            for guardrail in policy.guardrails
        ]
        guardrails_satisfied = all(item.satisfied for item in guardrails)
        if value is not None and comparable_benchmark and guardrails_satisfied and (
            best_feasible_value is None
            or _is_better(value, best_feasible_value, policy.direction)
        ):
            best_feasible_value = value
            best_feasible_iteration = iteration
        rounds.append(
            ExperimentSeriesRound(
                runId=str(record.get("runId") or ""),
                feedbackId=str(record.get("id") or ""),
                iterationNumber=iteration,
                value=value,
                delta=delta,
                improved=improved,
                gateStatus=(record.get("qualityAssessment") or {}).get("gateStatus"),
                decision=(record.get("iterationDecision") or {}).get("decision"),
                benchmarkFingerprint=benchmark_fingerprint or None,
                comparableBenchmark=comparable_benchmark,
                guardrailsSatisfied=guardrails_satisfied,
                guardrails=guardrails,
            )
        )
        if value is not None and comparable_benchmark:
            previous_value = value

    current_iteration = max((item.iterationNumber for item in rounds), default=0)
    status: SeriesStatus = "continue"
    reason = "minimum_iterations_not_reached"
    latest_decision = rounds[-1].decision if rounds else None
    latest_guardrails = rounds[-1].guardrails if rounds else []
    guardrail_violations = [item for item in latest_guardrails if not item.satisfied]
    guardrails_satisfied = not guardrail_violations

    if not rounds:
        status, reason = "blocked", "no_feedback_rounds"
    elif rounds[-1].value is None:
        status, reason = "blocked", "primary_metric_missing"
    elif latest_decision == "needs_human":
        status, reason = "blocked", "reviewx_requires_human_decision"
    elif current_iteration >= policy.maxIterations:
        reason = (
            "maximum_iterations_reached"
            if guardrails_satisfied
            else "maximum_iterations_reached_with_guardrail_violations"
        )
        status = "completed"
    elif not guardrails_satisfied:
        status, reason = "continue", "guardrail_recovery_required"
    elif policy.targetValue is not None and _target_reached(
        rounds[-1].value, policy.targetValue, policy.direction
    ):
        status, reason = "completed", "target_value_reached"
    elif current_iteration < policy.minIterations:
        status, reason = "continue", "minimum_iterations_not_reached"
    elif consecutive_no_improvement >= policy.patience:
        status, reason = "completed", "no_improvement_patience_exhausted"
    elif latest_decision in {"rerun_experiment", "revise_plan"}:
        status, reason = "continue", f"reviewx_decision_{latest_decision}"
    else:
        status, reason = "continue", "optimization_budget_remaining"

    return ExperimentSeriesProgress(
        researchSeriesId=research_series_id,
        status=status,
        stopReason=reason,
        primaryMetric=policy.primaryMetric,
        direction=policy.direction,
        roundsObserved=len(rounds),
        currentIteration=current_iteration,
        bestIteration=best_iteration,
        bestValue=best_value,
        bestFeasibleIteration=best_feasible_iteration,
        bestFeasibleValue=best_feasible_value,
        consecutiveNoImprovement=consecutive_no_improvement,
        guardrailsSatisfied=guardrails_satisfied,
        guardrailViolations=guardrail_violations,
        availableMetrics=available_metrics,
        rounds=rounds,
    )


def iteration_controller_feedback(
    policy: ExperimentLoopPolicy,
    progress: ExperimentSeriesProgress,
) -> Dict[str, Any]:
    """Build an explicit optimization instruction for the next child run."""

    verb = "increase" if policy.direction == "maximize" else "decrease"
    reference_run_id = next(
        (
            item.runId
            for item in progress.rounds
            if item.iterationNumber == progress.bestFeasibleIteration
        ),
        "",
    )
    best_constraint = ""
    if progress.bestFeasibleValue is not None:
        target = (
            progress.bestFeasibleValue + policy.minAbsoluteImprovement
            if policy.direction == "maximize"
            else progress.bestFeasibleValue - policy.minAbsoluteImprovement
        )
        comparator = "above" if policy.direction == "maximize" else "below"
        best_constraint = (
            f" Surpass the best guardrail-feasible result from iteration "
            f"{progress.bestFeasibleIteration} ({progress.bestFeasibleValue:.12g}) "
            f"with a value {comparator} {target:.12g}."
        )
    guardrail_instruction = ""
    if policy.guardrails:
        constraints = ", ".join(
            f"'{item.metric}' {'>=' if item.direction == 'maximize' else '<='} "
            f"{item.threshold:.12g}"
            for item in policy.guardrails
        )
        violations = ", ".join(item.metric for item in progress.guardrailViolations)
        guardrail_instruction = f" Hard guardrails: {constraints}."
        if violations:
            guardrail_instruction += f" Recover current violations first: {violations}."
    action = (
        f"Run controlled iteration {progress.currentIteration + 1} on the inherited frozen "
        f"benchmark and {verb} '{policy.primaryMetric}' by more than "
        f"{policy.minAbsoluteImprovement:g}; preserve baseline definitions and report ablations."
        f"{guardrail_instruction}{best_constraint}"
    )
    return {
        "primaryMetric": policy.primaryMetric,
        "direction": policy.direction,
        "nextAction": action,
        "stopReason": progress.stopReason,
        "policy": policy.model_dump(mode="json"),
        "guardrailViolations": [
            item.model_dump(mode="json") for item in progress.guardrailViolations
        ],
        "referenceRunId": reference_run_id or None,
    }


def _metric_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _metric_value(snapshot: Any, target_key: str) -> Optional[float]:
    for metric in snapshot or []:
        if not isinstance(metric, dict) or _metric_key(str(metric.get("name") or "")) != target_key:
            continue
        value = metric.get("value")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def _evaluate_guardrail(
    snapshot: Any,
    guardrail: MetricGuardrail,
) -> GuardrailEvaluation:
    value = _metric_value(snapshot, _metric_key(guardrail.metric))
    satisfied = value is not None and (
        value >= guardrail.threshold
        if guardrail.direction == "maximize"
        else value <= guardrail.threshold
    )
    return GuardrailEvaluation(
        metric=guardrail.metric,
        direction=guardrail.direction,
        threshold=guardrail.threshold,
        value=value,
        satisfied=satisfied,
    )


def _is_better(current: float, best: float, direction: MetricDirection) -> bool:
    return current > best if direction == "maximize" else current < best


def _target_reached(current: float, target: float, direction: MetricDirection) -> bool:
    return current >= target if direction == "maximize" else current <= target
