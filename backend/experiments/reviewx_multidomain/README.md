# ReviewX Multidomain Stress Test

This experiment evaluates the same factorized ReviewX consistency model on two
additional real datasets:

- Climate-FEVER: climate claims paired with supporting, refuting, or
  not-enough-information evidence sentences.
- PubHealth: public-health claims paired with fact-checking articles and
  true/false/mixture/unproven labels.

It reports a lexical baseline, a model fitted in the target domain, a second
feedback round whose decision threshold is selected only on validation data,
and a model fitted only on SciFact and transferred to the target without
retraining. Macro F1 is the primary metric because the binary labels are
imbalanced. The integrity quality gate is separate from performance so negative
transfer remains visible rather than being discarded.

Run from `backend/`:

```bash
python -m experiments.reviewx_multidomain.run \
  --external-data-dir data/external \
  --output-dir data/experiments/reviewx_multidomain \
  --download \
  --bootstrap-samples 500
```

Raw data and generated per-record predictions remain under ignored
`backend/data/` directories. Climate-FEVER has no explicit repository license;
PubHealth includes text sourced from third-party fact-checking sites. Do not
redistribute either raw dataset without a separate rights review.

References:

- <https://github.com/tdiggelm/climate-fever-dataset>
- <https://arxiv.org/abs/2012.00614>
- <https://github.com/neemakot/Health-Fact-Checking>
- <https://aclanthology.org/2020.emnlp-main.623/>
