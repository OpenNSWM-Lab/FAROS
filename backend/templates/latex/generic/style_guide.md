# Generic Research Paper Style Guide

This guide supplements the LaTeX template. Use it as prompt context when no specific venue is selected or when drafting an early, venue-neutral paper.

## Reviewer Expectations

- Make the research question, contribution, evidence, and scope understandable without assuming a venue-specific reviewing culture.
- Prefer conservative claims and clear traceability from claim to evidence.
- Explain enough background for an adjacent-field reader while keeping the main technical line compact.
- Use the generic template for early drafts, internal reports, workshop drafts, or papers whose final venue is undecided.
- Preserve material that can later be adapted to a venue-specific style: limitations, reproducibility details, ablations, related work, and impact notes.

## Common Content Structure

1. **Abstract:** State problem, approach, evidence, and takeaway in one paragraph.
2. **Introduction:** Explain motivation, gap, core idea, and contributions.
3. **Related Work / Background:** Provide enough context to understand the contribution and avoid overstating novelty.
4. **Method:** Describe the proposed approach, assumptions, equations, algorithms, or system design.
5. **Experiments / Evaluation:** Present questions, data, baselines, metrics, setup, results, and interpretation.
6. **Results / Analysis:** Separate main findings from diagnostic analyses, ablations, or qualitative examples.
7. **Discussion / Limitations:** Clarify scope, threats to validity, trade-offs, and future work.
8. **Conclusion:** Summarize the durable takeaway without new evidence.

## Argument and Tone

- Write for clarity before optimization for a specific venue.
- Use short topic sentences that state the point of each paragraph.
- Mark speculative claims as hypotheses and evidence-backed claims as findings.
- Use figures and tables only when they answer a reader question.
- Avoid venue-specific promises such as "state of the art" unless the evidence is already complete.

## Evidence Planning

- Include basic baselines and sanity checks before expensive comparisons.
- Record missing evidence explicitly so later drafting can close the gap.
- Keep method details reproducible: data, code, settings, seeds, compute, and evaluation scripts.
- Include at least one section that tests why the method works, not only whether it works.
- Track limitations and threats to validity early.

## Outline Guidance

- Use a balanced outline: motivation, method, evaluation, analysis, and limitations.
- Keep section titles conventional so the draft can later be transformed into ICML, NeurIPS, ICLR, ACL, or another venue.
- If evidence is sparse, make the draft an honest research report rather than a submission-style claim.
- Put open TODOs in notes or artifacts rather than final paper prose.
