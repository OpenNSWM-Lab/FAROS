from .base import PaperSkillContext, PaperSkillResult
from .utils import dedupe_figure_entries, get_linked_figure_entries, write_artifact


STEP_ID = "07_figure_generate"


def run(ctx: PaperSkillContext) -> PaperSkillResult:
    figures_dir = f"{ctx.latex_dir}/figures"
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

    figure_entries = dedupe_figure_entries(linked_entries + generated_entries)
    if generated_entries or linked_entries:
        summary = f"{len(linked_entries)} linked + {len(generated_entries)} generated figure(s)"

    summary_lines = [
        "# Figure Generate",
        f"linked: {len(linked_entries)}",
        f"generated: {len(generated_entries)}",
        f"count: {len(figure_entries)}",
    ]
    artifacts = write_artifact(
        ctx.paper_id,
        STEP_ID,
        {
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
        data={"figure_entries": figure_entries},
    )
