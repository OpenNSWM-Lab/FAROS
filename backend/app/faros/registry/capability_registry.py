from typing import Dict, List

from app.faros.capabilities.adapters.experiment import ExperimentCapability
from app.faros.capabilities.adapters.idea_refinement import IdeaRefinementCapability
from app.faros.capabilities.adapters.paper_drafting import PaperDraftingCapability
from app.faros.capabilities.adapters.reviewer_simulation import ReviewerSimulationCapability
from app.faros.capabilities.base import BaseCapability


class CapabilityRegistry:
    """Registry for executable FAROS capabilities."""

    def __init__(self):
        self._capabilities: Dict[str, BaseCapability] = {}

    def register(self, capability: BaseCapability) -> None:
        self._capabilities[capability.capability_id] = capability

    def get(self, capability_id: str) -> BaseCapability:
        if capability_id not in self._capabilities:
            raise KeyError(f"Capability '{capability_id}' is not registered")
        return self._capabilities[capability_id]

    def list(self) -> List[dict]:
        return [
            {
                'id': capability.capability_id,
                'description': capability.description,
                'defaultAgentId': capability.default_agent_id or None,
                'defaultSkillIds': list(capability.default_skill_ids),
                'artifactTypes': list(capability.artifact_types),
            }
            for capability in self._capabilities.values()
        ]


_registry: CapabilityRegistry | None = None


def get_capability_registry() -> CapabilityRegistry:
    global _registry
    if _registry is None:
        _registry = CapabilityRegistry()
        _registry.register(IdeaRefinementCapability())
        _registry.register(ExperimentCapability())
        _registry.register(PaperDraftingCapability())
        _registry.register(ReviewerSimulationCapability())
    return _registry
