"""View builders for human-readable and downstream PlanPackage projections."""

from __future__ import annotations

from typing import Any, Dict, List

from app.models.plan_package import (
    PlanHandoffEvidenceTrace,
    PlanPackage,
    PlanPackageHandoff,
    PlanPackagePresentation,
    PlanPresentationBackground,
    PlanPresentationDebugRef,
    PlanPresentationEvidenceSummary,
    PlanPresentationGap,
    PlanPresentationLiterature,
    PlanPresentationMethod,
    PlanPresentationReviewSummary,
    PlanReadablePaper,
    PlanReadableStage,
    PlanReadableStep,
)


def _compact_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("summary", "claimText", "finding", "method", "name", "desc", "description"):
            if key in value and str(value[key]).strip():
                return str(value[key]).strip()
    return str(value).strip()


def _selected_gap(package: PlanPackage):
    return next(
        (item for item in package.gap.items if item.id == package.gap.selectedGapId),
        package.gap.items[0] if package.gap.items else None,
    )


def _readable_paper(paper, supports: List[str] | None = None) -> PlanReadablePaper:
    return PlanReadablePaper(
        paperId=paper.paperId,
        title=paper.title,
        source=paper.source,
        relevanceScore=paper.relevanceScore,
        summary=paper.summary,
        methods=[_compact_text(item) for item in paper.methods[:3] if _compact_text(item)],
        findings=[_compact_text(item) for item in paper.findings[:3] if _compact_text(item)],
        limitations=paper.limitations[:3],
        supports=supports or [],
    )


def _key_papers(package: PlanPackage, limit: int = 6) -> List[PlanReadablePaper]:
    selected = _selected_gap(package)
    support_ids = set(selected.supportedByPaperIds if selected else [])
    papers = sorted(
        package.literatureSurvey.papers,
        key=lambda paper: (
            paper.paperId in support_ids,
            paper.relevanceScore,
            bool(paper.limitations),
        ),
        reverse=True,
    )
    readable: List[PlanReadablePaper] = []
    for paper in papers[:limit]:
        supports: List[str] = []
        if paper.paperId in support_ids:
            supports.append("Supports the selected GAP")
        if paper.limitations:
            supports.append("Provides limitation context")
        readable.append(_readable_paper(paper, supports=supports))
    return readable


def _weak_papers(package: PlanPackage, limit: int = 4) -> List[PlanReadablePaper]:
    papers = [
        paper
        for paper in package.literatureSurvey.papers
        if paper.relevanceReason and paper.relevanceScore < 0.45
    ]
    return [_readable_paper(paper, supports=["Needs human confirmation"]) for paper in papers[:limit]]


def _readable_stages(package: PlanPackage) -> List[PlanReadableStage]:
    stages: List[PlanReadableStage] = []
    for stage in package.stages:
        steps: List[PlanReadableStep] = []
        for step in stage.steps:
            steps.append(
                PlanReadableStep(
                    id=step.id,
                    order=step.order,
                    title=step.title,
                    description=step.desc,
                    method=step.method,
                    outputs=[
                        {
                            "type": str(output.type.value if hasattr(output.type, "value") else output.type),
                            "name": output.name,
                            "description": output.desc,
                        }
                        for output in step.outputs
                    ],
                    expected=[
                        {
                            "metric": expected.metric,
                            "target": expected.target,
                            "description": expected.desc,
                        }
                        for expected in step.expected
                    ],
                )
            )
        stages.append(
            PlanReadableStage(
                id=stage.id,
                order=stage.order,
                title=stage.title,
                goal=stage.goal,
                method=stage.method,
                dependsOn=stage.dependsOn,
                steps=steps,
            )
        )
    return stages


def _confidence(package: PlanPackage) -> str:
    if (
        package.qualityGate.evidenceValid
        and package.qualityGate.topicRelevant
        and package.qualityGate.citationFaithful
        and package.qualityGate.overallScore >= 0.80
    ):
        return "high"
    if package.qualityGate.evidenceValid and package.qualityGate.overallScore >= 0.60:
        return "medium"
    return "low"


def _review_concerns(package: PlanPackage) -> List[str]:
    concerns = [
        issue.message
        for issue in (package.metaReview.blockingIssues if package.metaReview else [])
    ]
    if not concerns:
        concerns.extend(package.qualityGate.errors[:4])
    if not concerns and package.metaReview:
        concerns.extend(issue.message for issue in package.metaReview.warnings[:4])
    return concerns[:6]


def _next_actions(package: PlanPackage) -> List[str]:
    if package.status == "approved" or str(package.status).endswith("APPROVED"):
        return [
            "Hand off the compact package to code, paper, and review modules.",
            "Keep the full package available only for audit/debug traceability.",
        ]
    if package.qualityGate.agentApproved:
        return [
            "Ask a human owner to approve or add targeted feedback.",
            "Use the Revise action for any required wording or metric fixes.",
        ]
    return [
        "Resolve reviewer blocking issues before downstream handoff.",
        "Add targeted human feedback, then run Revise and Review again.",
    ]


def build_plan_package_presentation(package: PlanPackage) -> PlanPackagePresentation:
    selected = _selected_gap(package)
    key_papers = _key_papers(package)
    weak_papers = _weak_papers(package)
    stage_count = len(package.stages)
    step_count = sum(len(stage.steps) for stage in package.stages)
    executive_summary = (
        f"{package.idea.title or 'The selected idea'} addresses {package.researchQuestion.strip()} "
        f"with a {stage_count}-stage, {step_count}-step implementation plan. "
        f"The selected gap is: {(selected.statement if selected else package.gap.summary).strip()} "
        f"The proposed mechanism is: {(package.principle.mechanism or package.idea.proposedMethod).strip()}"
    )

    evidence_weak_points = list(package.qualityGate.errors[:4])
    evidence_weak_points.extend(package.qualityGate.warnings[:4])
    evidence_weak_points.extend(
        f"{issue.module}: {issue.message}"
        for issue in package.downstreamReadiness.blockingIssues[:4]
    )
    if selected and selected.whyUnsolved:
        evidence_weak_points.append(selected.whyUnsolved)

    return PlanPackagePresentation(
        packageId=package.packageId,
        packageStatus=str(package.status.value if hasattr(package.status, "value") else package.status),
        title=package.idea.title or package.researchQuestion,
        executiveSummary=executive_summary,
        researchQuestion=package.researchQuestion,
        hypothesis=package.hypothesis,
        background=PlanPresentationBackground(
            summary=package.background.summary,
            whyValuable=package.background.motivation or package.idea.expectedOutcome,
            currentLimitations=package.background.currentLimitations,
            scope=package.background.domainContext,
        ),
        gap=PlanPresentationGap(
            statement=selected.statement if selected else package.gap.summary,
            existingCoverage=selected.existingCoverage if selected else "",
            unresolvedIssue=selected.unresolvedIssue if selected else "",
            proposedEntry=selected.proposedEntry if selected else "",
            boundary=selected.boundary if selected else "",
            validationNeeds=selected.validationNeeds if selected else [],
        ),
        method=PlanPresentationMethod(
            principle=package.principle.summary,
            mechanism=package.principle.mechanism,
            noveltyClaim=package.principle.noveltyClaim,
            contributions=[item.statement for item in package.contributionStatement],
            assumptions=package.principle.assumptions,
            risks=package.principle.risks,
        ),
        literature=PlanPresentationLiterature(
            summary=package.literatureSurvey.summary,
            keyPapers=key_papers,
            weakOrUnconfirmedPapers=weak_papers,
        ),
        implementationPlan=_readable_stages(package),
        evidenceSummary=PlanPresentationEvidenceSummary(
            confidence=_confidence(package),
            summary=(
                f"{len(key_papers)} key papers are surfaced for user-facing evidence. "
                f"Reviewer score is {package.qualityGate.overallScore:.2f}."
            ),
            supportingPapers=key_papers[:4],
            weakPoints=evidence_weak_points[:8],
        ),
        reviewSummary=PlanPresentationReviewSummary(
            decision=package.qualityGate.reviewDecision,
            score=package.qualityGate.overallScore,
            mainConcerns=_review_concerns(package),
            requiredFixes=(package.metaReview.requiredRepairs[:6] if package.metaReview else []),
            reviewerMode=package.generation.reviewerMode,
            llmReviewerUsed=package.generation.llmReviewerUsed,
        ),
        nextActions=_next_actions(package),
        debug=PlanPresentationDebugRef(
            fullPackageEndpoint=f"/api/v1/plans/packages/{package.packageId}",
            packageId=package.packageId,
            ideaSessionId=package.source.ideaSessionId,
            ideaCandidateId=package.source.ideaCandidateId,
        ),
    )


def build_plan_package_handoff(package: PlanPackage) -> PlanPackageHandoff:
    selected = _selected_gap(package)
    if selected is None:
        raise ValueError("PlanPackage has no selected GAP for handoff")
    return PlanPackageHandoff(
        packageId=package.packageId,
        status=str(package.status.value if hasattr(package.status, "value") else package.status),
        idea=package.idea,
        researchQuestion=package.researchQuestion,
        hypothesis=package.hypothesis,
        constants=package.constants,
        backgroundSummary=package.background.summary,
        selectedGap=selected,
        principle=package.principle,
        contributionStatement=package.contributionStatement,
        keyPapers=_key_papers(package, limit=10),
        stages=package.stages,
        qualityGate=package.qualityGate,
        downstreamReadiness=package.downstreamReadiness,
        evidenceTrace=PlanHandoffEvidenceTrace(
            ideaCandidateId=package.evidenceTrace.ideaCandidateId,
            searchNodeId=package.evidenceTrace.searchNodeId,
            pathSeedId=package.evidenceTrace.pathSeedId,
            reasoningKgId=package.evidenceTrace.reasoningKgId,
            literatureMapId=package.evidenceTrace.literatureMapId,
            selectedPaperIds=package.evidenceTrace.selectedPaperIds,
            structuredPaperIds=package.evidenceTrace.structuredPaperIds,
            probePaperIds=package.evidenceTrace.probePaperIds,
        ),
        downstreamContract=package.downstreamContract,
    )
