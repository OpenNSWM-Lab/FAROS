"""
Paper Storage — JSON-file persistence for papers and generation sessions.
"""

import json
import os
import re
import uuid
import shutil
import zipfile
import logging
from datetime import UTC, datetime
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PAPERS_DIR = os.path.join(_BASE_DIR, "data", "papers")
os.makedirs(PAPERS_DIR, exist_ok=True)


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _gen_id() -> str:
    return f"paper_{uuid.uuid4().hex[:12]}"


def _normalize_record(record: Dict[str, Any]) -> Dict[str, Any]:
    record.setdefault("experimentIds", [])
    record.setdefault("figureIds", [])
    record.setdefault("selectedFigures", [])
    record.setdefault("runIds", [])
    record.setdefault("logs", [])
    record.setdefault("pdfAvailable", False)
    record.setdefault("briefJson", None)
    record.setdefault("briefUserEdits", "")
    record.setdefault("briefStatus", "missing")
    record.setdefault("outlineJson", None)
    record.setdefault("outlineStatus", "missing")
    return record


def create_paper(data: Dict[str, Any]) -> Dict[str, Any]:
    paper_id = _gen_id()
    now = _utcnow_iso()
    record = {
        "id": paper_id,
        "title": data.get("title", "Untitled Paper"),
        "paperType": data.get("paperType", "algorithm"),
        "targetVenue": data.get("targetVenue", "generic"),
        "status": "created",
        "planLinkId": data.get("planLinkId"),
        "projectId": data.get("projectId"),
        "experimentIds": data.get("experimentIds", []),
        "figureIds": data.get("figureIds", []),
        "selectedFigures": data.get("selectedFigures", []),
        "runIds": data.get("runIds", []),
        "providerName": data.get("providerName", "moonshot"),
        "model": data.get("model", "moonshot-v1-8k"),
        "notes": data.get("notes"),
        "briefJson": data.get("briefJson"),
        "briefUserEdits": data.get("briefUserEdits", ""),
        "briefStatus": data.get("briefStatus", "missing"),
        "outlineJson": data.get("outlineJson"),
        "outlineStatus": data.get("outlineStatus", "missing"),
        "pdfAvailable": False,
        "logs": [],
        "createdAt": now,
        "updatedAt": now,
    }
    paper_dir = os.path.join(PAPERS_DIR, paper_id)
    os.makedirs(paper_dir, exist_ok=True)
    _save_record(paper_id, record)
    return record


def get_paper(paper_id: str) -> Optional[Dict[str, Any]]:
    path = os.path.join(PAPERS_DIR, paper_id, "meta.json")
    if not os.path.isfile(path):
        return None
    with open(path) as f:
        return _normalize_record(json.load(f))


def list_papers() -> List[Dict[str, Any]]:
    results = []
    if not os.path.isdir(PAPERS_DIR):
        return results
    for name in sorted(os.listdir(PAPERS_DIR), reverse=True):
        meta = os.path.join(PAPERS_DIR, name, "meta.json")
        if os.path.isfile(meta):
            try:
                with open(meta) as f:
                    results.append(_normalize_record(json.load(f)))
            except Exception:
                pass
    return results


def update_paper(paper_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    record = get_paper(paper_id)
    if not record:
        return None
    record.update(updates)
    record["updatedAt"] = _utcnow_iso()
    _save_record(paper_id, record)
    return record


def _clean_latex_label_part(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", value or "").strip("_").lower()
    return cleaned or "figure"


def _figure_file_name(fig: Dict[str, Any]) -> str:
    file_name = (
        fig.get("fileNamePdf")
        or fig.get("fileNamePng")
        or fig.get("fileName")
    )
    if not file_name:
        for key in ("pdfPath", "pngPath", "pathPdf", "pathPng", "path"):
            value = fig.get(key)
            if value:
                file_name = os.path.basename(str(value))
                break
    return os.path.basename(str(file_name or ""))


def _caption_from_figure(title: str, caption: str, notes: str = "") -> str:
    cleaned = re.sub(r"\s+", " ", (caption or "").strip())
    if len(cleaned) >= 24:
        return cleaned
    title_text = re.sub(r"\s+", " ", (title or "").strip())
    notes_text = re.sub(r"\s+", " ", (notes or "").strip())
    if cleaned and title_text and cleaned.lower() != title_text.lower():
        return f"{cleaned}. {title_text}."
    if title_text:
        suffix = f" {notes_text}" if notes_text else " Interpret the figure together with the linked evidence and paper text."
        return f"{title_text}.{suffix}"
    if cleaned:
        return cleaned
    return "Linked paper figure. Interpret the figure together with the linked evidence and paper text."


def _coerce_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def normalize_paper_figure(
    paper_id: str,
    figure: Dict[str, Any],
    ensure_copied: bool = False,
) -> Optional[Dict[str, Any]]:
    """Normalize user-selected figure metadata stored on the paper record."""
    figure_id = figure.get("figureId") or figure.get("id")
    source: Dict[str, Any] = {}
    if figure_id:
        try:
            from app.storage.experiment_storage import get_figure
            source = get_figure(str(figure_id)) or {}
        except Exception:
            source = {}

    copied: Dict[str, Any] = {}
    if ensure_copied and figure_id:
        copied = copy_figure_to_paper(paper_id, str(figure_id), select=False) or {}
        if not source and not copied and not figure.get("path"):
            return None

    merged = {**source, **copied, **figure}
    figure_id = merged.get("figureId") or merged.get("id") or figure_id
    if not figure_id:
        return None

    file_name = _figure_file_name(merged)
    base_name, ext = os.path.splitext(file_name)
    ext = ext.lstrip(".")
    if not base_name:
        base_name = _clean_latex_label_part(str(figure_id))
    if not ext:
        ext = "pdf"

    title = str(merged.get("title") or merged.get("figureType") or base_name.replace("_", " ")).strip()
    notes = str(merged.get("notes") or "").strip()
    caption = _caption_from_figure(title, str(merged.get("caption") or ""), notes)
    label = str(merged.get("label") or merged.get("latexLabel") or f"fig:{_clean_latex_label_part(str(figure_id))}").strip()
    target_section = str(merged.get("targetSection") or merged.get("target_section") or "").strip()

    return {
        "figureId": str(figure_id),
        "title": title,
        "caption": caption,
        "targetSection": target_section,
        "label": label,
        "path": str(merged.get("path") or f"figures/{base_name}.{ext}"),
        "filename": base_name,
        "ext": ext,
        "include": _coerce_bool(merged.get("include", True)),
        "notes": notes,
        "figureType": merged.get("figureType"),
        "experimentId": merged.get("experimentId"),
        "source": merged.get("source") or "selected",
    }


def get_selected_figures(paper_id: str) -> List[Dict[str, Any]]:
    paper = get_paper(paper_id)
    if not paper:
        return []
    figures: List[Dict[str, Any]] = []
    for item in paper.get("selectedFigures", []) or []:
        if not isinstance(item, dict):
            continue
        normalized = normalize_paper_figure(paper_id, item, ensure_copied=False)
        if normalized:
            figures.append(normalized)
    return figures


def update_selected_figures(paper_id: str, figures: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in figures:
        if not isinstance(item, dict):
            continue
        figure = normalize_paper_figure(paper_id, item, ensure_copied=True)
        if not figure:
            continue
        key = figure["figureId"]
        if key in seen:
            continue
        seen.add(key)
        normalized.append(figure)

    paper = get_paper(paper_id)
    if not paper:
        return None
    figure_ids = list(dict.fromkeys([*paper.get("figureIds", []), *(fig["figureId"] for fig in normalized)]))
    return update_paper(paper_id, {"selectedFigures": normalized, "figureIds": figure_ids})


def select_figure_for_paper(
    paper_id: str,
    figure_id: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    metadata = metadata or {}
    paper = get_paper(paper_id)
    if not paper:
        return None
    current = [
        item for item in (paper.get("selectedFigures", []) or [])
        if isinstance(item, dict) and str(item.get("figureId") or item.get("id")) != str(figure_id)
    ]
    selected = normalize_paper_figure(
        paper_id,
        {"figureId": figure_id, **metadata, "include": metadata.get("include", True)},
        ensure_copied=True,
    )
    if not selected:
        return None
    current.append(selected)
    updated = update_selected_figures(paper_id, current)
    if not updated:
        return None
    return selected


def remove_selected_figure(paper_id: str, figure_id: str) -> Optional[Dict[str, Any]]:
    paper = get_paper(paper_id)
    if not paper:
        return None
    selected = [
        item for item in (paper.get("selectedFigures", []) or [])
        if isinstance(item, dict) and str(item.get("figureId") or item.get("id")) != str(figure_id)
    ]
    figure_ids = [fid for fid in paper.get("figureIds", []) if str(fid) != str(figure_id)]
    return update_paper(paper_id, {"selectedFigures": selected, "figureIds": figure_ids})


def add_log(paper_id: str, message: str):
    record = get_paper(paper_id)
    if record:
        record.setdefault("logs", []).append({
            "timestamp": _utcnow_iso(),
            "message": message,
        })
        _save_record(paper_id, record)


def get_paper_dir(paper_id: str) -> str:
    return os.path.join(PAPERS_DIR, paper_id)


def get_paper_latex_dir(paper_id: str) -> str:
    d = os.path.join(PAPERS_DIR, paper_id, "latex")
    os.makedirs(d, exist_ok=True)
    return d


def write_paper_file(paper_id: str, rel_path: str, content: str):
    latex_dir = get_paper_latex_dir(paper_id)
    abs_path = os.path.join(latex_dir, rel_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(content)


def read_paper_file(paper_id: str, rel_path: str) -> Optional[str]:
    latex_dir = get_paper_latex_dir(paper_id)
    abs_path = os.path.join(latex_dir, rel_path)
    real = os.path.realpath(abs_path)
    if not real.startswith(os.path.realpath(latex_dir)):
        return None
    if not os.path.isfile(abs_path):
        return None
    with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def list_paper_files(paper_id: str) -> List[Dict[str, Any]]:
    latex_dir = get_paper_latex_dir(paper_id)
    if not os.path.isdir(latex_dir):
        return []
    entries = []
    for root, dirs, files in os.walk(latex_dir):
        for fname in files:
            abs_path = os.path.join(root, fname)
            rel = os.path.relpath(abs_path, latex_dir)
            entries.append({
                "path": rel,
                "name": fname,
                "size": os.path.getsize(abs_path),
                "isDir": False,
            })
        for dname in dirs:
            abs_path = os.path.join(root, dname)
            rel = os.path.relpath(abs_path, latex_dir)
            entries.append({
                "path": rel,
                "name": dname,
                "size": 0,
                "isDir": True,
            })
    entries.sort(key=lambda e: (not e["isDir"], e["path"]))
    return entries


def create_paper_zip(paper_id: str) -> Optional[str]:
    latex_dir = get_paper_latex_dir(paper_id)
    if not os.path.isdir(latex_dir):
        return None
    zip_path = os.path.join(PAPERS_DIR, paper_id, f"{paper_id}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(latex_dir):
            for fname in files:
                abs_path = os.path.join(root, fname)
                arc_name = os.path.relpath(abs_path, latex_dir)
                zf.write(abs_path, arc_name)
    return zip_path


def _save_record(paper_id: str, record: Dict):
    paper_dir = os.path.join(PAPERS_DIR, paper_id)
    os.makedirs(paper_dir, exist_ok=True)
    with open(os.path.join(paper_dir, "meta.json"), "w") as f:
        json.dump(record, f, indent=2, default=str)


def get_paper_figures_dir(paper_id: str) -> str:
    """Get or create the figures directory for a paper."""
    latex_dir = get_paper_latex_dir(paper_id)
    figures_dir = os.path.join(latex_dir, "figures")
    os.makedirs(figures_dir, exist_ok=True)
    return figures_dir


def copy_figure_to_paper(
    paper_id: str,
    figure_id: str,
    select: bool = True,
) -> Optional[Dict[str, Any]]:
    """Copy a figure from experiments to a paper's latex directory."""
    # Get figure data from experiment storage
    from app.storage.experiment_storage import get_figure
    fig = get_figure(figure_id)
    if not fig:
        return None
    
    paper_fig_dir = get_paper_figures_dir(paper_id)
    
    # Copy PNG file
    png_dest = None
    if fig.get("pathPng") and os.path.exists(fig["pathPng"]):
        png_filename = fig.get("fileNamePng", f"{figure_id}.png")
        png_dest = os.path.join(paper_fig_dir, png_filename)
        shutil.copy2(fig["pathPng"], png_dest)
    
    # Copy PDF file
    pdf_dest = None
    if fig.get("pathPdf") and os.path.exists(fig["pathPdf"]):
        pdf_filename = fig.get("fileNamePdf", f"{figure_id}.pdf")
        pdf_dest = os.path.join(paper_fig_dir, pdf_filename)
        shutil.copy2(fig["pathPdf"], pdf_dest)
    
    # Update paper's figure list
    paper = get_paper(paper_id)
    if paper:
        figure_ids = paper.get("figureIds", [])
        if figure_id not in figure_ids:
            figure_ids.append(figure_id)
        update_paper(paper_id, {"figureIds": figure_ids})
    
    # Generate LaTeX reference
    fig_label = f"fig:{figure_id}"
    caption = fig.get("caption", "")
    latex_ref = f"""\\begin{{figure}}[htbp]
  \\centering
  \\includegraphics[width=0.8\\textwidth]{{figures/{os.path.basename(png_dest) if png_dest else os.path.basename(pdf_dest) if pdf_dest else ''}}}
  \\caption{{{caption}}}
  \\label{{{fig_label}}}
\\end{{figure}}"""
    
    result = {
        "figureId": figure_id,
        "title": fig.get("title", ""),
        "caption": caption,
        "pngPath": png_dest,
        "pdfPath": pdf_dest,
        "latexLabel": fig_label,
        "latexRef": latex_ref,
        "fileNamePng": fig.get("fileNamePng"),
        "fileNamePdf": fig.get("fileNamePdf"),
    }
    if select:
        selected = select_figure_for_paper(paper_id, figure_id, result)
        if selected:
            result["selectedFigure"] = selected
    return result


def get_paper_figures(paper_id: str) -> List[Dict[str, Any]]:
    """Get all figures associated with a paper."""
    paper = get_paper(paper_id)
    if not paper:
        return []
    
    from app.storage.experiment_storage import get_figure
    figure_ids = paper.get("figureIds", [])
    figures = []
    for fig_id in figure_ids:
        fig = get_figure(fig_id)
        if fig:
            figures.append(fig)
    return figures


def generate_latex_figure_reference(figure_id: str, fig_num: int = 1) -> str:
    """Generate LaTeX figure reference code."""
    from app.storage.experiment_storage import get_figure
    fig = get_figure(figure_id)
    if not fig:
        return ""
    
    fig_label = f"fig:{figure_id}"
    caption = fig.get("caption", "")
    png_filename = fig.get("fileNamePng", f"{figure_id}.png")
    
    return f"""\\begin{{figure}}[htbp]
  \\centering
  \\includegraphics[width=0.8\\textwidth]{{figures/{png_filename}}}
  \\caption{{{caption}}}
  \\label{{{fig_label}}}
\\end{{figure}}"""
