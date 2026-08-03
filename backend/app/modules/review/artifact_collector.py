"""Collect paper, experiment, and code artifacts for ReviewX."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from app.modules.review.storage import get_paper, list_paper_files, read_paper_file


_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_DATA_DIR = os.path.join(_BASE_DIR, "data")


def _read_json(path: str, fallback: Any) -> Any:
    if not os.path.isfile(path):
        return fallback
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return fallback


def _safe_rel(path: str) -> str:
    try:
        return os.path.relpath(path, _BASE_DIR)
    except ValueError:
        return path


def collect_reviewx_artifacts(paper_id: str) -> Dict[str, Any]:
    """Return all locally available artifacts that can ground a review run."""
    paper = get_paper(paper_id)
    if not paper:
        raise ValueError(f"Paper not found: {paper_id}")

    latex_files: List[Dict[str, Any]] = []
    for file_info in list_paper_files(paper_id):
        if file_info.get("isDir"):
            continue
        path = file_info.get("path", "")
        if not path.endswith((".tex", ".bib", ".bbl")):
            continue
        content = read_paper_file(paper_id, path)
        if content:
            latex_files.append({
                "path": path,
                "name": file_info.get("name", path),
                "content": content,
            })

    experiments: List[Dict[str, Any]] = []
    for exp_id in paper.get("experimentIds", []) or []:
        exp_dir = os.path.join(_DATA_DIR, "experiments", exp_id)
        exp = _read_json(os.path.join(exp_dir, "experiment.json"), {})
        metrics = _read_json(os.path.join(exp_dir, "metrics.json"), [])
        figures = _read_json(os.path.join(exp_dir, "figures.json"), [])
        report_path = os.path.join(exp_dir, "experiment_report.md")
        report = ""
        if os.path.isfile(report_path):
            with open(report_path, encoding="utf-8", errors="replace") as f:
                report = f.read()
        experiments.append({
            "id": exp_id,
            "path": _safe_rel(exp_dir),
            "record": exp,
            "metrics": metrics,
            "figures": figures,
            "report": report,
        })

    code_artifacts: List[Dict[str, Any]] = []
    project_id = paper.get("projectId")
    if project_id:
        project_dir = os.path.join(_DATA_DIR, "code_projects", project_id)
        exports_dir = os.path.join(project_dir, "exports")
        if os.path.isdir(exports_dir):
            for root, _dirs, files in os.walk(exports_dir):
                for name in files:
                    abs_path = os.path.join(root, name)
                    if os.path.getsize(abs_path) > 250_000:
                        continue
                    content = ""
                    if name.endswith((".json", ".md", ".txt", ".py", ".yaml", ".yml")):
                        with open(abs_path, encoding="utf-8", errors="replace") as f:
                            content = f.read()[:5000]
                    code_artifacts.append({
                        "path": _safe_rel(abs_path),
                        "name": name,
                        "content": content,
                    })

    return {
        "paper": paper,
        "latexFiles": latex_files,
        "experiments": experiments,
        "codeArtifacts": code_artifacts,
    }
