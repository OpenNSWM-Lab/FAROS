# ReviewX Evaluation

This folder contains the lightweight evaluation harness for ReviewX.

## What to Export

Each exported JSONL row follows `reviewx_eval_v1` and includes:

- review metadata: `reviewId`, `paperId`, `budgetMode`, model/provider
- summary metrics: findings, severity counts, support counts, coverage
- mismatch metrics: `meanMismatch`, `maxMismatch`, high-mismatch claim count
- claim-level scores: raw mismatch, calibrated mismatch, support status, linked evidence count
- graph stats: claim/evidence/verification/finding node and edge counts
- revision loop state: action items and improvement requests
- model trace: local passes, LLM call count, estimated token cost

## Quick Export

Start the backend, then run:

```bash
python experiments/reviewx_eval/export_reviewx_eval.py \
  --api-base http://localhost:8005 \
  --output experiments/reviewx_eval/reviewx_eval.jsonl
```

The script exports the latest completed ReviewX run for every paper returned by
`/api/v1/papers`. Run ReviewX from the UI first if a paper has no completed run.

## Batch Run

Run ReviewX for each sample and each selected mode:

```bash
python experiments/reviewx_eval/run_eval.py \
  --api-base http://localhost:8005 \
  --papers experiments/reviewx_eval/sample_papers.example.jsonl \
  --modes local_only,balanced,deep \
  --ablations full \
  --output experiments/reviewx_eval/outputs/reviewx_runs.jsonl \
  --continue-on-error
```

For long API runs, add `--resume` to append to an existing output and skip
sample/method pairs that were already exported successfully.

If `--papers` is omitted, the runner fetches all papers from `/api/v1/papers`.

## Frozen Experiment Manifests

Paper-facing runs should use a frozen manifest instead of ad hoc mode flags.
The manifest hashes the sample/gold files, every sampled paper artifact tree,
referenced experiment/code artifacts, the method matrix, and ReviewX evaluation
code. It also records the Git branch/commit and dirty-worktree state.

Create and verify a manifest:

```bash
python experiments/reviewx_eval/freeze_experiment.py \
  --samples experiments/reviewx_eval/cem_bench/v2_arxiv5_frozen/samples.jsonl \
  --gold experiments/reviewx_eval/cem_bench/v2_arxiv5_frozen/gold_labels.jsonl \
  --matrix experiments/reviewx_eval/experiment_matrices/p0_local_ablations.json \
  --output experiments/reviewx_eval/outputs/v2_arxiv5_manifest.json \
  --api-base http://127.0.0.1:8005

python experiments/reviewx_eval/freeze_experiment.py \
  --verify experiments/reviewx_eval/outputs/v2_arxiv5_manifest.json
```

Run or resume exactly that experiment:

```bash
python experiments/reviewx_eval/run_eval.py \
  --experiment-manifest experiments/reviewx_eval/outputs/v2_arxiv5_manifest.json \
  --output experiments/reviewx_eval/outputs/v2_arxiv5_runs.jsonl \
  --continue-on-error \
  --resume
```

`run_eval.py` refuses changed inputs/code and refuses to resume an output whose
rows have a different experiment fingerprint. Each row records the method
configuration and repetition index. `--verify-only` checks the manifest without
calling the backend. Method matrices support explicit provider/model, repetition
count, and optional `maxEstimatedTokens`; unsupported method kinds fail closed.

## Build CEM-Bench

Import public arXiv source packages as external real-paper inputs:

```bash
python experiments/reviewx_eval/import_arxiv_papers.py \
  --arxiv-id 2408.06292 \
  --arxiv-id 2501.04227 \
  --manifest-output docs/tempdocs/arxiv_real_sources.jsonl
```

The importer stores source provenance and licensing metadata under the local
paper record. Imported source data is written under `backend/data/papers` and
must remain git-ignored. These papers are external evaluation inputs, not
FAROS-generated outputs.

Build leakage-resistant v2 variants in natural manuscript sections. V2 keeps
corruption labels outside ReviewX-visible paper IDs, titles, metadata, and
LaTeX content:

```bash
python experiments/reviewx_eval/make_cem_bench_v2.py \
  --source-manifest docs/tempdocs/arxiv_real_sources.jsonl \
  --max-sources 5 \
  --corruption-suite all \
  --overwrite \
  --output-dir experiments/reviewx_eval/cem_bench/v2_arxiv5

python experiments/reviewx_eval/validate_benchmark_leakage.py \
  --samples experiments/reviewx_eval/cem_bench/v2_arxiv5/samples.jsonl \
  --gold experiments/reviewx_eval/cem_bench/v2_arxiv5/gold_labels.jsonl \
  --backend-data backend/data
```

Always run the leakage validator before reporting v2 results. `make_cem_bench.py`
remains available for engineering regression tests, but its explicit injected
section and metadata must not be used for headline paper results.

Create a small controlled corruption benchmark from a local FAROS paper:

```bash
python experiments/reviewx_eval/make_cem_bench.py \
  --source-paper-id paper_d9e1e21eb18c \
  --overwrite
```

For a phase-1 local benchmark with controlled clean source variants:

```bash
python experiments/reviewx_eval/make_cem_bench.py \
  --source-paper-id paper_d9e1e21eb18c \
  --controlled-source-variants 4 \
  --overwrite \
  --output-dir experiments/reviewx_eval/cem_bench/phase1_generated
```

For real multi-source benchmarks, repeat `--source-paper-id`, pass
`--source-paper-ids`, use `--source-manifest`, or use `--auto-discover`.
The builder refuses corrupted CEM-Bench papers as sources. Use
`--real-sources-only` to exclude controlled CEM-Bench source variants as well.

Dry-run source discovery before generating a benchmark:

```bash
python experiments/reviewx_eval/make_cem_bench.py \
  --auto-discover \
  --real-sources-only \
  --dry-run
```

A source manifest may be JSONL:

```json
{"paperId":"paper_001","notes":"real FAROS paper"}
{"paperId":"paper_002","include":false,"notes":"skip draft"}
```

or JSON:

```json
{"papers":[{"paperId":"paper_001"},{"paperId":"paper_002"}]}
```

Build from a manifest:

```bash
python experiments/reviewx_eval/make_cem_bench.py \
  --source-manifest docs/tempdocs/phase4_real_sources.jsonl \
  --real-sources-only \
  --corruption-suite all \
  --overwrite \
  --output-dir experiments/reviewx_eval/cem_bench/phase4_real_generated
```

To generate hard cases for testing LLM gap-scan and citation semantics:

```bash
python experiments/reviewx_eval/make_cem_bench.py \
  --source-paper-id paper_d9e1e21eb18c \
  --source-paper-id paper_cembench_source_68a414f7d2_retrieval \
  --corruption-suite hard \
  --overwrite \
  --output-dir experiments/reviewx_eval/cem_bench/phase2_hard_generated
```

Use `--corruption-suite all` to include both standard and hard corruptions.

Then run ReviewX on the generated benchmark:

```bash
python experiments/reviewx_eval/run_eval.py \
  --api-base http://localhost:8005 \
  --papers experiments/reviewx_eval/cem_bench/generated/samples.jsonl \
  --modes local_only,balanced \
  --ablations full,no_verifier,no_mismatch_routing,no_revision_feedback,no_llm_calibration \
  --output experiments/reviewx_eval/outputs/cembench_runs.jsonl \
  --continue-on-error
```

To run paper ablations, add `--ablations`:

```bash
python experiments/reviewx_eval/run_eval.py \
  --api-base http://localhost:8005 \
  --papers experiments/reviewx_eval/sample_papers.example.jsonl \
  --modes balanced \
  --ablations full,no_verifier,no_citation_semantic,no_mismatch_routing,no_risk_tree,no_revision_feedback,no_llm_calibration \
  --output experiments/reviewx_eval/outputs/ablation_runs.jsonl \
  --continue-on-error
```

Supported ablation modes:

- `no_verifier`: skip evidence verification and test rule-only risk analysis.
- `no_citation_semantic`: keep other verifiers but remove claim-citation semantic checks.
- `no_external_calibration`: treat missing external-paper artifacts as strict unsupported evidence gaps.
- `no_mismatch_routing`: replace CEM routing with severity/confidence routing.
- `no_risk_tree`: remove risk question tree output.
- `no_revision_feedback`: disable improvement-request feedback calibration.
- `no_llm_calibration`: keep LLM text refinement but disable LLM decision score calibration.

ReviewX support statuses are intentionally distinct:

- `supported`: linked evidence directly supports the claim.
- `weakly_supported`: linked evidence is relevant but not conclusive.
- `unsupported`: available evidence fails to support the claim.
- `contradicted`: available evidence conflicts with the claim.
- `artifact_absent`: a required FAROS-local artifact was not imported; this is not proof of a false claim.
- `needs_human_verification`: metadata-level checks are inconclusive and require full-text or expert review.

Each sample row should contain at least:

```json
{"sampleId":"paper_001_clean","paperId":"paper_001","sampleType":"faros_clean","title":"..."}
```

## Score Against Gold Labels

Use lightweight gold labels to compute first-pass detection metrics:

```bash
python experiments/reviewx_eval/score_eval.py \
  --predictions experiments/reviewx_eval/outputs/reviewx_runs.jsonl \
  --gold experiments/reviewx_eval/gold_labels.example.jsonl \
  --samples experiments/reviewx_eval/sample_papers.example.jsonl \
  --output experiments/reviewx_eval/outputs/scores.json \
  --csv-output experiments/reviewx_eval/outputs/scores.csv
```

Generate paper-ready aggregate tables:

```bash
python experiments/reviewx_eval/analyze_results.py \
  --predictions experiments/reviewx_eval/outputs/reviewx_runs.jsonl \
  --output experiments/reviewx_eval/outputs/analysis.csv
```

For CEM-Bench runs, generate compact paper tables from the score JSON:

```bash
python experiments/reviewx_eval/make_paper_tables.py \
  --scores experiments/reviewx_eval/outputs/cembench_scores.json \
  --analysis experiments/reviewx_eval/outputs/cembench_analysis.csv \
  --output-dir experiments/reviewx_eval/outputs/paper_tables
```

Export selected findings for human quality annotation:

```bash
python experiments/reviewx_eval/export_human_eval.py \
  --predictions experiments/reviewx_eval/outputs/cembench_runs.jsonl \
  --gold experiments/reviewx_eval/cem_bench/generated/gold_labels.jsonl \
  --csv-output experiments/reviewx_eval/outputs/human_eval_selected_findings.csv \
  --jsonl-output experiments/reviewx_eval/outputs/human_eval_selected_findings.jsonl \
  --blind \
  --shuffle-seed 20260710
```

The human-eval export includes blank columns for:

- `humanCorrectness`
- `humanActionability`
- `humanSpecificity`
- `humanGrounding`
- `humanSeverityAgreement`
- `humanNotes`

After annotators fill the 1-5 human score columns, summarize the ratings:

```bash
python experiments/reviewx_eval/summarize_human_eval.py \
  --input experiments/reviewx_eval/outputs/human_eval_selected_findings.csv \
  --csv-output experiments/reviewx_eval/outputs/human_eval_summary.csv \
  --json-output experiments/reviewx_eval/outputs/human_eval_summary.json
```

Gold rows follow `gold_schema.json` and include:

```json
{
  "sampleId": "paper_001_corrupt_numeric_01",
  "paperId": "paper_001",
  "corruptionType": "numeric_mismatch",
  "targetClaimText": "The method improves accuracy by 62%.",
  "expectedRiskType": "unsupported_claim",
  "expectedSupportStatus": "contradicted",
  "targetSection": "Results",
  "severity": "blocker"
}
```

Gold labels are non-exhaustive by default. In this mode, precision and F1 are
reported as null rather than silently treating unlabelled findings as correct.
Use `--gold-is-exhaustive` only after every finding has been adjudicated; then
unmatched issue findings are counted as false positives. The deprecated
`--ignore-unmatched-findings` flag is retained only for command compatibility.

The scorer reports three complementary stages:

- `targetExtractionRecall`: whether the target claim entered the review set.
- `targetFlagRecall`: whether ReviewX emitted a finding for the target claim.
- `triageRecall`: whether any claim verifier produced the expected support state.

Strict `unsupportedRecall` and `contradictedRecall` remain separate. A claim may
have multiple verifier-level support states, while `supportStatus` is the most
severe aggregate state shown in the UI.

Optional outputs:

- `--corruption-csv-output`: per-corruption detection rate table.
- `--sample-output`: sample-level JSONL with detection status and matched finding IDs.
- `--samples`: sample manifest used to compute same-source clean baseline deltas.

Compute confidence intervals by resampling source papers, not individual
variants derived from the same paper:

```bash
python experiments/reviewx_eval/bootstrap_scores.py \
  --samples experiments/reviewx_eval/outputs/cembench_samples.jsonl \
  --output experiments/reviewx_eval/outputs/cembench_bootstrap.json \
  --csv-output experiments/reviewx_eval/outputs/cembench_bootstrap.csv
```

For blind annotation of strict findings, combine `--all-findings`,
`--strict-findings-only`, `--blind`, and a fixed `--shuffle-seed`. Blind export
masks sample and paper IDs as well as method, gold, source, and cost fields.

## Suggested Baselines

- Standard FAROS reviewer
- ReviewX `local_only`
- ReviewX `balanced`
- ReviewX `deep`
- ReviewX `balanced` w/o verifier
- ReviewX `balanced` w/o mismatch routing
- ReviewX `balanced` w/o risk tree
- ReviewX `balanced` w/o revision feedback
- ReviewX `balanced` w/o LLM calibration

## PeerQA Expert-Alignment Pilot

PeerQA provides real reviewer questions, author answers, and sentence-level
evidence mappings. Keep its CC-BY-NC-SA-4.0 data under the ignored
`external_data/` directory and use it only for non-commercial research.

After downloading PeerQA v1.0 into `external_data/peerqa`, import a diverse
development/held-out pilot:

```bash
python experiments/reviewx_eval/import_peerqa_pilot.py \
  --max-papers 20 \
  --max-per-source 4 \
  --dev-papers 8 \
  --overwrite
```

Run only `development_samples.jsonl` while developing. Keep the held-out split
unseen until the method and alignment threshold are frozen. After ReviewX runs,
export expert-alignment tasks:

```bash
python experiments/reviewx_eval/export_peerqa_human_eval.py \
  --predictions experiments/reviewx_eval/outputs/peerqa_development_local_runs.jsonl \
  --references experiments/reviewx_eval/external_data/peerqa/faros_pilot/development_references.jsonl \
  --output-prefix experiments/reviewx_eval/outputs/peerqa_development_reviewx_alignment \
  --threshold 0.12 \
  --shuffle-seed 20260711
```

The lexical alignment is candidate generation, not a gold label. Human raters
must judge whether ReviewX actually covers the expert question and agrees with
the author-provided evidence. Do not mix this batch into an existing annotation
database without project/batch isolation.

After at least two raters finish, export the website CSV and run the multi-rater
analysis. The answer key is used only at this final unblinding stage:

```bash
python experiments/reviewx_eval/analyze_peerqa_human_eval.py \
  --input experiments/reviewx_eval/outputs/peerqa_annotations_export.csv \
  --answer-key experiments/reviewx_eval/outputs/peerqa_development_reviewx_alignment_answer_key.csv \
  --output-prefix experiments/reviewx_eval/outputs/peerqa_human_analysis \
  --bootstrap-iterations 10000
```

The command reports task-clustered bootstrap confidence intervals,
Krippendorff alpha, pairwise weighted kappa, strict/broad expert-question
coverage, an adjudication queue, and the development-only alignment threshold
curve. Freeze the threshold before running the held-out split.

### NLPeer v2 pilot

NLPeer v2 files require an approved request from the official TU Darmstadt
repository. After placing approved subsets under `external_data/nlpeer-v2`,
import a source-balanced pilot with:

```bash
python experiments/reviewx_eval/import_nlpeer_pilot.py \
  --input-dir experiments/reviewx_eval/external_data/nlpeer-v2 \
  --datasets ARR-EMNLP-2024,EMNLP23,PLOS \
  --max-papers 30 --max-per-dataset 10 --dev-papers 12
```

The importer reads the official ITG/reviews layout without the heavy NLPeer
training dependencies, excludes summary/strength fields, pseudonymizes review
IDs, never exports reviewer names, and keeps the held-out split separate.
Generic `main` review sections remain candidate units and require development
adjudication before they can be treated as weakness gold.

### Matched-budget Qwen comparison

The single-prompt baseline uses section-stratified context, provider-enforced
JSON output, exact-quote grounding checks, and full token/latency provenance:

```bash
backend/.venv/bin/python \
  experiments/reviewx_eval/baselines/single_prompt_qwen.py \
  --samples experiments/reviewx_eval/external_data/peerqa/faros_pilot/development_samples.jsonl \
  --backend-data backend/data \
  --output experiments/reviewx_eval/outputs/peerqa_single_prompt_qwen_matched_development_v2_runs.jsonl \
  --provider-name qwen --model qwen-max \
  --max-input-chars 8000 --max-output-tokens 1800 \
  --max-total-tokens 4000 --max-findings 6 --repetitions 3
```

The stronger one-call structured-rubric baseline checks research question,
method validity, experiment design, claim-evidence agreement, reproducibility,
and scope/limitations under the same context, output, finding-count, and total
token limits. It does not see PeerQA reviewer questions or author answers:

```bash
backend/.venv/bin/python \
  experiments/reviewx_eval/baselines/structured_rubric_qwen.py \
  --samples experiments/reviewx_eval/external_data/peerqa/faros_pilot/development_samples.jsonl \
  --backend-data backend/data \
  --output experiments/reviewx_eval/outputs/peerqa_structured_rubric_qwen_matched_development_runs.jsonl \
  --provider-name qwen --model qwen-max \
  --max-input-chars 8000 --max-output-tokens 1800 \
  --max-total-tokens 4000 --max-findings 6 --repetitions 3
```

Run the development split first. Add this method to the blind paired batch only
after checking failure rate and token-budget compliance. Do not inspect or tune
the rubric against held-out PeerQA questions.

The paired protocol is frozen in
`experiment_matrices/peerqa_matched_budget_protocol.json`. Build a blind paired
batch by selecting the same balanced repetition for both methods:

```bash
python experiments/reviewx_eval/export_peerqa_method_comparison.py \
  --predictions experiments/reviewx_eval/outputs/peerqa_single_prompt_qwen_matched_development_v2_runs.jsonl \
  --predictions experiments/reviewx_eval/outputs/peerqa_reviewx_balanced_matched_development_runs.jsonl \
  --references experiments/reviewx_eval/external_data/peerqa/faros_pilot/development_references.jsonl \
  --output-prefix experiments/reviewx_eval/outputs/peerqa_matched_budget_method_comparison
```

Do not deploy this 106-task comparison batch over an active annotation batch.
It requires a separate project/database and at least two independent raters.
After exporting that batch, run paired analysis with its matching shuffle seed:

```bash
python experiments/reviewx_eval/analyze_peerqa_human_eval.py \
  --input experiments/reviewx_eval/outputs/peerqa_method_comparison_annotations.csv \
  --answer-key experiments/reviewx_eval/outputs/peerqa_matched_budget_method_comparison_answer_key.csv \
  --shuffle-seed 20260712 \
  --output-prefix experiments/reviewx_eval/outputs/peerqa_matched_budget_human_analysis
```

The report includes per-method task-bootstrap intervals and paired
right-minus-left effects keyed by `comparisonPairId`.

For comparisons with three or more methods, the exporter may place all methods
for the same expert question under one `comparisonPairId`. The analyzer reports
all deterministic pairwise right-minus-left effects and pair-clustered bootstrap
intervals. Use the exact shuffle seed recorded by that batch manifest when
unblinding.

Because PeerQA contains multiple reviewer questions per paper, the analysis
also reports `paperClusterBootstrap95CI` by resampling complete papers. Use this
cluster interval for headline results; the task bootstrap remains available for
backward compatibility and diagnostics.

Comparison batch manifests use `peerqa_method_comparison_batch_v2`. They record
balanced per-method task counts and SHA-256 digests for both answer-key and blind
CSV/JSONL files. Verify these digests before deployment or unblinding.

## Initial Metrics

- unsupported claim count
- contradicted claim count
- mean mismatch score
- raw mean mismatch and calibration gain
- high mismatch claim count
- actionable finding count
- created and resolved improvement request count
- LLM token cost and latency
- selected findings per run
- selected finding gold precision
- selected finding actionability score
- selected reviewer assessment specificity score
- selected finding grounding cue score
- selected reviewer assessment rate
- low-confidence citation findings selected for LLM review
- token cost per selected finding
- token cost per valid reviewer decision
- injected issue detection rate by corruption type
- issue finding delta from the same-source clean control

## Files

- `export_reviewx_eval.py`: export latest completed ReviewX eval records.
- `run_eval.py`: batch run ReviewX modes and export predictions.
- `score_eval.py`: score predictions against gold labels.
- `analyze_results.py`: aggregate raw/final mismatch, calibration, routing, cost, and finding metrics.
- `make_paper_tables.py`: convert score JSON into compact paper summary tables.
- `export_human_eval.py`: export selected findings for human actionability/specificity/grounding annotation.
- `summarize_human_eval.py`: aggregate filled human annotation CSVs into method-level quality scores.
- `make_cem_bench.py`: generate local controlled corruption CEM-Bench samples.
- `prepare_external_samples.py`: normalize PeerRead/OpenReview/MOPRD/custom JSONL rows into CEM-Bench manifests.
- `sample_papers.example.jsonl`: sample input format.
- `gold_labels.example.jsonl`: small example gold labels.
- `gold_schema.json`: gold label schema.
