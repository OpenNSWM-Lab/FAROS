# SciFact Real-Data Results

Run date: 2026-08-25

## Dataset and protocol

- Dataset: SciFact official release ([repository](https://github.com/allenai/scifact), [paper](https://aclanthology.org/2020.emnlp-main.609/))
- Archive SHA-256: `11c621288d41ac144d29b13b0f8503b3820b7d6e8b1f6ff24dff335c196d76be`
- Training: 919 unique claim-document pairs from the official train split
- Evaluation: 339 unique claim-document pairs from the official dev split
- Positive class: SciFact `CONTRADICT` or cited-document-without-evidence (`NEI`), mapped to ReviewX `unsupported`
- Controls: fixed decision threshold `0.5`; no dev labels used for training; paired bootstrap with 2,000 samples and seed `20260826`

The runner removes one repeated claim-document reference in the upstream dev file so every frozen benchmark sample has a unique identity.

## Aggregate results

| Method | Precision | Recall | F1 | Brier | ECE | AUROC |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Lexical baseline | 0.7966 | 0.7015 | 0.7460 | 0.1929 | 0.0819 | 0.7784 |
| ReviewX factorized method | 0.7333 | 0.8209 | **0.7746** | **0.1875** | **0.0345** | **0.7804** |

Lower is better for Brier and ECE. The method trades some precision for materially higher recall, improving F1 by `0.0286` and reducing ECE by `0.0475`.

## Ablations

| Variant | F1 | Brier | ECE | AUROC |
| --- | ---: | ---: | ---: | ---: |
| Full method | 0.7746 | 0.1875 | 0.0345 | 0.7804 |
| Without negation factors | 0.7601 | 0.1909 | **0.0330** | 0.7706 |
| Without numeric factor | **0.7765** | **0.1871** | 0.0343 | **0.7811** |
| Without entity factors | 0.7728 | 0.1876 | 0.0361 | 0.7802 |

Negation factors provide the clearest F1 and AUROC contribution. The current numeric alignment factor does not provide a stable gain and should be redesigned or removed before it is presented as an innovation.

## Uncertainty

| Metric | Mean improvement | 95% bootstrap interval | P(improvement) |
| --- | ---: | ---: | ---: |
| F1 | +0.0290 | [-0.0040, 0.0640] | 0.949 |
| Brier | +0.0056 | [-0.0051, 0.0163] | 0.840 |
| ECE | +0.0345 | [-0.0172, 0.0797] | 0.908 |
| AUROC | +0.0020 | [-0.0148, 0.0176] | 0.609 |

Positive values consistently mean improvement. The point estimates and improvement probabilities are promising, but all 95% intervals cross zero. This run is a credible real-data proof of concept, not evidence of statistically conclusive superiority or generalization to full papers.

## FAROS audit

- Frozen benchmark schema and fingerprint: passed
- Benchmark/evaluation sample alignment: passed, 339/339 records
- Independent metric recomputation: passed
- Required named ablations: passed
- Experiment evidence status: `executed`, with no integrity failures
- Benchmark fingerprint: `sha256:2af7d17d837b765c66bfbc6b76ce56a55b13d5ae5382862d4901688c1ad25de8`

Raw data, per-sample predictions, logs, and evidence bundles remain under ignored `backend/data/` paths and are not redistributed.
