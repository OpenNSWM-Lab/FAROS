#!/usr/bin/env python3
"""Compute source-paper cluster bootstrap intervals from scorer sample rows."""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def percentile(values: list[float], probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def source_metrics(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        source_id = str(row.get("sourcePaperId") or row.get("paperId") or "")
        if source_id:
            grouped[source_id].append(row)

    result = {}
    for source_id, source_rows in grouped.items():
        variants = [row for row in source_rows if row.get("sampleType") != "clean_control"]
        clean = [row for row in source_rows if row.get("sampleType") == "clean_control"]
        result[source_id] = {
            "targetExtractionRecall": mean([float(bool(row.get("targetExtracted"))) for row in variants]),
            "targetFlagRecall": mean([float(bool(row.get("targetFlagged"))) for row in variants]),
            "triageRecall": mean([float(bool(row.get("detected"))) for row in variants]),
            "cleanFlagRate": mean([float((row.get("issueFindingCount") or 0) > 0) for row in clean]),
            "cleanIssueFindingsPerSample": mean([
                float(row.get("issueFindingCount") or 0) for row in clean
            ]),
        }
    return result


def bootstrap(
    metrics_by_source: dict[str, dict[str, float]],
    iterations: int,
    seed: int,
) -> dict[str, dict[str, float]]:
    source_ids = sorted(metrics_by_source)
    if not source_ids:
        return {}
    metric_names = list(next(iter(metrics_by_source.values())))
    rng = random.Random(seed)
    samples: dict[str, list[float]] = {name: [] for name in metric_names}
    for _ in range(iterations):
        selected = [rng.choice(source_ids) for _ in source_ids]
        for name in metric_names:
            samples[name].append(mean([metrics_by_source[source][name] for source in selected]))
    return {
        name: {
            "estimate": round(mean([metrics_by_source[source][name] for source in source_ids]), 4),
            "ci95Low": round(percentile(values, 0.025), 4),
            "ci95High": round(percentile(values, 0.975), 4),
        }
        for name, values in samples.items()
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", required=True, help="Sample JSONL emitted by score_eval.py")
    parser.add_argument("--output", required=True)
    parser.add_argument("--csv-output")
    parser.add_argument("--iterations", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=20260710)
    args = parser.parse_args()

    rows = read_jsonl(Path(args.samples))
    by_method: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_method[str(row.get("method") or "unknown")].append(row)

    payload: dict[str, Any] = {
        "bootstrapUnit": "source_paper",
        "iterations": args.iterations,
        "seed": args.seed,
        "methods": {},
    }
    csv_rows = []
    for method, method_rows in sorted(by_method.items()):
        per_source = source_metrics(method_rows)
        intervals = bootstrap(per_source, args.iterations, args.seed)
        payload["methods"][method] = {
            "sourcePaperCount": len(per_source),
            "metrics": intervals,
            "perSource": per_source,
        }
        for metric, values in intervals.items():
            csv_rows.append({"method": method, "metric": metric, **values})

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.csv_output:
        csv_output = Path(args.csv_output)
        csv_output.parent.mkdir(parents=True, exist_ok=True)
        with csv_output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["method", "metric", "estimate", "ci95Low", "ci95High"],
            )
            writer.writeheader()
            writer.writerows(csv_rows)
    print(f"methods={len(payload['methods'])} output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
