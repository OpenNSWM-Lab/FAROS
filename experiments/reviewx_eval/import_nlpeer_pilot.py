#!/usr/bin/env python3
"""Import a deterministic NLPeer v2 pilot without exposing reviewer identities."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "experiments" / "reviewx_eval" / "external_data" / "nlpeer-v2"
DEFAULT_OUTPUT = DEFAULT_INPUT / "faros_pilot"
DEFAULT_BACKEND_DATA = ROOT / "backend" / "data"
DATASET_URL = "https://tudatalib.ulb.tu-darmstadt.de/handle/tudatalib/4459"
DEFAULT_LICENSE = "CC-BY-NC-4.0"
TEXT_NODE_TYPES = {"article-title", "title", "heading", "abstract", "p", "paragraph", "list", "list_item", "item", "caption"}
NEGATIVE_FIELD_HINTS = {
    "weak", "limitation", "concern", "question", "comment", "suggest", "request",
    "error", "typo", "improve", "major", "minor", "soundness", "clarity",
}
POSITIVE_FIELD_HINTS = {"strength", "summary", "confidence", "expertise"}


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def stable_id(prefix: str, value: str) -> str:
    return prefix + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def version_number(path: Path) -> int:
    match = re.fullmatch(r"v(\d+)", path.name)
    return int(match.group(1)) if match else -1


def discover_papers(input_dir: Path, datasets: set[str]) -> list[dict[str, Any]]:
    papers = []
    for dataset_dir in sorted(path for path in input_dir.iterdir() if path.is_dir()):
        if datasets and dataset_dir.name not in datasets:
            continue
        data_dir = dataset_dir / "data"
        if not data_dir.is_dir():
            continue
        for paper_dir in sorted(path for path in data_dir.iterdir() if path.is_dir()):
            versions = sorted(
                (path for path in paper_dir.iterdir() if path.is_dir() and version_number(path) >= 0),
                key=version_number,
            )
            candidates = [path for path in versions if (path / "paper.itg.json").is_file() and (path / "reviews.json").is_file()]
            if not candidates:
                continue
            version_dir = candidates[-1]
            papers.append({
                "dataset": dataset_dir.name,
                "sourcePaperId": paper_dir.name,
                "version": version_number(version_dir),
                "paperDir": paper_dir,
                "versionDir": version_dir,
            })
    return papers


def load_itg(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    nodes = payload.get("nodes") if isinstance(payload, dict) else None
    if not isinstance(nodes, list):
        raise ValueError(f"ITG file has no nodes list: {path}")
    return [node for node in nodes if isinstance(node, dict)]


def paper_title(nodes: list[dict[str, Any]], fallback: str) -> str:
    for node in nodes:
        if node.get("ntype") == "article-title" and clean_text(node.get("content")):
            return clean_text(node["content"])
    return fallback


def manuscript(nodes: list[dict[str, Any]]) -> str:
    parts = ["\\documentclass{article}", "\\begin{document}"]
    for node in nodes:
        node_type = str(node.get("ntype") or "")
        content = clean_text(node.get("content"))
        if not content or node_type not in TEXT_NODE_TYPES or node_type == "article-title":
            continue
        content = content.replace("\\", " ").replace("{", "(").replace("}", ")").replace("%", " percent ")
        if node_type in {"title", "heading"}:
            parts.append(f"\\section{{{content}}}")
        else:
            parts.append(content)
    parts.append("\\end{document}")
    return "\n\n".join(parts) + "\n"


def split_review_text(text: str, *, max_chars: int = 1800) -> list[str]:
    paragraphs = [
        clean_text(part)
        for part in re.split(r"\n\s*\n|^\s*[-*•]\s+", text, flags=re.MULTILINE)
        if clean_text(part)
    ]
    result = []
    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            if len(paragraph) >= 30:
                result.append(paragraph)
            continue
        sentences = re.split(r"(?<=[.!?])\s+", paragraph)
        current = ""
        for sentence in sentences:
            if current and len(current) + len(sentence) + 1 > max_chars:
                result.append(current)
                current = sentence
            else:
                current = f"{current} {sentence}".strip()
        if len(current) >= 30:
            result.append(current)
    return result


def review_units(review: dict[str, Any]) -> list[dict[str, str]]:
    report = review.get("report") or {}
    if not isinstance(report, dict):
        return []
    units = []
    for field, raw_text in report.items():
        field_name = str(field).lower().replace("_", " ")
        text = clean_text(raw_text)
        is_negative = any(hint in field_name for hint in NEGATIVE_FIELD_HINTS)
        is_positive = any(hint in field_name for hint in POSITIVE_FIELD_HINTS)
        if not text or (is_positive and not is_negative):
            continue
        field_is_target = field_name == "main" or is_negative
        if not field_is_target:
            continue
        for index, unit in enumerate(split_review_text(str(raw_text))):
            units.append({"sourceField": str(field), "unitIndex": str(index), "text": unit})
    return units


def load_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    version_dir = Path(candidate["versionDir"])
    nodes = load_itg(version_dir / "paper.itg.json")
    reviews = json.loads((version_dir / "reviews.json").read_text(encoding="utf-8"))
    if not isinstance(reviews, list):
        raise ValueError(f"reviews.json is not a list: {version_dir}")
    units = []
    for review_index, review in enumerate(reviews):
        if not isinstance(review, dict):
            continue
        opaque_review_id = stable_id(
            "review_", f"{candidate['dataset']}|{candidate['sourcePaperId']}|{review.get('rid', review_index)}",
        )
        for unit in review_units(review):
            units.append({**unit, "reviewId": opaque_review_id})
    metadata_path = version_dir / "meta.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.is_file() else {}
    return {**candidate, "nodes": nodes, "units": units, "metadata": metadata}


def select_candidates(candidates: list[dict[str, Any]], max_papers: int, max_per_dataset: int) -> list[dict[str, Any]]:
    ranked = sorted(candidates, key=lambda row: (len(row["units"]), row["dataset"], row["sourcePaperId"]), reverse=True)
    selected, counts = [], Counter()
    for candidate in ranked:
        if not candidate["units"] or counts[candidate["dataset"]] >= max_per_dataset:
            continue
        selected.append(candidate)
        counts[candidate["dataset"]] += 1
        if len(selected) >= max_papers:
            break
    return selected


def import_pilot(
    input_dir: Path, output_dir: Path, backend_data: Path, *, datasets: set[str],
    max_papers: int, max_per_dataset: int, dev_papers: int, overwrite: bool,
) -> dict[str, Any]:
    discovered = discover_papers(input_dir, datasets)
    loaded = [load_candidate(candidate) for candidate in discovered]
    selected = select_candidates(loaded, max_papers, max_per_dataset)
    if not selected:
        raise ValueError("no NLPeer papers with ITG text and review units were found")
    samples, references = [], []
    for index, candidate in enumerate(selected):
        source_key = f"{candidate['dataset']}/{candidate['sourcePaperId']}/v{candidate['version']}"
        paper_id = stable_id("paper_nlpeer_", source_key)
        sample_id = paper_id.replace("paper_", "sample_", 1)
        split = "development" if index < dev_papers else "held_out"
        title = paper_title(candidate["nodes"], candidate["sourcePaperId"])
        license_text = clean_text(candidate["metadata"].get("license")) or DEFAULT_LICENSE
        paper_dir = backend_data / "papers" / paper_id
        if paper_dir.exists() and not overwrite:
            raise FileExistsError(f"paper already exists: {paper_dir}; pass --overwrite")
        if paper_dir.exists():
            shutil.rmtree(paper_dir)
        (paper_dir / "latex").mkdir(parents=True)
        (paper_dir / "latex" / "main.tex").write_text(manuscript(candidate["nodes"]), encoding="utf-8")
        (paper_dir / "meta.json").write_text(json.dumps({
            "id": paper_id, "title": title, "status": "completed",
            "notes": "Imported from NLPeer v2 for non-commercial ReviewX evaluation.",
            "externalPaper": {
                "dataset": "NLPeer v2", "subset": candidate["dataset"],
                "originalPaperId": candidate["sourcePaperId"], "version": candidate["version"],
                "license": license_text, "datasetUrl": DATASET_URL, "evaluationSplit": split,
            },
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        samples.append({
            "sampleId": sample_id, "paperId": paper_id, "sourcePaperId": source_key,
            "sampleType": "nlpeer_real_paper", "title": title,
            "externalSource": "nlpeer_v2", "sourceSubset": candidate["dataset"],
            "split": split, "license": license_text,
        })
        for unit_index, unit in enumerate(candidate["units"]):
            references.append({
                "referenceId": stable_id("nlpeer_ref_", f"{source_key}|{unit['reviewId']}|{unit['sourceField']}|{unit['unitIndex']}"),
                "sampleId": sample_id, "paperId": paper_id, "sourcePaperId": source_key,
                "title": title, "split": split, "reviewUnit": unit["text"],
                "reviewField": unit["sourceField"], "reviewId": unit["reviewId"],
                "license": license_text, "datasetUrl": DATASET_URL,
            })
    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(output_dir / "samples.jsonl", samples)
    write_jsonl(output_dir / "references.jsonl", references)
    for split in ("development", "held_out"):
        write_jsonl(output_dir / f"{split}_samples.jsonl", [row for row in samples if row["split"] == split])
        write_jsonl(output_dir / f"{split}_references.jsonl", [row for row in references if row["split"] == split])
    manifest = {
        "schemaVersion": "reviewx_nlpeer_pilot_v1", "createdAt": datetime.now(UTC).isoformat(),
        "datasetUrl": DATASET_URL, "nonCommercialOnly": True,
        "selection": {
            "discoveredPaperCount": len(discovered), "selectedPaperCount": len(samples),
            "referenceCount": len(references), "datasetCounts": dict(Counter(row["sourceSubset"] for row in samples)),
            "developmentPaperCount": sum(row["split"] == "development" for row in samples),
            "heldOutPaperCount": sum(row["split"] == "held_out" for row in samples),
        },
    }
    (output_dir / "selection_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", default=str(DEFAULT_INPUT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--backend-data", default=str(DEFAULT_BACKEND_DATA))
    parser.add_argument("--datasets", default="ARR-EMNLP-2024,EMNLP23,PLOS")
    parser.add_argument("--max-papers", type=int, default=30)
    parser.add_argument("--max-per-dataset", type=int, default=10)
    parser.add_argument("--dev-papers", type=int, default=12)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    manifest = import_pilot(
        Path(args.input_dir), Path(args.output_dir), Path(args.backend_data),
        datasets={value.strip() for value in args.datasets.split(",") if value.strip()},
        max_papers=args.max_papers, max_per_dataset=args.max_per_dataset,
        dev_papers=args.dev_papers, overwrite=args.overwrite,
    )
    selection = manifest["selection"]
    print(
        f"papers={selection['selectedPaperCount']} references={selection['referenceCount']} "
        f"development={selection['developmentPaperCount']} heldOut={selection['heldOutPaperCount']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
