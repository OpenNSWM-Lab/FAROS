from app.faros.models.profile import Profile
from app.faros.models.verification import ProfileValidationResult


class ProfileValidator:
    """Validate FAROS execution profiles against registered runtime assets."""

    def __init__(self, capabilities, providers, agents, skills):
        self.capabilities = capabilities
        self.providers = providers
        self.agents = agents
        self.skills = skills

    def validate(self, profile: Profile) -> ProfileValidationResult:
        errors: list[str] = []
        warnings: list[str] = []

        for capability_id, binding in profile.capability_bindings.items():
            try:
                self.capabilities.get(capability_id)
            except Exception as exc:
                errors.append(str(exc))
            try:
                provider = self.providers.get(binding.provider_type)
            except Exception as exc:
                errors.append(str(exc))
                provider = None
            if not binding.provider:
                errors.append(f"Capability binding '{capability_id}' must define a provider id")
            elif provider and not provider.supports_provider(binding.provider):
                errors.append(
                    f"Capability binding '{capability_id}' references unsupported provider id '{binding.provider}' for type '{binding.provider_type}'"
                )

        for binding_key, binding in profile.agent_bindings.items():
            if binding_key != binding.agent_id:
                errors.append(
                    f"Agent binding key '{binding_key}' does not match embedded agent_id '{binding.agent_id}'"
                )
            try:
                agent = self.agents.get(binding.agent_id)
            except Exception as exc:
                errors.append(str(exc))
                continue
            try:
                provider = self.providers.get(binding.provider_type)
            except Exception as exc:
                errors.append(str(exc))
                provider = None
            if binding.preferred_provider and provider and not provider.supports_provider(binding.preferred_provider):
                errors.append(
                    f"Agent '{binding.agent_id}' references unsupported preferred provider '{binding.preferred_provider}' for type '{binding.provider_type}'"
                )
            for skill_id in binding.skill_overrides:
                self._validate_skill_for_agent(skill_id, agent.role, errors)

        for agent_id, skill_ids in profile.skill_defaults.items():
            try:
                agent = self.agents.get(agent_id)
            except Exception as exc:
                errors.append(str(exc))
                continue
            if not skill_ids:
                warnings.append(f"Skill defaults for agent '{agent_id}' are empty")
            for skill_id in skill_ids:
                self._validate_skill_for_agent(skill_id, agent.role, errors)

        status = 'passed' if not errors else 'failed'
        message = (
            f"Profile '{profile.id}' passed validation"
            if status == 'passed'
            else f"Profile '{profile.id}' failed validation"
        )
        return ProfileValidationResult(
            profile_id=profile.id,
            status=status,
            message=message,
            errors=errors,
            warnings=warnings,
        )

    def _validate_skill_for_agent(self, skill_id: str, agent_role: str, errors: list[str]) -> None:
        try:
            skill = self.skills.get(skill_id)
        except Exception as exc:
            errors.append(str(exc))
            return
        if agent_role not in skill.agent_roles:
            errors.append(
                f"Skill '{skill_id}' is not compatible with agent role '{agent_role}'"
            )
