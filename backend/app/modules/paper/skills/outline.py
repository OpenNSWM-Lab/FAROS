import json
import re
from typing import Any, Dict, List

from app.llm.provider_client import ChatMessage
from app.modules.paper.storage import update_paper
from .base import PaperSkillContext, PaperSkillResult
from .constants import MIN_ALGORITHMS, MIN_EQUATIONS, MIN_FIGURES, MIN_REFERENCES, MIN_TABLES
from .utils import _extract_json, load_venue_style_guide, write_artifact


STEP_ID = "03_outline"

OUTLINE_PROMPT = """You are a senior ML researcher writing a {paper_type} paper for {venue_name}.

**Title:** {title}
**Venue style guide:** {venue_style_guide}
**Context from plan/project:** {plan_context}
**Experiment metrics:** {metrics_summary}
**Run execution results:** {runs_summary}
**Available paper figures:** {figures_summary}
**Paper writing brief:** {paper_brief}
**User notes:** {user_notes}

Generate a DETAILED paper outline. You MUST include:
- At least 7 sections (Introduction, Related Work, Background/Preliminaries, Method, Experiments, Analysis/Discussion, Conclusion)
- If the Venue style guide defines mandatory standardized fields or sections, use those fields as the required section list even when they differ from the default academic-paper sections above.
- At least {min_refs} references — use REAL, well-known papers in the field. DO NOT invent DOIs. Use format: authors, title, venue, year. If uncertain about a reference, include it but add "note": "to verify".
- Mark which sections need: algorithms (at least {min_algos}), equations (at least {min_eqs}), tables (at least {min_tables}), figures (at least {min_figs})
- If Available paper figures are listed, assign them to the most relevant sections using their exact path, label, and caption. Do not invent alternate filenames.
- Follow the Paper writing brief. Preserve its research question, core claim, must-use evidence, and avoid-claims constraints.
- Follow the Venue style guide when choosing section order, contribution framing, evaluation emphasis, limitations, and appendix-worthy material.

Return strict JSON:
{{
  "title": "...",
  "authors": ["Author One", "Author Two"],
  "abstract": "200-300 word abstract covering motivation, method, results, and significance",
  "sections": [
    {{
      "id": "intro",
      "title": "Introduction",
      "keyPoints": ["Motivation and problem statement", "Key contributions (3+)", "Paper organization"],
      "minWords": 600,
      "hasAlgorithm": false,
      "hasEquations": true,
      "numEquations": 1,
      "hasTables": false,
      "hasFigures": true,
      "figureDescriptions": ["Overview figure showing the proposed framework"]
    }}
  ],
  "references": [
    {{"key": "vaswani2017attention", "authors": "Vaswani, A. et al.", "title": "Attention is All You Need", "venue": "NeurIPS", "year": 2017}}
  ],
  "algorithms": [
    {{"id": "alg1", "name": "Main Algorithm Name", "inSection": "method"}},
    {{"id": "alg2", "name": "Training Procedure", "inSection": "method"}}
  ],
  "contributions": ["Contribution 1", "Contribution 2", "Contribution 3"]
}}
Return ONLY valid JSON, no markdown fences.
"""

def _clean_section_id(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", value or "").strip("_").lower()
    return cleaned or fallback


def _normalize_string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _normalize_sections(sections: Any) -> List[Dict[str, Any]]:
    if not isinstance(sections, list):
        return []

    normalized: List[Dict[str, Any]] = []
    for idx, raw_section in enumerate(sections, start=1):
        if not isinstance(raw_section, dict):
            continue
        title = str(raw_section.get("title") or f"Section {idx}").strip()
        section_id = str(raw_section.get("id") or "").strip()
        section_id = _clean_section_id(section_id or title, f"section_{idx}")
        min_words = raw_section.get("minWords", 500)
        num_equations = raw_section.get("numEquations", 0)
        try:
            min_words = int(min_words)
        except (TypeError, ValueError):
            min_words = 500
        try:
            num_equations = int(num_equations)
        except (TypeError, ValueError):
            num_equations = 0

        normalized.append({
            **raw_section,
            "id": section_id,
            "title": title,
            "keyPoints": _normalize_string_list(raw_section.get("keyPoints")),
            "minWords": max(150, min_words),
            "hasAlgorithm": bool(raw_section.get("hasAlgorithm", False)),
            "hasEquations": bool(raw_section.get("hasEquations", False)),
            "numEquations": max(0, num_equations),
            "hasTables": bool(raw_section.get("hasTables", False)),
            "hasFigures": bool(raw_section.get("hasFigures", False)),
            "figureDescriptions": _normalize_string_list(raw_section.get("figureDescriptions")),
        })
    return normalized


def _normalize_outline(outline: Dict[str, Any], ctx: PaperSkillContext) -> Dict[str, Any]:
    normalized = dict(outline)
    normalized["title"] = str(normalized.get("title") or ctx.paper.get("title") or "Untitled Paper").strip()
    normalized["authors"] = _normalize_string_list(normalized.get("authors")) or ["Auto-LLM Draft"]
    normalized["abstract"] = str(normalized.get("abstract") or "").strip()
    normalized["sections"] = _normalize_sections(normalized.get("sections"))
    normalized["references"] = normalized.get("references") if isinstance(normalized.get("references"), list) else []
    normalized["algorithms"] = normalized.get("algorithms") if isinstance(normalized.get("algorithms"), list) else []
    normalized["contributions"] = _normalize_string_list(normalized.get("contributions"))
    return normalized


def build_outline(ctx: PaperSkillContext, force: bool = False) -> PaperSkillResult:
    context = ctx.get("context", {})
    paper_brief = ctx.get("paper_brief", {})
    existing = ctx.paper.get("outlineJson")
    source = "existing"

    if existing and not force:
        source = ctx.paper.get("outlineStatus") or "existing"
        outline = _normalize_outline(existing, ctx)
    else:
        source = "generated"
        outline_prompt = OUTLINE_PROMPT.format(
            paper_type=ctx.paper_type,
            venue_name=ctx.venue_cfg["name"],
            title=ctx.paper.get("title", "Untitled"),
            venue_style_guide=load_venue_style_guide(ctx.venue)[:3000],
            plan_context=context.get("plan_context", "N/A")[:1500],
            metrics_summary=context.get("metrics_summary", "N/A")[:1500],
            runs_summary=context.get("runs_summary", "N/A")[:1500],
            figures_summary=context.get("figures_summary", "N/A")[:1500],
            paper_brief=json.dumps(paper_brief, ensure_ascii=False)[:2000] if paper_brief else "N/A",
            user_notes=context.get("user_notes", "N/A"),
            min_refs=MIN_REFERENCES,
            min_algos=MIN_ALGORITHMS,
            min_eqs=MIN_EQUATIONS,
            min_tables=MIN_TABLES,
            min_figs=MIN_FIGURES,
        )

        resp = ctx.client.chat(
            messages=[ChatMessage(role="user", content=outline_prompt)],
            model=ctx.model, temperature=0.4, max_tokens=8000, timeout=ctx.llm_timeout(),
        )
        parsed = _extract_json(resp.text)
        if not parsed or "sections" not in parsed:
            raise ValueError(f"LLM returned invalid outline: {resp.text[:500]}")
        outline = _normalize_outline(parsed, ctx)
        update_paper(ctx.paper_id, {"outlineJson": outline, "outlineStatus": source})

    summary_lines = [
        "# Outline",
        f"source: {source}",
        f"sections: {len(outline.get('sections', []))}",
        f"references: {len(outline.get('references', []))}",
        f"contributions: {len(outline.get('contributions', []))}",
    ]
    artifacts = write_artifact(ctx.paper_id, STEP_ID, outline, summary_lines)
    return PaperSkillResult(
        name="outline",
        summary=f"{source} outline",
        artifacts=artifacts,
        data={"outline": outline},
    )


def run(ctx: PaperSkillContext) -> PaperSkillResult:
    return build_outline(ctx, force=False)
