import os

from app.modules.paper.storage import update_paper
from .base import PaperSkillContext, PaperSkillResult
from .utils import write_artifact


STEP_ID = "09_compile_pdf"


def run(ctx: PaperSkillContext) -> PaperSkillResult:
    pdf_path = os.path.join(ctx.latex_dir, "main.pdf")
    status = "unknown"
    size = 0
    errors = None

    try:
        from app.services.pdf_renderer import compile_latex_project, render_paper_pdf
        compile_latex_project(ctx.latex_dir)
        if os.path.isfile(pdf_path):
            size = os.path.getsize(pdf_path)
        update_paper(ctx.paper_id, {"pdfAvailable": True})
        status = "latexmk"
    except Exception as exc:
        errors = str(exc)[:300]
        try:
            outline = ctx.get("outline", {})
            sections = ctx.get("sections", [])
            sections_content = ctx.get("sections_content", {})
            refs = outline.get("references", [])
            figures_dir = os.path.join(ctx.latex_dir, "figures")
            sections_for_pdf = [
                {"title": s.get("title", s["id"]), "content": sections_content.get(s["id"], "")}
                for s in sections
            ]
            render_paper_pdf(
                output_path=pdf_path,
                title=outline.get("title", ctx.paper.get("title", "Untitled")),
                authors=outline.get("authors", ["Anonymous"]),
                abstract=outline.get("abstract", ""),
                sections=sections_for_pdf,
                references=refs,
                figures_dir=figures_dir,
                figure_entries=ctx.get("figure_entries", []),
            )
            if os.path.isfile(pdf_path):
                size = os.path.getsize(pdf_path)
            update_paper(ctx.paper_id, {"pdfAvailable": True})
            status = "fallback"
        except Exception as fallback_error:
            errors = f"{errors}; fallback: {str(fallback_error)[:300]}"
            status = "failed"

    summary_lines = [
        "# Compile PDF",
        f"status: {status}",
        f"size: {size}",
    ]
    if errors:
        summary_lines.append(f"errors: {errors}")

    artifacts = write_artifact(
        ctx.paper_id,
        STEP_ID,
        {"status": status, "size": size, "errors": errors},
        summary_lines,
    )
    return PaperSkillResult(
        name="compile_pdf",
        summary=f"{status} ({size} bytes)" if size else status,
        artifacts=artifacts,
        data={"pdf_available": status != "failed"},
    )
