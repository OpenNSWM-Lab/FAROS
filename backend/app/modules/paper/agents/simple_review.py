from __future__ import annotations

import os
import time
from typing import Any, Dict, List

from app.modules.paper.skills.base import PaperSkillContext, PaperSkillResult
from app.modules.paper.skills.review_feedback import get_simple_review_feedback
from app.modules.paper.skills.utils import write_artifact
from .base import PaperAgent
from .latex_compile import LatexCompileAgent
from .writing import PaperWritingAgent


STEP_ID = "10_simple_review_loop"
MAX_REVIEW_ITERATIONS = 4


class SimpleReviewAgent(PaperAgent):
    name = "simple_review_agent"

    def _write_review_artifact(
        self,
        ctx: PaperSkillContext,
        review: Dict[str, Any],
        review_round: int,
        summary_lines: List[str],
        writing_rewrites: List[Dict[str, Any]] | None = None,
        compile_repair_results: List[Dict[str, Any]] | None = None,
    ) -> List[str]:
        review_source = str(review.get("source") or "simple_review")
        artifact_name = "evidence_usage.json" if review_source == "evidence_usage" else "review.json"
        return write_artifact(
            ctx.paper_id,
            STEP_ID,
            {
                "round": review_round,
                "source": review_source,
                "review": review,
                "reviews": [review],
                "passed": review.get("passed"),
                "issues": review.get("issues", []),
                "targets": review.get("targets", []),
                "writingRewrites": writing_rewrites or [],
                "compileRepairResults": compile_repair_results or [],
                "compileStatus": ctx.get("compile_status"),
                "compileErrors": ctx.get("compile_errors"),
            },
            summary_lines,
            artifact_path=f"artifacts/feedback/round_{max(1, review_round):02d}/{artifact_name}",
        )

    @staticmethod
    def _passed(review: Dict[str, Any]) -> bool:
        return not any(
            str(issue.get("severity", "")).lower() in {"blocking", "major"}
            for issue in review.get("issues", [])
            if isinstance(issue, dict)
        )

    @staticmethod
    def _read_sections(ctx: PaperSkillContext) -> Dict[str, str]:
        sections_dir = os.path.join(ctx.latex_dir, "sections")
        sections: Dict[str, str] = {}
        try:
            names = sorted(name for name in os.listdir(sections_dir) if name.endswith(".tex"))
        except OSError:
            return sections
        for name in names:
            path = os.path.join(sections_dir, name)
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    sections[os.path.splitext(name)[0]] = handle.read()
            except OSError:
                continue
        return sections

    @staticmethod
    def _all_section_text(sections_content: Dict[str, str]) -> str:
        return "\n".join(sections_content.values()).lower()

    @staticmethod
    def _outline_section_titles(ctx: PaperSkillContext) -> Dict[str, str]:
        outline = ctx.get("outline") or ctx.paper.get("outlineJson") or {}
        sections = outline.get("sections", []) if isinstance(outline, dict) else []
        titles: Dict[str, str] = {}
        for section in sections:
            if not isinstance(section, dict):
                continue
            section_id = str(section.get("id") or "").strip()
            if section_id:
                titles[section_id] = str(section.get("title") or section_id)
        return titles

    @classmethod
    def _select_existing_section_path(
        cls,
        ctx: PaperSkillContext,
        sections_content: Dict[str, str],
        preferred_terms: List[str],
    ) -> str:
        if not sections_content:
            return "sections"
        titles = cls._outline_section_titles(ctx)
        for section_id in sections_content:
            haystack = f"{section_id} {titles.get(section_id, '')}".lower()
            if any(term in haystack for term in preferred_terms):
                return f"sections/{section_id}.tex"
        first_section_id = next(iter(sections_content))
        return f"sections/{first_section_id}.tex"

    @staticmethod
    def _idea_reference_entries(ctx: PaperSkillContext) -> List[Dict[str, Any]]:
        evidence = ctx.get("plan_evidence") or ctx.paper.get("evidenceJson") or {}
        if not isinstance(evidence, dict):
            return []
        literature = evidence.get("literature") if isinstance(evidence.get("literature"), dict) else {}
        key_papers = literature.get("keyPapers") if isinstance(literature.get("keyPapers"), list) else []
        return [paper for paper in key_papers if isinstance(paper, dict)]

    @staticmethod
    def _outline_reference_candidates(ctx: PaperSkillContext, reference: Dict[str, Any]) -> List[str]:
        outline = ctx.get("outline") or ctx.paper.get("outlineJson") or {}
        refs = outline.get("references", []) if isinstance(outline, dict) else []
        candidates: List[str] = []
        reference_title = str(reference.get("title") or "").strip().lower()
        reference_ids = {
            str(reference.get("paperId") or "").strip().lower(),
            str(reference.get("structuredPaperId") or "").strip().lower(),
        }
        for item in refs:
            if not isinstance(item, dict):
                continue
            item_title = str(item.get("title") or "").strip().lower()
            item_ids = {
                str(item.get("paperId") or "").strip().lower(),
                str(item.get("structuredPaperId") or "").strip().lower(),
            }
            if reference_title and item_title == reference_title or any(value and value in item_ids for value in reference_ids):
                candidates.extend([
                    str(item.get("key") or ""),
                    str(item.get("title") or ""),
                    str(item.get("paperId") or ""),
                    str(item.get("structuredPaperId") or ""),
                ])
        return candidates

    @classmethod
    def _reference_used(cls, ctx: PaperSkillContext, reference: Dict[str, Any], sections_text: str) -> bool:
        candidates = [
            reference.get("paperId"),
            reference.get("structuredPaperId"),
            reference.get("title"),
        ]
        candidates.extend(cls._outline_reference_candidates(ctx, reference))
        for candidate in candidates:
            value = str(candidate or "").strip().lower()
            if value and value in sections_text:
                return True
        return False

    @staticmethod
    def _artifact_used(artifact: Dict[str, Any], sections_text: str) -> bool:
        candidates = [
            artifact.get("label"),
            artifact.get("path"),
            artifact.get("name"),
            artifact.get("title"),
            artifact.get("tableId"),
            artifact.get("figureId"),
        ]
        for candidate in candidates:
            value = str(candidate or "").strip().lower()
            if value and value in sections_text:
                return True
        return False

    def _evidence_usage_review(self, ctx: PaperSkillContext) -> Dict[str, Any]:
        sections_content = self._read_sections(ctx)
        sections_text = self._all_section_text(sections_content)
        code_figures = ctx.get("code_figure_entries", []) or []
        code_tables = ctx.get("code_table_entries", []) or []
        idea_refs = self._idea_reference_entries(ctx)

        issues: List[Dict[str, Any]] = []
        targets: List[Dict[str, Any]] = []

        unused_figures = [
            artifact for artifact in code_figures
            if isinstance(artifact, dict) and not self._artifact_used(artifact, sections_text)
        ]
        unused_tables = [
            artifact for artifact in code_tables
            if isinstance(artifact, dict) and not self._artifact_used(artifact, sections_text)
        ]
        uncited_refs = [
            reference for reference in idea_refs
            if isinstance(reference, dict) and not self._reference_used(ctx, reference, sections_text)
        ]
        code_target_path = self._select_existing_section_path(
            ctx,
            sections_content,
            ["experiment", "result", "analysis", "evaluation", "method", "模型", "实验", "结果", "分析", "方法"],
        )
        reference_target_path = self._select_existing_section_path(
            ctx,
            sections_content,
            ["related", "introduction", "background", "literature", "prior", "引言", "相关", "背景", "综述"],
        )

        for artifact in unused_figures[:5]:
            label = artifact.get("label") or artifact.get("path") or artifact.get("title") or artifact.get("figureId")
            issues.append({
                "severity": "minor",
                "path": code_target_path,
                "message": f"Code-produced figure is available but not used in the manuscript: {label}",
            })
        for artifact in unused_tables[:5]:
            label = artifact.get("path") or artifact.get("name") or artifact.get("tableId")
            issues.append({
                "severity": "minor",
                "path": code_target_path,
                "message": f"Code-produced table is available but not used in the manuscript: {label}",
            })
        for reference in uncited_refs[:8]:
            title = reference.get("title") or reference.get("paperId") or reference.get("structuredPaperId")
            issues.append({
                "severity": "minor",
                "path": reference_target_path,
                "message": f"Idea-linked paper is not cited or discussed in the manuscript: {title}",
            })

        if unused_figures or unused_tables:
            targets.append({
                "path": code_target_path,
                "instruction": (
                    "Revise the experiments or analysis section to use the available code-produced figures/tables "
                    "when they support a claim. If an artifact is irrelevant, explicitly explain why it is omitted."
                ),
            })
        if uncited_refs:
            targets.append({
                "path": reference_target_path,
                "instruction": (
                    "Revise related work or introduction to cite and discuss the idea-linked papers that are relevant "
                    "to the manuscript's research question. If a linked paper is irrelevant, state the exclusion reason."
                ),
            })

        return {
            "source": "evidence_usage",
            "passed": True,
            "issues": issues,
            "targets": targets,
            "unusedCodeFigures": unused_figures,
            "unusedCodeTables": unused_tables,
            "uncitedIdeaPapers": uncited_refs,
        }

    def run(self, ctx: PaperSkillContext, writing_agent: PaperWritingAgent | None = None) -> PaperSkillResult:
        simple_reviews: List[Dict[str, Any]] = []
        writing_rewrites: List[Dict[str, Any]] = []
        compile_repair_results: List[Dict[str, Any]] = []
        evidence_usage_review = self._evidence_usage_review(ctx)
        artifacts: List[str] = []
        if evidence_usage_review.get("issues"):
            simple_reviews.append(evidence_usage_review)
            artifacts.extend(self._write_review_artifact(
                ctx,
                evidence_usage_review,
                1,
                [
                    "# Simple Review Agent",
                    "source: evidence_usage",
                    f"issues: {len(evidence_usage_review.get('issues', []))}",
                ],
            ))
        passed = True

        for iteration in range(1, MAX_REVIEW_ITERATIONS + 1):
            self.log(f"{self.name}: requesting feedback round {iteration}")
            start = time.time()
            review = get_simple_review_feedback(ctx, iteration)
            elapsed = time.time() - start
            issue_count = len(review.get("issues", [])) if isinstance(review.get("issues"), list) else 0
            self.log(f"{self.name}/review_feedback: round {iteration}; {issue_count} issue(s) ({elapsed:.1f}s)")
            simple_reviews.append(review)
            artifacts.extend(self._write_review_artifact(
                ctx,
                review,
                iteration,
                [
                    "# Simple Review Agent",
                    f"round: {iteration}",
                    f"issues: {issue_count}",
                ],
            ))
            passed = self._passed(review)
            if passed:
                break

            round_writing_rewrites: List[Dict[str, Any]] = []
            if writing_agent:
                try:
                    rewrite_result = writing_agent.apply_feedback(ctx, "simple_review", [review], feedback_round=iteration)
                except TypeError:
                    rewrite_result = writing_agent.apply_feedback(ctx, "simple_review", [review])
                round_writing_rewrites = rewrite_result.data.get("simple_review_writing_rewrites", [])
                writing_rewrites.extend(round_writing_rewrites)
            if not round_writing_rewrites:
                break

            compile_agent = LatexCompileAgent(self.paper_id, self.log)
            try:
                repair = compile_agent.run(
                    ctx,
                    step_id="10_simple_review_compile_agent",
                    writing_agent=writing_agent,
                    feedback_round=iteration + 1,
                )
            except TypeError:
                repair = compile_agent.run(
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
            if ctx.get("compile_status") != "latexmk":
                break

        summary_lines = [
            "# Simple Review Agent",
            f"reviews: {len(simple_reviews)}",
            f"writing_rewrites: {len(writing_rewrites)}",
            f"compile_repair_runs: {len(compile_repair_results)}",
            f"passed: {passed}",
            f"compile_status: {ctx.get('compile_status')}",
        ]
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
