from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List

from app.modules.paper.skills.base import PaperSkillContext, PaperSkillResult
from app.modules.paper.skills.latex_compile_support import compile_latex_once
from app.modules.paper.skills.review_feedback import get_latex_compile_feedback
from app.modules.paper.skills.utils import write_artifact
from .base import PaperAgent
from .writing import PaperWritingAgent


STEP_ID = "09_latex_compile_agent"
MAX_REVIEW_ITERATIONS = 4


class LatexCompileAgent(PaperAgent):
    name = "latex_compile_agent"

    @staticmethod
    def _section_targets(review: Dict[str, Any]) -> List[str]:
        paths: List[str] = []
        for key in ("issues", "targets"):
            for item in review.get(key, []) if isinstance(review, dict) else []:
                if not isinstance(item, dict):
                    continue
                path = str(item.get("path") or "").replace("\\", "/").lstrip("/")
                if path.startswith("sections/") and path.endswith(".tex") and path not in paths:
                    paths.append(path)
        return paths

    @staticmethod
    def _undefined_commands(errors: str) -> List[str]:
        commands: List[str] = []
        for match in re.finditer(r"Undefined control sequence\.[\s\S]{0,300}?\\([A-Za-z]+)", errors or ""):
            command = "\\" + match.group(1)
            if command not in commands:
                commands.append(command)
        for match in re.finditer(r"`(\\[A-Za-z]+)`", errors or ""):
            command = match.group(1)
            if command not in commands:
                commands.append(command)
        return commands

    @classmethod
    def _augment_review_targets(cls, ctx: PaperSkillContext, review: Dict[str, Any]) -> Dict[str, Any]:
        if cls._section_targets(review):
            return review

        commands = cls._undefined_commands(str(ctx.get("compile_errors") or ""))
        if not commands:
            return review

        targets = list(review.get("targets", [])) if isinstance(review.get("targets"), list) else []
        issues = list(review.get("issues", [])) if isinstance(review.get("issues"), list) else []
        sections_dir = os.path.join(ctx.latex_dir, "sections")
        try:
            names = sorted(name for name in os.listdir(sections_dir) if name.endswith(".tex"))
        except OSError:
            names = []

        for name in names:
            path = f"sections/{name}"
            try:
                with open(os.path.join(sections_dir, name), "r", encoding="utf-8") as handle:
                    content = handle.read()
            except OSError:
                continue
            if not any(command in content for command in commands):
                continue
            command_list = ", ".join(commands)
            issues.append({
                "severity": "blocking",
                "path": path,
                "message": f"LaTeX compile failed with undefined command(s) {command_list} found in this section.",
            })
            targets.append({
                "path": path,
                "instruction": (
                    f"Fix the undefined LaTeX command(s) {command_list} in this section. "
                    "If they are prose mentions, convert them to safe text; if they are malformed LaTeX, rewrite the local syntax so it compiles."
                ),
            })
            break

        if targets or issues:
            review = dict(review)
            review["issues"] = issues
            review["targets"] = targets
        return review

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
            start = time.time()
            review = get_latex_compile_feedback(ctx, iteration)
            elapsed = time.time() - start
            review = self._augment_review_targets(ctx, review)
            issue_count = len(review.get("issues", [])) if isinstance(review.get("issues"), list) else 0
            self.log(f"{self.name}/compile_feedback: round {iteration}; {issue_count} issue(s) ({elapsed:.1f}s)")
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
