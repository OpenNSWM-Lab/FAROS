from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class PaperSkillResult:
    name: str
    summary: str
    artifacts: List[str] = field(default_factory=list)
    data: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


@dataclass
class PaperSkillContext:
    paper_id: str
    paper: Dict[str, Any]
    settings: Any
    provider_name: str
    model: str
    paper_type: str
    venue: str
    venue_cfg: Dict[str, Any]
    client: Any
    latex_dir: str
    artifacts_dir: str
    data: Dict[str, Any] = field(default_factory=dict)
    step_log: List[Dict[str, Any]] = field(default_factory=list)

    def update(self, key: str, value: Any) -> None:
        self.data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def llm_timeout(self) -> int:
        provider_timeout = getattr(getattr(self.client, "config", None), "timeout", 0) or 0
        paper_timeout = getattr(self.settings, "PAPER_GENERATION_TIMEOUT", provider_timeout) or provider_timeout
        return max(provider_timeout, paper_timeout)
