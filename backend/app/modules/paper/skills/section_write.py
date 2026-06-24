from typing import Dict

from app.modules.paper.storage import write_paper_file
from .base import PaperSkillContext, PaperSkillResult
from .section_writers import (
    build_refs_summary,
    build_section_draft_request,
    get_section_writer,
    parse_figures_summary,
)
from .utils import write_artifact


STEP_ID = "05_section_write"


def run(ctx: PaperSkillContext) -> PaperSkillResult:
    outline = ctx.get("outline", {})
    sections = outline.get("sections", [])
    refs = outline.get("references", [])
    context = ctx.get("context", {})

    sections_content: Dict[str, str] = {}
    writer_assignments = []
    prev_context = ""
    refs_summary = build_refs_summary(refs)
    parsed_figures = parse_figures_summary(context.get("figures_summary", "N/A"))

    for i, section in enumerate(sections):
        request = build_section_draft_request(
            ctx=ctx,
            outline=outline,
            section=section,
            section_index=i,
            total_sections=len(sections),
            refs_summary=refs_summary,
            parsed_figures=parsed_figures,
            prev_context=prev_context,
        )
        writer = get_section_writer(request.section_kind)
        content = writer.write(request)

        write_paper_file(ctx.paper_id, f"sections/{request.section_id}.tex", content)
        sections_content[request.section_id] = content
        prev_context = content[:400]
        writer_assignments.append({
            "sectionId": request.section_id,
            "title": request.section_title,
            "writer": writer.kind,
        })

    summary_lines = [
        "# Section Write",
        f"sections: {len(sections_content)}",
    ]
    summary_lines.extend(
        f"{assignment['sectionId']}: {assignment['writer']}"
        for assignment in writer_assignments
    )
    artifacts = write_artifact(
        ctx.paper_id,
        STEP_ID,
        {
            "section_ids": list(sections_content.keys()),
            "writer_assignments": writer_assignments,
        },
        summary_lines,
    )

    return PaperSkillResult(
        name="section_write",
        summary=f"{len(sections_content)} sections generated",
        artifacts=artifacts,
        data={
            "sections": sections,
            "sections_content": sections_content,
            "section_writer_assignments": writer_assignments,
        },
    )
