# NeurIPS Paper Style Guide

This guide supplements the LaTeX template. Use it as prompt context when preparing the paper brief, outline, and section drafts for NeurIPS-style submissions.

## Reviewer Expectations

- Lead with the broad scientific or engineering problem, then quickly narrow to the exact technical contribution.
- NeurIPS papers are often judged on significance, originality, technical soundness, empirical rigor, reproducibility, and clarity. Make those dimensions visible in the outline.
- Position the work against both immediate baselines and the larger research program. Explain why the result matters beyond one benchmark.
- Treat reproducibility as part of the main contribution: implementation details, compute budget, hyperparameters, seeds, and dataset splits should be easy to find.
- Be explicit about limitations, societal impact, and possible misuse when the method affects people, large-scale deployment, or generative systems.

## Common Content Structure

1. **Abstract:** State the problem, contribution, evidence, and broader implication. Keep it concrete and avoid overclaiming.
2. **Introduction:** Motivate at field level, identify the gap, summarize the technical idea, and list contributions that map to theory, method, and experiments.
3. **Background / Related Work:** Connect the paper to multiple subcommunities when relevant. Use thematic grouping instead of a flat bibliography.
4. **Method / Theory:** Provide the core mechanism, assumptions, algorithm, theorem, architecture, or objective. Highlight what is novel and what is inherited.
5. **Experiments:** State research questions first, then datasets, baselines, metrics, implementation details, and main results.
6. **Ablations / Analysis:** Explain why the method works, when it fails, and what variables control performance.
7. **Limitations / Broader Impact:** Address scope, safety, fairness, compute, environmental cost, or deployment risks when relevant.
8. **Conclusion:** Emphasize the scientific takeaway and future directions without adding new claims.

## Argument and Tone

- Use confident but bounded claims. NeurIPS style rewards ambition, but reviewers punish unsupported universality.
- Make theoretical assumptions and empirical protocols explicit before interpreting results.
- Use figures to reveal mechanisms, trade-offs, or scaling trends; use tables for benchmark comparisons and ablations.
- Prefer "we show", "we prove", "we empirically find", and "we hypothesize" over vague verbs such as "demonstrate" when evidence differs by strength.
- Do not hide important negative results. Frame them as scope conditions or diagnostic insight.

## Evidence Planning

- Include enough baselines to cover simple, strong, and recent alternatives.
- Include ablations for the central design choices, not only peripheral hyperparameters.
- Report uncertainty across seeds or runs for stochastic experiments.
- Include compute, model size, data size, and training protocol when they affect interpretation.
- For theoretical papers, state assumptions, proof strategy, and whether constants or regimes are practically meaningful.

## Outline Guidance

- Allocate space for reproducibility and analysis; a benchmark-only paper is fragile unless the benchmark itself is the contribution.
- If the paper is method-heavy, include a dedicated ablation or diagnostic section.
- If the paper is theory-heavy, pair formal claims with a small empirical sanity check when possible.
- Plan a Limitations or Broader Impact section for work with deployment, safety, fairness, privacy, or resource implications.
