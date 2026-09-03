from app.modules.review.cem_guidance import annotate_risk_tree_with_mismatch
from app.modules.review.cem_guidance import build_cem_budget_plan
from app.modules.review import artifact_collector
from app.modules.review.evidence_verifier import verify_claim_evidence
from app.modules.review.mismatch_scorer import build_mismatch_report
from app.modules.review.model_router import (
    _apply_additional_findings,
    _build_severity_budget_plan,
    rank_findings_for_review,
)
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


def test_artifact_collector_keeps_large_export_and_discovers_nested_source(monkeypatch, tmp_path):
    project_id = "cproj_artifacts"
    project_dir = tmp_path / "code_projects" / project_id
    exports_dir = project_dir / "exports"
    repo_dir = project_dir / "repo"
    exports_dir.mkdir(parents=True)
    (exports_dir / "project.zip").write_bytes(b"x" * 250_001)
    (repo_dir / "src" / "package").mkdir(parents=True)
    (repo_dir / "src" / "package" / "model.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (repo_dir / "configs").mkdir()
    (repo_dir / "configs" / "experiment.yaml").write_text("seed: 42\n", encoding="utf-8")
    (repo_dir / ".venv").mkdir()
    (repo_dir / ".venv" / "ignored.py").write_text("secret = True\n", encoding="utf-8")

    monkeypatch.setattr(artifact_collector, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(artifact_collector, "_BASE_DIR", str(tmp_path.parent))
    monkeypatch.setattr(
        artifact_collector,
        "get_paper",
        lambda _paper_id: {
            "id": "paper_artifacts",
            "projectId": project_id,
            "experimentIds": [],
        },
    )
    monkeypatch.setattr(artifact_collector, "list_paper_files", lambda _paper_id: [])

    result = artifact_collector.collect_reviewx_artifacts("paper_artifacts")
    names = {item["name"] for item in result["codeArtifacts"]}
    paths = {item["path"] for item in result["codeArtifacts"]}

    assert {"project.zip", "model.py", "experiment.yaml"} <= names
    assert not any(".venv" in path for path in paths)
    export = next(item for item in result["codeArtifacts"] if item["name"] == "project.zip")
    assert export["content"] == ""
    assert export["contentOmitted"] is True
    assert export["sizeBytes"] == 250_001


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


def _guardrail_checks(claim_text: str, avoid_claims: list[str]):
    claim = Claim(
        id="claim_guardrail",
        paperId="paper_guardrail",
        text=claim_text,
        claimType="robustness",
        importance="high",
        requiresEvidence=False,
        sourceSpan=SourceSpan(file="main.tex", section="Limitations", line=8),
    )
    return [
        item
        for item in verify_claim_evidence(
            {"id": "paper_guardrail", "briefJson": {"avoid_claims": avoid_claims}},
            [claim],
            [],
            {claim.id: []},
        )
        if item.verifierType == "brief_guardrail"
    ]


def test_guardrail_accepts_fixed_seed_synthetic_limitation_statement():
    checks = _guardrail_checks(
        "We evaluate only on a fixed-seed synthetic benchmark and do not claim external validity.",
        [
            "Do not claim the benchmark is anything other than fixed-seed synthetic.",
            "Do not claim external, real-world, or human validation.",
        ],
    )

    assert checks == []


def test_guardrail_rejects_affirmative_external_validation():
    checks = _guardrail_checks(
        "The method was validated on an external real-world benchmark.",
        ["Do not claim external validation."],
    )

    assert len(checks) == 1
    assert checks[0].supportStatus == "contradicted"


def test_guardrail_rejects_unsupported_rate_improvement():
    checks = _guardrail_checks(
        "Our method reduces the unsupported claim rate relative to the baseline.",
        ["Do not claim that unsupported claim rate improved."],
    )

    assert len(checks) == 1


def test_guardrail_accepts_disclosed_nonzero_calibration_error():
    checks = _guardrail_checks(
        "The method has a non-zero ECE of 0.29, so calibration remains a limitation.",
        ["Do not hide or ignore the non-zero ECE."],
    )

    assert checks == []


def test_guardrail_accepts_ece_increase_from_zero_to_nonzero():
    checks = _guardrail_checks(
        "ECE increased from 0.0 for the baseline to approximately 0.292 for our method.",
        ["Do not ignore or hide the non-zero ECE of the proposed method."],
    )

    assert checks == []


def test_guardrail_accepts_future_external_validation_as_limitation():
    checks = _guardrail_checks(
        "Future work must extend evaluation to real-world datasets to ascertain generalizability.",
        [
            "Do not claim external, real-world, or human validation.",
            "Do not claim the method generalizes to real-world reviews without further validation.",
        ],
    )

    assert checks == []


def test_guardrail_allows_high_metrics_without_state_of_the_art_claim():
    checks = _guardrail_checks(
        "The method maintains high AUROC and F1 while improving calibration on this benchmark.",
        ["Do not claim the method achieves state-of-the-art F1-Score or AUROC."],
    )

    assert checks == []


def test_guardrail_allows_specific_ece_improvement_when_metrics_are_mixed():
    checks = _guardrail_checks(
        "ECE decreases from 0.2699 for the baseline to 0.2259 for our method.",
        [
            "Do not claim the method outperforms the baseline in all metrics; "
            "explicitly acknowledge the lower F1 score."
        ],
    )

    assert checks == []


def test_guardrail_rejects_blanket_all_metric_outperformance():
    checks = _guardrail_checks(
        "Our method outperforms the baseline across all metrics.",
        [
            "Do not claim the method outperforms the baseline in all metrics; "
            "explicitly acknowledge the lower F1 score."
        ],
    )

    assert len(checks) == 1


def test_guardrail_allows_calibration_improvement_without_elimination_claim():
    checks = _guardrail_checks(
        "The method reduces ECE from 0.270 to 0.226 on the synthetic benchmark.",
        [
            "Do not claim the method eliminates hallucinations; frame it as improved "
            "detection and calibration."
        ],
    )

    assert checks == []


def test_baseline_verifier_ignores_explicit_external_validity_limitation():
    claim = Claim(
        id="claim_limitation",
        paperId="paper_limitation",
        text=(
            "These results should not be generalized to claim superiority over "
            "state-of-the-art systems without further validation."
        ),
        claimType="robustness",
        importance="medium",
        requiresEvidence=False,
        sourceSpan=SourceSpan(file="results.tex", section="Limitations", line=20),
    )

    checks = verify_claim_evidence(
        {"id": "paper_limitation", "briefJson": {}}, [claim], [], {claim.id: []},
    )

    assert not any(item.verifierType == "baseline_coverage" for item in checks)


def test_numeric_verifier_recomputes_relative_metric_improvement():
    claim = Claim(
        id="claim_relative",
        paperId="paper_relative",
        text="The method reduces ECE by approximately 38%.",
        claimType="performance",
        importance="high",
        requiresEvidence=True,
        sourceSpan=SourceSpan(file="results.tex", section="Results", line=5),
    )
    evidence = [
        Evidence(
            id="metric_baseline_ece",
            paperId="paper_relative",
            evidenceType="metric",
            sourceModule="experiment",
            sourcePath="metrics.json",
            summary="baseline:ECE = 0.269982",
            metadata={"metricName": "baseline:Expected Calibration Error (ECE)", "value": 0.269982},
        ),
        Evidence(
            id="metric_method_ece",
            paperId="paper_relative",
            evidenceType="metric",
            sourceModule="experiment",
            sourcePath="metrics.json",
            summary="method:ECE = 0.167472",
            metadata={"metricName": "method:Expected Calibration Error (ECE)", "value": 0.167472},
        ),
    ]

    verifications = verify_claim_evidence(
        {"id": "paper_relative", "briefJson": {}},
        [claim],
        evidence,
        {claim.id: [item.id for item in evidence]},
    )
    numeric = next(item for item in verifications if item.verifierType == "numeric_metric")

    assert numeric.supportStatus == "supported"
    assert "baseline/method values" in numeric.verdict


def test_own_experiment_result_does_not_require_bibliography_citation():
    claim = Claim(
        id="claim_own_result",
        paperId="paper_own_result",
        text="Our method achieves an ECE of 0.167 on the frozen benchmark.",
        claimType="performance",
        importance="high",
        requiresEvidence=True,
        sourceSpan=SourceSpan(file="results.tex", section="Results", line=9),
    )
    metric = Evidence(
        id="metric_method_ece",
        paperId="paper_own_result",
        evidenceType="metric",
        sourceModule="experiment",
        sourcePath="metrics.json",
        summary="method:ECE = 0.167",
        metadata={"metricName": "method:Expected Calibration Error (ECE)", "value": 0.167},
    )

    verifications = verify_claim_evidence(
        {"id": "paper_own_result", "briefJson": {}},
        [claim],
        [metric],
        {claim.id: [metric.id]},
    )
    citation = next(item for item in verifications if item.verifierType == "citation_context")

    assert citation.supportStatus == "supported"


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


def test_review_findings_are_ranked_by_recorded_cem_priority():
    low_priority_blocker = _finding(id="finding_blocker", severity="blocker", confidence=1.0)
    high_priority_citation = _finding(
        id="finding_citation", severity="minor", confidence=0.4, riskType="citation_mismatch"
    )
    trace = {
        "budgetAllocations": [
            {"findingId": "finding_blocker", "priority": 0.6},
            {"findingId": "finding_citation", "priority": 0.9},
        ]
    }

    ranked = rank_findings_for_review([low_priority_blocker, high_priority_citation], trace)

    assert [finding.id for finding in ranked] == ["finding_citation", "finding_blocker"]


def test_review_finding_rank_falls_back_to_severity_and_confidence():
    minor = _finding(id="finding_minor", severity="minor", confidence=0.5)
    major = _finding(id="finding_major", severity="major", confidence=0.7)

    ranked = rank_findings_for_review([minor, major], {})

    assert [finding.id for finding in ranked] == ["finding_major", "finding_minor"]


def test_llm_specific_gap_merges_into_generic_finding_and_is_not_discarded():
    claim = _claim()
    generic = _finding(
        severity="minor",
        riskType="citation_uncertainty",
        title="Performance claim needs human verification",
        description="No imported FAROS artifact is available.",
        confidence=0.42,
        supportStatus="artifact_absent",
    )

    applications = _apply_additional_findings(
        [generic],
        [{
            "claimId": claim.id,
            "severity": "major",
            "riskType": "unsupported_claim",
            "supportStatus": "needs_human_verification",
            "title": "Unverified first-of-its-kind claim",
            "description": "The novelty claim needs a concrete comparison with prior work.",
            "targetModule": "papers",
            "suggestedFix": "Add a scoped literature comparison or weaken the novelty claim.",
            "confidence": 0.86,
        }],
        [claim],
        "qwen-test",
        claim.paperId,
    )

    assert applications == [{
        "claimId": claim.id,
        "findingId": generic.id,
        "outcome": "merged",
    }]
    assert generic.title == "Unverified first-of-its-kind claim"
    assert generic.severity == "major"
    assert generic.riskType == "unsupported_claim"
    assert generic.confidence == 0.86
    assert generic.reviewerDecision == "partially_valid"
    assert generic.cemCalibration["llmMergedFinding"] is True
    assert "Local evidence context" in generic.description


def test_llm_merged_finding_uses_updated_risk_when_ranked():
    generic = _finding(
        id="finding_generic",
        severity="major",
        confidence=0.86,
        cemCalibration={"llmMergedFinding": True, "llmFactor": 1.0},
    )
    unchanged = _finding(id="finding_unchanged", severity="minor", confidence=0.5)
    trace = {
        "budgetAllocations": [
            {"findingId": generic.id, "priority": 0.31},
            {"findingId": unchanged.id, "priority": 0.72},
        ]
    }

    ranked = rank_findings_for_review([unchanged, generic], trace)

    assert [finding.id for finding in ranked] == [generic.id, unchanged.id]


def test_llm_specific_gap_does_not_overwrite_existing_specific_finding():
    claim = _claim()
    existing = _finding(
        id="finding_contradiction",
        severity="major",
        riskType="contradiction",
        supportStatus="contradicted",
        title="Measured result contradicts the paper claim",
    )

    applications = _apply_additional_findings(
        [existing],
        [{
            "claimId": claim.id,
            "severity": "minor",
            "riskType": "citation_uncertainty",
            "supportStatus": "needs_human_verification",
            "title": "Citation scope is unclear",
            "description": "The citation does not establish the full scope of the claim.",
            "targetModule": "papers",
            "suggestedFix": "Narrow the claim or add a direct citation.",
            "confidence": 0.72,
        }],
        [claim],
        "qwen-test",
        claim.paperId,
    )

    assert len(applications) == 1
    assert applications[0]["outcome"] == "added"
    assert applications[0]["findingId"] != existing.id
    assert existing.title == "Measured result contradicts the paper claim"


def test_llm_gap_cannot_claim_contradiction_without_direct_evidence():
    claim = _claim()
    findings = []

    _apply_additional_findings(
        findings,
        [{
            "claimId": claim.id,
            "severity": "major",
            "riskType": "citation_mismatch",
            "supportStatus": "contradicted",
            "title": "Citation may not support the claim",
            "description": "The cited scope appears narrower than the claim.",
            "confidence": 0.84,
        }],
        [claim],
        "qwen-test",
        claim.paperId,
    )

    assert findings[0].supportStatus == "needs_human_verification"
    assert findings[0].reviewerDecision == "partially_valid"
    assert findings[0].cemCalibration["llmDecision"] == "partially_valid"


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


def test_comparison_claim_links_role_specific_metrics_and_audit():
    from app.modules.review.evidence_graph import build_evidence, link_claims_to_evidence

    claim = Claim(
        id="claim_comparison",
        paperId="paper_metrics",
        text="The baseline F1 is 0.80 while our method reaches 1.0.",
        claimType="performance",
        importance="high",
        requiresEvidence=True,
        sourceSpan=SourceSpan(file="main.tex", section="Results", line=18),
    )
    artifacts = {
        "paper": {"id": "paper_metrics", "briefJson": {}},
        "latexFiles": [],
        "experiments": [],
        "codeArtifacts": [],
        "experimentEvidence": {
            "status": "executed",
            "codeHash": "sha256:code",
            "environmentHash": "sha256:environment",
            "codeRunId": "code_1",
            "metrics": [
                {"name": "baseline_f1_score", "value": 0.8, "definition": "Baseline F1.", "split": "test"},
                {"name": "method_f1_score", "value": 1.0, "definition": "Method F1.", "split": "test"},
            ],
            "metricAudit": {
                "status": "passed",
                "sourcePath": "evaluation_records.json",
                "positiveClass": "unsupported",
                "recordCount": 100,
                "errors": [],
            },
        },
    }

    evidence = build_evidence(artifacts)
    links = link_claims_to_evidence([claim], evidence)
    linked = [item for item in evidence if item.id in links[claim.id]]

    linked_metric_names = {item.metadata.get("metricName") for item in linked}
    assert {"baseline_f1_score", "method_f1_score"} <= linked_metric_names
    assert any(item.evidenceType == "metric_audit" for item in linked)

    verifications = verify_claim_evidence(
        artifacts["paper"], [claim], evidence, links,
    )
    audit = next(item for item in verifications if item.verifierType == "metric_semantics")
    assert audit.supportStatus == "supported"


def test_failed_metric_audit_contradicts_performance_claim():
    claim = _claim()
    audit_evidence = Evidence(
        id="evidence_audit",
        paperId=claim.paperId,
        evidenceType="metric_audit",
        sourceModule="experiment",
        sourcePath="evaluation_records.json",
        summary="Independent metric audit status=failed.",
        confidence=0.99,
        metadata={"status": "failed", "errors": ["method_f1_score reports 1.0 but recomputed 0.5"]},
    )

    verifications = verify_claim_evidence(
        {"id": claim.paperId, "briefJson": {}},
        [claim],
        [audit_evidence],
        {claim.id: [audit_evidence.id]},
    )

    audit = next(item for item in verifications if item.verifierType == "metric_semantics")
    assert audit.supportStatus == "contradicted"


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
