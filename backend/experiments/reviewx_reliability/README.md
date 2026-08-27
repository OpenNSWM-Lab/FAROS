# ReviewX Scientific Reliability Benchmark

This benchmark creates paired clean and faulty experiment packages from real
SciFact, Climate-FEVER, and PubHealth prediction records. It evaluates whether
Qwen alone, static structure rules, and the full FAROS evidence audit reject:

- fabricated aggregate metrics;
- final-test label tuning;
- claim-group split leakage;
- selective metric reporting;
- evaluation-record hash tampering.

The default protocol creates 45 faulty cases and 45 paired clean controls. Raw
dataset text is never sent to Qwen. FAROS receives the evaluation records because
independent metric recomputation is the capability being tested.

Run from `backend/` after the SciFact and multidomain experiments exist:

```bash
python -m experiments.reviewx_reliability.run \
  --experiment-root data/experiments \
  --output-dir data/experiments/reviewx_reliability \
  --replicas 3 \
  --qwen
```

Generated cases, predictions, provider traces, and reports remain under the
ignored `backend/data/` directory. Controlled fault detection is a reliability
test; it is not a measure of paper quality or human peer-review helpfulness.
