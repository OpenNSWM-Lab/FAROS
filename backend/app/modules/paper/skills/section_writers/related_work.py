from .base import SectionWriter


class RelatedWorkWriter(SectionWriter):
    kind = "related_work"
    prompt_template = """You are writing the Related Work section of a {{paper_type}} paper titled "{{title}}" for {{venue_name}}.

Paper abstract: {{abstract}}
Section key points: {{key_points}}
Paper writing brief: {{paper_brief}}
Venue style guide: {{venue_style_guide}}
References available: {{refs_summary}}
Context from previous sections: {{prev_context}}

Write COMPLETE LaTeX content for the related work.
Mandatory requirements:
- Start with \section{{{section_title}}}
- Write at least {{min_words}} words.
- Organize prior work by technical theme, not as a flat paper-by-paper list.
- Use citations densely and accurately; cite at least 6 references when available.
- Compare the paper's approach against prior work using precise differences in assumptions, method, evidence, or scope.
- End by explaining the unresolved gap this paper addresses.
- Do not include experimental results, algorithm pseudocode, or unsupported performance claims unless the outline explicitly asks for them.
{{eq_req}}
{{table_req}}
{{fig_req}}
- If Section-selected figures is not N/A, include every listed figure exactly once using its exact path, label, and caption.
- Follow the venue style guide for positioning and related-work tone.

Return ONLY the LaTeX content, with no markdown fences or explanations.
"""
