"""Skill-based paper generation pipeline."""

from .leader import PaperSkillLeader, build_default_skill_chain
from .base import PaperSkillContext, PaperSkillResult

__all__ = [
    "PaperSkillLeader",
    "PaperSkillContext",
    "PaperSkillResult",
    "build_default_skill_chain",
]
