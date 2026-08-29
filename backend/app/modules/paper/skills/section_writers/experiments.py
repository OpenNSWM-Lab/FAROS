from .base import SectionWriter


class ExperimentsWriter(SectionWriter):
    kind = "experiments"
    prompt_template = """You are writing an Experiments/Evaluation/Results section of a {{paper_type}} paper titled "{{title}}" for {{venue_name}}.

Paper abstract: {{abstract}}
Section key points: {{key_points}}
Special requirements: {{requirements}}
Experiment metrics: {{metrics_data}}
Run evidence: {{runs_data}}
Code table artifacts: {{code_tables_data}}
Available paper figures: {{figures_data}}
Section-selected figures: {{section_figures_data}}
Paper writing brief: {{paper_brief}}
Venue style guide: {{venue_style_guide}}
References available: {{refs_summary}}
Context from previous sections: {{prev_context}}

Write COMPLETE LaTeX content for the evaluation-oriented section.
Mandatory requirements:
- Write at least {{min_words}} words.
- Cover evaluation setup, metrics, baselines or comparisons, implementation/runtime evidence, and result interpretation when supported by the linked context.
- Use linked metrics and run evidence as the source of quantitative claims. If a number is not present in the context, do not invent it.
- When Code table artifacts are available, use their label, file name, location, and analysis as evidence pointers. Do not copy raw table rows, chart bodies, or artifact preview content into the manuscript.
- Use code result analysis from node results when explaining the meaning of metrics, ablations, robustness tests, or failures.
- Discuss selected figures concretely and connect each figure to a claim in the text.
- Cite at least 3 relevant references for baselines, datasets, metrics, or evaluation protocol when available.
{{eq_req}}
{{table_req}}
{{fig_req}}
- Tables must be grounded in linked metrics where available; if metrics are insufficient, use qualitative or structural tables instead of fabricated scores.
- Follow the venue style guide for evidence density, ablation discussion, and limitations.
"""
