#!/usr/bin/env python3
"""Build a small controlled CEM-Bench from local FAROS papers.

The script creates copied paper records under backend/data/papers with injected
claim-evidence errors, then writes samples.jsonl and gold_labels.jsonl for the
ReviewX eval runner.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


INJECTION_SECTION = "CEM-Bench Injected Claims"
VARIANT_MARKER = ".reviewx_eval_variant"


CORRUPTIONS = [
    {
        "kind": "numeric_mismatch",
        "suffix": "numeric",
        "claim": (
            "CEM-Bench numeric stress claim: We improve factual F1 by 97 percent "
            "and reduce inference latency to 3 ms on HotpotQA."
        ),
        "expectedRiskType": "unsupported_claim",
        "expectedSupportStatus": "unsupported",
        "severity": "blocker",
        "notes": "Injected numeric claim has no matching metric artifact or run identifier.",
    },
    {
        "kind": "missing_baseline",
        "suffix": "baseline",
        "claim": (
            "CEM-Bench baseline stress claim: We outperform speculative decoding, "
            "FlashAttention, and retrieval pruning baselines on every benchmark."
        ),
        "expectedRiskType": "unsupported_claim",
        "expectedSupportStatus": "unsupported",
        "severity": "major",
        "notes": "Injected baseline comparison lacks linked baseline evidence.",
    },
    {
        "kind": "unsupported_claim",
        "suffix": "unsupported",
        "claim": (
            "CEM-Bench unsupported stress claim: We demonstrate VeriHypo is safe for clinical, "
            "legal, and financial question answering without additional validation."
        ),
        "expectedRiskType": "unsupported_claim",
        "expectedSupportStatus": "unsupported",
        "severity": "major",
        "notes": "Injected broad deployment claim is unsupported by FAROS artifacts.",
    },
    {
        "kind": "citation_gap",
        "suffix": "citation",
        "claim": (
            "CEM-Bench citation stress claim: We propose that the critique-loop optimizer follows the "
            "established CEM theorem for pruning safety."
        ),
        "expectedRiskType": "traceability_gap",
        "expectedSupportStatus": "unsupported",
        "severity": "minor",
        "notes": "Injected method claim has no nearby citation or supporting bibliography entry.",
    },
    {
        "kind": "brief_guardrail_conflict",
        "suffix": "guardrail",
        "claim": (
            "CEM-Bench guardrail stress claim: We reduce memory by 80 percent and claim "
            "quantitative speedup without empirical measurement."
        ),
        "expectedRiskType": "unsupported_claim",
        "expectedSupportStatus": "contradicted",
        "severity": "blocker",
        "notes": "Injected claim conflicts with brief guardrails against unmeasured speedup/memory claims.",
    },
]


HARD_CORRUPTIONS = [
    {
        "kind": "semantic_citation_mismatch",
        "suffix": "hard_semantic_cite",
        "claim": (
            "CEM-Bench hard semantic citation claim: Our framework generalizes to low-resource "
            "clinical triage deployments under distribution shift \\cite{vaswani2017attention}."
        ),
        "expectedRiskType": "citation_mismatch",
        "expectedSupportStatus": "unsupported",
        "severity": "major",
        "notes": "The claim has a citation command, but the cited transformer paper does not support clinical-triage generalization.",
    },
    {
        "kind": "budget_distractor",
        "suffix": "hard_budget",
        "claim": (
            "CEM-Bench hard distractor claim: We improve factual F1 by 97 percent and reduce inference latency to 3 ms on HotpotQA. "
            "CEM-Bench hard distractor baseline claim: We outperform speculative decoding and FlashAttention baselines on every benchmark. "
            "CEM-Bench hard budget target claim: Our method is ready for autonomous use in clinical triage, legal discovery, and financial risk scoring \\cite{lewis2020retrieval}."
        ),
        "targetClaimText": (
            "CEM-Bench hard budget target claim: Our method is ready for autonomous use in clinical triage, legal discovery, and financial risk scoring."
        ),
        "expectedRiskType": "citation_mismatch",
        "expectedSupportStatus": "unsupported",
        "severity": "major",
        "notes": "The target claim is semantically unsupported while easier numeric/baseline distractors compete for the review budget.",
    },
    {
        "kind": "topic_evidence_mismatch",
        "suffix": "hard_topic",
        "claim": (
            "CEM-Bench hard topic mismatch claim: Our framework improves fairness for multilingual biomedical summarization \\cite{karpukhin2020dense}."
        ),
        "expectedRiskType": "citation_mismatch",
        "expectedSupportStatus": "unsupported",
        "severity": "major",
        "notes": "The sentence contains a citation, but the cited DPR work does not support the fairness/biomedical summarization claim.",
    },
]


SOURCE_VARIANTS = [
    {
        "suffix": "retrieval",
        "title_prefix": "TraceRAG",
        "abstract_note": "This controlled source variant emphasizes retrieval traceability and evidence logging.",
    },
    {
        "suffix": "safety",
        "title_prefix": "GuardInfer",
        "abstract_note": "This controlled source variant emphasizes safety guardrails for inference-time optimization.",
    },
    {
        "suffix": "benchmark",
        "title_prefix": "AuditBench",
        "abstract_note": "This controlled source variant emphasizes benchmark construction and auditable evaluation.",
    },
    {
        "suffix": "systems",
        "title_prefix": "ProofServe",
        "abstract_note": "This controlled source variant emphasizes systems integration and serving-time observability.",
    },
]


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def stable_paper_id(source_id: str, suffix: str) -> str:
    source_slug = hashlib.sha1(source_id.encode("utf-8")).hexdigest()[:10]
    return f"paper_cembench_{source_slug}_{suffix}"


def stable_source_variant_id(source_id: str, suffix: str) -> str:
    source_slug = hashlib.sha1(source_id.encode("utf-8")).hexdigest()[:10]
    return f"paper_cembench_source_{source_slug}_{suffix}"


def load_json_or_jsonl(path: Path) -> list[dict[str, Any]]:
    if path.suffix == ".jsonl":
        rows: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise SystemExit(f"invalid JSONL at {path}:{line_no}: {exc}") from exc
                if not isinstance(row, dict):
                    raise SystemExit(f"manifest row must be an object at {path}:{line_no}")
                rows.append(row)
        return rows

    payload = load_json(path)
    if isinstance(payload, dict):
        rows = payload.get("papers") or payload.get("sources") or payload.get("items")
        if rows is None and any(key in payload for key in ("paperId", "paper_id", "id")):
            rows = [payload]
    else:
        rows = payload
    if not isinstance(rows, list):
        raise SystemExit(f"manifest must be a JSON array, JSONL file, or object with papers/sources/items: {path}")
    if not all(isinstance(row, dict) for row in rows):
        raise SystemExit(f"manifest entries must be objects: {path}")
    return rows


def manifest_source_ids(paths: list[str] | None) -> list[str]:
    ids: list[str] = []
    for value in paths or []:
        for item in value.split(","):
            item = item.strip()
            if not item:
                continue
            path = Path(item)
            for row in load_json_or_jsonl(path):
                if row.get("include") is False:
                    continue
                paper_id = row.get("paperId") or row.get("paper_id") or row.get("sourcePaperId") or row.get("id")
                if paper_id:
                    ids.append(str(paper_id).strip())
    return [paper_id for paper_id in ids if paper_id]


def is_generated_cembench_meta(paper_id: str, meta: dict[str, Any]) -> bool:
    return bool(meta.get("cemBench")) or paper_id.startswith("paper_cembench_")


def inject_claim(main_tex: str, claim: str) -> str:
    block = (
        "\n\\section{CEM-Bench Injected Claims}\n"
        "The following paragraph is an intentionally injected benchmark perturbation.\n\n"
        f"{claim}\n\n"
    )
    if "\\bibliographystyle" in main_tex:
        return main_tex.replace("\\bibliographystyle", block + "\\bibliographystyle", 1)
    if "\\end{document}" in main_tex:
        return main_tex.replace("\\end{document}", block + "\\end{document}", 1)
    return main_tex + block


def retitle_latex(main_tex: str, title: str, abstract_note: str) -> str:
    import re

    content = re.sub(r"\\title\{[^{}]*\}", f"\\\\title{{{title}}}", main_tex, count=1)
    if "\\end{abstract}" in content:
        content = content.replace("\\end{abstract}", f" {abstract_note}\n\\end{{abstract}}", 1)
    return content


def build_source_variant(
    *,
    source_dir: Path,
    papers_dir: Path,
    source_meta: dict[str, Any],
    variant: dict[str, str],
    overwrite: bool,
) -> str:
    source_id = source_meta["id"]
    paper_id = stable_source_variant_id(source_id, variant["suffix"])
    target_dir = papers_dir / paper_id
    if target_dir.exists():
        if not overwrite:
            return paper_id
        shutil.rmtree(target_dir)
    shutil.copytree(
        source_dir,
        target_dir,
        ignore=shutil.ignore_patterns("*.pdf", "*.aux", "*.log", "*.fls", "*.fdb_latexmk", "*.zip"),
    )

    now = datetime.now(UTC).isoformat()
    source_title = source_meta.get("title", "Untitled")
    title = f"{variant['title_prefix']}: Controlled Source Variant for CEM-Review"
    meta = load_json(target_dir / "meta.json")
    meta.update({
        "id": paper_id,
        "title": title,
        "status": "completed",
        "notes": (
            f"{source_meta.get('notes') or ''}\n\n"
            f"CEM-Bench controlled clean source variant derived from {source_id}: {source_title}."
        ),
        "createdAt": now,
        "updatedAt": now,
        "cemBenchSourceVariant": {
            "sourcePaperId": source_id,
            "variantType": variant["suffix"],
            "purpose": "clean source expansion for controlled CEM-Bench phase-1 experiments",
        },
    })
    write_json(target_dir / "meta.json", meta)

    main_path = target_dir / "latex" / "main.tex"
    write_text(main_path, retitle_latex(read_text(main_path), title, variant["abstract_note"]))
    return paper_id


def build_variant(
    *,
    source_dir: Path,
    papers_dir: Path,
    source_meta: dict[str, Any],
    corruption: dict[str, str],
    overwrite: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_id = source_meta["id"]
    paper_id = stable_paper_id(source_id, corruption["suffix"])
    target_dir = papers_dir / paper_id
    if target_dir.exists():
        if not overwrite:
            raise FileExistsError(f"{target_dir} exists; rerun with --overwrite")
        shutil.rmtree(target_dir)
    shutil.copytree(source_dir, target_dir, ignore=shutil.ignore_patterns("*.pdf", "*.aux", "*.log", "*.fls", "*.fdb_latexmk", "*.zip"))

    now = datetime.now(UTC).isoformat()
    meta = load_json(target_dir / "meta.json")
    meta.update({
        "id": paper_id,
        "title": f"{source_meta.get('title', 'Untitled')} [CEM-Bench: {corruption['kind']}]",
        "status": "completed",
        "notes": f"{source_meta.get('notes') or ''}\n\nCEM-Bench corruption: {corruption['kind']} - {corruption['notes']}",
        "createdAt": now,
        "updatedAt": now,
        "cemBench": {
            "sourcePaperId": source_id,
            "corruptionType": corruption["kind"],
            "targetClaimText": corruption["claim"],
        },
    })
    write_json(target_dir / "meta.json", meta)

    main_path = target_dir / "latex" / "main.tex"
    write_text(main_path, inject_claim(read_text(main_path), corruption["claim"]))

    sample = {
        "sampleId": f"{source_id}_{corruption['suffix']}",
        "paperId": paper_id,
        "sourcePaperId": source_id,
        "sampleType": f"cembench_{corruption['kind']}",
        "title": meta["title"],
    }
    gold = {
        "sampleId": sample["sampleId"],
        "paperId": paper_id,
        "corruptionType": corruption["kind"],
        "targetClaimText": corruption.get("targetClaimText", corruption["claim"]),
        "expectedRiskType": corruption["expectedRiskType"],
        "expectedSupportStatus": corruption["expectedSupportStatus"],
        "targetSection": INJECTION_SECTION,
        "severity": corruption["severity"],
        "notes": corruption["notes"],
    }
    return sample, gold


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def parse_source_ids(args: argparse.Namespace, papers_dir: Path) -> list[str]:
    ids: list[str] = []
    for value in args.source_paper_id or []:
        ids.extend([item.strip() for item in value.split(",") if item.strip()])
    if args.source_paper_ids:
        ids.extend([item.strip() for item in args.source_paper_ids.split(",") if item.strip()])
    ids.extend(manifest_source_ids(args.source_manifest))

    if args.auto_discover:
        for meta_path in sorted(papers_dir.glob("*/meta.json")):
            if (meta_path.parent / VARIANT_MARKER).exists():
                continue
            try:
                meta = load_json(meta_path)
            except (json.JSONDecodeError, OSError):
                continue
            paper_id = str(meta.get("id") or meta_path.parent.name)
            if meta.get("cemBench"):
                continue
            if paper_id.startswith("paper_cembench_") and not meta.get("cemBenchSourceVariant"):
                continue
            if args.real_sources_only and (meta.get("cemBenchSourceVariant") or paper_id.startswith("paper_cembench_")):
                continue
            ids.append(paper_id)

    deduped = []
    seen = set()
    for paper_id in ids:
        if args.real_sources_only:
            meta_path = papers_dir / paper_id / "meta.json"
            if not meta_path.is_file():
                raise SystemExit(f"source paper not found: {papers_dir / paper_id}")
            meta = load_json(meta_path)
            if (
                meta.get("cemBenchSourceVariant")
                or is_generated_cembench_meta(paper_id, meta)
                or (meta_path.parent / VARIANT_MARKER).exists()
            ):
                continue
        if paper_id not in seen:
            deduped.append(paper_id)
            seen.add(paper_id)
    return deduped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-paper-id", action="append", help="Source paper id. May be repeated or comma-separated.")
    parser.add_argument("--source-paper-ids", help="Comma-separated source paper ids.")
    parser.add_argument(
        "--source-manifest",
        action="append",
        help=(
            "JSON/JSONL source manifest. Rows may use paperId, paper_id, "
            "sourcePaperId, or id; include=false skips a row."
        ),
    )
    parser.add_argument("--auto-discover", action="store_true", help="Use all non-corrupted local papers as sources.")
    parser.add_argument(
        "--real-sources-only",
        action="store_true",
        help="Exclude CEM-Bench corruptions and controlled source variants from selected sources.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print selected source papers and exit without copying or injecting benchmark samples.",
    )
    parser.add_argument(
        "--controlled-source-variants",
        type=int,
        default=0,
        help="Create this many clean source variants from the first source before building corruptions.",
    )
    parser.add_argument(
        "--corruption-suite",
        choices=["standard", "hard", "all"],
        default="standard",
        help="Which corruption set to generate.",
    )
    parser.add_argument("--backend-data", default="backend/data")
    parser.add_argument("--output-dir", default="experiments/reviewx_eval/cem_bench/generated")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    backend_data = Path(args.backend_data)
    papers_dir = backend_data / "papers"
    source_ids = parse_source_ids(args, papers_dir)
    if not source_ids:
        raise SystemExit("no source papers selected; pass --source-paper-id, --source-manifest, or --auto-discover")

    if args.dry_run:
        print(f"selectedSources={len(source_ids)}")
        for source_id in source_ids:
            meta = load_json(papers_dir / source_id / "meta.json")
            print(f"{source_id}\t{meta.get('title') or ''}")
        return 0

    if args.controlled_source_variants:
        base_id = source_ids[0]
        base_dir = papers_dir / base_id
        if not base_dir.is_dir():
            raise SystemExit(f"source paper not found: {base_dir}")
        base_meta = load_json(base_dir / "meta.json")
        variant_count = min(args.controlled_source_variants, len(SOURCE_VARIANTS))
        for variant in SOURCE_VARIANTS[:variant_count]:
            source_ids.append(build_source_variant(
                source_dir=base_dir,
                papers_dir=papers_dir,
                source_meta=base_meta,
                variant=variant,
                overwrite=args.overwrite,
            ))

    samples = []
    gold_labels: list[dict[str, Any]] = []

    if args.corruption_suite == "hard":
        corruptions = HARD_CORRUPTIONS
    elif args.corruption_suite == "all":
        corruptions = [*CORRUPTIONS, *HARD_CORRUPTIONS]
    else:
        corruptions = CORRUPTIONS

    for source_id in source_ids:
        source_dir = papers_dir / source_id
        if not source_dir.is_dir():
            raise SystemExit(f"source paper not found: {source_dir}")
        source_meta = load_json(source_dir / "meta.json")
        if source_meta.get("cemBench") or (source_dir / VARIANT_MARKER).exists():
            raise SystemExit(f"refusing to use corrupted CEM-Bench paper as source: {source_id}")

        samples.append({
            "sampleId": f"{source_id}_clean",
            "paperId": source_id,
            "sourcePaperId": source_id,
            "sampleType": "faros_clean",
            "title": source_meta.get("title"),
        })

        for corruption in corruptions:
            sample, gold = build_variant(
                source_dir=source_dir,
                papers_dir=papers_dir,
                source_meta=source_meta,
                corruption=corruption,
                overwrite=args.overwrite,
            )
            samples.append(sample)
            gold_labels.append(gold)

    output_dir = Path(args.output_dir)
    write_jsonl(output_dir / "samples.jsonl", samples)
    write_jsonl(output_dir / "gold_labels.jsonl", gold_labels)

    print(f"samples={len(samples)} goldLabels={len(gold_labels)} outputDir={output_dir}")
    for sample in samples:
        print(f"{sample['sampleId']} -> {sample['paperId']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
