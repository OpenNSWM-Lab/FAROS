"""Aggregate repeated ReviewX planning benchmark runs."""

from __future__ import annotations

import argparse
import json
import statistics
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


RATE_FIELDS = (
    "planExecutabilityRate",
    "constraintSatisfactionRate",
    "policyAgreementRate",
)


def _wilson(successes: int, total: int, z: float = 1.96) -> list[float]:
    if total <= 0:
        return [0.0, 0.0]
    rate = successes / total
    denominator = 1 + z * z / total
    center = (rate + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total)) / denominator
    return [max(0.0, center - margin), min(1.0, center + margin)]


def aggregate(
    paths: list[Path],
    *,
    attempted_runs: int | None = None,
    input_price_per_million: float | None = None,
    output_price_per_million: float | None = None,
) -> dict[str, Any]:
    runs = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    methods: dict[str, Any] = {}
    for method in ("qwen_one_shot", "frozen_rules", "qwen_reviewx"):
        values = {field: [run["methods"][method][field] for run in runs] for field in RATE_FIELDS}
        methods[method] = {
            field: {
                "mean": statistics.fmean(items),
                "min": min(items),
                "max": max(items),
                "sampleStd": statistics.stdev(items) if len(items) > 1 else 0.0,
            }
            for field, items in values.items()
        }
        pooled_total = sum(int(run["methods"][method]["cases"]) for run in runs)
        pooled_successes = sum(
            round(run["methods"][method]["policyAgreementRate"] * run["methods"][method]["cases"])
            for run in runs
        )
        methods[method]["pooledPolicyAgreement"] = {
            "successes": pooled_successes,
            "total": pooled_total,
            "rate": pooled_successes / pooled_total,
            "wilson95": _wilson(pooled_successes, pooled_total),
        }
    token_counts = [run["qwenTrace"]["usage"]["total_tokens"] for run in runs]
    input_tokens = sum(run["qwenTrace"]["usage"].get("prompt_tokens", 0) for run in runs)
    output_tokens = sum(run["qwenTrace"]["usage"].get("completion_tokens", 0) for run in runs)
    latencies = [run["qwenTrace"]["latencyMs"] for run in runs]
    estimated_cost = None
    if input_price_per_million is not None and output_price_per_million is not None:
        estimated_cost = (
            input_tokens * input_price_per_million
            + output_tokens * output_price_per_million
        ) / 1_000_000
    attempts = attempted_runs or len(runs)
    return {
        "schemaVersion": "reviewx-planning-stability/v1",
        "createdAt": datetime.now(UTC).isoformat(),
        "runCount": len(runs),
        "seeds": [run.get("seed") for run in runs],
        "methods": methods,
        "qwenCost": {
            "successfulCalls": len(runs),
            "attemptedCalls": attempts,
            "failureRate": (attempts - len(runs)) / attempts,
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "totalTokens": sum(token_counts),
            "meanTokens": statistics.fmean(token_counts),
            "totalLatencyMs": sum(latencies),
            "meanLatencyMs": statistics.fmean(latencies),
            "estimatedCostCny": estimated_cost,
            "pricing": {
                "inputCnyPerMillion": input_price_per_million,
                "outputCnyPerMillion": output_price_per_million,
                "note": "List-price estimate before free quota or account discounts; console bill is authoritative.",
            },
        },
        "responseHashes": [run["qwenTrace"]["responseHash"] for run in runs],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summaries", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--attempted-runs", type=int)
    parser.add_argument("--input-price-per-million", type=float)
    parser.add_argument("--output-price-per-million", type=float)
    args = parser.parse_args()
    result = aggregate(
        args.summaries,
        attempted_runs=args.attempted_runs,
        input_price_per_million=args.input_price_per_million,
        output_price_per_million=args.output_price_per_million,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
