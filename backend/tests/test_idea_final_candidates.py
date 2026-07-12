import json
import threading
import time
from types import SimpleNamespace

import pytest

from app.models.idea import (
    BFTSConfig,
    BFTSHandoff,
    CandidateGraphEvidence,
    IdeaCandidate,
    IdeaCritique,
    IdeaSession,
    IdeaSessionConfig,
    PriorWorkComparison,
    RawPaper,
    ReasoningPathSeed,
    StepResult,
    StructuredPaper,
    WorkflowTrace,
)
import app.modules.idea.service as idea_service_module
from app.modules.idea.service import IdeaGenerationService
from app.services.search_service import SearchResult
from app.storage.idea_storage import (
    CandidateStorage,
    IdeaSessionStorage,
    LiteratureGraphStorage,
    LiteratureStorage,
    RawPaperStorage,
    generate_candidate_id,
)


def _candidate(candidate_id: str, session_id: str, *, title: str, novelty: float = 8.0) -> IdeaCandidate:
    return IdeaCandidate(
        id=candidate_id,
        sessionId=session_id,
        title=title,
        problem=f"Problem for {title}",
        hypothesisStatement=f"Hypothesis for {title}",
        keyInsight=f"Insight for {title}",
        proposedMethod=f"Method for {title}",
        expectedOutcome=f"Outcome for {title}",
        novelty=novelty,
        feasibility=8.0,
        impact=8.0,
        clarity=8.0,
        risk=8.0,
        alignment=8.0,
        referenceSupport=8.0,
        experimentSpecificity=8.0,
    )


def _tag_direction(candidate: IdeaCandidate, direction_type: str, direction_id: str | None = None) -> IdeaCandidate:
    candidate.draftPlan = candidate.draftPlan or idea_service_module.DraftPlan(
        researchQuestion=candidate.problem,
        hypothesis=candidate.hypothesisStatement,
        methodology=candidate.proposedMethod,
        expectedOutcomes=[candidate.expectedOutcome],
    )
    direction_id = direction_id or f"dir-{direction_type}"
    candidate.draftPlan.tags = [
        f"direction:{direction_id}",
        f"directionType:{direction_type}",
    ]
    return candidate


def _service(tmp_path) -> IdeaGenerationService:
    service = object.__new__(IdeaGenerationService)
    service.session_storage = IdeaSessionStorage(data_dir=str(tmp_path))
    service.candidate_storage = CandidateStorage(data_dir=str(tmp_path))
    return service


def test_cjk_expand_query_backfills_role_queries_when_primary_output_omits_english(monkeypatch):
    service = object.__new__(IdeaGenerationService)
    session = IdeaSession(
        id="idea_cjk_query_roles",
        config=IdeaSessionConfig(
            seedQuery="\u9884\u6d4b\u7ea2\u697c\u68a6\u53ef\u80fd\u7ed3\u5c40",
            providerName="fake",
            model="fake-model",
            paperType="system",
        ),
    )

    class FakeClient:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(
                    text=json.dumps({
                        "refinedQuestion": "\u5982\u4f55\u8ba1\u7b97\u91cd\u5efa\u7ea2\u697c\u68a6\u7ed3\u5c40\uff1f",
                        "searchQueries": ["\u7ea2\u697c\u68a6\u7ed3\u5c40\u7814\u7a76"],
                        "keyConcepts": ["\u53d9\u4e8b\u95ed\u5408"],
                    }),
                    latency_ms=10,
                )
            return SimpleNamespace(
                text=json.dumps({
                    "domainQueries": ["Dream of the Red Chamber ending studies"],
                    "taskQueries": ["Hongloumeng narrative closure reconstruction"],
                    "methodQueries": ["computational literary narrative completion"],
                    "evaluationQueries": ["narrative coherence character consistency evaluation"],
                }),
                latency_ms=8,
            )

    client = FakeClient()
    monkeypatch.setattr(idea_service_module, "get_provider_client", lambda _provider: client)

    _, outputs, _ = service._step_expand_query(session)

    assert client.calls == 2
    assert outputs["translationStatus"] == "fallback"
    assert outputs["searchQueriesByRole"]["domain"] == [
        "Dream of the Red Chamber ending studies"
    ]
    assert outputs["searchQueriesByRole"]["method"] == [
        "computational literary narrative completion"
    ]
    assert outputs["englishSearchQueries"][0] == "Dream of the Red Chamber ending studies"


def test_cjk_expand_query_pauses_when_translation_fallback_is_empty(monkeypatch):
    service = object.__new__(IdeaGenerationService)
    session = IdeaSession(
        id="idea_cjk_translation_missing",
        config=IdeaSessionConfig(
            seedQuery="\u9884\u6d4b\u7ea2\u697c\u68a6\u53ef\u80fd\u7ed3\u5c40",
            providerName="fake",
            model="fake-model",
        ),
    )

    class FakeClient:
        def chat(self, messages, **kwargs):
            return SimpleNamespace(
                text=json.dumps({"searchQueries": ["\u7ea2\u697c\u68a6\u7ed3\u5c40\u7814\u7a76"]}),
                latency_ms=5,
            )

    monkeypatch.setattr(
        idea_service_module,
        "get_provider_client",
        lambda _provider: FakeClient(),
    )

    try:
        service._step_expand_query(session)
    except idea_service_module.RecoverableIdeaError as exc:
        assert exc.waiting_status == idea_service_module.IdeaSessionStatus.AWAITING_EVIDENCE
        assert exc.resume_from == "expandQuery"
    else:
        raise AssertionError("Missing CJK translation must pause the session")


def test_cjk_repair_queries_keep_english_core_topic_anchor(tmp_path):
    service = _service(tmp_path)
    session = IdeaSession(
        id="idea_cjk_repair_anchor",
        config=IdeaSessionConfig(seedQuery="\u9884\u6d4b\u7ea2\u697c\u68a6\u53ef\u80fd\u7ed3\u5c40"),
        trace=idea_service_module.WorkflowTrace(
            sessionId="idea_cjk_repair_anchor",
            startedAt=idea_service_module._utcnow(),
            steps=[
                idea_service_module.StepResult(
                    name="expandQuery",
                    status="ok",
                    outputs={
                        "englishSearchQueries": ["Dream of the Red Chamber ending studies"],
                        "searchQueriesByRole": {
                            "domain": ["Dream of the Red Chamber ending studies"],
                            "task": ["Hongloumeng narrative closure reconstruction"],
                            "method": [],
                            "evaluation": [],
                        },
                    },
                    startedAt=idea_service_module._utcnow(),
                    endedAt=idea_service_module._utcnow(),
                    durationSeconds=0.0,
                )
            ],
        ),
    )

    anchored = service._anchor_repair_queries(
        session,
        [
            "\u9884\u6d4b\u7ea2\u697c\u68a6\u53ef\u80fd\u7ed3\u5c40 evaluation evidence",
            "\u9884\u6d4b\u7ea2\u697c\u68a6\u53ef\u80fd\u7ed3\u5c40 method evidence",
        ],
    )

    assert anchored == [
        "Dream of the Red Chamber ending studies evaluation evidence",
        "Dream of the Red Chamber ending studies method evidence",
    ]


def test_core_search_queries_exclude_method_and_evaluation_only_roles(tmp_path):
    service = _service(tmp_path)
    session = IdeaSession(
        id="idea_role_core_queries",
        config=IdeaSessionConfig(seedQuery="\u9884\u6d4b\u7ea2\u697c\u68a6\u53ef\u80fd\u7ed3\u5c40"),
        trace=idea_service_module.WorkflowTrace(
            sessionId="idea_role_core_queries",
            startedAt=idea_service_module._utcnow(),
            steps=[
                idea_service_module.StepResult(
                    name="expandQuery",
                    status="ok",
                    outputs={
                        "englishSearchQueries": [
                            "Dream of the Red Chamber ending studies",
                            "Hongloumeng narrative closure reconstruction",
                            "computational literary narrative completion",
                            "narrative coherence evaluation",
                        ],
                        "searchQueriesByRole": {
                            "domain": ["Dream of the Red Chamber ending studies"],
                            "task": ["Hongloumeng narrative closure reconstruction"],
                            "method": ["computational literary narrative completion"],
                            "evaluation": ["narrative coherence evaluation"],
                        },
                    },
                    startedAt=idea_service_module._utcnow(),
                    endedAt=idea_service_module._utcnow(),
                    durationSeconds=0.0,
                )
            ],
        ),
    )

    assert service._core_search_queries(session) == [
        "Dream of the Red Chamber ending studies",
        "Hongloumeng narrative closure reconstruction",
    ]


def test_literature_search_rejects_generic_cross_domain_results(monkeypatch, tmp_path):
    service = object.__new__(IdeaGenerationService)
    service.raw_paper_storage = RawPaperStorage(data_dir=str(tmp_path))
    service.literature_storage = LiteratureStorage(data_dir=str(tmp_path))
    service.graph_storage = LiteratureGraphStorage(data_dir=str(tmp_path))
    service.graph_builder = idea_service_module.LiteratureGraphBuilder()
    session = IdeaSession(
        id="idea_literature_tiers",
        config=IdeaSessionConfig(
            seedQuery="预测红楼梦可能结局",
            paperType="system",
            maxPapers=24,
        ),
        trace=WorkflowTrace(
            sessionId="idea_literature_tiers",
            startedAt=idea_service_module._utcnow(),
            steps=[
                StepResult(
                    name="expandQuery",
                    status="ok",
                    outputs={
                        "englishSearchQueries": [
                            "Literary analysis of Dream of the Red Chamber",
                            "Computational prediction of Dream of the Red Chamber endings",
                        ],
                        "searchQueriesByRole": {
                            "domain": ["Literary analysis of 'Dream of the Red Chamber'"],
                            "task": ["Computational prediction of endings for 'Dream of the Red Chamber'"],
                            "method": ["Narrative completion using character constraints"],
                            "evaluation": ["Narrative coherence and character consistency evaluation"],
                        },
                    },
                    startedAt=idea_service_module._utcnow(),
                    endedAt=idea_service_module._utcnow(),
                    durationSeconds=0.0,
                )
            ],
        ),
    )

    papers = [
        SearchResult(
            title="Dream of the Red Chamber authorship and unfinished ending",
            authors=[],
            abstract="Computational evidence about Hongloumeng narrative closure.",
            year=2024,
            venue="test",
            url="",
            doi="10.1000/direct-1",
            arxiv_id=None,
            citation_count=1,
            source="openalex",
            relevance_score=0.9,
        ),
        SearchResult(
            title="Narrative completion for unfinished novels",
            authors=[],
            abstract="Computational character constraints reconstruct plausible endings.",
            year=2023,
            venue="test",
            url="",
            doi="10.1000/method-1",
            arxiv_id=None,
            citation_count=1,
            source="openalex",
            relevance_score=0.9,
        ),
        SearchResult(
            title="Evaluating narrative coherence and character consistency",
            authors=[],
            abstract="Computational evaluation metrics for generated novel endings.",
            year=2023,
            venue="test",
            url="",
            doi="10.1000/eval-1",
            arxiv_id=None,
            citation_count=1,
            source="openalex",
            relevance_score=0.9,
        ),
        SearchResult(
            title="Hongloumeng character relationships and narrative structure",
            authors=[],
            abstract="Dream of the Red Chamber characters constrain narrative outcomes.",
            year=2022,
            venue="test",
            url="",
            doi="10.1000/direct-2",
            arxiv_id=None,
            citation_count=1,
            source="openalex",
            relevance_score=0.9,
        ),
        SearchResult(
            title="Clinical time-series forecasting and analysis",
            authors=[],
            abstract="A web platform predicts patient outcomes with configurable models.",
            year=2025,
            venue="test",
            url="",
            doi="10.1000/clinical",
            arxiv_id=None,
            citation_count=0,
            source="openalex",
            relevance_score=0.9,
        ),
        SearchResult(
            title="ChemEval: A chemical evaluation for language models",
            authors=[],
            abstract="A multi-level benchmark for chemistry question answering.",
            year=2025,
            venue="test",
            url="",
            doi="10.1000/chem",
            arxiv_id=None,
            citation_count=0,
            source="openalex",
            relevance_score=0.9,
        ),
    ]

    class FakeSearchService:
        def search(self, _query, limit=10):
            return [paper for paper in papers[:limit]]

    monkeypatch.setattr(
        idea_service_module,
        "get_search_service",
        lambda: FakeSearchService(),
    )

    _, outputs, _ = service._step_literature_search(session)
    stored = service.raw_paper_storage.list_by_session(session.id)

    assert all(paper.evidenceTier in {"direct", "transferable"} for paper in stored)
    assert not any("Clinical time-series" in paper.title for paper in stored)
    assert not any("ChemEval" in paper.title for paper in stored)
    assert outputs["evidenceTierCounts"]["rejected"] >= 2
    assert outputs["duplicateMergeCount"] > 0
    assert outputs["topicIntentProfile"]["coreAnchors"]


def test_literature_search_pauses_weak_pool_before_deep_read(monkeypatch, tmp_path):
    service = object.__new__(IdeaGenerationService)
    service.raw_paper_storage = RawPaperStorage(data_dir=str(tmp_path))
    service.literature_storage = LiteratureStorage(data_dir=str(tmp_path))
    service.graph_storage = LiteratureGraphStorage(data_dir=str(tmp_path))
    service.graph_builder = idea_service_module.LiteratureGraphBuilder()
    session = IdeaSession(
        id="idea_literature_waiting",
        config=IdeaSessionConfig(seedQuery="预测红楼梦可能结局", maxPapers=20),
        trace=WorkflowTrace(
            sessionId="idea_literature_waiting",
            startedAt=idea_service_module._utcnow(),
            steps=[
                StepResult(
                    name="expandQuery",
                    status="ok",
                    outputs={
                        "englishSearchQueries": ["Dream of the Red Chamber endings"],
                        "searchQueriesByRole": {
                            "domain": ["Literary analysis of 'Dream of the Red Chamber'"],
                            "task": ["Computational prediction of endings for 'Dream of the Red Chamber'"],
                            "method": ["Narrative completion using character constraints"],
                            "evaluation": ["Narrative coherence evaluation"],
                        },
                    },
                    startedAt=idea_service_module._utcnow(),
                    endedAt=idea_service_module._utcnow(),
                    durationSeconds=0.0,
                )
            ],
        ),
    )

    distractors = [
        SearchResult(
            title="Clinical time-series forecasting and analysis",
            authors=[],
            abstract="Predicts patient outcomes.",
            year=2025,
            venue="test",
            url="",
            doi="10.1000/clinical-only",
            arxiv_id=None,
            citation_count=0,
            source="openalex",
            relevance_score=0.9,
        ),
        SearchResult(
            title="ChemEval chemical evaluation",
            authors=[],
            abstract="Evaluates chemistry language models.",
            year=2025,
            venue="test",
            url="",
            doi="10.1000/chem-only",
            arxiv_id=None,
            citation_count=0,
            source="openalex",
            relevance_score=0.9,
        ),
    ]

    class FakeSearchService:
        def search(self, _query, limit=10):
            return distractors[:limit]

    monkeypatch.setattr(
        idea_service_module,
        "get_search_service",
        lambda: FakeSearchService(),
    )

    with pytest.raises(idea_service_module.RecoverableIdeaError) as exc_info:
        service._step_literature_search(session)

    assert exc_info.value.resume_from == "literatureSearch"
    assert service.raw_paper_storage.list_by_session(session.id) == []


def test_deep_read_limit_is_bounded(monkeypatch):
    monkeypatch.setenv("FAROS_IDEA_DEEP_READ_MAX_PAPERS", "6")
    assert idea_service_module._deep_read_max_papers() == 6

    monkeypatch.setenv("FAROS_IDEA_DEEP_READ_MAX_PAPERS", "1")
    assert idea_service_module._deep_read_max_papers() == 4

    monkeypatch.setenv("FAROS_IDEA_DEEP_READ_MAX_PAPERS", "100")
    assert idea_service_module._deep_read_max_papers() == 40

    monkeypatch.setenv("FAROS_IDEA_DEEP_READ_MAX_PAPERS", "invalid")
    assert idea_service_module._deep_read_max_papers() == 24


def test_deep_read_selection_limits_transferable_quota_and_keeps_must_cite():
    papers = {
        **{
            f"raw_direct_{index}": RawPaper(
                id=f"raw_direct_{index}",
                sessionId="idea_quota",
                title=f"Direct paper {index}",
                evidenceTier="direct",
            )
            for index in range(5)
        },
        **{
            f"raw_transfer_{index}": RawPaper(
                id=f"raw_transfer_{index}",
                sessionId="idea_quota",
                title=f"Transferable paper {index}",
                evidenceTier="transferable",
            )
            for index in range(5)
        },
        "raw_must": RawPaper(
            id="raw_must",
            sessionId="idea_quota",
            title="Explicit must cite",
            evidenceTier="rejected",
            mustCiteOverride=True,
        ),
    }
    selected = [
        "raw_transfer_0",
        "raw_direct_0",
        "raw_transfer_1",
        "raw_direct_1",
        "raw_transfer_2",
        "raw_direct_2",
        "raw_transfer_3",
        "raw_direct_3",
        "raw_transfer_4",
        "raw_direct_4",
        "raw_must",
    ]

    limited = idea_service_module._limit_deep_read_selection(
        selected,
        papers,
        limit=6,
    )

    assert len([paper_id for paper_id in limited if "transfer" in paper_id]) == 2
    assert len([paper_id for paper_id in limited if "direct" in paper_id]) == 4
    assert "raw_must" in limited


def test_repair_search_merges_existing_raw_paper_provenance(tmp_path):
    service = object.__new__(IdeaGenerationService)
    service.raw_paper_storage = RawPaperStorage(data_dir=str(tmp_path))
    service.literature_storage = LiteratureStorage(data_dir=str(tmp_path))
    service.graph_storage = LiteratureGraphStorage(data_dir=str(tmp_path))
    service.graph_builder = idea_service_module.LiteratureGraphBuilder()
    session = IdeaSession(
        id="idea_repair_upsert",
        config=IdeaSessionConfig(
            seedQuery="citation-faithful medical RAG",
            domain="medical QA",
            paperType="system",
        ),
    )
    existing = RawPaper(
        id="raw_existing_repair",
        sessionId=session.id,
        title="Citation-Enforced Medical RAG",
        abstract="Citation faithful retrieval augmented generation.",
        doi="10.1000/repair-rag",
        source=["semantic_scholar"],
        retrievalRoles=["domain"],
        matchedQueries=["citation faithful medical RAG"],
        evidenceTier="direct",
        decisiveAnchors=["citation", "medical", "rag"],
        normalizedTitleHash=idea_service_module._compute_title_hash(
            "Citation-Enforced Medical RAG"
        ),
        relevanceScore=0.7,
    )
    service.raw_paper_storage.create(existing)
    initial_graph = service.graph_builder.build_graph_v0(
        session_id=session.id,
        raw_papers=[existing],
    )
    service.graph_storage.create(initial_graph)
    duplicate = SearchResult(
        title="Citation-Enforced Medical RAG",
        authors=[],
        abstract="A richer abstract with refusal evaluation and evidence traceability.",
        year=2025,
        venue="test",
        url="",
        doi="10.1000/repair-rag",
        arxiv_id=None,
        citation_count=4,
        source="openalex",
        relevance_score=0.9,
        retrieval_roles=["method", "evaluation", "repair"],
        matched_queries=["citation faithful medical RAG method evaluation"],
    )

    report = service._persist_repair_search_results(
        session,
        [duplicate],
        search_queries=["citation faithful medical RAG method evaluation"],
    )
    loaded = service.raw_paper_storage.get(existing.id)

    assert loaded is not None
    assert service.raw_paper_storage.list_by_session(session.id) == [loaded]
    assert loaded.retrievalRoles == ["domain", "method", "evaluation", "repair"]
    assert loaded.matchedQueries == [
        "citation faithful medical RAG",
        "citation faithful medical RAG method evaluation",
    ]
    assert loaded.source == ["semantic_scholar", "openalex"]
    assert loaded.relevanceScore >= existing.relevanceScore
    assert report["updatedRawPaperIds"] == [existing.id]
    assert report["duplicateMergeCount"] == 1
    assert len(list(service.graph_storage.base_path.glob("lg_*.json"))) == 1
    assert service.graph_storage.get_by_session(session.id).id == initial_graph.id


def test_repair_results_keep_query_and_missing_dimension_role():
    result = SearchResult(
        title="Citation faithful RAG verifier",
        authors=[],
        abstract="",
        year=2025,
        venue="test",
        url="",
        doi=None,
        arxiv_id=None,
        citation_count=0,
        source="openalex",
    )

    idea_service_module._tag_repair_results(
        [result],
        "citation faithful RAG method evidence",
    )

    assert result.retrieval_roles == ["repair", "method"]
    assert result.matched_queries == ["citation faithful RAG method evidence"]


def test_trace_counters_are_recomputed_from_recorded_attempts():
    trace = WorkflowTrace(
        sessionId="idea_trace_attempts",
        startedAt=idea_service_module._utcnow(),
    )
    ok = StepResult(
        name="literatureSearch",
        status="ok",
        startedAt=idea_service_module._utcnow(),
        endedAt=idea_service_module._utcnow(),
        durationSeconds=0.0,
    )
    failed = StepResult(
        name="evidenceGate",
        status="failed",
        startedAt=idea_service_module._utcnow(),
        endedAt=idea_service_module._utcnow(),
        durationSeconds=0.0,
        error="insufficient evidence",
    )

    idea_service_module._record_step_result(trace, ok)
    idea_service_module._record_step_result(trace, failed)

    assert trace.totalSteps == 2
    assert trace.successfulSteps == 1
    assert trace.failedSteps == 1
    assert trace.steps == [ok, failed]


def test_get_candidates_defaults_to_final_candidate_ids(tmp_path):
    service = _service(tmp_path)
    session_id = "idea_final_view"
    service.session_storage.create(
        IdeaSession(
            id=session_id,
            config=IdeaSessionConfig(seedQuery="citation faithful RAG"),
            candidateIds=["cand_hidden", "cand_final_2", "cand_final_1"],
            finalCandidateIds=["cand_final_1", "cand_final_2"],
            hiddenCandidateIds=["cand_hidden"],
        )
    )
    service.candidate_storage.create(_candidate("cand_hidden", session_id, title="Hidden", novelty=9.0))
    service.candidate_storage.create(_candidate("cand_final_1", session_id, title="Final one", novelty=7.8))
    service.candidate_storage.create(_candidate("cand_final_2", session_id, title="Final two", novelty=8.2))

    candidates = service.get_candidates(session_id)

    assert [candidate.id for candidate in candidates] == ["cand_final_1", "cand_final_2"]


def test_get_candidates_debug_view_returns_all_ranked_candidates(tmp_path):
    service = _service(tmp_path)
    session_id = "idea_debug_view"
    service.session_storage.create(
        IdeaSession(
            id=session_id,
            config=IdeaSessionConfig(seedQuery="citation faithful RAG"),
            candidateIds=["cand_low", "cand_high"],
            finalCandidateIds=["cand_high"],
            hiddenCandidateIds=["cand_low"],
        )
    )
    service.candidate_storage.create(_candidate("cand_low", session_id, title="Low", novelty=5.0))
    service.candidate_storage.create(_candidate("cand_high", session_id, title="High", novelty=9.0))

    candidates = service.get_candidates(session_id, view="debug")

    assert [candidate.id for candidate in candidates] == ["cand_high", "cand_low"]


def test_select_final_candidates_prefers_reviewed_diverse_candidates(tmp_path):
    service = _service(tmp_path)
    strong_a = _candidate(
        "cand_a",
        "idea_select",
        title="Citation-faithful refusal verifier for high-risk RAG",
        novelty=8.7,
    )
    strong_b = _candidate(
        "cand_b",
        "idea_select",
        title="Evidence audit trail benchmark for high-risk RAG",
        novelty=8.0,
    )
    duplicate_a = _candidate(
        "cand_dup",
        "idea_select",
        title="Citation-faithful refusal verifier for high-risk RAG",
        novelty=9.0,
    )
    weak = _candidate(
        "cand_weak",
        "idea_select",
        title="Generic RAG prompt template",
        novelty=5.0,
    )
    gate_reports = {
        "cand_a": {"passed": True, "scoreAfterGate": 8.3, "blockingIssues": [], "warnings": []},
        "cand_b": {"passed": True, "scoreAfterGate": 8.0, "blockingIssues": [], "warnings": []},
        "cand_dup": {"passed": True, "scoreAfterGate": 8.4, "blockingIssues": [], "warnings": []},
        "cand_weak": {"passed": False, "scoreAfterGate": 4.8, "blockingIssues": ["No evidence"], "warnings": []},
    }

    result = service._select_final_candidates(
        [duplicate_a, strong_a, strong_b, weak],
        gate_reports,
        max_count=3,
    )

    assert result["finalCandidateIds"] == ["cand_dup", "cand_b"]
    assert result["hiddenCandidateIds"] == ["cand_a"]
    assert result["rejectedCandidateIds"] == ["cand_weak"]


def test_select_final_candidates_prefers_different_direction_types(tmp_path):
    service = _service(tmp_path)
    method_top = _tag_direction(_candidate(
        "cand_method_top",
        "idea_select_direction",
        title="Citation faithful RAG verifier",
        novelty=9.0,
    ), "method")
    method_second = _tag_direction(_candidate(
        "cand_method_second",
        "idea_select_direction",
        title="Citation faithful RAG verifier with abstention",
        novelty=8.8,
    ), "method")
    benchmark = _tag_direction(_candidate(
        "cand_benchmark",
        "idea_select_direction",
        title="Citation faithful RAG benchmark for high-risk QA",
        novelty=8.0,
    ), "benchmark")
    gate_reports = {
        candidate.id: {
            "passed": True,
            "scoreAfterGate": candidate.overallScore,
            "blockingIssues": [],
            "warnings": [],
        }
        for candidate in [method_top, method_second, benchmark]
    }

    result = service._select_final_candidates(
        [method_top, method_second, benchmark],
        gate_reports,
        max_count=3,
    )

    assert result["finalCandidateIds"] == ["cand_method_top", "cand_benchmark"]
    assert result["hiddenCandidateIds"] == ["cand_method_second"]
    assert result["summary"]["finalDirectionTypes"] == ["method", "benchmark"]
    assert not result["summary"]["warnings"]


def test_select_final_candidates_warns_when_only_one_direction_passes(tmp_path):
    service = _service(tmp_path)
    method_top = _tag_direction(_candidate(
        "cand_method_top",
        "idea_select_direction_warning",
        title="Citation faithful RAG verifier",
        novelty=9.0,
    ), "method")
    method_second = _tag_direction(_candidate(
        "cand_method_second",
        "idea_select_direction_warning",
        title="Evidence-bound RAG verifier",
        novelty=8.2,
    ), "method")
    weak_benchmark = _tag_direction(_candidate(
        "cand_benchmark_weak",
        "idea_select_direction_warning",
        title="Weak benchmark idea",
        novelty=4.0,
    ), "benchmark")
    weak_benchmark.referenceSupport = 3.0
    gate_reports = {
        method_top.id: {"passed": True, "scoreAfterGate": 8.4, "blockingIssues": [], "warnings": []},
        method_second.id: {"passed": True, "scoreAfterGate": 8.1, "blockingIssues": [], "warnings": []},
        weak_benchmark.id: {
            "passed": False,
            "scoreAfterGate": 5.0,
            "blockingIssues": ["Specificity scores are below the handoff threshold."],
            "warnings": [],
        },
    }

    result = service._select_final_candidates(
        [method_top, method_second, weak_benchmark],
        gate_reports,
        max_count=3,
    )

    assert result["finalCandidateIds"] == ["cand_method_top", "cand_method_second"]
    assert result["summary"]["finalDirectionTypes"] == ["method", "method"]
    assert result["summary"]["directionDiversitySatisfied"] is False
    assert "Final shortlist lacks direction diversity" in result["summary"]["warnings"][0]


def test_select_final_candidates_marks_insufficient_final_count(tmp_path):
    service = _service(tmp_path)
    top = _candidate(
        "cand_only_final",
        "idea_insufficient_final",
        title="Evidence-grounded multi-agent research automation",
    )
    weak = _candidate(
        "cand_rejected",
        "idea_insufficient_final",
        title="Generic chatbot prompt",
        novelty=4.0,
    )
    gate_reports = {
        top.id: {"passed": True, "scoreAfterGate": 8.0, "blockingIssues": [], "warnings": []},
        weak.id: {
            "passed": False,
            "scoreAfterGate": 4.0,
            "blockingIssues": ["Candidate has weak topic overlap with the seed query."],
            "warnings": [],
        },
    }

    result = service._select_final_candidates([top, weak], gate_reports, max_count=2)

    assert result["finalCandidateIds"] == [top.id]
    assert result["summary"]["targetFinalCandidateCount"] == 2
    assert result["summary"]["qualityStatus"] == "insufficient_final_candidates"
    assert result["summary"]["requiresRegeneration"] is True


def test_target_final_candidate_count_remains_two_with_one_ranked_candidate(tmp_path):
    service = _service(tmp_path)
    session = IdeaSession(
        id="idea_target_two",
        config=IdeaSessionConfig(
            seedQuery="citation faithful RAG",
            maxCandidates=1,
        ),
    )
    only = _candidate("cand_only", session.id, title="Citation faithful RAG verifier")

    assert service._target_final_candidate_count(session, [only]) == 2


def test_resume_session_transitions_waiting_session_to_running(tmp_path):
    service = _service(tmp_path)
    session = IdeaSession(
        id="idea_resume_evidence",
        config=IdeaSessionConfig(seedQuery="citation faithful RAG"),
        status=idea_service_module.IdeaSessionStatus.AWAITING_EVIDENCE,
        startedAt=idea_service_module._utcnow(),
        endedAt=idea_service_module._utcnow(),
        errorMessage="Evidence pool is incomplete.",
        qualityLoopSummary={"resumeFrom": "evidenceGate"},
    )
    service.session_storage.create(session)

    resumed = service.resume_session(session.id)

    assert resumed.status == idea_service_module.IdeaSessionStatus.RUNNING
    assert resumed.endedAt is None
    assert resumed.errorMessage is None


def test_waiting_session_does_not_expose_internal_candidates(tmp_path):
    service = _service(tmp_path)
    session_id = "idea_waiting_hidden"
    service.session_storage.create(
        IdeaSession(
            id=session_id,
            config=IdeaSessionConfig(seedQuery="citation faithful RAG"),
            status=idea_service_module.IdeaSessionStatus.AWAITING_IDEAS,
            candidateIds=["cand_internal"],
            finalCandidateIds=[],
        )
    )
    service.candidate_storage.create(
        _candidate("cand_internal", session_id, title="Internal rejected candidate")
    )

    assert service.get_candidates(session_id) == []


def test_pipeline_persists_awaiting_evidence_instead_of_failed(monkeypatch, tmp_path):
    service = _service(tmp_path)
    service._pipeline_lock_guard = threading.Lock()
    service._pipeline_locks = {}
    session = IdeaSession(
        id="idea_pipeline_waiting_evidence",
        config=IdeaSessionConfig(seedQuery="citation faithful RAG"),
        status=idea_service_module.IdeaSessionStatus.RUNNING,
        startedAt=idea_service_module._utcnow(),
        trace=idea_service_module.WorkflowTrace(
            sessionId="idea_pipeline_waiting_evidence",
            startedAt=idea_service_module._utcnow(),
        ),
    )
    service.session_storage.create(session)

    def ok_step(_session):
        return {}, {}, []

    def evidence_step(_session):
        raise idea_service_module.AwaitingEvidenceError("Evidence is incomplete")

    for name in ["_step_expand_query", "_step_literature_search", "_step_novelty_check", "_step_gap_analysis"]:
        monkeypatch.setattr(service, name, ok_step)
    monkeypatch.setattr(service, "_step_evidence_gate", evidence_step)

    result = service.run_pipeline(session.id)

    assert result.status == idea_service_module.IdeaSessionStatus.AWAITING_EVIDENCE
    assert result.qualityLoopSummary["resumeFrom"] == "evidenceGate"
    assert result.endedAt is None


def test_pool_repair_targets_second_candidate_when_top_already_passes(tmp_path):
    service = _service(tmp_path)
    session = IdeaSession(
        id="idea_pool_repair",
        config=IdeaSessionConfig(seedQuery="citation faithful RAG", maxCandidates=3),
    )
    top = _candidate(
        "cand_top",
        session.id,
        title="Citation faithful RAG verifier with refusal decisions",
        novelty=9.0,
    )
    repairable = _candidate(
        "cand_repair",
        session.id,
        title="Traceable RAG risk index for high-risk QA",
        novelty=7.0,
    )
    weak = _candidate(
        "cand_weak",
        session.id,
        title="Generic chatbot prompt template",
        novelty=4.0,
    )
    weak.alignment = 3.0
    gate_reports = {
        top.id: {"passed": True, "scoreAfterGate": 8.3, "blockingIssues": [], "warnings": []},
        repairable.id: {
            "passed": False,
            "scoreAfterGate": 7.4,
            "blockingIssues": ["The idea is not sufficiently grounded in the provided evidenceRefs."],
            "warnings": [],
            "suggestedImprovements": ["Explicitly link claims to supporting evidence."],
        },
        weak.id: {
            "passed": False,
            "scoreAfterGate": 4.2,
            "blockingIssues": ["Candidate has weak topic overlap with the seed query."],
            "warnings": [],
        },
    }

    target_count = service._target_final_candidate_count(session, [top, repairable, weak])
    ready = service._current_final_ready_candidates([top, repairable, weak], gate_reports, target_count)
    targets = service._pick_pool_repair_targets(
        [top, repairable, weak],
        gate_reports,
        final_ready_ids={candidate.id for candidate in ready},
        paper_quality_gate={"passed": True},
        max_targets=2,
    )

    assert target_count == 2
    assert [candidate.id for candidate in ready] == [top.id]
    assert [target["candidateId"] for target in targets] == [repairable.id]
    assert targets[0]["action"] == "regenerate_idea"
    assert targets[0]["failureRoute"] == "candidate_ungrounded"


def test_pool_repair_prioritizes_missing_direction_candidate(tmp_path):
    service = _service(tmp_path)
    method_top = _tag_direction(_candidate(
        "cand_method_top",
        "idea_pool_repair_direction",
        title="Citation faithful RAG verifier",
        novelty=9.0,
    ), "method")
    method_repair = _tag_direction(_candidate(
        "cand_method_repair",
        "idea_pool_repair_direction",
        title="Method variant needing repair",
        novelty=8.5,
    ), "method")
    benchmark_repair = _tag_direction(_candidate(
        "cand_benchmark_repair",
        "idea_pool_repair_direction",
        title="Benchmark candidate needing repair",
        novelty=7.8,
    ), "benchmark")
    gate_reports = {
        method_top.id: {"passed": True, "scoreAfterGate": 8.7, "blockingIssues": [], "warnings": []},
        method_repair.id: {
            "passed": False,
            "scoreAfterGate": 8.4,
            "blockingIssues": ["Specificity scores are below the handoff threshold."],
            "warnings": [],
            "suggestedImprovements": ["Specify variables, metrics, and validation steps."],
        },
        benchmark_repair.id: {
            "passed": False,
            "scoreAfterGate": 7.5,
            "blockingIssues": ["Specificity scores are below the handoff threshold."],
            "warnings": [],
            "suggestedImprovements": ["Specify benchmark datasets and expected metrics."],
        },
    }

    targets = service._pick_pool_repair_targets(
        [method_top, method_repair, benchmark_repair],
        gate_reports,
        final_ready_ids={method_top.id},
        paper_quality_gate={"passed": True},
        max_targets=1,
    )

    assert [target["candidateId"] for target in targets] == ["cand_benchmark_repair"]
    assert targets[0]["directionType"] == "benchmark"
    assert targets[0]["fillsMissingDirection"] is True


def test_regenerated_candidate_preserves_direction_metadata(tmp_path, monkeypatch):
    service = _service(tmp_path)
    session = IdeaSession(
        id="idea_regenerate_direction",
        config=IdeaSessionConfig(
            seedQuery="citation faithful RAG",
            providerName="fake",
            model="fake-model",
        ),
    )
    base = _tag_direction(
        _candidate(
            "cand_benchmark_repair",
            session.id,
            title="Benchmark candidate needing repair",
        ),
        "benchmark",
        direction_id="dir-benchmark",
    )

    class _FakeResponse:
        text = json.dumps({
            "ideas": [{
                "title": "Evidence-faithful RAG benchmark with contradiction cases",
                "problem": "High-risk RAG systems lack a benchmark for citation-faithful abstention.",
                "keyInsight": "Stress tests should bind answer correctness, citation support, and abstention.",
                "approach": "Construct benchmark splits around unsupported, contradicted, and partially supported answers.",
                "expectedOutcomes": ["Improved citation faithfulness measurement"],
                "risks": [],
                "requiredExperiments": [],
            }]
        })

    class _FakeClient:
        def chat(self, *args, **kwargs):
            return _FakeResponse()

    monkeypatch.setattr(idea_service_module, "get_provider_client", lambda provider_name: _FakeClient())

    regenerated = service._regenerate_candidate_from_review(
        session=session,
        base_candidate=base,
        review_gate={
            "passed": False,
            "blockingIssues": ["Specificity scores are below the handoff threshold."],
            "suggestedImprovements": ["Specify benchmark datasets and expected metrics."],
        },
        critique=None,
        prior_work=[],
        literature_context="paper_1: citation-faithful RAG benchmark context",
    )

    assert regenerated is not None
    assert "direction:dir-benchmark" in regenerated.draftPlan.tags
    assert "directionType:benchmark" in regenerated.draftPlan.tags


def test_candidate_pool_failure_routes_drive_repair_actions(tmp_path):
    service = _service(tmp_path)
    candidate = _candidate(
        "cand_route",
        "idea_pool_repair",
        title="Citation faithful RAG verifier",
    )

    cases = [
        (
            {"passed": True, "blockingIssues": [], "warnings": []},
            {
                "passed": False,
                "errors": ["ideaReview: insufficient external evidence papers (0 < 2)"],
                "warnings": [],
            },
            "evidence_pool_bad",
            "literature_repair",
        ),
        (
            {
                "passed": False,
                "blockingIssues": ["Candidate has no valid evidence IDs linked to the graph."],
                "warnings": [],
                "suggestedImprovements": ["Rewrite the idea to bind each claim to evidenceRefs."],
            },
            {"passed": True},
            "candidate_ungrounded",
            "regenerate_idea",
        ),
        (
            {
                "passed": False,
                "blockingIssues": ["Closest prior work comparison is missing or too thin."],
                "warnings": [],
                "priorWorkComparisonConfidence": 0.2,
            },
            {"passed": True},
            "novelty_unclear",
            "literature_repair",
        ),
        (
            {
                "passed": False,
                "blockingIssues": ["Method and hypothesis are too vague for implementation."],
                "warnings": [],
                "suggestedImprovements": ["Specify variables, metrics, and datasets."],
            },
            {"passed": True},
            "method_vague",
            "regenerate_idea",
        ),
        (
            {
                "passed": False,
                "blockingIssues": ["Candidate has weak topic overlap with the seed query."],
                "warnings": [],
            },
            {"passed": True},
            "off_topic",
            "regenerate_idea",
        ),
    ]

    for gate, paper_gate, expected_route, expected_action in cases:
        route = service._candidate_pool_failure_route(candidate, gate, paper_gate)
        action = service._candidate_pool_repair_action(candidate, gate, paper_gate)

        assert route == expected_route
        assert action == expected_action


def test_pool_repair_regenerates_candidate_for_specificity_failure(tmp_path):
    service = _service(tmp_path)
    candidate = _candidate(
        "cand_specificity",
        "idea_pool_repair",
        title="Citation faithful RAG verifier",
    )
    gate = {
        "passed": False,
        "scoreAfterGate": 7.2,
        "blockingIssues": ["Specificity scores are below the handoff threshold."],
        "warnings": [],
        "suggestedImprovements": ["Specify variables, expected metrics, datasets, and validation steps."],
    }

    action = service._candidate_pool_repair_action(
        candidate,
        gate,
        paper_quality_gate={"passed": True},
    )

    assert action == "regenerate_idea"


def test_legacy_brainstorm_decomposes_seed_into_research_directions(monkeypatch, tmp_path):
    service = _service(tmp_path)
    service.structured_storage = SimpleNamespace(list_by_session=lambda session_id: [])
    service.get_literature = lambda session_id: []
    session = IdeaSession(
        id="idea_directional_brainstorm",
        config=IdeaSessionConfig(
            seedQuery="LLM agents for scientific discovery",
            providerName="fake",
            model="fake-model",
            maxCandidates=4,
        ),
    )

    class FakeClient:
        def __init__(self):
            self.calls = []

        def chat(self, messages, **kwargs):
            prompt = "\n".join(message.content for message in messages)
            self.calls.append(prompt)
            if "research direction decomposer" in prompt.lower():
                return SimpleNamespace(
                    text=json.dumps(
                        {
                            "researchDirections": [
                                {
                                    "id": "dir-method",
                                    "type": "method",
                                    "title": "Method direction",
                                    "focus": "Improve agent planning methods.",
                                    "rationale": "Method novelty is central.",
                                },
                                {
                                    "id": "dir-benchmark",
                                    "type": "benchmark",
                                    "title": "Benchmark direction",
                                    "focus": "Build faithful evaluation tasks.",
                                    "rationale": "The field lacks evaluation.",
                                },
                                {
                                    "id": "dir-safety",
                                    "type": "safety_reliability",
                                    "title": "Safety direction",
                                    "focus": "Reduce unreliable scientific claims.",
                                    "rationale": "Reliability is a key gap.",
                                },
                            ]
                        }
                    ),
                    latency_ms=11,
                )

            if "dir-method" in prompt:
                title = "Planning-aware scientific agent"
            elif "dir-benchmark" in prompt:
                title = "Citation-faithful discovery benchmark"
            else:
                title = "Reliability gate for scientific agents"
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "ideas": [
                            {
                                "title": title,
                                "problem": f"Problem for {title}",
                                "hypothesis": f"Hypothesis for {title}",
                                "keyInsight": f"Insight for {title}",
                                "proposedMethod": f"Method for {title}",
                                "expectedOutcomes": ["better grounded ideas"],
                            }
                        ]
                    }
                ),
                latency_ms=7,
            )

    fake_client = FakeClient()
    monkeypatch.setattr(idea_service_module, "get_provider_client", lambda provider: fake_client)

    _, outputs, _ = service._step_idea_brainstorm_legacy(session)
    candidates = service.candidate_storage.list_by_session(session.id)

    assert outputs["method"] == "legacy_directional_brainstorm"
    assert len(outputs["researchDirections"]) == 3
    assert len(outputs["candidateIds"]) == 3
    assert len(fake_client.calls) == 4
    assert {candidate.title for candidate in candidates} == {
        "Planning-aware scientific agent",
        "Citation-faithful discovery benchmark",
        "Reliability gate for scientific agents",
    }
    assert all(candidate.draftPlan and candidate.draftPlan.tags for candidate in candidates)
    assert any("directionType:benchmark" in candidate.draftPlan.tags for candidate in candidates)


def test_bfts_brainstorm_runs_per_research_direction(monkeypatch, tmp_path):
    service = _service(tmp_path)
    session = IdeaSession(
        id="idea_bfts_directional",
        config=IdeaSessionConfig(
            seedQuery="LLM agents for scientific discovery",
            providerName="fake",
            model="fake-model",
            maxCandidates=4,
        ),
    )
    handoff = BFTSHandoff(
        id="bh_directional",
        sessionId=session.id,
        literatureMapId="lm_directional",
        pathSeedIds=["rps_1"],
        bftsConfig=BFTSConfig(maxNodes=8, maxIterations=2, beamWidth=1, expansionWidth=1),
    )
    path_seed = ReasoningPathSeed(
        seedId="rps_1",
        sessionId=session.id,
        reasoningKgId="rkg_directional",
        templateType="generic",
        rationale="Explore scientific discovery agents",
    )
    service.handoff_storage = SimpleNamespace(get_by_session=lambda session_id: handoff)
    service.path_seed_storage = SimpleNamespace(get=lambda seed_id: path_seed)
    service.structured_storage = SimpleNamespace(list_by_session=lambda session_id: [])

    class FakeClient:
        def chat(self, messages, **kwargs):
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "researchDirections": [
                            {
                                "id": "dir-method",
                                "type": "method",
                                "title": "Method direction",
                                "focus": "Improve agent planning methods.",
                                "rationale": "Method novelty is central.",
                            },
                            {
                                "id": "dir-system",
                                "type": "system",
                                "title": "System direction",
                                "focus": "Build end-to-end scientific agent systems.",
                                "rationale": "System integration is underexplored.",
                            },
                            {
                                "id": "dir-safety",
                                "type": "safety_reliability",
                                "title": "Safety direction",
                                "focus": "Reduce unreliable scientific claims.",
                                "rationale": "Reliability is a key gap.",
                            },
                        ]
                    }
                ),
                latency_ms=5,
            )

    monkeypatch.setattr(idea_service_module, "get_provider_client", lambda provider: FakeClient())

    seen_seed_queries = []

    class FakeBFTSSearchTree:
        def __init__(self, **kwargs):
            seen_seed_queries.append(kwargs["seed_query"])
            self.seed_query = kwargs["seed_query"]

        def run(self):
            if "dir-method" in self.seed_query:
                title = "Planning-aware scientific agent"
            elif "dir-system" in self.seed_query:
                title = "End-to-end discovery agent system"
            else:
                title = "Reliability gate for scientific agents"
            return [_candidate(generate_candidate_id(), session.id, title=title)]

    import app.modules.idea.bfts_search as bfts_search_module

    monkeypatch.setattr(bfts_search_module, "BFTSSearchTree", FakeBFTSSearchTree)

    _, outputs, _ = service._step_idea_brainstorm_bfts(session)
    candidates = service.candidate_storage.list_by_session(session.id)

    assert outputs["method"] == "bfts_directional_tree_search"
    assert len(outputs["researchDirections"]) == 3
    assert len(seen_seed_queries) == 3
    assert len(outputs["candidateIds"]) == 3
    assert any("directionType:system" in candidate.draftPlan.tags for candidate in candidates)


def test_rank_candidates_repairs_pool_until_two_final_candidates(monkeypatch, tmp_path):
    service = _service(tmp_path)
    session = IdeaSession(
        id="idea_pool_step",
        config=IdeaSessionConfig(
            seedQuery="citation faithful RAG",
            providerName="fake",
            model="fake-model",
            maxCandidates=3,
            maxReviewIterations=2,
        ),
        candidateIds=["cand_top", "cand_repair"],
    )
    top = _candidate(
        "cand_top",
        session.id,
        title="Citation faithful RAG verifier with refusal decisions",
        novelty=9.0,
    )
    repairable = _candidate(
        "cand_repair",
        session.id,
        title="Traceable RAG risk index for high-risk QA",
        novelty=7.0,
    )
    service.session_storage.create(session)
    service.candidate_storage.create(top)
    service.candidate_storage.create(repairable)
    service.structured_storage = SimpleNamespace(
        list_by_session=lambda session_id: [
            StructuredPaper(
                id="raw_rag",
                sessionId=session_id,
                rawPaperId="raw_rag",
                title="Citation faithful RAG with abstention",
                abstract="Evidence grounding for high-risk RAG QA.",
                limitations=["Specificity of refusal metrics remains weak."],
                metrics=["citation fidelity", "refusal accuracy"],
            )
        ]
    )
    service.reasoning_kg_storage = SimpleNamespace(get_by_session=lambda session_id: None)
    service.path_seed_storage = SimpleNamespace(list_by_session=lambda session_id: [])
    service.evidence_link_storage = SimpleNamespace(list_by_session=lambda session_id: [])
    service.handoff_storage = SimpleNamespace(get_by_session=lambda session_id: None)
    service.ranked_output_storage = SimpleNamespace(create=lambda output: None)
    service._build_ranking_literature_context = lambda structured, kg, seeds: "literature context"
    service._get_step_output = lambda *args, **kwargs: []
    service._build_candidate_evidence = lambda candidate, **kwargs: CandidateGraphEvidence(
        candidateId=candidate.id,
        supportingPaperIds=["raw_rag"],
        supportingEntityIds=["ent_rag"],
        supportingPathSeedIds=["rps_rag"],
    )
    service._llm_analyze_candidate = lambda candidate, **kwargs: (
        PriorWorkComparison(
            candidateId=candidate.id,
            comparedPaperIds=["raw_rag"],
            differences=["Adds a distinct verifier path."],
            advantages=["Improves evidence traceability."],
            comparisonConfidence=0.8,
        ),
        IdeaCritique(
            candidateId=candidate.id,
            strengths=["Grounded in the literature."],
            weaknesses=[],
            failureModes=[],
            suggestedImprovements=[],
            critiqueConfidence=0.8,
        ),
    )

    def fake_regenerate_candidate_from_review(**kwargs):
        regenerated = _candidate(
            "cand_regenerated",
            session.id,
            title="Evidence traceability benchmark for high-risk RAG",
        )
        regenerated.scoringMethod = "llm_regenerated_from_idea_review"
        return regenerated

    service._regenerate_candidate_from_review = fake_regenerate_candidate_from_review

    class FakeRankingService:
        def rank_candidates(self, candidates, **kwargs):
            return candidates, []

    def fake_gate(ranked, **kwargs):
        reports = {}
        for candidate in ranked:
            if candidate.id in {"cand_top", "cand_regenerated"}:
                reports[candidate.id] = {
                    "passed": True,
                    "scoreAfterGate": candidate.overallScore,
                    "blockingIssues": [],
                    "warnings": [],
                    "suggestedImprovements": [],
                }
            else:
                reports[candidate.id] = {
                    "passed": False,
                    "scoreAfterGate": candidate.overallScore,
                    "blockingIssues": ["Specificity scores are below the handoff threshold."],
                    "warnings": [],
                    "suggestedImprovements": ["Specify variables, metrics, and validation steps."],
                }
        return reports

    monkeypatch.setattr(idea_service_module, "get_ranking_service", lambda: FakeRankingService())
    monkeypatch.setattr(
        idea_service_module,
        "_evaluate_paper_quality_gate",
        lambda **kwargs: {"passed": True, "errors": [], "warnings": []},
    )
    service._apply_idea_review_gate = fake_gate

    _, outputs, _ = service._step_rank_candidates(session)

    assert outputs["finalCandidateIds"] == ["cand_top", "cand_regenerated"]
    assert outputs["regeneratedCandidateIds"] == ["cand_regenerated"]
    assert outputs["internalReviewIterations"][0]["finalReadyCountBefore"] == 1
    assert outputs["internalReviewIterations"][0]["finalReadyCountAfter"] == 2
    assert outputs["internalReviewIterations"][0]["repairTargets"][0]["candidateId"] == "cand_repair"


def test_idea_review_gate_runs_five_independent_llm_reviewers(monkeypatch, tmp_path):
    service = _service(tmp_path)
    calls = []
    reviewer_names = [
        "IdeaEvidenceReviewer",
        "IdeaNoveltyReviewer",
        "IdeaFeasibilityReviewer",
        "IdeaSpecificityReviewer",
        "IdeaImpactReviewer",
    ]

    class FakeClient:
        def chat(self, messages, **kwargs):
            prompt = "\n".join(message.content for message in messages)
            reviewer = next(name for name in reviewer_names if name in prompt)
            calls.append(reviewer)
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "score": 8.2,
                        "pass": True,
                        "blockingIssues": [],
                        "repairInstructions": [f"{reviewer} repair note"],
                        "evidenceRefs": ["raw_rag"],
                        "confidence": 0.82,
                    }
                ),
                latency_ms=17,
            )

    monkeypatch.setattr(idea_service_module, "get_provider_client", lambda provider: FakeClient())
    candidate = _candidate(
        "cand_reviewed",
        "idea_reviewed",
        title="Citation-faithful refusal verifier for high-risk RAG",
    )
    report = service._apply_idea_review_gate(
        ranked=[candidate],
        evidence_list=[
            CandidateGraphEvidence(
                candidateId=candidate.id,
                supportingPaperIds=["raw_rag"],
                supportingEntityIds=["ent_rag"],
                supportingPathSeedIds=["rps_rag"],
            )
        ],
        prior_work_comparisons=[
            PriorWorkComparison(
                candidateId=candidate.id,
                comparedPaperIds=["raw_rag"],
                differences=["Adds refusal-aware citation verification."],
                advantages=["Improves auditability."],
                comparisonConfidence=0.8,
            )
        ],
        critiques=[
            IdeaCritique(
                candidateId=candidate.id,
                strengths=["Grounded in high-risk RAG limitations."],
                weaknesses=[],
                failureModes=[],
                suggestedImprovements=["Specify evaluation datasets."],
                critiqueConfidence=0.8,
            )
        ],
        seed_query="Improve citation faithfulness and refusal in high-risk RAG QA.",
        provider_name="fake",
        model="fake-model",
        literature_context="[raw_rag] Citation faithfulness and abstention paper.",
    )

    reviewer_reports = report[candidate.id]["reviewerReports"]

    assert set(calls) == set(reviewer_names)
    assert [item["reviewer"] for item in reviewer_reports] == reviewer_names
    for item in reviewer_reports:
        assert item["mode"] == "llm+rule"
        assert item["score"] == 8.2
        assert item["pass"] is True
        assert item["passed"] is True
        assert item["blockingIssues"] == []
        assert item["repairInstructions"]
        assert item["evidenceRefs"] == ["raw_rag"]
        assert item["confidence"] == 0.82


def test_idea_reviewers_run_with_bounded_concurrency(monkeypatch, tmp_path):
    service = _service(tmp_path)
    monkeypatch.setenv("FAROS_IDEA_REVIEWER_CONCURRENCY", "3")
    lock = threading.Lock()
    active = 0
    max_active = 0

    class FakeClient:
        def chat(self, messages, **kwargs):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            try:
                time.sleep(0.05)
                return SimpleNamespace(
                    text=json.dumps(
                        {
                            "score": 8.0,
                            "pass": True,
                            "blockingIssues": [],
                            "repairInstructions": [],
                            "evidenceRefs": ["raw_rag"],
                            "confidence": 0.8,
                        }
                    ),
                    latency_ms=50,
                )
            finally:
                with lock:
                    active -= 1

    monkeypatch.setattr(idea_service_module, "get_provider_client", lambda provider: FakeClient())
    candidate = _candidate(
        "cand_parallel",
        "idea_parallel",
        title="Citation-faithful refusal verifier for high-risk RAG",
    )

    report = service._apply_idea_review_gate(
        ranked=[candidate],
        evidence_list=[
            CandidateGraphEvidence(
                candidateId=candidate.id,
                supportingPaperIds=["raw_rag"],
                supportingEntityIds=["ent_rag"],
                supportingPathSeedIds=["rps_rag"],
            )
        ],
        prior_work_comparisons=[
            PriorWorkComparison(
                candidateId=candidate.id,
                comparedPaperIds=["raw_rag"],
                differences=["Adds refusal-aware citation verification."],
                advantages=["Improves traceability."],
                comparisonConfidence=0.8,
            )
        ],
        critiques=[],
        seed_query="Improve citation faithfulness and refusal in high-risk RAG QA.",
        provider_name="fake",
        model="fake-model",
        literature_context="[raw_rag] Citation faithfulness and abstention paper.",
    )

    assert report[candidate.id]["passed"] is True
    assert 1 < max_active <= 3
    assert [item["reviewer"] for item in report[candidate.id]["reviewerReports"]] == [
        "IdeaEvidenceReviewer",
        "IdeaNoveltyReviewer",
        "IdeaFeasibilityReviewer",
        "IdeaSpecificityReviewer",
        "IdeaImpactReviewer",
    ]


def test_idea_reviewer_default_concurrency_is_three(monkeypatch):
    monkeypatch.delenv("FAROS_IDEA_REVIEWER_CONCURRENCY", raising=False)

    assert idea_service_module._idea_reviewer_concurrency() == 3


def test_idea_review_gate_does_not_reject_bilingual_topic_match(monkeypatch, tmp_path):
    service = _service(tmp_path)

    class FakeClient:
        def chat(self, messages, **kwargs):
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "score": 8.1,
                        "pass": True,
                        "blockingIssues": [],
                        "repairInstructions": [],
                        "evidenceRefs": ["raw_rag"],
                        "confidence": 0.86,
                    }
                ),
                latency_ms=11,
            )

    monkeypatch.setattr(idea_service_module, "get_provider_client", lambda provider: FakeClient())
    candidate = _candidate(
        "cand_bilingual",
        "idea_bilingual",
        title="Enhancing RAG fidelity in high-risk QA with citation, refusal, and evidence traceability checks",
    )
    candidate.problem = (
        "Improve citation fidelity, refusal accuracy, and evidence traceability "
        "for retrieval-augmented generation in high-risk question answering."
    )
    candidate.keyInsight = (
        "A verifier combines citation support, abstention confidence, and traceable evidence chains."
    )

    report = service._apply_idea_review_gate(
        ranked=[candidate],
        evidence_list=[
            CandidateGraphEvidence(
                candidateId=candidate.id,
                supportingPaperIds=["raw_rag"],
                supportingEntityIds=["ent_rag"],
                supportingPathSeedIds=["rps_rag"],
            )
        ],
        prior_work_comparisons=[
            PriorWorkComparison(
                candidateId=candidate.id,
                comparedPaperIds=["raw_rag"],
                differences=["Adds refusal-aware citation verification for high-risk QA."],
                advantages=["Improves traceability."],
                comparisonConfidence=0.8,
            )
        ],
        critiques=[],
        seed_query="提升高风险问答场景中 RAG 系统的引用忠实性、拒答能力和证据可追踪性",
        provider_name="fake",
        model="fake-model",
        literature_context="[raw_rag] Citation faithfulness and abstention paper.",
    )

    gate = report[candidate.id]

    assert gate["passed"] is True
    assert "Candidate has weak topic overlap with the seed query." not in gate["blockingIssues"]


def test_rule_idea_reviewer_blocks_unrequested_application_domain_drift(tmp_path):
    service = _service(tmp_path)
    seed_query = "reliable multi-agent research automation with evidence-grounded planning and self-review"
    candidate = _candidate(
        "cand_carbon_drift",
        "idea_topic_drift",
        title="Evaluating reliable multi-agent systems in real-world carbon market analysis",
    )
    candidate.problem = (
        "Reliable multi-agent research automation needs evidence-grounded planning, "
        "but this proposal focuses the benchmark on carbon market analysis and automated reports."
    )
    candidate.keyInsight = (
        "Use carbon trading reports as the primary setting for self-review and reliability checks."
    )
    candidate.proposedMethod = (
        "Build a multi-agent workflow for carbon market data collection, report generation, "
        "and evidence-grounded self-review."
    )

    report = service._rule_idea_reviewer_report(
        spec={"name": "IdeaFeasibilityReviewer"},
        candidate=candidate,
        evidence=CandidateGraphEvidence(
            candidateId=candidate.id,
            supportingPaperIds=["raw_agent"],
            supportingEntityIds=["ent_agent"],
            supportingPathSeedIds=["rps_agent"],
        ),
        comparisons=[
            PriorWorkComparison(
                candidateId=candidate.id,
                comparedPaperIds=["raw_agent"],
                differences=["Adds evidence-grounded planning and self-review."],
                advantages=["Improves reliability."],
                comparisonConfidence=0.8,
            )
        ],
        critique=None,
        seed_query=seed_query,
        allowed_evidence_refs=["raw_agent", "ent_agent", "rps_agent"],
    )

    assert report["passed"] is False
    assert any("topic drift" in issue.lower() for issue in report["blockingIssues"])
    assert any("seed query" in instruction.lower() for instruction in report["repairInstructions"])


def test_rule_idea_reviewer_blocks_generic_unrequested_application_phrase(tmp_path):
    service = _service(tmp_path)
    seed_query = "reliable multi-agent research automation with evidence-grounded planning and self-review"
    candidate = _candidate(
        "cand_warehouse_drift",
        "idea_topic_drift",
        title="Reliable multi-agent self-review for warehouse logistics optimization",
    )
    candidate.problem = (
        "Use evidence-grounded planning to coordinate warehouse routing, packing, "
        "and inventory optimization."
    )
    candidate.keyInsight = "Warehouse logistics becomes the main evaluation scenario."

    report = service._rule_idea_reviewer_report(
        spec={"name": "IdeaFeasibilityReviewer"},
        candidate=candidate,
        evidence=CandidateGraphEvidence(
            candidateId=candidate.id,
            supportingPaperIds=["raw_agent"],
            supportingEntityIds=["ent_agent"],
            supportingPathSeedIds=["rps_agent"],
        ),
        comparisons=[
            PriorWorkComparison(
                candidateId=candidate.id,
                comparedPaperIds=["raw_agent"],
                differences=["Adds evidence-grounded planning and self-review."],
                advantages=["Improves reliability."],
                comparisonConfidence=0.8,
            )
        ],
        critique=None,
        seed_query=seed_query,
        allowed_evidence_refs=["raw_agent", "ent_agent", "rps_agent"],
    )

    assert report["passed"] is False
    assert any("unrequested application" in issue.lower() for issue in report["blockingIssues"])


def test_rule_idea_reviewer_blocks_generic_drift_for_cjk_seed_with_english_queries(tmp_path):
    service = _service(tmp_path)
    candidate = _candidate(
        "cand_cjk_warehouse_drift",
        "idea_cjk_topic_drift",
        title="Reliable multi-agent self-review for warehouse logistics optimization",
    )
    candidate.problem = (
        "Use evidence-grounded planning to coordinate warehouse routing, packing, "
        "and inventory optimization."
    )
    candidate.keyInsight = "Warehouse logistics becomes the main evaluation scenario."

    report = service._rule_idea_reviewer_report(
        spec={"name": "IdeaFeasibilityReviewer"},
        candidate=candidate,
        evidence=CandidateGraphEvidence(
            candidateId=candidate.id,
            supportingPaperIds=["raw_agent"],
            supportingEntityIds=["ent_agent"],
            supportingPathSeedIds=["rps_agent"],
        ),
        comparisons=[
            PriorWorkComparison(
                candidateId=candidate.id,
                comparedPaperIds=["raw_agent"],
                differences=["Adds evidence-grounded planning and self-review."],
                advantages=["Improves reliability."],
                comparisonConfidence=0.8,
            )
        ],
        critique=None,
        seed_query="\u57fa\u4e8e\u8bc1\u636e\u89c4\u5212\u548c\u81ea\u6211\u8bc4\u5ba1\u7684\u53ef\u9760\u591a\u667a\u80fd\u4f53\u7814\u7a76\u81ea\u52a8\u5316",
        allowed_evidence_refs=["raw_agent", "ent_agent", "rps_agent"],
        english_search_queries=[
            "reliable multi-agent research automation",
            "evidence-grounded planning and self-review",
        ],
    )

    assert report["passed"] is False
    assert any("unrequested application" in issue.lower() for issue in report["blockingIssues"])


def test_revalidate_session_final_candidates_removes_stale_topic_drift(tmp_path):
    service = _service(tmp_path)
    session_id = "idea_revalidate_drift"
    seed_query = "reliable multi-agent research automation with evidence-grounded planning and self-review"
    drifted = _candidate(
        "cand_drifted_final",
        session_id,
        title="Reliable multi-agent benchmark for carbon market analysis",
    )
    drifted.problem = (
        "Use evidence-grounded planning and self-review for carbon market reports."
    )
    drifted.keyInsight = "Carbon trading reports become the primary reliability setting."
    stable = _candidate(
        "cand_stable_hidden",
        session_id,
        title="Evidence-grounded planning benchmark for multi-agent research automation",
    )
    stable.problem = (
        "Evaluate reliable multi-agent research automation with evidence-grounded "
        "planning and self-review across research tasks."
    )
    for candidate in [drifted, stable]:
        candidate.graphEvidence = CandidateGraphEvidence(
            candidateId=candidate.id,
            supportingPaperIds=["raw_agent"],
            supportingEntityIds=["ent_agent"],
            supportingPathSeedIds=["rps_agent"],
        )
        candidate.closestPriorWork = [
            PriorWorkComparison(
                candidateId=candidate.id,
                comparedPaperIds=["raw_agent"],
                differences=["Adds evidence-grounded planning and self-review."],
                advantages=["Improves reliability."],
                comparisonConfidence=0.8,
            )
        ]

    service.session_storage.create(
        IdeaSession(
            id=session_id,
            config=IdeaSessionConfig(seedQuery=seed_query, maxCandidates=2),
            candidateIds=[drifted.id, stable.id],
            finalCandidateIds=[drifted.id],
            hiddenCandidateIds=[stable.id],
        )
    )
    service.candidate_storage.create(drifted)
    service.candidate_storage.create(stable)

    session = service.revalidate_final_candidates(session_id)

    assert session.finalCandidateIds == [stable.id]
    assert drifted.id in session.rejectedCandidateIds
    assert session.qualityLoopSummary["revalidation"]["removedFinalCandidateIds"] == [drifted.id]
    assert session.qualityLoopSummary["revalidation"]["status"] == "updated"


def test_idea_evidence_reviewer_does_not_require_inline_raw_ids(monkeypatch, tmp_path):
    service = _service(tmp_path)
    reviewer_names = [
        "IdeaEvidenceReviewer",
        "IdeaNoveltyReviewer",
        "IdeaFeasibilityReviewer",
        "IdeaSpecificityReviewer",
        "IdeaImpactReviewer",
    ]

    class FakeClient:
        def chat(self, messages, **kwargs):
            prompt = "\n".join(message.content for message in messages)
            reviewer = next(name for name in reviewer_names if name in prompt)
            if reviewer == "IdeaEvidenceReviewer":
                return SimpleNamespace(
                    text=json.dumps(
                        {
                            "score": 4.6,
                            "pass": False,
                            "blockingIssues": [
                                "Lack of concrete evidenceRefs to support the idea",
                                "No explicit citations in the candidate's text",
                            ],
                            "repairInstructions": [
                                "Integrate specific references from the allowedEvidenceRefs into the candidate's text.",
                            ],
                            "evidenceRefs": ["raw_rag"],
                            "confidence": 0.85,
                        }
                    ),
                    latency_ms=13,
                )
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "score": 8.0,
                        "pass": True,
                        "blockingIssues": [],
                        "repairInstructions": [],
                        "evidenceRefs": ["raw_rag"],
                        "confidence": 0.85,
                    }
                ),
                latency_ms=13,
            )

    monkeypatch.setattr(idea_service_module, "get_provider_client", lambda provider: FakeClient())
    candidate = _candidate(
        "cand_evidence",
        "idea_evidence",
        title="Citation-faithful refusal verifier for high-risk RAG",
    )

    report = service._apply_idea_review_gate(
        ranked=[candidate],
        evidence_list=[
            CandidateGraphEvidence(
                candidateId=candidate.id,
                supportingPaperIds=["raw_rag"],
                supportingEntityIds=["ent_rag"],
                supportingPathSeedIds=["rps_rag"],
            )
        ],
        prior_work_comparisons=[
            PriorWorkComparison(
                candidateId=candidate.id,
                comparedPaperIds=["raw_rag"],
                differences=["Adds refusal-aware citation verification for high-risk QA."],
                advantages=["Improves traceability."],
                comparisonConfidence=0.8,
            )
        ],
        critiques=[
            IdeaCritique(
                candidateId=candidate.id,
                strengths=["Grounded in high-risk RAG limitations."],
                weaknesses=[],
                failureModes=[],
                suggestedImprovements=[],
                critiqueConfidence=0.8,
            )
        ],
        seed_query="Improve citation faithfulness and refusal in high-risk RAG QA.",
        provider_name="fake",
        model="fake-model",
        literature_context="[raw_rag] Citation faithfulness and abstention paper.",
    )

    gate = report[candidate.id]
    evidence_report = gate["reviewerReports"][0]

    assert gate["passed"] is True
    assert evidence_report["passed"] is True
    assert evidence_report["blockingIssues"] == []
    assert "No explicit citations in the candidate's text" in evidence_report["repairInstructions"]


def test_parse_ideas_json_coerces_string_expected_outcomes(tmp_path):
    service = _service(tmp_path)
    text = json.dumps(
        {
            "ideas": [
                {
                    "title": "Citation-faithful RAG verifier",
                    "problem": "High-risk RAG needs faithful citations.",
                    "keyInsight": "Verifier improves citation faithfulness.",
                    "approach": "Train and evaluate a verifier.",
                    "expectedOutcomes": "Higher citation fidelity and safer refusal behavior.",
                    "risks": [],
                    "requiredExperiments": [],
                }
            ]
        }
    )

    candidates = service._parse_ideas_json("idea_parse", text, 1)

    assert len(candidates) == 1
    assert candidates[0].expectedMetrics == ["Higher citation fidelity and safer refusal behavior."]
    assert candidates[0].draftPlan.expectedOutcomes == ["Higher citation fidelity and safer refusal behavior."]
    assert candidates[0].hypothesisStatement == "Verifier improves citation faithfulness."
    assert candidates[0].proposedMethod == "Train and evaluate a verifier."
    assert candidates[0].expectedOutcome == "Higher citation fidelity and safer refusal behavior."


def test_parse_ideas_json_coerces_scalar_candidate_containers(tmp_path):
    service = _service(tmp_path)
    text = json.dumps(
        {
            "ideas": {
                "title": "Reliable multi-agent self-review",
                "problem": "Long-running research agents accumulate planning errors.",
                "hypothesis": "Evidence-grounded self-review reduces unrecovered planning errors.",
                "proposedMethod": "Add evidence checks before task reassignment.",
                "expectedOutcomes": 0.2,
                "risks": "Review loops may add latency.",
                "requiredExperiments": 3,
            }
        }
    )

    candidates = service._parse_ideas_json("idea_scalar_parse", text, 1)

    assert len(candidates) == 1
    assert candidates[0].title == "Reliable multi-agent self-review"
    assert candidates[0].expectedMetrics == ["0.2"]
    assert candidates[0].risks == []
    assert candidates[0].requiredExperiments == []


def test_parse_ideas_json_drops_non_text_experiment_metrics_and_datasets(tmp_path):
    service = _service(tmp_path)
    text = json.dumps(
        {
            "ideas": [
                {
                    "title": "Reliable self-review",
                    "problem": "Research agents need reliable recovery.",
                    "hypothesis": "Self-review improves recovery.",
                    "proposedMethod": "Compare self-review with a no-review baseline.",
                    "requiredExperiments": [
                        {
                            "name": "Recovery evaluation",
                            "metrics": ["recovery rate", None, {"junk": 1}, ["nested"], 0.2, True],
                            "datasets": ["ResearchBench", {"junk": 2}, False],
                        }
                    ],
                }
            ]
        }
    )

    candidates = service._parse_ideas_json("idea_experiment_parse", text, 1)

    assert candidates[0].requiredExperiments[0].metrics == ["recovery rate"]
    assert candidates[0].requiredExperiments[0].datasets == ["ResearchBench"]
