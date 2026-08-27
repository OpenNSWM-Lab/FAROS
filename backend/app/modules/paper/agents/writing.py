from __future__ import annotations

import os
import re
from collections import OrderedDict
from typing import Any, Dict
from typing import Callable, List

from app.modules.paper.skills.base import PaperSkillContext, PaperSkillResult
from app.modules.paper.skills.leader import build_writing_skill_chain
from app.modules.paper.skills.section_rewrite import rewrite_section
from app.modules.paper.skills.utils import write_artifact
from app.modules.paper.storage import read_paper_file, write_paper_file
from .base import PaperAgent


class PaperWritingAgent(PaperAgent):
    name = "writing_agent"

    def skills(self) -> List[Callable[[PaperSkillContext], PaperSkillResult]]:
        return build_writing_skill_chain()

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

    @staticmethod
    def _feedback_instruction(source: str, messages: List[str]) -> str:
        if source == "latex_compile":
            header = (
                "Revise this section only to fix the LaTeX compile error(s) below. "
                "Make the smallest source change needed for successful compilation. "
                "Do not mention compile feedback, logs, reviewers, agents, or repair history in the manuscript. "
                "Do not introduce Markdown backticks. If command names must appear as prose, write them as LaTeX-safe text. "
                "Preserve scientific claims and section structure unless a local syntax fix requires a tiny adjustment.\n"
            )
        else:
            header = (
                f"Revise this section only to address {source} feedback. "
                "Focus on the reported local issue; do not change the paper's core idea, evidence chain, or claims beyond what is needed. "
                "Do not mention feedback, reviewers, agents, or revision history in the manuscript. "
                "Do not introduce Markdown backticks.\n"
            )
        return header + "\n".join(f"- {message}" for message in messages[:8])

    @staticmethod
    def _neutralize_unsupported_significance(content: str) -> str:
        content = re.sub(
            r"\bsignificantly\s+(?=(?:improv\w*|reduc\w*|lower\w*|higher\w*|"
            r"better\b|worse\b|outperform\w*))",
            "",
            content,
            flags=re.IGNORECASE,
        )
        return re.sub(
            r"\bstatistically significant(?:ly)?\b",
            "observed",
            content,
            flags=re.IGNORECASE,
        )

    def apply_feedback(
        self,
        ctx: PaperSkillContext,
        source: str,
        reviews: List[Dict[str, Any]],
        feedback_round: int = 1,
    ) -> PaperSkillResult:
        section_feedback = self._section_feedback(reviews, source)
        rewrites: List[Dict[str, Any]] = []
        warnings: List[str] = []

        for section_id, messages in section_feedback.items():
            instruction = self._feedback_instruction(source, messages)
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
            if (
                source == "evidence_usage"
                and any("statistical-significance" in message for message in messages)
            ):
                normalized = self._neutralize_unsupported_significance(
                    result.data.get("content", "")
                )
                if normalized != result.data.get("content", ""):
                    result.data["content"] = normalized
                    write_paper_file(ctx.paper_id, result.data["path"], normalized)
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

            if section_id == "abstract":
                main_tex = read_paper_file(ctx.paper_id, "main.tex")
                if main_tex:
                    abstract_body = re.sub(
                        r"^\s*\\section\*?\{[^}]*\}\s*",
                        "",
                        result.data.get("content", ""),
                        count=1,
                    )
                    abstract_body = re.sub(
                        r"^\s*\\label\{sec:abstract\}\s*", "", abstract_body, count=1,
                    ).strip()
                    synchronized, count = re.subn(
                        r"(\\begin\{abstract\}\s*).*?(\s*\\end\{abstract\})",
                        lambda match: match.group(1) + abstract_body + match.group(2),
                        main_tex,
                        count=1,
                        flags=re.DOTALL,
                    )
                    if count:
                        write_paper_file(ctx.paper_id, "main.tex", synchronized)

        artifact_suffix = {
            "latex_compile": "compile",
            "simple_review": "review",
            "evidence_usage": "evidence_usage",
        }.get(source, source.replace("/", "_").replace("\\", "_"))
        artifacts = write_artifact(
            ctx.paper_id,
            f"feedback_rewrite_{source}",
            {
                "round": feedback_round,
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
            artifact_path=f"artifacts/feedback/round_{max(1, feedback_round):02d}/rewrite_{artifact_suffix}.json",
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
