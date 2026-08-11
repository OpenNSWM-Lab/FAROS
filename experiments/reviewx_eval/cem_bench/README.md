# CEM-Bench

CEM-Bench is the controlled benchmark layer for CEM-Review. It complements
FAROS-generated paper demos with reproducible claim-evidence corruptions and
gold labels.

## Build a Local Seed Benchmark

The first version copies a local FAROS paper and injects five controlled claim
errors:

- `numeric_mismatch`
- `missing_baseline`
- `unsupported_claim`
- `citation_gap`
- `brief_guardrail_conflict`

Run:

```bash
python experiments/reviewx_eval/make_cem_bench.py \
  --source-paper-id paper_d9e1e21eb18c \
  --overwrite
```

For the phase-1 engineering benchmark, expand one available FAROS paper into
controlled clean source variants:

```bash
python experiments/reviewx_eval/make_cem_bench.py \
  --source-paper-id paper_d9e1e21eb18c \
  --controlled-source-variants 4 \
  --overwrite \
  --output-dir experiments/reviewx_eval/cem_bench/phase1_generated
```

Controlled source variants are useful for testing the full evaluation harness,
but should later be replaced by genuinely independent FAROS/OpenReview/PeerRead
papers for final paper claims.

This creates local paper records under `backend/data/papers` and writes:

```text
experiments/reviewx_eval/cem_bench/generated/samples.jsonl
experiments/reviewx_eval/cem_bench/generated/gold_labels.jsonl
```

The generated files are ignored by git because they are local benchmark
instances.

## Build From Real Source Papers

For paper-facing experiments, prefer genuinely independent FAROS papers over
controlled clean source variants. First inspect available real sources:

```bash
python experiments/reviewx_eval/make_cem_bench.py \
  --auto-discover \
  --real-sources-only \
  --dry-run
```

Use a JSONL manifest when you want a fixed reproducible source set:

```json
{"paperId":"paper_001","notes":"real FAROS generated paper"}
{"paperId":"paper_002","notes":"imported OpenReview-derived paper"}
```

Then build all standard and hard corruptions:

```bash
python experiments/reviewx_eval/make_cem_bench.py \
  --source-manifest docs/tempdocs/phase4_real_sources.jsonl \
  --real-sources-only \
  --corruption-suite all \
  --overwrite \
  --output-dir experiments/reviewx_eval/cem_bench/phase4_real_generated
```

`--real-sources-only` excludes both corrupted CEM-Bench samples and controlled
source variants. This keeps final paper experiments separate from engineering
regression benchmarks.

## Run ReviewX on CEM-Bench

Start the backend, then run:

```bash
python experiments/reviewx_eval/run_eval.py \
  --api-base http://localhost:8005 \
  --papers experiments/reviewx_eval/cem_bench/generated/samples.jsonl \
  --modes local_only,balanced \
  --ablations full,no_verifier,no_mismatch_routing,no_revision_feedback,no_llm_calibration \
  --output experiments/reviewx_eval/outputs/cembench_runs.jsonl \
  --continue-on-error
```

Score against gold labels:

```bash
python experiments/reviewx_eval/score_eval.py \
  --predictions experiments/reviewx_eval/outputs/cembench_runs.jsonl \
  --gold experiments/reviewx_eval/cem_bench/generated/gold_labels.jsonl \
  --samples experiments/reviewx_eval/cem_bench/generated/samples.jsonl \
  --output experiments/reviewx_eval/outputs/cembench_scores.json \
  --csv-output experiments/reviewx_eval/outputs/cembench_scores.csv \
  --corruption-csv-output experiments/reviewx_eval/outputs/cembench_corruptions.csv \
  --sample-output experiments/reviewx_eval/outputs/cembench_samples.jsonl
```

Injected gold is non-exhaustive, so precision/F1 are null by default. Use
`--gold-is-exhaustive` only after every emitted finding has been adjudicated;
then unmatched strict findings are counted as false positives.

The generated clean sample is a same-source control, not a human-certified
perfect paper. Use `issueFindingDeltaFromClean` to report how many issue
findings are added by each corruption relative to that control.

Use `cembench_corruptions.csv` to inspect detection rate by corruption type.
Use `cembench_samples.jsonl` for per-sample error analysis.

Generate aggregate analysis:

```bash
python experiments/reviewx_eval/analyze_results.py \
  --predictions experiments/reviewx_eval/outputs/cembench_runs.jsonl \
  --output experiments/reviewx_eval/outputs/cembench_analysis.csv
```

Generate paper tables:

```bash
python experiments/reviewx_eval/make_paper_tables.py \
  --scores experiments/reviewx_eval/outputs/cembench_scores.json \
  --output-dir experiments/reviewx_eval/outputs/paper_tables
```

## Paper Use

Use CEM-Bench for controlled detection metrics and ablations:

- unsupported / contradicted precision, recall, F1
- raw vs calibrated mismatch
- CEM routing vs severity/confidence routing
- LLM/revision calibration gain
- same-source clean-control issue delta
- quality-cost tradeoff

This seed benchmark should later be expanded with OpenReview, PeerRead, and
MOPRD samples for real-world generalization.

## External Dataset Bridge

When PeerRead, OpenReview, or MOPRD rows are available as JSONL, normalize them
into the same manifest format:

```bash
python experiments/reviewx_eval/prepare_external_samples.py \
  --input /path/to/openreview_cases.jsonl \
  --source openreview \
  --samples-output experiments/reviewx_eval/cem_bench/external_samples.jsonl \
  --gold-output experiments/reviewx_eval/cem_bench/external_gold_labels.jsonl
```

Expected external row fields can be flexible, but should include at least a
`paperId` matching a local FAROS paper record. Optional fields such as
`targetClaimText`, `expectedSupportStatus`, `expectedRiskType`, `section`, and
`severity` are converted into gold labels.
