from abc import ABC, abstractmethod

from app.faros.models.provider import ProviderDescriptor, ProviderHealth, ProviderResult, ProviderTask


class BaseProvider(ABC):
    """Base provider contract for FAROS execution."""

    provider_type: str

    @abstractmethod
    def invoke(self, task: ProviderTask) -> ProviderResult:
        raise NotImplementedError

    @abstractmethod
    def describe(self) -> ProviderDescriptor:
        raise NotImplementedError

    @abstractmethod
    def health(self) -> ProviderHealth:
        raise NotImplementedError

    @abstractmethod
    def supports_provider(self, provider_id: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def supports_capability(self, capability_name: str) -> bool:
        raise NotImplementedError
