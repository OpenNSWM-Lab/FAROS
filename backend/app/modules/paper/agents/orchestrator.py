from __future__ import annotations

from typing import Callable

from app.modules.paper.skills.base import PaperSkillContext
from .latex_compile import LatexCompileAgent
from .simple_review import SimpleReviewAgent
from .writing import PaperWritingAgent


class PaperAgentOrchestrator:
    def __init__(self, paper_id: str, log_func: Callable[[str], None]) -> None:
        self.paper_id = paper_id
        self.log = log_func
        self.writing_agent = PaperWritingAgent(paper_id, log_func)
        self.latex_compile_agent = LatexCompileAgent(paper_id, log_func)
        self.simple_review_agent = SimpleReviewAgent(paper_id, log_func)

    def run(self, ctx: PaperSkillContext) -> None:
        self.log("Paper agent orchestrator: starting writing agent")
        self.writing_agent.run(ctx)
        self.log("Paper agent orchestrator: starting LaTeX compile agent")
        self.latex_compile_agent.run(ctx, writing_agent=self.writing_agent)
        if ctx.get("compile_status") != "latexmk":
            self.log("Paper agent orchestrator: skipping simple review because LaTeX compile did not pass")
            return
        self.log("Paper agent orchestrator: starting simple review agent")
        self.simple_review_agent.run(ctx, writing_agent=self.writing_agent)
