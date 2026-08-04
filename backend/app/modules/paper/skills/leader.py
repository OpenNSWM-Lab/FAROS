import time
from typing import Callable, List

from app.modules.paper.storage import add_log
from .base import PaperSkillContext, PaperSkillResult
from .collect_context import run as collect_context
from .paper_brief import run as paper_brief
from .outline import run as outline
from .outline_gate import run as outline_gate
from .section_write import run as section_write
from .evidence_gate import run as evidence_gate
from .figure_generate import run as figure_generate
from .assemble_latex import run as assemble_latex
from .compile_pdf import run as compile_pdf
from .qa_audit import run as qa_audit


def build_default_skill_chain() -> List[Callable[[PaperSkillContext], PaperSkillResult]]:
    return [
        collect_context,
        paper_brief,
        outline,
        outline_gate,
        section_write,
        evidence_gate,
        figure_generate,
        assemble_latex,
        compile_pdf,
        qa_audit,
    ]


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
