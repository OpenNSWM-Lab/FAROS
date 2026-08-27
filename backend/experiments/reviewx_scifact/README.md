# ReviewX SciFact Real-Data Experiment

This experiment evaluates ReviewX's factorized claim-evidence consistency model on the official SciFact train/dev split.

SciFact contains scientific claims, paper abstracts, and human annotations for `SUPPORT`, `CONTRADICT`, and no-evidence cases. The experiment maps `CONTRADICT` and no-evidence pairs to ReviewX's positive `unsupported` class. It trains only on the official train split and evaluates only on the official dev split.

Run from `backend/`:

```bash
python -m experiments.reviewx_scifact.run \
  --data-dir data/external/scifact \
  --output-dir data/experiments/reviewx_scifact \
  --download
```

Generated datasets and results stay under the ignored `backend/data/` tree. The runner writes:

- `data/frozen_benchmark.json`
- `evaluation_records.json`
- `metrics.json`
- `summary.json`
- `experiment_report.md`

The checked-in [RESULTS.md](RESULTS.md) records aggregate results, ablations,
uncertainty, and the FAROS integrity-audit outcome without redistributing raw data.

## Two-round Qwen-guided loop

The competition evidence runner keeps the official SciFact dev split hidden from
Qwen until after a round-two plan is selected. It executes both rounds on one
claim-grouped frozen feedback benchmark, audits method-specific decision
thresholds, and then evaluates the selected revision once on the untouched dev
split:

```bash
python -m experiments.reviewx_scifact.closed_loop \
  --data-dir data/external/scifact \
  --output-dir data/experiments/reviewx_scifact_closed_loop \
  --model qwen3.7-plus-2026-05-26
```

All prompts, Qwen usage metadata, evidence bundles, per-record predictions, and
the competition-oriented report remain under the ignored output directory.

The source repository does not expose a standard SPDX license. FAROS therefore downloads SciFact from its official URL at runtime and does not redistribute it. Review the upstream terms before external redistribution.

References:

- [SciFact repository](https://github.com/allenai/scifact)
- [Fact or Fiction: Verifying Scientific Claims](https://aclanthology.org/2020.emnlp-main.609/)
