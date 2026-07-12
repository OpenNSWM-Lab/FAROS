from app.models.idea import Claim, ExperimentSpec, IdeaCandidate
from app.models.plan_package import (
    PlanBackground,
    PlanContributionStatement,
    PlanEvidenceRef,
    PlanEvidenceTrace,
    PlanExpectedMetric,
    PlanGap,
    PlanGapItem,
    PlanIdeaSummary,
    PlanLiteratureCoverage,
    PlanLiteraturePaperSummary,
    PlanLiteratureSurvey,
    PlanOutput,
    PlanPackage,
    PlanPrinciple,
    PlanSource,
    PlanStage,
    PlanStep,
)
from app.services.plan_package_builder import (
    build_contribution_statements,
    build_default_stages,
)
from app.services.plan_package_validator import validate_plan_package


def _candidate(**overrides):
    data = {
        "id": "cand_test",
        "sessionId": "idea_test",
        "title": "Citation-faithful refusal-aware RAG",
        "problem": "High-risk RAG QA needs citation faithfulness, abstention, and evidence traceability.",
        "hypothesisStatement": "A refusal-aware evidence verifier improves citation faithfulness and abstention decisions.",
        "keyInsight": "Jointly verify answer claims, citations, and insufficient-evidence refusal decisions.",
        "proposedMethod": (
            "Use an evidence verifier to align answer spans with cited passages, "
            "trigger refusal when retrieved evidence is insufficient, and emit an audit trail."
        ),
        "expectedOutcome": "Improve citation faithfulness and refusal accuracy while preserving traceability.",
    }
    data.update(overrides)
    return IdeaCandidate(**data)


def _survey_with_claim_metrics():
    return PlanLiteratureSurvey(
        summary="RAG evidence survey",
        coverage=PlanLiteratureCoverage(
            rawPaperCount=2,
            selectedPaperCount=2,
            structuredPaperCount=2,
        ),
        papers=[
            PlanLiteraturePaperSummary(
                paperId="raw_relevant",
                title="Citation-Enforced RAG with Abstention",
                source="structured",
                summary="Studies citation faithfulness, refusal, and provenance.",
                claims=[
                    {
                        "claimId": "cl_rel",
                        "text": "Citation faithfulness is measured with attribution precision.",
                        "claimType": "metric",
                    },
                    {
                        "claimId": "cl_refusal",
                        "text": "Refusal accuracy measures abstention on insufficient-evidence questions.",
                        "claimType": "metric",
                    }
                ],
            ),
            PlanLiteraturePaperSummary(
                paperId="raw_irrelevant",
                title="Attention projection diversity for vision transformers",
                source="structured",
                summary="Studies unrelated q/v attention projection structure.",
                claims=[
                    {
                        "claimId": "cl_bad",
                        "text": "The Vendi Score metric promotes semantic diversity in q/v attention projections.",
                        "claimType": "metric",
                    }
                ],
            ),
        ],
    )


def _gap():
    return PlanGap(
        summary="Gap",
        selectedGapId="gap-1",
        items=[
            PlanGapItem(
                id="gap-1",
                kind="selected",
                statement="Existing RAG methods do not jointly validate citation faithfulness, refusal, and traceability.",
                severity="high",
                existingCoverage="Prior work covers each part separately.",
                unresolvedIssue="Joint validation remains under-specified.",
                proposedEntry="A verifier links answer spans to citations and refusal decisions.",
                validationNeeds=["citation faithfulness", "refusal accuracy", "traceability score"],
                supportedByPaperIds=["raw_relevant"],
            )
        ],
    )


def test_default_stages_filter_unrelated_claim_metrics():
    stages = build_default_stages(
        candidate=_candidate(),
        literature_survey=_survey_with_claim_metrics(),
        gap=_gap(),
        principle=PlanPrinciple(summary="Verifier", mechanism="Verifier"),
        paper_type="algorithm",
        max_stages=3,
        max_steps_per_stage=3,
    )

    metrics = [
        expected.metric
        for stage in stages
        for step in stage.steps
        for expected in step.expected
    ]

    assert "citation faithfulness" in metrics
    assert "refusal accuracy" in metrics
    assert "Vendi Score" not in metrics
    assert not any("attention projection" in metric for metric in metrics)


def test_default_stages_prefer_candidate_experiment_metrics_over_claim_text():
    candidate = _candidate(
        experimentSpecs=[
            ExperimentSpec(
                name="High-risk QA evaluation",
                description="Evaluate verifier",
                metrics=["citation faithfulness", "refusal accuracy", "traceability score"],
                datasets=["LegalQA"],
            )
        ]
    )

    stages = build_default_stages(
        candidate=candidate,
        literature_survey=_survey_with_claim_metrics(),
        gap=_gap(),
        principle=PlanPrinciple(summary="Verifier", mechanism="Verifier"),
        paper_type="algorithm",
        max_stages=3,
        max_steps_per_stage=3,
    )
    validation_step = next(
        step
        for stage in stages
        for step in stage.steps
        if step.id == "step-3-1"
    )

    assert [item.metric for item in validation_step.expected] == [
        "citation faithfulness",
        "refusal accuracy",
        "traceability score",
    ]


def test_default_stages_keep_method_stage_tied_to_candidate_topic():
    stages = build_default_stages(
        candidate=_candidate(),
        literature_survey=_survey_with_claim_metrics(),
        gap=_gap(),
        principle=PlanPrinciple(
            summary="Citation and refusal verifier",
            mechanism="Verify cited spans and abstain on insufficient evidence.",
        ),
        paper_type="algorithm",
        max_stages=3,
        max_steps_per_stage=3,
    )

    method_stage = next(stage for stage in stages if stage.id == "stage-2")
    method_text = " ".join(
        [
            method_stage.title,
            method_stage.goal,
            method_stage.method,
            *[step.title + " " + step.desc + " " + step.method for step in method_stage.steps],
        ]
    ).lower()

    assert "citation" in method_text
    assert "refusal" in method_text


def test_default_stages_merge_grounding_method_and_validation_when_limited_to_one_stage():
    stages = build_default_stages(
        candidate=_candidate(),
        literature_survey=_survey_with_claim_metrics(),
        gap=_gap(),
        principle=PlanPrinciple(
            summary="Citation and refusal verifier",
            mechanism="Verify cited spans and abstain on insufficient evidence.",
        ),
        paper_type="algorithm",
        max_stages=1,
        max_steps_per_stage=3,
    )

    assert len(stages) == 1
    text = " ".join(
        [
            stages[0].title,
            stages[0].goal,
            stages[0].method,
            *[step.title + " " + step.desc + " " + step.method for step in stages[0].steps],
        ]
    ).lower()
    output_types = {output.type for step in stages[0].steps for output in step.outputs}
    metrics = {expected.metric for step in stages[0].steps for expected in step.expected}

    assert "citation" in text
    assert "refusal" in text
    assert {"checkpoint", "report", "metrics"} <= output_types
    assert {"citation faithfulness", "refusal accuracy"} <= metrics


def test_default_stages_single_step_keeps_all_plan_roles_and_valid_dependencies():
    common = {
        "candidate": _candidate(),
        "literature_survey": _survey_with_claim_metrics(),
        "gap": _gap(),
        "principle": PlanPrinciple(
            summary="Citation and refusal verifier",
            mechanism="Verify cited spans and abstain on insufficient evidence.",
        ),
        "paper_type": "algorithm",
        "max_steps_per_stage": 1,
    }

    one_stage = build_default_stages(max_stages=1, **common)
    two_stages = build_default_stages(max_stages=2, **common)
    one_step = one_stage[0].steps[0]
    output_types = {output.type for output in one_step.outputs}
    expected_metrics = {expected.metric for expected in one_step.expected}
    known_step_ids = {step.id for stage in two_stages for step in stage.steps}

    assert {"checkpoint", "report", "code", "metrics", "table"} <= output_types
    assert {"citation faithfulness", "refusal accuracy", "ablation_coverage"} <= expected_metrics
    assert all(
        dependency in known_step_ids
        for stage in two_stages
        for step in stage.steps
        for dependency in step.inputFrom
    )


def test_constrained_survey_and_benchmark_stages_preserve_roles_and_dependencies():
    for paper_type in ["survey", "benchmark"]:
        stages = build_default_stages(
            candidate=_candidate(),
            literature_survey=_survey_with_claim_metrics(),
            gap=_gap(),
            principle=PlanPrinciple(
                summary="Citation and refusal verifier",
                mechanism="Verify cited spans and abstain on insufficient evidence.",
            ),
            paper_type=paper_type,
            max_stages=1,
            max_steps_per_stage=1,
        )
        step = stages[0].steps[0]
        known_step_ids = {item.id for stage in stages for item in stage.steps}
        text = " ".join([stages[0].title, stages[0].goal, stages[0].method, step.desc, step.method]).lower()

        assert len(stages) == 1
        assert "citation" in text
        assert step.outputs
        assert step.expected
        assert all(dependency in known_step_ids for dependency in step.inputFrom)


def test_contribution_novelty_basis_replaces_heuristic_prior_work_placeholder():
    candidate = _candidate()
    stages = build_default_stages(
        candidate=candidate,
        literature_survey=_survey_with_claim_metrics(),
        gap=_gap(),
        principle=PlanPrinciple(
            summary="Verifier",
            mechanism="Verifier",
            noveltyClaim=(
                "The candidate proposes a method for the stated problem; "
                "this needs to be contrasted against the cited evidence papers."
            ),
        ),
        paper_type="algorithm",
    )

    contributions = build_contribution_statements(
        candidate=candidate,
        gap=_gap(),
        principle=PlanPrinciple(
            summary="Verifier",
            mechanism="Verifier",
            noveltyClaim=(
                "The candidate proposes a method for the stated problem; "
                "this needs to be contrasted against the cited evidence papers."
            ),
        ),
        stages=stages,
    )

    assert "needs to be contrasted" not in contributions[0].noveltyBasis
    assert "citation faithfulness" in contributions[0].noveltyBasis.lower()
    assert "refusal" in contributions[0].noveltyBasis.lower()


def test_validator_blocks_stage_topic_detachment():
    package = PlanPackage(
        packageId="ppkg_topic_detached",
        source=PlanSource(ideaSessionId="idea_test", ideaCandidateId="cand_test"),
        idea=PlanIdeaSummary(
            id="cand_test",
            title="Citation-faithful refusal-aware RAG",
            problem="High-risk RAG QA needs citation faithfulness and refusal decisions.",
            hypothesisStatement="Verifier improves citation faithfulness and refusal accuracy.",
            proposedMethod="Verify answer claims against cited passages and refuse insufficient evidence.",
            expectedOutcome="Higher citation faithfulness and refusal accuracy.",
        ),
        background=PlanBackground(
            summary="Clinical RAG systems need citation faithfulness.",
            evidenceRefs=[PlanEvidenceRef(type="paper", id="raw_relevant")],
        ),
        literatureSurvey=_survey_with_claim_metrics(),
        gap=_gap(),
        principle=PlanPrinciple(
            summary="Citation verifier",
            mechanism="Verify cited evidence and refusal decisions.",
            noveltyClaim="Joint citation and refusal validation.",
        ),
        contributionStatement=[
            PlanContributionStatement(
                id="contrib-1",
                type="method",
                statement="A citation-faithful refusal verifier.",
                validationStageIds=["stage-1"],
                validationStepIds=["step-1"],
                evidenceRefs=[PlanEvidenceRef(type="paper", id="raw_relevant")],
            )
        ],
        researchQuestion="Can citation-faithful RAG improve high-risk QA?",
        hypothesis="Citation verification improves refusal and traceability.",
        constants={"seedQuery": "citation-faithful medical RAG for high-risk clinical QA", "paperType": "algorithm"},
        stages=[
            PlanStage(
                id="stage-1",
                order=1,
                title="Warehouse inventory setup",
                goal="Configure shelves and bins",
                method="Count items and arrange labels",
                steps=[
                    PlanStep(
                        id="step-1",
                        order=1,
                        title="Shelf sorting",
                        desc="Sort warehouse boxes by aisle",
                        method="Manual inventory checklist",
                        outputs=[PlanOutput(type="metrics", name="baseline.json")],
                        expected=[
                            PlanExpectedMetric(
                                metric="citation faithfulness",
                                target="specified before implementation",
                            )
                        ],
                        evidenceRefs=[PlanEvidenceRef(type="gap", id="gap-1")],
                    )
                ],
            ),
            PlanStage(
                id="stage-2",
                order=2,
                title="Garden watering trial",
                goal="Measure irrigation timing",
                method="Compare watering schedules",
                dependsOn=["stage-1"],
                steps=[
                    PlanStep(
                        id="step-2",
                        order=1,
                        title="Watering schedule",
                        desc="Record soil moisture",
                        method="Garden checklist",
                        outputs=[PlanOutput(type="table", name="ablation.csv")],
                        expected=[
                            PlanExpectedMetric(
                                metric="refusal accuracy",
                                target="specified before implementation",
                            )
                        ],
                        inputFrom=["step-1"],
                        evidenceRefs=[PlanEvidenceRef(type="candidate", id="cand_test")],
                    )
                ],
            ),
        ],
        evidenceTrace=PlanEvidenceTrace(
            ideaCandidateId="cand_test",
            selectedPaperIds=["raw_relevant"],
            structuredPaperIds=["raw_relevant"],
            reasoningTrace=[{"step": "candidate", "id": "cand_test"}],
        ),
        rawIdeaOutputs={"ideaCandidate": {"proposedMethod": "Verify answer claims against cited passages and refuse insufficient evidence."}},
        sourceFields={"idea": ["candidate"], "background": ["paper"], "gap": ["literature"], "principle": ["candidate"]},
    )

    gate = validate_plan_package(package)

    assert gate.evidenceValid is False
    assert any("no visible overlap" in error for error in gate.errors)
