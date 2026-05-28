"""
Experiment Storage — JSON-file persistence for experiments, metrics, and figure artifacts.

Stores:
- experiments/{id}.json
- experiments/{id}/metrics.json
- figures/{id}/spec.json, figure.png, figure.pdf
"""

import json
import os
import logging
from datetime import UTC, datetime
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_EXPERIMENTS_DIR = os.path.join(_BASE_DIR, "data", "experiments")
_FIGURES_DIR = os.path.join(_BASE_DIR, "data", "figures")
os.makedirs(_EXPERIMENTS_DIR, exist_ok=True)
os.makedirs(_FIGURES_DIR, exist_ok=True)


def _gen_id(prefix: str) -> str:
    import uuid
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


# ── Experiments ──────────────────────────────────────────────

def create_experiment(data: Dict[str, Any]) -> Dict[str, Any]:
    exp_id = _gen_id("exp")
    now = _utcnow_iso()
    record = {
        "id": exp_id,
        "name": data.get("name", "Untitled Experiment"),
        "projectId": data.get("projectId"),
        "planSessionId": data.get("planSessionId"),
        "planLinkId": data.get("planLinkId"),
        "status": data.get("status", "created"),
        "tags": data.get("tags", []),
        "description": data.get("description", ""),
        "createdAt": now,
        "updatedAt": now,
    }
    exp_dir = os.path.join(_EXPERIMENTS_DIR, exp_id)
    os.makedirs(exp_dir, exist_ok=True)
    with open(os.path.join(exp_dir, "experiment.json"), "w") as f:
        json.dump(record, f, indent=2)
    # Initialize empty metrics
    with open(os.path.join(exp_dir, "metrics.json"), "w") as f:
        json.dump([], f)
    return record


def get_experiment(exp_id: str) -> Optional[Dict[str, Any]]:
    path = os.path.join(_EXPERIMENTS_DIR, exp_id, "experiment.json")
    if not os.path.isfile(path):
        return None
    with open(path) as f:
        return json.load(f)


def list_experiments(project_id: Optional[str] = None) -> List[Dict[str, Any]]:
    results = []
    if not os.path.isdir(_EXPERIMENTS_DIR):
        return results
    for name in sorted(os.listdir(_EXPERIMENTS_DIR), reverse=True):
        exp_path = os.path.join(_EXPERIMENTS_DIR, name, "experiment.json")
        if os.path.isfile(exp_path):
            try:
                with open(exp_path) as f:
                    data = json.load(f)
                if project_id and data.get("projectId") != project_id:
                    continue
                results.append(data)
            except Exception:
                pass
    return results


def update_experiment(exp_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    record = get_experiment(exp_id)
    if not record:
        return None
    record.update(updates)
    record["updatedAt"] = _utcnow_iso()
    path = os.path.join(_EXPERIMENTS_DIR, exp_id, "experiment.json")
    with open(path, "w") as f:
        json.dump(record, f, indent=2)
    return record


# ── Metrics ──────────────────────────────────────────────

def ingest_metrics(exp_id: str, metrics: List[Dict[str, Any]]) -> int:
    exp_dir = os.path.join(_EXPERIMENTS_DIR, exp_id)
    metrics_path = os.path.join(exp_dir, "metrics.json")
    if not os.path.isdir(exp_dir):
        return 0
    existing = []
    if os.path.isfile(metrics_path):
        with open(metrics_path) as f:
            existing = json.load(f)
    now = _utcnow_iso()
    for m in metrics:
        metric_id = _gen_id("met")
        entry = {
            "id": metric_id,
            "experimentId": exp_id,
            "key": m.get("key", "unknown"),
            "value": m.get("value", 0),
            "step": m.get("step"),
            "timestamp": m.get("timestamp", now),
        }
        existing.append(entry)
    with open(metrics_path, "w") as f:
        json.dump(existing, f, indent=2)
    return len(metrics)


def get_metrics(exp_id: str) -> List[Dict[str, Any]]:
    metrics_path = os.path.join(_EXPERIMENTS_DIR, exp_id, "metrics.json")
    if not os.path.isfile(metrics_path):
        return []
    with open(metrics_path) as f:
        return json.load(f)


# ── Figures ──────────────────────────────────────────────

def save_figure_artifact(
    exp_id: str,
    figure_type: str,
    spec: Dict[str, Any],
    png_bytes: bytes,
    pdf_bytes: Optional[bytes],
    caption: str,
    prompt_used: str,
    model_used: str,
    plot_code: Optional[str] = None,
) -> Dict[str, Any]:
    fig_id = _gen_id("fig")
    fig_dir = os.path.join(_FIGURES_DIR, fig_id)
    os.makedirs(fig_dir, exist_ok=True)

    # Generate clean figure title for filename
    title = spec.get("title", "figure")
    # Clean title for filename: keep alphanumeric, replace spaces with underscores
    import re
    clean_title = re.sub(r'[^\w\s-]', '', title)
    clean_title = re.sub(r'[-\s]+', '_', clean_title.strip())
    clean_title = clean_title[:50]  # Limit length
    
    # Generate unique filename: fig_<id>_<type>_<title>.png
    # This ensures uniqueness while being descriptive
    fig_filename = f"{fig_id}_{figure_type}_{clean_title}"
    
    # Save spec
    spec_data = {
        "id": fig_id,
        "experimentId": exp_id,
        "figureType": figure_type,
        "spec": spec,
        "caption": caption,
        "promptUsed": prompt_used,
        "modelUsed": model_used,
        "createdAt": _utcnow_iso(),
        "fileName": fig_filename,
        "title": title,
    }
    with open(os.path.join(fig_dir, "spec.json"), "w") as f:
        json.dump(spec_data, f, indent=2)

    # Save PNG with proper naming
    png_filename = f"{fig_filename}.png"
    png_path = os.path.join(fig_dir, png_filename)
    with open(png_path, "wb") as f:
        f.write(png_bytes)

    # Save PDF if available
    pdf_path = None
    pdf_filename = None
    if pdf_bytes:
        pdf_filename = f"{fig_filename}.pdf"
        pdf_path = os.path.join(fig_dir, pdf_filename)
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)

    spec_data["pathPng"] = png_path
    spec_data["pathPdf"] = pdf_path
    spec_data["fileNamePng"] = png_filename
    spec_data["fileNamePdf"] = pdf_filename
    spec_data["sizePng"] = len(png_bytes)
    spec_data["sizePdf"] = len(pdf_bytes) if pdf_bytes else 0

    # Save plot code
    if plot_code:
        code_path = os.path.join(fig_dir, "plot.py")
        with open(code_path, "w") as f:
            f.write(plot_code)
        spec_data["pathCode"] = code_path
        spec_data["hasCode"] = True
    else:
        spec_data["hasCode"] = False

    # Update spec with paths
    with open(os.path.join(fig_dir, "spec.json"), "w") as f:
        json.dump(spec_data, f, indent=2)

    # Add to experiment's figure list
    exp_dir = os.path.join(_EXPERIMENTS_DIR, exp_id)
    figures_index_path = os.path.join(exp_dir, "figures.json")
    figures_list = []
    if os.path.isfile(figures_index_path):
        with open(figures_index_path) as f:
            figures_list = json.load(f)
    figures_list.append(spec_data)
    os.makedirs(exp_dir, exist_ok=True)
    with open(figures_index_path, "w") as f:
        json.dump(figures_list, f, indent=2)

    return spec_data


def get_figure(fig_id: str) -> Optional[Dict[str, Any]]:
    spec_path = os.path.join(_FIGURES_DIR, fig_id, "spec.json")
    if not os.path.isfile(spec_path):
        return None
    with open(spec_path) as f:
        return json.load(f)


def list_figures(exp_id: str) -> List[Dict[str, Any]]:
    figures_path = os.path.join(_EXPERIMENTS_DIR, exp_id, "figures.json")
    if not os.path.isfile(figures_path):
        return []
    with open(figures_path) as f:
        return json.load(f)


# ── Datasets ──────────────────────────────────────────────

_DATASETS_DIR = os.path.join(_BASE_DIR, "data", "experiment_datasets")
os.makedirs(_DATASETS_DIR, exist_ok=True)


def save_dataset(exp_id: str, name: str, fmt: str, raw_bytes: bytes, parsed_preview: List[Dict]) -> Dict[str, Any]:
    ds_id = _gen_id("ds")
    ds_dir = os.path.join(_DATASETS_DIR, ds_id)
    os.makedirs(ds_dir, exist_ok=True)

    ext = "csv" if fmt == "csv" else "json"
    raw_path = os.path.join(ds_dir, f"raw.{ext}")
    with open(raw_path, "wb") as f:
        f.write(raw_bytes)

    preview_path = os.path.join(ds_dir, "preview.json")
    with open(preview_path, "w") as f:
        json.dump(parsed_preview[:200], f, indent=2, default=str)

    meta = {
        "id": ds_id,
        "experimentId": exp_id,
        "name": name,
        "format": fmt,
        "rawPath": raw_path,
        "rowCount": len(parsed_preview),
        "columns": list(parsed_preview[0].keys()) if parsed_preview else [],
        "createdAt": _utcnow_iso(),
    }
    with open(os.path.join(ds_dir, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)

    # Index under experiment
    exp_dir = os.path.join(_EXPERIMENTS_DIR, exp_id)
    os.makedirs(exp_dir, exist_ok=True)
    idx_path = os.path.join(exp_dir, "datasets.json")
    idx = []
    if os.path.isfile(idx_path):
        with open(idx_path) as f:
            idx = json.load(f)
    idx.append(meta)
    with open(idx_path, "w") as f:
        json.dump(idx, f, indent=2)

    return meta


def get_dataset(ds_id: str) -> Optional[Dict[str, Any]]:
    meta_path = os.path.join(_DATASETS_DIR, ds_id, "meta.json")
    if not os.path.isfile(meta_path):
        return None
    with open(meta_path) as f:
        return json.load(f)


def get_dataset_preview(ds_id: str) -> List[Dict]:
    preview_path = os.path.join(_DATASETS_DIR, ds_id, "preview.json")
    if not os.path.isfile(preview_path):
        return []
    with open(preview_path) as f:
        return json.load(f)


def list_datasets(exp_id: str) -> List[Dict[str, Any]]:
    idx_path = os.path.join(_EXPERIMENTS_DIR, exp_id, "datasets.json")
    if not os.path.isfile(idx_path):
        return []
    with open(idx_path) as f:
        return json.load(f)
