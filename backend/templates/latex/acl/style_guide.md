# ACL Paper Style Guide

This guide supplements the LaTeX template. Use it as prompt context when preparing the paper brief, outline, and section drafts for ACL-style NLP submissions.

## Reviewer Expectations

- Start from a concrete language, task, dataset, evaluation, or modeling problem. NLP reviewers expect the linguistic or user-facing motivation to be clear.
- Distinguish task contribution, method contribution, dataset contribution, analysis contribution, and resource contribution.
- Be careful with evaluation validity. Explain metrics, annotation quality, data splits, leakage risks, and statistical significance when relevant.
- Analyze errors and qualitative examples. ACL papers often need to show what the model gets wrong, not only aggregate scores.
- Include ethics, limitations, data statements, or responsible NLP discussion when the work involves human subjects, generated text, sensitive attributes, web data, or deployed language technologies.

## Common Content Structure

1. **Abstract:** Name the task or NLP problem, the proposed method or resource, the main empirical finding, and the practical implication.
2. **Introduction:** Motivate the language problem, state the gap in prior NLP work, and list contributions.
3. **Related Work:** Group by task, method family, resource/dataset, and evaluation paradigm. Clarify exactly which gap remains.
4. **Task Definition / Data:** Define input-output format, annotation scheme, dataset construction, language coverage, and evaluation protocol.
5. **Methodology:** Describe model, prompting, training, decoding, retrieval, annotation, or system pipeline in reproducible detail.
6. **Experiments:** Present datasets, baselines, metrics, significance tests, and main results.
7. **Analysis / Error Analysis:** Include qualitative examples, subgroup behavior, robustness checks, annotation disagreements, or failure modes.
8. **Limitations / Ethics:** Discuss dataset bias, privacy, harmful outputs, demographic effects, language coverage, and deployment risks.

## Argument and Tone

- Use precise task language: specify input, output, supervision, evaluation, and target users.
- Avoid treating benchmark score as the whole story. Explain what performance means for language understanding or generation.
- State whether examples are cherry-picked, representative, or sampled by a rule.
- Keep claims about reasoning, understanding, fairness, or factuality tightly linked to the evaluation used.
- When using LLMs, distinguish training, prompting, data generation, judging, and analysis roles.

## Evidence Planning

- Include strong task baselines, simple baselines, and recent pretrained or LLM baselines where appropriate.
- Report statistical significance or confidence intervals for close comparisons.
- Include ablations for data, prompts, retrieval components, model size, decoding, and annotation filters.
- Include human evaluation details when automatic metrics are insufficient: instructions, annotator counts, agreement, and quality control.
- Include error analysis across phenomena, domains, languages, or demographic groups when relevant.

## Outline Guidance

- Add a Task Definition or Dataset section when the work introduces or changes an evaluation setup.
- Reserve space for Error Analysis; it is often more persuasive in ACL papers than another small metric table.
- Add Limitations and Ethics when data, users, generation, or deployment risks are material.
- Prefer examples and compact tables that make linguistic behavior inspectable.
