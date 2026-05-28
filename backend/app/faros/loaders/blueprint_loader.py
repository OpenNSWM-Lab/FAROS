import json
import re
import shutil
from pathlib import Path
from typing import Dict, List

from app.faros.models.blueprint import Blueprint
from app.faros.verification.blueprint_validator import BlueprintValidator
from app.faros.registry.package_compatibility import validate_compatibility_map


class BlueprintLoader:
    """Load blueprint assets from disk."""

    SEMVER_RE = re.compile(r'^\d+\.\d+\.\d+$')

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.validator = BlueprintValidator()

    def list_blueprints(self) -> List[Blueprint]:
        return [self.load(path.parent.name) for path in sorted(self.root.glob('*/blueprint.json'))]

    def load(self, blueprint_id: str) -> Blueprint:
        path = self.root / blueprint_id / 'blueprint.json'
        if not path.is_file():
            raise FileNotFoundError(f"Blueprint '{blueprint_id}' not found")
        return self.load_from_manifest(path)

    def load_from_manifest(self, manifest_path: Path) -> Blueprint:
        manifest_path = manifest_path.resolve()
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Blueprint manifest '{manifest_path}' not found")
        blueprint = Blueprint.model_validate(json.loads(manifest_path.read_text()))
        self.validate(blueprint, manifest_path)
        return blueprint

    def validate(self, blueprint: Blueprint, manifest_path: Path | None = None) -> None:
        if not self.SEMVER_RE.match(blueprint.version):
            raise ValueError(f"Blueprint '{blueprint.id}' must define a semantic version like X.Y.Z")
        validate_compatibility_map(blueprint.compatibility, f"Blueprint '{blueprint.id}'")
        result = self.validator.validate(blueprint)
        if result.status != 'passed':
            raise ValueError(f"Invalid blueprint '{blueprint.id}': {'; '.join(result.errors)}")
        if manifest_path is not None:
            manifest_path = manifest_path.resolve()
            if manifest_path.parent.name != blueprint.id:
                raise ValueError(
                    f"Blueprint directory '{manifest_path.parent.name}' must match blueprint id '{blueprint.id}'"
                )
            readme_path = manifest_path.parent / 'README.md'
            if not readme_path.is_file():
                raise ValueError(f"Blueprint '{blueprint.id}' must include a README.md")

    def validate_package(self, package_path: Path | str) -> Blueprint:
        manifest_path = self._resolve_manifest_path(package_path)
        return self.load_from_manifest(manifest_path)

    def install_package(self, package_path: Path | str, overwrite: bool = False) -> Blueprint:
        manifest_path = self._resolve_manifest_path(package_path)
        blueprint = self.load_from_manifest(manifest_path)
        src_dir = manifest_path.parent
        dst_dir = self.root / blueprint.id
        if dst_dir.exists():
            if not overwrite:
                raise ValueError(
                    f"Blueprint '{blueprint.id}' is already installed; pass overwrite=true to replace it"
                )
            shutil.rmtree(dst_dir)
        shutil.copytree(src_dir, dst_dir)
        return self.load(blueprint.id)

    def _resolve_manifest_path(self, package_path: Path | str) -> Path:
        path = Path(package_path).expanduser().resolve()
        manifest_path = path / 'blueprint.json' if path.is_dir() else path
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Blueprint manifest '{manifest_path}' not found")
        return manifest_path

    def describe(self) -> List[Dict[str, str]]:
        return [
            {
                'id': blueprint.id,
                'name': blueprint.name,
                'version': blueprint.version,
                'domain': blueprint.domain,
                'description': blueprint.description,
            }
            for blueprint in self.list_blueprints()
        ]
