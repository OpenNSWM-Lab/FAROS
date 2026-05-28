import json
import re
import shutil
from pathlib import Path
from typing import Dict, List

from app.faros.models.verification import VerifierPackageSpec, VerifierPackDescriptor
from app.faros.registry.package_compatibility import validate_compatibility_map


class VerifierLoader:
    """Load FAROS verifier package manifests from disk."""

    SEMVER_RE = re.compile(r'^\d+\.\d+\.\d+$')
    MANIFEST_VERSION_RE = re.compile(r'^\d+\.\d+$')
    IDENTIFIER_RE = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def list_packages(self) -> List[VerifierPackageSpec]:
        return [self.load(path.parent.name) for path in sorted(self.root.glob('*/verifier.json'))]

    def load(self, package_id: str) -> VerifierPackageSpec:
        path = self.root / package_id / 'verifier.json'
        if not path.is_file():
            raise FileNotFoundError(f"Verifier package '{package_id}' not found")
        return self.load_from_manifest(path)

    def load_from_manifest(self, manifest_path: Path) -> VerifierPackageSpec:
        manifest_path = manifest_path.resolve()
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Verifier manifest '{manifest_path}' not found")
        payload = json.loads(manifest_path.read_text())
        payload.setdefault('manifest_version', '1.0')
        payload['packs'] = self._normalize_packs(payload.get('packs', {}), payload.get('id', ''))
        try:
            payload.setdefault('manifest_path', str(manifest_path.relative_to(self.root.parent)))
        except ValueError:
            payload.setdefault('manifest_path', str(manifest_path))
        spec = VerifierPackageSpec.model_validate(payload)
        self.validate(spec, manifest_path)
        return spec

    def _normalize_packs(self, packs: Dict[str, object], package_id: str) -> Dict[str, Dict[str, object]]:
        normalized: Dict[str, Dict[str, object]] = {}
        for pack_id, raw in (packs or {}).items():
            if isinstance(raw, list):
                normalized[pack_id] = {
                    'id': pack_id,
                    'name': pack_id.replace('-', ' ').title(),
                    'verifier_ids': list(raw),
                    'package_id': package_id or None,
                }
                continue
            if not isinstance(raw, dict):
                raise ValueError(f"Verifier pack '{pack_id}' must be either a verifier id list or an object")
            item = dict(raw)
            item.setdefault('id', pack_id)
            item.setdefault('name', pack_id.replace('-', ' ').title())
            item.setdefault('package_id', package_id or None)
            normalized[pack_id] = item
        return normalized

    def validate(self, spec: VerifierPackageSpec, manifest_path: Path | None = None) -> None:
        if not self.IDENTIFIER_RE.match(spec.id):
            raise ValueError(f"Verifier package '{spec.id}' must use lowercase kebab-case identifier format")
        if not spec.name:
            raise ValueError(f"Verifier package '{spec.id}' must define a name")
        if not self.SEMVER_RE.match(spec.version):
            raise ValueError(f"Verifier package '{spec.id}' must define a semantic version like X.Y.Z")
        if not self.MANIFEST_VERSION_RE.match(spec.manifest_version):
            raise ValueError(f"Verifier package '{spec.id}' must define a manifest version like X.Y")
        if not spec.description.strip():
            raise ValueError(f"Verifier package '{spec.id}' must define a description")
        if len(set(spec.verifier_ids)) != len(spec.verifier_ids):
            raise ValueError(f"Verifier package '{spec.id}' must not duplicate verifier ids")
        if len(set(spec.packs.keys())) != len(spec.packs.keys()):
            raise ValueError(f"Verifier package '{spec.id}' must not duplicate pack ids")
        validate_compatibility_map(spec.compatibility, f"Verifier package '{spec.id}'")
        for pack_id, pack in spec.packs.items():
            if pack.id != pack_id:
                raise ValueError(f"Verifier pack key '{pack_id}' must match pack id '{pack.id}' in '{spec.id}'")
            if not self.IDENTIFIER_RE.match(pack_id):
                raise ValueError(f"Verifier pack '{pack_id}' in '{spec.id}' must use lowercase kebab-case format")
            if not pack.name.strip():
                raise ValueError(f"Verifier pack '{pack_id}' in '{spec.id}' must define a name")
            if not isinstance(pack.verifier_ids, list) or not pack.verifier_ids:
                raise ValueError(f"Verifier pack '{pack_id}' in '{spec.id}' must contain at least one verifier id")
            if len(set(pack.verifier_ids)) != len(pack.verifier_ids):
                raise ValueError(f"Verifier pack '{pack_id}' in '{spec.id}' must not duplicate verifier ids")
            if len(set(pack.tags)) != len(pack.tags):
                raise ValueError(f"Verifier pack '{pack_id}' in '{spec.id}' must not duplicate tags")
            if len(set(pack.capability_ids)) != len(pack.capability_ids):
                raise ValueError(f"Verifier pack '{pack_id}' in '{spec.id}' must not duplicate capability ids")
            if len(set(pack.provider_types)) != len(pack.provider_types):
                raise ValueError(f"Verifier pack '{pack_id}' in '{spec.id}' must not duplicate provider types")
            if len(set(pack.recommended_node_ids)) != len(pack.recommended_node_ids):
                raise ValueError(f"Verifier pack '{pack_id}' in '{spec.id}' must not duplicate recommended node ids")
            if pack.package_id not in (None, '', spec.id):
                raise ValueError(f"Verifier pack '{pack_id}' in '{spec.id}' must not override package_id")
        if not spec.manifest_path.endswith('verifier.json'):
            raise ValueError(f"Verifier package '{spec.id}' manifest_path must point to verifier.json")
        if manifest_path is not None:
            manifest_path = manifest_path.resolve()
            if manifest_path.parent.name != spec.id:
                raise ValueError(f"Verifier package directory '{manifest_path.parent.name}' must match id '{spec.id}'")
            readme_path = manifest_path.parent / 'README.md'
            if not readme_path.is_file():
                raise ValueError(f"Verifier package '{spec.id}' must include a README.md")

    def validate_package(self, package_path: Path | str) -> VerifierPackageSpec:
        manifest_path = self._resolve_manifest_path(package_path)
        return self.load_from_manifest(manifest_path)

    def install_package(self, package_path: Path | str, overwrite: bool = False) -> VerifierPackageSpec:
        manifest_path = self._resolve_manifest_path(package_path)
        spec = self.load_from_manifest(manifest_path)
        src_dir = manifest_path.parent
        dst_dir = self.root / spec.id
        if dst_dir.exists():
            if not overwrite:
                raise ValueError(f"Verifier package '{spec.id}' is already installed; pass overwrite=true to replace it")
            shutil.rmtree(dst_dir)
        shutil.copytree(src_dir, dst_dir)
        return self.load(spec.id)

    def _resolve_manifest_path(self, package_path: Path | str) -> Path:
        path = Path(package_path).expanduser().resolve()
        manifest_path = path / 'verifier.json' if path.is_dir() else path
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Verifier manifest '{manifest_path}' not found")
        return manifest_path

    def describe(self) -> List[Dict[str, str]]:
        return [
            {
                'id': spec.id,
                'name': spec.name,
                'version': spec.version,
                'manifestVersion': spec.manifest_version,
                'description': spec.description,
            }
            for spec in self.list_packages()
        ]
