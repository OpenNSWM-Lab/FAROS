# Scientific Research Contract Fixtures

These files are the canonical `scientific-research/v1` module handoffs. They
contain demonstration data only and must not be reported as experiment results.

| File | Producer | Consumer |
| --- | --- | --- |
| `question_run.json` | Platform | all modules |
| `research_dossier.json` | Idea | Code, Paper, Review |
| `execution_assessment.json` | Code | Platform, Paper, Review |
| `experiment_evidence.json` | Code | Paper, Review |
| `research_narrative.json` | Paper | Review, Platform |
| `quality_assessment.json` | Review | all modules |
| `question_batch.json` | Platform | all modules |
| `question_set_manifest.json` | Platform | all modules |

Validate a changed fixture with:

```bash
cd backend
pytest -q tests/test_scientific_research_contracts.py
```
