import shutil
from pathlib import Path
from typing import Dict, List

from app.faros.loaders.blueprint_loader import BlueprintLoader
from app.faros.models.blueprint import Blueprint
from app.faros.registry.package_lifecycle import compare_semver, package_dir_exists
from app.faros.registry.package_compatibility import enforce_compatibility, validate_dependency_graph
from app.faros.registry.package_trust import enforce_trust_policy, inspect_package_trust
from app.faros.runtime.package_audit import PackageAuditStore


class BlueprintRegistry:
    """In-memory registry backed by blueprint assets on disk."""

    def __init__(self, root: Path, audit_store: PackageAuditStore | None = None):
        self.root = root
        self.loader = BlueprintLoader(root)
        self.audit = audit_store or PackageAuditStore()
        self._blueprints: Dict[str, Blueprint] = {}
        self.refresh()

    def refresh(self) -> List[Blueprint]:
        self._blueprints = {bp.id: bp for bp in self.loader.list_blueprints()}
        return self.list()

    def validate_package(self, package_path: Path | str) -> Blueprint:
        return self.loader.validate_package(package_path)

    def install_package(
        self,
        package_path: Path | str,
        overwrite: bool = False,
        allow_downgrade: bool = False,
        allow_untrusted: bool = True,
    ) -> Blueprint:
        from app.faros.registry.agent_registry import get_agent_registry
        from app.faros.registry.profile_registry import get_profile_registry
        from app.faros.registry.skill_registry import get_skill_registry

        incoming = self.loader.validate_package(package_path)
        enforce_compatibility(
            f"Blueprint '{incoming.id}'",
            incoming.compatibility,
            {
                'profiles': {profile.id: profile.version for profile in get_profile_registry().list()},
                'skills': {skill.id: skill.version for skill in get_skill_registry().list()},
                'agents': {agent.id: getattr(agent, 'version', '0.0.0') for agent in get_agent_registry().list()},
                'blueprints': {bp.id: bp.version for bp in self.list()},
            },
        )
        existing = self.loader.load(incoming.id) if package_dir_exists(self.root, incoming.id, 'blueprint.json') else None
        source_dir = Path(package_path).expanduser().resolve()
        if source_dir.is_file():
            source_dir = source_dir.parent
        trust_report = inspect_package_trust('blueprint', incoming.id, source_dir, version=getattr(incoming, 'version', None))
        enforce_trust_policy(trust_report, allow_untrusted=allow_untrusted)

        action = 'install'
        previous_version = None
        if existing is not None:
            previous_version = existing.version
            if not overwrite:
                raise ValueError(
                    f"Blueprint '{incoming.id}' is already installed; pass overwrite=true to replace it"
                )
            if compare_semver(incoming.version, existing.version) < 0 and not allow_downgrade:
                raise ValueError(
                    f"Refusing to downgrade blueprint '{incoming.id}' from {existing.version} to {incoming.version}; pass allow_downgrade=true to override"
                )
            self.audit.backup_package('blueprint', incoming.id, self.root / incoming.id, version=existing.version)
            action = 'upgrade' if compare_semver(incoming.version, existing.version) != 0 else 'reinstall'
        blueprint = self.loader.install_package(package_path, overwrite=overwrite)
        self.refresh()
        try:
            validate_dependency_graph({
                'profiles': get_profile_registry().list(),
                'agents': get_agent_registry().list(),
                'skills': get_skill_registry().list(),
                'blueprints': self.list(),
            })
        except Exception:
            if existing is not None:
                self.audit.restore_backup('blueprint', incoming.id, self.root / incoming.id)
            else:
                shutil.rmtree(self.root / incoming.id, ignore_errors=True)
            self.refresh()
            raise
        self.audit.append_event({
            'packageType': 'blueprint',
            'packageId': blueprint.id,
            'action': action,
            'version': blueprint.version,
            'previousVersion': previous_version,
            'sourcePath': str(package_path),
            'trustStatus': trust_report.get('policyStatus'),
            'trustLevel': trust_report.get('trustLevel'),
            'trusted': trust_report.get('trusted'),
        })
        return self.get(blueprint.id)


    def rollback_package(self, blueprint_id: str) -> Blueprint:
        restored = self.audit.restore_backup('blueprint', blueprint_id, self.root / blueprint_id)
        self.refresh()
        version = restored.get('version')
        if version and blueprint_id in self._blueprints and self._blueprints[blueprint_id].version != version:
            raise ValueError(f"Rollback restored unexpected version for blueprint '{blueprint_id}'")
        return self.get(blueprint_id)

    def uninstall_package(self, blueprint_id: str) -> Dict[str, str]:
        manifest_path = self.root / blueprint_id / 'blueprint.json'
        if not manifest_path.is_file():
            raise ValueError(f"Blueprint '{blueprint_id}' is not installed")
        previous = self.loader.load(blueprint_id)
        shutil.rmtree(manifest_path.parent)
        self.refresh()
        self.audit.append_event({
            'packageType': 'blueprint',
            'packageId': blueprint_id,
            'action': 'uninstall',
            'version': previous.version,
        })
        return {'id': blueprint_id, 'version': previous.version, 'status': 'uninstalled'}

    def audit_log(self, limit: int = 50) -> List[dict]:
        return self.audit.list_events(package_type='blueprint', limit=limit)

    def list(self) -> List[Blueprint]:
        return list(self._blueprints.values())

    def get(self, blueprint_id: str) -> Blueprint:
        if blueprint_id not in self._blueprints:
            raise KeyError(f"Blueprint '{blueprint_id}' is not registered")
        return self._blueprints[blueprint_id]


_registry: BlueprintRegistry | None = None


def get_blueprint_registry() -> BlueprintRegistry:
    global _registry
    if _registry is None:
        root = Path(__file__).resolve().parents[1] / 'blueprints'
        _registry = BlueprintRegistry(root)
    return _registry
