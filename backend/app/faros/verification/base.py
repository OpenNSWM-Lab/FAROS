from abc import ABC, abstractmethod
from typing import Any

from app.faros.models.capability import CapabilityResult
from app.faros.models.verification import VerificationResult, VerifierDescriptor


class BaseVerifier(ABC):
    """Base verifier contract."""

    verifier_id: str
    name: str = ''
    description: str = ''
    default_enabled: bool = False
    tags: list[str] = []

    def describe(self) -> VerifierDescriptor:
        return VerifierDescriptor(
            id=self.verifier_id,
            name=self.name or self.verifier_id,
            description=self.description,
            default_enabled=self.default_enabled,
            tags=list(self.tags),
        )

    @abstractmethod
    def verify(self, capability_id: str, result: CapabilityResult, **context: Any) -> VerificationResult:
        raise NotImplementedError
