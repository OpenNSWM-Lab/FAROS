from app.modules.review.claim_extractor import extract_claims


def test_claim_selection_keeps_late_high_risk_claim_and_section_coverage():
    introduction = "\n".join(
        f"We propose analysis component number {index} for the research workflow."
        for index in range(45)
    )
    artifacts = {
        "paper": {"id": "paper_long", "briefJson": {}},
        "latexFiles": [{
            "path": "main.tex",
            "content": (
                "\\section{Introduction}\n"
                f"{introduction}\n"
                "\\section{Late Evaluation}\n"
                "Our system guarantees safe deployment in unseen clinical domains "
                "with a 99 percent accuracy improvement.\n"
            ),
        }],
    }

    claims = extract_claims(artifacts)

    assert len(claims) == 40
    assert any("safe deployment" in claim.text for claim in claims)
    assert any(claim.sourceSpan.section == "Late Evaluation" for claim in claims)


def test_claim_extraction_includes_evidence_assertions():
    assertions = [
        "The evidence establishes reliable deployment across unseen high-stakes domains without further evaluation.",
        "Prior work has established that retrieval grounding eliminates factual errors in scientific reasoning.",
        "The cited work further proves safe deployment in unseen medical and legal environments.",
        "This evidence demonstrates robust transfer to clinical decision support in low-resource languages.",
    ]
    artifacts = {
        "paper": {"id": "paper_assertions", "briefJson": {}},
        "latexFiles": [{
            "path": "main.tex",
            "content": "\\section{Discussion}\n" + "\n".join(assertions),
        }],
    }

    claims = extract_claims(artifacts)

    assert {claim.text for claim in claims} == set(assertions)
    assert all(claim.requiresEvidence for claim in claims)
    assert all("evidence_assertion" in claim.riskHints for claim in claims)
