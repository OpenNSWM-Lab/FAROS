import shutil
from pathlib import Path
from typing import Dict, List

from app.faros.loaders.skill_loader import SkillLoader
from app.faros.models.skill import SkillSpec
from app.faros.registry.package_lifecycle import compare_semver, package_dir_exists
from app.faros.registry.package_compatibility import enforce_compatibility, validate_dependency_graph
from app.faros.registry.package_trust import enforce_trust_policy, inspect_package_trust
from app.faros.runtime.package_audit import PackageAuditStore


class SkillRegistry:
    """Registry of runtime skills available to FAROS agents."""

    def __init__(self, root: Path, audit_store: PackageAuditStore | None = None):
        self.root = root
        self.loader = SkillLoader(root)
        self.audit = audit_store or PackageAuditStore()
        self._skills: Dict[str, SkillSpec] = {}
        self.refresh()

    def refresh(self) -> List[SkillSpec]:
        self._skills = {skill.id: skill for skill in self.loader.list_skills()}
        return self.list()

    def register(self, skill: SkillSpec) -> None:
        self._skills[skill.id] = skill

    def validate_package(self, package_path: Path | str) -> SkillSpec:
        return self.loader.validate_package(package_path)

    def install_package(
        self,
        package_path: Path | str,
        overwrite: bool = False,
        allow_downgrade: bool = False,
        allow_untrusted: bool = True,
    ) -> SkillSpec:
        incoming = self.loader.validate_package(package_path)
        existing = self.loader.load(incoming.id) if package_dir_exists(self.root, incoming.id, 'skill.json') else None
        from app.faros.registry.agent_registry import get_agent_registry
        from app.faros.registry.profile_registry import get_profile_registry

        enforce_compatibility(
            f"Skill '{incoming.id}'",
            incoming.compatibility,
            {
                'profiles': {profile.id: profile.version for profile in get_profile_registry().list()},
                'agents': {agent.id: getattr(agent, 'version', '0.0.0') for agent in get_agent_registry().list()},
                'skills': {skill.id: skill.version for skill in self.list()},
            },
        )

        source_dir = Path(package_path).expanduser().resolve()
        if source_dir.is_file():
            source_dir = source_dir.parent
        trust_report = inspect_package_trust('skill', incoming.id, source_dir, version=getattr(incoming, 'version', None))
        enforce_trust_policy(trust_report, allow_untrusted=allow_untrusted)

        action = 'install'
        previous_version = None
        if existing is not None:
            previous_version = existing.version
            if not overwrite:
                raise ValueError(
                    f"Skill '{incoming.id}' is already installed; pass overwrite=true to replace it"
                )
            if compare_semver(incoming.version, existing.version) < 0 and not allow_downgrade:
                raise ValueError(
                    f"Refusing to downgrade skill '{incoming.id}' from {existing.version} to {incoming.version}; pass allow_downgrade=true to override"
                )
            self.audit.backup_package('skill', incoming.id, self.root / incoming.id, version=existing.version)
            action = 'upgrade' if compare_semver(incoming.version, existing.version) != 0 else 'reinstall'
        skill = self.loader.install_package(package_path, overwrite=overwrite)
        self.refresh()
        try:
            from app.faros.registry.blueprint_registry import get_blueprint_registry
            validate_dependency_graph({
                'profiles': get_profile_registry().list(),
                'agents': get_agent_registry().list(),
                'skills': self.list(),
                'blueprints': get_blueprint_registry().list(),
            })
        except Exception:
            if existing is not None:
                self.audit.restore_backup('skill', incoming.id, self.root / incoming.id)
            else:
                shutil.rmtree(self.root / incoming.id, ignore_errors=True)
            self.refresh()
            raise
        self.audit.append_event({
            'packageType': 'skill',
            'packageId': skill.id,
            'action': action,
            'version': skill.version,
            'previousVersion': previous_version,
            'sourcePath': str(package_path),
            'trustStatus': trust_report.get('policyStatus'),
            'trustLevel': trust_report.get('trustLevel'),
            'trusted': trust_report.get('trusted'),
        })
        return self.get(skill.id)


    def rollback_package(self, skill_id: str) -> SkillSpec:
        restored = self.audit.restore_backup('skill', skill_id, self.root / skill_id)
        self.refresh()
        version = restored.get('version')
        if version and skill_id in self._skills and self._skills[skill_id].version != version:
            raise ValueError(f"Rollback restored unexpected version for skill '{skill_id}'")
        return self.get(skill_id)

    def uninstall_package(self, skill_id: str) -> Dict[str, str]:
        manifest_path = self.root / skill_id / 'skill.json'
        if not manifest_path.is_file():
            raise ValueError(f"Skill '{skill_id}' is not installed")
        previous = self.loader.load(skill_id)
        shutil.rmtree(manifest_path.parent)
        self.refresh()
        self.audit.append_event({
            'packageType': 'skill',
            'packageId': skill_id,
            'action': 'uninstall',
            'version': previous.version,
        })
        return {'id': skill_id, 'version': previous.version, 'status': 'uninstalled'}

    def audit_log(self, limit: int = 50) -> List[dict]:
        return self.audit.list_events(package_type='skill', limit=limit)

    def get(self, skill_id: str) -> SkillSpec:
        if skill_id not in self._skills:
            raise KeyError(f"Skill '{skill_id}' is not registered")
        return self._skills[skill_id]

    def list(self) -> List[SkillSpec]:
        return list(self._skills.values())


_registry: SkillRegistry | None = None


def get_skill_registry() -> SkillRegistry:
    global _registry
    if _registry is None:
        root = Path(__file__).resolve().parents[1] / 'skills'
        _registry = SkillRegistry(root)
    return _registry
