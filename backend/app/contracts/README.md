# FAROS Scientific Research Contracts

This package defines the stable `scientific-research/v1` boundary between Idea,
Code, Paper, Review, and Platform. It does not replace module-internal models.

## Contract Flow

```text
ScientificQuestionRun
  -> Idea: ResearchDossier
  -> Code: ExecutionAssessment + ExperimentEvidence
  -> Paper: ResearchNarrative
  -> Review: QualityAssessment
  -> Platform: QuestionBatch aggregation and artifact index
```

The 125 competition questions are represented by `QuestionSetManifest` and
processed as ordinary `ScientificQuestionRun` children of `QuestionBatch`.
No module may hard-code a question ID or special-case the official set.

## Rules

1. Import shared handoff models from `app.contracts`.
2. Keep richer persistence models inside the owning module and convert them at
   the boundary.
3. Do not import another module's service, storage, or private model directly.
4. Treat `schemaVersion`, IDs, hashes, statuses, and enum meanings as frozen.
5. During independent development, only add optional fields. Renames, removals,
   type changes, and semantic changes require an integration decision.
6. `executed` means reproducible artifacts exist. Planned or simulated work
   must use the appropriate status and wording.
7. `QualityAssessment.findings` points to the responsible module and field;
   Review reports defects but does not silently rewrite upstream artifacts.
8. Keep module-specific additions under `metadata` until they are accepted into
   the shared contract.

Unknown fields are rejected to catch misspellings and accidental contract
drift. Score fields use the closed interval `[0, 1]`. Hypothesis evidence IDs
must resolve inside the dossier.

## Canonical Fixtures

Fixtures live in `backend/tests/fixtures/scientific_research/`. They are the
independent-development inputs and outputs and contain demonstration data only.
They are not competition evidence or experiment results.

Run the contract checks before opening a pull request:

```bash
cd backend
pytest -q tests/test_scientific_research_contracts.py
```

JSON Schemas are available without checked-in generated files:

```python
from app.contracts import contract_json_schemas

schemas = contract_json_schemas()
```

Frontend code imports the matching TypeScript types from
`@/lib/types/scientificResearch` or `@/lib/types`.

## Existing Module Branches

After this contract commit is available on `origin/devtzb`, each owner syncs
their existing branch. These are shared remote branches, so merge instead of
rewriting published history:

```bash
git fetch origin
git switch devtzb_idea       # use devtzb_code, devtzb_paper, or devtzb_reviewx
git pull --ff-only origin devtzb_idea
git merge origin/devtzb
```

Resolve only files owned by that module. Escalate conflicts in contracts,
global routers, navigation, dependency manifests, and database migrations to
the integration owner.

## Pull Request Exit Criteria

- New output validates against the v1 contract.
- Canonical input fixtures still work without a live upstream module.
- Module tests and the shared contract test pass.
- No secrets, runtime data, generated outputs, or databases are committed.
- The PR includes a completed `MODULE_HANDOFF.template.md` report.
