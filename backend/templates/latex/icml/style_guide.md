# ICML Paper Style Guide

This guide supplements the LaTeX template. Use it as prompt context when preparing the paper brief, outline, and section drafts for ICML-style submissions.

## Reviewer Expectations

- Lead with a precise machine learning problem, why the current frontier is insufficient, and what technical insight makes the proposed approach plausible.
- Make the claimed contribution narrow enough to be testable. ICML papers are usually rewarded for conceptual clarity, methodological rigor, and evidence that isolates why the method works.
- Avoid broad "solves X" claims. Prefer explicit scopes, assumptions, datasets, model classes, or regimes where the method is expected to help.
- Explain relationships to prior work early. The introduction and related work should show the exact gap, not just list adjacent papers.
- Tie every major empirical claim to a table, figure, ablation, theorem, or cited result. Unsupported intuition should be marked as intuition.

## Common Content Structure

1. **Abstract:** State the problem, the technical idea, the main evidence, and the practical implication in one compact paragraph. Avoid marketing language.
2. **Introduction:** Move from motivation to gap to insight to contributions. End with 3-4 concrete contributions that map to later sections.
3. **Related Work / Background:** Organize by conceptual comparison, not chronology. Make clear which limitation of prior work motivates each design choice.
4. **Preliminaries or Problem Setup:** Define notation, objective functions, assumptions, datasets, or evaluation protocols before presenting the method.
5. **Method:** Present the central mechanism with enough detail for implementation. Include algorithms, equations, and complexity or stability discussion when relevant.
6. **Experiments:** Start with questions or hypotheses, then datasets, baselines, metrics, implementation details, and main results.
7. **Analysis / Ablations:** Isolate components, sensitivity, failure cases, compute cost, robustness, and limitations. ICML reviewers often look for evidence that the method's gains are not accidental.
8. **Conclusion:** Summarize the technical takeaway and realistic scope. Do not introduce new claims.

## Argument and Tone

- Prefer direct technical prose over rhetorical flourish. Sentences should make falsifiable claims.
- Distinguish "we propose", "we prove", "we observe", and "we hypothesize".
- Use equations to define objectives, mechanisms, or bounds rather than to decorate the text.
- Use figures to explain mechanisms or reveal empirical patterns; use tables for comparisons and ablations.
- Make limitations visible in the main paper when they affect interpretation, even if details are deferred to the appendix.

## Evidence Planning

- Include strong baselines and describe whether they are reproduced, taken from prior work, or reimplemented.
- Reserve space for ablations that test the core insight, not just hyperparameter variants.
- Report uncertainty or multiple seeds when the task is stochastic.
- Discuss computational budget and practical trade-offs when training or inference cost is material.
- If using generated or linked figures, assign exact paths and labels in the outline so section drafting can reference them consistently.

## Outline Guidance

- The outline should allocate substantial space to method and experiments; a thin method section is risky for ICML.
- The first half of the paper should answer "what is new and why should it work"; the second half should answer "does it work, when, and why".
- Include an Analysis, Ablation, or Limitations section unless the paper type clearly makes it unnecessary.
- Keep appendix-only material separate from the main narrative: extra proofs, full hyperparameter grids, extended qualitative examples, and additional dataset details can be deferred.
