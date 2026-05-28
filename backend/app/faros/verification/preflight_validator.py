from typing import Any, List

from app.faros.models.blueprint import Blueprint, WorkflowNode
from app.faros.models.profile import Profile
from app.faros.models.verification import PreflightNodeResult, RunPreflightResult


class RunPreflightValidator:
    """Validate runtime bindings before a FAROS run is created."""

    def __init__(self, capabilities, providers):
        self.capabilities = capabilities
        self.providers = providers

    def validate(self, blueprint: Blueprint, profile: Profile, node_resolver) -> RunPreflightResult:
        nodes: List[PreflightNodeResult] = []
        errors: List[str] = []
        warnings: List[str] = []

        for node in blueprint.workflow:
            node_result = self._validate_node(node, blueprint, profile, node_resolver)
            nodes.append(node_result)
            errors.extend([f"{node.id}: {item}" for item in node_result.errors])
            warnings.extend([f"{node.id}: {item}" for item in node_result.warnings])

        status = 'passed' if not errors else 'failed'
        message = (
            f"Preflight passed for blueprint '{blueprint.id}' with profile '{profile.id}'"
            if status == 'passed'
            else f"Preflight failed for blueprint '{blueprint.id}' with profile '{profile.id}'"
        )
        return RunPreflightResult(
            blueprint_id=blueprint.id,
            profile_id=profile.id,
            status=status,
            message=message,
            nodes=nodes,
            errors=errors,
            warnings=warnings,
        )

    def _validate_node(self, node: WorkflowNode, blueprint: Blueprint, profile: Profile, node_resolver) -> PreflightNodeResult:
        errors: List[str] = []
        warnings: List[str] = []
        provider_types: List[str] = []
        provider_ids: List[str] = []
        agent_id: str | None = None
        skill_ids: List[str] = []

        try:
            capability = self.capabilities.get(node.capability)
        except Exception as exc:
            return PreflightNodeResult(
                node_id=node.id,
                capability=node.capability,
                status='failed',
                errors=[str(exc)],
            )

        try:
            runtime = node_resolver(node, profile, capability)
            agent_id = runtime['agentId']
            skill_ids = list(runtime['skillIds'])
        except Exception as exc:
            return PreflightNodeResult(
                node_id=node.id,
                capability=node.capability,
                status='failed',
                errors=[str(exc)],
            )

        if not skill_ids:
            errors.append('node resolved with no skills')

        required_outputs = self._required_outputs_for_node(blueprint, node.capability)
        declared_outputs = set(node.outputs)
        missing_declared = [item for item in required_outputs if item not in declared_outputs]
        if missing_declared:
            errors.append(f"node outputs do not declare verification requirements: {missing_declared}")

        capability_binding = profile.capability_bindings.get(node.capability)
        if capability_binding:
            provider_types.append(capability_binding.provider_type)
            provider_ids.append(capability_binding.provider)
            try:
                provider = self.providers.get(capability_binding.provider_type)
                if not provider.supports_provider(capability_binding.provider):
                    errors.append(
                        f"provider id '{capability_binding.provider}' is not supported by provider type '{capability_binding.provider_type}'"
                    )
            except Exception as exc:
                errors.append(str(exc))
            if not capability_binding.provider:
                errors.append('capability binding is missing provider id')
        else:
            warnings.append('no capability-level provider binding declared')

        agent_binding = profile.agent_bindings.get(agent_id or '')
        if agent_binding:
            provider_types.append(agent_binding.provider_type)
            if agent_binding.preferred_provider:
                provider_ids.append(agent_binding.preferred_provider)
            try:
                provider = self.providers.get(agent_binding.provider_type)
                if agent_binding.preferred_provider and not provider.supports_provider(agent_binding.preferred_provider):
                    errors.append(
                        f"preferred provider '{agent_binding.preferred_provider}' is not supported by provider type '{agent_binding.provider_type}'"
                    )
            except Exception as exc:
                errors.append(str(exc))
        else:
            warnings.append('no agent-level provider binding declared')

        skill_artifacts = self._collect_skill_artifacts(runtime.get('skills', []))
        expected_artifacts = set(blueprint.artifact_schema.get(node.capability, []))
        if expected_artifacts and not expected_artifacts.intersection(skill_artifacts):
            warnings.append(
                'skill manifests do not advertise any of the blueprint artifact types '
                f"{sorted(expected_artifacts)}"
            )

        requirement_warnings = self._collect_provider_capability_warnings(runtime.get('skills', []), provider_types)
        warnings.extend(requirement_warnings)

        return PreflightNodeResult(
            node_id=node.id,
            capability=node.capability,
            status='passed' if not errors else 'failed',
            agent_id=agent_id,
            skill_ids=skill_ids,
            provider_types=sorted(set(provider_types + provider_ids)),
            errors=errors,
            warnings=warnings,
        )

    def _required_outputs_for_node(self, blueprint: Blueprint, capability_id: str) -> List[str]:
        for rule in blueprint.verification_rules:
            if rule.get('capability') == capability_id:
                return list(rule.get('requires', []))
        return []

    def _collect_skill_artifacts(self, skills: List[Any]) -> set[str]:
        artifact_types: set[str] = set()
        for skill in skills:
            artifact_types.update(skill.artifact_types)
        return artifact_types

    def _collect_provider_capability_warnings(self, skills: List[Any], provider_types: List[str]) -> List[str]:
        warnings: List[str] = []
        resolved_providers = []
        for provider_type in provider_types:
            if provider_type in resolved_providers:
                continue
            if provider_type not in {item.type for item in [self.providers.describe(provider_type)]}:
                continue
            resolved_providers.append(provider_type)
        available = {}
        for provider_type in set(provider_types):
            try:
                provider = self.providers.get(provider_type)
            except Exception:
                continue
            available[provider_type] = provider
        for skill in skills:
            for requirement, target in skill.provider_requirements.items():
                if available and not any(provider.supports_capability(requirement) for provider in available.values()):
                    warnings.append(
                        f"skill '{skill.id}' requires provider capability '{requirement}' but resolved providers do not advertise it"
                    )
        return warnings
