"""Collect paper, experiment, and code artifacts for ReviewX."""

from __future__ import annotations

import json
import os
import hashlib
import re
from typing import Any, Dict, List

from app.core.paths import get_data_dir

from app.modules.review.storage import get_paper, list_paper_files, read_paper_file


_BASE_DIR = str(get_data_dir().parent)
_DATA_DIR = str(get_data_dir())
_REVIEWABLE_CODE_SUFFIXES = {
    ".cfg", ".ini", ".json", ".md", ".py", ".sh", ".toml", ".txt", ".yaml", ".yml",
}
_REVIEWABLE_CODE_NAMES = {"Dockerfile", "LICENSE", "Makefile"}
_IGNORED_CODE_DIRS = {
    ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".venv",
    "__pycache__", "node_modules", "venv",
}
_VISUAL_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
_MAX_VISUAL_BYTES = 8 * 1024 * 1024
_FIGURE_BLOCK_RE = re.compile(
    r"\\begin\{figure\*?\}(.*?)\\end\{figure\*?\}",
    re.IGNORECASE | re.DOTALL,
)
_INCLUDE_GRAPHICS_RE = re.compile(
    r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}",
    re.IGNORECASE,
)
_CAPTION_RE = re.compile(
    r"\\caption(?:\[[^\]]*\])?\{((?:[^{}]|\{[^{}]*\})*)\}",
    re.IGNORECASE | re.DOTALL,
)


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


def _path_within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([os.path.realpath(path), os.path.realpath(root)]) == os.path.realpath(root)
    except ValueError:
        return False


def _image_mime(path: str) -> str | None:
    try:
        with open(path, "rb") as handle:
            header = handle.read(16)
    except OSError:
        return None
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(header) >= 12 and header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image/webp"
    return None


def _valid_visual_path(path: str) -> tuple[str, str] | None:
    real = os.path.realpath(path)
    if not _path_within(real, _DATA_DIR) or not os.path.isfile(real):
        return None
    if os.path.splitext(real)[1].lower() not in _VISUAL_SUFFIXES:
        return None
    size = os.path.getsize(real)
    if size <= 0 or size > _MAX_VISUAL_BYTES:
        return None
    mime = _image_mime(real)
    return (real, mime) if mime else None


def _resolve_visual_path(candidates: Any, roots: List[str]) -> tuple[str, str] | None:
    values = candidates if isinstance(candidates, (list, tuple)) else [candidates]
    for candidate in values:
        value = str(candidate or "").strip()
        if not value:
            continue
        possibilities = [value] if os.path.isabs(value) else [os.path.join(root, value) for root in roots]
        expanded: List[str] = []
        for path in possibilities:
            stem, suffix = os.path.splitext(path)
            if suffix.lower() in _VISUAL_SUFFIXES:
                expanded.append(path)
            elif suffix:
                expanded.extend(stem + visual_suffix for visual_suffix in _VISUAL_SUFFIXES)
            else:
                expanded.extend(path + visual_suffix for visual_suffix in _VISUAL_SUFFIXES)
        for path in expanded:
            resolved = _valid_visual_path(path)
            if resolved:
                return resolved
    return None


def _clean_caption(value: str) -> str:
    text = re.sub(r"\\(?:textbf|textit|emph)\{([^{}]*)\}", r"\1", str(value or ""))
    text = re.sub(r"\\[A-Za-z]+(?:\[[^\]]*\])?", " ", text)
    text = text.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", text).strip()[:1200]


def _visual_id(source_path: str) -> str:
    digest = hashlib.sha256(source_path.encode("utf-8")).hexdigest()[:12]
    return f"visual_{digest}"


def _collect_visual_figures(
    paper_id: str,
    paper: Dict[str, Any],
    latex_files: List[Dict[str, Any]],
    experiments: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    latex_root = os.path.join(_DATA_DIR, "papers", paper_id, "latex")
    figures: List[Dict[str, Any]] = []
    by_path: Dict[str, Dict[str, Any]] = {}

    def add(
        candidate: Any,
        *,
        roots: List[str],
        caption: str = "",
        title: str = "",
        figure_id: Any = None,
        experiment_id: Any = None,
        source: str,
        source_tex_path: str = "",
        source_line: int | None = None,
    ) -> None:
        resolved = _resolve_visual_path(candidate, roots)
        if not resolved:
            return
        absolute_path, mime = resolved
        existing = by_path.get(absolute_path)
        if existing:
            if source_tex_path:
                existing["sourceTexPath"] = source_tex_path
                existing["sourceLine"] = source_line
                existing["source"] = "paper_latex"
            if caption and not existing.get("caption"):
                existing["caption"] = _clean_caption(caption)
            return
        source_path = _safe_rel(absolute_path)
        record = {
            "id": str(figure_id or _visual_id(source_path)),
            "source": source,
            "sourcePath": source_path,
            "absolutePath": absolute_path,
            "mimeType": mime,
            "sizeBytes": os.path.getsize(absolute_path),
            "caption": _clean_caption(caption),
            "title": _clean_caption(title),
            "experimentId": str(experiment_id) if experiment_id else None,
            "sourceTexPath": source_tex_path or None,
            "sourceLine": source_line,
        }
        figures.append(record)
        by_path[absolute_path] = record

    for exp in experiments:
        for figure in exp.get("figures", []) or []:
            figure_id = figure.get("id")
            fallback_root = os.path.join(_DATA_DIR, "figures", str(figure_id or ""))
            add(
                [
                    figure.get("pathPng"),
                    figure.get("fileNamePng"),
                    figure.get("fileName"),
                    figure.get("pathPdf"),
                ],
                roots=[fallback_root, latex_root],
                caption=str(figure.get("caption") or ""),
                title=str(figure.get("title") or figure.get("figureType") or ""),
                figure_id=figure_id,
                experiment_id=exp.get("id"),
                source="experiment",
            )

    for figure in paper.get("selectedFigures", []) or []:
        if not isinstance(figure, dict):
            continue
        candidate = [
            figure.get("pngPath"),
            figure.get("pathPng"),
            figure.get("path"),
            figure.get("fileNamePng"),
            figure.get("filename"),
        ]
        add(
            candidate,
            roots=[latex_root, os.path.join(latex_root, "figures"), os.path.join(latex_root, "Figures")],
            caption=str(figure.get("caption") or ""),
            title=str(figure.get("title") or figure.get("figureType") or ""),
            figure_id=figure.get("figureId") or figure.get("id"),
            experiment_id=figure.get("experimentId"),
            source="paper_selection",
        )

    for latex_file in latex_files:
        if not str(latex_file.get("path", "")).lower().endswith(".tex"):
            continue
        content = str(latex_file.get("content") or "")
        file_dir = os.path.dirname(os.path.join(latex_root, str(latex_file.get("path") or "")))
        matched_paths: set[str] = set()
        for block_match in _FIGURE_BLOCK_RE.finditer(content):
            block = block_match.group(1)
            source_line = content.count("\n", 0, block_match.start()) + 1
            caption_match = _CAPTION_RE.search(block)
            caption = caption_match.group(1) if caption_match else ""
            for candidate in _INCLUDE_GRAPHICS_RE.findall(block):
                matched_paths.add(candidate)
                add(
                    candidate,
                    roots=[file_dir, latex_root],
                    caption=caption,
                    title=os.path.basename(candidate),
                    source="paper_latex",
                    source_tex_path=str(latex_file.get("path") or ""),
                    source_line=source_line,
                )
        for include_match in _INCLUDE_GRAPHICS_RE.finditer(content):
            candidate = include_match.group(1)
            if candidate in matched_paths:
                continue
            add(
                candidate,
                roots=[file_dir, latex_root],
                title=os.path.basename(candidate),
                source="paper_latex",
                source_tex_path=str(latex_file.get("path") or ""),
                source_line=content.count("\n", 0, include_match.start()) + 1,
            )

    return figures[:40]


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

    visual_figures = _collect_visual_figures(paper_id, paper, latex_files, experiments)

    code_artifacts: List[Dict[str, Any]] = []
    execution_assessment: Dict[str, Any] = {}
    experiment_evidence: Dict[str, Any] = {}
    project_id = paper.get("projectId")
    if project_id:
        project_dir = os.path.join(_DATA_DIR, "code_projects", project_id)
        exports_dir = os.path.join(project_dir, "exports")
        if os.path.isdir(exports_dir):
            for root, _dirs, files in os.walk(exports_dir):
                for name in files:
                    abs_path = os.path.join(root, name)
                    size_bytes = os.path.getsize(abs_path)
                    content = ""
                    if size_bytes <= 250_000 and name.endswith((".json", ".md", ".txt", ".py", ".yaml", ".yml")):
                        with open(abs_path, encoding="utf-8", errors="replace") as f:
                            content = f.read()[:5000]
                    code_artifacts.append({
                        "path": _safe_rel(abs_path),
                        "name": name,
                        "content": content,
                        "sizeBytes": size_bytes,
                        "contentOmitted": bool(size_bytes > 250_000),
                    })
        repo_dir = os.path.join(project_dir, "repo")
        evidence_dir = os.path.join(repo_dir, "artifacts", "evidence")
        execution_assessment = _read_json(
            os.path.join(evidence_dir, "execution_assessment.json"), {}
        )
        experiment_evidence = _read_json(
            os.path.join(evidence_dir, "experiment_evidence.json"), {}
        )
        preferred_paths = [
            "src/main.py",
            "configs/experiment.json",
            "configs/experiment.yaml",
            "metrics.json",
            "evaluation_records.json",
            "experiment_report.md",
            "artifacts/evidence/run_manifest.json",
            "artifacts/evidence/environment.json",
            "artifacts/evidence/artifact_hashes.json",
            "artifacts/evidence/execution_assessment.json",
            "artifacts/evidence/experiment_evidence.json",
        ]
        seen_paths = {item["path"] for item in code_artifacts}
        reviewable_paths = list(preferred_paths)
        if os.path.isdir(repo_dir):
            discovered: List[str] = []
            for root, dirs, files in os.walk(repo_dir):
                dirs[:] = sorted(item for item in dirs if item not in _IGNORED_CODE_DIRS)
                for name in sorted(files):
                    suffix = os.path.splitext(name)[1].lower()
                    if suffix not in _REVIEWABLE_CODE_SUFFIXES and name not in _REVIEWABLE_CODE_NAMES:
                        continue
                    discovered.append(os.path.relpath(os.path.join(root, name), repo_dir))
            reviewable_paths.extend(discovered)

        for rel_path in dict.fromkeys(reviewable_paths):
            if len(code_artifacts) >= 80:
                break
            abs_path = os.path.join(repo_dir, rel_path)
            safe_path = _safe_rel(abs_path)
            if safe_path in seen_paths or not os.path.isfile(abs_path) or os.path.getsize(abs_path) > 250_000:
                continue
            with open(abs_path, encoding="utf-8", errors="replace") as f:
                content = f.read()[:12_000]
            code_artifacts.append({
                "path": safe_path,
                "name": os.path.basename(abs_path),
                "content": content,
                "sizeBytes": os.path.getsize(abs_path),
                "contentOmitted": False,
            })
            seen_paths.add(safe_path)

    return {
        "paper": paper,
        "latexFiles": latex_files,
        "experiments": experiments,
        "visualFigures": visual_figures,
        "codeArtifacts": code_artifacts,
        "executionAssessment": execution_assessment,
        "experimentEvidence": experiment_evidence,
    }
