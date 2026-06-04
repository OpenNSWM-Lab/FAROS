"""
Plan Generation Service

Orchestrates plan generation using real LLM calls.
Follows the same pattern as idea_service.py.
"""

import json
import re
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

from app.models.plan_session import (
    PlanSession,
    PlanSessionStatus,
    PlanSessionConfig,
    CandidatePlan,
    SelectedPlan,
    ScoreBreakdown,
    ExperimentDesign,
    PlanWorkflowTrace,
    PlanStepResult,
    PAPER_TYPE_LABELS,
)
from app.storage.plan_session_storage import (
    get_session_storage,
    get_candidate_storage,
    get_selected_storage,
    generate_plan_session_id,
    generate_candidate_plan_id,
    generate_selected_plan_id,
)
from app.llm.provider_client import get_provider_client, ChatMessage, ProviderError
from app.services.plan_builder import build_research_plan_from_candidate
from app.models.idea import IdeaCandidate, DraftPlan, RiskItem, ExperimentSpec
from app.storage.research_plan_storage import get_storage as get_plan_storage

logger = logging.getLogger(__name__)


PLAN_GENERATION_PROMPT = """You are a senior research scientist. Given the following research context, generate {max_candidates} distinct candidate research plans.

**Research Context:**
- Topic/Seed Query: {seed_query}
- Paper Type: {paper_type_label}
- Direction: {direction_title}
{idea_context}
{user_notes_section}

**Instructions:**
Generate exactly {max_candidates} candidate plans. Each plan should be distinct in approach, methodology, or focus.
For each candidate, provide detailed and realistic content. Scores should be non-uniform (vary between candidates).

**Output Format (strict JSON):**
```json
{{
  "candidates": [
    {{
      "title": "Concise plan title",
      "planAbstract": "2-3 sentence abstract describing the plan",
      "novelty": "What is novel about this approach",
      "feasibility": "Assessment of implementation feasibility",
      "risks": "Key risks and mitigation strategies",
      "gapAnalysis": "What gap in current research this addresses",
      "method": "High-level methodology description",
      "experimentDesign": {{
        "research_question": "Clear research question ending with ?",
        "hypothesis": "Testable hypothesis statement",
        "variables": {{
          "independent": ["var1", "var2"],
          "dependent": ["metric1", "metric2"],
          "controls": ["control1"]
        }},
        "methodology": {{
          "approach": "comparative_training or ablation_study or baseline_establishment",
          "datasets": ["Dataset1", "Dataset2"],
          "steps": ["Step 1", "Step 2", "Step 3"]
        }},
        "expected_outcomes": {{
          "primary_metric": "main_metric_name",
          "success_criteria": "Clear criterion for success",
          "baseline_comparison": "What baseline to compare against"
        }}
      }},
      "evaluationProtocol": {{
        "metrics": ["metric1", "metric2"],
        "baselines": ["baseline1", "baseline2"],
        "ablations": ["ablation1"],
        "statistical_tests": ["t-test", "bootstrap CI"]
      }},
      "resourcesEstimate": "Estimated compute/time/data requirements",
      "scoreBreakdown": {{
        "novelty": 7.5,
        "feasibility": 8.0,
        "impact": 6.5,
        "clarity": 8.5,
        "risk": 3.0,
        "overall": 7.3,
        "rationale": "Brief rationale for scores"
      }}
    }}
  ]
}}
```

IMPORTANT: Return ONLY valid JSON. No markdown fences, no extra text outside the JSON.
Ensure scores are non-uniform across candidates (not all the same).
"""


def _extract_json(text: str) -> Optional[Dict]:
    """Extract JSON from LLM response, handling markdown fences."""
    text = text.strip()
    # Remove markdown fences
    if "```json" in text:
        text = text.split("```json", 1)[1]
        if "```" in text:
            text = text.rsplit("```", 1)[0]
    elif "```" in text:
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1]
        elif len(parts) >= 2:
            text = parts[1]

    text = text.strip()

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return None


class PlanGenerationService:
    """Service for managing plan generation sessions."""

    def __init__(self):
        self.session_storage = get_session_storage()
        self.candidate_storage = get_candidate_storage()
        self.selected_storage = get_selected_storage()

    def create_session(self, config: PlanSessionConfig) -> PlanSession:
        session = PlanSession(
            id=generate_plan_session_id(),
            config=config,
            status=PlanSessionStatus.PENDING,
            createdAt=datetime.utcnow(),
        )
        return self.session_storage.create(session)

    def get_session(self, session_id: str) -> Optional[PlanSession]:
        return self.session_storage.get(session_id)

    def list_sessions(self, status: Optional[PlanSessionStatus] = None) -> List[PlanSession]:
        return self.session_storage.list_all(status)

    def start_session(self, session_id: str) -> PlanSession:
        session = self.session_storage.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        if session.status != PlanSessionStatus.PENDING:
            raise ValueError(f"Cannot start session in {session.status} state")

        session.status = PlanSessionStatus.RUNNING
        session.startedAt = datetime.utcnow()
        session.trace = PlanWorkflowTrace(
            sessionId=session_id,
            startedAt=datetime.utcnow(),
        )
        return self.session_storage.update(session)

    def cancel_session(self, session_id: str) -> PlanSession:
        session = self.session_storage.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        if session.is_terminal():
            raise ValueError(f"Cannot cancel session in {session.status} state")

        session.status = PlanSessionStatus.CANCELLED
        session.endedAt = datetime.utcnow()
        if session.trace:
            session.trace.endedAt = datetime.utcnow()
        return self.session_storage.update(session)

    def get_candidates(self, session_id: str) -> List[CandidatePlan]:
        return self.candidate_storage.list_by_session(session_id)

    def get_candidate(self, candidate_id: str) -> Optional[CandidatePlan]:
        return self.candidate_storage.get(candidate_id)

    def run_pipeline(self, session_id: str):
        """Run the plan generation pipeline (called in background)."""
        session = self.session_storage.get(session_id)
        if not session:
            logger.error(f"Session {session_id} not found for pipeline")
            return

        try:
            # Step 1: Build prompt
            step_start = datetime.utcnow()
            prompt = self._build_prompt(session)
            self._add_step(session, "build_prompt", "ok", step_start)

            # Step 2: Call LLM
            step_start = datetime.utcnow()
            raw_response = self._call_llm(session, prompt)
            self._add_step(session, "call_llm", "ok", step_start)

            # Step 3: Parse response
            step_start = datetime.utcnow()
            parsed = self._parse_response(raw_response, session)
            self._add_step(session, "parse_response", "ok", step_start)

            # Step 4: Create candidates
            step_start = datetime.utcnow()
            candidates = self._create_candidates(session, parsed)
            self._add_step(session, "create_candidates", "ok", step_start)

            # Step 5: Rank candidates
            step_start = datetime.utcnow()
            self._rank_candidates(candidates)
            self._add_step(session, "rank_candidates", "ok", step_start)

            # Complete session
            session.status = PlanSessionStatus.COMPLETED
            session.endedAt = datetime.utcnow()
            session.duration = int((session.endedAt - session.startedAt).total_seconds()) if session.startedAt else 0
            session.candidateIds = [c.id for c in candidates]
            if session.trace:
                session.trace.endedAt = datetime.utcnow()
            self.session_storage.update(session)

        except Exception as e:
            logger.error(f"Pipeline failed for session {session_id}: {e}", exc_info=True)
            session = self.session_storage.get(session_id)
            if session:
                session.status = PlanSessionStatus.FAILED
                session.endedAt = datetime.utcnow()
                session.errorMessage = str(e)
                if session.trace:
                    session.trace.endedAt = datetime.utcnow()
                self.session_storage.update(session)

    def _add_step(self, session: PlanSession, name: str, status: str, start: datetime, error: str = None):
        end = datetime.utcnow()
        step = PlanStepResult(
            name=name,
            status=status,
            startedAt=start,
            endedAt=end,
            durationSeconds=(end - start).total_seconds(),
            error=error,
        )
        if session.trace:
            session.trace.steps.append(step)
            session.trace.totalSteps = len(session.trace.steps)
            session.trace.successfulSteps = sum(1 for s in session.trace.steps if s.status == "ok")
            session.trace.failedSteps = sum(1 for s in session.trace.steps if s.status == "failed")
        self.session_storage.update(session)

    def _build_prompt(self, session: PlanSession) -> str:
        config = session.config
        paper_type_label = PAPER_TYPE_LABELS.get(config.paperType, config.paperType)
        direction_title = config.directionTitle or config.directionId or "General"
        seed_query = config.ideaSeedQuery or config.directionTitle or "Research topic"

        idea_context = ""
        if config.ideaCandidateTitle:
            idea_context = f"- Source Idea: {config.ideaCandidateTitle}\n"
        if config.ideaCandidateId:
            idea_context += f"- Idea Candidate ID: {config.ideaCandidateId}\n"

        user_notes_section = ""
        if config.userNotes:
            user_notes_section = f"- Additional Notes: {config.userNotes}\n"

        return PLAN_GENERATION_PROMPT.format(
            max_candidates=config.maxCandidates,
            seed_query=seed_query,
            paper_type_label=paper_type_label,
            direction_title=direction_title,
            idea_context=idea_context,
            user_notes_section=user_notes_section,
        )

    def _call_llm(self, session: PlanSession, prompt: str) -> str:
        config = session.config
        client = get_provider_client(config.providerName)
        timeout = max(client.config.timeout, client.settings.PLAN_GENERATION_TIMEOUT)
        messages = [
            ChatMessage(role="system", content="You are a senior research scientist who generates detailed research plans in strict JSON format."),
            ChatMessage(role="user", content=prompt),
        ]

        response = client.chat(
            messages=messages,
            model=config.model,
            temperature=0.8,
            max_tokens=4096,
            timeout=timeout,
        )
        return response.text

    def _parse_response(self, raw: str, session: PlanSession, retry_count: int = 0) -> Dict:
        parsed = _extract_json(raw)
        if parsed and "candidates" in parsed:
            return parsed

        # Retry with repair prompt
        if retry_count < 2:
            logger.warning(f"JSON parse failed (attempt {retry_count + 1}), retrying with repair prompt")
            config = session.config
            client = get_provider_client(config.providerName)
            repair_prompt = (
                "The following text was supposed to be valid JSON with a 'candidates' array, "
                "but it failed to parse. Please fix it and return ONLY valid JSON:\n\n"
                f"{raw[:3000]}"
            )
            messages = [ChatMessage(role="user", content=repair_prompt)]
            timeout = max(client.config.timeout, client.settings.PLAN_GENERATION_TIMEOUT)
            response = client.chat(
                messages=messages,
                model=config.model,
                temperature=0,
                max_tokens=4096,
                timeout=timeout,
            )
            return self._parse_response(response.text, session, retry_count + 1)

        raise ValueError(f"Failed to parse LLM response as JSON after retries. Raw: {raw[:500]}")

    def _create_candidates(self, session: PlanSession, parsed: Dict) -> List[CandidatePlan]:
        raw_candidates = parsed.get("candidates", [])
        candidates = []

        for idx, raw in enumerate(raw_candidates, start=1):
            cid = generate_candidate_plan_id()

            # Parse score breakdown
            sb_raw = raw.get("scoreBreakdown", {})
            score_breakdown = ScoreBreakdown(
                novelty=float(sb_raw.get("novelty", 5.0)),
                feasibility=float(sb_raw.get("feasibility", 5.0)),
                impact=float(sb_raw.get("impact", 5.0)),
                clarity=float(sb_raw.get("clarity", 5.0)),
                risk=float(sb_raw.get("risk", 5.0)),
                overall=float(sb_raw.get("overall", 5.0)),
                rationale=str(sb_raw.get("rationale", "")),
            )

            # Parse experiment design
            ed_raw = raw.get("experimentDesign", {})
            experiment_design = ExperimentDesign(
                research_question=str(ed_raw.get("research_question", "")),
                hypothesis=str(ed_raw.get("hypothesis", "")),
                variables=ed_raw.get("variables", {}),
                methodology=ed_raw.get("methodology", {}),
                expected_outcomes=ed_raw.get("expected_outcomes", {}),
            )

            # Parse evaluation protocol
            eval_raw = raw.get("evaluationProtocol", {})

            candidate = CandidatePlan(
                id=cid,
                sessionId=session.id,
                indexNumber=idx,
                title=str(raw.get("title", f"Plan {idx}")),
                planAbstract=str(raw.get("planAbstract", "")),
                novelty=str(raw.get("novelty", "")),
                feasibility=str(raw.get("feasibility", "")),
                risks=str(raw.get("risks", "")),
                gapAnalysis=str(raw.get("gapAnalysis", "")),
                method=str(raw.get("method", "")),
                experimentDesign=experiment_design,
                evaluationProtocol=eval_raw,
                ablations=eval_raw.get("ablations", []) if isinstance(eval_raw, dict) else [],
                baselines=eval_raw.get("baselines", []) if isinstance(eval_raw, dict) else [],
                resourcesEstimate=str(raw.get("resourcesEstimate", "")),
                scoreBreakdown=score_breakdown,
                overallScore=score_breakdown.overall,
                createdAt=datetime.utcnow(),
            )
            self.candidate_storage.create(candidate)
            candidates.append(candidate)

        return candidates

    def _rank_candidates(self, candidates: List[CandidatePlan]):
        """Sort candidates by overall score descending and re-index."""
        candidates.sort(key=lambda c: c.overallScore, reverse=True)
        for i, c in enumerate(candidates, start=1):
            c.indexNumber = i
            self.candidate_storage.create(c)  # overwrite with new index

    def select_candidate(self, session_id: str, candidate_id: str) -> Dict[str, str]:
        """Select a candidate plan and create a ResearchPlan from it."""
        session = self.session_storage.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        candidate = self.candidate_storage.get(candidate_id)
        if not candidate:
            raise ValueError(f"Candidate {candidate_id} not found")

        # Build a minimal IdeaCandidate to reuse plan_builder
        idea_candidate = IdeaCandidate(
            id=candidate.id,
            sessionId=session_id,
            title=candidate.title,
            problem=candidate.planAbstract,
            keyInsight=candidate.novelty,
            novelty=candidate.scoreBreakdown.novelty,
            noveltyRationale=candidate.novelty,
            feasibility=candidate.scoreBreakdown.feasibility,
            feasibilityRationale=candidate.feasibility,
            impact=candidate.scoreBreakdown.impact,
            impactRationale=candidate.gapAnalysis,
            overallScore=candidate.overallScore,
            risks=[],
            requiredExperiments=[],
            expectedMetrics=list(candidate.experimentDesign.expected_outcomes.get("primary_metric", "accuracy").split(",")) if isinstance(candidate.experimentDesign.expected_outcomes.get("primary_metric"), str) else ["accuracy"],
            draftPlan=DraftPlan(
                researchQuestion=candidate.experimentDesign.research_question,
                hypothesis=candidate.experimentDesign.hypothesis,
                variables=candidate.experimentDesign.variables,
                expectedOutcomes=list(candidate.experimentDesign.expected_outcomes.values()) if candidate.experimentDesign.expected_outcomes else [],
            ),
            references=[],
            createdAt=datetime.utcnow(),
        )

        seed_query = session.config.ideaSeedQuery or session.config.directionTitle or "Research"
        paper_type = session.config.paperType.replace("_", "")  # map to plan_builder types

        # Map PaperType enum to plan_builder types
        paper_type_map = {
            "algorithmic_method": "algorithm",
            "systems_infrastructure": "system",
            "application_domain": "application",
            "survey_tutorial": "survey",
            "benchmark_dataset": "benchmark",
            "evaluation_metrics": "evaluation",
            "security_robustness": "safety",
            "theory_analysis": "theory",
            "multimodal_agent": "application",
        }
        mapped_type = paper_type_map.get(session.config.paperType, "algorithm")

        plan = build_research_plan_from_candidate(
            candidate=idea_candidate,
            seed_query=seed_query,
            paper_type=mapped_type,
            session_id=session_id,
            candidate_index=candidate.indexNumber,
            direction_id=session.config.directionId,
        )

        # Save plan
        plan_storage = get_plan_storage()
        created_plan = plan_storage.create(plan)

        # Save selection record
        sel = SelectedPlan(
            id=generate_selected_plan_id(),
            sessionId=session_id,
            candidateId=candidate_id,
            researchPlanId=created_plan.id,
            createdAt=datetime.utcnow(),
        )
        self.selected_storage.create(sel)

        # Update session
        session.selectedCandidateId = candidate_id
        session.resultingPlanId = created_plan.id
        self.session_storage.update(session)

        return {
            "selectedPlanId": sel.id,
            "researchPlanId": created_plan.id,
            "candidateId": candidate_id,
        }


# Singleton
_service: Optional[PlanGenerationService] = None


def get_plan_generation_service() -> PlanGenerationService:
    global _service
    if _service is None:
        _service = PlanGenerationService()
    return _service
