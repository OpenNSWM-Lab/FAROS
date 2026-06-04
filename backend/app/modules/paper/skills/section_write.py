import json
from typing import Dict

from app.llm.provider_client import ChatMessage
from app.modules.paper.storage import write_paper_file
from .base import PaperSkillContext, PaperSkillResult
from .utils import write_artifact


STEP_ID = "05_section_write"

SECTION_PROMPT = """You are writing the "{section_title}" section of a {paper_type} paper titled "{title}" for {venue_name}.

**Abstract:** {abstract}
**Section key points:** {key_points}
**Contributions:** {contributions}
**Special requirements:** {requirements}
**Metrics data (if relevant):** {metrics_data}
**Run evidence:** {runs_data}
**Available paper figures:** {figures_data}
**Paper writing brief:** {paper_brief}
**Context from previous sections:** {prev_context}
**References available:** {refs_summary}

Write COMPLETE LaTeX content for this section. MANDATORY requirements:
- Start with \\section{{{section_title}}}
- Write at least {min_words} words of substantive, technical content
- Use proper LaTeX formatting throughout
- Cite references using \\cite{{key}} — you MUST cite at least 3 references in this section
{algo_req}
{eq_req}
{table_req}
{fig_req}
- Professional academic tone appropriate for {venue_name}
- Do NOT use placeholder text like "Lorem ipsum" or "TODO"
- Every claim must be supported by citation or evidence
- Follow the Paper writing brief, especially core claim, must-use evidence, must-use figures, and avoid-claims.

Return ONLY the LaTeX content (no markdown fences, no explanations).
"""

ALGORITHM_TEMPLATE = """- MUST include algorithm block(s) using:
\\begin{algorithm}[H]
\\SetAlgoLined
\\caption{Algorithm Name}
\\label{alg:name}
\\KwIn{Input description}
\\KwOut{Output description}
Step 1\\;
Step 2\\;
\\end{algorithm}
Include detailed pseudocode with proper notation."""

EQUATION_TEMPLATE = """- MUST include at least {n} numbered equations using \\begin{{equation}} ... \\end{{equation}}
  Each equation must be meaningful and referenced in text."""

TABLE_TEMPLATE = """- MUST include at least {n} tables using:
\\begin{{table}}[t]
\\caption{{Table caption}}
\\label{{tab:name}}
\\centering
\\begin{{tabular}}{{...}}
\\toprule ... \\midrule ... \\bottomrule
\\end{{tabular}}
\\end{{table}}
Tables must contain plausible numerical results."""

FIGURE_TEMPLATE = """- MUST reference figures using:
\\begin{figure}[t]
\\centering
\\includegraphics[width=\\linewidth]{figures/fig_name.pdf}
\\caption{Figure caption}
\\label{fig:name}
\\end{figure}
Reference each figure in the text. If Available paper figures lists concrete paths, labels, or captions, use those exact values instead of inventing placeholder filenames."""


def run(ctx: PaperSkillContext) -> PaperSkillResult:
    outline = ctx.get("outline", {})
    sections = outline.get("sections", [])
    refs = outline.get("references", [])
    contributions = outline.get("contributions", [])
    context = ctx.get("context", {})
    paper_brief = ctx.get("paper_brief", {})

    refs_summary = ", ".join(
        f"{r.get('key', 'ref')}: {r.get('title', '')[:40]}" for r in refs[:15]
    )

    sections_content: Dict[str, str] = {}
    prev_context = ""
    figures_summary = context.get("figures_summary", "N/A")

    for i, section in enumerate(sections):
        sec_title = section.get("title", f"Section {i+1}")

        algo_req = ALGORITHM_TEMPLATE if section.get("hasAlgorithm") else ""
        n_eq = section.get("numEquations", 2 if section.get("hasEquations") else 0)
        eq_req = EQUATION_TEMPLATE.format(n=max(n_eq, 2)) if section.get("hasEquations") else ""
        n_tab = 2 if section.get("hasTables") else 0
        table_req = TABLE_TEMPLATE.format(n=n_tab) if n_tab > 0 else ""
        fig_descs = section.get("figureDescriptions", [])
        section_lower = sec_title.lower()
        figures_needed = (
            section.get("hasFigures")
            or fig_descs
            or (
                figures_summary != "N/A"
                and any(kw in section_lower for kw in ["experiment", "result", "analysis", "method", "ablation"])
            )
        )
        fig_req = FIGURE_TEMPLATE if figures_needed else ""

        prompt = SECTION_PROMPT.format(
            section_title=sec_title,
            paper_type=ctx.paper_type,
            title=outline.get("title", ctx.paper.get("title", "Untitled")),
            venue_name=ctx.venue_cfg["name"],
            abstract=outline.get("abstract", "")[:500],
            key_points=json.dumps(section.get("keyPoints", [])),
            contributions=json.dumps(contributions),
            requirements="; ".join([r for r in [
                f"Min {section.get('minWords', 500)} words",
                "Include algorithm" if section.get("hasAlgorithm") else "",
                f"{n_eq} equations" if n_eq else "",
                f"{n_tab} tables" if n_tab else "",
                "Include figures" if fig_req else "",
            ] if r]),
            metrics_data=context.get("metrics_summary", "N/A")[:1000],
            runs_data=context.get("runs_summary", "N/A")[:1500],
            figures_data=figures_summary[:1500],
            paper_brief=json.dumps(paper_brief, ensure_ascii=False)[:1800] if paper_brief else "N/A",
            prev_context=prev_context[:600],
            refs_summary=refs_summary,
            min_words=section.get("minWords", 500),
            algo_req=algo_req,
            eq_req=eq_req,
            table_req=table_req,
            fig_req=fig_req,
        )

        resp = ctx.client.chat(
            messages=[ChatMessage(role="user", content=prompt)],
            model=ctx.model, temperature=0.4, max_tokens=6000, timeout=ctx.llm_timeout(),
        )
        content = resp.text.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])

        section_id = section.get("id", f"section_{i+1}")
        write_paper_file(ctx.paper_id, f"sections/{section_id}.tex", content)
        sections_content[section_id] = content
        prev_context = content[:400]

    summary_lines = [
        "# Section Write",
        f"sections: {len(sections_content)}",
    ]
    artifacts = write_artifact(
        ctx.paper_id,
        STEP_ID,
        {"section_ids": list(sections_content.keys())},
        summary_lines,
    )

    return PaperSkillResult(
        name="section_write",
        summary=f"{len(sections_content)} sections generated",
        artifacts=artifacts,
        data={
            "sections": sections,
            "sections_content": sections_content,
        },
    )
