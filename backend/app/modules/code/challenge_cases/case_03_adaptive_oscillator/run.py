from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.modules.code.challenge_cases.runtime import finalize_case_cart
from app.modules.code.execution_assessment import ExecutionClass
from experiments.reviewx_oscillator.run import (
    baseline_design,
    candidate_designs,
    run_round,
    statistical_gate,
    summarize_round,
)


SMOKE_PROTOCOL = {
    "trueOmega0": 1.2,
    "trueZeta": 0.1,
    "noiseStd": 0.08,
    "observationBudget": 80,
    "optimizerMaxNfev": 80,
    "candidateNearResonanceFrequency": 1.15,
    "holdoutExcitationFrequencies": [0.7, 0.95, 1.35],
    "holdoutTrajectoryPoints": 120,
}
SEEDS = list(range(4201, 4209))


def run(output_root: Path) -> Path:
    first_design = baseline_design(SMOKE_PROTOCOL)
    second_design = candidate_designs(SMOKE_PROTOCOL)[2]
    first = run_round(SEEDS, first_design, SMOKE_PROTOCOL)
    second = run_round(SEEDS, second_design, SMOKE_PROTOCOL)
    first_summary = summarize_round(first)
    second_summary = summarize_round(second)
    statistics = statistical_gate(
        [row["heldout_trajectory_nrmse"] for row in first],
        [row["heldout_trajectory_nrmse"] for row in second],
        bootstrap_samples=500,
        bootstrap_seed=4200,
    )
    metrics = {
        "round_1_heldout_trajectory_nrmse": round(statistics["round1Mean"], 8),
        "round_2_heldout_trajectory_nrmse": round(statistics["round2Mean"], 8),
        "relative_nrmse_improvement": round(statistics["relativeImprovement"], 8),
        "paired_ci_lower": round(statistics["pairedBootstrap95"][0], 8),
        "paired_ci_upper": round(statistics["pairedBootstrap95"][1], 8),
        "matched_observation_budget": first_design.observation_budget,
    }
    plan_delta = {
        "selectedCandidateId": second_design.candidate_id,
        "oldValue": first_design.as_dict(),
        "newValue": second_design.as_dict(),
        "evidence": "Round 1 Fisher-information and transient-coverage diagnosis",
        "affectedNodes": ["experiment.design", "experiment.fit", "reviewx.statistics"],
        "finalHoldoutExposedToQwen": False,
        "note": "This Code case is an offline execution fixture, not the final Qwen representative run.",
    }
    per_seed = [
        {
            "seed": left["seed"],
            "round1Nrmse": left["heldout_trajectory_nrmse"],
            "round2Nrmse": right["heldout_trajectory_nrmse"],
        }
        for left, right in zip(first, second)
    ]
    return finalize_case_cart(
        output_root=output_root,
        case_id="case_03_adaptive_oscillator",
        project_source=Path(__file__).parent,
        execution_class=ExecutionClass.SIMULATION_READY,
        metrics=metrics,
        artifacts={
            "per_seed_results.json": json.dumps(per_seed, ensure_ascii=False, indent=2),
            "plan_delta.json": json.dumps(plan_delta, ensure_ascii=False, indent=2),
            "round_1_summary.json": json.dumps(first_summary, ensure_ascii=False, indent=2),
            "round_2_summary.json": json.dumps(second_summary, ensure_ascii=False, indent=2),
            "statistical_summary.json": json.dumps(statistics, ensure_ascii=False, indent=2),
        },
        config={
            **SMOKE_PROTOCOL,
            "seeds": SEEDS,
            "round1Design": first_design.as_dict(),
            "round2Design": second_design.as_dict(),
            "stopCondition": "stop after the paired fixed-seed protocol",
        },
        method="Matched-budget damped-oscillator identification with a ReviewX Plan Delta.",
        baseline="Single off-resonance excitation with steady-state uniform sampling.",
        log_text=json.dumps({"metrics": metrics, "decision": statistics["decision"]}, ensure_ascii=False) + "\n",
        expected=[{"metric": "relative_nrmse_improvement", "target": "> 0.15"}],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(run(args.output))
