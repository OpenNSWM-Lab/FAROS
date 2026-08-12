from typing import Dict

from .base import PaperSkillContext, PaperSkillResult
from .utils import collect_context


STEP_ID = "01_collect_context"


def run(ctx: PaperSkillContext) -> PaperSkillResult:
    context = collect_context(ctx.paper)
    return PaperSkillResult(
        name="collect_context",
        summary="context collected",
        artifacts=[],
        data={"context": context},
    )
