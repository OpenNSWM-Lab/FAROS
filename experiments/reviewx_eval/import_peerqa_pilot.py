#!/usr/bin/env python3
"""Import a deterministic, diverse PeerQA pilot into FAROS paper storage."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "experiments" / "reviewx_eval" / "external_data" / "peerqa"
DEFAULT_OUTPUT = DEFAULT_INPUT / "faros_pilot"
DEFAULT_BACKEND_DATA = ROOT / "backend" / "data"
DATASET_LICENSE = "CC-BY-NC-SA-4.0"
DATASET_URL = "https://github.com/UKPLab/PeerQA"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_group(paper_id: str) -> str:
    parts = paper_id.split("/")
    return "/".join(parts[:2]) if len(parts) >= 2 else parts[0]


def faros_paper_id(peerqa_id: str) -> str:
    return "paper_peerqa_" + hashlib.sha256(peerqa_id.encode("utf-8")).hexdigest()[:16]


def clean_text(text: Any) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    value = value.replace("\\", " ").replace("%", " percent")
    return value


def title_for(rows: list[dict[str, Any]], paper_id: str) -> str:
    for row in rows:
        if row.get("paper_id") == paper_id and row.get("type") == "title":
            return clean_text(row.get("content"))
    return paper_id


def selection_score(questions: list[dict[str, Any]]) -> tuple[int, int, int, str]:
    mapped = sum(question.get("answerable_mapped") is True for question in questions)
    unanswerable = sum(question.get("answerable") is False for question in questions)
    answered = sum(bool(question.get("answer_free_form")) for question in questions)
    # Prefer several expert questions, mapped evidence, and at least one negative case.
    return (len(questions), mapped, answered + min(unanswerable, 1), questions[0]["paper_id"])


def select_papers(
    paragraphs: list[dict[str, Any]],
    questions: list[dict[str, Any]],
    max_papers: int,
    max_per_source: int,
    excluded_papers: set[str] | None = None,
) -> list[str]:
    excluded_papers = excluded_papers or set()
    text_ids = {str(row["paper_id"]) for row in paragraphs}
    by_paper: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for question in questions:
        paper_id = str(question["paper_id"])
        if paper_id in text_ids and paper_id not in excluded_papers:
            by_paper[paper_id].append(question)
    ranked = sorted(by_paper, key=lambda paper_id: selection_score(by_paper[paper_id]), reverse=True)
    selected = []
    source_counts: Counter[str] = Counter()
    while ranked and len(selected) < max_papers:
        made_progress = False
        for paper_id in list(ranked):
            source = source_group(paper_id)
            if source_counts[source] >= max_per_source:
                continue
            selected.append(paper_id)
            source_counts[source] += 1
            ranked.remove(paper_id)
            made_progress = True
            if len(selected) >= max_papers:
                break
        if not made_progress:
            break
    return selected


def excluded_source_ids(paths: list[Path]) -> set[str]:
    excluded = set()
    for path in paths:
        for row in read_jsonl(path):
            source_id = row.get("sourcePaperId") or row.get("paper_id")
            if source_id:
                excluded.add(str(source_id))
    return excluded


def manuscript(rows: list[dict[str, Any]]) -> str:
    parts = ["\\documentclass{article}", "\\begin{document}"]
    current_heading = None
    for row in sorted(rows, key=lambda item: int(item.get("idx", 0))):
        row_type = str(row.get("type") or "paragraph")
        content = clean_text(row.get("content"))
        if not content:
            continue
        if row_type == "title":
            continue
        if row_type == "heading":
            if content != current_heading:
                parts.append(f"\\section{{{content.replace('{', '(').replace('}', ')')}}}")
                current_heading = content
            continue
        heading = clean_text(row.get("last_heading"))
        if heading and heading != current_heading:
            parts.append(f"\\section{{{heading.replace('{', '(').replace('}', ')')}}}")
            current_heading = heading
        parts.append(content)
    parts.append("\\end{document}")
    return "\n\n".join(parts) + "\n"


def reference_row(
    question: dict[str, Any],
    sample_id: str,
    paper_id: str,
    title: str,
    split: str,
) -> dict[str, Any]:
    return {
        "referenceId": "peerqa_" + str(question["question_id"]),
        "sampleId": sample_id,
        "paperId": paper_id,
        "sourcePaperId": question["paper_id"],
        "title": title,
        "split": split,
        "reviewerQuestion": question.get("question"),
        "authorAnswerable": question.get("answerable"),
        "authorAnswerableMapped": question.get("answerable_mapped"),
        "authorAnswer": question.get("answer_free_form"),
        "evidenceSentences": question.get("answer_evidence_sent") or [],
        "evidenceMappings": question.get("answer_evidence_mapped") or [],
        "license": DATASET_LICENSE,
        "datasetUrl": DATASET_URL,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", default=str(DEFAULT_INPUT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--backend-data", default=str(DEFAULT_BACKEND_DATA))
    parser.add_argument("--max-papers", type=int, default=20)
    parser.add_argument("--max-per-source", type=int, default=4)
    parser.add_argument("--dev-papers", type=int, default=8)
    parser.add_argument(
        "--exclude-samples",
        action="append",
        default=[],
        help="JSONL samples whose sourcePaperId values must not be selected.",
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    backend_data = Path(args.backend_data)
    papers_path = input_dir / "papers.jsonl"
    qa_path = input_dir / "qa.jsonl"
    paragraphs = read_jsonl(papers_path)
    questions = read_jsonl(qa_path)
    exclusion_paths = [Path(path) for path in args.exclude_samples]
    excluded = excluded_source_ids(exclusion_paths)
    selected = select_papers(
        paragraphs,
        questions,
        args.max_papers,
        args.max_per_source,
        excluded_papers=excluded,
    )
    if not selected:
        raise ValueError("no PeerQA papers with both parsed text and questions were found")

    paragraphs_by_paper: dict[str, list[dict[str, Any]]] = defaultdict(list)
    questions_by_paper: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in paragraphs:
        if row["paper_id"] in selected:
            paragraphs_by_paper[row["paper_id"]].append(row)
    for row in questions:
        if row["paper_id"] in selected:
            questions_by_paper[row["paper_id"]].append(row)

    samples = []
    references = []
    candidate_rows = []
    for index, source_id in enumerate(selected):
        paper_id = faros_paper_id(source_id)
        sample_id = "sample_" + paper_id.removeprefix("paper_")
        title = title_for(paragraphs_by_paper[source_id], source_id)
        split = "development" if index < args.dev_papers else "held_out"
        paper_dir = backend_data / "papers" / paper_id
        if paper_dir.exists() and not args.overwrite:
            raise FileExistsError(f"paper already exists: {paper_dir}; pass --overwrite")
        if paper_dir.exists():
            shutil.rmtree(paper_dir)
        (paper_dir / "latex").mkdir(parents=True)
        meta = {
            "id": paper_id,
            "title": title,
            "status": "completed",
            "notes": "Imported from PeerQA v1.0 for non-commercial ReviewX evaluation.",
            "externalPaper": {
                "dataset": "PeerQA",
                "datasetVersion": "1.0",
                "originalPaperId": source_id,
                "sourceGroup": source_group(source_id),
                "license": DATASET_LICENSE,
                "datasetUrl": DATASET_URL,
                "evaluationSplit": split,
            },
        }
        (paper_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        (paper_dir / "latex" / "main.tex").write_text(
            manuscript(paragraphs_by_paper[source_id]), encoding="utf-8",
        )
        samples.append({
            "sampleId": sample_id,
            "paperId": paper_id,
            "sourcePaperId": source_id,
            "sampleType": "peerqa_real_paper",
            "title": title,
            "externalSource": "peerqa",
            "split": split,
            "license": DATASET_LICENSE,
        })
        for question in questions_by_paper[source_id]:
            reference = reference_row(question, sample_id, paper_id, title, split)
            references.append(reference)
            candidate_rows.append({
                "referenceId": reference["referenceId"],
                "sampleId": sample_id,
                "paperId": paper_id,
                "sourceGroup": source_group(source_id),
                "split": split,
                "title": title,
                "reviewerQuestion": reference["reviewerQuestion"],
                "authorAnswerable": reference["authorAnswerable"],
                "authorAnswer": reference["authorAnswer"],
                "evidenceSentences": " || ".join(reference["evidenceSentences"]),
                "humanQuestionValidity": "",
                "humanEvidenceAgreement": "",
                "humanReviewXCoverage": "",
                "humanNotes": "",
            })

    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(output_dir / "samples.jsonl", samples)
    write_jsonl(output_dir / "peerqa_references.jsonl", references)
    write_jsonl(
        output_dir / "development_samples.jsonl",
        [row for row in samples if row["split"] == "development"],
    )
    write_jsonl(
        output_dir / "held_out_samples.jsonl",
        [row for row in samples if row["split"] == "held_out"],
    )
    write_jsonl(
        output_dir / "development_references.jsonl",
        [row for row in references if row["split"] == "development"],
    )
    write_jsonl(
        output_dir / "held_out_references.jsonl",
        [row for row in references if row["split"] == "held_out"],
    )
    with (output_dir / "annotation_candidates.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(candidate_rows[0]))
        writer.writeheader()
        writer.writerows(candidate_rows)
    manifest = {
        "schemaVersion": "reviewx_peerqa_pilot_v1",
        "createdAt": datetime.now(UTC).isoformat(),
        "dataset": "PeerQA",
        "datasetVersion": "1.0",
        "datasetUrl": DATASET_URL,
        "license": DATASET_LICENSE,
        "nonCommercialOnly": True,
        "inputs": {
            "papers": {"path": str(papers_path), "sha256": sha256_file(papers_path)},
            "qa": {"path": str(qa_path), "sha256": sha256_file(qa_path)},
        },
        "selection": {
            "paperCount": len(samples),
            "referenceCount": len(references),
            "maxPerSource": args.max_per_source,
            "developmentPaperCount": len([row for row in samples if row["split"] == "development"]),
            "heldOutPaperCount": len([row for row in samples if row["split"] == "held_out"]),
            "sourceCounts": dict(Counter(source_group(row["sourcePaperId"]) for row in samples)),
            "answerableCounts": dict(Counter(str(row["authorAnswerable"]) for row in references)),
            "excludedSourcePaperCount": len(excluded),
            "exclusionFiles": [
                {"path": str(path), "sha256": sha256_file(path)} for path in exclusion_paths
            ],
        },
    }
    (output_dir / "selection_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(
        f"papers={len(samples)} references={len(references)} "
        f"development={manifest['selection']['developmentPaperCount']} "
        f"heldOut={manifest['selection']['heldOutPaperCount']} output={output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
