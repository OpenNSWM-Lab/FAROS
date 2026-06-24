"""
Paper orchestrator using skill-based pipeline.

Each skill emits intermediate artifacts under: artifacts/<step>.{json,md}
"""

import logging
import time
from typing import Any, Dict

from app.core.settings import get_settings
from app.llm.provider_client import get_provider_client
from app.modules.paper.storage import add_log, get_paper, get_paper_latex_dir, update_paper
from app.modules.paper.skills import PaperSkillContext, PaperSkillLeader, build_default_skill_chain
from app.modules.paper.skills.collect_context import run as collect_context_skill
from app.modules.paper.skills.constants import VENUE_CONFIGS
from app.modules.paper.skills.outline import build_outline
from app.modules.paper.skills.paper_brief import build_brief
from app.modules.paper.skills.section_rewrite import rewrite_section
from app.modules.paper.skills.utils import ensure_artifacts_dir

logger = logging.getLogger(__name__)


def _build_skill_context(paper_id: str, paper: Dict[str, Any], step_log: list[Dict[str, Any]]) -> PaperSkillContext:
    settings = get_settings()
    provider_name = paper.get("providerName") or settings.get_active_provider()
    model = paper.get("model") or settings.get_active_model(provider_name)
    paper_type = paper.get("paperType", "algorithm")
    venue = paper.get("targetVenue", "generic")
    venue_cfg = VENUE_CONFIGS.get(venue, VENUE_CONFIGS["generic"])
    client = get_provider_client(provider_name)
    latex_dir = get_paper_latex_dir(paper_id)
    artifacts_dir = ensure_artifacts_dir(paper_id)

    return PaperSkillContext(
        paper_id=paper_id,
        paper=paper,
        settings=settings,
        provider_name=provider_name,
        model=model,
        paper_type=paper_type,
        venue=venue,
        venue_cfg=venue_cfg,
        client=client,
        latex_dir=latex_dir,
        artifacts_dir=artifacts_dir,
        data={},
        step_log=step_log,
    )


def _apply_result_data(ctx: PaperSkillContext, result: Any) -> None:
    if result.data:
        for k, v in result.data.items():
            ctx.update(k, v)


def generate_paper_brief(paper_id: str, brief_user_edits: str | None = None, force: bool = True) -> Dict[str, Any]:
    paper = get_paper(paper_id)
    if not paper:
        raise ValueError(f"Paper not found: {paper_id}")

    if brief_user_edits is not None:
        paper = update_paper(paper_id, {"briefUserEdits": brief_user_edits}) or paper

    step_log: list[Dict[str, Any]] = []
    ctx = _build_skill_context(paper_id, paper, step_log)
    add_log(paper_id, "Running skill: collect_context")
    context_result = collect_context_skill(ctx)
    _apply_result_data(ctx, context_result)
    add_log(paper_id, f"collect_context: {context_result.summary}")

    add_log(paper_id, "Running skill: paper_brief")
    brief_result = build_brief(ctx, force=force)
    _apply_result_data(ctx, brief_result)
    add_log(paper_id, f"paper_brief: {brief_result.summary}")
    if brief_result.artifacts:
        add_log(paper_id, f"Artifacts: {', '.join(brief_result.artifacts)}")

    return get_paper(paper_id)


def generate_paper_outline(paper_id: str, force: bool = True) -> Dict[str, Any]:
    paper = get_paper(paper_id)
    if not paper:
        raise ValueError(f"Paper not found: {paper_id}")

    step_log: list[Dict[str, Any]] = []
    ctx = _build_skill_context(paper_id, paper, step_log)

    add_log(paper_id, "Running skill: collect_context")
    context_result = collect_context_skill(ctx)
    _apply_result_data(ctx, context_result)
    add_log(paper_id, f"collect_context: {context_result.summary}")

    add_log(paper_id, "Running skill: paper_brief")
    brief_result = build_brief(ctx, force=False)
    _apply_result_data(ctx, brief_result)
    add_log(paper_id, f"paper_brief: {brief_result.summary}")

    refreshed = get_paper(paper_id) or paper
    ctx.paper = refreshed

    add_log(paper_id, "Running skill: outline")
    outline_result = build_outline(ctx, force=force)
    _apply_result_data(ctx, outline_result)
    add_log(paper_id, f"outline: {outline_result.summary}")
    if outline_result.artifacts:
        add_log(paper_id, f"Artifacts: {', '.join(outline_result.artifacts)}")

    return get_paper(paper_id)


def generate_paper(paper_id: str) -> Dict[str, Any]:
    paper = get_paper(paper_id)
    if not paper:
        raise ValueError(f"Paper not found: {paper_id}")

    update_paper(paper_id, {"status": "generating"})
    step_log = []

    def _log(msg: str) -> None:
        add_log(paper_id, msg)
        step_log.append({"time": time.time(), "msg": msg})
        logger.info(f"[{paper_id}] {msg}")

    try:
        ctx = _build_skill_context(paper_id, paper, step_log)

        leader = PaperSkillLeader(paper_id, _log)
        skills = build_default_skill_chain()
        leader.run(ctx, skills)

        outline = ctx.get("outline", {})
        references = outline.get("references", [])
        sections = outline.get("sections", [])
        figure_entries = ctx.get("figure_entries", [])
        evidence_gates = ctx.get("evidence_gates", {})
        pdf_available = ctx.get("pdf_available", False)

        update_paper(paper_id, {
            "status": "completed",
            "targetVenue": ctx.venue,
            "templateId": ctx.venue,
            "evidenceGates": evidence_gates,
            "figureCount": len(figure_entries),
            "sectionCount": len(sections),
            "referenceCount": len(references),
            "pdfAvailable": pdf_available,
        })
        _log("Paper generation completed successfully")

    except Exception as exc:
        logger.error(f"Paper generation failed: {exc}", exc_info=True)
        update_paper(paper_id, {"status": "failed"})
        add_log(paper_id, f"FAILED: {str(exc)[:500]}")
        raise

    return get_paper(paper_id)


def rewrite_paper_section(
    paper_id: str,
    section_id: str,
    instruction: str = "",
    mode: str = "improve",
    preserve_citations: bool = True,
    preserve_figures: bool = True,
    target_length: int | None = None,
) -> Dict[str, Any]:
    paper = get_paper(paper_id)
    if not paper:
        raise ValueError(f"Paper not found: {paper_id}")
    if ".." in section_id or "/" in section_id or "\\" in section_id:
        raise ValueError("Invalid section_id")

    step_log: list[Dict[str, Any]] = []
    ctx = _build_skill_context(paper_id, paper, step_log)

    add_log(paper_id, "Running skill: collect_context")
    context_result = collect_context_skill(ctx)
    _apply_result_data(ctx, context_result)
    add_log(paper_id, f"collect_context: {context_result.summary}")

    add_log(paper_id, f"Running skill: section_rewrite:{section_id}")
    rewrite_result = rewrite_section(
        ctx,
        section_id,
        instruction=instruction,
        mode=mode,
        preserve_citations=preserve_citations,
        preserve_figures=preserve_figures,
        target_length=target_length,
    )
    _apply_result_data(ctx, rewrite_result)
    add_log(paper_id, f"section_rewrite: {rewrite_result.summary}")
    if rewrite_result.warnings:
        add_log(paper_id, f"section_rewrite warnings: {'; '.join(rewrite_result.warnings[:3])}")
    if rewrite_result.artifacts:
        add_log(paper_id, f"Artifacts: {', '.join(rewrite_result.artifacts)}")

    update_paper(paper_id, {
        "pdfAvailable": False,
        "lastSectionRewrite": {
            "sectionId": rewrite_result.data.get("sectionId", section_id),
            "path": rewrite_result.data.get("path"),
            "mode": mode,
            "timestamp": time.time(),
            "warnings": rewrite_result.warnings,
        },
    })

    return {
        "paperId": paper_id,
        "sectionId": rewrite_result.data.get("sectionId", section_id),
        "path": rewrite_result.data.get("path"),
        "content": rewrite_result.data.get("content", ""),
        "beforeWordCount": rewrite_result.data.get("beforeWordCount"),
        "afterWordCount": rewrite_result.data.get("afterWordCount"),
        "warnings": rewrite_result.warnings,
        "artifacts": rewrite_result.artifacts,
    }
