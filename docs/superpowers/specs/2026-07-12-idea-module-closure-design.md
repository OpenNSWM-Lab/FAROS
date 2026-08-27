# FAROS Idea Module Closure Design

## 1. Objective

Complete one bounded closure round for the Idea module, then freeze its primary
pipeline. The closure must improve generic evidence relevance, make retrieval
role telemetry trustworthy, preserve the two-final-candidate contract, and
reduce the cost of rejecting low-quality evidence pools.

This work does not add a literature-specific, Chinese-specific, or other
domain-specific research module. It applies the same evidence rules to all
seeds and paper types.

## 2. Current Evidence

The black-box session `idea_61873f0261c9` used the seed
`预测红楼梦可能结局` and reached `awaiting_evidence`, which is the correct
lifecycle outcome for insufficient evidence. It did not fail because of a
backend exception or a complete external-search outage.

The run still exposed general pipeline defects:

- 120 papers survived into the stored raw pool, despite 72 results being
  filtered out.
- High-ranked papers included unrelated clinical forecasting and chemical
  evaluation work.
- The initial paper gate reported `roleCoverage.enabled=false` because it
  evaluated `SearchResult` objects with snake-case role fields through logic
  written for camel-case `RawPaper` fields.
- Later role coverage passed even though the LLM evidence reviewer correctly
  found that method, evaluation, and limitation evidence was not directly
  applicable to the seed.
- Duplicate results were discarded without merging their retrieval roles and
  matched queries.
- The run spent about 975 seconds before pausing: about 481 seconds in deep
  reading and 325 seconds in the evidence gate and repair path.

These are generic retrieval and evidence-governance problems. They should be
fixed without teaching FAROS how to conduct literary research.

## 3. Scope

### In Scope

- Extract stable core topic anchors from the seed, domain, and translated query
  families.
- Separate discriminative topic signals from generic academic or task words.
- Classify retrieved papers as direct, transferable, or rejected evidence.
- Merge retrieval provenance during deduplication.
- Make role coverage depend on semantic eligibility and paper type.
- Reject weak evidence pools before expensive deep reading.
- Preserve resumable waiting states and the two-final-candidate completion
  contract.
- Add unit, regression, and real black-box verification for these behaviors.
- Add telemetry needed to explain why papers were accepted, transferred, or
  rejected.

### Out of Scope

- A literature, humanities, Chinese, or named-work research subsystem.
- CNKI or WanFang integration.
- A new vector database, embedding provider, or cross-encoder service.
- Changes to code, experiment, paper, review, or other team-owned modules.
- PlanPackage optimization.
- Rewriting the entire Idea service or storage architecture.

## 4. System Contract

The Idea session lifecycle is the external contract:

1. When evidence and reviewer quality are sufficient, the session may become
   `completed` only with at least two approved, diverse final candidates.
2. When topic-grounded evidence is insufficient, the session becomes
   `awaiting_evidence` and retains its completed work for resume.
3. When evidence is sufficient but fewer than two candidates survive review,
   the session becomes `awaiting_ideas` and retains the evidence pool for
   regeneration.
4. A session must never become `completed` with zero or one final candidate.
5. The system must not manufacture weak candidates merely to satisfy the count
   requirement.

## 5. Topic Intent Profile

Query expansion will expose a normalized topic intent profile in its step
outputs. The profile is diagnostic data and can remain a structured dictionary;
it does not require a new persisted API model in this closure.

The profile contains:

- `coreAnchors`: named works, named entities, domain-specific phrases, acronyms,
  and other terms that identify the research object.
- `taskAnchors`: phrases describing the requested research operation or outcome.
- `methodAnchors`: method families explicitly requested by the user or produced
  by role-specific query expansion.
- `evaluationAnchors`: evaluation concepts explicitly requested or needed for
  the selected paper type.
- `genericTerms`: query words that are too common to establish relevance by
  themselves, such as `analysis`, `method`, `prediction`, `potential`,
  `outcome`, `model`, and `evaluation`.

For a CJK seed, the original CJK text and its English role queries are analyzed
together. A translated named work such as `Dream of the Red Chamber` remains a
core anchor rather than being split into independently decisive words such as
`dream`, `red`, and `chamber`.

Anchor extraction must be deterministic after query expansion. LLM output may
suggest anchors, but rule normalization determines the final profile so that
tests and resumption remain stable.

## 6. Evidence Relevance Tiers

Every search result receives one of three tiers before it becomes a `RawPaper`.

### Direct Evidence

A paper is direct evidence when it visibly matches the research object or a
strong equivalent phrase and also matches at least one task, method, or
evaluation signal. Direct evidence may support background, GAP, principle,
novelty, method, and evaluation claims when its structured contents permit it.

Examples include a paper specifically about `Dream of the Red Chamber`, or a
paper directly about citation-faithful medical RAG for the corresponding seed.

### Transferable Evidence

A paper is transferable evidence when it does not study the exact research
object but strongly matches a requested task-method or task-evaluation
combination. It may support method choices, baselines, metrics, limitations, or
evaluation design, but it must not be presented as direct evidence of the seed's
domain-specific GAP.

For example, computational detection of novel endings may be transferable to a
specific novel-ending task. Generic clinical time-series forecasting is not
transferable merely because the query contains `predicting`.

### Rejected Evidence

A paper is rejected when it matches only generic terms, provider relevance, or
one weak token. Rejected papers do not become raw evidence, do not participate
in role coverage, and are not sent to DeepReader.

### Eligibility Rules

Provider relevance and citation count may reorder eligible papers but cannot
make an ineligible paper eligible. The low external relevance threshold remains
a fallback signal, not a replacement for core or transferable relevance.

Role-query provenance also cannot make a paper eligible. Being returned for an
`evaluation` query means only that the provider returned it for that query.

## 7. Deduplication and Provenance Merge

Deduplication retains the existing identity priority:

1. DOI
2. arXiv ID
3. Semantic Scholar ID
4. normalized title hash

When a duplicate is found, the retained result must merge:

- all unique `retrieval_roles`;
- all unique `matched_queries`;
- available external identifiers;
- sources when the model supports multiple sources, or the preferred source
  plus source telemetry when it does not;
- the strongest provider relevance score;
- the richer non-empty abstract and metadata fields.

The merged result is then scored once. This prevents a paper found by both task
and method queries from retaining only the role of its first occurrence.

## 8. Role-Aware Evidence Gate

Role coverage is calculated only from direct or transferable papers whose
semantic score passes the corresponding tier threshold.

Role requirements are aligned with the existing paper-type evidence coverage:

- `algorithm` and `system`: domain or task evidence, method evidence, evaluation
  evidence, and limitation signals.
- `benchmark`, `evaluation`, and `reproducibility`: task or domain evidence,
  dataset/evaluation evidence, baseline or metric evidence, and limitations.
- `survey`, `position`, and `theory`: domain evidence, claims or synthesis,
  limitations, and an explicit GAP. A method-query hit is not mandatory.
- Other paper types use the conservative algorithm/system requirements.

Role coverage complements topic alignment; it does not bypass minimum aligned
paper counts or minimum top alignment. A passing role count with weak semantic
alignment must still fail.

The rule gate and LLM Evidence Coverage reviewer have separate responsibilities:

- The rule gate verifies identities, anchors, relevance tiers, counts, sources,
  paper-type requirements, and evidence IDs.
- The LLM reviewer judges whether the eligible evidence substantively supports
  the required dimensions.
- The final gate fails when either hard rule requirements or required LLM
  dimensions fail.

This removes the current contradiction where role coverage passes while the LLM
correctly reports that the evidence is unrelated.

## 9. Early Cost Gate

The pipeline performs an inexpensive relevance and coverage check before deep
reading:

1. Expand and translate role-specific queries.
2. Search all configured providers.
3. Merge duplicates and build the topic intent profile.
4. Classify results into direct, transferable, and rejected tiers.
5. Run the raw evidence gate on eligible results.
6. If insufficient, run one anchored literature repair round.
7. If still insufficient, persist diagnostics and enter `awaiting_evidence`.
8. Only then select the strongest eligible papers for DeepReader.
9. Run structured Evidence Gate 2.1 before brainstorming.

Deep-reading selection should remain configurable and bounded. The default
closure target is at most 24 papers, prioritizing direct evidence and reserving
a smaller quota for transferable method and evaluation evidence. Existing
must-cite requirements remain exempt from normal ranking but must be visibly
marked when they do not pass topic relevance.

## 10. Repair and Resume

Evidence repair queries must preserve at least one core anchor and add exactly
one missing dimension at a time. Generic suffixes such as `method evidence`
must not replace the topic anchor.

Repair results pass through the same deduplication, provenance merge, and tier
classification as initial results. A repair query does not grant eligibility.

Resuming `awaiting_evidence` reruns from the stored `resume_from` step without
duplicating RawPaper, StructuredPaper, literature-map, or trace artifacts.
Resuming `awaiting_ideas` reuses the accepted evidence pool and starts from idea
brainstorming or regeneration.

## 11. Candidate Completion Contract

Existing multi-candidate review remains the final authority after evidence
passes. Closure verification will ensure:

- all final candidates pass the mixed rule and LLM review path;
- final candidates directly answer the seed and retain core topic anchors;
- candidate evidence references resolve to eligible papers;
- the selected two candidates are not near-duplicates;
- failed, hidden, or unreviewed candidates cannot fill the final count;
- insufficient final candidates produce `awaiting_ideas`, never `completed`.

No new candidate-generation feature is introduced unless black-box testing
shows that relevant evidence passes but the existing repair and regeneration
loop systematically fails to produce two candidates.

## 12. Telemetry

Literature-search and evidence-gate step outputs will expose:

- result count before deduplication;
- unique result count after deduplication;
- duplicate merge count;
- direct, transferable, and rejected counts;
- rejection counts grouped by reason;
- eligible paper count by retrieval role;
- role coverage requirements and observed counts;
- deep-read requested count and cache-hit count;
- initial and repaired gate decisions;
- time spent in search, relevance filtering, deep reading, gate review, repair,
  brainstorming, and candidate review.

Top-paper diagnostics include title, source, tier, semantic score components,
retrieval roles, matched queries, and the decisive anchors. This telemetry must
not expose hidden model reasoning.

## 13. Testing Strategy

### Unit and Regression Tests

Tests will cover:

- English and CJK tokenization without treating a full CJK sentence as one
  decisive token.
- Preservation of translated named-work and multi-word anchors.
- Rejection of clinical forecasting and chemical evaluation results for an
  unrelated narrative seed.
- Acceptance of direct named-object papers.
- Acceptance of strongly transferable task-method papers.
- Rejection of papers matching only generic query terms.
- Deduplication merging roles and matched queries across providers.
- Initial `SearchResult` and later `RawPaper` gates reading role metadata
  consistently.
- Paper-type-specific role and coverage requirements.
- Role coverage never bypassing weak semantic alignment.
- Evidence repair retaining core anchors.
- `completed` requiring two approved candidates.
- Evidence shortage entering `awaiting_evidence`.
- Candidate shortage entering `awaiting_ideas`.
- Resume avoiding duplicate artifacts.

### Real Black-Box Regression

The existing evaluator's three technical seeds remain the positive suite:

1. `LLM agents for scientific discovery`
2. `citation-faithful medical RAG for high-risk clinical question answering`
3. `reliable multi-agent research automation with evidence-grounded planning and self-review`

The CJK named-work seed remains a negative stress test for generic topic
pollution, not a requirement to add literary capability. Its acceptable outcome
is either two genuinely supported candidates or `awaiting_evidence`; it must not
complete with fabricated support.

## 14. Freeze Acceptance Criteria

Idea is ready to freeze when all of the following hold:

1. All targeted Idea unit and regression tests pass.
2. Python compilation and `git diff --check` pass.
3. Each successful technical black-box session has at least two final candidate
   IDs, and each exposed candidate has passed the configured review gate.
4. No completed session has fewer than two final candidates.
5. Evidence-insufficient sessions use a waiting state rather than failed or
   completed.
6. The top ten evidence papers in each technical run contain no clearly
   unrelated application-domain pollution on manual inspection.
7. Duplicate papers preserve the union of their roles and matched queries.
8. Role coverage is enabled at both raw and structured gate stages when role
   queries exist.
9. Reviewer telemetry confirms real LLM usage when the provider is available,
   while rule fallback remains explicit when it is not.
10. Candidate pairs are not near-duplicates and show no unrequested application
    drift.
11. The CJK stress run no longer ranks generic clinical or chemical papers as
    strong evidence.
12. The closure run records a nonzero structured-paper cache hit when repeated
    stable paper identities are present.
13. Median Idea runtime across the three technical seeds improves relative to
    their recorded baselines, and no evidence-rejected run performs bulk deep
    reading before pausing.

Runtime is an optimization criterion rather than a reason to weaken evidence
quality. Correctness and evidence integrity remain the release gate.

## 15. Delivery Boundaries

Implementation will be split into reviewable commits:

1. Topic intent profile and evidence-tier tests.
2. Deduplication provenance merge.
3. Tier-aware ranking and early raw gate.
4. Paper-type-aware role coverage and structured gate consistency.
5. Resume and completion-contract regressions.
6. Evaluation telemetry and black-box closure report.

After these commits pass the freeze criteria, no further Idea feature work is
planned. Remaining improvements move to the Plan module unless a production
regression violates the lifecycle or evidence-integrity contract above.
