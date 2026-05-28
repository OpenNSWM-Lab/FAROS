from typing import Any, Dict

from app.faros.capabilities.base import BaseCapability
from app.faros.models.artifact import ArtifactRecord
from app.faros.models.capability import CapabilityResult
from app.faros.models.execution import ExecutionContext
from app.modules.idea.contracts import IdeaSessionConfig
from app.modules.idea.service import get_idea_service


class IdeaRefinementCapability(BaseCapability):
    capability_id = "idea_refinement"
    description = "Run the existing LLM Scientist idea pipeline and normalize outputs for FAROS."
    default_agent_id = "researcher"
    default_skill_ids = ["literature-grounding", "idea-analysis"]
    artifact_types = ["idea_session"]

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
            domain=inputs.get("domain"),
            constraints=inputs.get("constraints"),
            mustCiteList=inputs.get("mustCiteList"),
        )

        service = get_idea_service()
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
        return CapabilityResult(
            status="completed" if session_status == "completed" else "failed",
            outputs={
                "ideaSessionId": session.id,
                "candidateCount": len(candidate_dicts),
                "selectedCandidateId": selected["id"] if selected else None,
                "selectedCandidate": selected,
                "ideaCandidates": candidate_dicts,
                "ideaTrace": session.trace.model_dump() if session.trace else {},
            },
            artifacts=[
                ArtifactRecord(
                    id=f"{context.run_id}:{self.capability_id}:session",
                    type="idea_session",
                    uri=f"idea://{session.id}",
                    producer=self.capability_id,
                    summary=f"Idea session {session.id} with {len(candidate_dicts)} candidates",
                    metadata={"sessionId": session.id, "selectedCandidateId": selected["id"] if selected else None},
                )
            ],
            events=[
                {
                    "level": "info",
                    "message": f"Idea refinement completed with {len(candidate_dicts)} candidates",
                }
            ],
        )
