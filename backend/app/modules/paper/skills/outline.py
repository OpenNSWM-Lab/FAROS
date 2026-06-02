import json

from app.llm.provider_client import ChatMessage
from app.modules.paper.storage import update_paper
from .base import PaperSkillContext, PaperSkillResult
from .constants import MIN_ALGORITHMS, MIN_EQUATIONS, MIN_FIGURES, MIN_REFERENCES, MIN_TABLES
from .utils import _extract_json, write_artifact


STEP_ID = "02_outline"

OUTLINE_PROMPT = """You are a senior ML researcher writing a {paper_type} paper for {venue_name}.

**Title:** {title}
**Context from plan/project:** {plan_context}
**Experiment metrics:** {metrics_summary}
**Run execution results:** {runs_summary}
**Available paper figures:** {figures_summary}
**User notes:** {user_notes}

Generate a DETAILED paper outline. You MUST include:
- At least 7 sections (Introduction, Related Work, Background/Preliminaries, Method, Experiments, Analysis/Discussion, Conclusion)
- At least {min_refs} references — use REAL, well-known papers in the field. DO NOT invent DOIs. Use format: authors, title, venue, year. If uncertain about a reference, include it but add "note": "to verify".
- Mark which sections need: algorithms (at least {min_algos}), equations (at least {min_eqs}), tables (at least {min_tables}), figures (at least {min_figs})
- If Available paper figures are listed, assign them to the most relevant sections using their exact path, label, and caption. Do not invent alternate filenames.

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


def run(ctx: PaperSkillContext) -> PaperSkillResult:
    context = ctx.get("context", {})
    outline_prompt = OUTLINE_PROMPT.format(
        paper_type=ctx.paper_type,
        venue_name=ctx.venue_cfg["name"],
        title=ctx.paper.get("title", "Untitled"),
        plan_context=context.get("plan_context", "N/A")[:1500],
        metrics_summary=context.get("metrics_summary", "N/A")[:1500],
        runs_summary=context.get("runs_summary", "N/A")[:1500],
        figures_summary=context.get("figures_summary", "N/A")[:1500],
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
    outline = _extract_json(resp.text)
    if not outline or "sections" not in outline:
        raise ValueError(f"LLM returned invalid outline: {resp.text[:500]}")

    update_paper(ctx.paper_id, {"outlineJson": outline})
    summary_lines = [
        "# Outline",
        f"sections: {len(outline.get('sections', []))}",
        f"references: {len(outline.get('references', []))}",
        f"contributions: {len(outline.get('contributions', []))}",
    ]
    artifacts = write_artifact(ctx.paper_id, STEP_ID, outline, summary_lines)
    return PaperSkillResult(
        name="outline",
        summary="outline generated",
        artifacts=artifacts,
        data={"outline": outline},
    )
