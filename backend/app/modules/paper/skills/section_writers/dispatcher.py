import json
import re
from typing import Any, Dict, List

from app.modules.paper.skills.base import PaperSkillContext
from .analysis import AnalysisWriter
from .background import BackgroundWriter
from .base import SectionDraftRequest, SectionWriter, build_artifact_requirements
from .conclusion import ConclusionWriter
from .experiments import ExperimentsWriter
from .generic import GenericSectionWriter
from .introduction import IntroductionWriter
from .method import MethodWriter
from .related_work import RelatedWorkWriter


_WRITERS: Dict[str, SectionWriter] = {
    "introduction": IntroductionWriter(),
    "related_work": RelatedWorkWriter(),
    "background": BackgroundWriter(),
    "method": MethodWriter(),
    "experiments": ExperimentsWriter(),
    "analysis": AnalysisWriter(),
    "conclusion": ConclusionWriter(),
    "generic": GenericSectionWriter(),
}


def _section_text(section: Dict[str, Any]) -> str:
    parts = [
        str(section.get("id") or ""),
        str(section.get("title") or ""),
        " ".join(str(item) for item in section.get("keyPoints", []) if item),
    ]
    return " ".join(parts).lower()


def classify_section(section: Dict[str, Any]) -> str:
    text = _section_text(section)
    normalized = re.sub(r"[^a-z0-9]+", " ", text)

    if any(kw in normalized for kw in ["intro", "motivation", "overview and contribution"]):
        return "introduction"
    if any(kw in normalized for kw in ["related work", "literature", "prior work", "comparison to prior"]):
        return "related_work"
    if any(kw in normalized for kw in ["background", "preliminar", "problem setup", "notation", "definitions"]):
        return "background"
    if any(kw in normalized for kw in [
        "experiment", "evaluation", "result", "benchmark", "empirical", "dataset", "metric", "baseline"
    ]):
        return "experiments"
    if any(kw in normalized for kw in [
        "method", "methodology", "approach", "algorithm", "model", "architecture", "framework",
        "system design", "implementation", "design"
    ]):
        return "method"
    if any(kw in normalized for kw in [
        "analysis", "discussion", "ablation", "limitation", "case study", "sensitivity", "error"
    ]):
        return "analysis"
    if any(kw in normalized for kw in ["conclusion", "future work", "closing"]):
        return "conclusion"
    return "generic"


def get_section_writer(section_or_kind: Dict[str, Any] | str) -> SectionWriter:
    kind = section_or_kind if isinstance(section_or_kind, str) else classify_section(section_or_kind)
    return _WRITERS.get(kind, _WRITERS["generic"])


def build_refs_summary(refs: List[Dict[str, Any]], max_refs: int = 18) -> str:
    return ", ".join(
        f"{r.get('key', 'ref')}: {str(r.get('title', ''))[:50]}"
        for r in refs[:max_refs]
        if isinstance(r, dict)
    )


def parse_figures_summary(figures_summary: str) -> List[Dict[str, Any]]:
    if not figures_summary or figures_summary == "N/A":
        return []
    try:
        parsed = json.loads(figures_summary)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict) and item.get("include", True)]


def _section_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def figure_targets_section(fig: Dict[str, Any], section: Dict[str, Any], section_title: str) -> bool:
    target = _section_key(str(fig.get("targetSection") or fig.get("target_section") or ""))
    if not target:
        return False
    section_keys = {
        _section_key(str(section.get("id", ""))),
        _section_key(section_title),
    }
    return any(target == key or target in key or key in target for key in section_keys if key)


def split_figures_for_section(
    figures_summary: str,
    parsed_figures: List[Dict[str, Any]],
    section: Dict[str, Any],
    section_title: str,
) -> Dict[str, Any]:
    section_figures = [
        fig for fig in parsed_figures
        if figure_targets_section(fig, section, section_title)
    ]
    untargeted_figures = [
        fig for fig in parsed_figures
        if not (fig.get("targetSection") or fig.get("target_section"))
    ]
    figures_for_prompt = section_figures if section_figures else untargeted_figures
    return {
        "section_figures": section_figures,
        "figures_for_prompt": figures_for_prompt,
        "figures_data": json.dumps(figures_for_prompt, ensure_ascii=False) if figures_for_prompt else "N/A",
        "section_figures_data": json.dumps(section_figures, ensure_ascii=False) if section_figures else "N/A",
        "figures_summary": figures_summary,
    }


def build_section_draft_request(
    ctx: PaperSkillContext,
    outline: Dict[str, Any],
    section: Dict[str, Any],
    section_index: int,
    total_sections: int,
    refs_summary: str,
    parsed_figures: List[Dict[str, Any]],
    prev_context: str,
) -> SectionDraftRequest:
    context = ctx.get("context", {})
    figures_summary = context.get("figures_summary", "N/A")
    section_id = str(section.get("id") or f"section_{section_index + 1}")
    section_title = str(section.get("title") or f"Section {section_index + 1}")
    figure_ctx = split_figures_for_section(figures_summary, parsed_figures, section, section_title)
    artifact_requirements = build_artifact_requirements(
        ctx,
        section,
        section_title,
        figures_summary,
        figure_ctx["figures_for_prompt"],
        figure_ctx["section_figures"],
    )
    section_kind = classify_section(section)
    return SectionDraftRequest(
        ctx=ctx,
        outline=outline,
        section=section,
        section_id=section_id,
        section_title=section_title,
        section_index=section_index,
        total_sections=total_sections,
        section_kind=section_kind,
        context=context,
        paper_brief=ctx.get("paper_brief", {}),
        contributions=outline.get("contributions", []),
        refs_summary=refs_summary,
        prev_context=prev_context,
        figures_data=figure_ctx["figures_data"],
        section_figures_data=figure_ctx["section_figures_data"],
        requirements_text=artifact_requirements["requirements_text"],
        min_words=artifact_requirements["min_words"],
        algo_req=artifact_requirements["algo_req"],
        eq_req=artifact_requirements["eq_req"],
        table_req=artifact_requirements["table_req"],
        fig_req=artifact_requirements["fig_req"],
    )
