from app.modules.idea.evidence_relevance import (
    EvidenceTier,
    assess_search_result,
    build_topic_intent_profile,
    deduplicate_search_results,
)
from app.services.search_service import SearchResult
from app.models.idea import RawPaper
from app.storage.idea_storage import RawPaperStorage


def _result(title: str, abstract: str = "") -> SearchResult:
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


def _red_chamber_profile():
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
    profile = _red_chamber_profile()

    assert "dream of the red chamber" in profile.core_anchors
    assert "dream" in profile.generic_terms


def test_hyphenated_seed_phrases_are_preserved_and_stopwords_are_not_anchors():
    profile = build_topic_intent_profile(
        seed="citation-faithful medical RAG for high-risk clinical QA",
        domain="medical NLP, retrieval-augmented generation",
        role_queries={
            "domain": ["citation-faithful medical RAG"],
            "task": ["high-risk clinical question answering"],
            "method": ["retrieval-augmented generation verifier"],
            "evaluation": ["citation faithfulness evaluation"],
        },
    )

    assert "citation faithful" in profile.seed_anchors
    assert "high risk" in profile.seed_anchors
    assert "for" not in profile.core_anchors


def test_direct_named_work_paper_is_eligible():
    assessment = assess_search_result(
        _result(
            "Multiple Authors Detection: A Quantitative Analysis of Dream of the Red Chamber",
            "Computational evidence about authorship and the unfinished ending.",
        ),
        _red_chamber_profile(),
    )

    assert assessment.tier is EvidenceTier.DIRECT
    assert "dream of the red chamber" in assessment.decisive_anchors


def test_transferable_narrative_method_is_eligible_but_not_direct():
    assessment = assess_search_result(
        _result(
            "Narrative completion for unfinished novels",
            "Computational character constraints and coherence evaluation reconstruct plausible endings.",
        ),
        _red_chamber_profile(),
    )

    assert assessment.tier is EvidenceTier.TRANSFERABLE


def test_generic_clinical_forecasting_is_rejected():
    assessment = assess_search_result(
        _result(
            "Clinical time-series forecasting and analysis",
            "A web platform predicts patient outcomes with configurable models.",
        ),
        _red_chamber_profile(),
    )

    assert assessment.tier is EvidenceTier.REJECTED
    assert assessment.rejection_reason == "generic_overlap_only"


def test_generic_chemical_evaluation_is_rejected():
    assessment = assess_search_result(
        _result("ChemEval: A multi-level chemical evaluation for language models"),
        _red_chamber_profile(),
    )

    assert assessment.tier is EvidenceTier.REJECTED


def test_generic_autonomous_llm_agent_is_not_direct_scientific_discovery_evidence():
    profile = build_topic_intent_profile(
        seed="LLM agents for scientific discovery",
        domain="AI for science, autonomous research agents",
        role_queries={
            "domain": ["LLM agents for scientific discovery"],
            "task": ["autonomous research agents using LLMs"],
            "method": ["LLM agent architectures and methods"],
            "evaluation": ["scientific discovery agent evaluation"],
        },
    )

    assessment = assess_search_result(
        _result(
            "Agents: An Open-source Framework for Autonomous Language Agents",
            "A framework for building and deploying LLM agents.",
        ),
        profile,
    )

    assert assessment.tier is not EvidenceTier.DIRECT


def test_scientific_discovery_agent_paper_remains_direct():
    profile = build_topic_intent_profile(
        seed="LLM agents for scientific discovery",
        domain="AI for science, autonomous research agents",
        role_queries={
            "domain": ["LLM agents for scientific discovery"],
            "task": ["autonomous research agents using LLMs"],
            "method": ["LLM agent architectures and methods"],
            "evaluation": ["scientific discovery agent evaluation"],
        },
    )

    assessment = assess_search_result(
        _result(
            "Autonomous LLM Agents for Scientific Discovery",
            "Large language model agents formulate and evaluate scientific hypotheses.",
        ),
        profile,
    )

    assert assessment.tier is EvidenceTier.DIRECT


def test_generic_rag_paper_is_not_direct_medical_citation_evidence():
    profile = build_topic_intent_profile(
        seed="citation-faithful medical RAG for high-risk clinical question answering",
        domain="medical NLP, retrieval-augmented generation, clinical QA",
        role_queries={
            "domain": ["citation-faithful medical RAG"],
            "task": ["high-risk clinical question answering"],
            "method": ["medical RAG attribution methods"],
            "evaluation": ["clinical RAG citation faithfulness evaluation"],
        },
    )

    assessment = assess_search_result(
        _result(
            "A Survey of Graph Retrieval-Augmented Generation",
            "Graph RAG methods, benchmarks, and limitations for customized language models.",
        ),
        profile,
    )

    assert assessment.tier is not EvidenceTier.DIRECT


def test_legal_citation_qa_is_not_direct_medical_clinical_evidence():
    profile = build_topic_intent_profile(
        seed="citation-faithful medical RAG for high-risk clinical question answering",
        domain="medical NLP, retrieval-augmented generation, clinical QA",
        role_queries={
            "domain": ["citation-faithful medical RAG"],
            "task": ["high-risk clinical question answering"],
            "method": ["medical RAG attribution methods"],
            "evaluation": ["clinical RAG citation faithfulness evaluation"],
        },
    )

    assessment = assess_search_result(
        _result(
            "Attribution-Aware Citation Quality for Legal QA",
            "A citation-faithful retrieval-augmented method for legal question answering.",
        ),
        profile,
    )

    assert assessment.tier is not EvidenceTier.DIRECT


def test_high_risk_clinical_citation_rag_remains_direct():
    profile = build_topic_intent_profile(
        seed="citation-faithful medical RAG for high-risk clinical question answering",
        domain="medical NLP, retrieval-augmented generation, clinical QA",
        role_queries={
            "domain": ["citation-faithful medical RAG"],
            "task": ["high-risk clinical question answering"],
            "method": ["medical RAG attribution methods"],
            "evaluation": ["clinical RAG citation faithfulness evaluation"],
        },
    )

    assessment = assess_search_result(
        _result(
            "Citation-Faithful Medical RAG for High-Risk Clinical QA",
            "The system retrieves medical evidence and verifies every clinical citation.",
        ),
        profile,
    )

    assert assessment.tier is EvidenceTier.DIRECT


def test_single_multi_agent_phrase_is_not_direct_research_automation_evidence():
    profile = build_topic_intent_profile(
        seed="reliable multi-agent research automation with evidence-grounded planning and self-review",
        domain="autonomous research agents",
        role_queries={
            "domain": ["multi-agent research automation"],
            "task": ["reliable autonomous research workflows"],
            "method": ["evidence-grounded planning and self-review"],
            "evaluation": ["multi-agent research reliability benchmark"],
        },
    )

    assessment = assess_search_result(
        _result(
            "Multi-Agent Workflow for Medical Intent Classification",
            "A reliable agent framework evaluated on healthcare intent benchmarks.",
        ),
        profile,
    )

    assert assessment.tier is not EvidenceTier.DIRECT


def test_multi_phrase_research_automation_evidence_remains_direct():
    profile = build_topic_intent_profile(
        seed="reliable multi-agent research automation with evidence-grounded planning and self-review",
        domain="autonomous research agents",
        role_queries={
            "domain": ["multi-agent research automation"],
            "task": ["reliable autonomous research workflows"],
            "method": ["evidence-grounded planning and self-review"],
            "evaluation": ["multi-agent research reliability benchmark"],
        },
    )

    assessment = assess_search_result(
        _result(
            "Evidence-Grounded Planning and Self-Review for Multi-Agent Research Automation",
            "The system improves reliable autonomous research workflows and evidence traceability.",
        ),
        profile,
    )

    assert assessment.tier is EvidenceTier.DIRECT


def test_deduplication_merges_roles_queries_sources_and_richer_metadata():
    first = _result("Citation-Enforced RAG")
    first.doi = "10.1000/rag"
    first.source = "semantic_scholar"
    first.retrieval_sources = ["semantic_scholar"]
    first.retrieval_roles = ["domain"]
    first.matched_queries = ["citation faithful RAG"]
    first.relevance_score = 0.4

    second = _result(
        "Citation-Enforced RAG",
        "A richer abstract with refusal evaluation.",
    )
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
        "citation faithful RAG",
        "RAG verifier",
        "RAG refusal benchmark",
    ]
    assert merged.retrieval_sources == ["semantic_scholar", "openalex"]
    assert merged.abstract == second.abstract
    assert merged.relevance_score == 0.8


def test_deduplication_uses_title_when_identifiers_are_split_across_sources():
    doi_result = _result("Evidence Grounded Research Agents")
    doi_result.doi = "10.1000/agents"
    arxiv_result = _result("Evidence-Grounded Research Agents")
    arxiv_result.arxiv_id = "2601.00001"
    arxiv_result.retrieval_roles = ["method"]

    outcome = deduplicate_search_results([doi_result, arxiv_result])

    assert outcome.merge_count == 1
    assert len(outcome.results) == 1
    assert outcome.results[0].arxiv_id == "2601.00001"


def test_raw_paper_storage_updates_persisted_evidence_assessment(tmp_path):
    storage = RawPaperStorage(data_dir=str(tmp_path))
    paper = RawPaper(
        id="raw_assessment",
        sessionId="idea_assessment",
        title="Citation-Enforced RAG",
        evidenceTier="direct",
        decisiveAnchors=["citation", "rag"],
        relevanceComponents={"coreTerms": 0.24},
        relevanceScore=0.8,
    )
    storage.create(paper)

    updated = paper.model_copy(update={
        "retrievalRoles": ["domain", "method"],
        "matchedQueries": ["citation faithful RAG", "RAG verifier"],
        "evidenceTier": "direct",
    })
    storage.update(updated)
    loaded = storage.get(paper.id)

    assert loaded is not None
    assert loaded.evidenceTier == "direct"
    assert loaded.decisiveAnchors == ["citation", "rag"]
    assert loaded.retrievalRoles == ["domain", "method"]
