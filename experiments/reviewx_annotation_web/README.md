# ReviewX Annotation Web

Lightweight authenticated human-annotation service using Python's standard
library and SQLite.

```bash
export ANNOTATION_ACCESS_CODE='replace-with-a-long-code'
export ANNOTATION_SESSION_SECRET='replace-with-at-least-32-random-characters'
python3 server.py \
  --tasks ../reviewx_eval/outputs/peerqa_development_reviewx_alignment_blind.csv \
  --db data/annotations.db \
  --static-dir static
```

The task CSV is imported idempotently. Each annotator receives an independent
rating row for every task. `/api/export.csv` exports annotations in a format
compatible with `summarize_human_eval.py` and adds annotator/status timestamps.
PeerQA tasks also require an explicit expert-question coverage label before a
row becomes complete.

To replace an unanswered task set, first back up the SQLite database and run:

```bash
python3 server.py \
  --tasks ../reviewx_eval/outputs/peerqa_development_reviewx_alignment_blind.csv \
  --db data/annotations.db \
  --replace-tasks --import-only
```

Replacement is refused when any annotation row exists. Inspect the active task
set with `python3 server.py --db data/annotations.db --status-only`.

For independent experiments, import a second batch without replacing or
activating the current one:

```bash
python3 server.py \
  --db data/annotations.db \
  --tasks ../reviewx_eval/outputs/peerqa_matched_budget_method_comparison_blind.csv \
  --batch-id peerqa_matched_budget \
  --batch-name "PeerQA matched-budget comparison" \
  --import-only
```

List and activate batches during a maintenance window:

```bash
python3 server.py --db data/annotations.db --list-batches
python3 server.py --db data/annotations.db \
  --activate-batch peerqa_matched_budget --import-only
```

Tasks, annotations, progress, completion state, and CSV exports are isolated by
batch. A browser page loaded before an operator switches batches cannot save
into the newly active batch and must reload first.

## Server deployment

The systemd service keeps the Python process on `127.0.0.1:8088`. Caddy exposes
the temporary experiment endpoint on `http://SERVER_IP:6236` and forwards the
original protocol so session cookies work on both HTTP and the reserved HTTPS
site. Do not expose port 8088 directly.

The port 6236 endpoint is intentionally temporary and has no transport
encryption. Restrict its security-group source addresses to known annotators,
do not reuse credentials from other systems, and remove the rule after data
collection. Use an ICP-compliant domain with HTTPS, or a suitable non-mainland
deployment, for any long-running public service.
