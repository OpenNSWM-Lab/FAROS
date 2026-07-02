"""PlanPackage orchestration service."""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional

from app.llm.provider_client import ChatMessage, get_provider_client
from app.models.idea import IdeaCandidate, IdeaSession
from app.models.plan_package import (
    PlanEvidenceRef,
    PlanExpectedMetric,
    PlanHumanFeedback,
    PlanOutput,
    PlanPackage,
    PlanPackageHandoff,
    PlanPackagePresentation,
    PlanPackageStatus,
    PlanReviewerIssue,
    PlanReviewerReport,
    PlanRevision,
    PlanStage,
    PlanStep,
)
from app.services.plan_package_builder import build_contribution_statements, build_plan_package
from app.services.plan_package_llm_schema import llm_plan_output_schema_hint, validate_llm_plan_output
from app.services.plan_package_plan_quality import (
    build_plan_blueprint,
    build_single_plan_design_brief,
    missing_plan_roles,
    sanitize_constants,
)
from app.services.plan_package_readiness import evaluate_downstream_readiness
from app.services.plan_package_revisor import build_plan_revision_patch
from app.services.plan_package_reviewers import apply_review_to_quality_gate
from app.services.plan_package_validator import validate_plan_package
from app.services.plan_package_views import build_plan_package_handoff, build_plan_package_presentation
from app.storage.idea_storage import (
    get_candidate_storage as get_idea_candidate_storage,
    get_graph_patch_storage,
    get_handoff_storage,
    get_literature_map_storage,
    get_path_seed_storage,
    get_probe_literature_storage,
    get_ranked_output_storage,
    get_raw_paper_storage,
    get_reasoning_kg_storage,
    get_search_tree_storage,
    get_session_storage as get_idea_session_storage,
    get_structured_paper_storage,
)
from app.storage.plan_package_storage import get_plan_package_storage

logger = logging.getLogger(__name__)


class PlanPackageNotFoundError(ValueError):
    pass


class PlanPackageConflictError(ValueError):
    pass


LLM_REVIEWER_FOCUS: Dict[str, Dict[str, Any]] = {
    "RelevanceReviewer": {
        "dimension": "topic and literature relevance",
        "checklist": [
            "Judge whether the researchQuestion, selected GAP, principle, and stages preserve the original seed topic.",
            "Judge whether key papers are truly relevant, not just loosely related through generic NLP/LLM terms.",
            "Block if the plan drifts to a different research problem.",
        ],
    },
    "EvidenceReviewer": {
        "dimension": "evidence faithfulness and grounding",
        "checklist": [
            "Judge whether selectedGap is actually supported by cited paper limitations, claims, KG signals, or probe evidence.",
            "Judge whether step evidenceRefs are meaningful for the step, not merely present.",
            "Block if the package makes unsupported claims or invents evidence.",
        ],
    },
    "FeasibilityReviewer": {
        "dimension": "implementation and research-plan feasibility",
        "checklist": [
            "Judge whether stages and steps are concrete enough for code/experiment modules to execute later.",
            "Judge whether datasets, baselines, variables, artifacts, and dependencies are clear enough.",
            "Block if the plan is generic, circular, or impossible to operationalize.",
        ],
    },
    "MetricReviewer": {
        "dimension": "metric and validation design",
        "checklist": [
            "Judge whether expected metrics can validate the hypothesis and selected GAP.",
            "Judge whether targets are measurable without claiming executed results.",
            "Block if metrics are generic, mismatched to the method, or cannot test the hypothesis.",
        ],
    },
    "NoveltyReviewer": {
        "dimension": "novelty, contribution, and prior-work difference",
        "checklist": [
            "Judge whether noveltyClaim and contributionStatement state a real difference from prior work.",
            "Judge whether the contribution is supported by the selected GAP and planned validation.",
            "Block if novelty is vague, duplicated by closest prior work, or not testable.",
        ],
    },
}


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    text = (text or "").strip().lstrip("\ufeff")
    if "```json" in text:
        text = text.split("```json", 1)[1]
        if "```" in text:
            text = text.rsplit("```", 1)[0]
    elif "```" in text:
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1]
    text = text.strip().lstrip("\ufeff")
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start:index + 1]
                try:
                    parsed = json.loads(candidate)
                    return parsed if isinstance(parsed, dict) else None
                except json.JSONDecodeError:
                    return None
    return None


def _topic_terms_from_package(package: PlanPackage) -> List[str]:
    text = " ".join([
        str(package.constants.get("seedQuery", "")),
        str(package.constants.get("domain", "")),
        package.researchQuestion,
        package.hypothesis,
        package.idea.title,
        package.idea.problem,
        package.idea.proposedMethod,
        package.gap.summary,
        package.principle.summary,
        package.principle.mechanism,
    ]).lower().replace("-", " ")
    if "rag" in text:
        text = f"{text} retrieval augmented generation"
    stopwords = {
        "and", "are", "based", "can", "does", "for", "from", "how", "into",
        "method", "methods", "model", "models", "paper", "research", "study",
        "than", "that", "the", "this", "through", "using", "what", "with",
        "language", "generation", "large", "learning",
    }
    terms: List[str] = []
    for token in re.findall(r"[a-zA-Z][a-zA-Z0-9]{2,}|[\u4e00-\u9fff]{2,}", text):
        if token in stopwords:
            continue
        if token not in terms:
            terms.append(token)
    return terms[:24]


def _hit_count(text: str, terms: List[str]) -> int:
    lowered = text.lower().replace("-", " ")
    return sum(1 for term in terms if term and term.lower() in lowered)


def _contains_any(text: str, terms: List[str]) -> bool:
    return any(term and term.lower() in text for term in terms)


def _clean_string_list(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    cleaned: List[str] = []
    for item in raw:
        text = str(item or "").strip()
        if text and text.lower() not in {"none", "null", "undefined"} and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _first_text(raw: Dict[str, Any], keys: List[str], default: str = "") -> str:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default


def _normalize_output_type(value: Any) -> str:
    raw = str(value or "report").strip().lower()
    aliases = {
        "metric": "metrics",
        "metrics": "metrics",
        "figure": "chart",
        "plot": "chart",
        "chart": "chart",
        "table": "table",
        "checkpoint": "checkpoint",
        "code": "code",
        "script": "code",
        "report": "report",
        "document": "report",
        "log": "log",
    }
    return aliases.get(raw, "report")


class PlanPackageService:
    """Create and retrieve complete idea+plan deliverables."""

    def __init__(self):
        self.package_storage = get_plan_package_storage()
        self.idea_session_storage = get_idea_session_storage()
        self.idea_candidate_storage = get_idea_candidate_storage()
        self.ranked_output_storage = get_ranked_output_storage()
        self.search_tree_storage = get_search_tree_storage()
        self.literature_map_storage = get_literature_map_storage()
        self.reasoning_kg_storage = get_reasoning_kg_storage()
        self.path_seed_storage = get_path_seed_storage()
        self.raw_paper_storage = get_raw_paper_storage()
        self.structured_paper_storage = get_structured_paper_storage()
        self.probe_storage = get_probe_literature_storage()
        self.graph_patch_storage = get_graph_patch_storage()
        self.handoff_storage = get_handoff_storage()

    def get(self, package_id: str) -> Optional[PlanPackage]:
        return self.package_storage.get(package_id)

    def get_presentation(self, package_id: str) -> PlanPackagePresentation:
        package = self.package_storage.get(package_id)
        if not package:
            raise PlanPackageNotFoundError(f"PlanPackage {package_id} not found")
        return build_plan_package_presentation(package)

    def get_handoff(self, package_id: str) -> PlanPackageHandoff:
        package = self.package_storage.get(package_id)
        if not package:
            raise PlanPackageNotFoundError(f"PlanPackage {package_id} not found")
        return build_plan_package_handoff(package)

    def get_by_idea_session(self, idea_session_id: str) -> Optional[PlanPackage]:
        return self.package_storage.get_by_idea_session(idea_session_id)

    def get_presentation_by_idea_session(self, idea_session_id: str) -> PlanPackagePresentation:
        package = self.package_storage.get_by_idea_session(idea_session_id)
        if not package:
            raise PlanPackageNotFoundError(f"PlanPackage for idea session {idea_session_id} not found")
        return build_plan_package_presentation(package)

    def validate(self, package_id: str, reviewer_mode: str = "hybrid") -> PlanPackage:
        package = self.package_storage.get(package_id)
        if not package:
            raise PlanPackageNotFoundError(f"PlanPackage {package_id} not found")
        reviewer_mode = self._normalize_reviewer_mode(reviewer_mode)
        package.qualityGate = validate_plan_package(package)
        package.qualityGate.humanApproved = package.status == PlanPackageStatus.APPROVED
        package.qualityGate = self._apply_review_mode(package, package.qualityGate, reviewer_mode=reviewer_mode)
        self._set_status_from_gate(package)
        return self.package_storage.update(package)

    def add_feedback(
        self,
        package_id: str,
        *,
        section_path: str,
        display_label: str = "",
        source_view: str = "presentation",
        target_sections: Optional[List[str]] = None,
        feedback_type: str,
        comment: str,
        severity: str = "medium",
        requested_action: str = "revise",
    ) -> PlanPackage:
        package = self.package_storage.get(package_id)
        if not package:
            raise PlanPackageNotFoundError(f"PlanPackage {package_id} not found")
        if not comment.strip():
            raise ValueError("feedback comment is required")
        feedback = PlanHumanFeedback(
            id=f"pfb_{uuid.uuid4().hex[:10]}",
            sectionPath=section_path.strip() or "package",
            displayLabel=display_label.strip() if display_label else "",
            sourceView=source_view.strip() if source_view else "presentation",
            targetSections=self._normalize_revision_targets(target_sections) if target_sections else [],
            feedbackType=feedback_type.strip() or "comment",
            comment=comment.strip(),
            severity=severity.strip() or "medium",
            requestedAction=requested_action.strip() or "revise",
        )
        package.humanFeedback.append(feedback)
        if not feedback.targetSections:
            patch = build_plan_revision_patch(
                package,
                include_feedback=True,
                allow_narrative=True,
            )
            feedback.targetSections = self._normalize_revision_targets(patch.changedSections) if patch.changedSections else []
        package.status = (
            PlanPackageStatus.NEEDS_REVISION
            if feedback.requestedAction in {"revise", "regenerate", "repair"}
            or feedback.feedbackType in {"correction", "reject", "regenerate"}
            else PlanPackageStatus.NEEDS_HUMAN_REVIEW
        )
        package.qualityGate.humanApproved = False
        return self.package_storage.update(package)

    def run_review(self, package_id: str, reviewer_mode: str = "hybrid") -> PlanPackage:
        package = self.package_storage.get(package_id)
        if not package:
            raise PlanPackageNotFoundError(f"PlanPackage {package_id} not found")
        reviewer_mode = self._normalize_reviewer_mode(reviewer_mode)
        was_approved = package.status == PlanPackageStatus.APPROVED and package.qualityGate.humanApproved
        package.status = PlanPackageStatus.AGENT_REVIEWING
        package.qualityGate = validate_plan_package(package)
        package.qualityGate.humanApproved = was_approved
        package.qualityGate = self._apply_review_mode(package, package.qualityGate, reviewer_mode=reviewer_mode)
        if was_approved and package.qualityGate.agentApproved and package.qualityGate.schemaValid and package.qualityGate.evidenceValid:
            package.status = PlanPackageStatus.APPROVED
            package.qualityGate.reviewDecision = "approved"
            package.qualityGate.implementationReady = True
        else:
            self._set_status_from_gate(package)
        return self.package_storage.update(package)

    def revise(
        self,
        package_id: str,
        *,
        generation_mode: str = "hybrid",
        max_stages: Optional[int] = None,
        max_steps_per_stage: Optional[int] = None,
        max_repair_rounds: int = 2,
        target_sections: Optional[List[str]] = None,
        reviewer_mode: str = "hybrid",
    ) -> PlanPackage:
        package = self.package_storage.get(package_id)
        if not package:
            raise PlanPackageNotFoundError(f"PlanPackage {package_id} not found")
        session = self.idea_session_storage.get(package.source.ideaSessionId)
        if not session:
            raise PlanPackageNotFoundError(f"Idea session {package.source.ideaSessionId} not found")

        mode = (generation_mode or "hybrid").strip().lower()
        if mode not in {"hybrid", "deterministic"}:
            raise ValueError("generationMode must be one of: deterministic, hybrid")
        reviewer_mode = self._normalize_reviewer_mode(reviewer_mode)
        revision_id = f"prev_{uuid.uuid4().hex[:10]}"
        unresolved_feedback_ids = [
            feedback.id
            for feedback in package.humanFeedback
            if not feedback.resolved
        ]
        package.status = PlanPackageStatus.NEEDS_REVISION
        package.schemaVersion = "plan-package/v4"

        previous_generation_repair_rounds = package.generation.repairRounds
        revision_patch = build_plan_revision_patch(
            package,
            target_sections=target_sections,
            include_feedback=True,
            allow_narrative=True,
        )
        normalized_targets = self._normalize_revision_targets(
            revision_patch.changedSections
            if revision_patch.changedSections
            else self._infer_revision_targets_from_feedback(package)
        )
        if mode == "hybrid":
            self._apply_llm_plan_fields(
                package,
                session,
                max_stages=max_stages or max(1, len(package.stages) or 3),
                max_steps_per_stage=max_steps_per_stage or max(
                    1,
                    max((len(stage.steps) for stage in package.stages), default=3),
                ),
                max_repair_rounds=max_repair_rounds,
                target_sections=normalized_targets,
            )
        else:
            package.generation.mode = "deterministic"
            package.generation.fallbackUsed = True
            package.generation.promptVersion = "plan-package-deterministic-review-v1"

        package.contributionStatement = build_contribution_statements(
            candidate=self._candidate_from_package(package),
            gap=package.gap,
            principle=package.principle,
            stages=package.stages,
        )
        for feedback in package.humanFeedback:
            if feedback.id in unresolved_feedback_ids:
                feedback.resolved = True
                feedback.resolvedByRevisionId = revision_id

        package.revisions.append(
            PlanRevision(
                id=revision_id,
                parentPackageId=package.packageId,
                changedSections=[
                    *normalized_targets,
                    "contributionStatement",
                    "qualityGate",
                    "reviewReports",
                ],
                feedbackIds=unresolved_feedback_ids,
                summary="Revised PlanPackage from human feedback and reviewer findings.",
                generationMode=mode,
                repairRounds=max(0, package.generation.repairRounds - previous_generation_repair_rounds),
                patchSummary=revision_patch.model_dump(),
            )
        )
        package.qualityGate = validate_plan_package(package)
        package.qualityGate = self._apply_review_mode(package, package.qualityGate, reviewer_mode=reviewer_mode)
        if mode == "hybrid":
            self._auto_repair_plan_from_review(
                package,
                session,
                max_stages=max_stages or max(1, len(package.stages) or 3),
                max_steps_per_stage=max_steps_per_stage or max(
                    1,
                    max((len(stage.steps) for stage in package.stages), default=3),
                ),
                max_repair_rounds=max_repair_rounds,
                reviewer_mode=reviewer_mode,
            )
        self._set_status_from_gate(package)
        return self.package_storage.update(package)

    def _auto_repair_plan_from_review(
        self,
        package: PlanPackage,
        session: IdeaSession,
        *,
        max_stages: int,
        max_steps_per_stage: int,
        max_repair_rounds: int,
        reviewer_mode: str,
    ) -> None:
        """Automatically repair plan-owned fields from reviewer findings."""

        if max_repair_rounds <= 0:
            return
        for repair_index in range(max_repair_rounds):
            if package.qualityGate.agentApproved and package.qualityGate.implementationReady:
                return
            revision_patch = build_plan_revision_patch(
                package,
                include_feedback=False,
                allow_narrative=False,
            )
            targets = self._normalize_revision_targets(revision_patch.changedSections) if revision_patch.changedSections else []
            if not targets and not revision_patch.upstreamBlocked:
                targets = self._infer_plan_repair_targets_from_review(package)
            if revision_patch.upstreamBlocked:
                message = "PlanPackage auto repair blocked by upstream issue: " + "; ".join(revision_patch.unresolvedIssues[:3])
                if message not in package.generation.warnings:
                    package.generation.warnings.append(message)
                if message not in package.qualityGate.warnings:
                    package.qualityGate.warnings.append(message)
                return
            if not targets:
                return
            try:
                previous_repair_rounds = package.generation.repairRounds
                self._apply_llm_plan_fields(
                    package,
                    session,
                    max_stages=max_stages,
                    max_steps_per_stage=max_steps_per_stage,
                    max_repair_rounds=1,
                    target_sections=targets,
                )
                package.contributionStatement = build_contribution_statements(
                    candidate=self._candidate_from_package(package),
                    gap=package.gap,
                    principle=package.principle,
                    stages=package.stages,
                )
                package.revisions.append(
                    PlanRevision(
                        id=f"prev_{uuid.uuid4().hex[:10]}",
                        parentPackageId=package.packageId,
                        changedSections=[*targets, "contributionStatement", "qualityGate", "reviewReports"],
                        feedbackIds=[],
                        summary="Auto-repaired PlanPackage from reviewer findings.",
                        generationMode="hybrid",
                        repairRounds=max(0, package.generation.repairRounds - previous_repair_rounds),
                        patchSummary=revision_patch.model_dump(),
                    )
                )
                package.qualityGate = validate_plan_package(package)
                package.qualityGate = self._apply_review_mode(
                    package,
                    package.qualityGate,
                    reviewer_mode=reviewer_mode,
                )
            except Exception as exc:
                logger.warning("PlanPackage auto repair failed: %s", exc, exc_info=True)
                message = f"PlanPackage auto repair failed on round {repair_index + 1}: {exc}"
                if message not in package.generation.warnings:
                    package.generation.warnings.append(message)
                if message not in package.qualityGate.warnings:
                    package.qualityGate.warnings.append(message)
                return

    def _infer_plan_repair_targets_from_review(self, package: PlanPackage) -> List[str]:
        targets: List[str] = []

        def add(*sections: str) -> None:
            for section in sections:
                if section not in targets:
                    targets.append(section)

        issues: List[str] = []
        if package.metaReview:
            issues.extend(
                f"{issue.sectionPath} {issue.message}"
                for issue in [*package.metaReview.blockingIssues, *package.metaReview.warnings]
            )
            issues.extend(package.metaReview.requiredRepairs)
        issues.extend(package.qualityGate.errors)
        issues.extend(package.qualityGate.warnings)
        text = " ".join(issues).lower()

        if _contains_any(text, ["researchquestion", "research question", "研究问题"]):
            add("researchQuestion")
        if _contains_any(text, [
            "topic", "seed", "drift", "relevance", "relevant", "faithful", "faithfulness",
            "citation", "selected idea", "主题", "跑偏", "漂移", "相关性", "忠于", "引用",
        ]):
            add("researchQuestion", "hypothesis", "stages")
        if _contains_any(text, ["hypothesis", "假设"]):
            add("hypothesis")
        if _contains_any(text, ["constant", "dataset", "model", "hardware", "baseline", "常量", "数据集", "模型", "基线"]):
            add("constants")
        if _contains_any(text, ["stage", "step", "stages", "steps", "implementation", "method", "阶段", "步骤", "计划", "实施"]):
            add("stages")
        if _contains_any(text, ["expected", "metric", "target", "output", "evaluation", "指标", "目标", "输出", "评估"]):
            add("expectedMetrics", "stages")
        if _contains_any(text, [
            "evidenceref", "evidence ref", "selectedgap", "selected gap", "supportedbypaperids",
            "supporting paper", "probe", "graph patch", "证据", "支撑论文", "选中gap", "选中 gap",
        ]):
            add("stages")

        # Do not auto-rewrite upstream idea/evidence fields here. Those should be
        # handled by the idea-stage review gate before PlanPackage creation.
        return self._normalize_revision_targets(targets) if targets else []

    def _normalize_reviewer_mode(self, reviewer_mode: str) -> str:
        mode = (reviewer_mode or "deterministic").strip().lower()
        if mode not in {"deterministic", "hybrid"}:
            raise ValueError("reviewerMode must be one of: deterministic, hybrid")
        return mode

    def _llm_review_used(self, reports: List[PlanReviewerReport]) -> bool:
        return any(
            report.reviewer in LLM_REVIEWER_FOCUS
            and not self._is_llm_unavailable_report(report)
            for report in reports
        )

    def _is_llm_unavailable_report(self, report: PlanReviewerReport) -> bool:
        return any(
            issue.sectionPath == "reviewReports"
            and issue.message.startswith("LLM reviewer unavailable")
            for issue in report.warnings
        )

    def _apply_review_mode(
        self,
        package: PlanPackage,
        gate: Any,
        *,
        reviewer_mode: str,
    ):
        mode = self._normalize_reviewer_mode(reviewer_mode)
        gate = apply_review_to_quality_gate(package, gate)
        package.qualityGate = gate
        llm_reports = self._run_llm_reviewers(package, reviewer_mode=mode)
        if llm_reports:
            gate = apply_review_to_quality_gate(package, gate, extra_reports=llm_reports)
        gate = self._apply_downstream_readiness(package, gate)
        package.generation.reviewerMode = mode
        package.generation.llmReviewerUsed = self._llm_review_used(llm_reports)
        return gate

    def _apply_downstream_readiness(self, package: PlanPackage, gate: Any):
        readiness = evaluate_downstream_readiness(package)
        package.downstreamReadiness = readiness
        gate.downstreamReady = readiness.overallReady
        readiness_errors = [
            f"downstream.{issue.module}: {issue.message}"
            for issue in readiness.blockingIssues
        ]
        readiness_warnings = [
            f"downstream.{issue.module}: {issue.message}"
            for issue in readiness.warnings
        ]
        existing_errors = set(gate.errors)
        existing_warnings = set(gate.warnings)
        gate.errors.extend(message for message in readiness_errors if message not in existing_errors)
        gate.warnings.extend(message for message in readiness_warnings if message not in existing_warnings)
        if not readiness.overallReady:
            gate.implementationReady = False
            gate.agentApproved = False
            if gate.reviewDecision == "approve":
                gate.reviewDecision = "revise"
        else:
            gate.implementationReady = bool(
                gate.schemaValid
                and gate.evidenceValid
                and gate.topicRelevant
                and gate.citationFaithful
                and gate.planSpecific
                and gate.agentApproved
            )
        return gate

    def _run_llm_reviewers(self, package: PlanPackage, *, reviewer_mode: str) -> List[PlanReviewerReport]:
        mode = self._normalize_reviewer_mode(reviewer_mode)
        if mode != "hybrid":
            return []
        session = self.idea_session_storage.get(package.source.ideaSessionId)
        provider_name = session.config.providerName if session else package.generation.providerName
        model = session.config.model if session else package.generation.model
        rule_reports = [
            report
            for report in package.reviewReports
            if report.reviewer in LLM_REVIEWER_FOCUS
        ]
        if not rule_reports:
            return []
        if not provider_name or not model:
            return [
                self._llm_unavailable_report(
                    report.reviewer,
                    "provider/model is not available for this package",
                    base_score=report.score,
                )
                for report in rule_reports
            ]
        try:
            client = get_provider_client(provider_name)
        except Exception as exc:
            logger.warning("LLM reviewer provider initialization failed: %s", exc, exc_info=True)
            return [
                self._llm_unavailable_report(
                    report.reviewer,
                    f"provider initialization failed: {exc}",
                    base_score=report.score,
                )
                for report in rule_reports
            ]
        llm_reports: List[PlanReviewerReport] = []
        for rule_report in rule_reports:
            try:
                response = client.chat(
                    messages=[
                        ChatMessage(
                            role="system",
                            content=(
                                "You are a strict scientific reviewer for one specific dimension of an idea+plan handoff package. "
                                "Return one JSON object only. Do not invent paper IDs, claim IDs, datasets, benchmarks, KG IDs, "
                                "probe IDs, graph patch IDs, or executed results."
                            ),
                        ),
                        ChatMessage(
                            role="user",
                            content=self._build_llm_review_prompt(package, rule_report=rule_report),
                        ),
                    ],
                    model=model,
                    temperature=0.0,
                    max_tokens=3072,
                    response_format={"type": "json_object"},
                )
                parsed = _extract_json(response.text or "")
                if not parsed:
                    llm_reports.append(
                        self._llm_unavailable_report(
                            rule_report.reviewer,
                            "LLM returned non-JSON output",
                            base_score=rule_report.score,
                        )
                    )
                    continue
                llm_reports.append(
                    self._parse_llm_review_report(
                        parsed,
                        reviewer=rule_report.reviewer,
                        default_score=rule_report.score,
                    )
                )
            except Exception as exc:
                logger.warning("%s LLM reviewer failed: %s", rule_report.reviewer, exc, exc_info=True)
                llm_reports.append(
                    self._llm_unavailable_report(
                        rule_report.reviewer,
                        str(exc),
                        base_score=rule_report.score,
                    )
                )
        return llm_reports

    def _llm_unavailable_report(self, reviewer: str, message: str, *, base_score: float = 0.5) -> PlanReviewerReport:
        return PlanReviewerReport(
            reviewer=reviewer,
            score=base_score,
            passed=True,
            warnings=[
                PlanReviewerIssue(
                    id=f"pri_{uuid.uuid4().hex[:10]}",
                    severity="warning",
                    sectionPath="reviewReports",
                    message=f"LLM reviewer unavailable for {reviewer}: {message}",
                )
            ],
            repairSuggestions=[],
        )

    def _parse_llm_review_report(
        self,
        parsed: Dict[str, Any],
        *,
        reviewer: str,
        default_score: float = 0.5,
    ) -> PlanReviewerReport:
        score = parsed.get("score", parsed.get("overallScore", default_score))
        try:
            score_value = float(score)
        except (TypeError, ValueError):
            score_value = default_score
        if score_value > 1.0:
            score_value = score_value / 100.0
        score_value = max(0.0, min(1.0, round(score_value, 3)))
        decision = str(parsed.get("decision", "")).strip().lower()
        blocking = self._parse_llm_issues(parsed.get("blockingIssues", []), default_severity="blocking")
        warnings = self._parse_llm_issues(parsed.get("warnings", []), default_severity="warning")
        if "passed" in parsed:
            passed = bool(parsed.get("passed"))
        elif decision:
            passed = decision in {"approve", "pass", "passed"}
        else:
            passed = score_value >= 0.72 and not blocking
        if not passed and not blocking:
            blocking.append(
                PlanReviewerIssue(
                    id=f"pri_{uuid.uuid4().hex[:10]}",
                    severity="blocking",
                    sectionPath="package",
                    message=str(parsed.get("rationale", f"{reviewer} LLM review did not pass this package.")),
                )
            )
        suggestions_raw = parsed.get("repairSuggestions", parsed.get("requiredRepairs", []))
        if isinstance(suggestions_raw, str):
            suggestions = [suggestions_raw]
        elif isinstance(suggestions_raw, list):
            suggestions = [str(item).strip() for item in suggestions_raw if str(item).strip()]
        else:
            suggestions = []
        rationale = str(parsed.get("rationale", "")).strip()
        if rationale:
            warnings.append(
                PlanReviewerIssue(
                    id=f"pri_{uuid.uuid4().hex[:10]}",
                    severity="info",
                    sectionPath="metaReview",
                    message=rationale,
                )
            )
        return PlanReviewerReport(
            reviewer=reviewer,
            score=score_value,
            passed=passed and not blocking,
            blockingIssues=blocking,
            warnings=warnings,
            repairSuggestions=suggestions,
        )

    def _parse_llm_issues(self, raw_issues: Any, *, default_severity: str) -> List[PlanReviewerIssue]:
        if isinstance(raw_issues, str):
            raw_issues = [raw_issues]
        if not isinstance(raw_issues, list):
            return []
        issues: List[PlanReviewerIssue] = []
        for raw_issue in raw_issues[:12]:
            if isinstance(raw_issue, dict):
                message = str(raw_issue.get("message") or raw_issue.get("issue") or raw_issue.get("text") or "").strip()
                section_path = str(raw_issue.get("sectionPath") or raw_issue.get("section") or "package").strip()
                severity = str(raw_issue.get("severity") or default_severity).strip()
            else:
                message = str(raw_issue).strip()
                section_path = "package"
                severity = default_severity
            if not message:
                continue
            issues.append(
                PlanReviewerIssue(
                    id=f"pri_{uuid.uuid4().hex[:10]}",
                    severity=severity if severity in {"info", "warning", "blocking"} else default_severity,
                    sectionPath=section_path or "package",
                    message=message,
                )
            )
        return issues

    def _build_llm_review_prompt(self, package: PlanPackage, *, rule_report: PlanReviewerReport) -> str:
        focus = LLM_REVIEWER_FOCUS[rule_report.reviewer]
        rule_summary = {
            "reviewer": rule_report.reviewer,
            "ruleScore": rule_report.score,
            "rulePassed": rule_report.passed,
            "ruleBlockingIssues": [
                {"sectionPath": issue.sectionPath, "message": issue.message}
                for issue in rule_report.blockingIssues[:6]
            ],
            "ruleWarnings": [
                {"sectionPath": issue.sectionPath, "message": issue.message}
                for issue in rule_report.warnings[:6]
            ],
            "ruleRepairSuggestions": rule_report.repairSuggestions[:6],
        }
        context = {
            "reviewTask": {
                "reviewer": rule_report.reviewer,
                "dimension": focus["dimension"],
                "focusChecklist": focus["checklist"],
                "returnShape": {
                    "score": "0..1",
                    "passed": "boolean",
                    "decision": "approve | revise | reject",
                    "rationale": "short explanation",
                    "blockingIssues": [{"sectionPath": "field path", "message": "blocking issue", "severity": "blocking"}],
                    "warnings": [{"sectionPath": "field path", "message": "warning", "severity": "warning"}],
                    "repairSuggestions": ["specific repair instruction"],
                },
                "approvalRules": [
                    "Judge only this review dimension. Do not re-score unrelated dimensions unless they directly affect this dimension.",
                    "Use the rule reviewer summary as a starting point, but add semantic scientific judgment.",
                    "Block only when the issue would make downstream handoff unsafe or scientifically misleading.",
                    "Warn for wording, specificity, or minor evidence risks that do not block handoff.",
                ],
            },
            "seedQuery": package.constants.get("seedQuery", ""),
            "researchQuestion": package.researchQuestion,
            "hypothesis": package.hypothesis,
            "idea": package.idea.model_dump(),
            "background": package.background.model_dump(),
            "selectedGap": next(
                (item.model_dump() for item in package.gap.items if item.id == package.gap.selectedGapId),
                package.gap.model_dump(),
            ),
            "principle": package.principle.model_dump(),
            "contributionStatement": [item.model_dump() for item in package.contributionStatement],
            "literatureSurvey": [
                {
                    "paperId": paper.paperId,
                    "source": paper.source,
                    "title": paper.title,
                    "summary": paper.summary,
                    "limitations": paper.limitations[:3],
                    "claims": paper.claims[:3],
                    "relevanceScore": paper.relevanceScore,
                    "relevanceSignals": paper.relevanceSignals[:8],
                }
                for paper in package.literatureSurvey.papers[:12]
            ],
            "stages": [
                {
                    "id": stage.id,
                    "title": stage.title,
                    "goal": stage.goal,
                    "method": stage.method,
                    "steps": [
                        {
                            "id": step.id,
                            "title": step.title,
                            "desc": step.desc,
                            "method": step.method,
                            "outputs": [output.model_dump() for output in step.outputs],
                            "expected": [expected.model_dump() for expected in step.expected],
                            "evidenceRefs": [ref.model_dump() for ref in step.evidenceRefs],
                        }
                        for step in stage.steps
                    ],
                }
                for stage in package.stages
            ],
            "qualityGate": package.qualityGate.model_dump(),
            "ruleReviewerSummary": rule_summary,
        }
        return (
            f"Review the PlanPackage only as {rule_report.reviewer}. "
            "Use the exact return shape in reviewTask.returnShape. "
            "Return JSON only. Do not quote long source text. Do not add markdown. "
            "Do not invent paper IDs, claim IDs, KG IDs, probe IDs, graph patch IDs, datasets, or executed results.\n"
            f"{json.dumps(context, ensure_ascii=False, default=str)}"
        )

    def _normalize_revision_targets(self, target_sections: Optional[List[str]]) -> List[str]:
        allowed = {
            "researchQuestion",
            "hypothesis",
            "constants",
            "stages",
            "expectedMetrics",
            "background",
            "gap",
            "principle",
        }
        if not target_sections:
            return ["researchQuestion", "hypothesis", "constants", "stages"]
        normalized: List[str] = []
        for section in target_sections:
            section = str(section or "").strip()
            if section in allowed and section not in normalized:
                normalized.append(section)
        if not normalized:
            raise ValueError("targetSections must contain at least one writable section")
        if "expectedMetrics" in normalized and "stages" not in normalized:
            normalized.append("stages")
        return normalized

    def _infer_revision_targets_from_feedback(self, package: PlanPackage) -> List[str]:
        targets: List[str] = []

        def add(*sections: str) -> None:
            for section in sections:
                if section not in targets:
                    targets.append(section)

        unresolved = [feedback for feedback in package.humanFeedback if not feedback.resolved]
        if not unresolved:
            return self._normalize_revision_targets(None)

        for feedback in unresolved:
            text = " ".join([
                feedback.sectionPath or "",
                feedback.feedbackType or "",
                feedback.comment or "",
            ]).lower()

            if _contains_any(text, [
                "overall", "whole", "regenerate", "rewrite",
                "整体", "全部", "全局", "重写", "重新生成", "质量低", "不合理",
            ]):
                add("researchQuestion", "hypothesis", "constants", "stages")

            if _contains_any(text, [
                "research question", "question", "problem", "scope", "scenario", "boundary",
                "研究问题", "问题", "对象", "场景", "边界",
            ]):
                add("researchQuestion")

            if _contains_any(text, [
                "background", "motivation", "limitation", "context",
                "背景", "动机", "限制", "现状",
            ]):
                add("background")

            if _contains_any(text, [
                "gap", "unresolved", "entry point", "research gap",
                "缺口", "gap", "切入点", "未解决", "已有方法",
            ]):
                add("gap", "researchQuestion", "hypothesis")

            if _contains_any(text, [
                "principle", "method", "mechanism", "novelty", "contribution", "claim",
                "原理", "方法", "机制", "创新", "贡献", "声明",
            ]):
                add("principle", "hypothesis", "stages")

            if _contains_any(text, [
                "hypothesis", "assumption", "expected outcome", "improvement", "target",
                "假设", "预期", "提升", "目标", "效果",
            ]):
                add("hypothesis")

            if _contains_any(text, [
                "constant", "dataset", "model", "hardware", "setting", "baseline", "constraint", "parameter",
                "常量", "数据集", "模型", "硬件", "设置", "基线", "约束", "参数",
            ]):
                add("constants")

            if _contains_any(text, [
                "stage", "step", "plan", "implementation", "experiment", "ablation", "comparison",
                "阶段", "步骤", "计划", "实施", "实验", "消融", "对比", "流程",
            ]):
                add("stages")

            if _contains_any(text, [
                "metric", "expected", "output", "evaluation", "measure", "benchmark", "result",
                "指标", "输出", "评估", "度量", "结果",
            ]):
                add("expectedMetrics", "stages")

        if not targets:
            add("researchQuestion", "hypothesis", "stages")
        return self._normalize_revision_targets(targets)

    def approve(self, package_id: str, reviewer_mode: Optional[str] = None) -> PlanPackage:
        package = self.package_storage.get(package_id)
        if not package:
            raise PlanPackageNotFoundError(f"PlanPackage {package_id} not found")
        mode = self._normalize_reviewer_mode(reviewer_mode or package.generation.reviewerMode or "hybrid")
        package.qualityGate = validate_plan_package(package)
        package.qualityGate = self._apply_review_mode(package, package.qualityGate, reviewer_mode=mode)
        unresolved_blocking_feedback = [
            feedback
            for feedback in package.humanFeedback
            if not feedback.resolved
            and feedback.feedbackType != "approve"
            and feedback.severity in {"high", "blocking"}
        ]
        if unresolved_blocking_feedback:
            ids = ", ".join(feedback.id for feedback in unresolved_blocking_feedback)
            raise PlanPackageConflictError(f"Blocking human feedback must be resolved before approval: {ids}")
        if not package.qualityGate.agentApproved or package.qualityGate.reviewDecision != "approve":
            raise PlanPackageConflictError("PlanPackage has not passed agent review")
        if (
            not package.qualityGate.schemaValid
            or not package.qualityGate.evidenceValid
            or not package.qualityGate.topicRelevant
            or not package.qualityGate.citationFaithful
            or not package.qualityGate.planSpecific
            or not package.qualityGate.downstreamReady
            or package.qualityGate.errors
        ):
            raise PlanPackageConflictError("PlanPackage schema/evidence gate has not passed")
        package.status = PlanPackageStatus.APPROVED
        package.qualityGate.humanApproved = True
        package.qualityGate.reviewDecision = "approved"
        package.qualityGate.implementationReady = package.qualityGate.downstreamReady
        return self.package_storage.update(package)

    def create_from_idea_session(
        self,
        idea_session_id: str,
        *,
        candidate_id: Optional[str] = None,
        max_stages: int = 3,
        max_steps_per_stage: int = 3,
        user_notes: Optional[str] = None,
        generation_mode: str = "hybrid",
        max_repair_rounds: int = 2,
        reviewer_mode: str = "hybrid",
    ) -> PlanPackage:
        session = self.idea_session_storage.get(idea_session_id)
        if not session:
            raise PlanPackageNotFoundError(f"Idea session {idea_session_id} not found")

        ranked_output = self.ranked_output_storage.get_by_session(idea_session_id)
        candidate = self._select_candidate(session, ranked_output, candidate_id)
        if not candidate:
            raise PlanPackageConflictError("No final idea candidate is available for this session")

        search_tree = self.search_tree_storage.get_by_session(idea_session_id)
        literature_map = self.literature_map_storage.get_by_session(idea_session_id)
        reasoning_kg = self.reasoning_kg_storage.get_by_session(idea_session_id)
        path_seeds = self.path_seed_storage.list_by_session(idea_session_id)
        raw_papers = self.raw_paper_storage.list_by_session(idea_session_id)
        structured_papers = self.structured_paper_storage.list_by_session(idea_session_id)
        probe_results = self.probe_storage.list_by_session(idea_session_id)
        graph_patches = self.graph_patch_storage.list_by_session(idea_session_id)
        handoff = self.handoff_storage.get_by_session(idea_session_id)

        package = build_plan_package(
            idea_session_id=idea_session_id,
            candidate=candidate,
            ranked_output=ranked_output,
            search_tree=search_tree,
            literature_map=literature_map,
            reasoning_kg=reasoning_kg,
            path_seeds=path_seeds,
            raw_papers=raw_papers,
            structured_papers=structured_papers,
            probe_results=probe_results,
            graph_patches=graph_patches,
            handoff=handoff,
            user_notes=user_notes,
            max_stages=max_stages,
            max_steps_per_stage=max_steps_per_stage,
            paper_type=session.config.paperType,
        )
        package.constants.setdefault("seedQuery", session.config.seedQuery)
        if session.config.domain:
            package.constants.setdefault("domain", session.config.domain)
        package.constants.setdefault("paperType", session.config.paperType)

        generation_warnings: List[str] = []
        mode = (generation_mode or "hybrid").strip().lower()
        if mode not in {"deterministic", "hybrid"}:
            raise ValueError("generationMode must be one of: deterministic, hybrid")
        reviewer_mode = self._normalize_reviewer_mode(reviewer_mode)

        package.generation.mode = mode
        package.generation.reviewerMode = reviewer_mode
        package.generation.repairRounds = 0
        package.generation.fallbackUsed = mode == "deterministic"
        package.generation.promptVersion = (
            "plan-package-single-implementation-planner-v2"
            if mode == "hybrid"
            else "plan-package-adapter-v1"
        )
        self._record_plan_blueprint(
            package,
            max_stages=max_stages,
            max_steps_per_stage=max_steps_per_stage,
        )
        package.sourceFields.implementationPlan = [
            "LLM implementation planner" if mode == "hybrid" else "deterministic fallback stage builder",
            "single-plan quality skeleton",
            "PlanPackage.idea",
            "PlanPackage.background",
            "PlanPackage.gap",
            "PlanPackage.principle",
            "PlanPackage.literatureSurvey",
        ]

        if mode == "hybrid":
            try:
                self._apply_llm_plan_fields(
                    package,
                    session,
                    max_stages=max_stages,
                    max_steps_per_stage=max_steps_per_stage,
                    max_repair_rounds=max_repair_rounds,
                )
            except Exception as exc:
                logger.warning("LLM plan field generation failed: %s", exc, exc_info=True)
                generation_warnings.append(f"LLM plan field generation failed: {exc}")
                package.generation.fallbackUsed = True

        package.contributionStatement = build_contribution_statements(
            candidate=candidate,
            gap=package.gap,
            principle=package.principle,
            stages=package.stages,
        )
        package.schemaVersion = "plan-package/v4"
        package.qualityGate = validate_plan_package(package)
        package.qualityGate = self._apply_review_mode(package, package.qualityGate, reviewer_mode=reviewer_mode)
        package.qualityGate.warnings.extend(generation_warnings)
        package.generation.warnings.extend(generation_warnings)
        if mode == "hybrid":
            self._auto_repair_plan_from_review(
                package,
                session,
                max_stages=max_stages,
                max_steps_per_stage=max_steps_per_stage,
                max_repair_rounds=max_repair_rounds,
                reviewer_mode=reviewer_mode,
            )
        self._set_status_from_gate(package)
        return self.package_storage.create(package)

    def _set_status_from_gate(self, package: PlanPackage) -> None:
        if package.status == PlanPackageStatus.APPROVED:
            if package.qualityGate.agentApproved and package.qualityGate.schemaValid and package.qualityGate.evidenceValid:
                return
        package.status = (
            PlanPackageStatus.NEEDS_HUMAN_REVIEW
            if package.qualityGate.agentApproved and package.qualityGate.implementationReady
            else PlanPackageStatus.NEEDS_REVISION
        )

    def _record_plan_blueprint(
        self,
        package: PlanPackage,
        *,
        max_stages: int,
        max_steps_per_stage: int,
    ) -> None:
        blueprint = build_plan_blueprint(
            package,
            max_stages=max_stages,
            max_steps_per_stage=max_steps_per_stage,
        )
        package.generation.blueprintVersion = blueprint.version
        package.generation.templateId = blueprint.templateId
        package.generation.blueprintSummary = {
            "paperType": blueprint.paperType,
            "requiredRoleIds": [role.get("id", "") for role in blueprint.requiredRoles],
            "stageShape": blueprint.recommendedStageShape,
            "metricRequirements": blueprint.metricRequirements,
            "artifactRequirements": blueprint.artifactRequirements,
            "downstreamReadinessChecks": blueprint.downstreamReadinessChecks,
        }

    def _candidate_from_package(self, package: PlanPackage) -> IdeaCandidate:
        raw_candidate = {}
        if isinstance(package.rawIdeaOutputs, dict):
            raw_candidate = package.rawIdeaOutputs.get("ideaCandidate") or {}
        scores = package.idea.scores if isinstance(package.idea.scores, dict) else {}
        try:
            return IdeaCandidate(**raw_candidate)
        except Exception:
            return IdeaCandidate(
                id=package.idea.id,
                sessionId=package.source.ideaSessionId,
                title=package.idea.title,
                problem=package.idea.problem,
                hypothesisStatement=package.idea.hypothesisStatement,
                keyInsight=package.idea.keyInsight,
                proposedMethod=package.idea.proposedMethod,
                expectedOutcome=package.idea.expectedOutcome,
                scores=scores,
                searchNodeId=package.source.searchNodeId,
                pathSeedId=package.source.pathSeedId,
            )

    def _select_candidate(
        self,
        session: IdeaSession,
        ranked_output: Any,
        candidate_id: Optional[str],
    ) -> Optional[IdeaCandidate]:
        candidates: List[IdeaCandidate] = []
        try:
            candidates.extend(self.idea_candidate_storage.list_by_session(session.id))
        except Exception:
            logger.debug("CandidateStorage list_by_session failed", exc_info=True)

        if ranked_output:
            ranked_candidates = [
                c for c in ranked_output.rankedCandidates
                if all(existing.id != c.id for existing in candidates)
            ]
            candidates.extend(ranked_candidates)

        for stored_id in session.candidateIds:
            if any(existing.id == stored_id for existing in candidates):
                continue
            stored = self.idea_candidate_storage.get(stored_id)
            if stored:
                candidates.append(stored)

        if candidate_id:
            for candidate in candidates:
                if candidate.id == candidate_id:
                    return candidate
            stored = self.idea_candidate_storage.get(candidate_id)
            if stored:
                return stored
            return None

        if session.selectedCandidateId:
            for candidate in candidates:
                if candidate.id == session.selectedCandidateId:
                    return candidate

        if ranked_output and ranked_output.topCandidateId:
            for candidate in candidates:
                if candidate.id == ranked_output.topCandidateId:
                    return candidate

        if candidates:
            candidates.sort(key=lambda c: c.overallScore, reverse=True)
            return candidates[0]
        return None

    def _apply_llm_plan_fields(
        self,
        package: PlanPackage,
        session: IdeaSession,
        *,
        max_stages: int,
        max_steps_per_stage: int,
        max_repair_rounds: int,
        target_sections: Optional[List[str]] = None,
    ) -> None:
        client = get_provider_client(session.config.providerName)
        package.generation.providerName = session.config.providerName
        package.generation.model = session.config.model
        package.generation.promptVersion = "plan-package-single-implementation-planner-v2"
        self._record_plan_blueprint(
            package,
            max_stages=max_stages,
            max_steps_per_stage=max_steps_per_stage,
        )
        prompt = self._build_llm_prompt(
            package,
            max_stages=max_stages,
            max_steps_per_stage=max_steps_per_stage,
            target_sections=target_sections,
        )
        base_messages = [
            ChatMessage(
                role="system",
                content=(
                    "You generate implementation-plan JSON only. Do not invent paper IDs, "
                    "claim IDs, KG IDs, probe IDs, graph patch IDs, datasets, or executed results. "
                    "The plan must stay semantically faithful to the seed query and selected idea."
                ),
            ),
            ChatMessage(role="user", content=prompt),
        ]

        last_issues: List[str] = []
        last_response_text = ""
        schema_repair_rounds = 0
        attempts = max(1, max_repair_rounds + 1)
        for attempt in range(attempts):
            messages = list(base_messages)
            if attempt > 0:
                messages.extend([
                    ChatMessage(role="assistant", content=last_response_text[:4000]),
                    ChatMessage(
                        role="user",
                        content=self._build_llm_repair_prompt(last_issues, target_sections=target_sections),
                    ),
                ])
            response = client.chat(
                messages=messages,
                model=session.config.model,
                temperature=0.2 if attempt == 0 else 0.0,
                max_tokens=8192,
                response_format={"type": "json_object"},
            )
            last_response_text = response.text or ""
            raw_parsed = _extract_json(last_response_text)
            if not raw_parsed:
                last_issues = ["LLM did not return one complete valid JSON object"]
                schema_repair_rounds += 1
                continue
            parsed, schema_issues = validate_llm_plan_output(
                raw_parsed,
                target_sections=target_sections,
            )
            if schema_issues or parsed is None:
                last_issues = schema_issues or ["LLM output failed schema validation"]
                schema_repair_rounds += 1
                continue

            candidate_package = package.model_copy(deep=True)
            self._apply_parsed_plan_fields(
                candidate_package,
                parsed,
                max_stages=max_stages,
                max_steps_per_stage=max_steps_per_stage,
                target_sections=target_sections,
            )
            last_issues = self._validate_generated_plan_fields(
                candidate_package,
                target_sections=target_sections,
            )
            if last_issues:
                continue

            self._apply_parsed_plan_fields(
                package,
                parsed,
                max_stages=max_stages,
                max_steps_per_stage=max_steps_per_stage,
                target_sections=target_sections,
            )
            used_sections = ["implementationPlan"]
            if set(target_sections or []) & {"background", "gap", "principle"}:
                used_sections.append("feedbackNarrative")
            package.generation.llmUsedSections = used_sections
            package.generation.repairRounds = attempt
            package.generation.schemaRepairRounds += schema_repair_rounds
            package.generation.fallbackUsed = False
            return

        package.generation.schemaRepairRounds += schema_repair_rounds
        raise ValueError("LLM plan field generation failed validation: " + "; ".join(last_issues))

    def _apply_parsed_plan_fields(
        self,
        package: PlanPackage,
        parsed: Dict[str, Any],
        *,
        max_stages: int,
        max_steps_per_stage: int,
        target_sections: Optional[List[str]] = None,
    ) -> None:
        writable = set(target_sections or ["researchQuestion", "hypothesis", "constants", "stages"])
        if "researchQuestion" in writable and isinstance(parsed.get("researchQuestion"), str) and parsed["researchQuestion"].strip():
            package.researchQuestion = parsed["researchQuestion"].strip()
        if "hypothesis" in writable and isinstance(parsed.get("hypothesis"), str):
            package.hypothesis = parsed["hypothesis"].strip()
        if "constants" in writable and isinstance(parsed.get("constants"), dict):
            protected = {"ideaSessionId", "ideaCandidateId", "planStage", "seedQuery", "domain", "paperType"}
            for key, value in sanitize_constants(parsed["constants"]).items():
                if key not in protected:
                    package.constants[key] = value
        if "stages" in writable and isinstance(parsed.get("stages"), list) and parsed["stages"]:
            package.stages = self._parse_llm_stages(
                parsed["stages"],
                package,
                max_stages=max_stages,
                max_steps_per_stage=max_steps_per_stage,
            )
        if "background" in writable and isinstance(parsed.get("background"), dict):
            self._apply_parsed_background(package, parsed["background"])
        if "gap" in writable and isinstance(parsed.get("gap"), dict):
            self._apply_parsed_gap(package, parsed["gap"])
        if "principle" in writable and isinstance(parsed.get("principle"), dict):
            self._apply_parsed_principle(package, parsed["principle"])

    def _apply_parsed_background(self, package: PlanPackage, raw: Dict[str, Any]) -> None:
        if isinstance(raw.get("summary"), str) and raw["summary"].strip():
            package.background.summary = raw["summary"].strip()
        if isinstance(raw.get("motivation"), str):
            package.background.motivation = raw["motivation"].strip()
        if isinstance(raw.get("currentLimitations"), list):
            package.background.currentLimitations = [
                str(item).strip()
                for item in raw["currentLimitations"]
                if str(item).strip()
            ][:8]
        if isinstance(raw.get("domainContext"), list):
            package.background.domainContext = [
                str(item).strip()
                for item in raw["domainContext"]
                if str(item).strip()
            ][:8]

    def _apply_parsed_gap(self, package: PlanPackage, raw: Dict[str, Any]) -> None:
        if isinstance(raw.get("summary"), str) and raw["summary"].strip():
            package.gap.summary = raw["summary"].strip()
        if isinstance(raw.get("selectedGapId"), str):
            existing_ids = {item.id for item in package.gap.items}
            if raw["selectedGapId"] in existing_ids:
                package.gap.selectedGapId = raw["selectedGapId"]

        raw_items = raw.get("items")
        if not isinstance(raw_items, list):
            return
        existing_by_id = {item.id: item for item in package.gap.items}
        for index, raw_item in enumerate(raw_items):
            if not isinstance(raw_item, dict):
                continue
            item_id = str(raw_item.get("id") or "").strip()
            item = existing_by_id.get(item_id)
            if not item and index < len(package.gap.items):
                item = package.gap.items[index]
            if not item:
                continue
            for field_name in [
                "statement",
                "severity",
                "existingCoverage",
                "unresolvedIssue",
                "proposedEntry",
                "boundary",
                "whyUnsolved",
            ]:
                value = raw_item.get(field_name)
                if isinstance(value, str) and value.strip():
                    setattr(item, field_name, value.strip())
            validation_needs = raw_item.get("validationNeeds")
            if isinstance(validation_needs, list):
                item.validationNeeds = [
                    str(value).strip()
                    for value in validation_needs
                    if str(value).strip()
                ][:8]

    def _apply_parsed_principle(self, package: PlanPackage, raw: Dict[str, Any]) -> None:
        for field_name in ["summary", "mechanism", "noveltyClaim"]:
            value = raw.get(field_name)
            if isinstance(value, str) and value.strip():
                setattr(package.principle, field_name, value.strip())
        for field_name in ["assumptions", "risks"]:
            value = raw.get(field_name)
            if isinstance(value, list):
                setattr(
                    package.principle,
                    field_name,
                    [str(item).strip() for item in value if str(item).strip()][:8],
                )

    def _validate_generated_plan_fields(
        self,
        package: PlanPackage,
        *,
        target_sections: Optional[List[str]] = None,
    ) -> List[str]:
        issues: List[str] = []
        if not package.researchQuestion.strip():
            issues.append("researchQuestion is empty")
        if not package.hypothesis.strip():
            issues.append("hypothesis is empty")
        if not package.stages:
            issues.append("stages is empty")

        for stage in package.stages:
            if not stage.steps:
                issues.append(f"{stage.id}.steps is empty")
            for step in stage.steps:
                if not step.outputs:
                    issues.append(f"{step.id}.outputs is empty")
                if not step.expected:
                    issues.append(f"{step.id}.expected is empty")
                if "default step inserted" in step.desc.lower():
                    issues.append(f"{step.id} used default fallback text")

        topic_terms = _topic_terms_from_package(package)
        if len(topic_terms) >= 4:
            rq_hits = _hit_count(f"{package.researchQuestion} {package.hypothesis}", topic_terms)
            if rq_hits < 2:
                issues.append("researchQuestion/hypothesis drifted away from seed query and selected idea")
            plan_text = " ".join(
                chunk
                for stage in package.stages
                for chunk in [
                    stage.title,
                    stage.goal,
                    stage.method,
                    *[
                        " ".join([
                            step.title,
                            step.desc,
                            step.method,
                            " ".join(output.name + " " + output.desc for output in step.outputs),
                            " ".join(expected.metric + " " + expected.target + " " + expected.desc for expected in step.expected),
                        ])
                        for step in stage.steps
                    ],
                ]
            )
            min_hits = max(2, min(4, len(topic_terms) // 4))
            if _hit_count(plan_text, topic_terms) < min_hits:
                issues.append("stages/steps drifted away from seed query and selected idea")
        stages_writable = target_sections is None or "stages" in target_sections or "expectedMetrics" in target_sections
        if stages_writable:
            for role in missing_plan_roles(package):
                issues.append(
                    f"stages missing required single-plan role: {role['label']} - {role['repairHint']}"
                )
        gate = validate_plan_package(package)
        gate = apply_review_to_quality_gate(package, gate)
        if gate.errors:
            issues.extend([f"quality gate: {error}" for error in gate.errors[:8]])
        if package.metaReview and package.metaReview.blockingIssues:
            issues.extend(
                f"{issue.sectionPath}: {issue.message}"
                for issue in package.metaReview.blockingIssues[:8]
            )
        return issues

    def _build_llm_repair_prompt(self, issues: List[str], target_sections: Optional[List[str]] = None) -> str:
        issue_text = "\n".join(f"- {issue}" for issue in issues[:12]) or "- invalid output"
        target_text = ", ".join(target_sections or ["researchQuestion", "hypothesis", "constants", "stages"])
        return (
            "Repair your previous answer.\n"
            "Problems:\n"
            f"{issue_text}\n"
            f"Revision target sections: {target_text}.\n"
            f"Schema rules: {llm_plan_output_schema_hint(target_sections)}\n"
            "Repair the same single PlanPackage. Do not generate multiple plan candidates, alternatives, options, or rankings.\n"
            "Return one complete valid JSON object only. Include writable keys only from: "
            "researchQuestion, hypothesis, constants, stages, background, gap, principle. "
            "Keep the same seed topic and selected idea. Preserve all evidence IDs. Do not add markdown or explanation."
        )

    def _build_llm_prompt(
        self,
        package: PlanPackage,
        *,
        max_stages: int,
        max_steps_per_stage: int,
        target_sections: Optional[List[str]] = None,
    ) -> str:
        writable_sections = target_sections or ["researchQuestion", "hypothesis", "constants", "stages"]
        locked_sections = [
            section
            for section in [
                "background",
                "gap",
                "principle",
                "literatureSurvey",
                "contributionStatement",
                "evidenceTrace",
                "sourceFields",
                "rawIdeaOutputs",
            ]
            if section not in writable_sections
        ]
        blueprint = build_plan_blueprint(
            package,
            max_stages=max_stages,
            max_steps_per_stage=max_steps_per_stage,
        )
        ablation_instruction = (
            "At least one step must define baselines/control comparisons, and at least one step must define ablation, sensitivity, robustness, or failure analysis."
            if blueprint.ablationRequirements
            else "For this paperType, do not force experiment ablation; use taxonomy/comparison/GAP-synthesis checks from the blueprint instead."
        )
        compact = {
            "readonlyContract": {
                "lockedSections": ["idea", *locked_sections],
                "writableSections": writable_sections,
                "maxStages": max_stages,
                "maxStepsPerStage": max_steps_per_stage,
                "note": "Plan describes intended implementation and validation design only; it must not claim executed results.",
            },
            "planBlueprint": blueprint.model_dump(),
            "singlePlanDesignBrief": build_single_plan_design_brief(package, max_stages=max_stages, max_steps_per_stage=max_steps_per_stage),
            "seedQuery": package.constants.get("seedQuery", ""),
            "domain": package.constants.get("domain", ""),
            "paperType": package.constants.get("paperType", ""),
            "topicAnchors": _topic_terms_from_package(package),
            "idea": package.idea.model_dump(),
            "background": package.background.model_dump(),
            "gap": package.gap.model_dump(),
            "principle": package.principle.model_dump(),
            "allowedEvidenceIds": self._allowed_evidence_ids(package),
            "paperSummaries": [
                {
                    "paperId": p.paperId,
                    "source": p.source,
                    "title": p.title,
                    "summary": p.summary,
                    "relevanceScore": p.relevanceScore,
                    "relevanceSignals": p.relevanceSignals[:8],
                    "relevanceReason": p.relevanceReason,
                    "methods": p.methods[:3],
                    "findings": p.findings[:3],
                    "limitations": p.limitations[:3],
                }
                for p in package.literatureSurvey.papers[:20]
            ],
            "humanFeedback": [
                {
                    "id": feedback.id,
                    "sectionPath": feedback.sectionPath,
                    "feedbackType": feedback.feedbackType,
                    "severity": feedback.severity,
                    "requestedAction": feedback.requestedAction,
                    "comment": feedback.comment,
                }
                for feedback in package.humanFeedback
                if not feedback.resolved
            ],
            "reviewFindings": {
                "decision": package.metaReview.decision if package.metaReview else "",
                "blockingIssues": [
                    {
                        "sectionPath": issue.sectionPath,
                        "message": issue.message,
                    }
                    for issue in (package.metaReview.blockingIssues if package.metaReview else [])[:12]
                ],
                "requiredRepairs": (package.metaReview.requiredRepairs if package.metaReview else [])[:12],
            },
        }
        return (
            "Return ONLY valid JSON. Include writable top-level keys only from: researchQuestion, hypothesis, constants, stages, background, gap, principle.\n"
            f"{llm_plan_output_schema_hint(writable_sections)}\n"
            "Do not return or rewrite literatureSurvey, contributionStatement, evidenceTrace, sourceFields, or rawIdeaOutputs.\n"
            "Generate exactly one coherent implementation plan for this PlanPackage. Do not generate multiple plan candidates, alternative options, or plan rankings.\n"
            "Use planBlueprint.requiredRoles as a hard checklist. If maxStages is small, combine multiple roles inside the same stage or step, but do not omit them.\n"
            "Follow planBlueprint.recommendedStageShape and paperType-specific template requirements.\n"
            "If background, gap, or principle are writable, revise wording and research logic only from the provided context. Preserve existing item IDs, evidenceRefs, paper IDs, KG IDs, probe IDs, and graph patch IDs.\n"
            f"Focus revision on these writable sections: {', '.join(writable_sections)}. Preserve already-good writable content unless it conflicts with feedback or reviewer findings.\n"
            "Stay faithful to seedQuery, topicAnchors, selected idea, selected GAP, and principle. Reject generic NLP/LLM plans when the topic is specific.\n"
            "Incorporate unresolved humanFeedback and reviewFindings by revising only writable plan fields.\n"
            "Use only the provided allowedEvidenceIds when adding evidenceRefs.\n"
            "stages[].steps[].outputs[].type must be one of metrics, chart, table, checkpoint, code, report, log.\n"
            "Each stage must contain steps, and each step should include evidenceRefs when possible.\n"
            f"{ablation_instruction}\n"
            "These are planned outputs and expected metrics, not executed results.\n"
            f"Return at most {max_stages} stages and at most {max_steps_per_stage} steps per stage. Prefer fewer, high-signal steps over long experiment checklists.\n"
            "Do not invent exact benchmark results, exact dataset sizes, or exact training budgets unless they are present in the context.\n"
            "Avoid null values. Use empty arrays/objects or concise 'to be specified downstream' strings when a value is unknown.\n"
            "Use this exact shape for every stage and step:\n"
            "{\n"
            '  "researchQuestion": "specific research question",\n'
            '  "hypothesis": "testable hypothesis",\n'
            '  "constants": {"datasets": ["planned dataset names"], "models": ["planned model names"]},\n'
            '  "stages": [\n'
            '    {\n'
            '      "id": "stage-1", "order": 1, "title": "specific stage title",\n'
            '      "goal": "specific stage goal", "method": "stage-level method", "dependsOn": [],\n'
            '      "steps": [\n'
            '        {\n'
            '          "id": "step-1-1", "order": 1, "title": "specific step title",\n'
            '          "desc": "detailed planned action, not a result",\n'
            '          "method": "concrete method for this step", "inputFrom": [],\n'
            '          "outputs": [{"type": "report", "name": "artifact_name.md", "desc": "planned artifact"}],\n'
            '          "expected": [{"metric": "planned_metric", "target": "planned target", "desc": "why this metric matters"}],\n'
            '          "evidenceRefs": [{"type": "gap", "id": "gap-1"}]\n'
            "        }\n"
            "      ]\n"
            "    }\n"
            "  ],\n"
            '  "background": {"summary": "optional revised summary", "motivation": "optional revised motivation", "currentLimitations": [], "domainContext": []},\n'
            '  "gap": {"summary": "optional revised gap summary", "selectedGapId": "existing-gap-id", "items": [{"id": "existing-gap-id", "statement": "specific gap", "existingCoverage": "covered part", "unresolvedIssue": "unresolved part", "proposedEntry": "entry point", "boundary": "scope", "validationNeeds": []}]},\n'
            '  "principle": {"summary": "optional revised principle", "mechanism": "optional mechanism", "noveltyClaim": "optional novelty claim", "assumptions": [], "risks": []}\n'
            "}\n"
            f"Context JSON:\n{json.dumps(compact, ensure_ascii=False, default=str)}"
        )

    def _allowed_evidence_ids(self, package: PlanPackage) -> Dict[str, List[str]]:
        return {
            "candidate": [package.idea.id],
            "gap": [item.id for item in package.gap.items],
            "paper": [paper.paperId for paper in package.literatureSurvey.papers],
            "probe": package.evidenceTrace.probeResultIds,
            "graph_patch": package.evidenceTrace.graphPatchIds,
            "path_seed": [
                item
                for item in package.principle.graphGrounding.pathSeedIds + ([package.source.pathSeedId] if package.source.pathSeedId else [])
                if item
            ],
            "kg_entity": package.principle.graphGrounding.entityIds,
            "kg_relation": package.principle.graphGrounding.relationIds,
            "literature_map": [package.evidenceTrace.literatureMapId] if package.evidenceTrace.literatureMapId else [],
            "reasoning_kg": [package.evidenceTrace.reasoningKgId] if package.evidenceTrace.reasoningKgId else [],
            "principle": ["principle"],
        }

    def _parse_evidence_refs(self, raw_refs: Any, package: PlanPackage, stage_index: int) -> List[PlanEvidenceRef]:
        allowed = self._allowed_evidence_ids(package)
        refs: List[PlanEvidenceRef] = []
        if isinstance(raw_refs, list):
            for raw_ref in raw_refs:
                if not isinstance(raw_ref, dict):
                    continue
                ref_type = str(raw_ref.get("type", "")).strip()
                ref_id = str(raw_ref.get("id", "")).strip()
                if ref_type in allowed and ref_id in allowed[ref_type]:
                    refs.append(
                        PlanEvidenceRef(
                            type=ref_type,
                            id=ref_id,
                            source=str(raw_ref.get("source", "")),
                            note=str(raw_ref.get("note", "")),
                        )
                    )
        if refs:
            if stage_index == 2:
                has_idea_ref = any(ref.type in {"candidate", "principle"} for ref in refs)
                if not has_idea_ref:
                    refs.extend([
                        PlanEvidenceRef(type="candidate", id=package.idea.id, source="idea"),
                        PlanEvidenceRef(type="principle", id="principle", source="idea_principle"),
                    ])
            return refs

        if stage_index == 1:
            defaults = [PlanEvidenceRef(type="gap", id=package.gap.selectedGapId, source="idea_gap")]
            if package.literatureSurvey.papers:
                paper = package.literatureSurvey.papers[0]
                defaults.append(PlanEvidenceRef(type="paper", id=paper.paperId, source=paper.source))
            return defaults
        if stage_index == 2:
            return [
                PlanEvidenceRef(type="candidate", id=package.idea.id, source="idea"),
                PlanEvidenceRef(type="principle", id="principle", source="idea_principle"),
            ]
        return [PlanEvidenceRef(type="candidate", id=package.idea.id, source="idea")]

    def _parse_llm_stages(
        self,
        raw_stages: List[Dict[str, Any]],
        package: PlanPackage,
        *,
        max_stages: int,
        max_steps_per_stage: int,
    ) -> List[PlanStage]:
        stages: List[PlanStage] = []
        for stage_index, raw_stage in enumerate(raw_stages[:max_stages], start=1):
            steps: List[PlanStep] = []
            if not isinstance(raw_stage, dict):
                raw_stage = {}
            stage_title = _first_text(raw_stage, ["title", "name"], f"Stage {stage_index}")
            for step_index, raw_step in enumerate((raw_stage.get("steps", []) or [])[:max_steps_per_stage], start=1):
                if isinstance(raw_step, str):
                    raw_step = {"title": raw_step, "desc": raw_step, "method": raw_step}
                if not isinstance(raw_step, dict):
                    raw_step = {}
                raw_outputs = [
                    item if isinstance(item, dict) else {"name": str(item), "type": "report"}
                    for item in (raw_step.get("outputs", []) or raw_step.get("artifacts", []) or [])
                    if str(item or "").strip().lower() not in {"", "none", "null", "undefined"}
                ]
                outputs = [
                    PlanOutput(
                        type=_normalize_output_type(raw_output.get("type", "report")),
                        name=_first_text(
                            raw_output,
                            ["name", "file", "filename", "artifact", "artifactName"],
                            f"stage_{stage_index}_step_{step_index}_output.{_normalize_output_type(raw_output.get('type', 'report'))}",
                        ),
                        desc=_first_text(raw_output, ["desc", "description", "details"], ""),
                        requiredFor=list(raw_output.get("requiredFor", []) or raw_output.get("consumedBy", []) or []),
                    )
                    for raw_output in raw_outputs
                ]
                raw_expected_items = [
                    item if isinstance(item, dict) else {"metric": str(item), "target": "specified before implementation"}
                    for item in (raw_step.get("expected", []) or raw_step.get("metrics", []) or [])
                    if str(item or "").strip().lower() not in {"", "none", "null", "undefined"}
                ]
                expected = [
                    PlanExpectedMetric(
                        metric=_first_text(raw_expected, ["metric", "name", "measure"], "primary_metric"),
                        target=_first_text(raw_expected, ["target", "successCriteria", "criterion", "expected"], "specified before implementation"),
                        desc=_first_text(raw_expected, ["desc", "description", "rationale"], ""),
                    )
                    for raw_expected in raw_expected_items
                ]
                step_title = _first_text(
                    raw_step,
                    ["title", "name", "action", "task"],
                    f"{stage_title} task {step_index}",
                )
                step_desc = _first_text(
                    raw_step,
                    ["desc", "description", "details", "rationale"],
                    f"Plan and document {step_title}.",
                )
                step_method = _first_text(
                    raw_step,
                    ["method", "approach", "procedure", "implementationMethod"],
                    step_desc,
                )
                steps.append(
                    PlanStep(
                        id=str(raw_step.get("id") or f"step-{stage_index}-{step_index}").strip(),
                        order=_safe_int(raw_step.get("order", step_index), step_index),
                        title=step_title,
                        desc=step_desc,
                        method=step_method,
                        inputFrom=_clean_string_list(raw_step.get("inputFrom", []) or []),
                        outputs=outputs or [
                            PlanOutput(type="report", name=f"stage_{stage_index}_step_{step_index}.md")
                        ],
                        expected=expected or [
                            PlanExpectedMetric(metric="readiness", target="specified before implementation")
                        ],
                        evidenceRefs=self._parse_evidence_refs(raw_step.get("evidenceRefs", []), package, stage_index),
                    )
                )
            stages.append(
                PlanStage(
                    id=str(raw_stage.get("id") or f"stage-{stage_index}").strip(),
                    order=_safe_int(raw_stage.get("order", stage_index), stage_index),
                    title=stage_title,
                    goal=_first_text(raw_stage, ["goal", "objective", "purpose"], f"Complete {stage_title}."),
                    method=_first_text(raw_stage, ["method", "approach", "procedure"], f"Plan and execute {stage_title} in downstream modules."),
                    dependsOn=_clean_string_list(raw_stage.get("dependsOn", []) or []),
                    steps=steps or [
                        PlanStep(
                            id=f"step-{stage_index}-1",
                            order=1,
                            title="Default plan step",
                            desc="Default step inserted because the LLM omitted steps.",
                            method="Complete this step before downstream implementation.",
                            outputs=[PlanOutput(type="report", name=f"stage_{stage_index}_plan.md")],
                            expected=[PlanExpectedMetric(metric="readiness", target="specified before implementation")],
                            evidenceRefs=self._parse_evidence_refs([], package, stage_index),
                        )
                    ],
                )
            )
        return self._sanitize_stage_dependencies(stages)

    def _sanitize_stage_dependencies(self, stages: List[PlanStage]) -> List[PlanStage]:
        stage_ids = {stage.id for stage in stages}
        step_ids = {step.id for stage in stages for step in stage.steps}
        for stage in stages:
            stage.dependsOn = [dep for dep in stage.dependsOn if dep in stage_ids]
            for step in stage.steps:
                step.inputFrom = [ref for ref in step.inputFrom if ref in step_ids]
        return stages


_service: Optional[PlanPackageService] = None


def get_plan_package_service() -> PlanPackageService:
    global _service
    if _service is None:
        _service = PlanPackageService()
    return _service
