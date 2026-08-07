#!/usr/bin/env python3
"""Validate that CEM-Bench v2 variants do not expose benchmark labels to ReviewX."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


FORBIDDEN_PATTERNS = [
    r"cem[-_ ]?bench",
    r"injected claims?",
    r"benchmark perturbation",
    r"numeric_mismatch",
    r"missing_baseline",
    r"unsupported_overclaim",
    r"citation_gap",
    r"semantic_citation_mismatch",
    r"budget_distractor",
    r"topic_evidence_mismatch",
    r"unmeasured_efficiency_claim",
]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", required=True)
    parser.add_argument("--gold", required=True)
    parser.add_argument("--backend-data", default="backend/data")
    args = parser.parse_args()

    samples = read_jsonl(Path(args.samples))
    gold_rows = read_jsonl(Path(args.gold))
    gold_by_sample = {str(row["sampleId"]): row for row in gold_rows}
    papers_dir = Path(args.backend_data) / "papers"
    leaks: list[str] = []
    checked = 0

    for sample in samples:
        gold = gold_by_sample.get(str(sample.get("sampleId")))
        if not gold:
            continue
        checked += 1
        paper_id = str(sample["paperId"])
        paper_dir = papers_dir / paper_id
        meta_path = paper_dir / "meta.json"
        if not meta_path.is_file():
            leaks.append(f"{paper_id}: missing meta.json")
            continue
        if any(token in paper_id.lower() for token in ["cembench", "corrupt", str(gold["corruptionType"]).lower()]):
            leaks.append(f"{paper_id}: paper ID exposes benchmark information")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if "cemBench" in meta or "cemBenchSourceVariant" in meta:
            leaks.append(f"{paper_id}: benchmark metadata is visible in meta.json")
        visible = [json.dumps(meta, ensure_ascii=False)]
        visible.extend(
            path.read_text(encoding="utf-8", errors="replace")
            for path in sorted((paper_dir / "latex").rglob("*.tex"))
        )
        corpus = "\n".join(visible)
        for pattern in FORBIDDEN_PATTERNS:
            if re.search(pattern, corpus, re.IGNORECASE):
                leaks.append(f"{paper_id}: visible content matches /{pattern}/")
        claim = str(gold.get("targetClaimText") or "")
        claim_tokens = [token for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{3,}", claim.lower())]
        if claim_tokens:
            overlap = sum(1 for token in set(claim_tokens) if token in corpus.lower()) / len(set(claim_tokens))
            if overlap < 0.55:
                leaks.append(f"{paper_id}: target claim not recoverable from manuscript (token overlap={overlap:.2f})")
        if str(gold.get("targetSection") or "").lower() == "cem-bench injected claims":
            leaks.append(f"{paper_id}: forbidden synthetic target section")
        marker = paper_dir / ".reviewx_eval_variant"
        if not marker.is_file():
            leaks.append(f"{paper_id}: missing private variant marker")

    if leaks:
        print(f"checkedVariants={checked} leaks={len(leaks)}")
        for leak in leaks:
            print(f"LEAK {leak}")
        return 1
    print(f"checkedVariants={checked} leaks=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
