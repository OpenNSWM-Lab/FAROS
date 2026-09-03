from app.modules.review.claim_extractor import extract_claims


def test_final_manuscript_claims_supersede_stale_brief_claims():
    artifacts = {
        "paper": {
            "id": "paper_final",
            "briefJson": {
                "core_claim": "The stale brief claims Brier Score improved by 99 percent.",
                "contributions": ["The stale plan contribution outperforms every baseline."],
            },
        },
        "latexFiles": [{
            "path": "sections/results.tex",
            "content": (
                "\\section{Results}\n"
                "Our method reduces ECE from 0.270 to 0.226 on the executed benchmark."
            ),
        }],
    }

    claims = extract_claims(artifacts)

    assert claims
    assert all(claim.sourceSpan.section != "Brief" for claim in claims)
    assert all("99 percent" not in claim.text for claim in claims)


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


def test_claim_extraction_supports_chinese_quantitative_and_method_claims():
    artifacts = {
        "paper": {"id": "paper_zh", "briefJson": {}},
        "latexFiles": [{
            "path": "sections/results.tex",
            "content": (
                "\\section{实验结果}\n"
                "我们提出基于证据门禁的阈值校准方法，并在独立划分上进行选择。\n"
                "实验结果表明，该方法将 Macro F1 从 0.4640 提升 11.41%，"
                "同时准确率降低 3.11%。\n"
            ),
        }],
    }

    claims = extract_claims(artifacts)

    assert len(claims) == 2
    assert claims[0].claimType == "method"
    assert claims[1].claimType == "performance"
    assert all(claim.requiresEvidence for claim in claims)
    assert claims[1].sourceSpan.section == "实验结果"


def test_claim_extraction_joins_wrapped_latex_paragraphs():
    artifacts = {
        "paper": {"id": "paper_wrapped", "briefJson": {}},
        "latexFiles": [{
            "path": "main.tex",
            "content": (
                "\\section{Results}\n"
                "Our method improves Macro F1 from 0.4640\n"
                "to 0.5781 on the held-out Climate-FEVER test split.\n"
            ),
        }],
    }

    claims = extract_claims(artifacts)

    assert len(claims) == 1
    assert "0.5781" in claims[0].text
    assert claims[0].sourceSpan.line == 2


def test_claim_extraction_recognizes_metric_name_before_decimal_value():
    artifacts = {
        "paper": {"id": "paper_metric", "briefJson": {}},
        "latexFiles": [{
            "path": "main.tex",
            "content": (
                "\\section{Results}\n"
                "The independent gate authorized the threshold update. "
                "The method improved held-out Macro F1 from 0.4640 to 0.5781, "
                "an absolute gain of 11.41 percentage points.\n"
            ),
        }],
    }

    claims = extract_claims(artifacts)

    assert len(claims) == 2
    assert claims[0].claimType == "content"
    assert claims[1].claimType == "performance"
    assert "0.4640" in claims[1].text
