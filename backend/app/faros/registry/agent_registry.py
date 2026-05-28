import shutil
from pathlib import Path
from typing import Dict, List

from app.faros.loaders.agent_loader import AgentLoader
from app.faros.models.agent import AgentSpec
from app.faros.registry.package_lifecycle import compare_semver, package_dir_exists
from app.faros.registry.package_compatibility import enforce_compatibility, validate_dependency_graph
from app.faros.registry.package_trust import enforce_trust_policy, inspect_package_trust
from app.faros.runtime.package_audit import PackageAuditStore


class AgentRegistry:
    """Registry of runtime agent roles available to FAROS."""

    def __init__(self, root: Path, audit_store: PackageAuditStore | None = None):
        self.root = root
        self.loader = AgentLoader(root)
        self.audit = audit_store or PackageAuditStore()
        self._builtin_agents: Dict[str, AgentSpec] = {}
        self._package_agents: Dict[str, AgentSpec] = {}

    def register_builtin(self, agent: AgentSpec) -> None:
        self._builtin_agents[agent.id] = agent

    def refresh(self) -> List[AgentSpec]:
        self._package_agents = {agent.id: agent for agent in self.loader.list_agents()}
        return self.list()

    def register(self, agent: AgentSpec) -> None:
        self._package_agents[agent.id] = agent

    def validate_package(self, package_path: Path | str) -> AgentSpec:
        return self.loader.validate_package(package_path)

    def install_package(
        self,
        package_path: Path | str,
        overwrite: bool = False,
        allow_downgrade: bool = False,
        allow_untrusted: bool = True,
    ) -> AgentSpec:
        from app.faros.registry.profile_registry import get_profile_registry
        from app.faros.registry.skill_registry import get_skill_registry

        incoming = self.loader.validate_package(package_path)
        enforce_compatibility(
            f"Agent '{incoming.id}'",
            incoming.compatibility,
            {
                'profiles': {profile.id: profile.version for profile in get_profile_registry().list()},
                'skills': {skill.id: skill.version for skill in get_skill_registry().list()},
                'agents': {agent.id: getattr(agent, 'version', '0.0.0') for agent in self.list()},
            },
        )
        existing = self.loader.load(incoming.id) if package_dir_exists(self.root, incoming.id, 'agent.json') else None
        source_dir = Path(package_path).expanduser().resolve()
        if source_dir.is_file():
            source_dir = source_dir.parent
        trust_report = inspect_package_trust('agent', incoming.id, source_dir, version=getattr(incoming, 'version', None))
        enforce_trust_policy(trust_report, allow_untrusted=allow_untrusted)

        action = 'install'
        previous_version = None
        if existing is not None:
            previous_version = getattr(existing, 'version', '0.0.0')
            incoming_version = getattr(incoming, 'version', '0.0.0')
            if not overwrite:
                raise ValueError(
                    f"Agent '{incoming.id}' is already installed; pass overwrite=true to replace it"
                )
            if compare_semver(incoming_version, previous_version) < 0 and not allow_downgrade:
                raise ValueError(
                    f"Refusing to downgrade agent '{incoming.id}' from {previous_version} to {incoming_version}; pass allow_downgrade=true to override"
                )
            self.audit.backup_package('agent', incoming.id, self.root / incoming.id, version=previous_version)
            action = 'upgrade' if compare_semver(incoming_version, previous_version) != 0 else 'reinstall'
        agent = self.loader.install_package(package_path, overwrite=overwrite)
        self.refresh()
        try:
            from app.faros.registry.blueprint_registry import get_blueprint_registry
            validate_dependency_graph({
                'profiles': get_profile_registry().list(),
                'agents': self.list(),
                'skills': get_skill_registry().list(),
                'blueprints': get_blueprint_registry().list(),
            })
        except Exception:
            if existing is not None:
                self.audit.restore_backup('agent', incoming.id, self.root / incoming.id)
            else:
                shutil.rmtree(self.root / incoming.id, ignore_errors=True)
            self.refresh()
            raise
        self.audit.append_event({
            'packageType': 'agent',
            'packageId': agent.id,
            'action': action,
            'version': getattr(agent, 'version', '0.0.0'),
            'previousVersion': previous_version,
            'sourcePath': str(package_path),
            'trustStatus': trust_report.get('policyStatus'),
            'trustLevel': trust_report.get('trustLevel'),
            'trusted': trust_report.get('trusted'),
        })
        return self.get(agent.id)


    def rollback_package(self, agent_id: str) -> AgentSpec:
        restored = self.audit.restore_backup('agent', agent_id, self.root / agent_id)
        self.refresh()
        version = restored.get('version')
        if version and agent_id in self._package_agents and getattr(self._package_agents[agent_id], 'version', '0.0.0') != version:
            raise ValueError(f"Rollback restored unexpected version for agent '{agent_id}'")
        return self.get(agent_id)

    def uninstall_package(self, agent_id: str) -> Dict[str, str]:
        manifest_path = self.root / agent_id / 'agent.json'
        if not manifest_path.is_file():
            raise ValueError(f"Agent '{agent_id}' is not installed")
        previous = self.loader.load(agent_id)
        shutil.rmtree(manifest_path.parent)
        self.refresh()
        self.audit.append_event({
            'packageType': 'agent',
            'packageId': agent_id,
            'action': 'uninstall',
            'version': getattr(previous, 'version', '0.0.0'),
        })
        return {'id': agent_id, 'version': getattr(previous, 'version', '0.0.0'), 'status': 'uninstalled'}

    def audit_log(self, limit: int = 50) -> List[dict]:
        return self.audit.list_events(package_type='agent', limit=limit)

    def get(self, agent_id: str) -> AgentSpec:
        merged = {**self._builtin_agents, **self._package_agents}
        if agent_id not in merged:
            raise KeyError(f"Agent '{agent_id}' is not registered")
        return merged[agent_id]

    def list(self) -> List[AgentSpec]:
        merged = {**self._builtin_agents, **self._package_agents}
        return list(merged.values())


_registry: AgentRegistry | None = None


def get_agent_registry() -> AgentRegistry:
    global _registry
    if _registry is None:
        root = Path(__file__).resolve().parents[1] / 'agents'
        _registry = AgentRegistry(root)
        _registry.register_builtin(
            AgentSpec(
                id='researcher',
                name='Researcher Agent',
                role='researcher',
                description='Synthesizes literature context, ideas, and grounded research directions.',
                default_skills=['literature-grounding', 'idea-analysis'],
                provider_preferences={'reasoning': 'llm'},
            )
        )
        _registry.register_builtin(
            AgentSpec(
                id='experimenter',
                name='Experiment Agent',
                role='experimenter',
                description='Prepares executable experiment scaffolds and runtime specs.',
                default_skills=['experiment-scaffold', 'artifact-packaging'],
                provider_preferences={'reasoning': 'llm'},
            )
        )
        _registry.register_builtin(
            AgentSpec(
                id='writer',
                name='Writer Agent',
                role='writer',
                description='Drafts structured research papers and publication assets.',
                default_skills=['paper-outline', 'section-drafting', 'latex-assembly'],
                provider_preferences={'reasoning': 'llm'},
            )
        )
        _registry.register_builtin(
            AgentSpec(
                id='reviewer',
                name='Reviewer Agent',
                role='reviewer',
                description='Simulates peer review, criticism, and revision guidance.',
                default_skills=['review-critique', 'consistency-audit'],
                provider_preferences={'reasoning': 'llm'},
            )
        )
        _registry.refresh()
    return _registry
