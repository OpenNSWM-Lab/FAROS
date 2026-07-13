"""Internal reviewer issue routing for PlanPackage repair and presentation."""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Literal

from app.models.plan_package import PlanPackage, PlanReviewerIssue


IssueRoute = Literal[
    "plan_repairable",
    "upstream_blocking",
    "user_decision_required",
    "diagnostic_only",
]

_DIAGNOSTIC_TERMS = {
    "timeout",
    "provider",
    "non-json",
    "invalid json",
    "llm reviewer unavailable",
    "repair round",
}
_USER_DECISION_TERMS = {
    "user must choose",
    "human decision",
    "budget approval",
    "scope decision",
    "owner confirmation",
}


def route_review_issue(issue: PlanReviewerIssue) -> IssueRoute:
    path = issue.sectionPath.lower()
    text = issue.message.lower()
    if path.startswith("reviewreports") or any(
        term in text for term in _DIAGNOSTIC_TERMS
    ):
        return "diagnostic_only"
    if any(term in text for term in _USER_DECISION_TERMS):
        return "user_decision_required"
    if path.startswith(
        (
            "evidencetrace",
            "source.search",
            "source.path",
            "literaturesurvey",
            "gap",
            "principle",
        )
    ):
        return "upstream_blocking"
    return "plan_repairable"


def _issue_category(message: str) -> str:
    text = message.lower()
    categories = {
        "hypothesis_falsifiability": [
            "falsif",
            "rejection condition",
            "reject the hypothesis",
        ],
        "metric_target": ["metric target", "expected target", "generic target"],
        "baseline": ["baseline", "control comparison"],
        "evidence": ["evidence", "citation", "paper id", "claim id"],
        "topic": ["topic", "seed query", "drift", "relevance"],
    }
    for category, terms in categories.items():
        if any(term in text for term in terms):
            return category
    tokens = re.findall(r"[a-z0-9_]+", text)
    return " ".join(tokens[:5])


def _fingerprint(issue: PlanReviewerIssue) -> tuple[str, str]:
    return issue.sectionPath.lower(), _issue_category(issue.message)


def dedupe_review_issues(
    issues: list[PlanReviewerIssue],
) -> list[PlanReviewerIssue]:
    grouped: OrderedDict[tuple[str, str], PlanReviewerIssue] = OrderedDict()
    for issue in issues:
        key = _fingerprint(issue)
        existing = grouped.get(key)
        if existing is None or (
            existing.severity != "blocking" and issue.severity == "blocking"
        ):
            grouped[key] = issue
    return list(grouped.values())


def repair_targets(issues: list[PlanReviewerIssue]) -> list[str]:
    targets: list[str] = []
    for issue in issues:
        if route_review_issue(issue) != "plan_repairable":
            continue
        path = issue.sectionPath.lower()
        if path.startswith("researchquestion"):
            target = "researchQuestion"
        elif path.startswith("hypothesis"):
            target = "hypothesis"
        elif path.startswith("constants"):
            target = "constants"
        else:
            target = "stages"
        if target not in targets:
            targets.append(target)
    return targets


def repair_stage_ids(
    issues: list[PlanReviewerIssue],
    available_stage_ids: list[str],
) -> list[str]:
    selected: list[str] = []
    for issue in issues:
        if route_review_issue(issue) != "plan_repairable":
            continue
        match = re.match(r"stages\[([^\]]+)\]", issue.sectionPath.lower())
        if not match:
            continue
        selector = match.group(1)
        if selector.isdigit():
            index = int(selector)
            stage_id = (
                available_stage_ids[index]
                if 0 <= index < len(available_stage_ids)
                else ""
            )
        else:
            stage_id = next(
                (
                    item
                    for item in available_stage_ids
                    if item.lower() == selector
                ),
                "",
            )
        if stage_id and stage_id not in selected:
            selected.append(stage_id)
    return selected


def _gate_issues(package: PlanPackage) -> list[PlanReviewerIssue]:
    issues: list[PlanReviewerIssue] = []
    error_messages = set(package.qualityGate.errors)
    for index, message in enumerate(
        [*package.qualityGate.errors, *package.qualityGate.warnings]
    ):
        section, separator, detail = message.partition(":")
        issues.append(
            PlanReviewerIssue(
                id=f"visible-{index}",
                severity="blocking" if message in error_messages else "warning",
                sectionPath=section.strip() if separator else "package",
                message=detail.strip() if separator else message,
            )
        )
    return issues


def final_user_issues(package: PlanPackage) -> list[PlanReviewerIssue]:
    issues: list[PlanReviewerIssue] = []
    if package.metaReview:
        issues.extend(package.metaReview.blockingIssues)
        issues.extend(
            issue
            for issue in package.metaReview.warnings
            if route_review_issue(issue)
            in {"upstream_blocking", "user_decision_required"}
        )
    issues.extend(_gate_issues(package))
    return dedupe_review_issues(
        [
            issue
            for issue in issues
            if route_review_issue(issue) != "diagnostic_only"
            and (
                issue.severity == "blocking"
                or route_review_issue(issue)
                in {"upstream_blocking", "user_decision_required"}
            )
        ]
    )


def user_visible_concerns(package: PlanPackage) -> list[str]:
    routes = {route_review_issue(issue) for issue in final_user_issues(package)}
    concerns: list[str] = []
    if "upstream_blocking" in routes:
        concerns.append(
            "The upstream evidence trace or literature support is incomplete, "
            "so this plan remains a draft."
        )
    if "user_decision_required" in routes:
        concerns.append(
            "A research-scope, dataset, or resource decision still requires "
            "owner confirmation."
        )
    if "plan_repairable" in routes:
        concerns.append(
            "The plan could not fully resolve an experimental-specificity "
            "requirement within the repair budget."
        )
    return concerns[:3]


def required_user_actions(package: PlanPackage) -> list[str]:
    routes = {route_review_issue(issue) for issue in final_user_issues(package)}
    actions: list[str] = []
    if "upstream_blocking" in routes:
        actions.append(
            "Complete the missing evidence selection or trace before approval."
        )
    if "user_decision_required" in routes:
        actions.append(
            "Confirm the unresolved scope, dataset, or resource constraint."
        )
    if "plan_repairable" in routes:
        actions.append(
            "Regenerate the affected plan section with additional constraints."
        )
    return actions[:3]
