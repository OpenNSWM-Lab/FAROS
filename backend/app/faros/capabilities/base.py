from abc import ABC, abstractmethod
from typing import Any, Dict, List

from app.faros.models.capability import CapabilityResult
from app.faros.models.execution import ExecutionContext
from app.faros.models.profile import CapabilityBinding
from app.faros.models.provider import ProviderResult, ProviderTask


class BaseCapability(ABC):
    """Base contract for all FAROS capabilities."""

    capability_id: str
    description: str = ''
    default_agent_id: str = ''
    default_skill_ids: List[str] = []
    artifact_types: List[str] = []

    @abstractmethod
    def execute(self, context: ExecutionContext, inputs: Dict[str, object]) -> CapabilityResult:
        raise NotImplementedError

    def build_provider_task(
        self,
        context: ExecutionContext,
        inputs: Dict[str, Any],
        binding: CapabilityBinding,
    ) -> ProviderTask | None:
        return None

    def consume_provider_result(
        self,
        context: ExecutionContext,
        inputs: Dict[str, Any],
        provider_result: ProviderResult,
    ) -> CapabilityResult:
        raise NotImplementedError(f"{self.capability_id} does not support provider-owned execution")
