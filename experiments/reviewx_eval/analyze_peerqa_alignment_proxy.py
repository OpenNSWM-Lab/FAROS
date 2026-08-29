#!/usr/bin/env python3
"""Summarize PeerQA automatic alignment without treating it as human recall."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import random
import statistics
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rounded(value: float | None) -> float | None:
    return round(value, 6) if value is not None and math.isfinite(value) else None


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> list[float | None]:
    if total <= 0:
        return [None, None]
    rate = successes / total
    denominator = 1 + z * z / total
    center = (rate + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total)) / denominator
    return [rounded(max(0.0, center - margin)), rounded(min(1.0, center + margin))]


def cluster_bootstrap_ci(
    rows: list[dict[str, Any]],
    value: Callable[[dict[str, Any]], float],
    *,
    seed: int,
    iterations: int,
) -> list[float | None]:
    if not rows:
        return [None, None]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, row in enumerate(rows):
        grouped[str(row.get("sampleId") or f"missing_{index}")].append(row)
    clusters = sorted(grouped)
    rng = random.Random(seed)
    estimates = []
    for _ in range(iterations):
        sampled = [rng.choice(clusters) for _ in clusters]
        values = [value(row) for cluster in sampled for row in grouped[cluster]]
        estimates.append(statistics.fmean(values))
    return [rounded(percentile(estimates, 0.025)), rounded(percentile(estimates, 0.975))]


def exact_mcnemar_p(left_only: int, right_only: int) -> float:
    discordant = left_only + right_only
    if discordant == 0:
        return 1.0
    tail = sum(math.comb(discordant, k) for k in range(min(left_only, right_only) + 1))
    return min(1.0, 2 * tail / (2 ** discordant))


def model_names(record: dict[str, Any]) -> set[str]:
    trace = record.get("modelTrace") or {}
    calls = list(trace.get("llmCalls") or [])
    calls.extend((trace.get("llmRouting") or {}).get("llmCalls") or [])
    names = {str(call.get("model")) for call in calls if call.get("model")}
    configured = (record.get("methodConfig") or {}).get("model")
    if configured:
        names.add(str(configured))
    requested = (trace.get("llmRouting") or {}).get("requestedModel")
    if requested:
        names.add(str(requested))
    return names


def llm_call_count(record: dict[str, Any]) -> int:
    trace = record.get("modelTrace") or {}
    declared = trace.get("llmCallCount")
    if declared is not None:
        return max(0, int(declared or 0))
    direct = trace.get("llmCalls") or []
    routed = (trace.get("llmRouting") or {}).get("llmCalls") or []
    return max(len(direct), len(routed))


def summarize_runs(
    records: list[dict[str, Any]],
    selected_keys: set[tuple[str, str, int]],
    *,
    paper_count: int,
    expected_repetitions: int,
    max_total_tokens: int,
) -> dict[str, Any]:
    expected = paper_count * expected_repetitions
    statuses = [str(record.get("status") or "completed") for record in records]
    failed = sum(status not in {"completed", "success"} for status in statuses)
    tokens = [float((record.get("modelTrace") or {}).get("estimatedTokenCost", 0) or 0) for record in records]
    latencies = [float(record.get("runnerElapsedMs", 0) or 0) for record in records]
    call_counts = [llm_call_count(record) for record in records]
    escalated_tokens = [token for token, calls in zip(tokens, call_counts) if calls > 0]
    budget_exceeded = sum(
        bool((record.get("modelTrace") or {}).get("budgetExceeded")) or token > max_total_tokens
        for record, token in zip(records, tokens)
    )
    finding_total = sum(len(record.get("findings") or []) for record in records)
    grounded_total = sum(
        int((record.get("summary") or {}).get("exactQuoteGroundedCount", 0) or 0)
        for record in records
    )
    grounded_reported = any(
        (record.get("summary") or {}).get("exactQuoteGroundedCount") is not None
        for record in records
    )
    return {
        "expectedRunCount": expected,
        "observedRunCount": len(records),
        "selectedRunCount": sum(
            (str(record.get("method") or ""), str(record.get("sampleId") or ""),
             int(record.get("runnerRepetition", 0) or 0)) in selected_keys
            for record in records
        ),
        "failedRunCount": failed,
        "failureRate": rounded((failed + max(0, expected - len(records))) / expected) if expected else None,
        "budgetExceededCount": budget_exceeded,
        "meanTotalTokens": rounded(statistics.fmean(tokens)) if tokens else None,
        "p95TotalTokens": rounded(percentile(tokens, 0.95)),
        "meanLatencyMs": rounded(statistics.fmean(latencies)) if latencies else None,
        "p95LatencyMs": rounded(percentile(latencies, 0.95)),
        "llmCallCount": sum(call_counts),
        "llmEscalatedRunCount": sum(calls > 0 for calls in call_counts),
        "llmEscalationRate": rounded(sum(calls > 0 for calls in call_counts) / len(records)) if records else None,
        "localOnlyRunCount": sum(calls == 0 for calls in call_counts),
        "meanTokensPerEscalatedRun": rounded(statistics.fmean(escalated_tokens)) if escalated_tokens else 0.0,
        "meanFindingCount": rounded(finding_total / len(records)) if records else None,
        "exactQuoteGroundedRate": (
            rounded(grounded_total / finding_total) if grounded_reported and finding_total else None
        ),
        "models": sorted({name for record in records for name in model_names(record)}),
    }


def summarize_method(
    method: str,
    rows: list[dict[str, Any]],
    records: list[dict[str, Any]],
    *,
    seed: int,
    iterations: int,
    expected_repetitions: int,
    max_total_tokens: int,
) -> dict[str, Any]:
    candidate_count = sum(bool(row.get("automaticCoverageCandidate")) for row in rows)
    scores = [float(row.get("automaticMatchScore", 0) or 0) for row in rows]
    selected_keys = {
        (method, str(row.get("sampleId") or ""), int(row.get("selectedRepetition", 0) or 0))
        for row in rows
    }
    return {
        "method": method,
        "paperCount": len({str(row.get("sampleId") or "") for row in rows}),
        "taskCount": len(rows),
        "automaticAlignment": {
            "candidateCount": candidate_count,
            "candidateRate": rounded(candidate_count / len(rows)) if rows else None,
            "candidateRateWilson95": wilson_interval(candidate_count, len(rows)),
            "candidateRatePaperClusterBootstrap95": cluster_bootstrap_ci(
                rows, lambda row: float(bool(row.get("automaticCoverageCandidate"))),
                seed=seed, iterations=iterations,
            ),
            "meanBestMatchScore": rounded(statistics.fmean(scores)) if scores else None,
            "bestMatchScorePaperClusterBootstrap95": cluster_bootstrap_ci(
                rows, lambda row: float(row.get("automaticMatchScore", 0) or 0),
                seed=seed + 1, iterations=iterations,
            ),
        },
        "runQuality": summarize_runs(
            records,
            selected_keys,
            paper_count=len({str(row.get("sampleId") or "") for row in rows}),
            expected_repetitions=expected_repetitions,
            max_total_tokens=max_total_tokens,
        ),
    }


def paired_summary(
    left: str,
    right: str,
    by_pair: dict[str, dict[str, dict[str, Any]]],
    *,
    seed: int,
    iterations: int,
) -> dict[str, Any]:
    paired_rows = []
    left_only = right_only = 0
    score_wins = score_ties = score_losses = 0
    for pair_id, methods in sorted(by_pair.items()):
        a, b = methods[left], methods[right]
        a_covered = bool(a.get("automaticCoverageCandidate"))
        b_covered = bool(b.get("automaticCoverageCandidate"))
        left_only += int(a_covered and not b_covered)
        right_only += int(b_covered and not a_covered)
        delta = float(b.get("automaticMatchScore", 0) or 0) - float(a.get("automaticMatchScore", 0) or 0)
        score_wins += int(delta > 1e-12)
        score_losses += int(delta < -1e-12)
        score_ties += int(abs(delta) <= 1e-12)
        paired_rows.append({
            "comparisonPairId": pair_id,
            "sampleId": a.get("sampleId"),
            "coverageDelta": float(b_covered) - float(a_covered),
            "scoreDelta": delta,
        })
    coverage_delta = statistics.fmean(row["coverageDelta"] for row in paired_rows)
    score_delta = statistics.fmean(row["scoreDelta"] for row in paired_rows)
    return {
        "methodA": left,
        "methodB": right,
        "effectDirection": "methodB-minus-methodA",
        "pairCount": len(paired_rows),
        "candidateRateDelta": rounded(coverage_delta),
        "candidateRateDeltaPaperClusterBootstrap95": cluster_bootstrap_ci(
            paired_rows, lambda row: row["coverageDelta"], seed=seed, iterations=iterations,
        ),
        "meanBestMatchScoreDelta": rounded(score_delta),
        "scoreDeltaPaperClusterBootstrap95": cluster_bootstrap_ci(
            paired_rows, lambda row: row["scoreDelta"], seed=seed + 1, iterations=iterations,
        ),
        "candidateDiscordance": {
            "methodAOnly": left_only,
            "methodBOnly": right_only,
            "exactMcNemarPValue": rounded(exact_mcnemar_p(left_only, right_only)),
        },
        "scorePairOutcomeForMethodB": {
            "wins": score_wins,
            "ties": score_ties,
            "losses": score_losses,
        },
    }


def paired_efficiency_summary(
    left: str,
    right: str,
    records_by_method: dict[str, list[dict[str, Any]]],
    *,
    seed: int,
    iterations: int,
) -> dict[str, Any]:
    def keyed(records: list[dict[str, Any]]) -> dict[tuple[str, int], dict[str, Any]]:
        return {
            (
                str(record.get("sampleId") or record.get("paperId") or ""),
                int(record.get("runnerRepetition", 0) or 0),
            ): record
            for record in records
        }

    left_records = keyed(records_by_method.get(left, []))
    right_records = keyed(records_by_method.get(right, []))
    common = sorted(set(left_records) & set(right_records))
    paired = []
    for sample_id, repetition in common:
        a, b = left_records[(sample_id, repetition)], right_records[(sample_id, repetition)]
        a_tokens = float((a.get("modelTrace") or {}).get("estimatedTokenCost", 0) or 0)
        b_tokens = float((b.get("modelTrace") or {}).get("estimatedTokenCost", 0) or 0)
        paired.append({
            "sampleId": sample_id,
            "repetition": repetition,
            "latencyDeltaMs": float(b.get("runnerElapsedMs", 0) or 0) - float(a.get("runnerElapsedMs", 0) or 0),
            "tokenDelta": b_tokens - a_tokens,
            "llmCallDelta": llm_call_count(b) - llm_call_count(a),
            "findingCountDelta": len(b.get("findings") or []) - len(a.get("findings") or []),
        })

    def summarize(field: str, offset: int) -> dict[str, Any]:
        values = [float(row[field]) for row in paired]
        return {
            "mean": rounded(statistics.fmean(values)) if values else None,
            "paperClusterBootstrap95": cluster_bootstrap_ci(
                paired,
                lambda row: float(row[field]),
                seed=seed + offset,
                iterations=iterations,
            ),
        }

    return {
        "methodA": left,
        "methodB": right,
        "effectDirection": "methodB-minus-methodA",
        "expectedPairCount": max(len(left_records), len(right_records)),
        "observedPairCount": len(paired),
        "complete": set(left_records) == set(right_records),
        "latencyDeltaMs": summarize("latencyDeltaMs", 0),
        "tokenDelta": summarize("tokenDelta", 1),
        "llmCallDelta": summarize("llmCallDelta", 2),
        "findingCountDelta": summarize("findingCountDelta", 3),
    }


def analyze(
    comparison_rows: list[dict[str, Any]],
    prediction_rows: list[dict[str, Any]],
    *,
    split: str,
    threshold: float,
    seed: int,
    iterations: int,
    expected_repetitions: int,
    max_total_tokens: int,
    evaluated_finding_limit: int = 0,
) -> dict[str, Any]:
    rows_by_method: dict[str, list[dict[str, Any]]] = defaultdict(list)
    records_by_method: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_pair: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in comparison_rows:
        method = str(row.get("method") or "")
        pair_id = str(row.get("comparisonPairId") or "")
        if not method or not pair_id:
            raise ValueError("comparison rows require method and comparisonPairId")
        if method in by_pair[pair_id]:
            raise ValueError(f"duplicate method {method} in pair {pair_id}")
        rows_by_method[method].append(row)
        by_pair[pair_id][method] = row
    for record in prediction_rows:
        records_by_method[str(record.get("method") or "")].append(record)
    methods = sorted(rows_by_method)
    if len(methods) < 2:
        raise ValueError("at least two methods are required")
    complete_pairs = all(sorted(group) == methods for group in by_pair.values())
    threshold_consistent = all(
        bool(row.get("automaticCoverageCandidate"))
        == (str(row.get("findingId") or "") != "NO_MATCH" and float(row.get("automaticMatchScore", 0) or 0) >= threshold)
        for row in comparison_rows
    )
    summaries = [
        summarize_method(
            method,
            rows_by_method[method],
            records_by_method.get(method, []),
            seed=seed + index * 17,
            iterations=iterations,
            expected_repetitions=expected_repetitions,
            max_total_tokens=max_total_tokens,
        )
        for index, method in enumerate(methods)
    ]
    pairwise = [
        paired_summary(
            left, right, by_pair,
            seed=seed + index * 31,
            iterations=iterations,
        )
        for index, (left, right) in enumerate(itertools.combinations(methods, 2))
    ]
    efficiency_pairwise = [
        paired_efficiency_summary(
            left,
            right,
            records_by_method,
            seed=seed + index * 43,
            iterations=iterations,
        )
        for index, (left, right) in enumerate(itertools.combinations(methods, 2))
    ]
    run_counts_complete = all(
        summary["runQuality"]["observedRunCount"] == summary["runQuality"]["expectedRunCount"]
        for summary in summaries
    )
    budgets_respected = all(summary["runQuality"]["budgetExceededCount"] == 0 for summary in summaries)
    no_failures = all(summary["runQuality"]["failedRunCount"] == 0 for summary in summaries)
    checks = {
        "completeMethodPairs": complete_pairs,
        "alignmentThresholdConsistent": threshold_consistent,
        "allExpectedRunsPresent": run_counts_complete,
        "noRecordedRunFailures": no_failures,
        "tokenBudgetRespected": budgets_respected,
        "splitExplicitlyNamed": bool(split.strip()),
        "completeRunPairs": all(item["complete"] for item in efficiency_pairwise),
    }
    return {
        "schemaVersion": "peerqa_alignment_proxy_summary_v1",
        "createdAt": datetime.now(UTC).isoformat(),
        "split": split,
        "automaticAlignmentThreshold": threshold,
        "bootstrap": {"unit": "paper", "iterations": iterations, "seed": seed},
        "methodCount": len(methods),
        "paperCount": len({str(row.get("sampleId") or "") for row in comparison_rows}),
        "questionCount": len(by_pair),
        "evaluatedFindingLimit": evaluated_finding_limit or None,
        "methods": summaries,
        "pairwise": pairwise,
        "efficiencyPairwise": efficiency_pairwise,
        "qualityGate": {
            "status": "passed" if all(checks.values()) else "failed",
            "checks": checks,
        },
        "reportingBoundary": {
            "headlineEligibleAsExpertRecall": False,
            "allowedClaim": "Objective lexical evidence-alignment proxy under a frozen threshold.",
            "prohibitedClaim": "Expert review recall, correctness, or superiority without blind human labels.",
            "nextEvidence": "Complete the paired blind PeerQA annotation and report paper-cluster confidence intervals.",
        },
    }


def attach_protocol_audit(
    summary: dict[str, Any],
    protocol_path: Path,
    *,
    expected_repetitions: int,
    max_total_tokens: int,
) -> None:
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    if not isinstance(protocol, dict):
        raise ValueError("protocol must contain one JSON object")
    locked_files = protocol.get("lockedFiles") or {}
    file_checks = {}
    for name, item in locked_files.items():
        path = Path(str((item or {}).get("path") or ""))
        expected_hash = str((item or {}).get("sha256") or "")
        file_checks[str(name)] = bool(
            path.is_file() and expected_hash and sha256_file(path) == expected_hash
        )
    observed_methods = {str(item.get("method") or "") for item in summary.get("methods") or []}
    checks = {
        "paperCountMatches": int(protocol.get("paperCount", -1)) == int(summary.get("paperCount", -2)),
        "questionCountMatches": int(protocol.get("reviewerQuestionCount", -1)) == int(summary.get("questionCount", -2)),
        "methodIdsMatch": set(map(str, protocol.get("methods") or [])) == observed_methods,
        "thresholdMatches": math.isclose(
            float(protocol.get("automaticAlignmentThreshold", -1)),
            float(summary.get("automaticAlignmentThreshold", -2)),
            rel_tol=0,
            abs_tol=1e-12,
        ),
        "repetitionsMatch": int(protocol.get("repetitions", -1)) == expected_repetitions,
        "tokenBudgetMatches": int(protocol.get("maxTotalTokensPerPaper", -1)) == max_total_tokens,
        "lockedFileHashesMatch": bool(file_checks) and all(file_checks.values()),
        "validationSplitHasNoDevelopmentPapers": int(
            (protocol.get("selection") or {}).get("developmentPapersInThisSplit", -1)
        ) == 0,
        "findingLimitMatches": (
            protocol.get("fairComparisonMaxFindingsPerMethod") is None
            or int(protocol["fairComparisonMaxFindingsPerMethod"])
            == int(summary.get("evaluatedFindingLimit") or 0)
        ),
    }
    summary["protocolAudit"] = {
        "schemaVersion": protocol.get("schemaVersion"),
        "lockedAt": protocol.get("lockedAt"),
        "providerName": protocol.get("providerName"),
        "model": protocol.get("model"),
        "temperature": protocol.get("temperature"),
        "repetitions": protocol.get("repetitions"),
        "sourceCount": protocol.get("sourceCount"),
        "selection": protocol.get("selection") or {},
        "fairComparisonMaxFindingsPerMethod": protocol.get("fairComparisonMaxFindingsPerMethod"),
        "path": str(protocol_path),
        "sha256": sha256_file(protocol_path),
        "checks": checks,
        "lockedFileChecks": file_checks,
        "amendments": protocol.get("protocolAmendments") or [],
        "passed": all(checks.values()),
    }
    summary["qualityGate"]["checks"]["frozenProtocolVerified"] = all(checks.values())
    summary["qualityGate"]["status"] = (
        "passed" if all(summary["qualityGate"]["checks"].values()) else "failed"
    )


def markdown_report(summary: dict[str, Any]) -> str:
    lines = [
        "# PeerQA automatic alignment proxy",
        "",
        f"Split: `{summary['split']}`. Papers: {summary['paperCount']}. "
        f"Reviewer questions: {summary['questionCount']}. "
        f"Evaluated findings per method: {summary.get('evaluatedFindingLimit') or 'all'}.",
        "",
        "> This is a frozen lexical evidence-alignment proxy, not expert review recall or correctness.",
        "",
        "| Method | Candidate rate | Paper-cluster 95% CI | Mean score | LLM call rate | Tokens/run | Latency/run |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for method in summary["methods"]:
        alignment = method["automaticAlignment"]
        quality = method["runQuality"]
        ci = alignment["candidateRatePaperClusterBootstrap95"]
        lines.append(
            f"| {method['method']} | {alignment['candidateRate']:.3f} | "
            f"[{ci[0]:.3f}, {ci[1]:.3f}] | {alignment['meanBestMatchScore']:.3f} | "
            f"{quality['llmEscalationRate']:.1%} | {quality['meanTotalTokens']:.0f} | "
            f"{quality['meanLatencyMs']:.0f} ms |"
        )
    lines.extend(["", "## Paired effects", ""])
    for pair in summary["pairwise"]:
        ci = pair["candidateRateDeltaPaperClusterBootstrap95"]
        lines.append(
            f"- `{pair['methodB']}` minus `{pair['methodA']}`: candidate-rate delta "
            f"{pair['candidateRateDelta']:+.3f}, paper-cluster 95% CI [{ci[0]:+.3f}, {ci[1]:+.3f}], "
            f"exact McNemar p={pair['candidateDiscordance']['exactMcNemarPValue']:.4f}."
        )
    lines.extend(["", "## Paired efficiency", ""])
    for pair in summary["efficiencyPairwise"]:
        latency = pair["latencyDeltaMs"]
        tokens = pair["tokenDelta"]
        latency_ci = latency["paperClusterBootstrap95"]
        token_ci = tokens["paperClusterBootstrap95"]
        lines.append(
            f"- `{pair['methodB']}` minus `{pair['methodA']}`: latency "
            f"{latency['mean']:+.0f} ms [95% CI {latency_ci[0]:+.0f}, {latency_ci[1]:+.0f}]; "
            f"tokens {tokens['mean']:+.0f} [95% CI {token_ci[0]:+.0f}, {token_ci[1]:+.0f}]."
        )
    lines.extend([
        "",
        f"Quality gate: **{summary['qualityGate']['status']}**.",
        "",
        "Human blind annotation remains required for any expert-recall or review-quality claim.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--comparison", required=True)
    parser.add_argument("--predictions", action="append", required=True)
    parser.add_argument("--split", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--markdown-output")
    parser.add_argument("--threshold", type=float, default=0.12)
    parser.add_argument("--seed", type=int, default=20260827)
    parser.add_argument("--bootstrap-iterations", type=int, default=10000)
    parser.add_argument("--expected-repetitions", type=int, default=3)
    parser.add_argument("--max-total-tokens", type=int, default=4000)
    parser.add_argument(
        "--evaluated-finding-limit",
        type=int,
        default=0,
        help="Finding prefix used by the comparison exporter; 0 means all findings.",
    )
    parser.add_argument("--protocol", help="Optional frozen protocol JSON to hash and verify")
    args = parser.parse_args()
    comparison_path = Path(args.comparison)
    prediction_paths = [Path(path) for path in args.predictions]
    summary = analyze(
        read_jsonl(comparison_path),
        [record for path in prediction_paths for record in read_jsonl(path)],
        split=args.split,
        threshold=args.threshold,
        seed=args.seed,
        iterations=args.bootstrap_iterations,
        expected_repetitions=args.expected_repetitions,
        max_total_tokens=args.max_total_tokens,
        evaluated_finding_limit=args.evaluated_finding_limit,
    )
    if args.protocol:
        attach_protocol_audit(
            summary,
            Path(args.protocol),
            expected_repetitions=args.expected_repetitions,
            max_total_tokens=args.max_total_tokens,
        )
    summary["inputs"] = {
        "comparison": {"path": str(comparison_path), "sha256": sha256_file(comparison_path)},
        "predictions": [
            {"path": str(path), "sha256": sha256_file(path)} for path in prediction_paths
        ],
        **({
            "protocol": {
                "path": str(Path(args.protocol)),
                "sha256": sha256_file(Path(args.protocol)),
            }
        } if args.protocol else {}),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.markdown_output:
        markdown_path = Path(args.markdown_output)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(markdown_report(summary), encoding="utf-8")
    print(
        f"qualityGate={summary['qualityGate']['status']} papers={summary['paperCount']} "
        f"questions={summary['questionCount']} output={output}"
    )
    return 0 if summary["qualityGate"]["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
