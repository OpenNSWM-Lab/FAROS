# Case 01: UCI Iris real-data analysis

Downloads the official UCI Iris archive, verifies its SHA-256, and evaluates a
deterministic nearest-centroid classifier against a majority-class baseline.

- Source: https://archive.ics.uci.edu/dataset/53/iris
- DOI: https://doi.org/10.24432/C56C76
- License: CC BY 4.0
- Fixed seed: 42

Run from `backend/`:

```bash
python -m app.modules.code.challenge_cases.case_01_data_analysis.run --output data/challenge_cup/case_01
```
