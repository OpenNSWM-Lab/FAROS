import os

from app.modules.paper.storage import write_paper_file
from .base import PaperSkillContext, PaperSkillResult
from .utils import (
    build_bibtex,
    build_main_tex,
    copy_template_assets,
    normalize_section_figure_references,
    write_artifact,
)


STEP_ID = "07_assemble_latex"


def run(ctx: PaperSkillContext) -> PaperSkillResult:
    outline = ctx.get("outline", {})
    sections = ctx.get("sections", [])
    refs = outline.get("references", [])
    sections_content = ctx.get("sections_content", {})
    figure_entries = ctx.get("figure_entries", [])
    figures_dir = os.path.join(ctx.latex_dir, "figures")

    copy_template_assets(ctx.venue, ctx.paper_id)
    figure_rewrites = []
    for section in sections:
        section_id = section.get("id")
        if not section_id or section_id not in sections_content:
            continue
        normalized_content, rewrites = normalize_section_figure_references(
            sections_content[section_id],
            figure_entries,
            figures_dir,
        )
        if rewrites:
            write_paper_file(ctx.paper_id, f"sections/{section_id}.tex", normalized_content)
            sections_content[section_id] = normalized_content
            figure_rewrites.extend(
                {"section": section_id, "from": r["from"], "to": r["to"]}
                for r in rewrites
            )

    main_tex = build_main_tex(outline, sections, ctx.venue)
    write_paper_file(ctx.paper_id, "main.tex", main_tex)

    bibtex = build_bibtex(refs)
    write_paper_file(ctx.paper_id, "refs.bib", bibtex)

    readme_content = f"# {outline.get('title', ctx.paper.get('title', 'Paper'))}\n\n"
    readme_content += f"**Paper type:** {ctx.paper_type}  \n"
    readme_content += f"**Target venue:** {ctx.venue_cfg['name']}  \n\n"
    readme_content += "## Build Instructions\n\n"
    readme_content += "```bash\n# Option 1: latexmk (recommended)\nlatexmk -pdf main.tex\n\n"
    readme_content += "# Option 2: manual\npdflatex main.tex\nbibtex main\npdflatex main.tex\npdflatex main.tex\n```\n\n"
    readme_content += "## Structure\n\n```\n"
    readme_content += "main.tex          # Main document\n"
    readme_content += "refs.bib          # Bibliography\n"
    readme_content += "sections/         # Individual sections\n"
    for s in sections:
        readme_content += f"  {s['id']}.tex      # {s.get('title', s['id'])}\n"
    readme_content += "figures/          # Generated figures\n"
    readme_content += "```\n"
    write_paper_file(ctx.paper_id, "README.md", readme_content)

    summary_lines = [
        "# Assemble LaTeX",
        f"sections: {len(sections)}",
        f"refs: {len(refs)}",
        f"figure reference rewrites: {len(figure_rewrites)}",
    ]
    artifacts = write_artifact(
        ctx.paper_id,
        STEP_ID,
        {"sections": len(sections), "references": len(refs), "figure_rewrites": figure_rewrites},
        summary_lines,
    )
    return PaperSkillResult(
        name="assemble_latex",
        summary="LaTeX assembled",
        artifacts=artifacts,
        data={},
    )
