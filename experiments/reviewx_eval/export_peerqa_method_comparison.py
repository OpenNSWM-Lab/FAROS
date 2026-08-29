#!/usr/bin/env python3
"""Build a paired blind PeerQA batch from repeated reviewer-method runs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    from experiments.reviewx_eval.export_peerqa_human_eval import (
        OUTPUT_FIELDS, blind_rows, build_rows, read_jsonl,
    )
except ModuleNotFoundError:
    from export_peerqa_human_eval import OUTPUT_FIELDS, blind_rows, build_rows, read_jsonl


COMPARISON_FIELDS = [*OUTPUT_FIELDS, "comparisonPairId", "selectedRepetition"]


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def select_repetitions(records: list[dict[str, Any]], seed: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    grouped: dict[tuple[str, str], dict[int, dict[str, Any]]] = defaultdict(dict)
    methods, samples = set(), set()
    for record in records:
        method = str(record.get("method") or "")
        sample_id = str(record.get("sampleId") or record.get("paperId") or "")
        repetition = int(record.get("runnerRepetition", 0) or 0)
        if not method or not sample_id:
            raise ValueError("every prediction must contain method and sampleId/paperId")
        key = (method, sample_id)
        if repetition in grouped[key]:
            raise ValueError(f"duplicate method/sample/repetition: {method}/{sample_id}/{repetition}")
        grouped[key][repetition] = record
        methods.add(method)
        samples.add(sample_id)
    if len(methods) < 2:
        raise ValueError("method comparison requires at least two methods")
    selected, choices = [], {}
    ordered_samples = sorted(
        samples,
        key=lambda sample_id: hashlib.sha256(f"{seed}|{sample_id}".encode("utf-8")).hexdigest(),
    )
    for sample_rank, sample_id in enumerate(ordered_samples):
        available_sets = [set(grouped[(method, sample_id)]) for method in sorted(methods) if (method, sample_id) in grouped]
        if len(available_sets) != len(methods):
            raise ValueError(f"sample {sample_id} is missing one or more methods")
        common = sorted(set.intersection(*available_sets))
        if not common:
            raise ValueError(f"sample {sample_id} has no common repetition across methods")
        repetition = common[sample_rank % len(common)]
        choices[sample_id] = repetition
        selected.extend(grouped[(method, sample_id)][repetition] for method in sorted(methods))
    return selected, choices


def build_comparison_rows(
    records: list[dict[str, Any]], references: list[dict[str, Any]], threshold: float, seed: int,
    *, require_all_references: bool = True, max_findings: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    selected, choices = select_repetitions(records, seed)
    selected_sample_ids = set(choices)
    selected_references = [
        reference for reference in references
        if str(reference.get("sampleId") or "") in selected_sample_ids
    ]
    if require_all_references and len(selected_references) != len(references):
        missing_sample_ids = sorted({
            str(reference.get("sampleId") or "")
            for reference in references
            if str(reference.get("sampleId") or "") not in selected_sample_ids
        })
        raise ValueError(
            "predictions do not cover every reference sample: "
            + ", ".join(missing_sample_ids[:10])
        )
    rows = []
    for record in selected:
        method_rows = build_rows(
            [record], selected_references, threshold, max_findings=max_findings,
        )
        for row in method_rows:
            reference_key = str(row["annotationId"])
            method = str(record["method"])
            sample_id = str(record.get("sampleId") or record.get("paperId"))
            row["annotationId"] = "peerqa_cmp_" + hashlib.sha256(
                f"{method}|{reference_key}".encode("utf-8")
            ).hexdigest()[:16]
            row["comparisonPairId"] = "pair_" + hashlib.sha256(reference_key.encode("utf-8")).hexdigest()[:16]
            row["selectedRepetition"] = choices[sample_id]
            rows.append(row)
    if len({row["annotationId"] for row in rows}) != len(rows):
        raise ValueError("comparison rows contain duplicate annotation IDs")
    expected_methods = sorted({str(record["method"]) for record in selected})
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["comparisonPairId"])].append(row)
    for pair_id, pair_rows in grouped.items():
        pair_methods = sorted(str(row["method"]) for row in pair_rows)
        if pair_methods != expected_methods:
            raise ValueError(f"comparison pair {pair_id} does not contain every method exactly once")
        repetitions = {int(row["selectedRepetition"]) for row in pair_rows}
        if len(repetitions) != 1:
            raise ValueError(f"comparison pair {pair_id} uses inconsistent repetitions")
    if len(grouped) != len(selected_references):
        raise ValueError(
            f"comparison pair count {len(grouped)} does not match selected reference count "
            f"{len(selected_references)}"
        )
    return rows, choices


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COMPARISON_FIELDS)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in COMPARISON_FIELDS} for row in rows)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", action="append", required=True)
    parser.add_argument("--references", required=True)
    parser.add_argument("--output-prefix", required=True)
    parser.add_argument("--threshold", type=float, default=0.12)
    parser.add_argument("--selection-seed", type=int, default=20260711)
    parser.add_argument("--shuffle-seed", type=int, default=20260712)
    parser.add_argument(
        "--max-findings",
        type=int,
        default=0,
        help="Use only the first N ranked findings per method; 0 keeps all findings.",
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Export only references for prediction samples present in every method.",
    )
    args = parser.parse_args()
    records = [row for path in args.predictions for row in read_jsonl(Path(path))]
    references = read_jsonl(Path(args.references))
    rows, choices = build_comparison_rows(
        records,
        references,
        args.threshold,
        args.selection_seed,
        require_all_references=not args.allow_partial,
        max_findings=args.max_findings,
    )
    prefix = Path(args.output_prefix)
    answer_csv = prefix.with_name(prefix.name + "_answer_key.csv")
    answer_jsonl = prefix.with_name(prefix.name + "_answer_key.jsonl")
    blind_csv = prefix.with_name(prefix.name + "_blind.csv")
    blind_jsonl = prefix.with_name(prefix.name + "_blind.jsonl")
    write_csv(answer_csv, rows)
    write_jsonl(answer_jsonl, rows)
    blinded = blind_rows(rows, args.shuffle_seed)
    write_csv(blind_csv, blinded)
    write_jsonl(blind_jsonl, blinded)
    if any(row.get("sourcePaperId") or row.get("automaticMatchScore") for row in blinded):
        raise ValueError("blind comparison batch leaks source IDs or automatic scores")
    method_counts = Counter(str(row["method"]) for row in rows)
    manifest = {
        "schemaVersion": "peerqa_method_comparison_batch_v2",
        "selectionSeed": args.selection_seed, "shuffleSeed": args.shuffle_seed,
        "threshold": args.threshold, "taskCount": len(rows),
        "pairCount": len({row["comparisonPairId"] for row in rows}),
        "sourceReferenceCount": len(references),
        "selectedReferenceCount": len(rows) // len({str(row["method"]) for row in rows}),
        "partialExport": args.allow_partial,
        "maxFindingsPerMethod": args.max_findings or None,
        "methods": sorted({str(row["method"]) for row in rows}),
        "methodTaskCounts": dict(sorted(method_counts.items())),
        "selectedRepetitions": choices,
        "files": {
            "answerKeyCsv": {"name": answer_csv.name, "sha256": sha256_path(answer_csv)},
            "answerKeyJsonl": {"name": answer_jsonl.name, "sha256": sha256_path(answer_jsonl)},
            "blindCsv": {"name": blind_csv.name, "sha256": sha256_path(blind_csv)},
            "blindJsonl": {"name": blind_jsonl.name, "sha256": sha256_path(blind_jsonl)},
        },
        "warning": "Automatic alignment is candidate generation only, not expert recall.",
    }
    prefix.with_name(prefix.name + "_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(
        f"tasks={manifest['taskCount']} pairs={manifest['pairCount']} "
        f"methods={len(manifest['methods'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
