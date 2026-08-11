from __future__ import annotations

from typing import Any, Dict, List

from app.modules.paper.skills.base import PaperSkillContext, PaperSkillResult
from app.modules.paper.skills.latex_compile_support import compile_latex_once
from app.modules.paper.skills.review_feedback import get_latex_compile_feedback
from app.modules.paper.skills.utils import write_artifact
from .base import PaperAgent
from .writing import PaperWritingAgent


STEP_ID = "09_latex_compile_agent"
MAX_REVIEW_ITERATIONS = 2


class LatexCompileAgent(PaperAgent):
    name = "latex_compile_agent"

    def run(
        self,
        ctx: PaperSkillContext,
        step_id: str = STEP_ID,
        writing_agent: PaperWritingAgent | None = None,
    ) -> PaperSkillResult:
        compile_reviews: List[Dict[str, Any]] = []
        writing_rewrites: List[Dict[str, Any]] = []

        self._run_skill(ctx, "compile_latex_once", compile_latex_once)

        for iteration in range(1, MAX_REVIEW_ITERATIONS + 1):
            if ctx.get("compile_status") == "latexmk":
                break

            self.log(f"{self.name}: requesting feedback round {iteration}")
            review = get_latex_compile_feedback(ctx, iteration)
            compile_reviews.append(review)
            round_writing_rewrites: List[Dict[str, Any]] = []
            if writing_agent:
                rewrite_result = writing_agent.apply_feedback(ctx, "latex_compile", [review])
                round_writing_rewrites = rewrite_result.data.get("latex_compile_writing_rewrites", [])
                writing_rewrites.extend(round_writing_rewrites)
            if not round_writing_rewrites:
                break

            self._run_skill(ctx, "compile_latex_once", compile_latex_once)

        summary_lines = [
            "# LaTeX Compile Agent",
            f"reviews: {len(compile_reviews)}",
            f"writing_rewrites: {len(writing_rewrites)}",
            f"compile_status: {ctx.get('compile_status')}",
        ]
        artifacts = write_artifact(
            ctx.paper_id,
            step_id,
            {
                "reviews": compile_reviews,
                "writingRewrites": writing_rewrites,
                "compileStatus": ctx.get("compile_status"),
                "compileErrors": ctx.get("compile_errors"),
            },
            summary_lines,
        )
        result = PaperSkillResult(
            name=self.name,
            summary=f"{len(writing_rewrites)} writing rewrites; compile={ctx.get('compile_status')}",
            artifacts=artifacts,
            data={
                "compile_reviews": compile_reviews,
                "compile_writing_rewrites": writing_rewrites,
                "compile_status": ctx.get("compile_status"),
                "compile_errors": ctx.get("compile_errors"),
                "pdf_available": ctx.get("pdf_available", False),
            },
        )
        self._apply_result_data(ctx, result)
        return result
