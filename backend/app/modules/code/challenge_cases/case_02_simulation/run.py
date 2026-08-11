from __future__ import annotations

import argparse
import csv
import io
import json
import math
import random
from pathlib import Path

from app.modules.code.challenge_cases.runtime import finalize_case_cart
from app.modules.code.execution_assessment import ExecutionClass


SEED = 42
SAMPLE_SIZES = [1000, 5000, 20000]


def run(output_root: Path) -> Path:
    rows = []
    for sample_size in SAMPLE_SIZES:
        rng = random.Random(SEED)
        inside = sum(
            1
            for _ in range(sample_size)
            if rng.random() ** 2 + rng.random() ** 2 <= 1.0
        )
        estimate = 4.0 * inside / sample_size
        rows.append({
            "sample_size": sample_size,
            "pi_estimate": estimate,
            "absolute_error": abs(estimate - math.pi),
        })

    baseline, largest = rows[0], rows[-1]
    metrics = {
        "baseline_absolute_error": round(baseline["absolute_error"], 8),
        "largest_budget_absolute_error": round(largest["absolute_error"], 8),
        "error_reduction": round(baseline["absolute_error"] - largest["absolute_error"], 8),
        "largest_budget_samples": largest["sample_size"],
    }
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=["sample_size", "pi_estimate", "absolute_error"], lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    config = {
        "seed": SEED,
        "sampleSizes": SAMPLE_SIZES,
        "baseline": SAMPLE_SIZES[0],
        "comparison": SAMPLE_SIZES[-1],
        "stopCondition": f"stop after {sum(SAMPLE_SIZES)} total samples",
    }
    return finalize_case_cart(
        output_root=output_root,
        case_id="case_02_monte_carlo",
        project_source=Path(__file__).parent,
        execution_class=ExecutionClass.SIMULATION_READY,
        metrics=metrics,
        artifacts={"parameter_results.csv": buffer.getvalue()},
        config=config,
        method="Fixed-seed Monte Carlo estimation across three sample budgets.",
        baseline="1,000-sample Monte Carlo estimate using the same seed.",
        log_text=json.dumps({"rows": rows, "metrics": metrics}, ensure_ascii=False) + "\n",
        expected=[{"metric": "error_reduction", "target": "> 0"}],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(run(args.output))
