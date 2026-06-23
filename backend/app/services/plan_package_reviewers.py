"""Hybrid reviewer committee for PlanPackage quality control."""

from __future__ import annotations

import re
import uuid
from statistics import mean
from typing import Callable, Iterable, List, Optional

from app.models.plan_package import (
    PlanEvidenceRef,
    PlanMetaReview,
    PlanPackage,
    PlanQualityGate,
    PlanReviewerIssue,
    PlanReviewerReport,
)


def _issue(
    message: str,
    *,
    section_path: str = "",
    severity: str = "warning",
    evidence_refs: Iterable[PlanEvidenceRef] | None = None,
) -> PlanReviewerIssue:
    return PlanReviewerIssue(
        id=f"pri_{uuid.uuid4().hex[:10]}",
        severity=severity,
        sectionPath=section_path,
        message=message,
        evidenceRefs=list(evidence_refs or []),
    )


def _clamp_score(value: float) -> float:
    return max(0.0, min(1.0, round(value, 3)))


_STOPWORDS = {
    "about", "against", "also", "among", "and", "based", "between", "can",
    "could", "does", "for", "from", "how", "into", "large", "language",
    "learning", "method", "methods", "model", "models", "paper", "plan",
    "research", "should", "study", "than", "that", "the", "their", "this",
    "through", "using", "what", "when", "where", "with", "within", "would",
    "是否", "如何", "研究", "方法", "模型", "系统",
}


def _topic_terms(package: PlanPackage) -> list[str]:
    text = " ".join([
        str(package.constants.get("seedQuery", "")),
        str(package.constants.get("domain", "")),
        package.researchQuestion,
        package.hypothesis,
        package.idea.title,
        package.idea.problem,
        package.idea.hypothesisStatement,
        package.idea.proposedMethod,
        package.idea.expectedOutcome,
        package.gap.summary,
        package.principle.summary,
        package.principle.mechanism,
        package.principle.noveltyClaim,
    ]).lower().replace("-", " ")
    if "rag" in text:
        text = f"{text} retrieval augmented generation"
    terms: list[str] = []
    for token in re.findall(r"[a-zA-Z][a-zA-Z0-9]{2,}|[\u4e00-\u9fff]{2,}", text):
        if token in _STOPWORDS:
            continue
        if token not in terms:
            terms.append(token)
    return terms[:28]


def _hit_count(text: str, terms: list[str]) -> int:
    lowered = text.lower().replace("-", " ")
    return sum(1 for term in terms if term and term.lower() in lowered)


def _paper_text(paper) -> str:
    return " ".join([
        paper.title,
        paper.summary,
        " ".join(str(item) for item in paper.methods),
        " ".join(str(item) for item in paper.findings),
        " ".join(str(item) for item in paper.limitations),
        " ".join(str(item) for item in paper.claims),
    ])


def _plan_text(package: PlanPackage) -> str:
    chunks: list[str] = []
    for stage in package.stages:
        chunks.extend([stage.title, stage.goal, stage.method])
        for step in stage.steps:
            chunks.extend([
                step.title,
                step.desc,
                step.method,
                " ".join(f"{output.name} {output.desc}" for output in step.outputs),
                " ".join(f"{expected.metric} {expected.target} {expected.desc}" for expected in step.expected),
            ])
    return " ".join(chunks)


def _make_report(
    reviewer: str,
    score: float,
    blocking: list[PlanReviewerIssue],
    warnings: list[PlanReviewerIssue],
    suggestions: list[str],
    evidence_refs: Iterable[PlanEvidenceRef] | None = None,
) -> PlanReviewerReport:
    return PlanReviewerReport(
        reviewer=reviewer,
        score=_clamp_score(score),
        passed=not blocking,
        blockingIssues=blocking,
        warnings=warnings,
        repairSuggestions=suggestions,
        evidenceRefs=list(evidence_refs or []),
    )


def _dedupe_text(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _merge_rule_and_llm_report(rule_report: PlanReviewerReport, llm_report: PlanReviewerReport) -> PlanReviewerReport:
    """Merge deterministic rule checks with the same-dimension LLM semantic review."""

    score = _clamp_score(0.45 * rule_report.score + 0.55 * llm_report.score)
    return PlanReviewerReport(
        reviewer=rule_report.reviewer,
        score=score,
        passed=rule_report.passed and llm_report.passed,
        blockingIssues=[*rule_report.blockingIssues, *llm_report.blockingIssues],
        warnings=[*rule_report.warnings, *llm_report.warnings],
        repairSuggestions=_dedupe_text([*rule_report.repairSuggestions, *llm_report.repairSuggestions]),
        evidenceRefs=[*rule_report.evidenceRefs, *llm_report.evidenceRefs],
    )


def relevance_reviewer(package: PlanPackage) -> PlanReviewerReport:
    terms = _topic_terms(package)
    blocking: list[PlanReviewerIssue] = []
    warnings: list[PlanReviewerIssue] = []
    suggestions: list[str] = []
    if len(terms) < 3:
        warnings.append(_issue("Topic anchors are too sparse to judge relevance.", section_path="researchQuestion"))
        return _make_report("RelevanceReviewer", 0.55, blocking, warnings, suggestions)

    has_builder_scores = any(
        paper.relevanceReason or paper.relevanceSignals or paper.relevanceScore > 0
        for paper in package.literatureSurvey.papers
    )
    if has_builder_scores:
        relevant_count = sum(1 for paper in package.literatureSurvey.papers if paper.relevanceScore >= 0.45)
        strong_count = sum(1 for paper in package.literatureSurvey.papers if paper.relevanceScore >= 0.70)
        low_relevance = [
            paper
            for paper in package.literatureSurvey.papers
            if paper.source in {"structured", "probe"} and paper.relevanceScore < 0.25
        ]
    else:
        paper_hits = [
            (paper.paperId, _hit_count(_paper_text(paper), terms))
            for paper in package.literatureSurvey.papers
        ]
        relevant_count = sum(1 for _, hits in paper_hits if hits >= 2)
        strong_count = sum(1 for _, hits in paper_hits if hits >= 3)
        low_relevance = []
    plan_hits = _hit_count(_plan_text(package), terms)
    paper_ratio = relevant_count / max(1, len(package.literatureSurvey.papers))
    plan_ratio = min(1.0, plan_hits / max(3, min(8, len(terms) // 2)))
    score = 0.55 * paper_ratio + 0.45 * plan_ratio

    if len(low_relevance) >= max(2, len(package.literatureSurvey.papers) // 2):
        warnings.append(_issue(
            "Many investigated papers have low builder relevance scores.",
            section_path="literatureSurvey.papers[].relevanceScore",
        ))
        suggestions.append("Ask the user to confirm seed papers or rerun search with stricter topic anchors.")

    if package.literatureSurvey.papers and relevant_count == 0:
        blocking.append(_issue(
            "No investigated paper is clearly aligned with the seed topic.",
            section_path="literatureSurvey.papers",
            severity="blocking",
        ))
        suggestions.append("Re-run literature search or ask the user to confirm/remove unrelated papers before PlanPackage approval.")
    elif package.literatureSurvey.papers and strong_count == 0:
        warnings.append(_issue(
            "Investigated papers have weak topic overlap; evidence may be polluted by generic LLM/NLP papers.",
            section_path="literatureSurvey.papers",
        ))
        suggestions.append("Raise the paper relevance threshold or add user-confirmed seed papers.")

    if plan_hits < 2:
        blocking.append(_issue(
            "Implementation stages do not visibly preserve the selected idea topic.",
            section_path="stages",
            severity="blocking",
        ))
        suggestions.append("Regenerate only stages/steps with the original seed query and selected idea pinned in the prompt.")

    return _make_report("RelevanceReviewer", score, blocking, warnings, suggestions)


def evidence_reviewer(package: PlanPackage) -> PlanReviewerReport:
    blocking: list[PlanReviewerIssue] = []
    warnings: list[PlanReviewerIssue] = []
    suggestions: list[str] = []
    selected_gap = next((item for item in package.gap.items if item.id == package.gap.selectedGapId), None)
    if not selected_gap:
        blocking.append(_issue("Selected GAP is missing.", section_path="gap.selectedGapId", severity="blocking"))
    elif not selected_gap.supportedByPaperIds and not selected_gap.supportedByClaimIds:
        blocking.append(_issue(
            "Selected GAP has no paper or claim support.",
            section_path=f"gap.items[{selected_gap.id}]",
            severity="blocking",
        ))
        suggestions.append("Link the selected GAP to concrete paper limitations, claims, or graph signals.")

    if not package.background.evidenceRefs:
        warnings.append(_issue("Background has no evidenceRefs.", section_path="background.evidenceRefs"))
    if not package.principle.reasoningPath and not package.evidenceTrace.reasoningTrace:
        warnings.append(_issue("Principle has no visible reasoning path.", section_path="principle.reasoningPath"))

    total_steps = sum(len(stage.steps) for stage in package.stages)
    steps_with_refs = sum(
        1
        for stage in package.stages
        for step in stage.steps
        if step.evidenceRefs
    )
    if total_steps and steps_with_refs / total_steps < 0.5:
        warnings.append(_issue(
            "Less than half of plan steps have evidenceRefs.",
            section_path="stages[].steps[].evidenceRefs",
        ))
        suggestions.append("Attach candidate/gap/principle/paper refs to each core plan step.")

    score = 0.35
    if selected_gap and (selected_gap.supportedByPaperIds or selected_gap.supportedByClaimIds):
        score += 0.25
    if package.literatureSurvey.papers:
        score += 0.15
    if package.evidenceTrace.reasoningTrace or package.principle.reasoningPath:
        score += 0.15
    if total_steps:
        score += 0.10 * (steps_with_refs / total_steps)

    return _make_report("EvidenceReviewer", score, blocking, warnings, suggestions)


def feasibility_reviewer(package: PlanPackage) -> PlanReviewerReport:
    blocking: list[PlanReviewerIssue] = []
    warnings: list[PlanReviewerIssue] = []
    suggestions: list[str] = []
    if not package.stages:
        blocking.append(_issue("No implementation stages are defined.", section_path="stages", severity="blocking"))
        return _make_report("FeasibilityReviewer", 0.0, blocking, warnings, suggestions)

    generic_tokens = {"readiness", "primary_metric", "specified before implementation", "default plan step"}
    total_steps = 0
    detailed_steps = 0
    artifact_steps = 0
    metric_steps = 0
    for stage in package.stages:
        if not stage.steps:
            blocking.append(_issue(f"{stage.id} has no steps.", section_path=f"stages[{stage.id}].steps", severity="blocking"))
        for step in stage.steps:
            total_steps += 1
            text = f"{step.title} {step.desc} {step.method}".lower()
            if len(step.desc.strip()) >= 40 and len(step.method.strip()) >= 30 and not any(token in text for token in generic_tokens):
                detailed_steps += 1
            if step.outputs:
                artifact_steps += 1
            if step.expected and not all(
                expected.metric.lower() in generic_tokens or expected.target.lower() in generic_tokens
                for expected in step.expected
            ):
                metric_steps += 1

    if total_steps and detailed_steps / total_steps < 0.67:
        blocking.append(_issue(
            "Too many steps are generic or underspecified.",
            section_path="stages[].steps",
            severity="blocking",
        ))
        suggestions.append("Regenerate plan steps with concrete datasets, baselines, artifacts, and expected metrics.")
    if total_steps and metric_steps / total_steps < 0.67:
        warnings.append(_issue(
            "Many steps have generic expected metrics.",
            section_path="stages[].steps[].expected",
        ))
    if not package.constants:
        warnings.append(_issue("No constants are declared for downstream execution.", section_path="constants"))

    score = 0.20
    if total_steps:
        score += 0.35 * (detailed_steps / total_steps)
        score += 0.20 * (artifact_steps / total_steps)
        score += 0.20 * (metric_steps / total_steps)
    if package.constants:
        score += 0.05
    return _make_report("FeasibilityReviewer", score, blocking, warnings, suggestions)


def metric_reviewer(package: PlanPackage) -> PlanReviewerReport:
    blocking: list[PlanReviewerIssue] = []
    warnings: list[PlanReviewerIssue] = []
    suggestions: list[str] = []
    expected_items = [
        expected
        for stage in package.stages
        for step in stage.steps
        for expected in step.expected
    ]
    if not expected_items:
        blocking.append(_issue("No expected metrics are defined.", section_path="stages[].steps[].expected", severity="blocking"))
        return _make_report("MetricReviewer", 0.0, blocking, warnings, suggestions)

    generic = {"readiness", "primary_metric", "specified before implementation", "planned_metric"}
    def is_concrete_target(target: str) -> bool:
        normalized = target.strip().lower()
        if normalized in generic:
            return False
        if re.search(r"(>=|<=|>|<|=|±|\d)", normalized):
            return True
        return len(normalized) >= 6

    concrete = [
        item
        for item in expected_items
        if item.metric.strip().lower() not in generic
        and is_concrete_target(item.target)
    ]
    topic_terms = _topic_terms(package)
    metric_hits = _hit_count(
        " ".join(f"{item.metric} {item.target} {item.desc}" for item in expected_items),
        topic_terms,
    )
    if len(concrete) / len(expected_items) < 0.5:
        blocking.append(_issue(
            "Expected metrics are too generic for downstream validation.",
            section_path="stages[].steps[].expected",
            severity="blocking",
        ))
        suggestions.append("Add task-specific accuracy, faithfulness, latency, cost, robustness, or ablation metrics.")
    if topic_terms and metric_hits == 0:
        warnings.append(_issue(
            "Expected metrics do not mention topic-specific anchors.",
            section_path="stages[].steps[].expected",
        ))

    score = 0.25 + 0.65 * (len(concrete) / len(expected_items))
    if metric_hits > 0:
        score += 0.10
    return _make_report("MetricReviewer", score, blocking, warnings, suggestions)


def novelty_reviewer(package: PlanPackage) -> PlanReviewerReport:
    blocking: list[PlanReviewerIssue] = []
    warnings: list[PlanReviewerIssue] = []
    suggestions: list[str] = []
    selected_gap = next((item for item in package.gap.items if item.id == package.gap.selectedGapId), None)
    if not selected_gap:
        blocking.append(_issue("Selected GAP is missing.", section_path="gap", severity="blocking"))
    else:
        required_gap_fields = [
            selected_gap.existingCoverage,
            selected_gap.unresolvedIssue,
            selected_gap.proposedEntry,
            selected_gap.boundary,
        ]
        missing = sum(1 for value in required_gap_fields if not value.strip())
        if missing:
            blocking.append(_issue(
                "Selected GAP does not fully explain existing coverage, unresolved issue, entry point, and boundary.",
                section_path=f"gap.items[{selected_gap.id}]",
                severity="blocking",
            ))

    if not package.principle.noveltyClaim.strip():
        warnings.append(_issue("Principle has no noveltyClaim.", section_path="principle.noveltyClaim"))
    if not package.contributionStatement:
        blocking.append(_issue("No contributionStatement is defined.", section_path="contributionStatement", severity="blocking"))
    elif len(package.contributionStatement) < 2:
        warnings.append(_issue("Contribution statement is very thin.", section_path="contributionStatement"))

    score = 0.25
    if selected_gap:
        score += 0.30
        if selected_gap.existingCoverage and selected_gap.unresolvedIssue and selected_gap.proposedEntry and selected_gap.boundary:
            score += 0.15
    if package.principle.noveltyClaim:
        score += 0.15
    if package.contributionStatement:
        score += min(0.15, 0.05 * len(package.contributionStatement))
    return _make_report("NoveltyReviewer", score, blocking, warnings, suggestions)


REVIEWERS: list[Callable[[PlanPackage], PlanReviewerReport]] = [
    relevance_reviewer,
    evidence_reviewer,
    feasibility_reviewer,
    metric_reviewer,
    novelty_reviewer,
]


def run_plan_package_review(
    package: PlanPackage,
    extra_reports: Optional[List[PlanReviewerReport]] = None,
) -> tuple[list[PlanReviewerReport], PlanMetaReview]:
    rule_reports = [reviewer(package) for reviewer in REVIEWERS]
    extra_by_reviewer = {
        report.reviewer: report
        for report in (extra_reports or [])
    }
    merged_names: set[str] = set()
    reports: list[PlanReviewerReport] = []
    for report in rule_reports:
        extra = extra_by_reviewer.get(report.reviewer)
        if extra:
            reports.append(_merge_rule_and_llm_report(report, extra))
            merged_names.add(report.reviewer)
        else:
            reports.append(report)
    reports.extend(
        report
        for report in (extra_reports or [])
        if report.reviewer not in merged_names
    )
    blocking = [
        issue
        for report in reports
        for issue in report.blockingIssues
    ]
    warnings = [
        issue
        for report in reports
        for issue in report.warnings
    ]
    required_repairs: list[str] = []
    for report in reports:
        required_repairs.extend(report.repairSuggestions)
    overall_score = _clamp_score(mean([report.score for report in reports]) if reports else 0.0)
    decision = "approve"
    if blocking:
        decision = "revise" if overall_score >= 0.35 else "reject"
    elif overall_score < 0.72:
        decision = "revise"
    confidence = _clamp_score(0.45 + overall_score * 0.45 + (0.10 if not blocking else -0.10))
    meta = PlanMetaReview(
        overallScore=overall_score,
        decision=decision,
        confidence=confidence,
        blockingIssues=blocking,
        warnings=warnings,
        requiredRepairs=required_repairs,
        reviewerScores={report.reviewer: report.score for report in reports},
    )
    return reports, meta


def apply_review_to_quality_gate(
    package: PlanPackage,
    gate: PlanQualityGate,
    extra_reports: Optional[List[PlanReviewerReport]] = None,
) -> PlanQualityGate:
    reports, meta = run_plan_package_review(package, extra_reports=extra_reports)
    package.reviewReports = reports
    package.metaReview = meta

    report_map = {report.reviewer: report for report in reports}
    topic_relevant = report_map.get("RelevanceReviewer").passed if "RelevanceReviewer" in report_map else False
    evidence_passed = report_map.get("EvidenceReviewer").passed if "EvidenceReviewer" in report_map else False
    feasibility_passed = report_map.get("FeasibilityReviewer").passed if "FeasibilityReviewer" in report_map else False
    metrics_passed = report_map.get("MetricReviewer").passed if "MetricReviewer" in report_map else False
    novelty_passed = report_map.get("NoveltyReviewer").passed if "NoveltyReviewer" in report_map else False

    gate.topicRelevant = bool(topic_relevant)
    gate.citationFaithful = bool(gate.evidenceValid and evidence_passed)
    gate.planSpecific = bool(feasibility_passed and metrics_passed)
    gate.agentApproved = bool(meta.decision == "approve" and novelty_passed)
    gate.overallScore = meta.overallScore
    gate.reviewDecision = meta.decision
    gate.implementationReady = bool(
        gate.schemaValid
        and gate.evidenceValid
        and gate.topicRelevant
        and gate.citationFaithful
        and gate.planSpecific
        and gate.agentApproved
    )

    review_error_messages = [
        f"{issue.sectionPath}: {issue.message}" if issue.sectionPath else issue.message
        for issue in meta.blockingIssues
    ]
    review_warning_messages = [
        f"{issue.sectionPath}: {issue.message}" if issue.sectionPath else issue.message
        for issue in meta.warnings
    ]
    existing_errors = set(gate.errors)
    existing_warnings = set(gate.warnings)
    gate.errors.extend(message for message in review_error_messages if message not in existing_errors)
    gate.warnings.extend(message for message in review_warning_messages if message not in existing_warnings)
    return gate
