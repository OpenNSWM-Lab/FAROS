from typing import Dict, List

from app.faros.models.provider import ProviderDescriptor, ProviderHealth
from app.faros.providers.base import BaseProvider
from app.faros.providers.execution_provider import ExecutionProvider
from app.faros.providers.human_provider import HumanProvider
from app.faros.providers.llm_provider import LLMProvider
from app.faros.providers.tool_provider import ToolProvider


class ProviderRegistry:
    """Registry of provider implementations available to FAROS."""

    def __init__(self):
        self._providers: Dict[str, BaseProvider] = {}

    def register(self, provider_type: str, provider: BaseProvider) -> None:
        self._providers[provider_type] = provider

    def get(self, provider_type: str) -> BaseProvider:
        if provider_type not in self._providers:
            raise KeyError(f"Provider type '{provider_type}' is not registered")
        return self._providers[provider_type]

    def describe(self, provider_type: str) -> ProviderDescriptor:
        return self.get(provider_type).describe()

    def health(self, provider_type: str) -> ProviderHealth:
        return self.get(provider_type).health()

    def list(self) -> List[dict]:
        return [provider.describe().model_dump() for _, provider in sorted(self._providers.items())]

    def health_summary(self) -> List[dict]:
        return [provider.health().model_dump() for _, provider in sorted(self._providers.items())]


_registry: ProviderRegistry | None = None


def get_provider_registry() -> ProviderRegistry:
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
        _registry.register('execution', ExecutionProvider())
        _registry.register('human', HumanProvider())
        _registry.register('llm', LLMProvider())
        _registry.register('tool', ToolProvider())
    return _registry
