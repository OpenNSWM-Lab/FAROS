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


def make_plan_package() -> PlanPackage:
    paper_id = "paper-rag-1"
    return PlanPackage(
        packageId="ppkg_test",
        source=PlanSource(
            ideaSessionId="idea-test",
            ideaCandidateId="cand-test",
            searchTreeId="tree-test",
            searchNodeId="node-test",
            pathSeedId="path-test",
            literatureMapId="lit-test",
        ),
        idea=PlanIdeaSummary(
            id="cand-test",
            title="Citation-faithful refusal-aware RAG",
            problem="High-risk QA needs faithful citations and reliable refusal.",
            hypothesisStatement="Claim verification improves citation faithfulness.",
            proposedMethod="Verify answer claims against cited passages and refuse unsupported answers.",
            expectedOutcome="Higher citation faithfulness without weaker refusal accuracy.",
            closestPriorWork=[{"paperId": paper_id, "method": "vanilla RAG"}],
        ),
        background=PlanBackground(
            summary="High-risk RAG requires claim-level evidence verification.",
            motivation="Unsupported clinical answers can cause harmful decisions.",
            currentLimitations=["Vanilla RAG can cite passages that do not support answer claims."],
            evidenceRefs=[PlanEvidenceRef(type="paper", id=paper_id, source="structured")],
        ),
        literatureSurvey=PlanLiteratureSurvey(
            summary="Prior work studies citation attribution and abstention separately.",
            coverage=PlanLiteratureCoverage(
                rawPaperCount=1,
                selectedPaperCount=1,
                structuredPaperCount=1,
            ),
            papers=[
                PlanLiteraturePaperSummary(
                    paperId=paper_id,
                    structuredPaperId=paper_id,
                    source="structured",
                    title="Citation Attribution for Retrieval-Augmented Generation",
                    summary="Evaluates claim-level citation faithfulness against vanilla RAG.",
                    relevanceScore=0.95,
                    relevanceSignals=["citation", "faithfulness", "RAG", "baseline"],
                    relevanceReason="Directly supports the selected method and metric.",
                    methods=[{"name": "vanilla RAG", "role": "baseline"}],
                    claims=[
                        {
                            "claimId": "claim-rag-1",
                            "text": "Attribution precision measures citation support.",
                        }
                    ],
                )
            ],
        ),
        gap=PlanGap(
            summary="Joint citation verification and refusal evaluation remains under-specified.",
            selectedGapId="gap-1",
            items=[
                PlanGapItem(
                    id="gap-1",
                    kind="selected",
                    statement="Existing RAG evaluations do not jointly test claim support and refusal.",
                    severity="high",
                    existingCoverage="Citation and refusal are usually evaluated separately.",
                    unresolvedIssue="Their interaction on insufficient-evidence questions remains unclear.",
                    proposedEntry="Add a claim verifier with an evidence-aware refusal rule.",
                    boundary="High-risk question answering with retrieved textual evidence.",
                    validationNeeds=["citation faithfulness", "refusal accuracy"],
                    whyUnsolved="Existing evaluations lack a joint protocol.",
                    supportedByPaperIds=[paper_id],
                    supportedByClaimIds=["claim-rag-1"],
                )
            ],
        ),
        principle=PlanPrinciple(
            summary="Verify every answer claim before allowing a supported response.",
            mechanism="Align claims to cited spans and refuse when support is insufficient.",
            noveltyClaim="Jointly bind citation attribution and refusal to the same evidence test.",
            assumptions=[
                "The retrieved corpus contains answer-supporting evidence when the question is answerable."
            ],
            risks=["An over-conservative verifier can reduce answer coverage."],
        ),
        contributionStatement=[
            PlanContributionStatement(
                id="contribution-1",
                type="method",
                statement="A joint claim-verification and refusal mechanism.",
                noveltyBasis="Prior work evaluates citation and refusal separately.",
                validationStageIds=["stage-3"],
                validationStepIds=["step-3-1"],
                evidenceRefs=[PlanEvidenceRef(type="paper", id=paper_id, source="structured")],
            )
        ],
        researchQuestion="Can claim verification improve citation faithfulness over vanilla RAG on high-risk QA?",
        hypothesis=(
            "Compared with vanilla RAG, claim verification increases citation faithfulness; "
            "reject the hypothesis if the mean delta is not positive on the preregistered split."
        ),
        constants={
            "seedQuery": "citation-faithful medical RAG for high-risk clinical question answering",
            "paperType": "algorithmic_method",
            "baseline": "vanilla RAG",
            "datasetProtocol": "fixed preregistered train, validation, and test split",
            "randomSeeds": [13, 29, 47],
        },
        stages=[
            PlanStage(
                id="stage-1",
                order=1,
                title="Evidence and baseline grounding",
                goal="Bind the selected GAP to the strongest available comparison.",
                method="Use the selected paper and GAP to define vanilla RAG as the control.",
                steps=[
                    PlanStep(
                        id="step-1-1",
                        order=1,
                        title="Freeze baseline scope",
                        desc="Record the control method and shared evaluation inputs.",
                        method="Use vanilla RAG with the same corpus, prompts, split, and evaluator.",
                        outputs=[
                            PlanOutput(
                                type="table",
                                name="baseline_scope.csv",
                                requiredFor=["validation", "paper"],
                            )
                        ],
                        expected=[PlanExpectedMetric(metric="baseline_count", target=">= 1")],
                        evidenceRefs=[
                            PlanEvidenceRef(type="paper", id=paper_id, source="structured")
                        ],
                    )
                ],
            ),
            PlanStage(
                id="stage-2",
                order=2,
                title="Verifier implementation",
                goal="Specify the claim verifier and refusal rule.",
                method="Map answer claims to cited spans and emit supported, unsupported, or refuse.",
                dependsOn=["stage-1"],
                steps=[
                    PlanStep(
                        id="step-2-1",
                        order=1,
                        title="Implement claim verification",
                        desc="Define inputs, outputs, and refusal thresholds.",
                        method="Apply the same verifier configuration to every preregistered run.",
                        inputFrom=["step-1-1"],
                        outputs=[
                            PlanOutput(
                                type="code",
                                name="claim_verifier.py",
                                requiredFor=["code", "validation"],
                            )
                        ],
                        expected=[PlanExpectedMetric(metric="artifact_count", target=">= 1")],
                        evidenceRefs=[
                            PlanEvidenceRef(type="candidate", id="cand-test", source="idea")
                        ],
                    )
                ],
            ),
            PlanStage(
                id="stage-3",
                order=3,
                title="Controlled validation",
                goal="Test citation faithfulness and refusal accuracy against vanilla RAG.",
                method="Run paired evaluation on the same preregistered split and random seeds.",
                dependsOn=["stage-2"],
                steps=[
                    PlanStep(
                        id="step-3-1",
                        order=1,
                        title="Measure primary outcomes",
                        desc="Compare paired citation and refusal metrics.",
                        method="Report mean deltas and 95% confidence intervals across fixed seeds.",
                        inputFrom=["step-2-1"],
                        outputs=[
                            PlanOutput(
                                type="metrics",
                                name="validation_metrics.json",
                                requiredFor=["validation", "paper"],
                            )
                        ],
                        expected=[
                            PlanExpectedMetric(
                                metric="citation faithfulness",
                                target="mean_delta > 0 with 95% confidence interval excluding 0",
                            ),
                            PlanExpectedMetric(
                                metric="refusal accuracy",
                                target=">= vanilla RAG on the preregistered split",
                            ),
                        ],
                        evidenceRefs=[
                            PlanEvidenceRef(type="gap", id="gap-1", source="literature_map")
                        ],
                    )
                ],
            ),
        ],
        evidenceTrace=PlanEvidenceTrace(
            ideaCandidateId="cand-test",
            searchNodeId="node-test",
            pathSeedId="path-test",
            literatureMapId="lit-test",
            selectedPaperIds=[paper_id],
            structuredPaperIds=[paper_id],
            reasoningTrace=[
                {"from": "gap-1", "to": "cand-test", "relation": "addressed_by"}
            ],
        ),
    )
