from app.models.idea import RawPaper
from app.modules.idea.service import (
    _evaluate_evidence_gate_v2,
    _evaluate_paper_quality_gate,
    _normalize_coverage_report,
    _verify_coverage_dimension_support,
)
from app.services.search_service import SearchResult


def _raw(
    title: str,
    *,
    tier: str,
    roles: list[str],
    index: int,
    relevance: float = 0.9,
) -> RawPaper:
    return RawPaper(
        id=f"raw_gate_{index}",
        sessionId="idea_gate",
        title=title,
        abstract=title,
        source=["openalex"],
        retrievalRoles=roles,
        evidenceTier=tier,
        relevanceScore=relevance,
    )


def test_paper_quality_gate_exposes_local_only_fallback_risk():
    papers = [
        RawPaper(
            id=f"raw_local_{index}",
            sessionId="idea_local",
            title=f"Citation faithful RAG local fallback paper {index}",
            abstract="Citation faithful retrieval augmented generation with refusal and traceability.",
            source=["local"],
            relevanceScore=0.9,
        )
        for index in range(4)
    ]

    gate = _evaluate_paper_quality_gate(
        seed="citation faithful RAG for high-risk QA",
        domain="",
        papers=papers,
        stage="literatureSearch.initial",
    )

    assert gate["sourceQuality"] == "local_only"
    assert gate["providerFallbackRisk"] == "high"
    assert gate["localOnly"] is True
    assert "local" in gate["sourcesUsed"]


def test_paper_quality_gate_accepts_role_composed_interdisciplinary_evidence():
    papers = [
        RawPaper(
            id="raw_domain_1",
            sessionId="idea_roles",
            title="The known ending and narrative closure in Dream of the Red Chamber",
            abstract="A study of Hongloumeng ending scholarship and narrative closure.",
            source=["openalex"],
            retrievalRoles=["domain"],
            relevanceScore=0.9,
        ),
        RawPaper(
            id="raw_domain_2",
            sessionId="idea_roles",
            title="Hongloumeng authorship and the unfinished ending",
            abstract="Textual evidence about the ending of Dream of the Red Chamber.",
            source=["openalex"],
            retrievalRoles=["task"],
            relevanceScore=0.85,
        ),
        RawPaper(
            id="raw_method",
            sessionId="idea_roles",
            title="Computational narrative completion for unfinished literary works",
            abstract="A method for narrative closure reconstruction using character constraints.",
            source=["arxiv"],
            retrievalRoles=["method"],
            relevanceScore=0.8,
        ),
        RawPaper(
            id="raw_evaluation",
            sessionId="idea_roles",
            title="Evaluating narrative coherence and character consistency",
            abstract="Metrics and human evaluation for reconstructed story endings.",
            source=["arxiv"],
            retrievalRoles=["evaluation"],
            relevanceScore=0.8,
        ),
    ]

    gate = _evaluate_paper_quality_gate(
        seed="\u9884\u6d4b\u7ea2\u697c\u68a6\u53ef\u80fd\u7ed3\u5c40",
        domain="computational literary studies",
        papers=papers,
        stage="literatureSearch.initial",
        extra_terms=[
            "Dream of the Red Chamber ending studies",
            "Hongloumeng narrative closure reconstruction",
        ],
    )

    assert gate["passed"] is True
    assert gate["roleCoverage"]["passed"] is True
    assert gate["roleCoverage"]["counts"] == {
        "domain": 1,
        "task": 1,
        "method": 1,
        "evaluation": 1,
    }


def test_role_coverage_does_not_pass_with_rejected_query_hits():
    papers = [
        _raw("Clinical forecasting", tier="rejected", roles=["task"], index=1),
        _raw("Chemical evaluation", tier="rejected", roles=["evaluation"], index=2),
        _raw("Generic framework", tier="rejected", roles=["method"], index=3),
        _raw("Generic survey", tier="rejected", roles=["domain"], index=4),
    ]

    gate = _evaluate_paper_quality_gate(
        seed="citation-faithful medical RAG",
        domain="medical QA",
        papers=papers,
        stage="test",
        paper_type="system",
    )

    assert gate["roleCoverage"]["enabled"] is True
    assert gate["roleCoverage"]["passed"] is False
    assert gate["alignedPaperCount"] == 0


def test_survey_does_not_require_method_or_evaluation_query_role():
    papers = [
        _raw("RAG safety survey and open gaps", tier="direct", roles=["domain"], index=5),
        _raw("Retrieval augmented generation safety limitations", tier="direct", roles=["task"], index=6),
        _raw("RAG safety claims and synthesis", tier="direct", roles=["domain"], index=7),
        _raw("Open questions in safe RAG", tier="direct", roles=["task"], index=8),
    ]

    gate = _evaluate_paper_quality_gate(
        seed="RAG safety survey",
        domain="retrieval augmented generation",
        papers=papers,
        stage="test",
        paper_type="survey",
    )

    assert gate["roleCoverage"]["requirements"]["method"] == 0
    assert gate["roleCoverage"]["requirements"]["evaluation"] == 0
    assert gate["roleCoverage"]["passed"] is True


def test_transferable_paper_cannot_fill_domain_role():
    paper = _raw(
        "Narrative completion method for unfinished novel endings",
        tier="transferable",
        roles=["domain", "method"],
        index=9,
    )

    gate = _evaluate_paper_quality_gate(
        seed="Dream of the Red Chamber ending",
        domain="computational literary studies",
        papers=[paper],
        stage="test",
        paper_type="system",
    )

    assert gate["roleCoverage"]["counts"]["domain"] == 0
    assert gate["roleCoverage"]["counts"]["method"] == 1


def test_role_coverage_cannot_bypass_weak_semantic_alignment():
    papers = [
        _raw("Medical clinical forecasting", tier="direct", roles=["domain"], index=10, relevance=0.0),
        _raw("RAG chemical synthesis", tier="direct", roles=["task"], index=11, relevance=0.0),
        _raw("Citation air filtration materials", tier="direct", roles=["method"], index=12, relevance=0.0),
        _raw("Faithful hydraulic actuator benchmark", tier="direct", roles=["evaluation"], index=13, relevance=0.0),
    ]

    gate = _evaluate_paper_quality_gate(
        seed="citation-faithful medical RAG",
        domain="medical QA",
        papers=papers,
        stage="test",
        paper_type="system",
    )

    assert gate["roleCoverage"]["passed"] is True
    assert gate["passed"] is False


def test_raw_search_results_expose_snake_case_role_coverage():
    roles = ["domain", "task", "method", "evaluation"]
    papers = []
    for index, role in enumerate(roles):
        paper = SearchResult(
            title=f"Citation faithful medical RAG {role}",
            authors=[],
            abstract="Medical retrieval augmented generation citation refusal evaluation.",
            year=2025,
            venue="test",
            url="",
            doi=f"10.1000/{role}",
            arxiv_id=None,
            citation_count=0,
            source="openalex",
            relevance_score=0.9,
            evidence_tier="direct",
            retrieval_roles=[role],
        )
        papers.append(paper)

    gate = _evaluate_paper_quality_gate(
        seed="citation-faithful medical RAG",
        domain="medical QA",
        papers=papers,
        stage="test",
        paper_type="system",
    )

    assert gate["roleCoverage"]["enabled"] is True
    assert gate["roleCoverage"]["counts"] == {
        "domain": 1,
        "task": 1,
        "method": 1,
        "evaluation": 1,
    }


def test_evidence_gate_propagates_paper_type_to_role_requirements():
    papers = [
        _raw("RAG safety survey and open gaps", tier="direct", roles=["domain"], index=20),
        _raw("Retrieval augmented generation safety limitations", tier="direct", roles=["task"], index=21),
        _raw("RAG safety claims and synthesis", tier="direct", roles=["domain"], index=22),
        _raw("Open questions in safe RAG", tier="direct", roles=["task"], index=23),
    ]

    gate = _evaluate_evidence_gate_v2(
        seed="RAG safety survey",
        domain="retrieval augmented generation",
        paper_type="survey",
        structured_papers=papers,
        literature_map=None,
        gap_outputs={"gaps": ["RAG safety synthesis remains incomplete"]},
        stage="test",
    )

    assert gate["roleCoverage"]["requirements"]["method"] == 0
    assert gate["roleCoverage"]["requirements"]["evaluation"] == 0


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


def test_coverage_report_drops_transferable_gap_support_but_keeps_method_support():
    report = _normalize_coverage_report(
        {
            "passed": True,
            "overallEvidenceScore": 0.9,
            "dimensions": [
                {
                    "key": "gap",
                    "required": True,
                    "score": 0.9,
                    "supportingPaperIds": ["raw_transfer"],
                },
                {
                    "key": "method",
                    "required": True,
                    "score": 0.9,
                    "supportingPaperIds": ["raw_transfer"],
                },
            ],
        },
        available_paper_ids={"raw_transfer"},
        paper_tiers={"raw_transfer": "transferable"},
    )

    dimensions = {item["key"]: item for item in report["dimensions"]}
    assert dimensions["gap"]["supportingPaperIds"] == []
    assert dimensions["gap"]["status"] == "missing"
    assert dimensions["method"]["supportingPaperIds"] == ["raw_transfer"]
    assert report["passed"] is False
