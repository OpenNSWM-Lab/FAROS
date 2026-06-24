import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List

from app.llm.provider_client import ChatMessage
from app.modules.paper.skills.base import PaperSkillContext
from app.modules.paper.skills.utils import load_venue_style_guide


ALGORITHM2E_TEMPLATE = """- MUST include algorithm block(s) using:
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

ICML_ALGORITHM_TEMPLATE = """- MUST include algorithm block(s) using the ICML template's algorithm/algorithmic packages:
\\begin{algorithm}[tb]
\\caption{Algorithm Name}
\\label{alg:name}
\\begin{algorithmic}
\\STATE \\textbf{Input:} Input description
\\STATE \\textbf{Output:} Output description
\\FOR{$t = 1$ to $T$}
\\STATE Step description
\\ENDFOR
\\STATE \\textbf{return} Output
\\end{algorithmic}
\\end{algorithm}
Do not use algorithm2e commands such as \\SetAlgoLined, \\KwIn, \\KwOut, \\KwTo, or \\Return in ICML papers."""

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
Tables must be grounded in linked metrics where available; do not invent unsupported benchmark numbers."""

FIGURE_TEMPLATE = """- MUST reference figures using:
\\begin{figure}[t]
\\centering
\\includegraphics[width=\\linewidth]{figures/fig_name.pdf}
\\caption{Figure caption}
\\label{fig:name}
\\end{figure}
Reference each figure in the text. If figures list concrete paths, labels, or captions, use those exact values instead of inventing filenames."""


@dataclass
class SectionDraftRequest:
    ctx: PaperSkillContext
    outline: Dict[str, Any]
    section: Dict[str, Any]
    section_id: str
    section_title: str
    section_index: int
    total_sections: int
    section_kind: str
    context: Dict[str, str]
    paper_brief: Dict[str, Any]
    contributions: List[Any]
    refs_summary: str
    prev_context: str
    figures_data: str
    section_figures_data: str
    requirements_text: str
    min_words: int
    algo_req: str
    eq_req: str
    table_req: str
    fig_req: str

    def prompt_values(self) -> Dict[str, str]:
        return {
            "section_title": self.section_title,
            "section_id": self.section_id,
            "section_kind": self.section_kind,
            "section_index": str(self.section_index + 1),
            "total_sections": str(self.total_sections),
            "paper_type": self.ctx.paper_type,
            "title": str(self.outline.get("title") or self.ctx.paper.get("title") or "Untitled"),
            "venue_name": self.ctx.venue_cfg["name"],
            "abstract": str(self.outline.get("abstract", ""))[:700],
            "key_points": json.dumps(self.section.get("keyPoints", []), ensure_ascii=False),
            "contributions": json.dumps(self.contributions, ensure_ascii=False),
            "requirements": self.requirements_text,
            "venue_style_guide": load_venue_style_guide(self.ctx.venue)[:2500],
            "metrics_data": self.context.get("metrics_summary", "N/A")[:1200],
            "runs_data": self.context.get("runs_summary", "N/A")[:1500],
            "figures_data": self.figures_data[:1500],
            "section_figures_data": self.section_figures_data[:1500],
            "paper_brief": json.dumps(self.paper_brief, ensure_ascii=False)[:1800] if self.paper_brief else "N/A",
            "prev_context": self.prev_context[:700],
            "refs_summary": self.refs_summary,
            "min_words": str(self.min_words),
            "algo_req": self.algo_req,
            "eq_req": self.eq_req,
            "table_req": self.table_req,
            "fig_req": self.fig_req,
        }


def render_prompt(template: str, values: Dict[str, str]) -> str:
    prompt = template
    for key, value in values.items():
        prompt = prompt.replace("{{" + key + "}}", value)
    return prompt


def strip_markdown_fences(content: str) -> str:
    content = (content or "").strip()
    if not content.startswith("```"):
        return content
    lines = content.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def ensure_section_heading(content: str, section_title: str) -> str:
    content = strip_markdown_fences(content)
    if re.search(r"\\section\*?\{", content):
        return re.sub(
            r"\\section\*?\{[^}]*\}",
            f"\\section{{{section_title}}}",
            content,
            count=1,
        )
    return f"\\section{{{section_title}}}\n\n{content}"


class SectionWriter:
    kind = "generic"
    prompt_template = ""
    temperature = 0.4
    max_tokens = 6000

    def build_prompt(self, request: SectionDraftRequest) -> str:
        return render_prompt(self.prompt_template, request.prompt_values())

    def write(self, request: SectionDraftRequest) -> str:
        prompt = self.build_prompt(request)
        resp = request.ctx.client.chat(
            messages=[ChatMessage(role="user", content=prompt)],
            model=request.ctx.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            timeout=request.ctx.llm_timeout(),
        )
        return ensure_section_heading(resp.text, request.section_title)


def build_artifact_requirements(
    ctx: PaperSkillContext,
    section: Dict[str, Any],
    section_title: str,
    figures_summary: str,
    figures_for_prompt: List[Dict[str, Any]],
    section_figures: List[Dict[str, Any]],
) -> Dict[str, Any]:
    algorithm_template = ICML_ALGORITHM_TEMPLATE if ctx.venue == "icml" else ALGORITHM2E_TEMPLATE
    algo_req = algorithm_template if section.get("hasAlgorithm") else ""
    n_eq = section.get("numEquations", 2 if section.get("hasEquations") else 0)
    eq_req = EQUATION_TEMPLATE.format(n=max(n_eq, 2)) if section.get("hasEquations") else ""
    n_tab = 2 if section.get("hasTables") else 0
    table_req = TABLE_TEMPLATE.format(n=n_tab) if n_tab > 0 else ""
    fig_descs = section.get("figureDescriptions", [])
    section_lower = section_title.lower()
    figures_needed = (
        bool(section_figures)
        or section.get("hasFigures")
        or fig_descs
        or (
            figures_summary != "N/A"
            and figures_for_prompt
            and any(kw in section_lower for kw in ["experiment", "result", "analysis", "method", "ablation"])
        )
    )
    fig_req = FIGURE_TEMPLATE if figures_needed else ""

    requirements = [
        f"Min {section.get('minWords', 500)} words",
        "Include algorithm" if section.get("hasAlgorithm") else "",
        f"{n_eq} equations" if n_eq else "",
        f"{n_tab} tables" if n_tab else "",
        "Include figures" if fig_req else "",
    ]
    return {
        "requirements_text": "; ".join([item for item in requirements if item]),
        "min_words": int(section.get("minWords", 500) or 500),
        "algo_req": algo_req,
        "eq_req": eq_req,
        "table_req": table_req,
        "fig_req": fig_req,
    }
