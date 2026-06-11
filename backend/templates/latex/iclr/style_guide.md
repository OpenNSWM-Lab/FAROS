# ICLR Paper Style Guide

This guide supplements the LaTeX template. Use it as prompt context when preparing the paper brief, outline, and section drafts for ICLR-style submissions.

## Reviewer Expectations

- ICLR reviewers often value clear insight, reproducibility, and honest empirical analysis as much as raw benchmark gains.
- State the representation learning, optimization, generative modeling, or learning-theoretic question precisely before introducing machinery.
- Make the paper self-contained enough for an interdisciplinary ML audience. Define notation, assumptions, and experimental protocols early.
- Explain why the idea should work. A convincing ICLR paper usually has a conceptual story backed by ablations, visualizations, or theory.
- Discuss limitations, failure cases, and sensitivity rather than treating them as appendix-only details.

## Common Content Structure

1. **Abstract:** Present the problem, key insight, method, strongest evidence, and scope.
2. **Introduction:** Start with the learning problem and pain point, then introduce the hypothesis or design principle behind the method.
3. **Related Work:** Separate close competitors from broader context. Make comparisons analytical rather than merely historical.
4. **Preliminaries:** Define notation, model class, objective, or evaluation setting before the method.
5. **Method:** Explain the mechanism in a way that supports implementation. Include algorithm blocks, equations, or architecture diagrams where useful.
6. **Experiments:** Use questions or claims as organizing headers. Report baselines, datasets, metrics, implementation details, and main results.
7. **Analysis / Ablation:** Probe components, sensitivity, scaling, robustness, and qualitative behavior.
8. **Conclusion and Limitations:** Summarize what was learned and where the method should not be expected to work.

## Argument and Tone

- Prefer explanatory prose: "the method works because..." should be visible in the narrative.
- Keep claims calibrated to evidence. Avoid saying "general" when experiments cover only one family of tasks.
- Use mathematical notation to clarify the learning problem, not to obscure simple ideas.
- Use plots and qualitative examples to expose internal behavior, not just final scores.
- Be transparent about hyperparameter sensitivity and implementation choices.

## Evidence Planning

- Include strong recent baselines and simple baselines; ICLR reviewers often check whether gains survive reasonable tuning.
- Include ablations that isolate the core mechanism and remove plausible confounders.
- Include robustness, out-of-distribution, scaling, or sensitivity analysis when the method claims generality.
- Report seed variance, compute budget, and training details for neural experiments.
- If the paper proposes a representation or generative model, include qualitative diagnostics and failure examples when possible.

## Outline Guidance

- The outline should make the central intuition visible before experimental detail.
- Add Preliminaries when notation or assumptions are necessary for readability.
- Include Analysis or Ablations as a first-class section, not a short paragraph after results.
- Keep the conclusion modest and use limitations to strengthen, not weaken, reviewer trust.
