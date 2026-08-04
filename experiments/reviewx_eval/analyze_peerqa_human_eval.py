#!/usr/bin/env python3
"""Analyze multi-rater PeerQA annotations and automatic alignment quality."""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any, Callable

from experiments.reviewx_eval.export_peerqa_human_eval import blind_rows
from experiments.reviewx_eval.summarize_human_eval import HUMAN_SCORE_FIELDS, parse_score


COVERAGE_LABELS = {
    "covered", "partial", "not_covered", "invalid_question", "insufficient_context",
}


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def round_or_none(value: float | None) -> float | None:
    return round(value, 4) if value is not None and math.isfinite(value) else None


def bootstrap_mean_ci(values: list[float], *, seed: int, iterations: int) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    rng = random.Random(seed)
    estimates = sorted(
        statistics.fmean(rng.choice(values) for _ in values)
        for _ in range(iterations)
    )
    return estimates[int(0.025 * (iterations - 1))], estimates[int(0.975 * (iterations - 1))]


def cluster_bootstrap_mean_ci(
    values: list[tuple[str, float]], *, seed: int, iterations: int,
) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    grouped: dict[str, list[float]] = defaultdict(list)
    for index, (cluster_id, value) in enumerate(values):
        grouped[cluster_id or f"missing_cluster_{index}"].append(value)
    clusters = sorted(grouped)
    rng = random.Random(seed)
    estimates = []
    for _ in range(iterations):
        sampled = [rng.choice(clusters) for _ in clusters]
        estimates.append(statistics.fmean(value for cluster in sampled for value in grouped[cluster]))
    estimates.sort()
    return estimates[int(0.025 * (iterations - 1))], estimates[int(0.975 * (iterations - 1))]


def krippendorff_alpha(units: dict[str, list[Any]], distance: Callable[[Any, Any], float]) -> float | None:
    usable = [values for values in units.values() if len(values) >= 2]
    if not usable:
        return None
    observed_numerator = sum(
        sum(
            distance(left, right)
            for left_index, left in enumerate(values)
            for right_index, right in enumerate(values)
            if left_index != right_index
        ) / (len(values) - 1)
        for values in usable
    )
    observed_denominator = sum(len(values) for values in usable)
    all_values = [value for values in usable for value in values]
    if len(all_values) < 2:
        return None
    expected = sum(
        distance(left, right)
        for i, left in enumerate(all_values)
        for j, right in enumerate(all_values)
        if i != j
    ) / (len(all_values) * (len(all_values) - 1))
    if expected == 0:
        return 1.0
    observed = observed_numerator / observed_denominator
    return 1.0 - observed / expected


def weighted_kappa(pairs: list[tuple[int, int]], *, quadratic: bool) -> float | None:
    if not pairs:
        return None
    categories = list(range(1, 6)) if quadratic else sorted({value for pair in pairs for value in pair})
    if len(categories) == 1:
        return 1.0
    index = {value: i for i, value in enumerate(categories)}
    scale = max(1, len(categories) - 1)

    def disagreement(left: int, right: int) -> float:
        gap = abs(index[left] - index[right]) / scale
        return gap * gap if quadratic else float(left != right)

    observed = statistics.fmean(disagreement(left, right) for left, right in pairs)
    left_counts, right_counts = Counter(left for left, _ in pairs), Counter(right for _, right in pairs)
    expected = sum(
        (left_counts[left] / len(pairs)) * (right_counts[right] / len(pairs)) * disagreement(left, right)
        for left in categories for right in categories
    )
    return 1.0 if expected == 0 else 1.0 - observed / expected


def pairwise_kappas(
    rows: list[dict[str, str]], field: str, parser: Callable[[Any], Any], *, quadratic: bool,
) -> list[dict[str, Any]]:
    by_rater: dict[str, dict[str, Any]] = defaultdict(dict)
    for row in rows:
        value = parser(row.get(field))
        if value is not None:
            by_rater[row["annotatorId"]][row["annotationId"]] = value
    raters = sorted(by_rater)
    result = []
    for i, left in enumerate(raters):
        for right in raters[i + 1:]:
            common = sorted(set(by_rater[left]) & set(by_rater[right]))
            pairs = [(by_rater[left][task], by_rater[right][task]) for task in common]
            result.append({
                "raterA": left, "raterB": right, "commonTasks": len(common),
                "kappa": round_or_none(weighted_kappa(pairs, quadratic=quadratic)),
            })
    return result


def parse_coverage(value: Any) -> str | None:
    label = str(value or "").strip()
    return label if label in COVERAGE_LABELS else None


def consensus_label(values: list[str]) -> str | None:
    counts = Counter(values)
    if not counts:
        return None
    top = counts.most_common()
    return top[0][0] if len(top) == 1 or top[0][1] > top[1][1] else None


def task_groups(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["annotationId"]].append(row)
    return grouped


def build_task_aggregates(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    result = []
    for task_id, task_rows in sorted(task_groups(rows).items()):
        first = task_rows[0]
        item: dict[str, Any] = {
            "annotationId": task_id,
            "sampleId": first.get("sampleId", ""),
            "findingId": first.get("findingId", ""),
            "riskType": first.get("riskType", ""),
            "analysisMethod": first.get("analysisMethod") or first.get("method") or "unknown",
            "comparisonPairId": first.get("comparisonPairId", ""),
            "raterCount": len({row["annotatorId"] for row in task_rows}),
        }
        labels = [label for row in task_rows if (label := parse_coverage(row.get("humanCoverageLabel")))]
        item["coverageConsensus"] = consensus_label(labels) or ""
        item["coverageDisagreement"] = len(set(labels)) > 1
        for field in HUMAN_SCORE_FIELDS:
            scores = [score for row in task_rows if (score := parse_score(row.get(field))) is not None]
            item[f"{field}Mean"] = round_or_none(statistics.fmean(scores)) if scores else None
            item[f"{field}Range"] = max(scores) - min(scores) if scores else None
        result.append(item)
    return result


def build_disagreements(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    result = []
    for task_id, task_rows in sorted(task_groups(rows).items()):
        score_ranges = {}
        for field in HUMAN_SCORE_FIELDS:
            scores = [score for row in task_rows if (score := parse_score(row.get(field))) is not None]
            if scores and max(scores) - min(scores) >= 2:
                score_ranges[field] = max(scores) - min(scores)
        labels = [label for row in task_rows if (label := parse_coverage(row.get("humanCoverageLabel")))]
        if score_ranges or len(set(labels)) > 1:
            result.append({
                "annotationId": task_id,
                "sampleId": task_rows[0].get("sampleId", ""),
                "coverageLabels": json.dumps(labels, ensure_ascii=False),
                "scoreRanges": json.dumps(score_ranges, ensure_ascii=False, sort_keys=True),
                "raterScores": json.dumps({
                    row["annotatorId"]: {
                        "coverage": row.get("humanCoverageLabel", ""),
                        **{field: row.get(field, "") for field in HUMAN_SCORE_FIELDS},
                        "notes": row.get("humanNotes", ""),
                    } for row in task_rows
                }, ensure_ascii=False),
            })
    return result


def metric_summary(rows: list[dict[str, str]], *, seed: int, iterations: int) -> dict[str, Any]:
    aggregates = build_task_aggregates(rows)
    grouped = task_groups(rows)
    metrics: dict[str, Any] = {}
    for field in HUMAN_SCORE_FIELDS:
        task_means = [float(row[f"{field}Mean"]) for row in aggregates if row[f"{field}Mean"] is not None]
        low, high = bootstrap_mean_ci(task_means, seed=seed, iterations=iterations)
        cluster_values = [
            (str(row.get("sampleId") or ""), float(row[f"{field}Mean"]))
            for row in aggregates if row[f"{field}Mean"] is not None
        ]
        cluster_low, cluster_high = cluster_bootstrap_mean_ci(
            cluster_values, seed=seed, iterations=iterations
        )
        units = {
            task_id: [score for row in task_rows if (score := parse_score(row.get(field))) is not None]
            for task_id, task_rows in grouped.items()
        }
        metrics[field] = {
            "taskCount": len(task_means),
            "mean": round_or_none(statistics.fmean(task_means)) if task_means else None,
            "bootstrap95CI": [round_or_none(low), round_or_none(high)],
            "paperClusterBootstrap95CI": [round_or_none(cluster_low), round_or_none(cluster_high)],
            "krippendorffAlphaInterval": round_or_none(
                krippendorff_alpha(units, lambda left, right: (float(left) - float(right)) ** 2)
            ),
            "pairwiseQuadraticWeightedKappa": pairwise_kappas(
                rows, field, lambda value: int(score) if (score := parse_score(value)) is not None else None,
                quadratic=True,
            ),
        }
    return metrics


def coverage_summary(rows: list[dict[str, str]]) -> dict[str, Any]:
    aggregates = build_task_aggregates(rows)
    labels = [row["coverageConsensus"] for row in aggregates if row["coverageConsensus"]]
    valid = [label for label in labels if label not in {"invalid_question", "insufficient_context"}]
    units = {
        task_id: [label for row in grouped if (label := parse_coverage(row.get("humanCoverageLabel")))]
        for task_id, grouped in task_groups(rows).items()
    }
    counts = Counter(labels)
    return {
        "consensusTaskCount": len(labels),
        "tieOrMissingCount": len(aggregates) - len(labels),
        "labelCounts": dict(sorted(counts.items())),
        "strictCoverageRate": round_or_none(counts["covered"] / len(valid)) if valid else None,
        "broadCoverageRate": round_or_none(
            (counts["covered"] + counts["partial"]) / len(valid)
        ) if valid else None,
        "krippendorffAlphaNominal": round_or_none(
            krippendorff_alpha(units, lambda left, right: float(left != right))
        ),
        "pairwiseCohenKappa": pairwise_kappas(rows, "humanCoverageLabel", parse_coverage, quadratic=False),
    }


def unblind_key(answer_key: Path, seed: int) -> dict[str, dict[str, Any]]:
    originals = load_csv(answer_key)
    indexed = [{**row, "_answerIndex": index} for index, row in enumerate(originals)]
    blinded = blind_rows(indexed, seed)
    return {
        blind["annotationId"]: originals[int(blind["_answerIndex"])]
        for blind in blinded
    }


def alignment_summary(
    aggregates: list[dict[str, Any]], answer_key: Path | None, seed: int,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if answer_key is None:
        return None, []
    key = unblind_key(answer_key, seed)
    examples = []
    for row in aggregates:
        label = row["coverageConsensus"]
        if label not in {"covered", "partial", "not_covered"} or row["annotationId"] not in key:
            continue
        source = key[row["annotationId"]]
        examples.append({
            "annotationId": row["annotationId"],
            "score": float(source.get("automaticMatchScore") or 0),
            "humanPositive": label in {"covered", "partial"},
        })
    curve = []
    for step in range(0, 36):
        threshold = step / 100
        tp = sum(item["score"] >= threshold and item["humanPositive"] for item in examples)
        fp = sum(item["score"] >= threshold and not item["humanPositive"] for item in examples)
        fn = sum(item["score"] < threshold and item["humanPositive"] for item in examples)
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        f1 = 2 * precision * recall / (precision + recall) if precision and recall else 0.0
        curve.append({
            "threshold": round(threshold, 2), "tp": tp, "fp": fp, "fn": fn,
            "precision": round_or_none(precision), "recall": round_or_none(recall),
            "f1": round_or_none(f1),
        })
    best = max(curve, key=lambda row: (row["f1"] or 0, row["threshold"])) if curve else None
    current = next((row for row in curve if row["threshold"] == 0.12), None)
    return {"usableTasks": len(examples), "threshold012": current, "bestDevelopmentThreshold": best}, curve


def method_summaries(rows: list[dict[str, str]], *, seed: int, iterations: int) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("analysisMethod") or row.get("method") or "unknown")].append(row)
    result = {}
    for method, method_rows in sorted(grouped.items()):
        aggregates = build_task_aggregates(method_rows)
        metrics = {}
        for field in HUMAN_SCORE_FIELDS:
            values = [float(item[f"{field}Mean"]) for item in aggregates if item[f"{field}Mean"] is not None]
            low, high = bootstrap_mean_ci(values, seed=seed, iterations=iterations)
            cluster_values = [
                (str(item.get("sampleId") or ""), float(item[f"{field}Mean"]))
                for item in aggregates if item[f"{field}Mean"] is not None
            ]
            cluster_low, cluster_high = cluster_bootstrap_mean_ci(
                cluster_values, seed=seed, iterations=iterations
            )
            metrics[field] = {
                "mean": round_or_none(statistics.fmean(values)) if values else None,
                "taskCount": len(values), "bootstrap95CI": [round_or_none(low), round_or_none(high)],
                "paperClusterBootstrap95CI": [
                    round_or_none(cluster_low), round_or_none(cluster_high)
                ],
            }
        result[method] = {
            "annotationRows": len(method_rows), "taskCount": len(aggregates),
            "metrics": metrics, "coverage": coverage_summary(method_rows),
        }
    return result


def _paired_method_effect(
    groups: list[dict[str, dict[str, Any]]], left: str, right: str, *, seed: int, iterations: int,
) -> dict[str, Any]:
    complete = [methods for methods in groups if left in methods and right in methods]
    result: dict[str, Any] = {
        "leftMethod": left, "rightMethod": right,
        "differenceDirection": "right-minus-left", "completePairCount": len(complete),
        "metrics": {},
    }
    for field in HUMAN_SCORE_FIELDS:
        clustered_differences = [
            (
                str(methods[right].get("sampleId") or methods[left].get("sampleId") or ""),
                float(methods[right][f"{field}Mean"]) - float(methods[left][f"{field}Mean"]),
            )
            for methods in complete
            if left in methods and right in methods
            and methods[left][f"{field}Mean"] is not None and methods[right][f"{field}Mean"] is not None
        ]
        differences = [value for _, value in clustered_differences]
        low, high = bootstrap_mean_ci(differences, seed=seed, iterations=iterations)
        cluster_low, cluster_high = cluster_bootstrap_mean_ci(
            clustered_differences, seed=seed, iterations=iterations
        )
        result["metrics"][field] = {
            "pairCount": len(differences),
            "meanDifference": round_or_none(statistics.fmean(differences)) if differences else None,
            "bootstrap95CI": [round_or_none(low), round_or_none(high)],
            "paperClusterBootstrap95CI": [
                round_or_none(cluster_low), round_or_none(cluster_high)
            ],
            "rightWins": sum(value > 0 for value in differences),
            "ties": sum(value == 0 for value in differences),
            "leftWins": sum(value < 0 for value in differences),
        }
    clustered_coverage_pairs = []
    binary = {"covered": 1.0, "partial": 1.0, "not_covered": 0.0}
    for methods in complete:
        if left not in methods or right not in methods:
            continue
        left_value = binary.get(str(methods[left].get("coverageConsensus") or ""))
        right_value = binary.get(str(methods[right].get("coverageConsensus") or ""))
        if left_value is not None and right_value is not None:
            clustered_coverage_pairs.append((
                str(methods[right].get("sampleId") or methods[left].get("sampleId") or ""),
                right_value - left_value,
            ))
    coverage_pairs = [value for _, value in clustered_coverage_pairs]
    low, high = bootstrap_mean_ci(coverage_pairs, seed=seed, iterations=iterations)
    cluster_low, cluster_high = cluster_bootstrap_mean_ci(
        clustered_coverage_pairs, seed=seed, iterations=iterations
    )
    result["broadCoverage"] = {
        "pairCount": len(coverage_pairs),
        "meanDifference": round_or_none(statistics.fmean(coverage_pairs)) if coverage_pairs else None,
        "bootstrap95CI": [round_or_none(low), round_or_none(high)],
        "paperClusterBootstrap95CI": [round_or_none(cluster_low), round_or_none(cluster_high)],
        "rightOnlyCovered": sum(value > 0 for value in coverage_pairs),
        "same": sum(value == 0 for value in coverage_pairs),
        "leftOnlyCovered": sum(value < 0 for value in coverage_pairs),
    }
    return result


def paired_comparison(
    aggregates: list[dict[str, Any]], *, seed: int, iterations: int,
) -> dict[str, Any] | None:
    by_pair: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for item in aggregates:
        pair_id = str(item.get("comparisonPairId") or "")
        method = str(item.get("analysisMethod") or "unknown")
        if pair_id:
            by_pair[pair_id][method] = item
    groups = [methods for methods in by_pair.values() if len(methods) >= 2]
    if not groups:
        return None
    method_names = sorted({method for methods in groups for method in methods})
    effects = [
        _paired_method_effect(groups, left, right, seed=seed, iterations=iterations)
        for left, right in combinations(method_names, 2)
        if any(left in methods and right in methods for methods in groups)
    ]
    if len(effects) == 1:
        return {"comparisonMode": "single_pair", **effects[0]}
    return {
        "comparisonMode": "all_pairwise",
        "methodCount": len(method_names),
        "methods": method_names,
        "comparisonCount": len(effects),
        "comparisons": effects,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def render_markdown(report: dict[str, Any], disagreement_count: int) -> str:
    lines = [
        "# PeerQA Human Evaluation Report",
        "",
        "## Data Status",
        "",
        f"- Annotators: {report['annotatorCount']}",
        f"- Completed rows: {report['completedRows']} / {report['inputRows']}",
        f"- Incomplete rows excluded from analysis: {report['incompleteRows']}",
        f"- Tasks queued for adjudication: {disagreement_count}",
        "",
        "## Ordinal Ratings",
        "",
        "| Metric | Mean | Paper-cluster bootstrap 95% CI | Krippendorff alpha (interval) |",
        "|---|---:|---:|---:|",
    ]
    for field, values in report["metrics"].items():
        interval = values["paperClusterBootstrap95CI"]
        lines.append(
            f"| {field} | {values['mean']} | [{interval[0]}, {interval[1]}] | "
            f"{values['krippendorffAlphaInterval']} |"
        )
    coverage = report["coverage"]
    lines.extend([
        "",
        "## Expert-Question Coverage",
        "",
        f"- Consensus tasks: {coverage['consensusTaskCount']}",
        f"- Tie or missing consensus: {coverage['tieOrMissingCount']}",
        f"- Strict coverage rate: {coverage['strictCoverageRate']}",
        f"- Broad coverage rate (covered + partial): {coverage['broadCoverageRate']}",
        f"- Krippendorff alpha (nominal): {coverage['krippendorffAlphaNominal']}",
        f"- Label counts: `{json.dumps(coverage['labelCounts'], ensure_ascii=False, sort_keys=True)}`",
    ])
    alignment = report.get("automaticAlignment")
    if alignment:
        lines.extend([
            "",
            "## Development Alignment Calibration",
            "",
            f"- Usable consensus tasks: {alignment['usableTasks']}",
            f"- Threshold 0.12: `{json.dumps(alignment['threshold012'], sort_keys=True)}`",
            f"- Best development threshold: `{json.dumps(alignment['bestDevelopmentThreshold'], sort_keys=True)}`",
            "",
            "The selected threshold is development-only and must be frozen before held-out evaluation.",
        ])
    methods = report.get("methodSummaries") or {}
    if len(methods) > 1:
        lines.extend(["", "## Method Comparison", ""])
        for method, values in methods.items():
            correctness = values["metrics"]["humanCorrectness"]
            coverage = values["coverage"]
            lines.append(
                f"- `{method}`: correctness={correctness['mean']} "
                f"[{correctness['paperClusterBootstrap95CI'][0]}, "
                f"{correctness['paperClusterBootstrap95CI'][1]}], "
                f"broad coverage={coverage['broadCoverageRate']}"
            )
    paired = report.get("pairedComparison")
    if paired and paired.get("comparisonMode") == "all_pairwise":
        lines.extend(["", "## Pairwise Paired Effects", ""])
        for effect in paired["comparisons"]:
            lines.extend([
                f"### {effect['rightMethod']} - {effect['leftMethod']}",
                "",
                f"Complete pairs: {effect['completePairCount']}.",
                "",
                "| Metric | Mean difference | Paper-cluster bootstrap 95% CI |",
                "|---|---:|---:|",
            ])
            for field, values in effect["metrics"].items():
                interval = values["paperClusterBootstrap95CI"]
                lines.append(
                    f"| {field} | {values['meanDifference']} | [{interval[0]}, {interval[1]}] |"
                )
            coverage_effect = effect["broadCoverage"]
            interval = coverage_effect["paperClusterBootstrap95CI"]
            lines.extend([
                f"| broadCoverage | {coverage_effect['meanDifference']} | "
                f"[{interval[0]}, {interval[1]}] |",
                "",
            ])
    elif paired:
        lines.extend([
            "",
            "## Paired Effects",
            "",
            f"Difference direction: `{paired['rightMethod']} - {paired['leftMethod']}`; "
            f"complete pairs: {paired['completePairCount']}.",
            "",
            "| Metric | Mean difference | Paper-cluster bootstrap 95% CI |",
            "|---|---:|---:|",
        ])
        for field, values in paired["metrics"].items():
            interval = values["paperClusterBootstrap95CI"]
            lines.append(f"| {field} | {values['meanDifference']} | [{interval[0]}, {interval[1]}] |")
        coverage_effect = paired["broadCoverage"]
        interval = coverage_effect["paperClusterBootstrap95CI"]
        lines.append(
            f"| broadCoverage | {coverage_effect['meanDifference']} | [{interval[0]}, {interval[1]}] |"
        )
    return "\n".join(lines) + "\n"


def analyze(
    rows: list[dict[str, str]], *, answer_key: Path | None, seed: int, iterations: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    required = {"annotationId", "annotatorId"}
    if rows and not required.issubset(rows[0]):
        raise ValueError("input must contain annotationId and annotatorId")
    identities = [(row["annotationId"], row["annotatorId"]) for row in rows]
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate annotationId/annotatorId rows")
    completed = [dict(row) for row in rows if row.get("annotationStatus") == "completed"]
    if answer_key is not None:
        key = unblind_key(answer_key, seed)
        for row in completed:
            source = key.get(row["annotationId"], {})
            row["analysisMethod"] = str(source.get("method") or row.get("method") or "unknown")
            row["comparisonPairId"] = str(source.get("comparisonPairId") or row.get("comparisonPairId") or "")
    else:
        for row in completed:
            row["analysisMethod"] = str(row.get("method") or "unknown")
    aggregates = build_task_aggregates(completed)
    alignment, curve = alignment_summary(aggregates, answer_key, seed)
    annotators = sorted({row["annotatorId"] for row in rows})
    report = {
        "inputRows": len(rows),
        "completedRows": len(completed),
        "incompleteRows": len(rows) - len(completed),
        "annotatorCount": len(annotators),
        "annotators": {
            annotator: {
                "rowCount": sum(row["annotatorId"] == annotator for row in rows),
                "completedCount": sum(
                    row["annotatorId"] == annotator and row.get("annotationStatus") == "completed"
                    for row in rows
                ),
            } for annotator in annotators
        },
        "metrics": metric_summary(completed, seed=seed, iterations=iterations),
        "coverage": coverage_summary(completed),
        "methodSummaries": method_summaries(completed, seed=seed, iterations=iterations),
        "pairedComparison": paired_comparison(aggregates, seed=seed, iterations=iterations),
        "automaticAlignment": alignment,
    }
    return report, aggregates, build_disagreements(completed), curve


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-prefix", required=True)
    parser.add_argument("--answer-key")
    parser.add_argument("--shuffle-seed", type=int, default=20260711)
    parser.add_argument("--bootstrap-iterations", type=int, default=10_000)
    args = parser.parse_args()
    if args.bootstrap_iterations < 1:
        parser.error("--bootstrap-iterations must be positive")
    rows = load_csv(Path(args.input))
    report, aggregates, disagreements, curve = analyze(
        rows, answer_key=Path(args.answer_key) if args.answer_key else None,
        seed=args.shuffle_seed, iterations=args.bootstrap_iterations,
    )
    prefix = Path(args.output_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_name(prefix.name + "_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    prefix.with_name(prefix.name + "_report.md").write_text(
        render_markdown(report, len(disagreements)), encoding="utf-8",
    )
    write_csv(prefix.with_name(prefix.name + "_task_aggregates.csv"), aggregates)
    write_csv(prefix.with_name(prefix.name + "_disagreements.csv"), disagreements)
    write_csv(prefix.with_name(prefix.name + "_threshold_curve.csv"), curve)
    print(
        f"rows={report['inputRows']} completed={report['completedRows']} "
        f"annotators={report['annotatorCount']} disagreements={len(disagreements)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
