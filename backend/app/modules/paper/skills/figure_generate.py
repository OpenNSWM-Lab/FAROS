import json

from .base import PaperSkillContext, PaperSkillResult
from .utils import dedupe_figure_entries, get_linked_figure_entries, write_artifact


STEP_ID = "07_figure_generate"


def _summary_list(value: str) -> list[dict]:
    if not value or value == "N/A":
        return []
    try:
        parsed = json.loads(value)
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def run(ctx: PaperSkillContext) -> PaperSkillResult:
    figures_dir = f"{ctx.latex_dir}/figures"
    context = ctx.get("context", {})
    context_figures = _summary_list(context.get("figures_summary", "N/A")) if isinstance(context, dict) else []
    context_tables = _summary_list(context.get("code_tables_summary", "N/A")) if isinstance(context, dict) else []
    code_figure_entries = ctx.get("code_figure_entries", []) or []
    code_table_entries = ctx.get("code_table_entries", []) or context_tables
    existing_entries = ctx.get("figure_entries", []) or []
    linked_entries = get_linked_figure_entries(ctx.paper, ensure_copied=True)
    generated_entries = []
    summary = "0 linked + 0 generated figure(s)"
    try:
        from app.services.figure_generator import generate_all_figures
        generated_entries = generate_all_figures(figures_dir, ctx.paper.get("title", "Paper"))
        for entry in generated_entries:
            entry.setdefault("source", "generated")
    except Exception as exc:
        generated_entries = []
        summary = f"warning: {str(exc)[:200]}"

    figure_entries = dedupe_figure_entries(existing_entries + context_figures + code_figure_entries + linked_entries + generated_entries)
    if generated_entries or linked_entries or code_figure_entries:
        summary = f"{len(code_figure_entries)} code + {len(linked_entries)} linked + {len(generated_entries)} generated figure(s)"

    if isinstance(context, dict):
        if figure_entries:
            context["figures_summary"] = json.dumps([
                {
                    "figureId": entry.get("figureId"),
                    "title": entry.get("title"),
                    "caption": entry.get("caption"),
                    "path": entry.get("path"),
                    "label": entry.get("label"),
                    "targetSection": entry.get("targetSection"),
                    "include": entry.get("include", True),
                    "source": entry.get("source"),
                    "notes": entry.get("notes"),
                }
                for entry in figure_entries
            ], ensure_ascii=False, default=str)[:4000]
        if code_table_entries:
            context["code_tables_summary"] = json.dumps(code_table_entries, ensure_ascii=False, default=str)[:4000]
        ctx.update("context", context)

    summary_lines = [
        "# Figure Generate",
        f"code_figures: {len(code_figure_entries)}",
        f"code_tables: {len(code_table_entries)}",
        f"linked: {len(linked_entries)}",
        f"generated: {len(generated_entries)}",
        f"count: {len(figure_entries)}",
    ]
    artifacts = write_artifact(
        ctx.paper_id,
        STEP_ID,
        {
            "code_figures": code_figure_entries,
            "code_tables": code_table_entries,
            "code_warnings": ctx.get("code_artifact_warnings", []),
            "linked_figures": linked_entries,
            "generated_figures": generated_entries,
            "figures": figure_entries,
        },
        summary_lines,
    )
    return PaperSkillResult(
        name="figure_generate",
        summary=summary,
        artifacts=artifacts,
        data={
            "figure_entries": figure_entries,
            "code_table_entries": code_table_entries,
            "code_artifact_warnings": ctx.get("code_artifact_warnings", []),
        },
    )
