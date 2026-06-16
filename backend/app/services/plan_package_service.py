"""PlanPackage orchestration service."""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional

from app.llm.provider_client import ChatMessage, get_provider_client
from app.models.idea import IdeaCandidate, IdeaSession
from app.models.plan_package import PlanEvidenceRef, PlanExpectedMetric, PlanOutput, PlanPackage, PlanStage, PlanStep
from app.models.research_plan import ExpectedOutcomes, Methodology, ResearchApproach, ResearchPlan, Variables
from app.services.plan_package_builder import build_plan_package
from app.services.plan_package_validator import validate_plan_package
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
from app.storage.research_plan_storage import get_storage as get_research_plan_storage

logger = logging.getLogger(__name__)


class PlanPackageNotFoundError(ValueError):
    pass


class PlanPackageConflictError(ValueError):
    pass


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
        self.research_plan_storage = get_research_plan_storage()

    def get(self, package_id: str) -> Optional[PlanPackage]:
        return self.package_storage.get(package_id)

    def get_by_idea_session(self, idea_session_id: str) -> Optional[PlanPackage]:
        return self.package_storage.get_by_idea_session(idea_session_id)

    def get_by_plan_session(self, plan_session_id: str) -> Optional[PlanPackage]:
        return self.package_storage.get_by_plan_session(plan_session_id)

    def validate(self, package_id: str) -> PlanPackage:
        package = self.package_storage.get(package_id)
        if not package:
            raise PlanPackageNotFoundError(f"PlanPackage {package_id} not found")
        package.qualityGate = validate_plan_package(package)
        return self.package_storage.update(package)

    def to_research_plan(self, package_id: str) -> ResearchPlan:
        package = self.package_storage.get(package_id)
        if not package:
            raise PlanPackageNotFoundError(f"PlanPackage {package_id} not found")

        metric_names: List[str] = []
        first_target = "specified before implementation"
        for stage in package.stages:
            for step in stage.steps:
                for expected in step.expected:
                    if expected.metric:
                        metric_names.append(expected.metric)
                    if first_target == "specified before implementation" and expected.target:
                        first_target = expected.target

        dataset = package.constants.get("dataset") or package.constants.get("datasets")
        if isinstance(dataset, list):
            datasets = [str(item) for item in dataset if str(item).strip()]
        elif dataset:
            datasets = [str(dataset)]
        else:
            datasets = ["PlanPackage literature survey"]

        controls = [str(key) for key in package.constants.keys()]
        variables = Variables(
            independent=["implementation_method"],
            dependent=metric_names[:5] or ["implementation_readiness"],
            controls=controls,
        )
        methodology = Methodology(
            direction_id=str(package.constants.get("directionId") or package.source.ideaSessionId),
            approach=ResearchApproach.BASELINE_ESTABLISHMENT,
            datasets=datasets,
            template_id="plan_package_adapter",
        )
        expected_outcomes = ExpectedOutcomes(
            primary_metric=(metric_names[0] if metric_names else "implementation_readiness"),
            success_criteria=first_target,
        )
        research_question = package.researchQuestion.strip()
        if len(research_question) < 10:
            research_question = f"Can {package.idea.title or package.source.ideaCandidateId} address the selected research gap?"
        hypothesis = (
            package.hypothesis
            or package.idea.hypothesisStatement
            or package.idea.keyInsight
            or "The implementation plan will make the selected idea testable."
        ).strip()
        if len(hypothesis) < 10:
            hypothesis = "The implementation plan will make the selected idea testable."
        plan = ResearchPlan(
            id=f"plan_{uuid.uuid4().hex[:12]}",
            research_question=research_question,
            hypothesis=hypothesis,
            variables=variables,
            methodology=methodology,
            expected_outcomes=expected_outcomes,
            tags=["plan-package", "idea-plan"],
            notes=(
                f"Adapted from PlanPackage {package.packageId}. "
                f"Gap: {package.gap.summary}. Principle: {package.principle.summary}"
            ),
            source_session_id=package.source.ideaSessionId,
            source_candidate_id=package.source.ideaCandidateId,
            source_title=package.idea.title,
        )
        created_plan = self.research_plan_storage.create(plan)
        package.source.selectedResearchPlanId = created_plan.id
        self.package_storage.update(package)
        return created_plan

    def create_from_idea_session(
        self,
        idea_session_id: str,
        *,
        candidate_id: Optional[str] = None,
        plan_session_id: Optional[str] = None,
        max_stages: int = 3,
        max_steps_per_stage: int = 3,
        user_notes: Optional[str] = None,
        use_llm: Optional[bool] = None,
        generation_mode: str = "hybrid",
        max_repair_rounds: int = 1,
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
            plan_session_id=plan_session_id,
            user_notes=user_notes,
            max_stages=max_stages,
            max_steps_per_stage=max_steps_per_stage,
        )
        package.constants.setdefault("seedQuery", session.config.seedQuery)
        if session.config.domain:
            package.constants.setdefault("domain", session.config.domain)
        package.constants.setdefault("paperType", session.config.paperType)

        generation_warnings: List[str] = []
        mode = (generation_mode or "hybrid").strip().lower()
        if use_llm is not None:
            mode = "hybrid" if use_llm else "deterministic"
        if mode not in {"deterministic", "hybrid"}:
            raise ValueError("generationMode must be one of: deterministic, hybrid")

        package.generation.mode = mode
        package.generation.repairRounds = 0
        package.generation.fallbackUsed = mode == "deterministic"
        package.generation.promptVersion = (
            "plan-package-implementation-planner-v1"
            if mode == "hybrid"
            else "plan-package-adapter-v1"
        )
        package.sourceFields.implementationPlan = [
            "LLM implementation planner" if mode == "hybrid" else "deterministic fallback stage builder",
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

        package.qualityGate = validate_plan_package(package)
        package.qualityGate.warnings.extend(generation_warnings)
        package.generation.warnings.extend(generation_warnings)
        return self.package_storage.create(package)

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
    ) -> None:
        client = get_provider_client(session.config.providerName)
        package.generation.providerName = session.config.providerName
        package.generation.model = session.config.model
        package.generation.promptVersion = "plan-package-implementation-planner-v1"
        prompt = self._build_llm_prompt(
            package,
            max_stages=max_stages,
            max_steps_per_stage=max_steps_per_stage,
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
        attempts = max(1, max_repair_rounds + 1)
        for attempt in range(attempts):
            messages = list(base_messages)
            if attempt > 0:
                messages.extend([
                    ChatMessage(role="assistant", content=last_response_text[:4000]),
                    ChatMessage(
                        role="user",
                        content=self._build_llm_repair_prompt(last_issues),
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
            parsed = _extract_json(last_response_text)
            if not parsed:
                last_issues = ["LLM did not return one complete valid JSON object"]
                continue

            candidate_package = package.model_copy(deep=True)
            self._apply_parsed_plan_fields(
                candidate_package,
                parsed,
                max_stages=max_stages,
                max_steps_per_stage=max_steps_per_stage,
            )
            last_issues = self._validate_generated_plan_fields(candidate_package)
            if last_issues:
                continue

            self._apply_parsed_plan_fields(
                package,
                parsed,
                max_stages=max_stages,
                max_steps_per_stage=max_steps_per_stage,
            )
            package.generation.llmUsedSections = ["implementationPlan"]
            package.generation.repairRounds = attempt
            package.generation.fallbackUsed = False
            return

        raise ValueError("LLM plan field generation failed validation: " + "; ".join(last_issues))

    def _apply_parsed_plan_fields(
        self,
        package: PlanPackage,
        parsed: Dict[str, Any],
        *,
        max_stages: int,
        max_steps_per_stage: int,
    ) -> None:
        if isinstance(parsed.get("researchQuestion"), str) and parsed["researchQuestion"].strip():
            package.researchQuestion = parsed["researchQuestion"].strip()
        if isinstance(parsed.get("hypothesis"), str):
            package.hypothesis = parsed["hypothesis"].strip()
        if isinstance(parsed.get("constants"), dict):
            protected = {"ideaSessionId", "ideaCandidateId", "planStage", "seedQuery", "domain", "paperType"}
            for key, value in parsed["constants"].items():
                if key not in protected:
                    package.constants[key] = value
        if isinstance(parsed.get("stages"), list) and parsed["stages"]:
            package.stages = self._parse_llm_stages(
                parsed["stages"],
                package,
                max_stages=max_stages,
                max_steps_per_stage=max_steps_per_stage,
            )

    def _validate_generated_plan_fields(self, package: PlanPackage) -> List[str]:
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
        return issues

    def _build_llm_repair_prompt(self, issues: List[str]) -> str:
        issue_text = "\n".join(f"- {issue}" for issue in issues[:12]) or "- invalid output"
        return (
            "Repair your previous answer.\n"
            "Problems:\n"
            f"{issue_text}\n"
            "Return one complete valid JSON object only, with exactly these top-level keys: "
            "researchQuestion, hypothesis, constants, stages. "
            "Keep the same seed topic and selected idea. Do not add markdown or explanation."
        )

    def _build_llm_prompt(
        self,
        package: PlanPackage,
        *,
        max_stages: int,
        max_steps_per_stage: int,
    ) -> str:
        compact = {
            "readonlyContract": {
                "lockedSections": ["idea", "background", "literatureSurvey", "gap", "principle", "evidenceTrace"],
                "writableSections": ["researchQuestion", "hypothesis", "constants", "stages"],
                "maxStages": max_stages,
                "maxStepsPerStage": max_steps_per_stage,
                "note": "Plan describes intended implementation and validation design only; it must not claim executed results.",
            },
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
                    "methods": p.methods[:3],
                    "findings": p.findings[:3],
                    "limitations": p.limitations[:3],
                }
                for p in package.literatureSurvey.papers[:20]
            ],
        }
        return (
            "Return ONLY valid JSON with keys researchQuestion, hypothesis, constants, stages.\n"
            "Do not return or rewrite background, literatureSurvey, gap, principle, evidenceTrace, sourceFields, or rawIdeaOutputs.\n"
            "Stay faithful to seedQuery, topicAnchors, selected idea, selected GAP, and principle. Reject generic NLP/LLM plans when the topic is specific.\n"
            "Use only the provided allowedEvidenceIds when adding evidenceRefs.\n"
            "stages[].steps[].outputs[].type must be one of metrics, chart, table, checkpoint, code, report, log.\n"
            "Each stage must contain steps, and each step should include evidenceRefs when possible.\n"
            "These are planned outputs and expected metrics, not executed results.\n"
            f"Return at most {max_stages} stages and at most {max_steps_per_stage} steps per stage. Prefer fewer, high-signal steps over long experiment checklists.\n"
            "Do not invent exact benchmark results, exact dataset sizes, or exact training budgets unless they are present in the context.\n"
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
            "  ]\n"
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
            stage_title = _first_text(raw_stage, ["title", "name"], f"Stage {stage_index}")
            for step_index, raw_step in enumerate((raw_stage.get("steps", []) or [])[:max_steps_per_stage], start=1):
                if isinstance(raw_step, str):
                    raw_step = {"title": raw_step, "desc": raw_step, "method": raw_step}
                if not isinstance(raw_step, dict):
                    raw_step = {}
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
                    for raw_output in [
                        item if isinstance(item, dict) else {"name": str(item), "type": "report"}
                        for item in (raw_step.get("outputs", []) or raw_step.get("artifacts", []) or [])
                    ]
                ]
                expected = [
                    PlanExpectedMetric(
                        metric=_first_text(raw_expected, ["metric", "name", "measure"], "primary_metric"),
                        target=_first_text(raw_expected, ["target", "successCriteria", "criterion", "expected"], "specified before implementation"),
                        desc=_first_text(raw_expected, ["desc", "description", "rationale"], ""),
                    )
                    for raw_expected in [
                        item if isinstance(item, dict) else {"metric": str(item), "target": "specified before implementation"}
                        for item in (raw_step.get("expected", []) or raw_step.get("metrics", []) or [])
                    ]
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
                        id=str(raw_step.get("id", f"step-{stage_index}-{step_index}")),
                        order=int(raw_step.get("order", step_index)),
                        title=step_title,
                        desc=step_desc,
                        method=step_method,
                        inputFrom=list(raw_step.get("inputFrom", []) or []),
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
                    id=str(raw_stage.get("id", f"stage-{stage_index}")),
                    order=int(raw_stage.get("order", stage_index)),
                    title=stage_title,
                    goal=_first_text(raw_stage, ["goal", "objective", "purpose"], f"Complete {stage_title}."),
                    method=_first_text(raw_stage, ["method", "approach", "procedure"], f"Plan and execute {stage_title} in downstream modules."),
                    dependsOn=list(raw_stage.get("dependsOn", []) or []),
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
