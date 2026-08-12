import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.modules.paper.storage import get_paper_latex_dir, write_paper_file
from .constants import TEMPLATE_ROOT


LATEX_MATH_ENVS = {
    "align",
    "align*",
    "aligned",
    "alignat",
    "alignat*",
    "displaymath",
    "eqnarray",
    "eqnarray*",
    "equation",
    "equation*",
    "flalign",
    "flalign*",
    "gather",
    "gather*",
    "gathered",
    "math",
    "multline",
    "multline*",
    "split",
}


def ensure_artifacts_dir(paper_id: str) -> str:
    latex_dir = get_paper_latex_dir(paper_id)
    artifacts_dir = os.path.join(latex_dir, "artifacts")
    os.makedirs(artifacts_dir, exist_ok=True)
    return artifacts_dir


def reset_artifacts_dir(paper_id: str) -> str:
    artifacts_dir = ensure_artifacts_dir(paper_id)
    for name in os.listdir(artifacts_dir):
        path = os.path.join(artifacts_dir, name)
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
    return artifacts_dir


ARTIFACT_PATHS = {
    "00_plan_evidence": "artifacts/evidence.json",
    "02_code_artifacts": "artifacts/code_artifacts.json",
    "02_paper_brief": "artifacts/brief.json",
    "03_outline": "artifacts/outline.json",
    "08_assemble_latex": "artifacts/assembly.json",
    "09_latex_compile_agent": "artifacts/feedback/round_01/compile.json",
    "10_simple_review_compile_agent": "artifacts/feedback/round_01/compile.json",
    "10_simple_review_loop": "artifacts/feedback/round_01/review.json",
    "feedback_rewrite_latex_compile": "artifacts/feedback/round_01/rewrite_compile.json",
    "feedback_rewrite_simple_review": "artifacts/feedback/round_01/rewrite_review.json",
}


def write_artifact(
    paper_id: str,
    step_id: str,
    data: Dict[str, Any],
    summary_lines: List[str],
    artifact_path: str | None = None,
) -> List[str]:
    json_path = artifact_path or ARTIFACT_PATHS.get(step_id, f"artifacts/{step_id}.json")
    payload = {
        "_artifact": {
            "id": step_id,
            "path": json_path,
            "summaryLines": summary_lines,
        },
        **data,
    }
    write_paper_file(paper_id, json_path, json.dumps(payload, ensure_ascii=False, indent=2))
    return [json_path]


def stable_context_fingerprint(*parts: Any) -> str:
    """Return a stable short fingerprint for cache invalidation inputs."""
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


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
    path = str(fig.get("path") or "").strip()
    base_name = str(fig.get("filename") or "").strip()
    ext = str(fig.get("ext") or "").lstrip(".").strip()

    file_name = None
    if not base_name:
        file_name = (
            fig.get("fileNamePdf")
            or fig.get("fileNamePng")
            or fig.get("fileName")
        )
    if not file_name and not base_name:
        for path_key in ("pdfPath", "pngPath", "pathPdf", "pathPng"):
            path_value = fig.get(path_key)
            if path_value:
                file_name = os.path.basename(path_value)
                break
    if not file_name and path and not base_name:
        file_name = os.path.basename(path)
    if not file_name and not base_name:
        return None

    if file_name:
        base_name, file_ext = os.path.splitext(os.path.basename(file_name))
        ext = ext or file_ext.lstrip(".")
    ext = ext or "png"
    figure_id = fig.get("figureId") or fig.get("id") or base_name
    title = fig.get("title") or fig.get("figureType") or base_name.replace("_", " ")
    caption = fig.get("caption") or title
    label = fig.get("latexLabel") or fig.get("label") or f"fig:{_clean_label_part(str(figure_id))}"
    include = fig.get("include", True)
    if isinstance(include, str):
        include = include.lower() not in {"0", "false", "no", "off"}

    return {
        "figureId": figure_id,
        "filename": base_name,
        "ext": ext,
        "path": path or f"figures/{base_name}.{ext}",
        "caption": caption,
        "label": label,
        "title": title,
        "figureType": fig.get("figureType"),
        "experimentId": fig.get("experimentId"),
        "targetSection": fig.get("targetSection") or fig.get("target_section") or "",
        "notes": fig.get("notes") or "",
        "include": bool(include),
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


def load_selected_figure_entries(
    paper: Dict[str, Any],
    ensure_copied: bool = False,
    max_figures: int = 8,
) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    paper_id = paper.get("id")
    for item in paper.get("selectedFigures", []) or []:
        if len(entries) >= max_figures:
            break
        if not isinstance(item, dict):
            continue

        source_record = item
        if paper_id:
            try:
                from app.modules.paper.storage import normalize_paper_figure

                normalized = normalize_paper_figure(
                    paper_id,
                    item,
                    ensure_copied=ensure_copied,
                )
                if normalized:
                    source_record = normalized
            except Exception:
                source_record = item

        entry = figure_record_to_entry(source_record, source="selected")
        if entry and entry.get("include", True):
            entries.append(entry)

    return dedupe_figure_entries(entries)


def get_linked_figure_entries(
    paper: Dict[str, Any],
    ensure_copied: bool = False,
    max_figures: int = 8,
) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    paper_id = paper.get("id")

    if paper.get("selectedFiguresExplicit") or paper.get("selectedFigures"):
        return load_selected_figure_entries(
            paper,
            ensure_copied=ensure_copied,
            max_figures=max_figures,
        )

    selected_entries = load_selected_figure_entries(
        paper,
        ensure_copied=ensure_copied,
        max_figures=max_figures,
    )
    if selected_entries:
        return selected_entries

    for fig in load_linked_figure_records(paper, max_figures=max_figures):
        source_record = fig
        if ensure_copied and paper_id:
            try:
                from app.modules.paper.storage import copy_figure_to_paper

                figure_id = fig.get("id") or fig.get("figureId")
                if figure_id:
                    copied = copy_figure_to_paper(paper_id, figure_id, select=False)
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
        "plan_evidence": "N/A",
        "code_evidence": "N/A",
        "project_summary": "N/A",
        "metrics_summary": "N/A",
        "runs_summary": "N/A",
        "figures_summary": "N/A",
        "code_tables_summary": "N/A",
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

    try:
        evidence = paper.get("evidenceJson")
        if evidence and evidence.get("status") == "collected":
            ctx["plan_evidence"] = json.dumps(evidence, ensure_ascii=False, default=str)[:8000]
            code_evidence = evidence.get("codeEvidence")
            if isinstance(code_evidence, dict) and code_evidence.get("status") == "collected":
                ctx["code_evidence"] = json.dumps(code_evidence, ensure_ascii=False, default=str)[:8000]
                repo = code_evidence.get("repo") if isinstance(code_evidence.get("repo"), dict) else {}
                if repo.get("readme") and ctx["project_summary"] == "N/A":
                    ctx["project_summary"] = str(repo.get("readme"))[:2000]
                def artifact_ref(artifact: Dict[str, Any]) -> Dict[str, Any]:
                    return {
                        "id": artifact.get("id"),
                        "label": artifact.get("label") or artifact.get("tableId") or artifact.get("figureId"),
                        "name": artifact.get("name"),
                        "filename": artifact.get("filename"),
                        "path": artifact.get("path"),
                        "cartRelativePath": artifact.get("cartRelativePath"),
                        "sourcePath": artifact.get("sourcePath"),
                        "nodeId": artifact.get("nodeId"),
                        "kind": artifact.get("kind"),
                    }

                metrics_sources = {
                    "repoMetrics": repo.get("metrics"),
                    "runMetrics": [
                        {
                            "runId": run.get("runId"),
                            "metrics": run.get("metrics"),
                            "executionSummary": run.get("executionSummary"),
                        }
                        for run in code_evidence.get("runs", [])
                        if isinstance(run, dict)
                    ],
                    "cartMetrics": [
                        {
                            "cartId": cart.get("cartId"),
                            "constants": {
                                "datasets": (cart.get("constants") or {}).get("datasets"),
                                "models": (cart.get("constants") or {}).get("models"),
                                "paperType": (cart.get("constants") or {}).get("paperType"),
                            },
                            "metrics": cart.get("metrics"),
                            "nodeResults": [
                                {
                                    "nodeId": node.get("nodeId"),
                                    "success": node.get("success"),
                                    "durationMs": node.get("durationMs"),
                                    "metrics": node.get("metrics"),
                                    "experimentPlan": node.get("experimentPlan"),
                                    "dataset": node.get("dataset"),
                                    "baseline": node.get("baseline"),
                                    "figuresAndTables": node.get("figuresAndTables"),
                                    "resultAnalysis": node.get("resultAnalysis"),
                                    "codeTables": [
                                        artifact_ref(table)
                                        for table in node.get("codeTables", [])
                                        if isinstance(table, dict)
                                    ],
                                    "artifacts": [
                                        artifact_ref(artifact)
                                        for artifact in node.get("artifacts", [])
                                        if isinstance(artifact, dict)
                                    ],
                                }
                                for node in cart.get("nodeResults", [])
                                if isinstance(node, dict)
                            ],
                            "stages": [
                                {
                                    "stageId": stage.get("stage_id"),
                                    "title": stage.get("title"),
                                    "success": stage.get("success"),
                                }
                                for stage in cart.get("stages", [])
                                if isinstance(stage, dict)
                            ],
                        }
                        for cart in code_evidence.get("cartResults", [])
                        if isinstance(cart, dict)
                    ],
                    "experimentMetrics": [
                        {
                            "experimentId": exp.get("experimentId"),
                            "metrics": exp.get("metrics"),
                        }
                        for exp in code_evidence.get("experiments", [])
                        if isinstance(exp, dict)
                    ],
                }
                if any(metrics_sources.values()):
                    ctx["metrics_summary"] = json.dumps(metrics_sources, ensure_ascii=False, default=str)[:4000]
                code_figures = [
                    {
                        "figureId": f"{cart.get('cartId')}:{fig.get('nodeId')}:{fig.get('name')}",
                        "title": fig.get("name"),
                        "caption": f"Code-stage result artifact from {fig.get('nodeId')}: {fig.get('name')}",
                        "path": fig.get("cartRelativePath"),
                        "label": f"fig:code_{str(fig.get('nodeId') or '').replace('-', '_')}_{str(fig.get('name') or '').rsplit('.', 1)[0].replace('-', '_')}",
                        "targetSection": "Experiments",
                        "include": str(fig.get("name") or "").lower().endswith((".png", ".jpg", ".jpeg", ".pdf")),
                        "source": "code_cart",
                        "notes": "SVG figures are reported as evidence but require conversion before LaTeX inclusion.",
                    }
                    for cart in code_evidence.get("cartResults", [])
                    if isinstance(cart, dict)
                    for fig in cart.get("codeFigures", [])
                    if isinstance(fig, dict)
                ]
                if code_figures and ctx["figures_summary"] == "N/A":
                    ctx["figures_summary"] = json.dumps(code_figures, ensure_ascii=False, default=str)[:3000]
                code_tables = [
                    artifact_ref(table)
                    for cart in code_evidence.get("cartResults", [])
                    if isinstance(cart, dict)
                    for table in cart.get("codeTables", [])
                    if isinstance(table, dict)
                ]
                if code_tables:
                    ctx["code_tables_summary"] = json.dumps(code_tables, ensure_ascii=False, default=str)[:4000]
                run_sources = [
                    {
                        "runId": run.get("runId"),
                        "experimentIds": run.get("experimentIds"),
                        "experimentStatus": run.get("experimentStatus"),
                        "experimentDesign": run.get("experimentDesign"),
                        "executionSummary": run.get("executionSummary"),
                        "reportMdPath": run.get("reportMdPath"),
                        "experimentReport": run.get("experimentReport"),
                    }
                    for run in code_evidence.get("runs", [])
                    if isinstance(run, dict)
                ]
                if run_sources:
                    ctx["runs_summary"] = json.dumps(run_sources, ensure_ascii=False, default=str)[:4000]
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
                exp_metrics_summary = json.dumps(all_metrics[:30], default=str)
                if ctx["metrics_summary"] == "N/A":
                    ctx["metrics_summary"] = exp_metrics_summary[:2000]
                else:
                    ctx["metrics_summary"] = (ctx["metrics_summary"] + "\n" + exp_metrics_summary)[:4000]
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
        linked_figures = [
            {
                "figureId": f.get("figureId"),
                "title": f.get("title"),
                "caption": f.get("caption"),
                "path": f.get("path"),
                "label": f.get("label"),
                "targetSection": f.get("targetSection"),
                "target_section": f.get("targetSection"),
                "notes": f.get("notes"),
                "include": f.get("include", True),
                "figureType": f.get("figureType"),
                "experimentId": f.get("experimentId"),
                "source": f.get("source"),
            }
            for f in figure_entries
        ]
        if ctx["figures_summary"] == "N/A":
            ctx["figures_summary"] = json.dumps(linked_figures, default=str)[:2000]
        else:
            try:
                existing_figures = json.loads(ctx["figures_summary"])
                if not isinstance(existing_figures, list):
                    existing_figures = []
            except Exception:
                existing_figures = []
            ctx["figures_summary"] = json.dumps(existing_figures + linked_figures, default=str)[:4000]

    notes = paper.get("notes", "")
    if notes:
        ctx["user_notes"] = notes[:1000]

    return ctx


def load_venue_style_guide(venue: str, max_chars: int = 4000) -> str:
    """Load optional venue-specific writing guidance from the template directory."""
    template_dir = TEMPLATE_ROOT / venue
    if not template_dir.is_dir():
        return "N/A"

    for filename in ("style_guide.md", "writing_guide.md", "prompt_guide.md"):
        guide_path = template_dir / filename
        if guide_path.is_file():
            content = guide_path.read_text(encoding="utf-8").strip()
            return content[:max_chars] if content else "N/A"

    return "N/A"


def gate_outline(outline: Dict[str, Any]) -> List[str]:
    """Legacy diagnostic only; the active writing pipeline does not use this as a blocking quality gate."""
    issues = []
    sections = outline.get("sections", [])
    refs = outline.get("references", [])

    if len(sections) < 5:
        issues.append(f"Legacy diagnostic: compact outline has {len(sections)} section(s); consider adding coverage if the venue requires it.")
    if not refs:
        issues.append("Legacy diagnostic: outline has no references; cite only verified linked literature or venue-appropriate real sources.")

    algo_count = sum(1 for s in sections if s.get("hasAlgorithm"))
    eq_sections = sum(1 for s in sections if s.get("hasEquations"))
    table_sections = sum(1 for s in sections if s.get("hasTables"))

    if algo_count < 1:
        issues.append("Legacy diagnostic: no section is marked for an algorithm; include one only when the method evidence supports it.")
    if eq_sections < 1:
        issues.append("Legacy diagnostic: no section is marked for equations; include equations only when they clarify real method or analysis content.")
    if table_sections < 1:
        issues.append("Legacy diagnostic: no section is marked for tables; include tables only when linked metrics or table evidence supports them.")

    if not outline.get("abstract"):
        issues.append("Missing abstract")
    elif len(outline["abstract"].split()) < 50:
        issues.append(f"Legacy diagnostic: abstract is short ({len(outline['abstract'].split())} words); expand if the venue expects a full abstract.")

    return issues


def gate_evidence(sections_content: Dict[str, str]) -> Dict[str, Any]:
    """Legacy diagnostic only; counts are advisory and never define final generation status."""
    all_text = "\n".join(sections_content.values())

    algo_count = all_text.count("\\begin{algorithm")
    eq_count = all_text.count("\\begin{equation")
    table_count = all_text.count("\\begin{table")
    fig_count = all_text.count("\\includegraphics")
    cite_count = len(set(re.findall(r"\\cite\{([^}]+)\}", all_text)))

    gates = {
        "_mode": "legacy_diagnostic_only",
        "algorithms": {"count": algo_count, "required": None, "pass": True},
        "equations": {"count": eq_count, "required": None, "pass": True},
        "tables": {"count": table_count, "required": None, "pass": True},
        "figures": {"count": fig_count, "required": None, "pass": True},
        "citations": {"count": cite_count, "required": None, "pass": True},
    }
    gates["all_pass"] = True
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


def sanitize_latex_text_specials(content: str) -> str:
    """Escape text-mode LaTeX specials commonly emitted by LLM prose."""
    if not content:
        return content

    ampersand_alignment_envs = {
        "align",
        "align*",
        "aligned",
        "array",
        "bmatrix",
        "cases",
        "longtable",
        "matrix",
        "pmatrix",
        "smallmatrix",
        "split",
        "tabular",
        "tabularx",
        "vmatrix",
        "Vmatrix",
    }
    skip_arg_commands = {
        "bibliography",
        "bibliographystyle",
        "cite",
        "citep",
        "citet",
        "eqref",
        "href",
        "includegraphics",
        "input",
        "label",
        "ref",
        "url",
    }
    specials = {"_": r"\_", "%": r"\%", "#": r"\#"}
    out: List[str] = []
    alignment_env_stack: List[str] = []
    math_env_stack: List[str] = []
    i = 0
    inline_math_stack: List[str] = []

    def copy_balanced_group(start: int) -> int:
        depth = 0
        j = start
        while j < len(content):
            out.append(content[j])
            if content[j] == "\\" and j + 1 < len(content):
                j += 2
                if j <= len(content):
                    out.append(content[j - 1])
                continue
            if content[j] == "{":
                depth += 1
            elif content[j] == "}":
                depth -= 1
                if depth == 0:
                    return j + 1
            j += 1
        return j

    while i < len(content):
        ch = content[i]

        if content.startswith(r"\[", i) or content.startswith(r"\(", i):
            delimiter = content[i : i + 2]
            inline_math_stack.append(delimiter)
            out.append(delimiter)
            i += 2
            continue

        if content.startswith(r"\]", i) or content.startswith(r"\)", i):
            delimiter = content[i : i + 2]
            opener = r"\[" if delimiter == r"\]" else r"\("
            if opener in inline_math_stack:
                for idx in range(len(inline_math_stack) - 1, -1, -1):
                    if inline_math_stack[idx] == opener:
                        del inline_math_stack[idx:]
                        break
            out.append(delimiter)
            i += 2
            continue

        if ch == "\\":
            match = re.match(r"\\([A-Za-z]+)\*?", content[i:])
            if match:
                command = match.group(1)
                command_text = match.group(0)
                out.append(command_text)
                i += len(command_text)
                if command in {"begin", "end"}:
                    while i < len(content) and content[i].isspace():
                        out.append(content[i])
                        i += 1
                    env_match = re.match(r"\{([^}]+)\}", content[i:])
                    if env_match:
                        env_name = env_match.group(1)
                        out.append(env_match.group(0))
                        i += len(env_match.group(0))
                        if command == "begin" and env_name in ampersand_alignment_envs:
                            alignment_env_stack.append(env_name)
                        if command == "begin" and env_name in LATEX_MATH_ENVS:
                            math_env_stack.append(env_name)
                        elif command == "end" and env_name in alignment_env_stack:
                            for idx in range(len(alignment_env_stack) - 1, -1, -1):
                                if alignment_env_stack[idx] == env_name:
                                    del alignment_env_stack[idx:]
                                    break
                        if command == "end" and env_name in math_env_stack:
                            for idx in range(len(math_env_stack) - 1, -1, -1):
                                if math_env_stack[idx] == env_name:
                                    del math_env_stack[idx:]
                                    break
                    continue
                if command in skip_arg_commands:
                    while i < len(content) and content[i].isspace():
                        out.append(content[i])
                        i += 1
                    if i < len(content) and content[i] == "[":
                        depth = 0
                        while i < len(content):
                            out.append(content[i])
                            if content[i] == "[":
                                depth += 1
                            elif content[i] == "]":
                                depth -= 1
                                i += 1
                                if depth == 0:
                                    break
                                continue
                            i += 1
                    while i < len(content) and content[i].isspace():
                        out.append(content[i])
                        i += 1
                    if i < len(content) and content[i] == "{":
                        i = copy_balanced_group(i)
                continue

            out.append(ch)
            if i + 1 < len(content):
                out.append(content[i + 1])
                i += 2
            else:
                i += 1
            continue

        if content.startswith("$$", i):
            delimiter = "$$"
            if inline_math_stack and inline_math_stack[-1] == delimiter:
                inline_math_stack.pop()
            else:
                inline_math_stack.append(delimiter)
            out.append(delimiter)
            i += 2
            continue

        if ch == "$":
            delimiter = "$"
            if inline_math_stack and inline_math_stack[-1] == delimiter:
                inline_math_stack.pop()
            else:
                inline_math_stack.append(delimiter)
            out.append(ch)
            i += 1
            continue

        in_math = bool(inline_math_stack or math_env_stack)
        if not in_math and ch == "&" and not alignment_env_stack:
            out.append(r"\&")
            i += 1
            continue

        if not in_math and ch in specials:
            out.append(specials[ch])
        else:
            out.append(ch)
        i += 1

    return "".join(out)


def _count_tabular_columns(spec: str) -> int:
    """Return the declared number of columns in a simple LaTeX tabular spec."""
    count = 0
    i = 0
    while i < len(spec):
        ch = spec[i]
        if ch in {"l", "c", "r", "X"}:
            count += 1
            i += 1
            continue
        if ch in {"p", "m", "b"} and i + 1 < len(spec) and spec[i + 1] == "{":
            depth = 0
            i += 1
            while i < len(spec):
                if spec[i] == "{":
                    depth += 1
                elif spec[i] == "}":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                i += 1
            count += 1
            continue
        i += 1
    return count


def _count_row_columns(row: str) -> int:
    stripped = row.strip()
    if not stripped or stripped.startswith("\\"):
        return 0
    return len(re.findall(r"(?<!\\)&", row)) + 1


def _max_tabular_body_columns(body: str) -> int:
    rows = re.split(r"(?<!\\)\\\\", body)
    return max((_count_row_columns(row) for row in rows), default=0)


def normalize_tabular_column_specs(content: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Expand simple tabular column specs when LLM rows contain more columns."""
    if not content:
        return content, []

    rewrites: List[Dict[str, Any]] = []

    def normalize_simple_tabular(match: re.Match[str]) -> str:
        begin, spec, body, end = match.group(1), match.group(2), match.group(4), match.group(5)
        declared = _count_tabular_columns(spec)
        observed = _max_tabular_body_columns(body)
        if observed <= declared or declared <= 0:
            return match.group(0)
        replacement_spec = spec + ("c" * (observed - declared))
        rewrites.append({"from": spec, "to": replacement_spec, "declared": declared, "observed": observed})
        return f"{begin}{replacement_spec}}}{body}{end}"

    normalized = re.sub(
        r"(\\begin\{(?:tabular|longtable)\}\{)([^{}]+)(\})(.*?)(\\end\{(?:tabular|longtable)\})",
        normalize_simple_tabular,
        content,
        flags=re.DOTALL,
    )

    def normalize_tabularx(match: re.Match[str]) -> str:
        begin, spec, body, end = match.group(1), match.group(2), match.group(4), match.group(5)
        declared = _count_tabular_columns(spec)
        observed = _max_tabular_body_columns(body)
        if observed <= declared or declared <= 0:
            return match.group(0)
        replacement_spec = spec + ("c" * (observed - declared))
        rewrites.append({"from": spec, "to": replacement_spec, "declared": declared, "observed": observed})
        return f"{begin}{replacement_spec}}}{body}{end}"

    normalized = re.sub(
        r"(\\begin\{tabularx\}\{[^{}]+\}\{)([^{}]+)(\})(.*?)(\\end\{tabularx\})",
        normalize_tabularx,
        normalized,
        flags=re.DOTALL,
    )
    return normalized, rewrites


def normalize_section_citations(
    content: str,
    references: List[Dict[str, Any]],
) -> Tuple[str, List[Dict[str, str]]]:
    """Keep generated citations aligned with the BibTeX keys that will be written."""
    ordered_keys = [str(ref.get("key", "")).strip() for ref in references if str(ref.get("key", "")).strip()]
    known_keys = set(ordered_keys)
    if not content or not ordered_keys:
        return content, []

    rewrites: List[Dict[str, str]] = []

    def replace_cite(match: re.Match[str]) -> str:
        raw_keys = match.group(1)
        keys = [key.strip() for key in raw_keys.split(",") if key.strip()]
        valid_keys = [key for key in keys if key in known_keys]
        if len(valid_keys) == len(keys) and keys:
            return match.group(0)

        replacement = ",".join(valid_keys)
        rewrites.append({"from": raw_keys, "to": replacement})
        return f"\\cite{{{replacement}}}" if replacement else ""

    normalized = re.sub(r"\\cite\{([^}]+)\}", replace_cite, content)
    return normalized, rewrites


def normalize_duplicate_latex_labels(
    sections_content: Dict[str, str],
) -> Tuple[Dict[str, str], List[Dict[str, str]]]:
    """Rename repeated LaTeX label definitions so pdflatex does not emit duplicate-label warnings."""
    seen: set[str] = set()
    rewrites: List[Dict[str, str]] = []
    normalized_sections: Dict[str, str] = {}
    token_re = re.compile(r"\\(label|eqref|ref|autoref|pageref|cref|Cref)\{([^}]+)\}")

    for section_id, content in sections_content.items():
        counters: Dict[str, int] = {}
        local_renames: Dict[str, str] = {}

        def replace_token(match: re.Match[str]) -> str:
            command, value = match.group(1), match.group(2)
            if command == "label":
                label = value
                if label not in seen:
                    seen.add(label)
                    return match.group(0)

                counters[label] = counters.get(label, 0) + 1
                suffix = _clean_label_part(section_id)
                replacement = f"{label}:{suffix}"
                if counters[label] > 1:
                    replacement = f"{replacement}-{counters[label]}"
                while replacement in seen:
                    counters[label] += 1
                    replacement = f"{label}:{suffix}-{counters[label]}"
                seen.add(replacement)
                local_renames[label] = replacement
                rewrites.append({"section": section_id, "from": label, "to": replacement})
                return f"\\label{{{replacement}}}"

            if not local_renames:
                return match.group(0)

            parts = [part.strip() for part in value.split(",")]
            rewritten = [local_renames.get(part, part) for part in parts]
            if rewritten == parts:
                return match.group(0)
            return f"\\{command}{{{', '.join(rewritten)}}}"

        normalized_sections[section_id] = token_re.sub(replace_token, content)

    return normalized_sections, rewrites


def normalize_paper_authors(value: Any) -> List[str]:
    """Return explicitly supplied paper authors, or Anonymous when absent."""
    if isinstance(value, list):
        authors = [str(item).strip() for item in value if str(item).strip()]
    else:
        raw = str(value or "").strip()
        authors = [raw] if raw else []
    return authors or ["Anonymous"]


def build_main_tex(outline: Dict[str, Any], sections: List[Dict[str, Any]], venue: str) -> str:
    title = sanitize_latex_text_specials(outline.get("title", "Untitled Paper"))
    authors = normalize_paper_authors(outline.get("authors"))
    abstract = sanitize_latex_text_specials(outline.get("abstract", ""))
    running_title = title if len(title) <= 70 else title[:67] + "..."
    authors_text = sanitize_latex_text_specials(", ".join(authors[:4]))
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


def normalize_bibtex_authors(authors: Any) -> str:
    if isinstance(authors, list):
        cleaned_authors = [
            re.sub(r"\s+", " ", str(author).replace("&", "").strip())
            for author in authors
            if str(author).strip()
        ]
        return " and ".join(cleaned_authors) or "Unknown"

    text = str(authors or "Unknown").strip()
    if not text:
        return "Unknown"

    text = re.sub(r"\bet\s+al\.?", "and others", text)
    text = re.sub(r"\band\s*&\s*", "and ", text)
    text = re.sub(r"\s*&\s*", " and ", text)
    text = re.sub(r"\s*(?:,?\s+and\s+)?\.{3}\s*(?:and\s+)?", " and others and ", text)
    text = re.sub(r"\band\s+and\b", "and", text)
    text = re.sub(r"\band\s+others\s+and\s+[^,]+,\s*[^,]+$", "and others", text)
    text = re.sub(r"\s+", " ", text)
    if " and " in text and ", and " not in text:
        return text

    parts = [
        re.sub(r"^and\s+", "", part.strip())
        for part in text.split(",")
        if part.strip()
    ]
    if len(parts) >= 4 and len(parts) % 2 == 0:
        names = [f"{parts[i]}, {parts[i + 1]}" for i in range(0, len(parts), 2)]
        return " and ".join(names)

    return text


def escape_bibtex_field(value: Any) -> str:
    text = str(value or "")
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
    }
    return "".join(replacements.get(char, char) for char in text)


def build_bibtex(references: List[Dict[str, Any]]) -> str:
    entries = []
    for ref in references:
        key = ref.get("key", f"ref{len(entries)+1}")
        authors = normalize_bibtex_authors(ref.get("authors", "Unknown"))
        title = escape_bibtex_field(ref.get("title", "Untitled"))
        venue = escape_bibtex_field(ref.get("venue", "arXiv preprint"))
        year = ref.get("year", 2024)
        note = escape_bibtex_field(ref.get("note", ""))
        url = escape_bibtex_field(ref.get("url", ""))

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
        url_field = f"\n  url = {{{url}}}," if url else ""
        entries.append(
            f"""@{entry_type}{{{key},
  author = {{{authors}}},
  title = {{{title}}},
{venue_field}
  year = {{{year}}},{note_field}{url_field}
}}"""
        )
    return "\n\n".join(entries) + "\n"
