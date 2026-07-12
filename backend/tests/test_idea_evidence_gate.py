from app.models.idea import RawPaper
from app.modules.idea.service import _evaluate_paper_quality_gate


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
