from types import SimpleNamespace

import app.modules.idea.service as idea_service_module
from app.models.idea import IdeaSession, IdeaSessionConfig, StructuredPaper
from app.modules.idea.service import IdeaGenerationService


def test_gap_analysis_enforces_multiple_opportunities_via_module_helper(monkeypatch):
    class FakeClient:
        def chat(self, messages, **kwargs):
            return SimpleNamespace(
                text=(
                    '{"gapAnalysis": [], "prioritizedGaps": [], '
                    '"researchOpportunities": ["Evaluate citation faithfulness."]}'
                )
            )

    monkeypatch.setattr(idea_service_module, "get_provider_client", lambda provider: FakeClient())

    service = object.__new__(IdeaGenerationService)
    service.get_literature = lambda session_id: [
        SimpleNamespace(title="Citation-faithful RAG", year=2026, snippet="Evaluation study")
    ]
    literature_map = SimpleNamespace(id="lm_test", selectedPaperIds=["raw_citation"], gaps=[])
    service.map_storage = SimpleNamespace(get_by_session=lambda session_id: literature_map)
    service.structured_storage = SimpleNamespace(
        list_by_session=lambda session_id: [
            StructuredPaper(
                id="raw_citation",
                sessionId=session_id,
                rawPaperId="raw_citation",
                title="Citation-faithful RAG",
            )
        ]
    )
    service.reasoning_builder = SimpleNamespace(
        build_reasoning_kg=lambda **kwargs: SimpleNamespace(id="rkg_test")
    )
    service.reasoning_kg_storage = SimpleNamespace(create=lambda value: value)
    service.graph_linker = SimpleNamespace(link_graphs=lambda **kwargs: [])
    service.evidence_link_storage = SimpleNamespace(create=lambda value: value)
    service.path_seed_gen = SimpleNamespace(generate_seeds=lambda **kwargs: [])
    service.path_seed_storage = SimpleNamespace(create=lambda value: value)
    service.handoff_storage = SimpleNamespace(
        get_by_session=lambda session_id: None,
        delete=lambda handoff_id: None,
        create=lambda value: value,
    )
    service._get_step_output = lambda session, step_name, key, default=None: default
    service._build_rag_literature_context = lambda session: ""

    session = IdeaSession(
        id="idea_gap_analysis",
        config=IdeaSessionConfig(
            providerName="fake",
            model="fake-model",
            seedQuery="Improve citation faithfulness in high-risk RAG QA.",
            paperType="algorithm",
        ),
    )

    _, outputs, errors = service._step_gap_analysis(session)

    assert errors == []
    assert len(outputs["researchOpportunities"]) >= 3
