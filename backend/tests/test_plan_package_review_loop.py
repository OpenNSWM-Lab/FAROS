import json
from types import SimpleNamespace

from app.models.plan_package import (
    PlanMetaReview,
    PlanQualityGate,
    PlanReviewerIssue,
    PlanReviewerReport,
)
from app.services.plan_package_review_loop import (
    dedupe_review_issues,
    repair_stage_ids,
    repair_targets,
    route_review_issue,
)
from app.services.plan_package_reviewers import apply_review_to_quality_gate
from app.services.plan_package_service import PlanPackageService
from app.services.plan_package_validator import validate_plan_package
from app.services.plan_package_views import build_plan_package_presentation


def _issue(path, message, severity="blocking"):
    return PlanReviewerIssue(
        id=f"issue-{path}-{message}",
        severity=severity,
        sectionPath=path,
        message=message,
    )


def test_review_issue_routing_keeps_plan_owned_repairs_internal():
    metric_issue = _issue(
        "stages[1].steps[0].expected[0].target",
        "Metric target is generic.",
    )
    upstream_issue = _issue(
        "evidenceTrace.pathSeedId",
        "Required upstream path seed is missing.",
    )

    assert route_review_issue(metric_issue) == "plan_repairable"
    assert route_review_issue(upstream_issue) == "upstream_blocking"
    assert repair_targets([metric_issue]) == ["stages"]
    assert repair_stage_ids(
        [metric_issue],
        ["stage-1", "stage-2", "stage-3"],
    ) == ["stage-2"]


def test_review_issue_deduplication_merges_reviewer_wording():
    issues = dedupe_review_issues(
        [
            _issue("hypothesis", "Hypothesis lacks a falsification condition."),
            _issue(
                "hypothesis",
                "The hypothesis has no falsifiable rejection condition.",
            ),
        ]
    )

    assert len(issues) == 1
    assert issues[0].sectionPath == "hypothesis"


def test_internal_reviewer_diagnostic_is_not_copied_to_quality_gate(plan_package):
    gate = validate_plan_package(plan_package)
    diagnostic = PlanReviewerReport(
        reviewer="MetricReviewer",
        score=0.8,
        passed=True,
        warnings=[
            _issue(
                "reviewReports",
                "LLM reviewer unavailable for MetricReviewer: provider timeout",
                severity="warning",
            )
        ],
    )

    gate = apply_review_to_quality_gate(
        plan_package,
        gate,
        extra_reports=[diagnostic],
    )

    assert not any("LLM reviewer unavailable" in item for item in gate.warnings)
    assert any(
        "LLM reviewer unavailable" in issue.message
        for report in plan_package.reviewReports
        for issue in report.warnings
    )


def test_stage_repair_does_not_rewrite_unaffected_stages(monkeypatch, plan_package):
    before_stage_1 = plan_package.stages[0].model_dump(mode="json")
    before_stage_3 = plan_package.stages[2].model_dump(mode="json")
    repaired_stage = plan_package.stages[1].model_copy(deep=True)
    repaired_stage.title = "Verifier implementation with frozen interfaces"

    class FakeClient:
        def chat(self, **_kwargs):
            return SimpleNamespace(
                text=json.dumps({"stage": repaired_stage.model_dump(mode="json")})
            )

    monkeypatch.setattr(
        "app.services.plan_package_service.get_provider_client",
        lambda _name: FakeClient(),
    )
    monkeypatch.setattr(
        "app.services.plan_package_service.get_llm_task_scheduler",
        lambda: SimpleNamespace(run=lambda _name, fn, **_kwargs: fn()),
    )
    service = PlanPackageService()
    session = SimpleNamespace(
        config=SimpleNamespace(providerName="fake", model="fake-model")
    )

    service._repair_llm_stage_fields(
        plan_package,
        session,
        stage_ids=["stage-2"],
        max_steps_per_stage=3,
    )

    assert plan_package.stages[0].model_dump(mode="json") == before_stage_1
    assert plan_package.stages[1].title == (
        "Verifier implementation with frozen interfaces"
    )
    assert plan_package.stages[2].model_dump(mode="json") == before_stage_3


def test_resolved_reviewer_issue_moves_to_revision_audit(monkeypatch, plan_package):
    old_message = "Metric target is generic."
    issue = _issue(
        "stages[2].steps[0].expected[0].target",
        old_message,
    )
    plan_package.metaReview = PlanMetaReview(
        overallScore=0.5,
        decision="revise",
        blockingIssues=[issue],
        requiredRepairs=["Replace the generic target."],
    )
    plan_package.reviewReports = [
        PlanReviewerReport(
            reviewer="MetricReviewer",
            score=0.5,
            passed=False,
            blockingIssues=[issue],
        )
    ]
    plan_package.qualityGate = PlanQualityGate(
        schemaValid=True,
        evidenceValid=True,
        planSpecific=False,
        errors=[f"{issue.sectionPath}: {old_message}"],
    )
    service = PlanPackageService()

    def fake_stage_repair(package, _session, **_kwargs):
        package.stages[2].steps[0].expected[0].target = (
            "mean_delta > 0 with 95% confidence interval excluding 0"
        )
        package.generation.repairRounds += 1

    def fake_review(package, gate, *, reviewer_mode):
        assert reviewer_mode == "hybrid"
        gate.topicRelevant = True
        gate.citationFaithful = True
        gate.planSpecific = True
        gate.downstreamReady = True
        gate.agentApproved = True
        gate.implementationReady = True
        gate.errors = []
        gate.warnings = []
        package.metaReview = PlanMetaReview(
            overallScore=0.9,
            decision="approve",
            blockingIssues=[],
            warnings=[],
        )
        package.reviewReports = [
            PlanReviewerReport(
                reviewer="MetricReviewer",
                score=0.9,
                passed=True,
            )
        ]
        return gate

    monkeypatch.setattr(service, "_repair_llm_stage_fields", fake_stage_repair)
    monkeypatch.setattr(service, "_apply_review_mode", fake_review)
    monkeypatch.setattr(
        "app.services.plan_package_service.validate_plan_package",
        lambda _package: PlanQualityGate(schemaValid=True, evidenceValid=True),
    )
    monkeypatch.setattr(
        "app.services.plan_package_service.build_contribution_statements",
        lambda **_kwargs: plan_package.contributionStatement,
    )
    session = SimpleNamespace(
        config=SimpleNamespace(providerName="fake", model="fake-model")
    )

    service._auto_repair_plan_from_review(
        plan_package,
        session,
        max_stages=3,
        max_steps_per_stage=3,
        max_repair_rounds=2,
        reviewer_mode="hybrid",
    )

    assert old_message not in " ".join(plan_package.qualityGate.errors)
    assert plan_package.metaReview.blockingIssues == []
    assert old_message in json.dumps(
        plan_package.revisions[-1].patchSummary["reviewReportsBeforeRepair"]
    )


def test_presentation_hides_internal_reviewer_diagnostics(plan_package):
    diagnostic = _issue(
        "reviewReports",
        "LLM reviewer unavailable for MetricReviewer: provider timeout",
        severity="warning",
    )
    plan_package.generation.warnings = ["segment_fallback:stage-2:TimeoutError"]
    plan_package.qualityGate.warnings = [
        "reviewReports: LLM reviewer unavailable for MetricReviewer"
    ]
    plan_package.metaReview = PlanMetaReview(
        overallScore=0.8,
        decision="revise",
        warnings=[diagnostic],
    )

    presentation = build_plan_package_presentation(plan_package)
    rendered = " ".join(
        [
            *presentation.reviewSummary.mainConcerns,
            *presentation.reviewSummary.requiredFixes,
            *presentation.evidenceSummary.weakPoints,
            *presentation.nextActions,
        ]
    ).lower()

    assert "metricreviewer" not in rendered
    assert "timeout" not in rendered
    assert "provider" not in rendered
    assert "schema" not in rendered


def test_presentation_consolidates_unresolved_user_actions(plan_package):
    issues = [
        _issue(
            "evidenceTrace.pathSeedId",
            "Required upstream evidence path is missing.",
        ),
        _issue(
            "literatureSurvey",
            "Evidence coverage is insufficient.",
        ),
        _issue(
            "constants.resourceBudget",
            "User must choose the compute budget.",
        ),
        _issue(
            "stages[2].steps[0].expected[0].target",
            "Metric target is generic.",
        ),
    ]
    plan_package.metaReview = PlanMetaReview(
        overallScore=0.4,
        decision="revise",
        blockingIssues=issues,
    )

    presentation = build_plan_package_presentation(plan_package)

    assert len(presentation.reviewSummary.mainConcerns) == 3
    assert len(presentation.reviewSummary.requiredFixes) == 3
    assert len(set(presentation.reviewSummary.mainConcerns)) == 3


def test_successful_presentation_has_no_review_problem_list(plan_package):
    plan_package.qualityGate.agentApproved = True
    plan_package.qualityGate.implementationReady = True
    plan_package.qualityGate.errors = []
    plan_package.qualityGate.warnings = []
    plan_package.metaReview = PlanMetaReview(
        overallScore=0.9,
        decision="approve",
    )

    presentation = build_plan_package_presentation(plan_package)

    assert presentation.reviewSummary.mainConcerns == []
    assert presentation.reviewSummary.requiredFixes == []
