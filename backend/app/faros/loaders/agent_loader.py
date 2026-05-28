import json
import re
import shutil
from pathlib import Path
from typing import List

from app.faros.models.agent import AgentSpec
from app.faros.registry.skill_registry import get_skill_registry
from app.faros.registry.package_compatibility import validate_compatibility_map


class AgentLoader:
    """Load FAROS agent manifests from disk and validate their basic shape."""

    IDENTIFIER_RE = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
    ROLE_RE = re.compile(r'^[a-z0-9_]+$')
    SEMVER_RE = re.compile(r'^\d+\.\d+\.\d+$')

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def list_agents(self) -> List[AgentSpec]:
        return [self.load(path.parent.name) for path in sorted(self.root.glob('*/agent.json'))]

    def load(self, agent_id: str) -> AgentSpec:
        path = self.root / agent_id / 'agent.json'
        if not path.is_file():
            raise FileNotFoundError(f"Agent '{agent_id}' not found")
        return self.load_from_manifest(path)

    def load_from_manifest(self, manifest_path: Path) -> AgentSpec:
        manifest_path = manifest_path.resolve()
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Agent manifest '{manifest_path}' not found")
        payload = json.loads(manifest_path.read_text())
        agent = AgentSpec.model_validate(payload)
        self.validate(agent, manifest_path)
        return agent

    def validate(self, agent: AgentSpec, manifest_path: Path | None = None) -> None:
        if not agent.id:
            raise ValueError('Agent id cannot be empty')
        if not self.IDENTIFIER_RE.match(agent.id):
            raise ValueError(f"Agent '{agent.id}' must use lowercase kebab-case identifier format")
        if not agent.name.strip():
            raise ValueError(f"Agent '{agent.id}' must define a name")
        if not self.SEMVER_RE.match(agent.version):
            raise ValueError(f"Agent '{agent.id}' must define a semantic version like X.Y.Z")
        if not agent.role.strip() or not self.ROLE_RE.match(agent.role):
            raise ValueError(f"Agent '{agent.id}' must define a valid role")
        if not agent.description.strip():
            raise ValueError(f"Agent '{agent.id}' must define a description")
        if len(set(agent.default_skills)) != len(agent.default_skills):
            raise ValueError(f"Agent '{agent.id}' must not duplicate default skills")
        validate_compatibility_map(agent.compatibility, f"Agent '{agent.id}'")

        skills = get_skill_registry()
        for skill_id in agent.default_skills:
            try:
                skill = skills.get(skill_id)
            except KeyError as exc:
                raise ValueError(
                    f"Agent '{agent.id}' references unknown default skill '{skill_id}'"
                ) from exc
            if agent.role not in skill.agent_roles:
                raise ValueError(
                    f"Agent '{agent.id}' role '{agent.role}' is incompatible with skill '{skill_id}'"
                )

        if manifest_path is not None:
            manifest_path = manifest_path.resolve()
            if manifest_path.parent.name != agent.id:
                raise ValueError(
                    f"Agent directory '{manifest_path.parent.name}' must match agent id '{agent.id}'"
                )
            readme_path = manifest_path.parent / 'README.md'
            if not readme_path.is_file():
                raise ValueError(f"Agent '{agent.id}' must include a README.md")

    def validate_package(self, package_path: Path | str) -> AgentSpec:
        manifest_path = self._resolve_manifest_path(package_path)
        return self.load_from_manifest(manifest_path)

    def install_package(self, package_path: Path | str, overwrite: bool = False) -> AgentSpec:
        manifest_path = self._resolve_manifest_path(package_path)
        agent = self.load_from_manifest(manifest_path)
        src_dir = manifest_path.parent
        dst_dir = self.root / agent.id
        if dst_dir.exists():
            if not overwrite:
                raise ValueError(
                    f"Agent '{agent.id}' is already installed; pass overwrite=true to replace it"
                )
            shutil.rmtree(dst_dir)
        shutil.copytree(src_dir, dst_dir)
        return self.load(agent.id)

    def _resolve_manifest_path(self, package_path: Path | str) -> Path:
        path = Path(package_path).expanduser().resolve()
        manifest_path = path / 'agent.json' if path.is_dir() else path
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Agent manifest '{manifest_path}' not found")
        return manifest_path
