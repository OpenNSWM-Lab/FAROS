import time
from typing import Callable, List

from app.modules.paper.storage import add_log
from .base import PaperSkillContext, PaperSkillResult
from .evidence_collect import run as evidence_collect
from .collect_context import run as collect_context
from .code_artifact_collect import run as code_artifact_collect
from .figure_generate import run as figure_generate
from .paper_brief import run as paper_brief
from .outline import run as outline
from .section_write import run as section_write
from .assemble_latex import run as assemble_latex


def build_writing_skill_chain() -> List[Callable[[PaperSkillContext], PaperSkillResult]]:
    """Return the writing agent's skill pipeline in dependency order.

    Compile and review are handled by dedicated agents, not by this skill
    runner. Keep this helper for compatibility with older callers that still
    want the writing-only skill chain.
    """
    return [
        evidence_collect,
        collect_context,
        code_artifact_collect,
        figure_generate,
        paper_brief,
        outline,
        section_write,
        assemble_latex,
    ]


build_default_skill_chain = build_writing_skill_chain


class PaperSkillLeader:
    def __init__(self, paper_id: str, log_func: Callable[[str], None]) -> None:
        self.paper_id = paper_id
        self.log = log_func

    def run(self, ctx: PaperSkillContext, skills: List[Callable[[PaperSkillContext], PaperSkillResult]]) -> None:
        for skill in skills:
            self.log(f"Running skill: {skill.__name__}")
            start = time.time()
            result = skill(ctx)
            elapsed = time.time() - start
            if result.summary:
                self.log(f"{result.name}: {result.summary} ({elapsed:.1f}s)")
            else:
                self.log(f"{result.name}: completed ({elapsed:.1f}s)")
            if result.artifacts:
                add_log(self.paper_id, f"Artifacts: {', '.join(result.artifacts)}")
            if result.data:
                for k, v in result.data.items():
                    ctx.update(k, v)
