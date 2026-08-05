from .base import SectionWriter


class BackgroundWriter(SectionWriter):
    kind = "background"
    prompt_template = """You are writing the Background or Preliminaries section of a {{paper_type}} paper titled "{{title}}" for {{venue_name}}.

Paper abstract: {{abstract}}
Section key points: {{key_points}}
Paper writing brief: {{paper_brief}}
Venue style guide: {{venue_style_guide}}
References available: {{refs_summary}}
Context from previous sections: {{prev_context}}

Write COMPLETE LaTeX content for this background/preliminaries section.
Mandatory requirements:
- Write at least {{min_words}} words.
- Define notation, assumptions, problem setup, and evaluation concepts needed before the method or experiments.
- Use equations only when they clarify definitions or objectives; every equation must be introduced and referenced in text.
- Cite foundational references using \cite{key}; use at least 3 citations when available.
{{eq_req}}
{{table_req}}
{{fig_req}}
- Avoid repeating the introduction or previewing unsupported results.
- Follow the venue style guide for technical precision and compactness.
"""
