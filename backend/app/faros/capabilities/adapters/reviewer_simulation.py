from typing import Any, Dict

from app.faros.capabilities.base import BaseCapability
from app.faros.models.artifact import ArtifactRecord
from app.faros.models.capability import CapabilityResult
from app.faros.models.execution import ExecutionContext
from app.faros.models.profile import CapabilityBinding
from app.faros.models.provider import ProviderResult, ProviderTask
from app.modules.review.service import generate_review
from app.modules.review.storage import create_review, update_review


class ReviewerSimulationCapability(BaseCapability):
    capability_id = "reviewer_simulation"
    description = "Generate a structured paper review with actionable follow-up items."
    default_agent_id = "reviewer"
    default_skill_ids = ["review-critique", "consistency-audit"]
    artifact_types = ["review_report"]

    def build_provider_task(self, context: ExecutionContext, inputs: Dict[str, Any], binding: CapabilityBinding) -> ProviderTask | None:
        if binding.provider_type != 'human':
            return None
        paper_id = inputs.get("paperId")
        if not paper_id:
            raise ValueError("reviewer_simulation requires paperId from a previous capability")
        return ProviderTask(
            capability_id=self.capability_id,
            provider=binding.provider,
            model=binding.model,
            options={
                'paperId': paper_id,
                'reviewerProfile': inputs.get('reviewerProfile', 'senior_reviewer'),
            },
        )

    def execute(self, context: ExecutionContext, inputs: Dict[str, Any]) -> CapabilityResult:
        paper_id = inputs.get("paperId")
        if not paper_id:
            raise ValueError("reviewer_simulation requires paperId from a previous capability")

        binding = context.get_binding() or context.get_binding(self.capability_id)
        provider_name = binding.provider if binding else inputs.get("providerName", "moonshot")
        model = binding.model if binding and binding.model else inputs.get("model", "moonshot-v1-8k")

        record = create_review(
            {
                "paperId": paper_id,
                "reviewerProfile": inputs.get("reviewerProfile", "senior_reviewer"),
                "providerName": provider_name,
                "model": model,
            }
        )
        review = generate_review(record["id"])
        return self._build_result(context, review, paper_id, f"Reviewer simulation completed for paper {paper_id}")

    def consume_provider_result(self, context: ExecutionContext, inputs: Dict[str, Any], provider_result: ProviderResult) -> CapabilityResult:
        paper_id = inputs.get("paperId")
        if not paper_id:
            raise ValueError("reviewer_simulation requires paperId from a previous capability")
        record = create_review(
            {
                "paperId": paper_id,
                "reviewerProfile": inputs.get("reviewerProfile", "senior_reviewer"),
                "providerName": provider_result.provider,
                "model": provider_result.model,
            }
        )
        review = update_review(record['id'], {
            'status': provider_result.payload.get('reviewStatus', 'completed'),
            'scoreSuggestion': provider_result.payload.get('scoreSuggestion'),
            'jsonReport': provider_result.payload.get('jsonReport'),
            'markdownReport': provider_result.payload.get('markdownReport'),
            'actionItems': provider_result.payload.get('actionItems', []),
        }) or record
        return self._build_result(context, review, paper_id, provider_result.text or f"Human review completed for paper {paper_id}")

    def _build_result(self, context: ExecutionContext, review: Dict[str, Any], paper_id: str, event_message: str) -> CapabilityResult:
        action_items = review.get("actionItems", [])
        return CapabilityResult(
            status="completed" if review.get("status") == "completed" else review.get("status", "failed"),
            outputs={
                "reviewId": review["id"],
                "reviewStatus": review.get("status"),
                "scoreSuggestion": review.get("scoreSuggestion"),
                "actionItemCount": len(action_items),
                "actionItems": action_items,
            },
            artifacts=[
                ArtifactRecord(
                    id=f"{context.run_id}:{self.capability_id}:review",
                    type="review_report",
                    uri=f"review://{review['id']}",
                    producer=self.capability_id,
                    summary=f"Review {review['id']} with {len(action_items)} action items",
                    metadata={"reviewId": review["id"], "paperId": paper_id},
                )
            ],
            events=[{'level': 'info', 'message': event_message}],
        )
