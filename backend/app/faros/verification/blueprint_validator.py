from app.faros.models.blueprint import Blueprint
from app.faros.models.verification import BlueprintValidationResult
from app.faros.registry.agent_registry import get_agent_registry
from app.faros.registry.capability_registry import get_capability_registry
from app.faros.registry.skill_registry import get_skill_registry


class BlueprintValidator:
    """Validate blueprint structure and runtime references before execution."""

    def __init__(self):
        self.capabilities = get_capability_registry()
        self.agents = get_agent_registry()
        self.skills = get_skill_registry()

    def validate(self, blueprint: Blueprint) -> BlueprintValidationResult:
        errors: list[str] = []
        warnings: list[str] = []

        if not blueprint.workflow:
            errors.append('Blueprint workflow cannot be empty')

        node_ids = [node.id for node in blueprint.workflow]
        if len(node_ids) != len(set(node_ids)):
            errors.append('Blueprint contains duplicate workflow node ids')

        for node in blueprint.workflow:
            try:
                capability = self.capabilities.get(node.capability)
            except KeyError:
                errors.append(f"Node '{node.id}' references unknown capability '{node.capability}'")
                capability = None

            agent_id = node.agent or getattr(capability, 'default_agent_id', '') or None
            if not agent_id:
                errors.append(f"Node '{node.id}' has no agent binding")
            else:
                try:
                    agent = self.agents.get(agent_id)
                except KeyError:
                    errors.append(f"Node '{node.id}' references unknown agent '{agent_id}'")
                    agent = None

                if node.skills:
                    for skill_id in node.skills:
                        try:
                            skill = self.skills.get(skill_id)
                        except KeyError:
                            errors.append(f"Node '{node.id}' references unknown skill '{skill_id}'")
                            continue
                        if agent and agent.role not in skill.agent_roles:
                            errors.append(
                                f"Skill '{skill_id}' is not declared for agent role '{agent.role}' on node '{node.id}'"
                            )
                elif capability is not None and not getattr(capability, 'default_skill_ids', None) and agent is not None and not agent.default_skills:
                    warnings.append(f"Node '{node.id}' resolves to an agent without explicit skills")

        known_nodes = set(node_ids)
        for edge in blueprint.edges:
            if edge.source not in known_nodes:
                errors.append(f"Edge source '{edge.source}' is not defined in workflow")
            if edge.target not in known_nodes:
                errors.append(f"Edge target '{edge.target}' is not defined in workflow")

        if errors:
            return BlueprintValidationResult(
                blueprint_id=blueprint.id,
                status='failed',
                message='Blueprint validation failed',
                errors=errors,
                warnings=warnings,
            )

        return BlueprintValidationResult(
            blueprint_id=blueprint.id,
            status='passed',
            message='Blueprint validation passed',
            errors=[],
            warnings=warnings,
        )
