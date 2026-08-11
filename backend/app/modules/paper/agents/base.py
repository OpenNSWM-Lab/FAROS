from __future__ import annotations

import time
from typing import Any, Callable

from app.modules.paper.storage import add_log
from app.modules.paper.skills.base import PaperSkillContext, PaperSkillResult


class PaperAgent:
    name = "paper_agent"

    def __init__(self, paper_id: str, log_func: Callable[[str], None]) -> None:
        self.paper_id = paper_id
        self.log = log_func

    def _run_skill(
        self,
        ctx: PaperSkillContext,
        label: str,
        skill: Callable[[PaperSkillContext], PaperSkillResult],
    ) -> PaperSkillResult:
        self.log(f"{self.name}: running skill {label}")
        start = time.time()
        result = skill(ctx)
        elapsed = time.time() - start
        self._apply_result_data(ctx, result)
        if result.summary:
            self.log(f"{self.name}/{result.name}: {result.summary} ({elapsed:.1f}s)")
        else:
            self.log(f"{self.name}/{result.name}: completed ({elapsed:.1f}s)")
        if result.artifacts:
            add_log(self.paper_id, f"Artifacts: {', '.join(result.artifacts)}")
        return result

    @staticmethod
    def _apply_result_data(ctx: PaperSkillContext, result: Any) -> None:
        if result.data:
            for key, value in result.data.items():
                ctx.update(key, value)
