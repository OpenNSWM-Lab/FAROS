"""
Paper orchestrator using skill-based pipeline.

Paper artifacts are JSON-only records under artifacts/ with stable names:
evidence.json, brief.json, outline.json, code_artifacts.json, assembly.json,
and feedback/*.json.
"""

import logging
import time
from typing import Any, Callable, Dict

from app.core.settings import get_settings
from app.llm.provider_client import get_provider_client
from app.modules.paper.agents import PaperAgentOrchestrator
from app.modules.paper.storage import add_log, get_paper, get_paper_latex_dir, update_paper
from app.modules.paper.skills import PaperSkillContext
from app.modules.paper.skills.collect_context import run as collect_context_skill
from app.modules.paper.skills.constants import VENUE_CONFIGS
from app.modules.paper.skills.evidence_collect import run as evidence_collect_skill
from app.modules.paper.skills.outline import build_outline
from app.modules.paper.skills.paper_brief import build_brief
from app.modules.paper.skills.section_rewrite import rewrite_section
from app.modules.paper.skills.utils import ensure_artifacts_dir, reset_artifacts_dir

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


def _run_skill(
    paper_id: str,
    ctx: PaperSkillContext,
    label: str,
    skill: Callable[[PaperSkillContext], Any],
) -> Any:
    add_log(paper_id, f"Running skill: {label}")
    result = skill(ctx)
    _apply_result_data(ctx, result)
    add_log(paper_id, f"{result.name}: {result.summary}")
    return result


def _run_skill_sequence(
    paper_id: str,
    ctx: PaperSkillContext,
    skills: list[tuple[str, Callable[[PaperSkillContext], Any]]],
) -> list[Any]:
    results = []
    for label, skill in skills:
        results.append(_run_skill(paper_id, ctx, label, skill))
    return results


def _run_prerequisite_skills(paper_id: str, ctx: PaperSkillContext) -> None:
    _run_skill_sequence(
        paper_id,
        ctx,
        [
            ("evidence_collect", evidence_collect_skill),
            ("collect_context", collect_context_skill),
        ],
    )


def collect_paper_evidence(paper_id: str, force: bool = True) -> Dict[str, Any]:
    paper = get_paper(paper_id)
    if not paper:
        raise ValueError(f"Paper not found: {paper_id}")

    existing = paper.get("evidenceJson")
    if existing and not force:
        return {
            "paperId": paper_id,
            "evidence": existing,
            "evidenceStatus": paper.get("evidenceStatus", "collected"),
        }

    step_log: list[Dict[str, Any]] = []
    ctx = _build_skill_context(paper_id, paper, step_log)
    result = _run_skill(paper_id, ctx, "evidence_collect", evidence_collect_skill)
    evidence = result.data.get("plan_evidence", {})
    updated = get_paper(paper_id) or paper
    artifacts = result.artifacts
    return {
        "paperId": paper_id,
        "evidence": updated.get("evidenceJson", evidence),
        "evidenceStatus": updated.get("evidenceStatus", "missing"),
        "artifacts": artifacts,
    }


def generate_paper_brief(paper_id: str, brief_user_edits: str | None = None, force: bool = True) -> Dict[str, Any]:
    paper = get_paper(paper_id)
    if not paper:
        raise ValueError(f"Paper not found: {paper_id}")

    if brief_user_edits is not None:
        paper = update_paper(paper_id, {"briefUserEdits": brief_user_edits}) or paper

    step_log: list[Dict[str, Any]] = []
    ctx = _build_skill_context(paper_id, paper, step_log)
    _run_prerequisite_skills(paper_id, ctx)

    _run_skill_sequence(
        paper_id,
        ctx,
        [("paper_brief", lambda skill_ctx: build_brief(skill_ctx, force=force))],
    )

    return get_paper(paper_id)


def generate_paper_outline(paper_id: str, force: bool = True) -> Dict[str, Any]:
    paper = get_paper(paper_id)
    if not paper:
        raise ValueError(f"Paper not found: {paper_id}")

    step_log: list[Dict[str, Any]] = []
    ctx = _build_skill_context(paper_id, paper, step_log)

    _run_prerequisite_skills(paper_id, ctx)

    _run_skill_sequence(
        paper_id,
        ctx,
        [("paper_brief", lambda skill_ctx: build_brief(skill_ctx, force=False))],
    )

    refreshed = get_paper(paper_id) or paper
    ctx.paper = refreshed

    _run_skill_sequence(
        paper_id,
        ctx,
        [("outline", lambda skill_ctx: build_outline(skill_ctx, force=force))],
    )

    return get_paper(paper_id)


def _paper_final_status(compile_status: str | None, simple_review_passed: bool) -> str:
    """Generation status is stricter than preview availability.

    pdfAvailable only means a preview PDF exists, which may come from the
    fallback renderer. A paper is completed only after latexmk succeeds and the
    simple review loop has no blocking/major issues.
    """
    return "completed" if compile_status == "latexmk" and simple_review_passed else "failed"


def generate_paper(paper_id: str) -> Dict[str, Any]:
    paper = get_paper(paper_id)
    if not paper:
        raise ValueError(f"Paper not found: {paper_id}")

    update_paper(paper_id, {
        "status": "generating",
        "pdfAvailable": False,
        "compileStatus": None,
        "pdfRenderMode": None,
        "compileErrors": None,
        "simpleReviewPassed": False,
    })
    step_log = []
    reset_artifacts_dir(paper_id)

    def _log(msg: str) -> None:
        add_log(paper_id, msg)
        step_log.append({"time": time.time(), "msg": msg})
        logger.info(f"[{paper_id}] {msg}")

    try:
        ctx = _build_skill_context(paper_id, paper, step_log)

        orchestrator = PaperAgentOrchestrator(paper_id, _log)
        orchestrator.run(ctx)

        outline = ctx.get("outline", {})
        references = outline.get("references", [])
        sections = outline.get("sections", [])
        figure_entries = ctx.get("figure_entries", [])
        pdf_available = ctx.get("pdf_available", False)
        compile_status = ctx.get("compile_status")
        simple_review_passed = ctx.get("simple_review_passed", False)
        final_status = _paper_final_status(compile_status, simple_review_passed)

        update_paper(paper_id, {
            "status": final_status,
            "targetVenue": ctx.venue,
            "templateId": ctx.venue,
            "figureCount": len(figure_entries),
            "sectionCount": len(sections),
            "referenceCount": len(references),
            "pdfAvailable": pdf_available,
            "compileStatus": compile_status,
            "compileErrors": ctx.get("compile_errors"),
            "simpleReviewPassed": simple_review_passed,
        })
        if final_status == "completed":
            _log("Paper generation completed successfully")
        elif compile_status != "latexmk":
            _log("Paper generation finished with unresolved LaTeX compile errors")
        else:
            _log("Paper generation finished with unresolved simple review issues")

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

    _run_prerequisite_skills(paper_id, ctx)

    rewrite_result = _run_skill(
        paper_id,
        ctx,
        f"section_rewrite:{section_id}",
        lambda skill_ctx: rewrite_section(
            skill_ctx,
            section_id,
            instruction=instruction,
            mode=mode,
            preserve_citations=preserve_citations,
            preserve_figures=preserve_figures,
            target_length=target_length,
        ),
    )
    if rewrite_result.warnings:
        add_log(paper_id, f"section_rewrite warnings: {'; '.join(rewrite_result.warnings[:3])}")

    update_paper(paper_id, {
        "status": "created",
        "pdfAvailable": False,
        "compileStatus": None,
        "pdfRenderMode": None,
        "compileErrors": None,
        "simpleReviewPassed": False,
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
