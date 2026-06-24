from .base import SectionWriter


class IntroductionWriter(SectionWriter):
    kind = "introduction"
    prompt_template = """You are writing the Introduction section of a {{paper_type}} paper titled "{{title}}" for {{venue_name}}.

Paper abstract: {{abstract}}
Section key points: {{key_points}}
Paper contributions: {{contributions}}
Paper writing brief: {{paper_brief}}
Venue style guide: {{venue_style_guide}}
Available paper figures: {{figures_data}}
Section-selected figures: {{section_figures_data}}
References available: {{refs_summary}}

Write COMPLETE LaTeX content for the introduction.
Mandatory requirements:
- Start with \section{{{section_title}}}
- Write at least {{min_words}} words.
- Open with the research problem and why it matters; do not start with generic field history.
- Establish the concrete gap that this paper addresses, then state the paper's core claim.
- Summarize 3 or more contributions in prose or a compact itemized list.
- Briefly preview the evidence used later without over-claiming results.
- Cite at least 3 relevant references using \cite{key}.
{{fig_req}}
- If Section-selected figures is not N/A, include every listed figure exactly once using its exact path, label, and caption.
- Do not introduce unsupported datasets, baselines, or state-of-the-art claims.
- Follow the venue style guide for contribution framing and reviewer expectations.

Return ONLY the LaTeX content, with no markdown fences or explanations.
"""
