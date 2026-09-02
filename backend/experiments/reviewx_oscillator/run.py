"""Run the preregistered ReviewX adaptive-oscillator experiment.

The simulator and parameter fitter are deterministic for a fixed protocol and
seed list.  Qwen may select and explain one feasible Plan Delta, but it never
sees final-holdout observations and never generates measured values.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import least_squares
from scipy.stats import wilcoxon


SCHEMA = "reviewx-oscillator/v1"
DEFAULT_MODEL = "qwen3.7-plus-2026-05-26"


@dataclass(frozen=True)
class ExperimentDesign:
    candidate_id: str
    excitation_frequencies: tuple[float, ...]
    sample_allocations: tuple[int, ...]
    sampling_strategy: str
    observation_budget: int
    optimizer_max_nfev: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidateId": self.candidate_id,
            "excitationFrequencies": list(self.excitation_frequencies),
            "sampleAllocations": list(self.sample_allocations),
            "samplingStrategy": self.sampling_strategy,
            "observationBudget": self.observation_budget,
            "optimizerMaxNfev": self.optimizer_max_nfev,
        }


def baseline_design(config: dict[str, Any]) -> ExperimentDesign:
    budget = int(config["observationBudget"])
    return ExperimentDesign(
        candidate_id="keep_baseline",
        excitation_frequencies=(0.35,),
        sample_allocations=(budget,),
        sampling_strategy="steady_state_uniform",
        observation_budget=budget,
        optimizer_max_nfev=int(config["optimizerMaxNfev"]),
    )


def candidate_designs(config: dict[str, Any]) -> list[ExperimentDesign]:
    budget = int(config["observationBudget"])
    max_nfev = int(config["optimizerMaxNfev"])
    near_resonance = float(config["candidateNearResonanceFrequency"])
    return [
        baseline_design(config),
        ExperimentDesign(
            candidate_id="near_resonance_uniform",
            excitation_frequencies=(0.35, near_resonance),
            sample_allocations=(budget // 4, budget - budget // 4),
            sampling_strategy="uniform_full_trajectory",
            observation_budget=budget,
            optimizer_max_nfev=max_nfev,
        ),
        ExperimentDesign(
            candidate_id="adaptive_resonance_transient",
            excitation_frequencies=(0.35, near_resonance),
            sample_allocations=(budget // 4, budget - budget // 4),
            sampling_strategy="transient_weighted",
            observation_budget=budget,
            optimizer_max_nfev=max_nfev,
        ),
    ]


def validate_design(design: ExperimentDesign, config: dict[str, Any]) -> None:
    if sum(design.sample_allocations) != design.observation_budget:
        raise ValueError(f"{design.candidate_id}: sample allocations do not match observation budget")
    if design.observation_budget != int(config["observationBudget"]):
        raise ValueError(f"{design.candidate_id}: observation budget changed")
    if design.optimizer_max_nfev != int(config["optimizerMaxNfev"]):
        raise ValueError(f"{design.candidate_id}: optimizer budget changed")
    if len(design.excitation_frequencies) != len(design.sample_allocations):
        raise ValueError(f"{design.candidate_id}: frequency/allocation mismatch")
    if any(not 0.2 <= value <= 2.5 for value in design.excitation_frequencies):
        raise ValueError(f"{design.candidate_id}: infeasible excitation frequency")


def simulate_trajectory(
    omega0: float,
    zeta: float,
    excitation_frequency: float,
    times: Sequence[float],
    *,
    amplitude: float = 1.0,
    initial_state: tuple[float, float] = (0.0, 0.0),
    rtol: float = 1e-9,
    atol: float = 1e-11,
) -> np.ndarray:
    """Solve x'' + 2*zeta*omega0*x' + omega0^2*x = A*sin(w_f*t)."""

    requested = np.asarray(times, dtype=float)
    if requested.ndim != 1 or requested.size == 0 or np.any(requested < 0):
        raise ValueError("times must be a non-empty one-dimensional non-negative sequence")
    order = np.argsort(requested)
    sorted_times = requested[order]

    def rhs(t: float, state: np.ndarray) -> tuple[float, float]:
        x, velocity = state
        forcing = amplitude * math.sin(excitation_frequency * t)
        acceleration = forcing - 2.0 * zeta * omega0 * velocity - omega0**2 * x
        return velocity, acceleration

    solution = solve_ivp(
        rhs,
        (0.0, float(sorted_times[-1])),
        initial_state,
        t_eval=sorted_times,
        rtol=rtol,
        atol=atol,
        method="DOP853",
    )
    if not solution.success or solution.y.shape[1] != sorted_times.size:
        raise RuntimeError(f"ODE solver failed: {solution.message}")
    values = np.empty_like(solution.y[0])
    values[order] = solution.y[0]
    return values


def analytic_free_decay(
    omega0: float,
    zeta: float,
    times: Sequence[float],
    *,
    initial_displacement: float = 1.0,
    initial_velocity: float = 0.0,
) -> np.ndarray:
    """Analytic underdamped, unforced solution used by solver verification."""

    if not 0 <= zeta < 1:
        raise ValueError("analytic_free_decay requires 0 <= zeta < 1")
    t = np.asarray(times, dtype=float)
    omega_d = omega0 * math.sqrt(1.0 - zeta**2)
    coefficient = (initial_velocity + zeta * omega0 * initial_displacement) / omega_d
    return np.exp(-zeta * omega0 * t) * (
        initial_displacement * np.cos(omega_d * t) + coefficient * np.sin(omega_d * t)
    )


def _sample_times(count: int, strategy: str) -> np.ndarray:
    if strategy == "steady_state_uniform":
        return np.linspace(15.0, 30.0, count)
    if strategy == "uniform_full_trajectory":
        return np.linspace(0.15, 30.0, count)
    if strategy == "transient_weighted":
        transient_count = max(3, int(round(count * 0.7)))
        steady_count = count - transient_count
        transient = np.linspace(0.15, 8.0, transient_count, endpoint=False)
        steady = np.linspace(8.0, 30.0, steady_count) if steady_count else np.array([])
        return np.concatenate([transient, steady])
    raise ValueError(f"Unknown sampling strategy: {strategy}")


def _design_observations(
    design: ExperimentDesign,
    *,
    omega0: float,
    zeta: float,
    rng: np.random.Generator,
    noise_std: float,
) -> tuple[list[tuple[float, np.ndarray, np.ndarray]], int]:
    observations = []
    for frequency, count in zip(design.excitation_frequencies, design.sample_allocations):
        times = _sample_times(count, design.sampling_strategy)
        truth = simulate_trajectory(omega0, zeta, frequency, times)
        measured = truth + rng.normal(0.0, noise_std, size=truth.shape)
        observations.append((frequency, times, measured))
    return observations, sum(len(item[1]) for item in observations)


def _fit_parameters(
    observations: list[tuple[float, np.ndarray, np.ndarray]],
    design: ExperimentDesign,
) -> tuple[np.ndarray, Any]:
    def residual(parameters: np.ndarray) -> np.ndarray:
        omega0, zeta = parameters
        return np.concatenate([
            simulate_trajectory(omega0, zeta, frequency, times) - measured
            for frequency, times, measured in observations
        ])

    result = least_squares(
        residual,
        x0=np.array([0.9, 0.20]),
        bounds=(np.array([0.45, 0.015]), np.array([2.2, 0.45])),
        max_nfev=design.optimizer_max_nfev,
        ftol=1e-10,
        xtol=1e-10,
        gtol=1e-10,
        method="trf",
    )
    if not result.success or not np.all(np.isfinite(result.x)):
        raise RuntimeError(f"Parameter fit failed: {result.message}")
    return result.x, result


def evaluate_seed(seed: int, design: ExperimentDesign, config: dict[str, Any]) -> dict[str, Any]:
    validate_design(design, config)
    omega_true = float(config["trueOmega0"])
    zeta_true = float(config["trueZeta"])
    rng = np.random.default_rng(seed)
    observations, observation_count = _design_observations(
        design,
        omega0=omega_true,
        zeta=zeta_true,
        rng=rng,
        noise_std=float(config["noiseStd"]),
    )
    estimate, fit = _fit_parameters(observations, design)
    residuals = np.asarray(fit.fun)
    jacobian_information = np.asarray(fit.jac).T @ np.asarray(fit.jac)
    condition_number = float(np.linalg.cond(jacobian_information))
    if residuals.size > 2 and np.std(residuals[:-1]) > 0 and np.std(residuals[1:]) > 0:
        lag1 = float(np.corrcoef(residuals[:-1], residuals[1:])[0, 1])
    else:
        lag1 = 0.0

    holdout_times = np.linspace(0.1, 24.0, int(config["holdoutTrajectoryPoints"]))
    holdout_errors = []
    for frequency in config["holdoutExcitationFrequencies"]:
        truth = simulate_trajectory(omega_true, zeta_true, float(frequency), holdout_times)
        prediction = simulate_trajectory(float(estimate[0]), float(estimate[1]), float(frequency), holdout_times)
        scale = max(float(np.ptp(truth)), 1e-12)
        holdout_errors.append(float(np.sqrt(np.mean((prediction - truth) ** 2)) / scale))

    return {
        "seed": seed,
        "candidate_id": design.candidate_id,
        "omega0_estimate": float(estimate[0]),
        "zeta_estimate": float(estimate[1]),
        "omega0_relative_error": abs(float(estimate[0]) - omega_true) / omega_true,
        "zeta_relative_error": abs(float(estimate[1]) - zeta_true) / zeta_true,
        "heldout_trajectory_nrmse": float(np.mean(holdout_errors)),
        "residual_lag1_autocorrelation": lag1,
        "fisher_information_condition_number": condition_number,
        "observation_count": observation_count,
        "optimizer_nfev": int(fit.nfev),
        "optimizer_evaluation_limit": design.optimizer_max_nfev,
        "solver_failed": False,
    }


def run_round(seeds: Iterable[int], design: ExperimentDesign, config: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for seed in seeds:
        try:
            rows.append(evaluate_seed(int(seed), design, config))
        except Exception as exc:
            rows.append({
                "seed": int(seed),
                "candidate_id": design.candidate_id,
                "observation_count": design.observation_budget,
                "optimizer_evaluation_limit": design.optimizer_max_nfev,
                "solver_failed": True,
                "failure": f"{type(exc).__name__}: {exc}",
            })
    return rows


def summarize_round(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    successful = [row for row in rows if not row.get("solver_failed")]
    if len(successful) != len(rows):
        failures = [row.get("failure") for row in rows if row.get("solver_failed")]
        raise RuntimeError(f"Solver/fitter failures must not be dropped: {failures}")
    metrics = (
        "heldout_trajectory_nrmse",
        "omega0_relative_error",
        "zeta_relative_error",
        "residual_lag1_autocorrelation",
        "fisher_information_condition_number",
    )
    return {
        "repeats": len(successful),
        "failureRate": 0.0,
        "means": {name: float(np.mean([row[name] for row in successful])) for name in metrics},
        "medians": {name: float(np.median([row[name] for row in successful])) for name in metrics},
        "budget": {
            "observationsPerSeed": sorted({int(row["observation_count"]) for row in successful}),
            "optimizerEvaluationLimit": sorted({int(row["optimizer_evaluation_limit"]) for row in successful}),
            "actualOptimizerEvaluations": int(sum(row["optimizer_nfev"] for row in successful)),
        },
    }


def paired_bootstrap_interval(
    differences: Sequence[float],
    *,
    samples: int,
    seed: int,
) -> tuple[float, float]:
    values = np.asarray(differences, dtype=float)
    if values.size == 0:
        raise ValueError("paired bootstrap requires at least one difference")
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, values.size, size=(samples, values.size))
    means = values[indices].mean(axis=1)
    lower, upper = np.quantile(means, [0.025, 0.975])
    return float(lower), float(upper)


def statistical_gate(
    round_1_values: Sequence[float],
    round_2_values: Sequence[float],
    *,
    bootstrap_samples: int,
    bootstrap_seed: int,
) -> dict[str, Any]:
    first = np.asarray(round_1_values, dtype=float)
    second = np.asarray(round_2_values, dtype=float)
    if first.shape != second.shape or first.size < 2:
        raise ValueError("statistical gate requires paired arrays of at least two repeats")
    differences = second - first
    ci_lower, ci_upper = paired_bootstrap_interval(
        differences,
        samples=bootstrap_samples,
        seed=bootstrap_seed,
    )
    mean_first = float(np.mean(first))
    mean_second = float(np.mean(second))
    relative_improvement = (mean_first - mean_second) / max(abs(mean_first), 1e-12)
    if np.allclose(differences, 0):
        wilcoxon_p = 1.0
    else:
        wilcoxon_p = float(wilcoxon(differences, alternative="less").pvalue)
    rng = np.random.default_rng(bootstrap_seed + 1)
    sign_draws = rng.choice(np.array([-1.0, 1.0]), size=(10000, differences.size))
    permutation_means = (sign_draws * np.abs(differences)).mean(axis=1)
    permutation_p = float((np.sum(permutation_means <= differences.mean()) + 1) / (len(permutation_means) + 1))
    crosses_zero = ci_lower <= 0 <= ci_upper
    if relative_improvement >= 0.15 and ci_upper < 0:
        decision = "UPDATE"
    elif crosses_zero:
        decision = "BOUNDARY"
    elif mean_second >= mean_first:
        decision = "ROLLBACK"
    else:
        decision = "KEEP"
    return {
        "primaryMetric": "heldout_trajectory_nrmse",
        "direction": "minimize",
        "round1Mean": mean_first,
        "round2Mean": mean_second,
        "meanDifferenceRound2MinusRound1": float(np.mean(differences)),
        "relativeImprovement": float(relative_improvement),
        "pairedBootstrap95": [ci_lower, ci_upper],
        "bootstrapSamples": bootstrap_samples,
        "pairedWilcoxonP": wilcoxon_p,
        "pairedSignPermutationP": permutation_p,
        "effectSizeStandardizedMeanDifference": float(
            np.mean(differences) / max(np.std(differences, ddof=1), 1e-12)
        ),
        "ciCrossesZero": crosses_zero,
        "decision": decision,
    }


def _csv_text(rows: Sequence[dict[str, Any]]) -> str:
    if not rows:
        return ""
    fieldnames = sorted({key for row in rows for key in row})
    from io import StringIO

    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _diagnostics(summary: dict[str, Any]) -> dict[str, Any]:
    means = summary["means"]
    return {
        "residualLag1Autocorrelation": means["residual_lag1_autocorrelation"],
        "fisherInformationConditionNumber": means["fisher_information_condition_number"],
        "omega0RelativeError": means["omega0_relative_error"],
        "zetaRelativeError": means["zeta_relative_error"],
        "diagnosis": [
            "single off-resonance excitation limits parameter sensitivity",
            "steady-state-only sampling under-observes the informative transient",
        ],
        "finalHoldoutIncluded": False,
    }


def _candidate_payload(config: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for design in candidate_designs(config):
        validate_design(design, config)
        result.append({
            **design.as_dict(),
            "feasible": True,
            "expectedBenefit": {
                "keep_baseline": "control with no Plan Delta",
                "near_resonance_uniform": "increase response sensitivity while retaining uniform sampling",
                "adaptive_resonance_transient": "improve sensitivity and transient identifiability",
            }[design.candidate_id],
            "risk": "near-resonance excitation may amplify noise or model mismatch",
            "falsificationCondition": "held-out NRMSE CI crosses zero or a parameter guardrail fails",
        })
    return result


def _qwen_select(
    diagnostics: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    provider: str,
    model: str,
    require_real_api: bool,
) -> tuple[str, dict[str, Any], str]:
    deterministic_selection = "adaptive_resonance_transient"
    prompt_payload = {
        "scientificQuestion": "How should excitation and sampling be allocated to identify a damped second-order system under a fixed observation budget?",
        "round1AggregateDiagnostics": diagnostics,
        "constraints": {
            "observationBudgetMustRemainFixed": True,
            "optimizerEvaluationLimitMustRemainFixed": True,
            "selectOnlyFeasibleCandidate": True,
            "finalHoldoutUnavailable": True,
        },
        "candidates": candidates,
    }
    prompt = (
        "Select one feasible candidate for Round 2 using only the aggregate Round 1 diagnostics. "
        "Return JSON with selectedCandidateId, rationale, evidenceReferences, risks, and falsificationConditions. "
        "Do not invent observations or final-holdout metrics.\n"
        + json.dumps(prompt_payload, ensure_ascii=False, sort_keys=True)
    )
    if provider == "deterministic":
        trace = {
            "provider": "deterministic_policy",
            "model": None,
            "isRealApiCall": False,
            "latencyMs": 0,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "prompt": prompt,
            "rawResponse": None,
            "normalizedSelection": deterministic_selection,
            "policyCorrectionRequired": False,
            "finalHoldoutExposedToQwen": False,
        }
        return deterministic_selection, trace, prompt
    if provider != "qwen":
        raise ValueError("provider must be deterministic or qwen")

    from app.llm.provider_client import ChatMessage, get_provider_client

    response = get_provider_client("qwen").chat(
        [
            ChatMessage(role="system", content="You are a constrained scientific experiment-design reviewer. Return JSON only."),
            ChatMessage(role="user", content=prompt),
        ],
        model=model,
        temperature=0,
        max_tokens=1500,
        structured_output=True,
        timeout=90,
        request_max_retries=2,
    )
    raw = response.text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    parsed = json.loads(raw)
    selected = str(parsed.get("selectedCandidateId") or "")
    feasible = {item["candidateId"] for item in candidates if item["feasible"]}
    corrected = selected not in feasible
    if corrected:
        selected = deterministic_selection
    trace = {
        "provider": response.raw_provider,
        "model": response.model,
        "isRealApiCall": True,
        "calledAt": datetime.now(UTC).isoformat(),
        "latencyMs": response.latency_ms,
        "usage": response.usage,
        "finishReason": response.finish_reason,
        "prompt": prompt,
        "promptSha256": _sha256_bytes(prompt.encode("utf-8")),
        "rawResponse": parsed,
        "normalizedSelection": selected,
        "policyCorrectionRequired": corrected,
        "evidenceReferences": parsed.get("evidenceReferences") or [],
        "finalHoldoutExposedToQwen": False,
    }
    if require_real_api and not trace["isRealApiCall"]:
        raise RuntimeError("A real Qwen API call is required")
    return selected, trace, prompt


def _paired_holdout_rows(
    seeds: Sequence[int],
    round_1_design: ExperimentDesign,
    round_2_design: ExperimentDesign,
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    round_1 = run_round(seeds, round_1_design, config)
    round_2 = run_round(seeds, round_2_design, config)
    if any(row.get("solver_failed") for row in [*round_1, *round_2]):
        raise RuntimeError("A final-holdout solver failure occurred; results cannot be released")
    merged = []
    for first, second in zip(round_1, round_2):
        if first["seed"] != second["seed"]:
            raise RuntimeError("Final-holdout pairing was corrupted")
        merged.append({
            "seed": first["seed"],
            "round_1_heldout_trajectory_nrmse": first["heldout_trajectory_nrmse"],
            "round_2_heldout_trajectory_nrmse": second["heldout_trajectory_nrmse"],
            "difference_round_2_minus_round_1": second["heldout_trajectory_nrmse"] - first["heldout_trajectory_nrmse"],
            "round_1_omega0_relative_error": first["omega0_relative_error"],
            "round_2_omega0_relative_error": second["omega0_relative_error"],
            "round_1_zeta_relative_error": first["zeta_relative_error"],
            "round_2_zeta_relative_error": second["zeta_relative_error"],
            "round_1_fisher_condition_number": first["fisher_information_condition_number"],
            "round_2_fisher_condition_number": second["fisher_information_condition_number"],
            "round_1_observation_count": first["observation_count"],
            "round_2_observation_count": second["observation_count"],
            "round_1_optimizer_evaluation_limit": first["optimizer_evaluation_limit"],
            "round_2_optimizer_evaluation_limit": second["optimizer_evaluation_limit"],
        })
    return round_1, round_2, merged


def _guardrails(
    first: Sequence[dict[str, Any]],
    second: Sequence[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    def mean(rows: Sequence[dict[str, Any]], key: str) -> float:
        return float(np.mean([row[key] for row in rows]))

    omega_change = (mean(first, "omega0_relative_error") - mean(second, "omega0_relative_error")) / max(mean(first, "omega0_relative_error"), 1e-12)
    zeta_change = (mean(first, "zeta_relative_error") - mean(second, "zeta_relative_error")) / max(mean(first, "zeta_relative_error"), 1e-12)
    parameter_pass = (
        (omega_change >= 0.20 and zeta_change >= -0.05)
        or (zeta_change >= 0.20 and omega_change >= -0.05)
    )
    budgets_match = all(
        first_row["observation_count"] == second_row["observation_count"] == int(config["observationBudget"])
        and first_row["optimizer_evaluation_limit"] == second_row["optimizer_evaluation_limit"] == int(config["optimizerMaxNfev"])
        for first_row, second_row in zip(first, second)
    )
    return {
        "parameterError": {
            "omega0RelativeImprovement": omega_change,
            "zetaRelativeImprovement": zeta_change,
            "passed": parameter_pass,
        },
        "matchedBudget": {
            "observations": budgets_match,
            "optimizerEvaluationLimit": budgets_match,
            "passed": budgets_match,
        },
        "solverFailureRate": 0.0,
        "passed": parameter_pass and budgets_match,
    }


def execute_protocol(
    config: dict[str, Any],
    output: Path,
    *,
    provider: str,
    model: str = DEFAULT_MODEL,
    require_real_api: bool = False,
) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Refusing to overwrite non-empty output directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(UTC)
    first_design = baseline_design(config)
    for design in candidate_designs(config):
        validate_design(design, config)

    development_seeds = [int(seed) for seed in config["developmentSeeds"]]
    calibration_seeds = [int(seed) for seed in config.get("calibrationSeeds", [])]
    final_seeds = [int(seed) for seed in config["finalHoldoutSeeds"]]
    first_development = run_round(development_seeds, first_design, config)
    first_summary = summarize_round(first_development)
    diagnostics = _diagnostics(first_summary)
    candidates = _candidate_payload(config)
    selected_id, qwen_trace, _ = _qwen_select(
        diagnostics,
        candidates,
        provider=provider,
        model=model,
        require_real_api=require_real_api,
    )
    selected_design = next(design for design in candidate_designs(config) if design.candidate_id == selected_id)
    second_development = run_round(development_seeds, selected_design, config)
    second_summary = summarize_round(second_development)

    # Calibration is completed before the final holdout is touched.  It is
    # intentionally diagnostic: thresholds and budgets already live in the
    # frozen protocol and are never tuned from these outcomes.
    calibration_first, calibration_second, calibration_rows = _paired_holdout_rows(
        calibration_seeds,
        first_design,
        selected_design,
        config,
    )
    calibration_statistics = statistical_gate(
        [row["heldout_trajectory_nrmse"] for row in calibration_first],
        [row["heldout_trajectory_nrmse"] for row in calibration_second],
        bootstrap_samples=int(config["bootstrapSamples"]),
        bootstrap_seed=int(config["bootstrapSeed"]) - 1,
    )
    calibration_guardrails = _guardrails(calibration_first, calibration_second, config)
    calibration_statistics["guardrails"] = calibration_guardrails

    deterministic_design = next(
        design
        for design in candidate_designs(config)
        if design.candidate_id == "adaptive_resonance_transient"
    )
    if deterministic_design.candidate_id == selected_design.candidate_id:
        deterministic_calibration = calibration_second
    else:
        deterministic_calibration = run_round(calibration_seeds, deterministic_design, config)
        summarize_round(deterministic_calibration)

    method_comparison = {
        "schemaVersion": SCHEMA,
        "partition": "calibration",
        "seedCount": len(calibration_seeds),
        "candidateSet": [item["candidateId"] for item in candidates],
        "matchedBudget": {
            "observationBudget": int(config["observationBudget"]),
            "optimizerMaxNfev": int(config["optimizerMaxNfev"]),
            "passed": all(
                row["observation_count"] == int(config["observationBudget"])
                and row["optimizer_evaluation_limit"] == int(config["optimizerMaxNfev"])
                for row in [*deterministic_calibration, *calibration_second]
            ),
        },
        "methods": {
            "deterministic_only": {
                "selectedCandidateId": deterministic_design.candidate_id,
                "meanHeldoutTrajectoryNrmse": float(np.mean([
                    row["heldout_trajectory_nrmse"] for row in deterministic_calibration
                ])),
            },
            "faros_reviewx_qwen": {
                "selectedCandidateId": selected_design.candidate_id,
                "meanHeldoutTrajectoryNrmse": float(np.mean([
                    row["heldout_trajectory_nrmse"] for row in calibration_second
                ])),
                "qwenRealApiCall": bool(qwen_trace["isRealApiCall"]),
            },
        },
        "interpretation": (
            "Qwen and the frozen deterministic policy selected the same feasible design."
            if deterministic_design.candidate_id == selected_design.candidate_id
            else "The two policies selected different feasible designs; both calibration outcomes are retained."
        ),
        "finalHoldoutUsed": False,
    }
    method_comparison_rows = [
        {
            "seed": deterministic_row["seed"],
            "deterministic_only_candidate_id": deterministic_design.candidate_id,
            "deterministic_only_heldout_trajectory_nrmse": deterministic_row["heldout_trajectory_nrmse"],
            "faros_reviewx_qwen_candidate_id": selected_design.candidate_id,
            "faros_reviewx_qwen_heldout_trajectory_nrmse": qwen_row["heldout_trajectory_nrmse"],
            "observation_budget": int(config["observationBudget"]),
            "optimizer_evaluation_limit": int(config["optimizerMaxNfev"]),
        }
        for deterministic_row, qwen_row in zip(deterministic_calibration, calibration_second)
    ]

    plan_delta = {
        "schemaVersion": SCHEMA,
        "selectedCandidateId": selected_id,
        "changedSections": ["excitation", "sampling"],
        "parameterChanges": [
            {
                "field": "excitationFrequencies",
                "oldValue": list(first_design.excitation_frequencies),
                "newValue": list(selected_design.excitation_frequencies),
                "rationale": "Round 1 Fisher-information and parameter-coupling diagnosis",
                "targetNode": "experiment.design",
            },
            {
                "field": "samplingStrategy",
                "oldValue": first_design.sampling_strategy,
                "newValue": selected_design.sampling_strategy,
                "rationale": "Round 1 residual and transient-coverage diagnosis",
                "targetNode": "experiment.sampling",
            },
        ],
        "unchangedHardConstraints": {
            "observationBudget": first_design.observation_budget,
            "optimizerMaxNfev": first_design.optimizer_max_nfev,
        },
        "affectedNodesRerun": ["experiment.design", "experiment.fit", "reviewx.statistics"],
        "finalHoldoutExposedToQwen": False,
        "methodComparison": {
            "artifact": "method_comparison.json",
            "deterministicOnlyCandidateId": deterministic_design.candidate_id,
            "qwenCandidateId": selected_design.candidate_id,
            "matchedBudget": method_comparison["matchedBudget"]["passed"],
        },
    }

    first_holdout, second_holdout, holdout_rows = _paired_holdout_rows(
        final_seeds,
        first_design,
        selected_design,
        config,
    )
    statistics = statistical_gate(
        [row["heldout_trajectory_nrmse"] for row in first_holdout],
        [row["heldout_trajectory_nrmse"] for row in second_holdout],
        bootstrap_samples=int(config["bootstrapSamples"]),
        bootstrap_seed=int(config["bootstrapSeed"]),
    )
    guardrails = _guardrails(first_holdout, second_holdout, config)
    if statistics["decision"] == "UPDATE" and not guardrails["passed"]:
        statistics["decision"] = "KEEP"
        statistics["gateOverride"] = "A hard parameter or budget guardrail failed"
    statistics["guardrails"] = guardrails
    statistics["mechanism"] = {
        "round1MeanFisherConditionNumber": float(np.mean([row["fisher_information_condition_number"] for row in first_holdout])),
        "round2MeanFisherConditionNumber": float(np.mean([row["fisher_information_condition_number"] for row in second_holdout])),
    }

    _write_json(output / "protocol.json", config)
    (output / "round_1" / "per_seed_results.csv").parent.mkdir(parents=True, exist_ok=True)
    (output / "round_1" / "per_seed_results.csv").write_text(_csv_text(first_development), encoding="utf-8")
    _write_json(output / "round_1" / "metrics.json", first_summary)
    _write_json(output / "round_1" / "diagnostics.json", diagnostics)
    (output / "round_2" / "per_seed_results.csv").parent.mkdir(parents=True, exist_ok=True)
    (output / "round_2" / "per_seed_results.csv").write_text(_csv_text(second_development), encoding="utf-8")
    _write_json(output / "round_2" / "metrics.json", second_summary)
    (output / "calibration" / "per_seed_results.csv").parent.mkdir(parents=True, exist_ok=True)
    (output / "calibration" / "per_seed_results.csv").write_text(
        _csv_text(calibration_rows),
        encoding="utf-8",
    )
    _write_json(output / "calibration" / "statistical_summary.json", calibration_statistics)
    _write_json(output / "calibration" / "frozen_gate.json", {
        "thresholdsChangedAfterCalibration": False,
        "primaryGate": config["primaryGate"],
        "parameterGuardrail": config["parameterGuardrail"],
        "observationBudget": config["observationBudget"],
        "optimizerMaxNfev": config["optimizerMaxNfev"],
        "stopCondition": "Stop before final holdout on any solver failure or unmatched hard budget.",
    })
    (output / "calibration" / "method_comparison_per_seed.csv").write_text(
        _csv_text(method_comparison_rows),
        encoding="utf-8",
    )
    _write_json(output / "method_comparison.json", method_comparison)
    _write_json(output / "plan_delta.json", plan_delta)
    _write_json(output / "qwen_trace.json", qwen_trace)
    (output / "final_holdout" / "per_seed_results.csv").parent.mkdir(parents=True, exist_ok=True)
    (output / "final_holdout" / "per_seed_results.csv").write_text(_csv_text(holdout_rows), encoding="utf-8")
    _write_json(output / "statistical_summary.json", statistics)
    _write_json(output / "human_signoff.json", {
        "status": "pending",
        "approvedBy": None,
        "approvedAt": None,
        "note": "A development agent must not approve this record.",
    })

    script_path = Path(__file__).resolve()
    manifest = {
        "schemaVersion": SCHEMA,
        "startedAt": started_at.isoformat(),
        "completedAt": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": __import__("scipy").__version__,
        "codeHash": _sha256_file(script_path),
        "protocolHash": _sha256_file(output / "protocol.json"),
        "developmentSeeds": development_seeds,
        "calibrationSeeds": calibration_seeds,
        "retiredEngineeringPreflightSeeds": [
            int(seed) for seed in config.get("retiredEngineeringPreflightSeeds", [])
        ],
        "finalHoldoutSeeds": final_seeds,
        "selectedCandidateId": selected_id,
        "qwenRealApiCall": bool(qwen_trace["isRealApiCall"]),
        "finalHoldoutExposedToQwen": False,
        "thresholdsChangedAfterCalibration": False,
        "methodComparisonArtifact": "method_comparison.json",
        "budgets": {
            "round1ObservationBudget": first_design.observation_budget,
            "round2ObservationBudget": selected_design.observation_budget,
            "round1OptimizerMaxNfev": first_design.optimizer_max_nfev,
            "round2OptimizerMaxNfev": selected_design.optimizer_max_nfev,
        },
    }
    _write_json(output / "manifest.json", manifest)
    (output / "README.md").write_text(
        "# ReviewX adaptive oscillator run\n\n"
        f"Decision: `{statistics['decision']}`. Human signoff remains `pending`.\n\n"
        "Recompute metrics from `final_holdout/per_seed_results.csv`; the final holdout was not present in the Qwen prompt.\n",
        encoding="utf-8",
    )

    artifact_files = sorted(path for path in output.rglob("*") if path.is_file() and path.name != "CHECKSUMS.sha256")
    checksum_lines = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(output).as_posix()}"
        for path in artifact_files
    ]
    (output / "CHECKSUMS.sha256").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
    return {"manifest": manifest, "statistics": statistics, "planDelta": plan_delta}


def recompute_and_validate_output(output: Path) -> dict[str, Any]:
    required = [
        output / "round_1" / "per_seed_results.csv",
        output / "round_1" / "metrics.json",
        output / "round_2" / "per_seed_results.csv",
        output / "round_2" / "metrics.json",
        output / "final_holdout" / "per_seed_results.csv",
        output / "statistical_summary.json",
    ]
    missing = [str(path.relative_to(output)) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Evidence construction failed; missing artifacts: {missing}")
    with (output / "final_holdout" / "per_seed_results.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    stored = json.loads((output / "statistical_summary.json").read_text(encoding="utf-8"))
    recomputed = statistical_gate(
        [float(row["round_1_heldout_trajectory_nrmse"]) for row in rows],
        [float(row["round_2_heldout_trajectory_nrmse"]) for row in rows],
        bootstrap_samples=int(stored["bootstrapSamples"]),
        bootstrap_seed=int(json.loads((output / "protocol.json").read_text(encoding="utf-8"))["bootstrapSeed"]),
    )
    for key in ("round1Mean", "round2Mean", "meanDifferenceRound2MinusRound1", "relativeImprovement"):
        if not math.isclose(float(stored[key]), float(recomputed[key]), rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError(f"Stored statistical metric does not match per-seed evidence: {key}")
    if stored["decision"] not in {recomputed["decision"], "KEEP"}:
        raise ValueError("Stored decision is inconsistent with recomputed evidence")
    return recomputed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provider", choices=("deterministic", "qwen"), default="deterministic")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--require-real-api", action="store_true")
    args = parser.parse_args()
    if args.require_real_api and args.provider != "qwen":
        parser.error("--require-real-api requires --provider qwen")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    result = execute_protocol(
        config,
        args.output,
        provider=args.provider,
        model=args.model,
        require_real_api=args.require_real_api,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
