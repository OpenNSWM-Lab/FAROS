import json
from typing import Any, Dict

from app.llm.provider_client import ChatMessage
from app.modules.paper.storage import update_paper
from .base import PaperSkillContext, PaperSkillResult
from .utils import _extract_json, load_venue_style_guide, write_artifact


STEP_ID = "02_paper_brief"

BRIEF_PROMPT = """You are preparing a writing brief before drafting an academic paper.

**Title:** {title}
**Paper type:** {paper_type}
**Venue:** {venue_name}
**Venue style guide:** {venue_style_guide}
**Plan context:** {plan_context}
**Project summary:** {project_summary}
**Experiment metrics:** {metrics_summary}
**Run evidence:** {runs_summary}
**Available figures:** {figures_summary}
**User notes:** {user_notes}
**User brief edits:** {brief_user_edits}

Create a concise, concrete paper writing brief. The brief must guide the outline and section writing. It must not invent unsupported experiments, datasets, baselines, or claims. Use the Venue style guide to adapt the paper angle, content ordering, evidence emphasis, and tone to the target venue.

Return strict JSON:
{{
  "research_question": "...",
  "core_claim": "...",
  "paper_angle": "system | algorithm | benchmark | survey | application | security | position",
  "target_audience": "...",
  "contributions": ["...", "...", "..."],
  "must_use_evidence": ["metric or run evidence that must be discussed"],
  "must_use_figures": [
    {{"label": "fig:...", "path": "figures/...", "caption": "...", "target_section": "Experiments"}}
  ],
  "section_priorities": {{
    "Introduction": ["..."],
    "Method": ["..."],
    "Experiments": ["..."],
    "Analysis": ["..."]
  }},
  "avoid_claims": ["Do not claim ..."]
}}
Return ONLY valid JSON, no markdown fences.
"""


def _fallback_brief(ctx: PaperSkillContext, context: Dict[str, str], brief_user_edits: str) -> Dict[str, Any]:
    title = ctx.paper.get("title", "Untitled Paper")
    figures = context.get("figures_summary", "N/A")
    metrics = context.get("metrics_summary", "N/A")
    must_use_evidence = []
    if metrics != "N/A":
        must_use_evidence.append("Use the linked experiment metrics when discussing results.")
    if context.get("runs_summary", "N/A") != "N/A":
        must_use_evidence.append("Use linked run evidence for implementation or execution claims.")

    must_use_figures = []
    if figures != "N/A":
        must_use_figures.append({
            "label": "linked paper figures",
            "path": "figures/",
            "caption": "Use linked experiment figure captions where relevant.",
            "target_section": "Experiments",
        })

    return {
        "research_question": f"What problem does {title} solve, and what evidence supports the proposed approach?",
        "core_claim": "The paper should make only claims supported by linked plans, experiments, runs, figures, or user notes.",
        "paper_angle": ctx.paper_type,
        "target_audience": f"{ctx.venue_cfg['name']} reviewers and researchers in the paper topic area.",
        "contributions": [
            "Define the research problem and motivation clearly.",
            "Describe the proposed method or system in enough technical detail.",
            "Ground the evaluation in linked metrics, runs, and figures where available.",
        ],
        "must_use_evidence": must_use_evidence,
        "must_use_figures": must_use_figures,
        "section_priorities": {
            "Introduction": ["motivation", "problem statement", "contributions"],
            "Method": ["technical design", "assumptions", "implementation details"],
            "Experiments": ["linked metrics", "baselines", "figure discussion"],
            "Analysis": ["trade-offs", "limitations", "evidence-backed interpretation"],
        },
        "avoid_claims": [
            "Do not invent datasets, baselines, or experimental results.",
            "Do not claim state-of-the-art performance without explicit evidence.",
        ],
        "user_brief_edits": brief_user_edits,
    }


def _normalize_brief(brief: Dict[str, Any], ctx: PaperSkillContext, brief_user_edits: str) -> Dict[str, Any]:
    brief.setdefault("research_question", f"What does {ctx.paper.get('title', 'this paper')} investigate?")
    brief.setdefault("core_claim", "Claims should be grounded in the linked context and evidence.")
    brief.setdefault("paper_angle", ctx.paper_type)
    brief.setdefault("target_audience", ctx.venue_cfg["name"])
    brief.setdefault("contributions", [])
    brief.setdefault("must_use_evidence", [])
    brief.setdefault("must_use_figures", [])
    brief.setdefault("section_priorities", {})
    brief.setdefault("avoid_claims", [])
    if brief_user_edits:
        brief["user_brief_edits"] = brief_user_edits
    return brief


def build_brief(ctx: PaperSkillContext, force: bool = False) -> PaperSkillResult:
    context = ctx.get("context", {})
    brief_user_edits = (ctx.paper.get("briefUserEdits") or "").strip()
    existing = ctx.paper.get("briefJson")
    source = "existing"

    if existing and not force:
        brief = _normalize_brief(existing, ctx, brief_user_edits)
    else:
        prompt = BRIEF_PROMPT.format(
            title=ctx.paper.get("title", "Untitled"),
            paper_type=ctx.paper_type,
            venue_name=ctx.venue_cfg["name"],
            venue_style_guide=load_venue_style_guide(ctx.venue)[:2500],
            plan_context=context.get("plan_context", "N/A")[:1500],
            project_summary=context.get("project_summary", "N/A")[:1500],
            metrics_summary=context.get("metrics_summary", "N/A")[:1500],
            runs_summary=context.get("runs_summary", "N/A")[:1500],
            figures_summary=context.get("figures_summary", "N/A")[:1500],
            user_notes=context.get("user_notes", "N/A"),
            brief_user_edits=brief_user_edits or "N/A",
        )
        try:
            resp = ctx.client.chat(
                messages=[ChatMessage(role="user", content=prompt)],
                model=ctx.model,
                temperature=0.25,
                max_tokens=3000,
                timeout=ctx.llm_timeout(),
            )
            parsed = _extract_json(resp.text)
            if not parsed:
                raise ValueError(f"LLM returned invalid brief: {resp.text[:300]}")
            brief = _normalize_brief(parsed, ctx, brief_user_edits)
            source = "generated"
        except Exception:
            brief = _fallback_brief(ctx, context, brief_user_edits)
            source = "fallback"

        update_paper(ctx.paper_id, {
            "briefJson": brief,
            "briefStatus": source,
            "briefUserEdits": brief_user_edits,
        })

    summary_lines = [
        "# Paper Brief",
        f"source: {source}",
        f"research_question: {brief.get('research_question', '')}",
        f"core_claim: {brief.get('core_claim', '')}",
        f"contributions: {len(brief.get('contributions', []))}",
        f"must_use_evidence: {len(brief.get('must_use_evidence', []))}",
        f"must_use_figures: {len(brief.get('must_use_figures', []))}",
        f"user_edits: {'yes' if brief_user_edits else 'no'}",
    ]
    artifacts = write_artifact(
        ctx.paper_id,
        STEP_ID,
        {"brief": brief, "source": source, "briefUserEdits": brief_user_edits},
        summary_lines,
    )
    return PaperSkillResult(
        name="paper_brief",
        summary=f"{source} brief",
        artifacts=artifacts,
        data={"paper_brief": brief},
    )


def run(ctx: PaperSkillContext) -> PaperSkillResult:
    return build_brief(ctx, force=False)
