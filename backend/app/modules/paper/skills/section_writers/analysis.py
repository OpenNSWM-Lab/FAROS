from .base import SectionWriter


class AnalysisWriter(SectionWriter):
    kind = "analysis"
    prompt_template = """You are writing an Analysis/Discussion/Ablation/Limitation section of a {{paper_type}} paper titled "{{title}}" for {{venue_name}}.

Paper abstract: {{abstract}}
Section key points: {{key_points}}
Experiment metrics: {{metrics_data}}
Run evidence: {{runs_data}}
Available paper figures: {{figures_data}}
Section-selected figures: {{section_figures_data}}
Paper writing brief: {{paper_brief}}
Venue style guide: {{venue_style_guide}}
References available: {{refs_summary}}
Context from previous sections: {{prev_context}}

Write COMPLETE LaTeX content for the analysis-oriented section.
Mandatory requirements:
- Start with \section{{{section_title}}}
- Write at least {{min_words}} words.
- Interpret evidence rather than repeating raw results; explain trends, trade-offs, error cases, sensitivity, or limitations.
- Tie each interpretation back to linked metrics, run evidence, selected figures, or cited literature.
- Include limitations honestly and avoid claims that exceed available evidence.
- Cite at least 3 references when comparing explanations or known failure modes.
{{eq_req}}
{{table_req}}
{{fig_req}}
- If Section-selected figures is not N/A, include every listed figure exactly once using its exact path, label, and caption.
- Follow the venue style guide for discussion depth and reviewer-facing caveats.

Return ONLY the LaTeX content, with no markdown fences or explanations.
"""
