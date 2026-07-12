from app.modules.idea.evidence_relevance import (
    EvidenceTier,
    assess_search_result,
    build_topic_intent_profile,
)
from app.services.search_service import SearchResult


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
