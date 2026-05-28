from app.faros.models.provider import ProviderDescriptor, ProviderHealth, ProviderResult, ProviderTask
from app.faros.providers.base import BaseProvider
from app.faros.providers.external_backend import maybe_invoke_external_backend


class ToolProvider(BaseProvider):
    """Baseline tool provider for non-LLM runtime integration."""

    provider_type = 'tool'
    provider_ids = ['local-toolbox']
    provider_capabilities = ['retrieval', 'execution', 'compilation', 'writing']

    def invoke(self, task: ProviderTask) -> ProviderResult:
        external = maybe_invoke_external_backend(task)
        if external is not None:
            return external
        if task.capability_id == 'paper_drafting':
            title = task.options.get('title', 'FAROS Tool Draft')
            venue = task.options.get('targetVenue', 'generic')
            latex = task.options.get('latex') or (
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                f"\\section*{{{title}}}\n"
                "Tool-backed draft prepared by FAROS.\n"
                "\\end{document}\n"
            )
            return ProviderResult(
                ok=True,
                provider=task.provider,
                model=task.model or 'tool-paper-assembler',
                text=f'Tool provider assembled paper scaffold for {title}',
                payload={
                    'paperStatus': 'prepared',
                    'title': title,
                    'targetVenue': venue,
                    'latexFiles': {
                        'main.tex': latex,
                        'notes/tool_summary.txt': f'Tool-generated scaffold for {title} ({venue}).\n',
                    },
                    'pdfPlaceholder': b'%PDF-1.4\n% FAROS tool placeholder\n'.decode('latin1'),
                },
            )
        return ProviderResult(
            ok=False,
            provider=task.provider,
            model=task.model or 'tool-paper-assembler',
            text='',
            error=f"tool provider does not implement capability '{task.capability_id}'",
        )

    def describe(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            type=self.provider_type,
            name='Tool Provider',
            description='Baseline provider for tool-backed or API-backed runtime integrations.',
            supported_capabilities=list(self.provider_capabilities),
            supported_provider_ids=list(self.provider_ids),
            default_provider_id=self.provider_ids[0],
            defaults={'mode': 'baseline', 'supportsExternal': ['file', 'workspace_file', 'queue_file', 'approval_file', 'approval_queue', 'command']},
        )

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            type=self.provider_type,
            status='healthy',
            message='Tool provider baseline registered',
            provider_ids=list(self.provider_ids),
            details={'capabilities': list(self.provider_capabilities), 'mode': 'baseline', 'externalModes': ['file', 'workspace_file', 'queue_file', 'approval_file', 'approval_queue', 'command']},
        )

    def supports_provider(self, provider_id: str) -> bool:
        return provider_id in self.provider_ids

    def supports_capability(self, capability_name: str) -> bool:
        return capability_name in self.provider_capabilities
