import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.modules.paper.storage import get_paper_latex_dir, write_paper_file
from .constants import MIN_ALGORITHMS, MIN_EQUATIONS, MIN_FIGURES, MIN_REFERENCES, MIN_TABLES, TEMPLATE_ROOT


def ensure_artifacts_dir(paper_id: str) -> str:
    latex_dir = get_paper_latex_dir(paper_id)
    artifacts_dir = os.path.join(latex_dir, "artifacts")
    os.makedirs(artifacts_dir, exist_ok=True)
    return artifacts_dir


def write_artifact(paper_id: str, step_id: str, data: Dict[str, Any], summary_lines: List[str]) -> List[str]:
    json_path = f"artifacts/{step_id}.json"
    md_path = f"artifacts/{step_id}.md"
    write_paper_file(paper_id, json_path, json.dumps(data, ensure_ascii=False, indent=2))
    write_paper_file(paper_id, md_path, "\n".join(summary_lines) + "\n")
    return [json_path, md_path]


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    text = text.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1]
        if "```" in text:
            text = text.rsplit("```", 1)[0]
    elif "```" in text:
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1]
        elif len(parts) >= 2:
            text = parts[1]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None


def _clean_label_part(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", value or "").strip("_").lower()
    return cleaned or "figure"


def figure_record_to_entry(fig: Dict[str, Any], source: str = "selected") -> Optional[Dict[str, Any]]:
    """Normalize an experiment figure record into the paper figure entry shape."""
    file_name = (
        fig.get("fileNamePdf")
        or fig.get("fileNamePng")
        or fig.get("fileName")
    )
    if not file_name:
        for path_key in ("pdfPath", "pngPath", "pathPdf", "pathPng"):
            path_value = fig.get(path_key)
            if path_value:
                file_name = os.path.basename(path_value)
                break
    if not file_name:
        return None

    base_name, ext = os.path.splitext(os.path.basename(file_name))
    ext = ext.lstrip(".") or "png"
    figure_id = fig.get("figureId") or fig.get("id") or base_name
    title = fig.get("title") or fig.get("figureType") or base_name.replace("_", " ")
    caption = fig.get("caption") or title
    label = fig.get("latexLabel") or fig.get("label") or f"fig:{_clean_label_part(str(figure_id))}"

    return {
        "figureId": figure_id,
        "filename": base_name,
        "ext": ext,
        "path": f"figures/{base_name}.{ext}",
        "caption": caption,
        "label": label,
        "title": title,
        "figureType": fig.get("figureType"),
        "experimentId": fig.get("experimentId"),
        "source": source,
    }


def dedupe_figure_entries(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    unique: List[Dict[str, Any]] = []
    for entry in entries:
        key = entry.get("filename") or entry.get("label")
        if key in seen:
            continue
        seen.add(key)
        unique.append(entry)
    return unique


def load_linked_figure_records(paper: Dict[str, Any], max_figures: int = 8) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    seen = set()

    def add_figure(fig: Optional[Dict[str, Any]]) -> None:
        if not fig or len(records) >= max_figures:
            return
        fig_id = fig.get("id") or fig.get("figureId")
        key = fig_id or fig.get("fileNamePng") or fig.get("fileNamePdf") or fig.get("title")
        if key in seen:
            return
        seen.add(key)
        records.append(fig)

    try:
        from app.storage.experiment_storage import get_figure, list_figures

        for fig_id in paper.get("figureIds", [])[:max_figures]:
            add_figure(get_figure(fig_id))

        if len(records) < max_figures:
            for exp_id in paper.get("experimentIds", [])[:3]:
                for fig in list_figures(exp_id)[:max_figures]:
                    add_figure(fig)
                    if len(records) >= max_figures:
                        break
    except Exception:
        pass

    return records


def get_linked_figure_entries(
    paper: Dict[str, Any],
    ensure_copied: bool = False,
    max_figures: int = 8,
) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    paper_id = paper.get("id")

    for fig in load_linked_figure_records(paper, max_figures=max_figures):
        source_record = fig
        if ensure_copied and paper_id:
            try:
                from app.modules.paper.storage import copy_figure_to_paper

                figure_id = fig.get("id") or fig.get("figureId")
                if figure_id:
                    copied = copy_figure_to_paper(paper_id, figure_id)
                    if copied:
                        source_record = {**fig, **copied}
            except Exception:
                source_record = fig

        entry = figure_record_to_entry(source_record, source="selected")
        if entry:
            entries.append(entry)

    return dedupe_figure_entries(entries)


def collect_context(paper: Dict[str, Any]) -> Dict[str, str]:
    ctx = {
        "plan_context": "N/A",
        "project_summary": "N/A",
        "metrics_summary": "N/A",
        "runs_summary": "N/A",
        "figures_summary": "N/A",
        "user_notes": "N/A",
    }

    plan_link_id = paper.get("planLinkId")
    if plan_link_id:
        try:
            from app.modules.platform.storage import get_plan_link
            link_data = get_plan_link(plan_link_id)
            if link_data:
                ctx["plan_context"] = json.dumps(link_data, default=str)[:2000]
        except Exception:
            pass

    project_id = paper.get("projectId")
    if project_id:
        try:
            from app.services.code_project_service import read_file_content
            readme = read_file_content(project_id, "README.md")
            if readme:
                ctx["project_summary"] = readme[:2000]
        except Exception:
            pass

    exp_ids = paper.get("experimentIds", [])
    if exp_ids:
        try:
            from app.modules.paper.storage import get_experiment, get_metrics
            all_metrics = []
            for eid in exp_ids[:3]:
                exp = get_experiment(eid)
                if exp:
                    metrics = get_metrics(eid)
                    all_metrics.extend(metrics[:20])
            if all_metrics:
                ctx["metrics_summary"] = json.dumps(all_metrics[:30], default=str)[:2000]
        except Exception:
            pass

    run_ids = paper.get("runIds", [])
    if run_ids:
        try:
            from app.modules.platform.storage import get_run_storage, get_artifact_storage
            run_storage = get_run_storage()
            artifact_storage = get_artifact_storage()
            run_entries = []
            for run_id in run_ids[:5]:
                run = run_storage.get(run_id)
                if not run:
                    continue
                artifacts = artifact_storage.list_by_run(run_id)
                run_entries.append({
                    "id": run.id,
                    "status": run.status.value if hasattr(run.status, "value") else str(run.status),
                    "type": run.type.value if hasattr(run.type, "value") else str(run.type),
                    "model": run.config.model if getattr(run, "config", None) else None,
                    "workspace": run.config.workplaceName if getattr(run, "config", None) else None,
                    "duration": run.duration,
                    "error": run.errorMessage,
                    "artifactCount": len(artifacts),
                    "artifacts": [
                        {
                            "id": a.id,
                            "type": a.type.value if hasattr(a.type, "value") else str(a.type),
                            "filename": a.filename,
                            "size": a.size,
                        }
                        for a in artifacts[:10]
                    ],
                })
            if run_entries:
                ctx["runs_summary"] = json.dumps(run_entries, default=str)[:3000]
        except Exception:
            pass

    figure_entries = get_linked_figure_entries(paper, ensure_copied=False)
    if figure_entries:
        ctx["figures_summary"] = json.dumps([
            {
                "figureId": f.get("figureId"),
                "title": f.get("title"),
                "caption": f.get("caption"),
                "path": f.get("path"),
                "label": f.get("label"),
                "figureType": f.get("figureType"),
                "experimentId": f.get("experimentId"),
                "source": f.get("source"),
            }
            for f in figure_entries
        ], default=str)[:2000]

    notes = paper.get("notes", "")
    if notes:
        ctx["user_notes"] = notes[:1000]

    return ctx


def gate_outline(outline: Dict[str, Any]) -> List[str]:
    issues = []
    sections = outline.get("sections", [])
    refs = outline.get("references", [])

    if len(sections) < 5:
        issues.append(f"Only {len(sections)} sections (need >=5)")
    if len(refs) < MIN_REFERENCES:
        issues.append(f"Only {len(refs)} references (need >={MIN_REFERENCES})")

    algo_count = sum(1 for s in sections if s.get("hasAlgorithm"))
    eq_sections = sum(1 for s in sections if s.get("hasEquations"))
    table_sections = sum(1 for s in sections if s.get("hasTables"))

    if algo_count < 1:
        issues.append(f"No sections marked with algorithms (need >={MIN_ALGORITHMS} total)")
    if eq_sections < 2:
        issues.append(f"Only {eq_sections} sections with equations (need >=2)")
    if table_sections < 1:
        issues.append("No sections marked with tables")

    if not outline.get("abstract"):
        issues.append("Missing abstract")
    elif len(outline["abstract"].split()) < 50:
        issues.append(f"Abstract too short ({len(outline['abstract'].split())} words, need >=50)")

    return issues


def gate_evidence(sections_content: Dict[str, str]) -> Dict[str, Any]:
    all_text = "\n".join(sections_content.values())

    algo_count = all_text.count("\\begin{algorithm")
    eq_count = all_text.count("\\begin{equation")
    table_count = all_text.count("\\begin{table")
    fig_count = all_text.count("\\includegraphics")
    cite_count = len(set(re.findall(r"\\cite\{([^}]+)\}", all_text)))

    gates = {
        "algorithms": {"count": algo_count, "required": MIN_ALGORITHMS, "pass": algo_count >= MIN_ALGORITHMS},
        "equations": {"count": eq_count, "required": MIN_EQUATIONS, "pass": eq_count >= MIN_EQUATIONS},
        "tables": {"count": table_count, "required": MIN_TABLES, "pass": table_count >= MIN_TABLES},
        "figures": {"count": fig_count, "required": MIN_FIGURES, "pass": fig_count >= MIN_FIGURES},
        "citations": {"count": cite_count, "required": 10, "pass": cite_count >= 10},
    }
    gates["all_pass"] = all(g["pass"] for g in gates.values())
    return gates


def copy_template_assets(venue: str, paper_id: str) -> None:
    template_dir = TEMPLATE_ROOT / venue
    if not template_dir.is_dir():
        template_dir = TEMPLATE_ROOT / "generic"
    latex_dir = Path(get_paper_latex_dir(paper_id))
    for asset in template_dir.iterdir():
        if not asset.is_file():
            continue
        if asset.name in {"main.tex", "refs.bib", "references.bib"}:
            continue
        shutil.copy2(asset, latex_dir / asset.name)


def normalize_section_figure_references(
    content: str,
    figure_entries: List[Dict[str, str]],
    figures_dir: str,
) -> Tuple[str, List[Dict[str, str]]]:
    """Point missing includegraphics references at generated figure files."""
    if not content or not figure_entries:
        return content, []

    generated_paths = []
    for entry in figure_entries:
        filename = entry.get("filename")
        if not filename:
            continue
        ext = (entry.get("ext") or "pdf").lstrip(".")
        generated_paths.append(f"figures/{filename}.{ext}")

    if not generated_paths:
        return content, []

    rewrites: List[Dict[str, str]] = []
    replacement_index = 0

    def include_exists(path: str) -> bool:
        normalized = path.strip()
        if os.path.isabs(normalized):
            return os.path.isfile(normalized)
        relative = normalized
        if relative.startswith("figures/"):
            relative = relative[len("figures/"):]
        return os.path.isfile(os.path.join(figures_dir, relative))

    def replace_include(match: re.Match[str]) -> str:
        nonlocal replacement_index
        prefix, path, suffix = match.group(1), match.group(2).strip(), match.group(3)
        if include_exists(path):
            return match.group(0)

        target = generated_paths[min(replacement_index, len(generated_paths) - 1)]
        replacement_index += 1
        rewrites.append({"from": path, "to": target})
        return f"{prefix}{target}{suffix}"

    normalized = re.sub(
        r"(\\includegraphics(?:\[[^\]]*\])?\{)([^}]+)(\})",
        replace_include,
        content,
    )
    return normalized, rewrites


def build_main_tex(outline: Dict[str, Any], sections: List[Dict[str, Any]], venue: str) -> str:
    title = outline.get("title", "Untitled Paper")
    authors = outline.get("authors", ["Auto-LLM Draft"]) or ["Auto-LLM Draft"]
    abstract = outline.get("abstract", "")
    running_title = title if len(title) <= 70 else title[:67] + "..."
    authors_text = ", ".join(authors[:4])
    section_inputs = "\n\n".join(f"\\input{{sections/{s['id']}.tex}}" for s in sections)

    template_dir = TEMPLATE_ROOT / venue
    if not template_dir.is_dir():
        template_dir = TEMPLATE_ROOT / "generic"
    template_path = template_dir / "main.tex"
    if not template_path.is_file():
        template_path = TEMPLATE_ROOT / "generic" / "main.tex"

    shell = template_path.read_text(encoding="utf-8")
    return (shell
        .replace("%%TITLE%%", title)
        .replace("%%RUNNING_TITLE%%", running_title)
        .replace("%%AUTHORS%%", authors_text)
        .replace("%%ABSTRACT%%", abstract)
        .replace("%%SECTION_INPUTS%%", section_inputs)
    )


def build_bibtex(references: List[Dict[str, Any]]) -> str:
    entries = []
    for ref in references:
        key = ref.get("key", f"ref{len(entries)+1}")
        authors = ref.get("authors", "Unknown")
        title = ref.get("title", "Untitled")
        venue = ref.get("venue", "arXiv preprint")
        year = ref.get("year", 2024)
        note = ref.get("note", "")

        venue_lower = venue.lower()
        if any(kw in venue_lower for kw in [
            "conference", "proceedings", "workshop", "neurips", "icml", "iclr",
            "acl", "aaai", "cvpr", "eccv", "iccv"
        ]):
            entry_type = "inproceedings"
            venue_field = f"  booktitle = {{{venue}}},"
        elif any(kw in venue_lower for kw in ["journal", "transactions", "review"]):
            entry_type = "article"
            venue_field = f"  journal = {{{venue}}},"
        elif "arxiv" in venue_lower:
            entry_type = "article"
            venue_field = f"  journal = {{{venue}}},"
        else:
            entry_type = "article"
            venue_field = f"  journal = {{{venue}}},"

        note_field = f"\n  note = {{{note}}}," if note else ""
        entries.append(
            f"""@{entry_type}{{{key},
  author = {{{authors}}},
  title = {{{title}}},
{venue_field}
  year = {{{year}}},{note_field}
}}"""
        )
    return "\n\n".join(entries) + "\n"
