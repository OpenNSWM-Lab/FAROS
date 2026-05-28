from app.core.settings import get_settings
from app.faros.models.provider import ProviderDescriptor, ProviderHealth, ProviderResult, ProviderTask
from app.faros.providers.base import BaseProvider
from app.llm.provider_client import ChatMessage, ProviderError, get_provider_client


class LLMProvider(BaseProvider):
    """LLM-backed provider adapter over the existing provider client."""

    provider_type = "llm"
    provider_capabilities = ['reasoning', 'planning', 'writing', 'review', 'retrieval']

    def invoke(self, task: ProviderTask) -> ProviderResult:
        client = get_provider_client(task.provider)
        response = client.chat(
            messages=[
                ChatMessage(role=message["role"], content=message["content"])
                for message in task.messages
            ],
            model=task.model,
            **task.options,
        )
        return ProviderResult(
            ok=True,
            provider=response.raw_provider,
            model=response.model,
            text=response.text,
            usage=response.usage,
            latency_ms=response.latency_ms,
        )

    def describe(self) -> ProviderDescriptor:
        settings = get_settings()
        provider_ids = sorted(settings.PROVIDERS.keys())
        return ProviderDescriptor(
            type=self.provider_type,
            name='LLM Provider',
            description='Runtime adapter for chat/reasoning-capable language model providers.',
            supported_capabilities=list(self.provider_capabilities),
            supported_provider_ids=provider_ids,
            default_provider_id=settings.get_active_provider(),
            defaults={
                'activeProvider': settings.get_active_provider(),
                'activeModel': settings.get_active_model(),
            },
        )

    def health(self) -> ProviderHealth:
        settings = get_settings()
        provider_ids = sorted(settings.PROVIDERS.keys())
        active = settings.get_active_provider()
        configured = []
        for provider_id in provider_ids:
            cfg = settings.get_provider_config(provider_id)
            if settings.get_runtime_api_key(provider_id) or cfg.get_api_key():
                configured.append(provider_id)
        status = 'healthy' if active in provider_ids else 'degraded'
        return ProviderHealth(
            type=self.provider_type,
            status=status,
            message='LLM provider registry loaded',
            provider_ids=provider_ids,
            details={
                'activeProvider': active,
                'configuredProviders': configured,
                'capabilities': list(self.provider_capabilities),
            },
        )

    def supports_provider(self, provider_id: str) -> bool:
        settings = get_settings()
        return provider_id in settings.PROVIDERS

    def supports_capability(self, capability_name: str) -> bool:
        return capability_name in self.provider_capabilities
