from __future__ import annotations

from typing import Any, Dict, List

from app.modules.paper.skills.base import PaperSkillContext, PaperSkillResult
from app.modules.paper.skills.latex_compile_support import compile_latex_once
from app.modules.paper.skills.review_feedback import get_simple_review_feedback
from app.modules.paper.skills.utils import write_artifact
from .base import PaperAgent
from .latex_compile import LatexCompileAgent
from .writing import PaperWritingAgent


STEP_ID = "10_simple_review_loop"
MAX_REVIEW_ITERATIONS = 2


class SimpleReviewAgent(PaperAgent):
    name = "simple_review_agent"

    @staticmethod
    def _passed(review: Dict[str, Any]) -> bool:
        return bool(review.get("passed")) and not any(
            str(issue.get("severity", "")).lower() in {"blocking", "major"}
            for issue in review.get("issues", [])
            if isinstance(issue, dict)
        )

    def run(self, ctx: PaperSkillContext, writing_agent: PaperWritingAgent | None = None) -> PaperSkillResult:
        simple_reviews: List[Dict[str, Any]] = []
        writing_rewrites: List[Dict[str, Any]] = []
        compile_repair_results: List[Dict[str, Any]] = []
        passed = False

        for iteration in range(1, MAX_REVIEW_ITERATIONS + 1):
            self.log(f"{self.name}: requesting feedback round {iteration}")
            review = get_simple_review_feedback(ctx, iteration)
            simple_reviews.append(review)
            passed = self._passed(review)
            if passed:
                break

            round_writing_rewrites: List[Dict[str, Any]] = []
            if writing_agent:
                rewrite_result = writing_agent.apply_feedback(ctx, "simple_review", [review])
                round_writing_rewrites = rewrite_result.data.get("simple_review_writing_rewrites", [])
                writing_rewrites.extend(round_writing_rewrites)
            if not round_writing_rewrites:
                break

            self._run_skill(ctx, "compile_latex_once", compile_latex_once)
            if ctx.get("compile_status") != "latexmk":
                repair = LatexCompileAgent(self.paper_id, self.log).run(
                    ctx,
                    step_id="10_simple_review_compile_agent",
                    writing_agent=writing_agent,
                )
                compile_repair_results.append(
                    {
                        "summary": repair.summary,
                        "artifacts": repair.artifacts,
                        "compileStatus": ctx.get("compile_status"),
                    }
                )

        summary_lines = [
            "# Simple Review Agent",
            f"reviews: {len(simple_reviews)}",
            f"writing_rewrites: {len(writing_rewrites)}",
            f"compile_repair_runs: {len(compile_repair_results)}",
            f"passed: {passed}",
            f"compile_status: {ctx.get('compile_status')}",
        ]
        artifacts = write_artifact(
            ctx.paper_id,
            STEP_ID,
            {
                "reviews": simple_reviews,
                "writingRewrites": writing_rewrites,
                "compileRepairResults": compile_repair_results,
                "passed": passed,
                "compileStatus": ctx.get("compile_status"),
                "compileErrors": ctx.get("compile_errors"),
            },
            summary_lines,
        )
        result = PaperSkillResult(
            name=self.name,
            summary=f"{'passed' if passed else 'issues remain'}; {len(writing_rewrites)} writing rewrites",
            artifacts=artifacts,
            data={
                "simple_review_passed": passed,
                "simple_reviews": simple_reviews,
                "simple_review_writing_rewrites": writing_rewrites,
                "simple_review_compile_repairs": compile_repair_results,
                "compile_status": ctx.get("compile_status"),
                "compile_errors": ctx.get("compile_errors"),
                "pdf_available": ctx.get("pdf_available", False),
            },
        )
        self._apply_result_data(ctx, result)
        return result
