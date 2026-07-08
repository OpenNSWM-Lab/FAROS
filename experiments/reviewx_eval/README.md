# ReviewX Evaluation

This folder contains the lightweight evaluation harness for ReviewX.

## What to Export

Each exported JSONL row follows `reviewx_eval_v1` and includes:

- review metadata: `reviewId`, `paperId`, `budgetMode`, model/provider
- summary metrics: findings, severity counts, support counts, coverage
- mismatch metrics: `meanMismatch`, `maxMismatch`, high-mismatch claim count
- claim-level scores: mismatch score, support status, linked evidence count
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
  --output experiments/reviewx_eval/outputs/reviewx_runs.jsonl \
  --continue-on-error
```

If `--papers` is omitted, the runner fetches all papers from `/api/v1/papers`.

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
  --output experiments/reviewx_eval/outputs/scores.json \
  --csv-output experiments/reviewx_eval/outputs/scores.csv
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

## Suggested Baselines

- Standard FAROS reviewer
- ReviewX `local_only`
- ReviewX `balanced`
- ReviewX `deep`

## Initial Metrics

- unsupported claim count
- contradicted claim count
- mean mismatch score
- high mismatch claim count
- actionable finding count
- created and resolved improvement request count
- LLM token cost and latency

## Files

- `export_reviewx_eval.py`: export latest completed ReviewX eval records.
- `run_eval.py`: batch run ReviewX modes and export predictions.
- `score_eval.py`: score predictions against gold labels.
- `sample_papers.example.jsonl`: sample input format.
- `gold_labels.example.jsonl`: small example gold labels.
- `gold_schema.json`: gold label schema.
