from app.faros.models.provider import ProviderDescriptor, ProviderHealth, ProviderResult, ProviderTask
from app.faros.providers.base import BaseProvider
from app.faros.providers.external_backend import maybe_invoke_external_backend


class ExecutionProvider(BaseProvider):
    """Baseline execution provider for experiment scaffolding and runtime job specs."""

    provider_type = 'execution'
    provider_ids = ['local-executor']
    provider_capabilities = ['execution', 'storage', 'compilation']

    def invoke(self, task: ProviderTask) -> ProviderResult:
        external = maybe_invoke_external_backend(task)
        if external is not None:
            return external
        if task.capability_id == 'experiment':
            title = task.options.get('title', 'FAROS Experiment')
            framework = task.options.get('framework', 'FastAPI')
            language = task.options.get('language', 'python')
            files = task.options.get('files') or []
            return ProviderResult(
                ok=True,
                provider=task.provider,
                model=task.model or 'execution-scaffolder',
                text=f'Execution provider scaffolded experiment workspace for {title}',
                payload={
                    'projectTitle': f'{title} [{language}]',
                    'files': files,
                    'experimentStatus': 'designed',
                    'executionSpec': {
                        'entrypoint': 'scripts/run.sh',
                        'framework': framework,
                        'language': language,
                    },
                },
            )
        return ProviderResult(
            ok=False,
            provider=task.provider,
            model=task.model or 'execution-scaffolder',
            text='',
            error=f"execution provider does not implement capability '{task.capability_id}'",
        )

    def describe(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            type=self.provider_type,
            name='Execution Provider',
            description='Baseline provider for runtime execution, sandboxing, and job orchestration backends.',
            supported_capabilities=list(self.provider_capabilities),
            supported_provider_ids=list(self.provider_ids),
            default_provider_id=self.provider_ids[0],
            defaults={'mode': 'baseline', 'supportsExternal': ['file', 'workspace_file', 'queue_file', 'approval_file', 'approval_queue', 'command']},
        )

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            type=self.provider_type,
            status='healthy',
            message='Execution provider baseline registered',
            provider_ids=list(self.provider_ids),
            details={'capabilities': list(self.provider_capabilities), 'mode': 'baseline', 'externalModes': ['file', 'workspace_file', 'queue_file', 'approval_file', 'approval_queue', 'command']},
        )

    def supports_provider(self, provider_id: str) -> bool:
        return provider_id in self.provider_ids

    def supports_capability(self, capability_name: str) -> bool:
        return capability_name in self.provider_capabilities
