from .base import SectionWriter


class GenericSectionWriter(SectionWriter):
    kind = "generic"
    prompt_template = """You are writing the "{{section_title}}" section of a {{paper_type}} paper titled "{{title}}" for {{venue_name}}.

Paper abstract: {{abstract}}
Section key points: {{key_points}}
Paper contributions: {{contributions}}
Special requirements: {{requirements}}
Paper writing brief: {{paper_brief}}
Venue style guide: {{venue_style_guide}}
Metrics data: {{metrics_data}}
Run evidence: {{runs_data}}
Available paper figures: {{figures_data}}
Section-selected figures: {{section_figures_data}}
References available: {{refs_summary}}
Context from previous sections: {{prev_context}}

Write COMPLETE LaTeX content for this section.
Mandatory requirements:
- Start with \section{{{section_title}}}
- Write at least {{min_words}} words of substantive academic content.
- Follow the section key points and paper writing brief exactly.
- Cite references using \cite{key}; use at least 3 citations when available.
{{algo_req}}
{{eq_req}}
{{table_req}}
{{fig_req}}
- If Section-selected figures is not N/A, include every listed figure exactly once using its exact path, label, and caption.
- Use proper LaTeX formatting and reference every algorithm, equation, table, and figure in prose.
- Do not use placeholder text or invent unsupported evidence.
- Follow the venue style guide for argument structure and tone.

Return ONLY the LaTeX content, with no markdown fences or explanations.
"""
