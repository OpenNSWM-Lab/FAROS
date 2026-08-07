# Module Handoff: `idea`

## Identity

- Branch: `devtzb_idea`
- Base `origin/devtzb` commit: `960c5eb`
- Head commit: `f387695`
- Owner: `devtzb`
- Handoff time: `2026-08-07 12:00 CST`

## Delivered Capability

The Idea module accepts any `ScientificQuestion` and produces a contract-compliant
`ResearchDossier` containing:

1. **ProblemFrame** — scoped question, terminology, variables, hypotheses,
   boundaries, out-of-scope items, and sub-questions derived from the original
   question via LLM problem framing.
2. **Evidence-classified hypotheses** — each hypothesis binds supporting/counter/
   context evidence IDs, a derivation trace, falsification criteria, confounders,
   alternative explanations, and per-dimension scores (novelty/feasibility/evidence
   in [0,1]).
3. **ResearchPlan** — step-by-step plan with inputs, tools, methods, outputs,
   metrics, stopping conditions, dependencies, and risks for each step.
4. **Budget modes** — `coverage` (fast, no LLM framing/BFTS/deep reading) and
   `deep` (full pipeline). Both produce the same `ResearchDossier` schema.
5. **Degradation states** — explicit handling for NO_API, SEARCH_FAILURE,
   INSUFFICIENT_EVIDENCE, and TOPIC_DRIFT, each with fallback actions and
   confidence caps.
6. **Review feedback loop** — `create_child_run()` creates a v2 dossier from a
   Review finding; `diff_dossiers()` computes structured v1/v2 differences across
   problem frame, evidence, hypotheses, and plan.

## Contract Boundary

- Canonical input fixture(s): `backend/tests/fixtures/scientific_research/research_dossier.json`
- Produced contract(s): `ResearchDossier`, `ProblemFrame`, `Hypothesis`, `EvidenceRecord`, `EvidenceMap`, `ResearchPlan`, `ArtifactRef`, `GenerationTrace`
- Produced artifact kinds: `DOSSIER`, `PROBLEM_FRAME`, `RESEARCH_PLAN`
- New optional contract fields requested: `none`
- Degraded and failure states:
  - `NO_API` — no LLM provider configured; skip LLM-dependent steps, cap confidence at 0.3
  - `SEARCH_FAILURE` — all search sources returned 0 results; use local corpus only, cap at 0.2
  - `INSUFFICIENT_EVIDENCE` — fewer than 3 evidence records; mark evidence gaps, cap at 0.4
  - `TOPIC_DRIFT` — seed topic lost during generation; fall back to typed directions, cap at 0.3

## Verification

```text
cd backend && .venv/Scripts/python.exe -m pytest \
  tests/test_scientific_research_contracts.py \
  tests/test_research_dossier_schema.py \
  tests/test_idea_problem_framing.py \
  tests/test_idea_evidence_gate_contract.py \
  tests/test_idea_falsifiability.py \
  -v --tb=short

============================= 52 passed in 2.80s ==============================
```

- Independent demo input: `POST /api/v1/ideas/dossier` with `sessionId` from a completed idea session
- Independent demo output: `ResearchDossier` JSON (validated against contract schema)
- Live API required: `yes` — Qwen via OpenAI-compatible endpoint (api.silra.cn)
- Runtime and estimated cost: ~3-5 min per deep-mode run (63+ LLM calls, 140+ search calls); coverage mode ~30-60s

## Integration Impact

- Global router or navigation request: `none` — uses existing `/api/v1/ideas/` prefix
- Database migration: `none` — in-memory session storage
- Dependency/configuration change: `none` — uses existing LLM provider config
- Cross-module assumptions:
  - Code module expects `ResearchDossier.researchPlan` steps with `tool` field matching its executor registry
  - Paper module expects `ResearchDossier.hypotheses` with `evidenceRefs` resolvable via `evidenceMap`
  - Review module expects `ResearchDossier` to round-trip through `model_validate()` for finding injection
- Known limitations:
  - Evidence stance classification uses heuristic rules (relevance score + keyword matching), not LLM judgment
  - Falsification criteria are auto-derived from hypothesis text + experiment metrics, not LLM-generated
  - `coverage` mode skips ProblemFrame LLM framing; ProblemFrame is built from seed query only

## Evidence for Challenge Cup

- Screenshot/data table to retain: 52-test pass output + ResearchDossier JSON sample
- Traceable innovation claim supported: "Idea module outputs contract-compliant ResearchDossier with evidence-classified hypotheses, falsification criteria, and budget-graded degradation states"
- Result is real, simulated, or planned: `real` — tests use real Pydantic validation against frozen contract schema; LLM-dependent steps tested with mock fixtures

## Integration Checklist

- [x] Merged the current `origin/devtzb` into the module branch.
- [x] Shared contract tests pass.
- [x] Module tests pass.
- [x] Canonical fixture works without upstream services.
- [x] No official-question special cases were added.
- [x] No secret, database, runtime artifact, or generated result is tracked.
- [x] PR targets `devtzb` and contains no unrelated changes.
