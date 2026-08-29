"""Deterministic content-quality rules for existing PlanPackage fields."""

from __future__ import annotations

import hashlib
import re

from app.models.plan_package import PlanPackage, PlanReviewerIssue


_GENERIC_TARGETS = {
    "specified before implementation",
    "primary_metric",
    "readiness",
    "planned_metric",
    "higher",
    "lower",
    "better",
    "improved",
}
_GENERIC_METRICS = {
    "primary_metric",
    "planned_metric",
    "readiness",
    "default plan step",
}
_MEASURABLE_TARGET = re.compile(
    r"(?:\d|>=|<=|>|<|baseline|control|confidence interval|\bci\b|"
    r"non[- ]?inferior|statistical|all preregistered|zero failures|"
    r"\btrue\b|\bfalse\b|\bpass\b|complete|基线|对照|置信区间|不劣)",
    re.IGNORECASE,
)
_DIRECTION = re.compile(
    r"(?:increase|decrease|improve|reduce|higher|lower|positive|negative|"
    r"non[- ]?inferior|提升|提高|改善|增强|降低|增加|减少|优于|不劣)",
    re.IGNORECASE,
)
_FALSIFIER = re.compile(
    r"(?:reject|falsif|not positive|fails? if|unless|no improvement|"
    r"拒绝|证伪|不成立|未提升|没有改善)",
    re.IGNORECASE,
)
_MEASURE = re.compile(
    r"(?:metric|score|accuracy|faithfulness|rate|latency|cost|throughput|"
    r"effect size|coverage|recall|precision|指标|准确率|忠实度|错误率|"
    r"延迟|成本|吞吐|覆盖率|召回率|精确率)",
    re.IGNORECASE,
)


def metric_target_is_concrete(target: str) -> bool:
    normalized = " ".join(str(target or "").strip().lower().split())
    return bool(
        normalized
        and normalized not in _GENERIC_TARGETS
        and _MEASURABLE_TARGET.search(normalized)
    )


def hypothesis_is_falsifiable(hypothesis: str) -> bool:
    text = str(hypothesis or "").strip()
    return bool(
        _DIRECTION.search(text)
        and _MEASURE.search(text)
        and _FALSIFIER.search(text)
    )


def _issue(section_path: str, message: str) -> PlanReviewerIssue:
    digest = hashlib.sha1(
        f"{section_path}|{message}".encode("utf-8")
    ).hexdigest()[:12]
    return PlanReviewerIssue(
        id=f"specificity:{digest}",
        severity="blocking",
        sectionPath=section_path,
        message=message,
    )


def plan_specificity_issues(package: PlanPackage) -> list[PlanReviewerIssue]:
    issues: list[PlanReviewerIssue] = []
    paper_type = str(package.constants.get("paperType", "")).strip().lower()
    if paper_type != "survey" and not hypothesis_is_falsifiable(package.hypothesis):
        issues.append(
            _issue(
                "hypothesis",
                "Hypothesis must state a measurable direction and a falsification condition.",
            )
        )

    for stage_index, stage in enumerate(package.stages):
        for step_index, step in enumerate(stage.steps):
            base = f"stages[{stage_index}].steps[{step_index}]"
            if not step.outputs:
                issues.append(
                    _issue(
                        f"{base}.outputs",
                        "Step must declare at least one concrete artifact.",
                    )
                )
            if not step.expected:
                issues.append(
                    _issue(
                        f"{base}.expected",
                        "Step must declare at least one evaluation criterion.",
                    )
                )
            for metric_index, expected in enumerate(step.expected):
                metric_path = f"{base}.expected[{metric_index}]"
                if expected.metric.strip().lower() in _GENERIC_METRICS:
                    issues.append(
                        _issue(
                            f"{metric_path}.metric",
                            "Metric name must describe a scientific or operational outcome.",
                        )
                    )
                if not metric_target_is_concrete(expected.target):
                    issues.append(
                        _issue(
                            f"{metric_path}.target",
                            "Metric target must be numeric, baseline-relative, or statistically testable.",
                        )
                    )
    return issues
