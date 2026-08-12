from app.modules.review.cem_guidance import annotate_risk_tree_with_mismatch
from app.modules.review.cem_guidance import build_cem_budget_plan
from app.modules.review.evidence_verifier import verify_claim_evidence
from app.modules.review.mismatch_scorer import build_mismatch_report
from app.modules.review.model_router import _build_severity_budget_plan
from app.modules.review.reviewx_models import (
    Claim,
    Evidence,
    EvidenceVerification,
    Finding,
    RiskNode,
    SourceSpan,
)


def _claim() -> Claim:
    return Claim(
        id="claim_001",
        paperId="paper_001",
        text="The method improves accuracy by 42%.",
        claimType="performance",
        importance="high",
        requiresEvidence=True,
        sourceSpan=SourceSpan(file="main.tex", section="Results", line=12),
    )


def _finding(**updates) -> Finding:
    data = {
        "id": "finding_001",
        "paperId": "paper_001",
        "claimId": "claim_001",
        "severity": "blocker",
        "riskType": "unsupported_claim",
        "title": "Performance claim needs metric evidence",
        "description": "No metric evidence supports the numeric claim.",
        "evidenceIds": ["evidence_001"],
        "targetModule": "experiments",
        "suggestedFix": "Attach metrics.json and cite the exact run.",
        "confidence": 0.95,
        "supportStatus": "unsupported",
        "verifierIds": ["verify_001"],
    }
    data.update(updates)
    return Finding(**data)


def test_mismatch_report_keeps_raw_and_calibrated_scores():
    claim = _claim()
    evidence = Evidence(
        id="evidence_001",
        paperId="paper_001",
        evidenceType="experiment",
        sourceModule="experiment",
        sourcePath="metrics.json",
        summary="Experiment exists but does not contain the claimed metric.",
    )
    verification = EvidenceVerification(
        id="verify_001",
        paperId="paper_001",
        claimId="claim_001",
        verifierType="numeric_metric",
        supportStatus="unsupported",
        verdict="The numeric claim has no linked metric.",
        evidenceIds=["evidence_001"],
        confidence=1.0,
    )
    finding = _finding(
        reviewerDecision="partially_valid",
        cemCalibration={"revisionAdjustment": 0.22},
        revisionStatus="resolved",
    )

    report = build_mismatch_report(
        [claim],
        [evidence],
        {"claim_001": ["evidence_001"]},
        [verification],
        [finding],
    )

    score = report["claimScores"][0]
    assert score["rawMismatchScore"] == 0.95
    assert score["mismatchScore"] == 0.635
    assert score["calibration"]["llmDecision"] == "partially_valid"
    assert score["calibration"]["revisionAdjustment"] == 0.22
    assert score["supportStatuses"] == ["unsupported"]


def test_high_stakes_scope_waiver_is_unsupported():
    claim = Claim(
        id="claim_scope",
        paperId="paper_external",
        text=(
            "The evidence establishes reliable deployment across unseen high-stakes clinical domains "
            "without further domain-specific evaluation."
        ),
        claimType="robustness",
        importance="high",
        requiresEvidence=True,
        sourceSpan=SourceSpan(file="main.tex", section="Discussion", line=42),
        riskHints=["evidence_assertion", "citation_or_evidence_needed"],
    )

    verifications = verify_claim_evidence(
        {"id": "paper_external", "briefJson": {}, "externalPaper": {"arxivId": "2601.00001"}},
        [claim],
        [],
        {claim.id: []},
    )

    scope = [item for item in verifications if item.verifierType == "scope_guardrail"]
    assert len(scope) == 1
    assert scope[0].supportStatus == "unsupported"


def test_risk_tree_annotation_uses_cem_policy_and_drivers():
    claim = _claim()
    finding = _finding()
    report = build_mismatch_report([claim], [], {"claim_001": []}, [], [finding])
    tree = [
        RiskNode(
            id="risk_root",
            question="Root",
            claimIds=["claim_001"],
            riskScore=0.2,
            status="passed",
            assignedModel="rules",
        )
    ]

    annotated = annotate_risk_tree_with_mismatch(tree, report)

    assert annotated[0].mismatchScore == 0.95
    assert annotated[0].expansionPolicy == "deep_contradiction_revision"
    assert annotated[0].assignedModel == "qwen-max"
    assert "review_risk" in annotated[0].mismatchDrivers


def test_severity_baseline_budget_plan_is_not_mismatch_guided():
    plan = _build_severity_budget_plan([_finding(), _finding(id="finding_002", severity="minor", confidence=0.4)], "balanced")

    assert plan["policy"] == "severity_confidence_baseline"
    assert plan["selectedFindingIds"] == ["finding_001"]
    assert plan["allocations"][0]["drivers"] == ["severity", "confidence"]


def test_citation_semantic_verifier_flags_off_topic_citation():
    claim = Claim(
        id="claim_001",
        paperId="paper_001",
        text="Our framework generalizes to low-resource clinical triage deployments under distribution shift.",
        claimType="method",
        importance="high",
        requiresEvidence=False,
        sourceSpan=SourceSpan(file="main.tex", section="CEM-Bench Injected Claims", line=7),
        riskHints=["citation_key:vaswani2017attention"],
    )
    evidence = Evidence(
        id="evidence_001",
        paperId="paper_001",
        evidenceType="citation_entry",
        sourceModule="paper",
        sourcePath="refs.bib#vaswani2017attention",
        summary="vaswani2017attention: Attention is All You Need (NeurIPS, 2017)",
        metadata={
            "citationKey": "vaswani2017attention",
            "title": "Attention is All You Need",
            "venue": "NeurIPS",
        },
    )

    verifications = verify_claim_evidence(
        {"id": "paper_001", "briefJson": {}},
        [claim],
        [evidence],
        {"claim_001": ["evidence_001"]},
    )

    citation_checks = [item for item in verifications if item.verifierType == "citation_semantic"]
    assert citation_checks
    assert citation_checks[0].supportStatus == "unsupported"
    assert "domain_gap" in citation_checks[0].diagnostics["mismatchReasons"]
    assert citation_checks[0].diagnostics["lowConfidence"] is False


def test_citation_semantic_verifier_uses_abstract_to_avoid_false_positive():
    claim = Claim(
        id="claim_001",
        paperId="paper_001",
        text="Our model uses transformer attention for machine translation.",
        claimType="method",
        importance="medium",
        requiresEvidence=False,
        sourceSpan=SourceSpan(file="main.tex", section="Related Work", line=22),
        riskHints=["citation_key:vaswani2017attention"],
    )
    evidence = Evidence(
        id="evidence_001",
        paperId="paper_001",
        evidenceType="citation_entry",
        sourceModule="paper",
        sourcePath="refs.bib#vaswani2017attention",
        summary="vaswani2017attention: Attention is All You Need (NeurIPS, 2017)",
        metadata={
            "citationKey": "vaswani2017attention",
            "title": "Attention is All You Need",
            "venue": "NeurIPS",
            "abstract": "The Transformer architecture relies entirely on attention mechanisms and improves machine translation quality.",
        },
    )

    verifications = verify_claim_evidence(
        {"id": "paper_001", "briefJson": {}},
        [claim],
        [evidence],
        {"claim_001": ["evidence_001"]},
    )

    citation_checks = [item for item in verifications if item.verifierType == "citation_semantic"]
    assert citation_checks
    assert citation_checks[0].supportStatus == "weakly_supported"
    assert "abstract" in citation_checks[0].diagnostics["metadataFields"]
    assert citation_checks[0].diagnostics["overlapRatio"] > 0


def test_low_confidence_citation_gets_budget_routing_bonus():
    finding = _finding(
        severity="minor",
        confidence=0.6,
        supportStatus="unsupported",
        cemCalibration={
            "lowConfidenceCitation": True,
            "recommendedEscalation": "llm_citation_entailment",
        },
    )
    report = {
        "claimScores": [
            {
                "claimId": "claim_001",
                "mismatchScore": 0.4,
                "dimensions": {"coverage": 0.0, "baseline": 0.0, "numeric": 0.0, "guardrail": 0.0},
            }
        ]
    }

    plan = build_cem_budget_plan([finding], report, "balanced")

    assert plan["selectedFindingIds"] == ["finding_001"]
    assert "low_confidence_citation" in plan["allocations"][0]["drivers"]


def test_cem_budget_prioritizes_citation_mismatch_over_generic_blocker():
    citation_finding = _finding(
        id="finding_citation",
        severity="minor",
        riskType="citation_mismatch",
        supportStatus="unsupported",
        confidence=0.35,
    )
    generic_blocker = _finding(
        id="finding_generic",
        severity="blocker",
        riskType="unsupported_claim",
        supportStatus="unsupported",
        confidence=1.0,
    )
    report = {
        "claimScores": [
            {
                "claimId": "claim_001",
                "mismatchScore": 0.7,
                "dimensions": {"coverage": 0.2, "baseline": 0.0, "numeric": 0.0, "guardrail": 0.0},
            }
        ]
    }

    plan = build_cem_budget_plan([generic_blocker, citation_finding], report, "balanced")

    assert plan["allocations"][0]["findingId"] == "finding_citation"
    assert plan["allocations"][0]["escalationClass"] == 0


def test_cem_budget_prioritizes_high_stakes_citation_domain():
    high_stakes = _finding(
        id="finding_high_stakes",
        severity="minor",
        riskType="citation_mismatch",
        supportStatus="unsupported",
        confidence=0.35,
        cemCalibration={
            "citationSemantic": {
                "claimDomainTerms": ["clinical", "triage", "distribution", "shift"],
            }
        },
    )
    generic = _finding(
        id="finding_generic_citation",
        severity="minor",
        riskType="citation_mismatch",
        supportStatus="unsupported",
        confidence=0.58,
        cemCalibration={
            "citationSemantic": {
                "claimDomainTerms": ["robustness"],
            }
        },
    )
    report = {
        "claimScores": [
            {
                "claimId": "claim_001",
                "mismatchScore": 0.689,
                "dimensions": {"coverage": 0.2, "citation_semantic": 0.5},
            }
        ]
    }

    plan = build_cem_budget_plan([generic, high_stakes], report, "balanced")

    assert plan["allocations"][0]["findingId"] == "finding_high_stakes"
    assert "high_stakes_citation_domain" in plan["allocations"][0]["drivers"]


def test_bbl_bibliography_entries_are_collected():
    from app.modules.review.evidence_graph import build_evidence

    artifacts = {
        "paper": {"id": "paper_bbl", "briefJson": None},
        "latexFiles": [{
            "path": "main.bbl",
            "content": r"""
\begin{thebibliography}{1}
\bibitem[Asai et~al.(2023)]{asai2023selfrag}
Akari Asai et al.
\newblock Self-rag: Learning to retrieve, generate, and critique through self-reflection.
\newblock In \emph{ICLR}, 2024.
\newblock URL \url{https://arxiv.org/abs/2310.11511}.
\end{thebibliography}
""",
        }],
        "experiments": [],
        "codeArtifacts": [],
    }
    entries = [item for item in build_evidence(artifacts) if item.evidenceType == "citation_entry"]
    assert len(entries) == 1
    assert entries[0].metadata["citationKey"] == "asai2023selfrag"
    assert "Self-rag" in entries[0].metadata["title"]
    assert entries[0].metadata["year"] == "2024"


def test_external_missing_metric_is_artifact_absent():
    claim = Claim(
        id="claim_external",
        paperId="paper_external",
        text="The method improves accuracy by 12 percent.",
        claimType="performance",
        importance="high",
        requiresEvidence=True,
        sourceSpan=SourceSpan(file="main.tex", section="Results", line=10),
    )
    verifications = verify_claim_evidence(
        {"id": "paper_external", "briefJson": {}, "externalPaper": {"arxivId": "2400.00001"}},
        [claim],
        [],
        {claim.id: []},
    )
    numeric = next(item for item in verifications if item.verifierType == "numeric_metric")
    assert numeric.supportStatus == "artifact_absent"
    assert numeric.diagnostics["externalCalibration"]["originalSupportStatus"] == "unsupported"

    strict = verify_claim_evidence(
        {"id": "paper_external", "briefJson": {}, "externalPaper": {"arxivId": "2400.00001"}},
        [claim],
        [],
        {claim.id: []},
        calibrate_external=False,
    )
    strict_numeric = next(item for item in strict if item.verifierType == "numeric_metric")
    assert strict_numeric.supportStatus == "unsupported"


def test_external_low_confidence_citation_needs_human_verification():
    claim = Claim(
        id="claim_external",
        paperId="paper_external",
        text="Our optimizer uses a hierarchical protocol.",
        claimType="method",
        importance="medium",
        requiresEvidence=False,
        sourceSpan=SourceSpan(file="main.tex", section="Method", line=12),
        riskHints=["citation_key:unrelated2024"],
    )
    evidence = Evidence(
        id="evidence_external",
        paperId="paper_external",
        evidenceType="citation_entry",
        sourceModule="paper",
        sourcePath="refs.bib#unrelated2024",
        summary="unrelated2024: Sparse matrix decomposition",
        metadata={"citationKey": "unrelated2024", "title": "Sparse matrix decomposition"},
    )
    verifications = verify_claim_evidence(
        {"id": "paper_external", "briefJson": {}, "externalPaper": {"arxivId": "2400.00001"}},
        [claim],
        [evidence],
        {claim.id: [evidence.id]},
    )
    citation = next(item for item in verifications if item.verifierType == "citation_semantic")
    assert citation.supportStatus == "needs_human_verification"


def test_external_benchmark_claim_keeps_strict_unsupported_status():
    claim = Claim(
        id="claim_benchmark",
        paperId="paper_external_variant",
        text="CEM-Bench numeric stress claim: We improve accuracy by 97 percent.",
        claimType="performance",
        importance="high",
        requiresEvidence=True,
        sourceSpan=SourceSpan(file="main.tex", section="CEM-Bench Injected Claims", line=99),
    )
    verifications = verify_claim_evidence(
        {"id": "paper_external_variant", "briefJson": {}, "externalPaper": {"arxivId": "2400.00001"}},
        [claim],
        [],
        {claim.id: []},
    )
    numeric = next(item for item in verifications if item.verifierType == "numeric_metric")
    assert numeric.supportStatus == "unsupported"
