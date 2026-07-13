"""Internal reviewer issue routing for PlanPackage repair and presentation."""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Literal

from app.models.plan_package import PlanReviewerIssue


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
