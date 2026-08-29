# FAROS Idea Module Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze the FAROS Idea pipeline after making evidence relevance generic and trustworthy, pausing weak evidence before bulk deep reading, and guaranteeing that completed sessions expose at least two approved, diverse candidates.

**Architecture:** Add a focused `evidence_relevance` module that builds a deterministic topic profile, classifies papers as direct/transferable/rejected, and merges duplicate retrieval provenance. Keep orchestration in the existing Idea service, persist relevance diagnostics on `SearchResult` and `RawPaper`, and make both raw and structured evidence gates consume the same semantic eligibility and paper-type rules.

**Tech Stack:** Python 3, Pydantic v2, dataclasses, pytest, FastAPI service/storage patterns already used by FAROS, Qwen-backed black-box evaluator, React/Vite only for the already-implemented waiting-state UI baseline.

---

## File Map

- Create `backend/app/modules/idea/evidence_relevance.py`: deterministic topic profile, evidence assessment, duplicate merge, and paper-type role requirements.
- Create `backend/tests/test_idea_evidence_relevance.py`: focused unit tests for anchors, tiers, deduplication, and role eligibility. This path is ignored by the repository-wide tests rule, so commits must use `git add -f`.
- Modify `backend/app/services/search_service.py`: carry evidence-tier diagnostics on provider-neutral `SearchResult`.
- Modify `backend/app/models/idea.py`: persist tier and score diagnostics on `RawPaper`; retain waiting states already present in the working tree.
- Modify `backend/app/modules/idea/service.py`: integrate topic profiles, early raw gate, bounded deep reading, type-aware role coverage, repair reuse, and telemetry.
- Modify `backend/app/storage/idea_storage.py`: add atomic RawPaper update needed to merge retrieval provenance during resume/repair.
- Modify `backend/tests/test_idea_evidence_gate.py`: raw/structured gate consistency and paper-type requirements. Force-add because this file is currently ignored and untracked.
- Modify `backend/tests/test_search_service.py`: preserve current provider regressions and evidence diagnostic defaults. Force-add because this file is currently ignored and untracked.
- Modify `backend/tests/test_idea_final_candidates.py`: early waiting/resume behavior and the existing two-candidate completion contract.
- Modify `backend/scripts/run_idea_plan_eval.py`: report relevance tiers, deduplication, deep-read cost, waiting outcomes, and freeze assertions without changing Plan generation behavior.
- Create `backend/data/eval_runs/$env:FAROS_EVAL_RUN_ID/idea-closure-report.json` at runtime only; do not commit generated data.

## Task 1: Checkpoint the Existing Closure Foundation

This task separates the already-implemented CJK query roles, waiting states,
resume endpoint/UI, search cooldown, and two-final-candidate contract from the
new relevance work. Do not edit unrelated team modules.

**Files:**
- Modify: `backend/app/models/idea.py`
- Modify: `backend/app/modules/idea/ideas_api.py`
- Modify: `backend/app/modules/idea/service.py`
- Modify: `backend/app/services/prompts.py`
- Modify: `backend/app/services/search_service.py`
- Modify: `backend/tests/test_idea_final_candidates.py`
- Add existing ignored test: `backend/tests/test_idea_evidence_gate.py`
- Add existing ignored test: `backend/tests/test_search_service.py`
- Modify: `frontend/src/components/ideas/IdeaGenerationPanel.tsx`

- [ ] **Step 1: Verify the current focused baseline**

Run:

```powershell
pytest -q backend/tests/test_idea_final_candidates.py backend/tests/test_idea_evidence_gate.py backend/tests/test_search_service.py backend/tests/test_plan_package_llm_schema.py backend/tests/test_plan_package_quality_regressions.py backend/tests/test_plan_package_service_timeout.py backend/tests/test_llm_task_scheduler.py
```

Expected: `60 passed` with no failures.

- [ ] **Step 2: Verify backend syntax and frontend behavior**

Run:

```powershell
python -m py_compile backend/app/models/idea.py backend/app/modules/idea/ideas_api.py backend/app/modules/idea/service.py backend/app/services/prompts.py backend/app/services/search_service.py
Set-Location frontend
npm test -- --run
npm run typecheck
npm run build
Set-Location ..
```

Expected: Python compilation succeeds, frontend tests pass, TypeScript reports
no errors, and Vite produces a successful build.

- [ ] **Step 3: Review the checkpoint diff for scope**

Run:

```powershell
git diff --check
git diff --stat
git diff -- backend/app/models/idea.py backend/app/modules/idea/ideas_api.py backend/app/modules/idea/service.py backend/app/services/prompts.py backend/app/services/search_service.py backend/tests/test_idea_final_candidates.py frontend/src/components/ideas/IdeaGenerationPanel.tsx
```

Expected: only Idea/search/waiting UI changes are present; no code, experiment,
paper, or review module changes appear.

- [ ] **Step 4: Commit only the verified foundation**

```powershell
git add backend/app/models/idea.py backend/app/modules/idea/ideas_api.py backend/app/modules/idea/service.py backend/app/services/prompts.py backend/app/services/search_service.py backend/tests/test_idea_final_candidates.py frontend/src/components/ideas/IdeaGenerationPanel.tsx
git add -f backend/tests/test_idea_evidence_gate.py backend/tests/test_search_service.py
git diff --cached --check
git commit -m "feat: stabilize idea evidence waiting and cjk retrieval"
```

Expected: one commit containing the verified baseline, while unrelated working
tree changes, if any, remain untouched.

## Task 2: Build the Deterministic Topic Profile and Evidence Tiers

**Files:**
- Create: `backend/app/modules/idea/evidence_relevance.py`
- Create: `backend/tests/test_idea_evidence_relevance.py`
- Modify: `backend/app/services/search_service.py`

- [ ] **Step 1: Write failing tests for anchors and tiers**

Create `backend/tests/test_idea_evidence_relevance.py` with these cases:

```python
from app.modules.idea.evidence_relevance import (
    EvidenceTier,
    assess_search_result,
    build_topic_intent_profile,
)
from app.services.search_service import SearchResult


def result(title: str, abstract: str = "") -> SearchResult:
    return SearchResult(
        title=title,
        authors=[],
        abstract=abstract,
        year=2025,
        venue="test",
        url=None,
        doi=None,
        arxiv_id=None,
        citation_count=0,
        source="openalex",
    )


def red_chamber_profile():
    return build_topic_intent_profile(
        seed="预测红楼梦可能结局",
        domain="",
        role_queries={
            "domain": ["Literary analysis of 'Dream of the Red Chamber'"],
            "task": ["Computational prediction of endings for 'Dream of the Red Chamber'"],
            "method": ["Narrative completion using character constraints"],
            "evaluation": ["Narrative coherence and character consistency evaluation"],
        },
    )


def test_named_work_is_preserved_as_one_core_anchor():
    profile = red_chamber_profile()
    assert "dream of the red chamber" in profile.core_anchors
    assert "dream" in profile.generic_terms


def test_direct_named_work_paper_is_eligible():
    assessment = assess_search_result(
        result(
            "Multiple Authors Detection: A Quantitative Analysis of Dream of the Red Chamber",
            "Computational evidence about authorship and the unfinished ending.",
        ),
        red_chamber_profile(),
    )
    assert assessment.tier is EvidenceTier.DIRECT
    assert "dream of the red chamber" in assessment.decisive_anchors


def test_transferable_narrative_method_is_eligible_but_not_direct():
    assessment = assess_search_result(
        result(
            "Narrative completion for unfinished novels",
            "Computational character constraints and coherence evaluation reconstruct plausible endings.",
        ),
        red_chamber_profile(),
    )
    assert assessment.tier is EvidenceTier.TRANSFERABLE


def test_generic_clinical_forecasting_is_rejected():
    assessment = assess_search_result(
        result(
            "Clinical time-series forecasting and analysis",
            "A web platform predicts patient outcomes with configurable models.",
        ),
        red_chamber_profile(),
    )
    assert assessment.tier is EvidenceTier.REJECTED
    assert assessment.rejection_reason == "generic_overlap_only"


def test_generic_chemical_evaluation_is_rejected():
    assessment = assess_search_result(
        result("ChemEval: A multi-level chemical evaluation for language models"),
        red_chamber_profile(),
    )
    assert assessment.tier is EvidenceTier.REJECTED
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```powershell
pytest -q backend/tests/test_idea_evidence_relevance.py
```

Expected: collection fails because
`app.modules.idea.evidence_relevance` does not exist.

- [ ] **Step 3: Implement the relevance types and deterministic profile**

Create `backend/app/modules/idea/evidence_relevance.py` with these public types
and functions:

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Iterable, Mapping, Sequence

from app.services.search_service import SearchResult, tokenize_topic_text


class EvidenceTier(str, Enum):
    DIRECT = "direct"
    TRANSFERABLE = "transferable"
    REJECTED = "rejected"


GENERIC_TERMS = frozenset({
    "analysis", "application", "approach", "evaluation", "exploration",
    "framework", "generation", "method", "model", "outcome", "outcomes",
    "potential", "predict", "predicting", "prediction", "research", "study",
    "system", "using", "dream", "red", "chamber",
})


@dataclass(frozen=True)
class TopicIntentProfile:
    core_anchors: tuple[str, ...]
    task_anchors: tuple[str, ...]
    method_anchors: tuple[str, ...]
    evaluation_anchors: tuple[str, ...]
    generic_terms: tuple[str, ...]

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "coreAnchors": list(self.core_anchors),
            "taskAnchors": list(self.task_anchors),
            "methodAnchors": list(self.method_anchors),
            "evaluationAnchors": list(self.evaluation_anchors),
            "genericTerms": list(self.generic_terms),
        }


@dataclass(frozen=True)
class EvidenceAssessment:
    tier: EvidenceTier
    score: float
    decisive_anchors: tuple[str, ...]
    score_components: Mapping[str, float]
    rejection_reason: str = ""


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _quoted_phrases(values: Iterable[str]) -> tuple[str, ...]:
    phrases: list[str] = []
    for value in values:
        phrases.extend(
            match.strip().lower()
            for match in re.findall(r"[\"']([^\"']{4,100})[\"']", value or "")
        )
    return _unique(phrases)


def _discriminative_tokens(values: Iterable[str]) -> tuple[str, ...]:
    return _unique(
        token
        for value in values
        for token in tokenize_topic_text(value)
        if token not in GENERIC_TERMS and len(token) >= 3
    )


def build_topic_intent_profile(
    *,
    seed: str,
    domain: str,
    role_queries: Mapping[str, Sequence[str]],
) -> TopicIntentProfile:
    domain_queries = list(role_queries.get("domain", ()))
    task_queries = list(role_queries.get("task", ()))
    method_queries = list(role_queries.get("method", ()))
    evaluation_queries = list(role_queries.get("evaluation", ()))
    core_phrases = _quoted_phrases([seed, domain, *domain_queries, *task_queries])
    core_tokens = _discriminative_tokens([seed, domain, *domain_queries])
    return TopicIntentProfile(
        core_anchors=_unique([*core_phrases, *core_tokens]),
        task_anchors=_discriminative_tokens(task_queries),
        method_anchors=_discriminative_tokens(method_queries),
        evaluation_anchors=_discriminative_tokens(evaluation_queries),
        generic_terms=tuple(sorted(GENERIC_TERMS)),
    )


def _hits(text: str, anchors: Sequence[str]) -> tuple[str, ...]:
    return tuple(anchor for anchor in anchors if anchor and anchor in text)


def assess_search_result(
    result: SearchResult,
    profile: TopicIntentProfile,
) -> EvidenceAssessment:
    text = f"{result.title} {result.abstract}".lower().replace("-", " ")
    phrase_hits = tuple(anchor for anchor in profile.core_anchors if " " in anchor and anchor in text)
    core_hits = _hits(text, tuple(anchor for anchor in profile.core_anchors if " " not in anchor))
    task_hits = _hits(text, profile.task_anchors)
    method_hits = _hits(text, profile.method_anchors)
    evaluation_hits = _hits(text, profile.evaluation_anchors)

    components = {
        "corePhrase": min(0.55, 0.55 * len(phrase_hits)),
        "coreTerms": min(0.35, 0.12 * len(core_hits)),
        "task": min(0.25, 0.10 * len(task_hits)),
        "methodEvaluation": min(0.20, 0.06 * (len(method_hits) + len(evaluation_hits))),
        "provider": min(0.10, max(0.0, float(result.relevance_score or 0.0)) * 0.10),
    }
    score = min(1.0, sum(components.values()))
    decisive = _unique([*phrase_hits, *core_hits, *task_hits, *method_hits, *evaluation_hits])
    has_supporting_signal = bool(task_hits or method_hits or evaluation_hits)

    if (phrase_hits or len(core_hits) >= 2) and has_supporting_signal:
        tier = EvidenceTier.DIRECT
        reason = ""
    elif len(task_hits) >= 2 and bool(method_hits or evaluation_hits):
        tier = EvidenceTier.TRANSFERABLE
        reason = ""
    else:
        tier = EvidenceTier.REJECTED
        reason = "generic_overlap_only" if text.strip() else "missing_text"

    return EvidenceAssessment(
        tier=tier,
        score=round(score, 4),
        decisive_anchors=decisive,
        score_components=components,
        rejection_reason=reason,
    )
```

Add provider-neutral diagnostic fields to `SearchResult`:

```python
evidence_tier: str = "unclassified"
decisive_anchors: List[str] = field(default_factory=list)
relevance_components: Dict[str, float] = field(default_factory=dict)
rejection_reason: str = ""
must_cite_override: bool = False
```

- [ ] **Step 4: Verify deterministic normalization**

Run:

```powershell
pytest -q backend/tests/test_idea_evidence_relevance.py backend/tests/test_search_service.py
```

Expected: all tests pass. Do not add a seed-specific branch; corrections must
change generic phrase/token behavior.

- [ ] **Step 5: Commit the relevance core**

```powershell
git add backend/app/modules/idea/evidence_relevance.py backend/app/services/search_service.py
git add -f backend/tests/test_idea_evidence_relevance.py
git diff --cached --check
git commit -m "feat: classify idea evidence by topic relevance"
```

## Task 3: Merge Duplicate Retrieval Provenance and Persist Assessments

**Files:**
- Modify: `backend/app/modules/idea/evidence_relevance.py`
- Modify: `backend/app/services/search_service.py`
- Modify: `backend/app/models/idea.py`
- Modify: `backend/app/storage/idea_storage.py`
- Modify: `backend/tests/test_idea_evidence_relevance.py`

- [ ] **Step 1: Write failing deduplication tests**

Append:

```python
from app.modules.idea.evidence_relevance import deduplicate_search_results


def test_deduplication_merges_roles_queries_sources_and_richer_metadata():
    first = result("Citation-Enforced RAG")
    first.doi = "10.1000/rag"
    first.source = "semantic_scholar"
    first.retrieval_sources = ["semantic_scholar"]
    first.retrieval_roles = ["domain"]
    first.matched_queries = ["citation faithful RAG"]
    first.relevance_score = 0.4

    second = result("Citation-Enforced RAG", "A richer abstract with refusal evaluation.")
    second.doi = "10.1000/rag"
    second.source = "openalex"
    second.retrieval_roles = ["method", "evaluation"]
    second.matched_queries = ["RAG verifier", "RAG refusal benchmark"]
    second.relevance_score = 0.8

    outcome = deduplicate_search_results([first, second])

    assert outcome.merge_count == 1
    assert len(outcome.results) == 1
    merged = outcome.results[0]
    assert merged.retrieval_roles == ["domain", "method", "evaluation"]
    assert merged.matched_queries == [
        "citation faithful RAG", "RAG verifier", "RAG refusal benchmark"
    ]
    assert merged.retrieval_sources == ["semantic_scholar", "openalex"]
    assert merged.abstract == second.abstract
    assert merged.relevance_score == 0.8
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
pytest -q backend/tests/test_idea_evidence_relevance.py::test_deduplication_merges_roles_queries_sources_and_richer_metadata
```

Expected: import or attribute failure for the missing deduplication API.

- [ ] **Step 3: Implement canonical identity and merge behavior**

Add `retrieval_sources` to `SearchResult` and initialize it in
`__post_init__`:

```python
retrieval_sources: List[str] = field(default_factory=list)

def __post_init__(self) -> None:
    if self.source and self.source not in self.retrieval_sources:
        self.retrieval_sources.append(self.source)
```

Add to `evidence_relevance.py`:

```python
from dataclasses import dataclass
import hashlib


@dataclass(frozen=True)
class DedupeOutcome:
    results: tuple[SearchResult, ...]
    merge_count: int


def _identity_keys(result: SearchResult) -> tuple[str, ...]:
    keys: list[str] = []
    if result.doi:
        keys.append(f"doi:{result.doi.lower().strip()}")
    if result.arxiv_id:
        keys.append(f"arxiv:{result.arxiv_id.lower().strip()}")
    semantic_id = re.search(r"SemanticScholarID:(\w+)", result.url or "")
    if semantic_id:
        keys.append(f"s2:{semantic_id.group(1).lower()}")
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", result.title.lower())
    keys.append("title:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest())
    return tuple(keys)


def _append_unique(current: list[str], incoming: Iterable[str]) -> None:
    for value in incoming:
        if value and value not in current:
            current.append(value)


def _merge_result(target: SearchResult, incoming: SearchResult) -> SearchResult:
    _append_unique(target.retrieval_roles, incoming.retrieval_roles)
    _append_unique(target.matched_queries, incoming.matched_queries)
    _append_unique(target.retrieval_sources, incoming.retrieval_sources or [incoming.source])
    if len(incoming.abstract or "") > len(target.abstract or ""):
        target.abstract = incoming.abstract
    if not target.doi and incoming.doi:
        target.doi = incoming.doi
    if not target.arxiv_id and incoming.arxiv_id:
        target.arxiv_id = incoming.arxiv_id
    target.relevance_score = max(target.relevance_score, incoming.relevance_score)
    target.citation_count = max(target.citation_count or 0, incoming.citation_count or 0)
    return target


def deduplicate_search_results(results: Sequence[SearchResult]) -> DedupeOutcome:
    unique: list[SearchResult] = []
    index: dict[str, SearchResult] = {}
    merge_count = 0
    for result in results:
        keys = _identity_keys(result)
        target = next((index[key] for key in keys if key in index), None)
        if target is not None:
            _merge_result(target, result)
            merge_count += 1
        else:
            target = result
            unique.append(target)
        for key in (*_identity_keys(target), *keys):
            index[key] = target
    return DedupeOutcome(tuple(unique), merge_count)


_TIER_PRIORITY = {
    EvidenceTier.REJECTED.value: 0,
    "unclassified": 1,
    EvidenceTier.TRANSFERABLE.value: 2,
    EvidenceTier.DIRECT.value: 3,
}


def better_evidence_tier(current: str, incoming: str) -> str:
    return max((current, incoming), key=lambda tier: _TIER_PRIORITY.get(tier, 0))
```

- [ ] **Step 4: Persist evidence diagnostics and support RawPaper updates**

Add to `RawPaper`:

```python
evidenceTier: str = Field(
    default="unclassified",
    description="direct, transferable, rejected, unclassified",
)
decisiveAnchors: List[str] = Field(default_factory=list)
relevanceComponents: Dict[str, float] = Field(default_factory=dict)
rejectionReason: str = ""
mustCiteOverride: bool = False
```

Add to `RawPaperStorage` using the same atomic writer as `create`:

```python
def update(self, paper: RawPaper) -> RawPaper:
    path = self._get_path(paper.id)
    if not path.exists():
        raise ValueError(f"RawPaper {paper.id} not found")
    data = paper.model_dump()
    data["createdAt"] = (
        data["createdAt"].isoformat()
        if isinstance(data["createdAt"], datetime)
        else data["createdAt"]
    )
    _write_json_atomic(path, data, default=str)
    return paper
```

- [ ] **Step 5: Run model/storage/relevance tests**

Run:

```powershell
pytest -q backend/tests/test_idea_evidence_relevance.py backend/tests/test_structured_paper_cache.py backend/tests/test_search_service.py
```

Expected: all tests pass.

- [ ] **Step 6: Commit the provenance model**

```powershell
git add backend/app/modules/idea/evidence_relevance.py backend/app/services/search_service.py backend/app/models/idea.py backend/app/storage/idea_storage.py
git add -f backend/tests/test_idea_evidence_relevance.py
git diff --cached --check
git commit -m "feat: merge idea retrieval provenance"
```

## Task 4: Integrate Tier-Aware Search and the Early Evidence Gate

**Files:**
- Modify: `backend/app/modules/idea/service.py`
- Modify: `backend/tests/test_idea_final_candidates.py`
- Modify: `backend/tests/test_idea_evidence_gate.py`

- [ ] **Step 1: Write a failing service-level pollution test**

Add a test that constructs a session with stored `expandQuery` role outputs,
stubs `get_search_service().search`, and returns four direct/transferable papers
plus clinical and chemical distractors. Use a temporary service data directory.
The decisive assertions are:

```python
inputs, outputs, _ = service._step_literature_search(session)
stored = service.raw_paper_storage.list_by_session(session.id)

assert all(paper.evidenceTier in {"direct", "transferable"} for paper in stored)
assert not any("Clinical time-series" in paper.title for paper in stored)
assert not any("ChemEval" in paper.title for paper in stored)
assert outputs["evidenceTierCounts"]["rejected"] >= 2
assert outputs["duplicateMergeCount"] >= 0
assert outputs["topicIntentProfile"]["coreAnchors"]
```

- [ ] **Step 2: Run the service test and verify RED**

Run:

```powershell
pytest -q backend/tests/test_idea_final_candidates.py::test_literature_search_rejects_generic_cross_domain_results
```

Expected: failure because search outputs do not expose tier/profile telemetry and
rejected papers can still be persisted.

- [ ] **Step 3: Replace the nested skip-only dedupe with the shared pipeline**

In `_step_literature_search`:

```python
from collections import Counter

profile = build_topic_intent_profile(
    seed=seed,
    domain=session.config.domain or "",
    role_queries=role_queries if isinstance(role_queries, dict) else {},
)
must_cite_refs = [value.lower().strip() for value in (session.config.mustCiteList or [])]

def matches_must_cite(result: SearchResult) -> bool:
    haystack = " ".join(
        value
        for value in [result.doi, result.arxiv_id, result.url, result.title]
        if value
    ).lower()
    return any(reference and reference in haystack for reference in must_cite_refs)

def dedupe_assess_rank(results: List[SearchResult]):
    dedupe = deduplicate_search_results(results)
    persistable: list[SearchResult] = []
    gate_eligible: list[SearchResult] = []
    rejected: list[SearchResult] = []
    for result in dedupe.results:
        assessment = assess_search_result(result, profile)
        result.evidence_tier = assessment.tier.value
        result.decisive_anchors = list(assessment.decisive_anchors)
        result.relevance_components = dict(assessment.score_components)
        result.rejection_reason = assessment.rejection_reason
        result.relevance_score = assessment.score
        if assessment.tier is not EvidenceTier.REJECTED:
            persistable.append(result)
            gate_eligible.append(result)
        else:
            result.must_cite_override = matches_must_cite(result)
            rejected.append(result)
            if result.must_cite_override:
                persistable.append(result)
    persistable.sort(key=lambda item: item.relevance_score, reverse=True)
    gate_eligible.sort(key=lambda item: item.relevance_score, reverse=True)
    return persistable, gate_eligible, rejected, dedupe.merge_count, len(dedupe.results)
```

Before placing a rejected result in the rejected-only list, compare its DOI,
arXiv ID, URL, and normalized title with `session.config.mustCiteList`. A match
sets `result.must_cite_override = True` and keeps the paper for storage and
DeepReader, while its `evidence_tier` remains `rejected`. It is excluded from
aligned counts and role coverage. This preserves an explicit user citation
without allowing it to satisfy the quality gate.

Assign the returned collections explicitly: use `gate_eligible` for the raw
quality gate, use `persistable` for RawPaper/LiteratureItem creation, and use
`rejected` only for rejection telemetry. This prevents a must-cite override from
accidentally satisfying evidence coverage.

Use this same function after initial search and after repair search. Remove the
old `seen_*` loop and do not call `_filter_results_for_topic` as a second,
provider-score-only eligibility decision.

Tag repair results with both `repair` and the missing semantic dimension when
the repair query targets `domain`, `task`, `method`, or `evaluation`:

```python
def repair_roles(query: str) -> list[str]:
    lowered = query.lower()
    roles = ["repair"]
    for role in ("domain", "task", "method", "evaluation"):
        if role in lowered and role not in roles:
            roles.append(role)
    return roles
```

Merge these roles into every result returned for that repair query before
deduplication. `limitation` remains a coverage dimension rather than a retrieval
role.

- [ ] **Step 4: Persist semantic evidence and visible must-cite overrides**

When constructing `RawPaper`, map:

```python
source=list(result.retrieval_sources or ([result.source] if result.source else [])),
retrievalRoles=list(result.retrieval_roles),
matchedQueries=list(result.matched_queries),
evidenceTier=result.evidence_tier,
decisiveAnchors=list(result.decisive_anchors),
relevanceComponents=dict(result.relevance_components),
rejectionReason=result.rejection_reason,
mustCiteOverride=result.must_cite_override,
relevanceScore=min(1.0, max(0.0, result.relevance_score)),
```

Add these outputs:

```python
"topicIntentProfile": profile.to_dict(),
"resultCountBeforeDedup": len(all_results),
"uniqueResultCount": unique_count,
"duplicateMergeCount": duplicate_merge_count,
"evidenceTierCounts": {
    "direct": sum(item.evidence_tier == "direct" for item in unique_results),
    "transferable": sum(item.evidence_tier == "transferable" for item in unique_results),
    "rejected": len(rejected_results),
},
"rejectionReasonCounts": dict(Counter(item.rejection_reason for item in rejected_results)),
```

- [ ] **Step 5: Add an early recoverable error at the correct resume point**

Define:

```python
class AwaitingLiteratureEvidenceError(RecoverableIdeaError):
    waiting_status = IdeaSessionStatus.AWAITING_EVIDENCE
    resume_from = "literatureSearch"
```

After one anchored repair round, if the raw quality gate still fails, raise this
error with the complete search diagnostics. Add a test asserting:

```python
paused = service.run_pipeline(session.id)
assert paused.status is IdeaSessionStatus.AWAITING_EVIDENCE
assert paused.qualityLoopSummary["resumeFrom"] == "literatureSearch"
assert service.structured_storage.list_by_session(session.id) == []
```

- [ ] **Step 6: Run the search and waiting regressions**

Run:

```powershell
pytest -q backend/tests/test_idea_evidence_relevance.py backend/tests/test_idea_evidence_gate.py backend/tests/test_idea_final_candidates.py -k "literature or evidence or waiting or resume or completed"
```

Expected: all selected tests pass; weak raw pools never call DeepReader.

- [ ] **Step 7: Commit the early gate**

```powershell
git add backend/app/modules/idea/service.py backend/tests/test_idea_final_candidates.py
git add -f backend/tests/test_idea_evidence_gate.py
git diff --cached --check
git commit -m "feat: reject weak idea evidence before deep reading"
```

## Task 5: Make Role Coverage Semantic and Paper-Type Aware

**Files:**
- Modify: `backend/app/modules/idea/evidence_relevance.py`
- Modify: `backend/app/modules/idea/service.py`
- Modify: `backend/tests/test_idea_evidence_gate.py`

- [ ] **Step 1: Write failing paper-type and role tests**

Add tests with eligible `RawPaper` fixtures:

```python
from app.models.idea import RawPaper


def raw(title: str, *, tier: str, roles: list[str], index: int = 0) -> RawPaper:
    return RawPaper(
        id=f"raw_gate_{index}",
        sessionId="idea_gate",
        title=title,
        abstract=title,
        source=["openalex"],
        retrievalRoles=roles,
        evidenceTier=tier,
        relevanceScore=0.9,
    )


def test_role_coverage_does_not_pass_with_rejected_query_hits():
    papers = [
        raw("Clinical forecasting", tier="rejected", roles=["task"], index=1),
        raw("Chemical language-model evaluation", tier="rejected", roles=["evaluation"], index=2),
        raw("Generic framework", tier="rejected", roles=["method"], index=3),
        raw("Generic survey", tier="rejected", roles=["domain"], index=4),
    ]
    gate = _evaluate_paper_quality_gate(
        seed="citation-faithful medical RAG",
        domain="medical QA",
        papers=papers,
        stage="test",
        paper_type="system",
    )
    assert gate["roleCoverage"]["passed"] is False
    assert gate["alignedPaperCount"] == 0


def test_survey_does_not_require_method_query_role():
    papers = [
        raw("RAG safety survey and open gaps", tier="direct", roles=["domain"], index=5),
        raw("Retrieval augmented generation safety limitations", tier="direct", roles=["task"], index=6),
        raw("RAG safety claims and synthesis", tier="direct", roles=["domain"], index=7),
        raw("Open questions in safe RAG", tier="direct", roles=["task"], index=8),
    ]
    gate = _evaluate_paper_quality_gate(
        seed="RAG safety survey",
        domain="retrieval augmented generation",
        papers=papers,
        stage="test",
        paper_type="survey",
    )
    assert gate["roleCoverage"]["requirements"]["method"] == 0
    assert gate["roleCoverage"]["passed"] is True


def test_transferable_paper_cannot_fill_domain_role():
    paper = raw(
        "Narrative completion method",
        tier="transferable",
        roles=["domain", "method"],
        index=9,
    )
    gate = _evaluate_paper_quality_gate(
        seed="Dream of the Red Chamber ending",
        domain="",
        papers=[paper],
        stage="test",
        paper_type="system",
    )
    assert gate["roleCoverage"]["counts"]["domain"] == 0
    assert gate["roleCoverage"]["counts"]["method"] == 1
```

- [ ] **Step 2: Run the new gate tests and verify RED**

Run the three tests directly. Expected: failures because the existing gate has
universal role requirements and treats retrieval roles as semantic proof.

- [ ] **Step 3: Implement paper-type role requirements**

Add to `evidence_relevance.py`:

```python
def role_requirements_for_paper_type(paper_type: str) -> dict[str, int]:
    normalized = (paper_type or "algorithm").lower()
    if normalized in {"survey", "position", "theory"}:
        return {"domainOrTask": 2, "method": 0, "evaluation": 0}
    if normalized in {"benchmark", "evaluation", "reproducibility"}:
        return {"domainOrTask": 2, "method": 0, "evaluation": 2}
    return {"domainOrTask": 2, "method": 1, "evaluation": 1}


def semantically_eligible_roles(tier: str, roles: Sequence[str]) -> tuple[str, ...]:
    if tier == EvidenceTier.DIRECT.value:
        return _unique(roles)
    if tier == EvidenceTier.TRANSFERABLE.value:
        return _unique(role for role in roles if role in {"method", "evaluation"})
    if tier == "unclassified":
        return _unique(roles)
    return ()


def evidence_tier_allows_dimension(tier: str, dimension: str) -> bool:
    if tier in {EvidenceTier.DIRECT.value, "unclassified"}:
        return True
    if tier == EvidenceTier.TRANSFERABLE.value:
        return dimension in {
            "method", "evaluation", "dataset", "metric", "baseline", "limitation"
        }
    return False
```

- [ ] **Step 4: Make the quality gate read both model shapes and never bypass alignment**

Change `_evaluate_paper_quality_gate` to accept `paper_type: str = "algorithm"`.
Resolve metadata consistently:

```python
roles = (
    (paper_roles or {}).get(paper_id, [])
    or getattr(paper, "retrievalRoles", None)
    or getattr(paper, "retrieval_roles", [])
    or []
)
tier = (
    getattr(paper, "evidenceTier", None)
    or getattr(paper, "evidence_tier", "direct")
)
eligible_roles = semantically_eligible_roles(tier, roles)
```

Always enforce `min_aligned` and `avg_top_score`; remove conditions that skip
those errors when role coverage passes. Pass `paper_type` at every call site.
Set the displayed/alignment score to `0.0` when `tier == "rejected"`, so a
provider score or generic word cannot increment `alignedPaperCount`.
Keep `_paper_type_coverage_requirements` as the structured-content requirement
and make its categories match `role_requirements_for_paper_type`.

When validating the LLM Evidence Coverage report, resolve every
`supportingPaperId` to its RawPaper tier and remove IDs for which
`evidence_tier_allows_dimension(tier, dimension)` is false. If a required
dimension has no allowed supporting IDs after verification, mark it missing.
Include `evidenceTier` in each paper summary sent to the reviewer so the LLM can
distinguish direct background/GAP evidence from transferable method evidence.

Implement the verifier in `service.py`:

```python
def _verify_coverage_dimension_support(
    *,
    dimension: str,
    supporting_paper_ids: List[str],
    paper_tiers: Dict[str, str],
) -> List[str]:
    return [
        paper_id
        for paper_id in dict.fromkeys(supporting_paper_ids)
        if paper_id in paper_tiers
        and evidence_tier_allows_dimension(paper_tiers[paper_id], dimension)
    ]
```

Add this regression:

```python
def test_transferable_paper_cannot_be_the_only_gap_support():
    verified = _verify_coverage_dimension_support(
        dimension="gap",
        supporting_paper_ids=["raw_transfer"],
        paper_tiers={"raw_transfer": "transferable"},
    )
    assert verified == []


def test_transferable_paper_can_support_method_dimension():
    verified = _verify_coverage_dimension_support(
        dimension="method",
        supporting_paper_ids=["raw_transfer"],
        paper_tiers={"raw_transfer": "transferable"},
    )
    assert verified == ["raw_transfer"]
```

- [ ] **Step 5: Run all evidence-gate regressions**

Run:

```powershell
pytest -q backend/tests/test_idea_evidence_gate.py backend/tests/test_idea_final_candidates.py -k "gate or role or evidence or survey or benchmark"
```

Expected: all selected tests pass; role counts and structured coverage agree by
paper type.

- [ ] **Step 6: Commit semantic role coverage**

```powershell
git add backend/app/modules/idea/evidence_relevance.py backend/app/modules/idea/service.py
git add -f backend/tests/test_idea_evidence_gate.py
git diff --cached --check
git commit -m "feat: align idea evidence roles with paper type"
```

## Task 6: Bound Deep Reading and Make Repair/Resume Idempotent

**Files:**
- Modify: `backend/app/modules/idea/service.py`
- Modify: `backend/app/storage/idea_storage.py`
- Modify: `backend/tests/test_idea_final_candidates.py`
- Modify: `backend/tests/test_idea_evidence_relevance.py`

- [ ] **Step 1: Write failing deep-read quota and resume tests**

Add tests asserting:

```python
def identity_set(papers):
    return {
        paper.doi
        or paper.arxivId
        or paper.semanticScholarId
        or paper.openalexId
        or paper.normalizedTitleHash
        for paper in papers
    }


monkeypatch.setenv("FAROS_IDEA_DEEP_READ_MAX_PAPERS", "6")
_, outputs, _ = service._step_novelty_check(session)
assert outputs["deepReadRequestedCount"] <= 6
assert len(outputs["selectedPaperIds"]) <= 6 + len(session.config.mustCiteList or [])

before = identity_set(service.raw_paper_storage.list_by_session(session.id))
service.resume_session(session.id)
resumed = service.run_pipeline(session.id)
after = identity_set(service.raw_paper_storage.list_by_session(session.id))
assert before <= after
assert len(after) == len(service.raw_paper_storage.list_by_session(session.id))
assert resumed.trace.totalSteps == len(resumed.trace.steps)
```

Also simulate a repair result matching an existing DOI and assert the existing
paper gains the new role/query instead of creating a second RawPaper.

- [ ] **Step 2: Run the quota/resume tests and verify RED**

Expected: selection still reaches 40 papers and repair skips existing papers
without merging provenance.

- [ ] **Step 3: Bound selection with a validated environment setting**

Add:

```python
def _deep_read_max_papers() -> int:
    try:
        configured = int(os.getenv("FAROS_IDEA_DEEP_READ_MAX_PAPERS", "24"))
    except ValueError:
        configured = 24
    return max(4, min(40, configured))
```

Set `num_select = min(_deep_read_max_papers(), max(4, len(raw_papers)))` and
reserve no more than one third of selected slots for transferable evidence.
Direct evidence fills the remaining slots first. Must-cite papers may exceed the
cap but must retain their actual evidence tier in telemetry.

- [ ] **Step 4: Upsert repair papers by stable identity**

Refactor `_persist_repair_search_results` to run the same
deduplicate/classify pipeline as initial search. Build an identity map for
existing RawPaper records. When a match exists:

```python
updated = existing.model_copy(update={
    "retrievalRoles": list(dict.fromkeys([*existing.retrievalRoles, *result.retrieval_roles])),
    "matchedQueries": list(dict.fromkeys([*existing.matchedQueries, *result.matched_queries])),
    "source": list(dict.fromkeys([*existing.source, *result.retrieval_sources])),
    "relevanceScore": max(existing.relevanceScore, result.relevance_score),
    "evidenceTier": better_evidence_tier(existing.evidenceTier, result.evidence_tier),
    "decisiveAnchors": list(dict.fromkeys([*existing.decisiveAnchors, *result.decisive_anchors])),
})
self.raw_paper_storage.update(updated)
```

Return `updatedRawPaperIds`, `duplicateMergeCount`, tier counts, and rejection
counts in the repair report. Do not deep-read rejected repair results.

- [ ] **Step 5: Keep resume trace counters internally consistent**

Add a `_record_step_result` helper used by both success and failure paths:

```python
def _record_step_result(trace: PipelineTrace, result: StepResult) -> None:
    trace.steps.append(result)
    trace.totalSteps = len(trace.steps)
    trace.successfulSteps = sum(step.status == "ok" for step in trace.steps)
    trace.failedSteps = sum(step.status == "failed" for step in trace.steps)
```

Repeated step names represent visible attempts, while artifacts and raw-paper
identities remain deduplicated.

- [ ] **Step 6: Run resume, cache, and completion tests**

Run:

```powershell
pytest -q backend/tests/test_idea_evidence_relevance.py backend/tests/test_idea_final_candidates.py backend/tests/test_structured_paper_cache.py -k "resume or repair or cache or final or complete or deep_read"
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit bounded, idempotent evidence processing**

```powershell
git add backend/app/modules/idea/service.py backend/app/storage/idea_storage.py backend/tests/test_idea_final_candidates.py
git add -f backend/tests/test_idea_evidence_relevance.py
git diff --cached --check
git commit -m "perf: bound idea deep reading and reuse evidence"
```

## Task 7: Extend Evaluation Telemetry and Run the Freeze Suite

**Files:**
- Modify: `backend/scripts/run_idea_plan_eval.py`
- Test: all Idea/Plan boundary tests listed below
- Runtime output: `backend/data/eval_runs/$env:FAROS_EVAL_RUN_ID/idea-closure-report.json`

- [ ] **Step 1: Add evaluator assertions and summaries**

Extend the per-seed result with:

```python
status_value = getattr(session.status, "value", str(session.status))
is_negative_stress = bool(spec.get("negativeStress", False))
literature_out = steps.get("literatureSearch", {}).get("outputs", {})
raw_gate = literature_out.get("paperQualityGate", {})
item["retrievalQuality"] = {
    "resultCountBeforeDedup": literature_out.get("resultCountBeforeDedup", 0),
    "uniqueResultCount": literature_out.get("uniqueResultCount", 0),
    "duplicateMergeCount": literature_out.get("duplicateMergeCount", 0),
    "evidenceTierCounts": literature_out.get("evidenceTierCounts", {}),
    "rejectionReasonCounts": literature_out.get("rejectionReasonCounts", {}),
    "retrievalRoleCounts": literature_out.get("retrievalRoleCounts", {}),
    "topicIntentProfile": literature_out.get("topicIntentProfile", {}),
}
item["freezeChecks"] = {
    "positiveSeedCompleted": is_negative_stress or status_value == "completed",
    "completionHasTwoIdeas": status_value != "completed"
        or len(session.finalCandidateIds or []) >= 2,
    "waitingStateIsRecoverable": status_value in {
        "completed", "awaiting_evidence", "awaiting_ideas"
    },
    "rawRoleCoverageEnabled": bool(
        (raw_gate.get("roleCoverage") or {}).get("enabled", False)
    ),
    "structuredRoleCoverageEnabled": (
        is_negative_stress and status_value == "awaiting_evidence"
    ) or bool(
        (evidence_gate.get("roleCoverage") or {}).get("enabled", False)
    ),
    "deepReadBounded": int(novelty_out.get("deepReadRequestedCount", 0) or 0)
        <= int(os.getenv("FAROS_IDEA_DEEP_READ_MAX_PAPERS", "24")),
}
```

Add the negative stress case to `SEEDS` without changing the three positive
technical cases:

```python
{
    "label": "D_cjk_named_work_negative_stress",
    "seed": "预测红楼梦可能结局",
    "domain": "",
    "paperType": "system",
    "maxCandidates": 3,
    "baselineSeconds": 975,
    "baselineNote": "negative pollution stress; awaiting_evidence is valid",
    "negativeStress": True,
},
```

Set the evaluator process exit code to nonzero only when a hard freeze check is
false. A valid `awaiting_evidence` result is not a failure for the CJK negative
stress seed.

- [ ] **Step 2: Run the complete local regression suite**

Run:

```powershell
pytest -q backend/tests/test_idea_final_candidates.py backend/tests/test_idea_evidence_gate.py backend/tests/test_idea_evidence_relevance.py backend/tests/test_search_service.py backend/tests/test_structured_paper_cache.py backend/tests/test_plan_package_llm_schema.py backend/tests/test_plan_package_quality_regressions.py backend/tests/test_plan_package_service_timeout.py backend/tests/test_llm_task_scheduler.py
python -m py_compile backend/app/models/idea.py backend/app/modules/idea/evidence_relevance.py backend/app/modules/idea/ideas_api.py backend/app/modules/idea/service.py backend/app/services/search_service.py backend/app/storage/idea_storage.py backend/scripts/run_idea_plan_eval.py
git diff --check
```

Expected: all tests pass, compilation succeeds, and diff check produces no
errors.

- [ ] **Step 3: Run the three technical black-box seeds**

Use a fresh run ID and output directory:

```powershell
$env:FAROS_EVAL_RUN_ID = "idea-closure-$(Get-Date -Format yyyyMMdd-HHmmss)"
$env:FAROS_EVAL_SUMMARY = "backend/data/eval_runs/$env:FAROS_EVAL_RUN_ID/summary.json"
$env:FAROS_IDEA_DEEP_READ_MAX_PAPERS = "24"
$env:FAROS_EVAL_ONLY = "A_llm_agents_scientific_discovery,B_citation_faithful_medical_rag,C_reliable_multi_agent_research_automation"
python backend/scripts/run_idea_plan_eval.py
Remove-Item Env:FAROS_EVAL_ONLY
```

Expected for every successful session:

- status is `completed`;
- at least two final candidate IDs;
- real LLM reviewer telemetry when the configured provider is available;
- no duplicate final candidates or unrequested application drift;
- no clearly unrelated paper among the top ten evidence records;
- role coverage enabled at raw and structured stages;
- deep-read request count at most 24, excluding visible must-cite overrides.

A provider outage may produce `awaiting_evidence`; record it as an environmental
result and do not weaken gates to force completion.

- [ ] **Step 4: Run the CJK negative pollution stress seed**

Run:

```powershell
$env:FAROS_EVAL_ONLY = "D_cjk_named_work_negative_stress"
$env:FAROS_EVAL_RUN_ID = "idea-cjk-stress-$(Get-Date -Format yyyyMMdd-HHmmss)"
$env:FAROS_EVAL_SUMMARY = "backend/data/eval_runs/$env:FAROS_EVAL_RUN_ID/summary.json"
python backend/scripts/run_idea_plan_eval.py
Remove-Item Env:FAROS_EVAL_ONLY
```

Expected:

- no clinical forecasting or chemical evaluation paper is classified direct or
  transferable;
- the session either produces two genuinely supported candidates or reaches
  `awaiting_evidence` before bulk deep reading;
- it never completes with zero or one candidate.

- [ ] **Step 5: Repeat one technical seed to verify stable cache identity**

Run only `B_citation_faithful_medical_rag` again:

```powershell
$env:FAROS_EVAL_ONLY = "B_citation_faithful_medical_rag"
$env:FAROS_EVAL_RUN_ID = "idea-cache-$(Get-Date -Format yyyyMMdd-HHmmss)"
$env:FAROS_EVAL_SUMMARY = "backend/data/eval_runs/$env:FAROS_EVAL_RUN_ID/summary.json"
python backend/scripts/run_idea_plan_eval.py
Remove-Item Env:FAROS_EVAL_ONLY
```

Expected: `structuredGlobalCacheHitCount > 0` when stable paper identities recur.

- [ ] **Step 6: Write the closure report from measured JSON**

Write `backend/data/eval_runs/$env:FAROS_EVAL_RUN_ID/idea-closure-report.json`
from the measured session summaries with this construction:

```python
hard_checks = [
    check
    for seed in summary["seeds"]
    for check in seed.get("freezeChecks", {}).values()
]
report = {
    "decision": "freeze" if hard_checks and all(hard_checks) else "continue_closure",
    "technicalSeeds": [
        {
            "label": seed["label"],
            "sessionId": seed.get("sessionId"),
            "status": seed.get("status"),
            "finalCandidateIds": seed.get("finalCandidateIds", []),
            "performance": seed.get("performance", {}),
            "retrievalQuality": seed.get("retrievalQuality", {}),
            "freezeChecks": seed.get("freezeChecks", {}),
        }
        for seed in summary["seeds"]
    ],
    "externalProviderIncidents": [
        seed.get("sessionError")
        for seed in summary["seeds"]
        if seed.get("sessionError")
        and "provider" in seed.get("sessionError", "").lower()
    ],
    "remainingRisks": [
        f"{seed['label']}:{name}"
        for seed in summary["seeds"]
        for name, passed in seed.get("freezeChecks", {}).items()
        if not passed
    ],
}
```

Set `decision` to `freeze` only when every criterion in design section 14 is
satisfied. Otherwise use `continue_closure` and list the failing criterion with
its session ID and measured evidence. Generated evaluation data stays ignored.

- [ ] **Step 7: Commit evaluator telemetry and the final code state**

```powershell
git add backend/scripts/run_idea_plan_eval.py
git diff --cached --check
git commit -m "test: add idea closure acceptance telemetry"
git status --short --branch
```

Expected: source and test changes are committed; ignored runtime evaluation
artifacts are not added. Any pre-existing unrelated user changes remain visible
and untouched.

## Final Completion Gate

Do not declare Idea frozen until all conditions below are evidenced by commands
or black-box JSON:

- All focused tests pass.
- Python compilation, frontend verification for the waiting UI, and
  `git diff --check` pass.
- Completed technical sessions expose at least two approved, diverse ideas.
- Weak evidence uses `awaiting_evidence`; weak candidates use `awaiting_ideas`.
- Initial and structured role coverage are enabled when role queries exist.
- Generic provider hits cannot enter the evidence pool.
- Duplicate identities merge roles and matched queries.
- Evidence-rejected sessions stop before bulk DeepReader work.
- Repeated stable identities produce observable StructuredPaper cache hits.
- No other team module is modified.
