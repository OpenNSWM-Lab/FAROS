from .base import SectionWriter


class ConclusionWriter(SectionWriter):
    kind = "conclusion"
    prompt_template = """You are writing the Conclusion section of a {{paper_type}} paper titled "{{title}}" for {{venue_name}}.

Paper abstract: {{abstract}}
Section key points: {{key_points}}
Paper contributions: {{contributions}}
Paper writing brief: {{paper_brief}}
Venue style guide: {{venue_style_guide}}
References available: {{refs_summary}}
Context from previous sections: {{prev_context}}

Write COMPLETE LaTeX content for the conclusion.
Mandatory requirements:
- Start with \section{{{section_title}}}
- Write at least {{min_words}} words, but keep the section concise if the outline asks for a short conclusion.
- Restate the paper's problem, approach, and evidence-backed conclusion without introducing new experiments.
- Summarize the main contribution and practical or scientific implication.
- Mention limitations or future work when appropriate.
- Use citations sparingly; cite only references that support concluding context.
{{fig_req}}
- If Section-selected figures is not N/A, include every listed figure exactly once using its exact path, label, and caption.
- Do not claim state-of-the-art performance unless explicitly supported by the linked evidence.

Return ONLY the LaTeX content, with no markdown fences or explanations.
"""
