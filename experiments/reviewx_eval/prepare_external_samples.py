#!/usr/bin/env python3
"""Normalize external review datasets into CEM-Bench sample manifests.

This helper does not download datasets. It converts user-provided JSONL exports
from PeerRead, OpenReview, MOPRD, or custom sources into the sample/gold schema
used by run_eval.py and score_eval.py.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line and not line.startswith("#"):
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def normalize_sample(row: dict[str, Any], source: str) -> dict[str, Any]:
    paper_id = str(row.get("paperId") or row.get("paper_id") or row.get("id") or "")
    sample_id = str(row.get("sampleId") or row.get("sample_id") or f"{source}_{paper_id}")
    if not paper_id:
        raise ValueError(f"Missing paperId in row: {row}")
    return {
        "sampleId": sample_id,
        "paperId": paper_id,
        "sourcePaperId": row.get("sourcePaperId") or row.get("source_paper_id") or paper_id,
        "sampleType": row.get("sampleType") or row.get("sample_type") or source,
        "title": row.get("title") or row.get("paperTitle") or row.get("paper_title"),
        "externalSource": source,
        "externalId": row.get("externalId") or row.get("forum") or row.get("submission_id"),
    }


def normalize_gold(row: dict[str, Any], sample: dict[str, Any]) -> dict[str, Any] | None:
    issue = row.get("gold") or row.get("label") or row
    target_claim = issue.get("targetClaimText") or issue.get("target_claim") or issue.get("claim")
    expected_support = issue.get("expectedSupportStatus") or issue.get("supportStatus") or issue.get("support_status")
    corruption = issue.get("corruptionType") or issue.get("issueType") or issue.get("issue_type")
    if not target_claim and not expected_support and not corruption:
        return None
    return {
        "sampleId": sample["sampleId"],
        "paperId": sample["paperId"],
        "corruptionType": corruption or "external_review_issue",
        "targetClaimText": target_claim or "",
        "expectedRiskType": issue.get("expectedRiskType") or issue.get("riskType") or issue.get("risk_type"),
        "expectedSupportStatus": expected_support or "unsupported",
        "targetSection": issue.get("targetSection") or issue.get("section"),
        "severity": issue.get("severity") or "major",
        "notes": issue.get("notes") or issue.get("reviewComment") or issue.get("review_comment") or "",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="External JSONL export")
    parser.add_argument("--source", required=True, choices=["peerread", "openreview", "moprd", "custom"])
    parser.add_argument("--samples-output", default="experiments/reviewx_eval/cem_bench/external_samples.jsonl")
    parser.add_argument("--gold-output", default="experiments/reviewx_eval/cem_bench/external_gold_labels.jsonl")
    args = parser.parse_args()

    samples = []
    gold_labels = []
    for row in read_jsonl(Path(args.input)):
        sample = normalize_sample(row, args.source)
        samples.append(sample)
        gold = normalize_gold(row, sample)
        if gold:
            gold_labels.append(gold)

    write_jsonl(Path(args.samples_output), samples)
    write_jsonl(Path(args.gold_output), gold_labels)
    print(f"samples={len(samples)} goldLabels={len(gold_labels)}")
    print(f"samplesOutput={args.samples_output}")
    print(f"goldOutput={args.gold_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
