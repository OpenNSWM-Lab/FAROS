from __future__ import annotations

import os
from collections import OrderedDict
from typing import Any, Dict
from typing import Callable, List

from app.modules.paper.skills.assemble_latex import run as assemble_latex
from app.modules.paper.skills.base import PaperSkillContext, PaperSkillResult
from app.modules.paper.skills.collect_context import run as collect_context
from app.modules.paper.skills.evidence_collect import run as evidence_collect
from app.modules.paper.skills.evidence_gate import run as evidence_gate
from app.modules.paper.skills.figure_generate import run as figure_generate
from app.modules.paper.skills.outline import run as outline
from app.modules.paper.skills.outline_gate import run as outline_gate
from app.modules.paper.skills.paper_brief import run as paper_brief
from app.modules.paper.skills.section_rewrite import rewrite_section
from app.modules.paper.skills.section_write import run as section_write
from app.modules.paper.skills.utils import write_artifact
from .base import PaperAgent


class PaperWritingAgent(PaperAgent):
    name = "writing_agent"

    def skills(self) -> List[Callable[[PaperSkillContext], PaperSkillResult]]:
        return [
            evidence_collect,
            collect_context,
            paper_brief,
            outline,
            outline_gate,
            section_write,
            evidence_gate,
            figure_generate,
            assemble_latex,
        ]

    def run(self, ctx: PaperSkillContext) -> None:
        for skill in self.skills():
            self._run_skill(ctx, skill.__name__, skill)

    @staticmethod
    def _section_id_from_path(path: str) -> str | None:
        normalized = (path or "").replace("\\", "/").lstrip("/")
        if not normalized.startswith("sections/") or not normalized.endswith(".tex"):
            return None
        section_id = os.path.basename(normalized)[:-4]
        return section_id or None

    @classmethod
    def _section_feedback(cls, reviews: List[Dict[str, Any]], source: str) -> "OrderedDict[str, List[str]]":
        feedback: "OrderedDict[str, List[str]]" = OrderedDict()
        for review in reviews:
            for issue in review.get("issues", []) if isinstance(review, dict) else []:
                if not isinstance(issue, dict):
                    continue
                section_id = cls._section_id_from_path(str(issue.get("path") or ""))
                if not section_id:
                    continue
                message = str(issue.get("message") or "").strip()
                severity = str(issue.get("severity") or "issue").strip()
                if message:
                    feedback.setdefault(section_id, []).append(f"[{source}:{severity}] {message}")
            for target in review.get("targets", []) if isinstance(review, dict) else []:
                if not isinstance(target, dict):
                    continue
                section_id = cls._section_id_from_path(str(target.get("path") or ""))
                if not section_id:
                    continue
                instruction = str(target.get("instruction") or "").strip()
                if instruction:
                    feedback.setdefault(section_id, []).append(f"[{source}:target] {instruction}")
        return feedback

    def apply_feedback(
        self,
        ctx: PaperSkillContext,
        source: str,
        reviews: List[Dict[str, Any]],
    ) -> PaperSkillResult:
        section_feedback = self._section_feedback(reviews, source)
        rewrites: List[Dict[str, Any]] = []
        warnings: List[str] = []

        for section_id, messages in section_feedback.items():
            instruction = (
                f"Revise this section only to address {source} feedback. "
                "Focus on the reported local issue; do not change the paper's core idea, evidence chain, or claims beyond what is needed.\n"
                + "\n".join(f"- {message}" for message in messages[:8])
            )
            try:
                result = rewrite_section(
                    ctx,
                    section_id,
                    instruction=instruction,
                    mode="align",
                    preserve_citations=True,
                    preserve_figures=True,
                )
            except Exception as exc:
                warnings.append(f"{section_id}: {str(exc)[:300]}")
                continue
            self._apply_result_data(ctx, result)
            rewrites.append(
                {
                    "sectionId": section_id,
                    "path": result.data.get("path"),
                    "feedback": messages,
                    "artifacts": result.artifacts,
                    "warnings": result.warnings,
                }
            )

        artifacts = write_artifact(
            ctx.paper_id,
            f"feedback_rewrite_{source}",
            {
                "source": source,
                "rewrites": rewrites,
                "warnings": warnings,
            },
            [
                f"# Writing Feedback Rewrite: {source}",
                f"target_sections: {len(section_feedback)}",
                f"rewrites: {len(rewrites)}",
                f"warnings: {len(warnings)}",
            ],
        )
        result = PaperSkillResult(
            name="writing_feedback_rewrite",
            summary=f"{len(rewrites)} targeted section rewrites from {source}",
            artifacts=artifacts,
            warnings=warnings,
            data={
                f"{source}_writing_rewrites": rewrites,
                f"{source}_writing_warnings": warnings,
            },
        )
        self._apply_result_data(ctx, result)
        return result
