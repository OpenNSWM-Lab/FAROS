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
