from app.faros.models.provider import ProviderDescriptor, ProviderHealth, ProviderResult, ProviderTask
from app.faros.providers.base import BaseProvider
from app.faros.providers.external_backend import maybe_invoke_external_backend


class HumanProvider(BaseProvider):
    """Baseline human provider for approval and review checkpoints."""

    provider_type = 'human'
    provider_ids = ['manual-review']
    provider_capabilities = ['human', 'review', 'approval']

    def invoke(self, task: ProviderTask) -> ProviderResult:
        external = maybe_invoke_external_backend(task)
        if external is not None:
            return external
        if task.capability_id == 'reviewer_simulation':
            paper_id = task.options.get('paperId', 'unknown-paper')
            return ProviderResult(
                ok=True,
                provider=task.provider,
                model=task.model or 'human-review-baseline',
                text=f'Human review checkpoint completed for {paper_id}',
                payload={
                    'reviewStatus': 'completed',
                    'scoreSuggestion': 6,
                    'markdownReport': f'# Review for {paper_id}\n\nHuman review checkpoint completed by FAROS baseline provider.\n',
                    'jsonReport': {
                        'summary': 'Human review checkpoint completed',
                        'paperId': paper_id,
                    },
                    'actionItems': [
                        {'title': 'Validate claims', 'severity': 'major'},
                        {'title': 'Confirm experiment scope', 'severity': 'minor'},
                    ],
                },
            )
        return ProviderResult(
            ok=False,
            provider=task.provider,
            model=task.model or 'human-review-baseline',
            text='',
            error=f"human provider does not implement capability '{task.capability_id}'",
        )

    def describe(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            type=self.provider_type,
            name='Human Provider',
            description='Baseline provider for human review, approval, and intervention workflows.',
            supported_capabilities=list(self.provider_capabilities),
            supported_provider_ids=list(self.provider_ids),
            default_provider_id=self.provider_ids[0],
            defaults={'mode': 'baseline', 'supportsExternal': ['file', 'workspace_file', 'queue_file', 'approval_file', 'approval_queue', 'command']},
        )

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            type=self.provider_type,
            status='healthy',
            message='Human provider baseline registered',
            provider_ids=list(self.provider_ids),
            details={'capabilities': list(self.provider_capabilities), 'mode': 'baseline', 'externalModes': ['file', 'workspace_file', 'queue_file', 'approval_file', 'approval_queue', 'command']},
        )

    def supports_provider(self, provider_id: str) -> bool:
        return provider_id in self.provider_ids

    def supports_capability(self, capability_name: str) -> bool:
        return capability_name in self.provider_capabilities
