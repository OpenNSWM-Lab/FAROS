from .base import SectionWriter


class MethodWriter(SectionWriter):
    kind = "method"
    prompt_template = """You are writing a Method/Approach/System section of a {{paper_type}} paper titled "{{title}}" for {{venue_name}}.

Paper abstract: {{abstract}}
Section key points: {{key_points}}
Paper contributions: {{contributions}}
Special requirements: {{requirements}}
Paper writing brief: {{paper_brief}}
Venue style guide: {{venue_style_guide}}
Available paper figures: {{figures_data}}
Section-selected figures: {{section_figures_data}}
References available: {{refs_summary}}
Context from previous sections: {{prev_context}}

Write COMPLETE LaTeX content for the method-oriented section.
Mandatory requirements:
- Start with \section{{{section_title}}}
- Write at least {{min_words}} words.
- Explain the proposed mechanism, architecture, algorithm, or system design in enough detail to be reproducible.
- Separate assumptions, inputs/outputs, design choices, and complexity or failure modes where relevant.
- Do not report empirical performance unless the section key points explicitly ask for it.
- Cite at least 3 references for technical background or design lineage.
{{algo_req}}
{{eq_req}}
{{table_req}}
{{fig_req}}
- If Section-selected figures is not N/A, include every listed figure exactly once using its exact path, label, and caption.
- Every algorithm, equation, table, and figure must be referenced in the surrounding prose.
- Follow the venue style guide for method clarity and reviewer expectations.

Return ONLY the LaTeX content, with no markdown fences or explanations.
"""
