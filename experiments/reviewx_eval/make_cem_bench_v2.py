#!/usr/bin/env python3
"""Build leakage-resistant, in-place CEM-Bench v2 paper variants.

Unlike the v1 engineering benchmark, v2 does not expose corruption names in
paper IDs, metadata, titles, or manuscript section names. Gold labels and the
variant registry remain outside the ReviewX artifact collection path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


VARIANT_MARKER = ".reviewx_eval_variant"
SECTION_RE = re.compile(r"\\(?:section|subsection)\*?\{([^{}]+)\}", re.IGNORECASE)
CITATION_RE = re.compile(r"\\cite(?:p|t)?(?:\[[^\]]*\])?\{([^{}]+)\}")


STANDARD_CORRUPTIONS: list[dict[str, Any]] = [
    {
        "kind": "numeric_mismatch",
        "sectionKeywords": ["experiment", "result", "evaluation", "analysis"],
        "templates": [
            "Across all evaluated settings, our method improves the primary score by 37.4 percent and reduces end-to-end latency to 3 ms.",
            "The proposed approach achieves a 41.7 percent quality gain while requiring only 2 ms of additional latency.",
        ],
        "expectedRiskType": "unsupported_claim",
        "expectedSupportStatus": "artifact_absent",
        "severity": "major",
        "notes": "Natural-position numeric claim lacks an imported structured run or metric artifact.",
    },
    {
        "kind": "missing_baseline",
        "sectionKeywords": ["experiment", "result", "evaluation", "comparison"],
        "templates": [
            "Under an identical compute budget, our method outperforms every competitive baseline on all reported benchmarks.",
            "Our approach consistently exceeds all existing baselines under matched data, model, and inference budgets.",
        ],
        "expectedRiskType": "unsupported_claim",
        "expectedSupportStatus": "artifact_absent",
        "severity": "major",
        "notes": "Universal baseline comparison has no imported matched-budget baseline artifact.",
    },
    {
        "kind": "unsupported_overclaim",
        "sectionKeywords": ["conclusion", "discussion", "analysis", "introduction"],
        "templates": [
            "These results demonstrate that the system is safe for autonomous clinical, legal, and financial decision making without additional validation.",
            "The evidence establishes reliable deployment across unseen high-stakes domains without further domain-specific evaluation.",
        ],
        "expectedRiskType": "unsupported_claim",
        "expectedSupportStatus": "unsupported",
        "severity": "major",
        "notes": "Broad high-stakes deployment claim exceeds the available paper evidence.",
    },
    {
        "kind": "citation_gap",
        "sectionKeywords": ["related", "introduction", "method", "background"],
        "templates": [
            "Prior work has established that retrieval grounding eliminates factual errors in long-form scientific reasoning.",
            "Existing studies prove that iterative critique guarantees faithful reasoning under distribution shift.",
        ],
        "expectedRiskType": "traceability_gap",
        "expectedSupportStatus": "needs_human_verification",
        "severity": "minor",
        "notes": "Strong literature claim is inserted without a nearby citation.",
    },
    {
        "kind": "unmeasured_efficiency_claim",
        "sectionKeywords": ["conclusion", "experiment", "result", "analysis"],
        "templates": [
            "The proposed system reduces memory consumption by 80 percent and provides a quantitative speedup without any loss in accuracy.",
            "Our implementation cuts inference cost by 75 percent while preserving all task performance across settings.",
        ],
        "expectedRiskType": "unsupported_claim",
        "expectedSupportStatus": "artifact_absent",
        "severity": "major",
        "notes": "Efficiency claim has no imported measurement artifact.",
    },
]


HARD_CORRUPTIONS: list[dict[str, Any]] = [
    {
        "kind": "semantic_citation_mismatch",
        "sectionKeywords": ["related", "introduction", "method", "background"],
        "templates": [
            "The cited evidence establishes that our framework generalizes to low-resource clinical triage under distribution shift \\cite{{{citation}}}.",
            "Existing evidence proves that the method remains fair for multilingual biomedical summarization \\cite{{{citation}}}.",
        ],
        "expectedRiskType": "citation_mismatch",
        "expectedSupportStatus": "unsupported",
        "severity": "major",
        "notes": "An existing bibliography key is attached to an unrelated high-stakes domain claim.",
    },
    {
        "kind": "budget_distractor",
        "sectionKeywords": ["experiment", "result", "evaluation", "analysis"],
        "templates": [
            (
                "Our method improves the primary score by 37.4 percent and reduces latency to 3 ms. "
                "It also outperforms every baseline under matched compute. "
                "More importantly, the cited evidence establishes readiness for autonomous clinical triage, legal discovery, and financial risk scoring \\cite{{{citation}}}."
            ),
            (
                "The approach achieves a 41.7 percent gain and cuts memory by 75 percent. "
                "It dominates all existing systems under identical budgets. "
                "The cited work further proves safe deployment in unseen medical and legal environments \\cite{{{citation}}}."
            ),
        ],
        "targetTemplates": [
            "More importantly, the cited evidence establishes readiness for autonomous clinical triage, legal discovery, and financial risk scoring.",
            "The cited work further proves safe deployment in unseen medical and legal environments.",
        ],
        "expectedRiskType": "citation_mismatch",
        "expectedSupportStatus": "unsupported",
        "severity": "major",
        "notes": "A semantic citation target competes with easier numeric and baseline distractors.",
    },
    {
        "kind": "topic_evidence_mismatch",
        "sectionKeywords": ["related", "introduction", "method", "discussion"],
        "templates": [
            "The cited study shows that this framework improves fairness for multilingual biomedical summarization \\cite{{{citation}}}.",
            "This evidence demonstrates robust transfer to clinical decision support in low-resource languages \\cite{{{citation}}}.",
        ],
        "expectedRiskType": "citation_mismatch",
        "expectedSupportStatus": "unsupported",
        "severity": "major",
        "notes": "A real citation key is reused for an unrelated topic claim.",
    },
]


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"expected object in {path}")
    return payload


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_manifest(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        rows = []
        with path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError(f"manifest row must be an object: {path}:{line_no}")
                rows.append(row)
        return rows
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("papers") or payload.get("sources") or payload.get("items") or [payload]
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        raise ValueError(f"invalid manifest: {path}")
    return payload


def source_ids(args: argparse.Namespace) -> list[str]:
    ids = list(args.source_paper_id or [])
    for manifest in args.source_manifest or []:
        for row in load_manifest(Path(manifest)):
            if row.get("include") is False:
                continue
            paper_id = row.get("paperId") or row.get("paper_id") or row.get("sourcePaperId") or row.get("id")
            if paper_id:
                ids.append(str(paper_id))
    seen: set[str] = set()
    result = []
    for paper_id in ids:
        if paper_id not in seen:
            result.append(paper_id)
            seen.add(paper_id)
    return result[: args.max_sources or None]


def opaque_id(source_id: str, corruption: str, seed: int) -> str:
    digest = hashlib.sha256(f"{seed}|{source_id}|{corruption}".encode()).hexdigest()[:16]
    return f"paper_{digest}"


def opaque_sample_id(source_id: str, corruption: str, seed: int) -> str:
    digest = hashlib.sha256(f"sample|{seed}|{source_id}|{corruption}".encode()).hexdigest()[:18]
    return f"sample_{digest}"


def deterministic_index(source_id: str, corruption: str, seed: int, size: int) -> int:
    digest = hashlib.sha256(f"choice|{seed}|{source_id}|{corruption}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % size


def latex_files(paper_dir: Path) -> list[Path]:
    latex_dir = paper_dir / "latex"
    return sorted(path for path in latex_dir.rglob("*.tex") if path.is_file())


def citation_keys(paths: list[Path]) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for path in paths:
        content = path.read_text(encoding="utf-8", errors="replace")
        for group in CITATION_RE.findall(content):
            for key in group.split(","):
                key = key.strip()
                if key and key not in seen:
                    seen.add(key)
                    keys.append(key)
    return keys


def section_candidates(paths: list[Path]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for path in paths:
        content = path.read_text(encoding="utf-8", errors="replace")
        matches = list(SECTION_RE.finditer(content))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
            candidates.append({
                "path": path,
                "content": content,
                "section": re.sub(r"\\[a-zA-Z]+\{([^{}]+)\}", r"\1", match.group(1)).strip(),
                "insertStart": match.end(),
                "insertEnd": end,
            })
    return candidates


def choose_section(
    candidates: list[dict[str, Any]],
    keywords: list[str],
    source_id: str,
    corruption: str,
    seed: int,
) -> dict[str, Any]:
    if not candidates:
        raise ValueError("paper has no section or subsection command")
    scored = []
    for candidate in candidates:
        haystack = f"{candidate['section']} {candidate['path'].stem}".lower()
        score = sum(2 if keyword in candidate["section"].lower() else 1 for keyword in keywords if keyword in haystack)
        scored.append((score, candidate))
    best_score = max(score for score, _candidate in scored)
    best = [candidate for score, candidate in scored if score == best_score]
    return best[deterministic_index(source_id, corruption, seed, len(best))]


def render_claim(spec: dict[str, Any], source_id: str, seed: int, citations: list[str]) -> tuple[str, str]:
    index = deterministic_index(source_id, spec["kind"], seed, len(spec["templates"]))
    citation = citations[deterministic_index(source_id, spec["kind"] + "|citation", seed, len(citations))] if citations else "missing-reference"
    claim = spec["templates"][index].format(citation=citation)
    target_templates = spec.get("targetTemplates")
    target = target_templates[index] if target_templates else claim
    target = CITATION_RE.sub("", target)
    target = re.sub(r"\s+", " ", target).strip()
    target = re.sub(r"\s+([.,;:!?])", r"\1", target)
    return claim, target


def inject_into_section(candidate: dict[str, Any], claim: str) -> tuple[str, int]:
    content = candidate["content"]
    position = candidate["insertEnd"]
    segment = content[candidate["insertStart"]:candidate["insertEnd"]]
    terminators = [r"\bibliography", r"\begin{thebibliography}", r"\end{document}"]
    relative_positions = [segment.find(token) for token in terminators if segment.find(token) >= 0]
    if relative_positions:
        position = candidate["insertStart"] + min(relative_positions)
    block = f"\n\n{claim}\n"
    target_line = content.count("\n", 0, position) + 3
    return content[:position].rstrip() + block + content[position:].lstrip("\n"), target_line


def copy_variant(
    source_dir: Path,
    target_dir: Path,
    paper_id: str,
    spec: dict[str, Any],
    seed: int,
    overwrite: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if target_dir.exists():
        if not overwrite:
            raise FileExistsError(f"{target_dir} exists; pass --overwrite")
        shutil.rmtree(target_dir)
    shutil.copytree(
        source_dir,
        target_dir,
        ignore=shutil.ignore_patterns("*.pdf", "*.aux", "*.log", "*.fls", "*.fdb_latexmk", "*.zip"),
    )

    source_meta = load_json(source_dir / "meta.json")
    meta = load_json(target_dir / "meta.json")
    meta.pop("cemBench", None)
    meta.pop("cemBenchSourceVariant", None)
    meta["id"] = paper_id
    meta["createdAt"] = datetime.now(UTC).isoformat()
    meta["updatedAt"] = meta["createdAt"]
    write_json(target_dir / "meta.json", meta)

    paths = latex_files(target_dir)
    citations = citation_keys(paths)
    claim, target_claim = render_claim(spec, str(source_meta["id"]), seed, citations)
    candidate = choose_section(
        section_candidates(paths),
        list(spec["sectionKeywords"]),
        str(source_meta["id"]),
        str(spec["kind"]),
        seed,
    )
    updated, target_line = inject_into_section(candidate, claim)
    candidate["path"].write_text(updated, encoding="utf-8")
    relative_file = candidate["path"].relative_to(target_dir).as_posix()

    marker = {
        "schemaVersion": "reviewx_eval_variant_v2",
        "sourcePaperId": source_meta["id"],
        "corruptionType": spec["kind"],
        "targetFile": relative_file,
        "targetSection": candidate["section"],
        "targetLine": target_line,
    }
    write_json(target_dir / VARIANT_MARKER, marker)
    return marker, {
        "targetClaimText": target_claim,
        "expectedRiskType": spec["expectedRiskType"],
        "expectedSupportStatus": spec["expectedSupportStatus"],
        "severity": spec["severity"],
        "notes": spec["notes"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-paper-id", action="append")
    parser.add_argument("--source-manifest", action="append")
    parser.add_argument("--max-sources", type=int)
    parser.add_argument("--corruption-suite", choices=["standard", "hard", "all"], default="all")
    parser.add_argument("--seed", type=int, default=20260710)
    parser.add_argument("--backend-data", default="backend/data")
    parser.add_argument("--output-dir", default="experiments/reviewx_eval/cem_bench/v2_generated")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    papers_dir = Path(args.backend_data) / "papers"
    selected = source_ids(args)
    if not selected:
        raise SystemExit("no sources selected")
    specs = (
        STANDARD_CORRUPTIONS if args.corruption_suite == "standard"
        else HARD_CORRUPTIONS if args.corruption_suite == "hard"
        else [*STANDARD_CORRUPTIONS, *HARD_CORRUPTIONS]
    )
    for paper_id in selected:
        source_dir = papers_dir / paper_id
        if not (source_dir / "meta.json").is_file():
            raise SystemExit(f"source not found: {source_dir}")
        meta = load_json(source_dir / "meta.json")
        if meta.get("cemBench") or meta.get("cemBenchSourceVariant") or (source_dir / VARIANT_MARKER).exists():
            raise SystemExit(f"refusing generated benchmark source: {paper_id}")
        if not section_candidates(latex_files(source_dir)):
            raise SystemExit(f"source has no detectable sections: {paper_id}")

    if args.dry_run:
        print(f"sources={len(selected)} corruptions={len(specs)} variants={len(selected) * len(specs)}")
        for paper_id in selected:
            print(paper_id)
        return 0

    samples: list[dict[str, Any]] = []
    gold_rows: list[dict[str, Any]] = []
    registry: list[dict[str, Any]] = []
    for source_id in selected:
        source_dir = papers_dir / source_id
        source_meta = load_json(source_dir / "meta.json")
        samples.append({
            "sampleId": f"clean_{hashlib.sha256(source_id.encode()).hexdigest()[:18]}",
            "paperId": source_id,
            "sourcePaperId": source_id,
            "sampleType": "clean_control",
            "title": source_meta.get("title"),
        })
        for spec in specs:
            paper_id = opaque_id(source_id, str(spec["kind"]), args.seed)
            sample_id = opaque_sample_id(source_id, str(spec["kind"]), args.seed)
            marker, gold_fields = copy_variant(
                source_dir,
                papers_dir / paper_id,
                paper_id,
                spec,
                args.seed,
                args.overwrite,
            )
            samples.append({
                "sampleId": sample_id,
                "paperId": paper_id,
                "sourcePaperId": source_id,
                "sampleType": "paper_variant",
                "title": source_meta.get("title"),
            })
            gold_rows.append({
                "sampleId": sample_id,
                "paperId": paper_id,
                "corruptionType": spec["kind"],
                "targetClaimText": gold_fields["targetClaimText"],
                "expectedRiskType": gold_fields["expectedRiskType"],
                "expectedSupportStatus": gold_fields["expectedSupportStatus"],
                "targetSection": marker["targetSection"],
                "targetFile": marker["targetFile"],
                "targetLine": marker["targetLine"],
                "severity": gold_fields["severity"],
                "notes": gold_fields["notes"],
            })
            registry.append({"paperId": paper_id, **marker})

    output_dir = Path(args.output_dir)
    write_jsonl(output_dir / "samples.jsonl", samples)
    write_jsonl(output_dir / "gold_labels.jsonl", gold_rows)
    write_json(output_dir / "variant_registry.json", {"schemaVersion": "cem_bench_v2", "seed": args.seed, "variants": registry})
    print(f"sources={len(selected)} samples={len(samples)} goldLabels={len(gold_rows)} outputDir={output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
