import json
from pathlib import Path
from typing import Dict, List

from app.faros.models.profile import Profile
from app.faros.registry.agent_registry import get_agent_registry
from app.faros.registry.capability_registry import get_capability_registry
from app.faros.registry.provider_registry import get_provider_registry
from app.faros.registry.skill_registry import get_skill_registry
from app.faros.verification.profile_validator import ProfileValidator


class ProfileLoader:
    """Load FAROS execution profiles from disk."""

    def __init__(self, root: Path):
        self.root = root
        self.validator = ProfileValidator(
            capabilities=get_capability_registry(),
            providers=get_provider_registry(),
            agents=get_agent_registry(),
            skills=get_skill_registry(),
        )

    def list_profiles(self) -> List[Profile]:
        return [self.load(path.parent.name) for path in sorted(self.root.glob("*/profile.json"))]

    def load(self, profile_id: str) -> Profile:
        path = self.root / profile_id / "profile.json"
        if not path.is_file():
            raise FileNotFoundError(f"Profile '{profile_id}' not found")
        profile = Profile.model_validate(json.loads(path.read_text()))
        validation = self.validator.validate(profile)
        if validation.status != 'passed':
            raise ValueError(validation.message + ': ' + '; '.join(validation.errors))
        return profile

    def describe(self) -> List[Dict[str, str]]:
        return [
            {
                "id": profile.id,
                "name": profile.name,
                "version": profile.version,
                "description": profile.description,
            }
            for profile in self.list_profiles()
        ]
