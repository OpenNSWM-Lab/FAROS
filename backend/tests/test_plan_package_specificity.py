from app.services.plan_package_specificity import (
    hypothesis_is_falsifiable,
    metric_target_is_concrete,
    plan_specificity_issues,
)
from app.services.plan_package_reviewers import metric_reviewer
from app.services.plan_package_validator import validate_plan_package


def test_metric_target_rejects_generic_placeholders():
    for target in [
        "specified before implementation",
        "primary_metric",
        "readiness",
        "higher",
        "better",
    ]:
        assert metric_target_is_concrete(target) is False


def test_metric_target_accepts_measurable_or_baseline_relative_criteria():
    for target in [
        ">= strongest baseline",
        "mean_delta > 0 with 95% confidence interval excluding 0",
        "non-inferior to control within 1 percentage point",
        "failure rate <= 0.05",
        "true",
    ]:
        assert metric_target_is_concrete(target) is True


def test_hypothesis_requires_direction_measure_and_falsifier():
    assert hypothesis_is_falsifiable("The verifier should improve the system.") is False
    assert hypothesis_is_falsifiable(
        "Compared with vanilla RAG, claim verification increases citation faithfulness; "
        "the hypothesis is rejected if the mean delta is not positive on the preregistered split."
    ) is True


def test_specificity_issues_point_to_exact_metric_path(plan_package):
    plan_package.stages[2].steps[0].expected[0].target = "better"

    issues = plan_specificity_issues(plan_package)

    assert any(
        issue.sectionPath == "stages[2].steps[0].expected[0].target"
        for issue in issues
    )


def test_specificity_is_diagnostic_in_validator_and_blocking_in_metric_review(plan_package):
    plan_package.stages[2].steps[0].expected[0].target = "better"

    gate = validate_plan_package(plan_package)
    report = metric_reviewer(plan_package)

    assert any(
        warning.startswith("specificity.stages[2].steps[0].expected[0].target")
        for warning in gate.warnings
    )
    assert any(
        issue.sectionPath == "stages[2].steps[0].expected[0].target"
        for issue in report.blockingIssues
    )
