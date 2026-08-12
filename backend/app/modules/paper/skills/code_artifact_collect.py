import json

from .base import PaperSkillContext, PaperSkillResult
from .code_evidence import materialize_code_artifacts_for_paper
from .utils import dedupe_figure_entries, figure_record_to_entry, write_artifact


STEP_ID = "02_code_artifacts"


def _summary_list(value: str) -> list[dict]:
    if not value or value == "N/A":
        return []
    try:
        parsed = json.loads(value)
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def _dedupe_table_entries(entries: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key = str(
            entry.get("tableId")
            or entry.get("id")
            or entry.get("path")
            or entry.get("filename")
            or entry.get("title")
            or ""
        ).strip()
        if not key:
            key = json.dumps(entry, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        deduped.append({
            key_name: value
            for key_name, value in entry.items()
            if key_name not in {"preview", "content", "rows", "data"}
        })
    return deduped


def _normalize_figure_entries(entries: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        figure_entry = figure_record_to_entry(entry, source=str(entry.get("source") or "code_artifact"))
        normalized.append(figure_entry or entry)
    return normalized


def run(ctx: PaperSkillContext) -> PaperSkillResult:
    code_artifacts = materialize_code_artifacts_for_paper(ctx.paper, ctx.paper_id)
    context = ctx.get("context", {})
    if isinstance(context, dict):
        if code_artifacts.get("figures"):
            merged_figures = dedupe_figure_entries(_normalize_figure_entries([
                *_summary_list(context.get("figures_summary", "N/A")),
                *code_artifacts["figures"],
            ]))
            context["figures_summary"] = json.dumps(merged_figures, ensure_ascii=False, default=str)[:4000]
        if code_artifacts.get("tables"):
            merged_tables = _dedupe_table_entries([
                *_summary_list(context.get("code_tables_summary", "N/A")),
                *code_artifacts["tables"],
            ])
            context["code_tables_summary"] = json.dumps(merged_tables, ensure_ascii=False, default=str)[:4000]
        ctx.update("context", context)

    summary_lines = [
        "# Code Artifacts",
        f"figures: {len(code_artifacts.get('figures', []))}",
        f"tables: {len(code_artifacts.get('tables', []))}",
        f"warnings: {len(code_artifacts.get('warnings', []))}",
    ]
    artifacts = write_artifact(ctx.paper_id, STEP_ID, code_artifacts, summary_lines)
    return PaperSkillResult(
        name="code_artifact_collect",
        summary=f"{len(code_artifacts.get('figures', []))} code figures, {len(code_artifacts.get('tables', []))} code tables",
        artifacts=artifacts,
        data={
            "figure_entries": code_artifacts.get("figures", []),
            "code_figure_entries": code_artifacts.get("figures", []),
            "code_table_entries": code_artifacts.get("tables", []),
            "code_artifact_warnings": code_artifacts.get("warnings", []),
        },
        warnings=code_artifacts.get("warnings", []),
    )
