import json
from pathlib import Path
from typing import Any, Dict

from app.contracts import RunMode, ScientificQuestion
from app.core.paths import get_data_dir
from app.faros.capabilities.base import BaseCapability
from app.faros.models.artifact import ArtifactRecord
from app.faros.models.capability import CapabilityResult
from app.faros.models.execution import ExecutionContext
from app.modules.idea.contracts import IdeaSessionConfig
from app.modules.idea.research_dossier import build_research_dossier
from app.modules.idea.service import get_idea_service


class IdeaRefinementCapability(BaseCapability):
    capability_id = "idea_refinement"
    description = "Run the existing LLM Scientist idea pipeline and normalize outputs for FAROS."
    default_agent_id = "researcher"
    default_skill_ids = ["literature-grounding", "idea-analysis"]
    artifact_types = ["idea_session", "research_dossier"]

    def execute(self, context: ExecutionContext, inputs: Dict[str, Any]) -> CapabilityResult:
        binding = context.get_binding() or context.get_binding(self.capability_id)
        provider_name = binding.provider if binding else inputs.get("providerName", "moonshot")
        model = binding.model if binding and binding.model else inputs.get("model", "moonshot-v1-8k")

        config = IdeaSessionConfig(
            providerName=provider_name,
            model=model,
            seedQuery=inputs.get("seedQuery") or inputs.get("topic") or "AutoResearch idea exploration",
            paperType=inputs.get("paperType", "algorithm"),
            maxCandidates=inputs.get("maxCandidates", 5),
            maxPapers=inputs.get("maxPapers", 10),
            searchBudget=inputs.get("searchBudget", inputs.get("maxPapers", 10)),
            maxReviewIterations=inputs.get("maxReviewIterations", 3),
            domain=inputs.get("domain"),
            constraints=inputs.get("constraints"),
            mustCiteList=inputs.get("mustCiteList"),
        )

        service = get_idea_service()
        resume_session_id = str(
            inputs.get("resumeIdeaSessionId") or inputs.get("ideaSessionId") or ""
        ).strip()
        session = service.get_session(resume_session_id) if resume_session_id else None
        if resume_session_id and session is None:
            raise ValueError(f"Idea session '{resume_session_id}' was not found")
        if session is not None:
            if session.config.seedQuery != config.seedQuery:
                raise ValueError("The resumed Idea session belongs to a different seed query")
            session_status = getattr(session.status, "value", str(session.status))
            if session_status in {"awaiting_evidence", "awaiting_ideas"}:
                service.resume_session(session.id)
                session = service.run_pipeline(session.id)
            elif session_status != "completed":
                raise ValueError(
                    f"Idea session '{session.id}' cannot be reused from status '{session_status}'"
                )
        else:
            session = service.create_session(config)
            service.start_session(session.id)
            session = service.run_pipeline(session.id)
        candidates = service.get_candidates(session.id)

        candidate_dicts = [candidate.model_dump() for candidate in candidates]
        selected = None
        if session.selectedCandidateId:
            selected = next((c for c in candidate_dicts if c["id"] == session.selectedCandidateId), None)
        if selected is None and candidate_dicts:
            candidate_dicts.sort(key=lambda item: item.get("overallScore", 0), reverse=True)
            selected = candidate_dicts[0]

        session_status = getattr(session.status, "value", str(session.status))
        artifacts = [
            ArtifactRecord(
                id=f"{context.run_id}:{self.capability_id}:session",
                type="idea_session",
                uri=f"idea://{session.id}",
                producer=self.capability_id,
                summary=f"Idea session {session.id} with {len(candidate_dicts)} candidates",
                metadata={"sessionId": session.id, "selectedCandidateId": selected["id"] if selected else None},
            )
        ]
        if session_status != "completed" or selected is None:
            return CapabilityResult(
                status="failed",
                outputs={
                    "ideaSessionId": session.id,
                    "candidateCount": len(candidate_dicts),
                    "selectedCandidateId": None,
                    "selectedCandidate": None,
                    "ideaCandidates": candidate_dicts,
                    "ideaTrace": session.trace.model_dump() if session.trace else {},
                },
                artifacts=artifacts,
                events=[{
                    "level": "error",
                    "message": (
                        f"Idea refinement stopped with status '{session_status}': "
                        "no evidence-qualified candidate was selected"
                    ),
                }],
            )

        seed_query = config.seedQuery
        dossier = build_research_dossier(
            session,
            candidates,
            service.get_literature(session.id),
            question=ScientificQuestion(
                id=f"{context.run_id}:question",
                text=seed_query,
                domainHint=config.domain,
                constraints=config.constraints or [],
            ),
            run_id=context.run_id,
            mode=RunMode.COVERAGE,
            provider_name=provider_name,
            model=model,
        )
        run_dir = get_data_dir() / "faros" / "runs" / context.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        dossier_path = run_dir / "research_dossier.json"
        dossier_path.write_text(
            json.dumps(dossier.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        artifacts.append(
            ArtifactRecord(
                id=f"{context.run_id}:{self.capability_id}:dossier",
                type="research_dossier",
                uri=f"file://{dossier_path}",
                producer=self.capability_id,
                summary="Evidence-grounded research dossier for downstream execution and ReviewX",
                metadata={
                    "runId": context.run_id,
                    "questionId": dossier.questionId,
                    "path": str(dossier_path),
                },
            )
        )
        return CapabilityResult(
            status="completed",
            outputs={
                "ideaSessionId": session.id,
                "candidateCount": len(candidate_dicts),
                "selectedCandidateId": selected["id"] if selected else None,
                "selectedCandidate": selected,
                "ideaCandidates": candidate_dicts,
                "ideaTrace": session.trace.model_dump() if session.trace else {},
                "researchDossier": dossier.model_dump(mode="json"),
                "researchDossierPath": str(dossier_path),
            },
            artifacts=artifacts,
            events=[
                {
                    "level": "info",
                    "message": f"Idea refinement completed with {len(candidate_dicts)} candidates",
                }
            ],
        )
