import json

import app.modules.idea.service as idea_service_module
from app.models.idea import CandidateGraphEvidence, IdeaCandidate
from app.storage.idea_storage import LLMTaskCacheStorage


def _candidate() -> IdeaCandidate:
    return IdeaCandidate(
        id="cand_cache",
        sessionId="idea_cache",
        title="Citation-faithful RAG verifier",
        problem="High-risk RAG systems need citation-faithful verification.",
        hypothesisStatement="Verifier-guided abstention improves citation faithfulness.",
        keyInsight="Bind answer claims to retrieved evidence before allowing citations.",
        proposedMethod="Train a lightweight verifier over answer, citation, and retrieved passage triples.",
        expectedOutcome="Fewer unsupported citations at similar answer utility.",
        novelty=8.0,
        feasibility=8.0,
        impact=8.0,
        clarity=8.0,
        risk=8.0,
        alignment=8.0,
        referenceSupport=8.0,
        experimentSpecificity=8.0,
    )


def _service(tmp_path) -> idea_service_module.IdeaGenerationService:
    service = object.__new__(idea_service_module.IdeaGenerationService)
    service.llm_task_cache_storage = LLMTaskCacheStorage(data_dir=str(tmp_path))
    return service


def test_llm_idea_reviewer_cache_reuses_identical_inputs(tmp_path, monkeypatch):
    service = _service(tmp_path)
    call_count = {"value": 0}

    class _FakeResponse:
        text = json.dumps({
            "score": 8.2,
            "pass": True,
            "blockingIssues": [],
            "repairInstructions": [],
            "evidenceRefs": ["raw_1"],
            "confidence": 0.9,
            "summary": "Grounded and specific.",
        })
        latency_ms = 123

    class _FakeClient:
        def chat(self, *args, **kwargs):
            call_count["value"] += 1
            return _FakeResponse()

    monkeypatch.setattr(idea_service_module, "get_provider_client", lambda provider_name: _FakeClient())
    spec = idea_service_module.IDEA_REVIEWER_SPECS[0]
    candidate = _candidate()
    evidence = CandidateGraphEvidence(candidateId=candidate.id, supportingPaperIds=["raw_1"])

    first = service._run_llm_idea_reviewer(
        spec=spec,
        candidate=candidate,
        evidence=evidence,
        comparisons=[],
        critique=None,
        seed_query="citation faithful RAG",
        provider_name="fake",
        model="model-a",
        literature_context="raw_1 supports citation faithfulness.",
        allowed_evidence_refs=["raw_1"],
    )
    second = service._run_llm_idea_reviewer(
        spec=spec,
        candidate=candidate,
        evidence=evidence,
        comparisons=[],
        critique=None,
        seed_query="citation faithful RAG",
        provider_name="fake",
        model="model-a",
        literature_context="raw_1 supports citation faithfulness.",
        allowed_evidence_refs=["raw_1"],
    )

    assert call_count["value"] == 1
    assert first["cacheHit"] is False
    assert second["cacheHit"] is True
    assert second["score"] == first["score"]


def test_llm_idea_reviewer_cache_misses_when_evidence_context_changes(tmp_path, monkeypatch):
    service = _service(tmp_path)
    call_count = {"value": 0}

    class _FakeResponse:
        latency_ms = 50

        @property
        def text(self):
            call_count["value"] += 1
            return json.dumps({
                "score": 7.0 + call_count["value"],
                "pass": True,
                "blockingIssues": [],
                "repairInstructions": [],
                "evidenceRefs": [],
                "confidence": 0.8,
                "summary": "Reviewed.",
            })

    class _FakeClient:
        def chat(self, *args, **kwargs):
            return _FakeResponse()

    monkeypatch.setattr(idea_service_module, "get_provider_client", lambda provider_name: _FakeClient())
    spec = idea_service_module.IDEA_REVIEWER_SPECS[0]
    candidate = _candidate()

    first = service._run_llm_idea_reviewer(
        spec=spec,
        candidate=candidate,
        evidence=CandidateGraphEvidence(candidateId=candidate.id, supportingPaperIds=["raw_1"]),
        comparisons=[],
        critique=None,
        seed_query="citation faithful RAG",
        provider_name="fake",
        model="model-a",
        literature_context="raw_1 supports citation faithfulness.",
        allowed_evidence_refs=["raw_1"],
    )
    second = service._run_llm_idea_reviewer(
        spec=spec,
        candidate=candidate,
        evidence=CandidateGraphEvidence(candidateId=candidate.id, supportingPaperIds=["raw_2"]),
        comparisons=[],
        critique=None,
        seed_query="citation faithful RAG",
        provider_name="fake",
        model="model-a",
        literature_context="raw_2 is a different evidence context.",
        allowed_evidence_refs=["raw_2"],
    )

    assert call_count["value"] == 2
    assert first["score"] != second["score"]


def test_llm_idea_reviewer_runs_provider_call_through_scheduler(monkeypatch):
    service = object.__new__(idea_service_module.IdeaGenerationService)
    service.llm_task_cache_storage = None
    scheduler_calls = []

    class _FakeScheduler:
        def run(self, task_type, fn):
            scheduler_calls.append(task_type)
            return fn()

    class _FakeResponse:
        text = json.dumps({
            "score": 8.0,
            "pass": True,
            "blockingIssues": [],
            "repairInstructions": [],
            "evidenceRefs": ["raw_1"],
            "confidence": 0.8,
            "summary": "Reviewed through scheduler.",
        })
        latency_ms = 10

    class _FakeClient:
        def chat(self, *args, **kwargs):
            return _FakeResponse()

    monkeypatch.setattr(idea_service_module, "get_provider_client", lambda provider_name: _FakeClient())
    service.llm_task_scheduler = _FakeScheduler()

    report = service._run_llm_idea_reviewer(
        spec=idea_service_module.IDEA_REVIEWER_SPECS[0],
        candidate=_candidate(),
        evidence=CandidateGraphEvidence(candidateId="cand_cache", supportingPaperIds=["raw_1"]),
        comparisons=[],
        critique=None,
        seed_query="citation faithful RAG",
        provider_name="fake",
        model="model-a",
        literature_context="raw_1 supports citation faithfulness.",
        allowed_evidence_refs=["raw_1"],
    )

    assert report["passed"] is True
    assert scheduler_calls == ["idea_reviewer:IdeaEvidenceReviewer"]


def test_merge_idea_reviewer_report_preserves_cache_diagnostics():
    service = object.__new__(idea_service_module.IdeaGenerationService)
    merged = service._merge_idea_reviewer_reports(
        spec=idea_service_module.IDEA_REVIEWER_SPECS[0],
        rule_report={
            "reviewer": "IdeaEvidenceReviewer",
            "mode": "rule",
            "score": 8.0,
            "blockingIssues": [],
            "repairInstructions": [],
            "evidenceRefs": ["raw_1"],
        },
        llm_report={
            "reviewer": "IdeaEvidenceReviewer",
            "mode": "llm",
            "score": 8.0,
            "pass": True,
            "blockingIssues": [],
            "repairInstructions": [],
            "evidenceRefs": ["raw_1"],
            "confidence": 0.9,
            "summary": "Cached review.",
            "llmLatencyMs": 123,
            "cacheHit": True,
            "cacheKey": "cache-1",
        },
        allowed_evidence_refs=["raw_1"],
    )

    assert merged["cacheHit"] is True
    assert merged["cacheKey"] == "cache-1"
