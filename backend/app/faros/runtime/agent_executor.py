from dataclasses import dataclass
from typing import Any, Dict, List

from app.faros.capabilities.base import BaseCapability
from app.faros.errors import FarosProviderError
from app.faros.models.agent import AgentSpec
from app.faros.models.capability import CapabilityResult
from app.faros.models.execution import ExecutionContext
from app.faros.models.skill import SkillSpec
from app.faros.registry.provider_registry import get_provider_registry


@dataclass
class AgentExecutionPlan:
    """Resolved execution bundle for one workflow node."""

    agent: AgentSpec
    skills: List[SkillSpec]
    capability: BaseCapability
    inputs: Dict[str, Any]
    context: ExecutionContext


class AgentExecutor:
    """Executes a resolved runtime node through an agent boundary."""

    def __init__(self, provider_registry=None):
        self.providers = provider_registry or get_provider_registry()

    def execute(self, plan: AgentExecutionPlan) -> CapabilityResult:
        binding = plan.context.get_binding(plan.capability.capability_id)
        result: CapabilityResult
        if binding and binding.provider_type != 'llm':
            task = plan.capability.build_provider_task(plan.context, plan.inputs, binding)
            if task is not None:
                merged_options = dict(binding.options or {})
                merged_options.update(task.options)
                merged_options.setdefault('_faros', {
                    'runId': plan.context.run_id,
                    'nodeId': plan.context.node_id,
                    'capabilityId': plan.capability.capability_id,
                    'agentId': plan.agent.id,
                    'skillIds': [skill.id for skill in plan.skills],
                    'providerType': binding.provider_type,
                    'providerId': binding.provider,
                })
                task.options = merged_options
                provider = self.providers.get(binding.provider_type)
                provider_result = provider.invoke(task)
                if not provider_result.ok:
                    raise FarosProviderError(provider_result.error or f"provider {binding.provider_type} failed", error_code='provider_execution_failed')
                result = plan.capability.consume_provider_result(plan.context, plan.inputs, provider_result)
            else:
                result = plan.capability.execute(plan.context, plan.inputs)
        else:
            result = plan.capability.execute(plan.context, plan.inputs)

        existing = dict(result.verification)
        existing.setdefault('agentId', plan.agent.id)
        existing.setdefault('agentRole', plan.agent.role)
        existing.setdefault('skillIds', [skill.id for skill in plan.skills])
        if binding:
            existing.setdefault('providerType', binding.provider_type)
            existing.setdefault('providerId', binding.provider)
        result.verification = existing
        if not result.events:
            result.events = [
                {
                    'level': 'info',
                    'message': f"Agent {plan.agent.id} executed {plan.capability.capability_id}",
                }
            ]
        return result
