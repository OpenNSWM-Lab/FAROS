import json
import re
import shutil
from pathlib import Path
from typing import Dict, List

from app.faros.models.skill import SkillSpec
from app.faros.registry.package_compatibility import validate_compatibility_map


class SkillLoader:
    """Load FAROS skill manifests from disk and validate their basic shape."""

    ALLOWED_KINDS = {
        'reasoning',
        'retrieval',
        'execution',
        'runtime',
        'writing',
        'review',
        'verification',
        'compilation',
    }
    ALLOWED_PROVIDER_REQUIREMENT_KEYS = {
        'reasoning',
        'retrieval',
        'execution',
        'compilation',
        'human',
        'storage',
    }
    SEMVER_RE = re.compile(r'^\d+\.\d+\.\d+$')
    MANIFEST_VERSION_RE = re.compile(r'^\d+\.\d+$')
    IDENTIFIER_RE = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def list_skills(self) -> List[SkillSpec]:
        return [self.load(path.parent.name) for path in sorted(self.root.glob('*/skill.json'))]

    def load(self, skill_id: str) -> SkillSpec:
        path = self.root / skill_id / 'skill.json'
        if not path.is_file():
            raise FileNotFoundError(f"Skill '{skill_id}' not found")
        return self.load_from_manifest(path)

    def load_from_manifest(self, manifest_path: Path) -> SkillSpec:
        manifest_path = manifest_path.resolve()
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Skill manifest '{manifest_path}' not found")
        payload = json.loads(manifest_path.read_text())
        payload.setdefault('manifest_version', '1.0')
        try:
            payload.setdefault('manifest_path', str(manifest_path.relative_to(self.root.parent)))
        except ValueError:
            payload.setdefault('manifest_path', str(manifest_path))
        skill = SkillSpec.model_validate(payload)
        self.validate(skill, manifest_path)
        return skill

    def validate(self, skill: SkillSpec, manifest_path: Path | None = None) -> None:
        if not skill.id:
            raise ValueError('Skill id cannot be empty')
        if not self.IDENTIFIER_RE.match(skill.id):
            raise ValueError(
                f"Skill '{skill.id}' must use lowercase kebab-case identifier format"
            )
        if not skill.name:
            raise ValueError(f"Skill '{skill.id}' must define a name")
        if not self.SEMVER_RE.match(skill.version):
            raise ValueError(f"Skill '{skill.id}' must define a semantic version like X.Y.Z")
        if not self.MANIFEST_VERSION_RE.match(skill.manifest_version):
            raise ValueError(
                f"Skill '{skill.id}' must define a manifest version like X.Y"
            )
        if skill.kind not in self.ALLOWED_KINDS:
            raise ValueError(
                f"Skill '{skill.id}' kind '{skill.kind}' is not supported"
            )
        if not skill.description.strip():
            raise ValueError(f"Skill '{skill.id}' must define a description")
        if not skill.agent_roles:
            raise ValueError(f"Skill '{skill.id}' must declare at least one agent role")
        if len(set(skill.agent_roles)) != len(skill.agent_roles):
            raise ValueError(f"Skill '{skill.id}' must not duplicate agent roles")
        if len(set(skill.tags)) != len(skill.tags):
            raise ValueError(f"Skill '{skill.id}' must not duplicate tags")
        if len(set(skill.artifact_types)) != len(skill.artifact_types):
            raise ValueError(f"Skill '{skill.id}' must not duplicate artifact types")
        if len(set(skill.verification_hooks)) != len(skill.verification_hooks):
            raise ValueError(f"Skill '{skill.id}' must not duplicate verification hooks")

        validate_compatibility_map(skill.compatibility, f"Skill '{skill.id}'")

        for key, value in skill.provider_requirements.items():
            if key not in self.ALLOWED_PROVIDER_REQUIREMENT_KEYS:
                raise ValueError(
                    f"Skill '{skill.id}' has unsupported provider requirement key '{key}'"
                )
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"Skill '{skill.id}' provider requirement '{key}' must be a non-empty string"
                )

        if not skill.manifest_path.endswith('skill.json'):
            raise ValueError(f"Skill '{skill.id}' manifest_path must point to skill.json")

        if manifest_path is not None:
            manifest_path = manifest_path.resolve()
            if manifest_path.parent.name != skill.id:
                raise ValueError(
                    f"Skill directory '{manifest_path.parent.name}' must match skill id '{skill.id}'"
                )
            readme_path = manifest_path.parent / 'README.md'
            if not readme_path.is_file():
                raise ValueError(f"Skill '{skill.id}' must include a README.md")

    def validate_package(self, package_path: Path | str) -> SkillSpec:
        manifest_path = self._resolve_manifest_path(package_path)
        return self.load_from_manifest(manifest_path)

    def install_package(self, package_path: Path | str, overwrite: bool = False) -> SkillSpec:
        manifest_path = self._resolve_manifest_path(package_path)
        skill = self.load_from_manifest(manifest_path)
        src_dir = manifest_path.parent
        dst_dir = self.root / skill.id
        if dst_dir.exists():
            if not overwrite:
                raise ValueError(
                    f"Skill '{skill.id}' is already installed; pass overwrite=true to replace it"
                )
            shutil.rmtree(dst_dir)
        shutil.copytree(src_dir, dst_dir)
        return self.load(skill.id)

    def _resolve_manifest_path(self, package_path: Path | str) -> Path:
        path = Path(package_path).expanduser().resolve()
        manifest_path = path / 'skill.json' if path.is_dir() else path
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Skill manifest '{manifest_path}' not found")
        return manifest_path

    def describe(self) -> List[Dict[str, str]]:
        return [
            {
                'id': skill.id,
                'name': skill.name,
                'version': skill.version,
                'manifestVersion': skill.manifest_version,
                'kind': skill.kind,
                'description': skill.description,
            }
            for skill in self.list_skills()
        ]
