import shutil
from pathlib import Path
from typing import Dict, List

from app.faros.loaders.verifier_loader import VerifierLoader
from app.faros.models.verification import VerifierPackageSpec, VerifierPackDescriptor
from app.faros.registry.package_lifecycle import compare_semver, package_dir_exists
from app.faros.registry.package_compatibility import enforce_compatibility
from app.faros.registry.package_trust import enforce_trust_policy, inspect_package_trust
from app.faros.runtime.package_audit import PackageAuditStore
from app.faros.verification.base import BaseVerifier
from app.faros.verification.rules import (
    ArtifactContractVerifier,
    ArtifactSchemaVerifier,
    OutputSchemaVerifier,
    ReviewActionItemsVerifier,
    RuntimeMetadataVerifier,
    StatusVerifier,
)


class VerifierRegistry:
    """Registry of builtin verifiers plus installable verifier-pack bundles."""

    def __init__(self, root: Path, audit_store: PackageAuditStore | None = None):
        self.root = root
        self.loader = VerifierLoader(root)
        self.audit = audit_store or PackageAuditStore()
        self._verifiers: Dict[str, BaseVerifier] = {}
        self._builtin_packs: Dict[str, List[str]] = {}
        self._builtin_pack_metadata: Dict[str, VerifierPackDescriptor] = {}
        self._packages: Dict[str, VerifierPackageSpec] = {}
        self._package_packs: Dict[str, List[str]] = {}
        self._package_pack_metadata: Dict[str, VerifierPackDescriptor] = {}
        self._register_builtin_verifiers()
        self.refresh()

    def _register_builtin_verifiers(self) -> None:
        self._verifiers = {}
        for verifier in [
            StatusVerifier(),
            OutputSchemaVerifier(),
            ArtifactContractVerifier(),
            ArtifactSchemaVerifier(),
            RuntimeMetadataVerifier(),
            ReviewActionItemsVerifier(),
        ]:
            self._verifiers[verifier.verifier_id] = verifier
        self._builtin_packs = {
            'runtime_baseline': [
                'status',
                'required_outputs',
                'artifact_contract',
                'artifact_schema',
                'runtime_metadata',
            ],
            'review_quality': ['review_action_items'],
        }
        self._builtin_pack_metadata = {
            'runtime_baseline': VerifierPackDescriptor(
                id='runtime_baseline',
                name='Runtime Baseline',
                description='Default runtime verification pack for structural execution checks.',
                verifier_ids=list(self._builtin_packs['runtime_baseline']),
                tags=['runtime', 'baseline'],
                capability_ids=['idea_refinement', 'experiment', 'paper_drafting', 'reviewer_simulation'],
                provider_types=['llm', 'tool', 'execution', 'human'],
                recommended_node_ids=['idea', 'experiment', 'paper', 'review'],
            ),
            'review_quality': VerifierPackDescriptor(
                id='review_quality',
                name='Review Quality',
                description='Additional review-focused checks for actionable critique outputs.',
                verifier_ids=list(self._builtin_packs['review_quality']),
                tags=['review', 'quality'],
                capability_ids=['reviewer_simulation'],
                provider_types=['llm', 'human'],
                recommended_node_ids=['review'],
            ),
        }

    def refresh(self) -> List[VerifierPackageSpec]:
        self._packages = {spec.id: spec for spec in self.loader.list_packages()}
        self._package_packs = {}
        self._package_pack_metadata = {}
        for spec in self._packages.values():
            self._validate_package_refs(spec)
            for pack_id, pack in spec.packs.items():
                self._package_packs[pack_id] = list(pack.verifier_ids)
                self._package_pack_metadata[pack_id] = pack.model_copy(update={
                    'id': pack_id,
                    'package_id': spec.id,
                    'verifier_ids': list(pack.verifier_ids),
                })
        return self.list_packages()

    def _validate_package_refs(self, spec: VerifierPackageSpec) -> None:
        known_ids = set(self._verifiers.keys())
        for verifier_id in spec.verifier_ids:
            if verifier_id not in known_ids:
                raise ValueError(f"Verifier package '{spec.id}' references unknown verifier '{verifier_id}'")
        for pack_id, pack in spec.packs.items():
            for verifier_id in pack.verifier_ids:
                if verifier_id not in known_ids:
                    raise ValueError(f"Verifier package '{spec.id}' pack '{pack_id}' references unknown verifier '{verifier_id}'")
            if pack_id in self._builtin_packs:
                raise ValueError(f"Verifier package '{spec.id}' cannot override builtin pack '{pack_id}'")

    def validate_package(self, package_path: Path | str) -> VerifierPackageSpec:
        spec = self.loader.validate_package(package_path)
        self._validate_package_refs(spec)
        return spec

    def install_package(self, package_path: Path | str, overwrite: bool = False, allow_downgrade: bool = False, allow_untrusted: bool = True) -> VerifierPackageSpec:
        incoming = self.validate_package(package_path)
        existing = self.loader.load(incoming.id) if package_dir_exists(self.root, incoming.id, 'verifier.json') else None
        from app.faros.registry.profile_registry import get_profile_registry
        enforce_compatibility(
            f"Verifier package '{incoming.id}'",
            incoming.compatibility,
            {
                'profiles': {profile.id: profile.version for profile in get_profile_registry().list()},
                'agents': {},
                'skills': {},
                'blueprints': {},
                'verifiers': {spec.id: spec.version for spec in self.list_packages()},
            },
        )

        source_dir = Path(package_path).expanduser().resolve()
        if source_dir.is_file():
            source_dir = source_dir.parent
        trust_report = inspect_package_trust('verifier', incoming.id, source_dir, version=getattr(incoming, 'version', None))
        enforce_trust_policy(trust_report, allow_untrusted=allow_untrusted)

        action = 'install'
        previous_version = None
        if existing is not None:
            previous_version = existing.version
            if not overwrite:
                raise ValueError(f"Verifier package '{incoming.id}' is already installed; pass overwrite=true to replace it")
            if compare_semver(incoming.version, existing.version) < 0 and not allow_downgrade:
                raise ValueError(f"Refusing to downgrade verifier package '{incoming.id}' from {existing.version} to {incoming.version}; pass allow_downgrade=true to override")
            self.audit.backup_package('verifier', incoming.id, self.root / incoming.id, version=existing.version)
            action = 'upgrade' if compare_semver(incoming.version, existing.version) != 0 else 'reinstall'
        spec = self.loader.install_package(package_path, overwrite=overwrite)
        self.refresh()
        self.audit.append_event({
            'packageType': 'verifier',
            'packageId': spec.id,
            'action': action,
            'version': spec.version,
            'previousVersion': previous_version,
            'sourcePath': str(package_path),
            'trustStatus': trust_report.get('policyStatus'),
            'trustLevel': trust_report.get('trustLevel'),
            'trusted': trust_report.get('trusted'),
        })
        return self.get_package(spec.id)

    def rollback_package(self, package_id: str) -> VerifierPackageSpec:
        restored = self.audit.restore_backup('verifier', package_id, self.root / package_id)
        self.refresh()
        version = restored.get('version')
        if version and package_id in self._packages and self._packages[package_id].version != version:
            raise ValueError(f"Rollback restored unexpected version for verifier package '{package_id}'")
        return self.get_package(package_id)

    def uninstall_package(self, package_id: str) -> Dict[str, str]:
        manifest_path = self.root / package_id / 'verifier.json'
        if not manifest_path.is_file():
            raise ValueError(f"Verifier package '{package_id}' is not installed")
        previous = self.loader.load(package_id)
        shutil.rmtree(manifest_path.parent)
        self.refresh()
        self.audit.append_event({
            'packageType': 'verifier',
            'packageId': package_id,
            'action': 'uninstall',
            'version': previous.version,
        })
        return {'id': package_id, 'version': previous.version, 'status': 'uninstalled'}

    def audit_log(self, limit: int = 50) -> List[dict]:
        return self.audit.list_events(package_type='verifier', limit=limit)

    def get(self, verifier_id: str) -> BaseVerifier:
        if verifier_id not in self._verifiers:
            raise KeyError(f"Verifier '{verifier_id}' is not registered")
        return self._verifiers[verifier_id]

    def list(self) -> List[dict]:
        return [item.describe().model_dump() for _, item in sorted(self._verifiers.items())]

    def get_package(self, package_id: str) -> VerifierPackageSpec:
        if package_id not in self._packages:
            raise KeyError(f"Verifier package '{package_id}' is not installed")
        return self._packages[package_id]

    def list_packages(self) -> List[VerifierPackageSpec]:
        return list(self._packages.values())

    def packs(self) -> Dict[str, List[str]]:
        merged = {key: list(value) for key, value in sorted(self._builtin_packs.items())}
        for key, value in sorted(self._package_packs.items()):
            merged[key] = list(value)
        return merged

    def pack_descriptors(self) -> List[dict]:
        merged = {key: value.model_dump() for key, value in sorted(self._builtin_pack_metadata.items())}
        for key, value in sorted(self._package_pack_metadata.items()):
            merged[key] = value.model_dump()
        return list(merged.values())

    def expand(self, verifier_ids: List[str] | None = None, pack_ids: List[str] | None = None) -> List[str]:
        ordered: List[str] = []
        packs = self.packs()
        for pack_id in pack_ids or []:
            if pack_id not in packs:
                raise KeyError(f"Verifier pack '{pack_id}' is not registered")
            for verifier_id in packs[pack_id]:
                if verifier_id not in ordered:
                    ordered.append(verifier_id)
        for verifier_id in verifier_ids or []:
            if verifier_id not in self._verifiers:
                raise KeyError(f"Verifier '{verifier_id}' is not registered")
            if verifier_id not in ordered:
                ordered.append(verifier_id)
        return ordered

    def default_pack(self) -> List[str]:
        return list(self._builtin_packs.get('runtime_baseline', []))


_registry: VerifierRegistry | None = None


def get_verifier_registry() -> VerifierRegistry:
    global _registry
    if _registry is None:
        root = Path(__file__).resolve().parents[1] / 'verifier_packages'
        _registry = VerifierRegistry(root)
    return _registry
