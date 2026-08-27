from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List

from app.modules.paper.skills.base import PaperSkillContext, PaperSkillResult
from app.modules.paper.skills.review_feedback import get_simple_review_feedback
from app.modules.paper.skills.utils import write_artifact
from app.modules.paper.storage import read_paper_file, write_paper_file
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
    def _apply_final_scope_deterministic_repairs(
        ctx: PaperSkillContext,
        review: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        repairs: List[Dict[str, Any]] = []
        for issue in review.get("issues", []):
            if not isinstance(issue, dict):
                continue
            message = str(issue.get("message") or "")
            path = str(issue.get("path") or "").replace("\\", "/")
            if "statistical-significance language" not in message or not re.fullmatch(
                r"sections/[A-Za-z0-9_.-]+\.tex", path
            ):
                continue
            content = read_paper_file(ctx.paper_id, path)
            if content is None:
                continue
            normalized = PaperWritingAgent._neutralize_unsupported_significance(content)
            if normalized == content:
                continue
            write_paper_file(ctx.paper_id, path, normalized)
            repairs.append({
                "sectionId": os.path.basename(path)[:-4],
                "path": path,
                "feedback": [message],
                "artifacts": [],
                "warnings": ["Removed unsupported statistical-significance wording deterministically."],
                "deterministic": True,
            })
        return repairs

    @staticmethod
    def _filter_source_inconsistent_issues(
        review: Dict[str, Any], sections: Dict[str, str], main_tex: str = "",
        compile_status: str = "",
    ) -> Dict[str, Any]:
        issues = review.get("issues", []) if isinstance(review.get("issues"), list) else []
        retained: List[Dict[str, Any]] = []
        dropped_paths: set[str] = set()
        all_text = "\n".join(sections.values())
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            message = str(issue.get("message") or "")
            issue_path = str(issue.get("path") or "").replace("\\", "/").lower()
            if (
                compile_status == "latexmk"
                and re.search(r"undefined control sequence|compil(?:e|ation) error", message, re.IGNORECASE)
            ):
                dropped_paths.add(str(issue.get("path") or ""))
                continue
            if (
                "abstract" in message.lower()
                and (
                    issue_path.endswith("sections/abstract.tex")
                    or "sections/abstract" in message.lower()
                )
                and not re.search(
                    r"\\(?:input|include)\s*\{\s*sections/abstract(?:\.tex)?\s*\}",
                    main_tex,
                    re.IGNORECASE,
                )
            ):
                dropped_paths.add(str(issue.get("path") or ""))
                continue
            if "duplicat" in message.lower() and "label" in message.lower():
                match = re.search(
                    r"(?:label\s*)?[`'\"]([^`'\"]+)[`'\"]",
                    message,
                    re.IGNORECASE,
                )
                if match and all_text.count(rf"\label{{{match.group(1)}}}") < 2:
                    dropped_paths.add(str(issue.get("path") or ""))
                    continue
            retained.append(issue)
        if len(retained) == len(issues):
            return review
        filtered = dict(review)
        filtered["issues"] = retained
        filtered["targets"] = [
            target
            for target in review.get("targets", [])
            if isinstance(target, dict)
            and str(target.get("path") or "") not in dropped_paths
        ]
        filtered["passed"] = not any(
            str(issue.get("severity") or "").lower() in {"blocking", "major"}
            for issue in retained
        )
        return filtered

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
    def _read_main_tex(ctx: PaperSkillContext) -> str:
        try:
            with open(
                os.path.join(ctx.latex_dir, "main.tex"),
                "r",
                encoding="utf-8",
                errors="replace",
            ) as handle:
                return handle.read()
        except OSError:
            return ""

    @staticmethod
    def _all_section_text(sections_content: Dict[str, str]) -> str:
        return "\n".join(sections_content.values()).lower()

    @staticmethod
    def _contains_positive_scope_claim(content: str, patterns: tuple[str, ...]) -> bool:
        """Return true when a forbidden validation phrase is asserted positively."""
        negation = re.compile(
            r"\b(?:no|not|never|without|cannot|can't|do not|does not|did not|"
            r"is not|are not|was not|were not|lack|lacks|lacked|lacking|"
            r"absence|absent|neither|nor|"
            r"rather than|instead of|as opposed to|"
            r"refrain(?:s|ed|ing)? from|avoid(?:s|ed|ing)?|"
            r"do not claim|does not claim|did not claim)\b",
            re.IGNORECASE,
        )
        future_scope = re.compile(
            r"\b(?:future work|future research|future evaluation|will need|should|must|"
            r"needs? to|remains? to|required to|can be|could be|may be|would be|pending)\b",
            re.IGNORECASE,
        )
        prior_work_attribution = re.compile(
            r"(?:\\cite(?:p|t)?\{[^}]+\}|"
            r"\b(?:prior|previous|existing|earlier|recent|other) (?:work|studies|methods|"
            r"frameworks|benchmarks|approaches)|"
            r"\b(?:many|some|these|prior|previous|existing|recent)\b[^.!?]{0,80}"
            r"\b(?:studies|methods|frameworks|approaches|benchmarks|work)\b)",
            re.IGNORECASE,
        )
        current_work = re.compile(
            r"\b(?:we|our|this (?:paper|work|study|experiment|evaluation)|"
            r"the (?:present|current) (?:study|experiment|evaluation))\b",
            re.IGNORECASE,
        )
        for pattern in patterns:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                sentence_start = max(
                    content.rfind(".", 0, match.start()),
                    content.rfind("!", 0, match.start()),
                    content.rfind("?", 0, match.start()),
                    content.rfind("\n", 0, match.start()),
                ) + 1
                sentence_end_candidates = [
                    position
                    for marker in (".", "!", "?", "\n")
                    if (position := content.find(marker, match.end())) >= 0
                ]
                sentence_end = min(sentence_end_candidates) if sentence_end_candidates else len(content)
                prefix = content[sentence_start:match.start()][-180:]
                sentence = content[sentence_start:sentence_end]
                if negation.search(prefix) or future_scope.search(sentence):
                    continue
                if prior_work_attribution.search(sentence) and not current_work.search(sentence):
                    continue
                return True
        return False

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
    def _executed_metrics(ctx: PaperSkillContext) -> Dict[str, Dict[str, float]]:
        evidence = ctx.paper.get("evidenceJson") or {}
        code_evidence = evidence.get("codeEvidence") if isinstance(evidence, dict) else {}
        runs = code_evidence.get("runs") if isinstance(code_evidence, dict) else []
        result: Dict[str, Dict[str, float]] = {}
        for run in runs if isinstance(runs, list) else []:
            for metric in run.get("metrics", []) if isinstance(run, dict) else []:
                if not isinstance(metric, dict):
                    continue
                name = str(metric.get("name") or "")
                if ":" not in name:
                    continue
                variant, metric_name = name.split(":", 1)
                try:
                    value = float(metric.get("value"))
                except (TypeError, ValueError):
                    continue
                normalized_metric = re.sub(
                    r"[^a-z0-9]+", "_", metric_name.lower()
                ).strip("_")
                result.setdefault(variant.strip().lower(), {})[normalized_metric] = value
        return result

    @staticmethod
    def _matches_observed_rounding(value_text: str, observed: List[float]) -> bool:
        decimals = len(value_text.split(".", 1)[1])
        value = float(value_text)
        # Manuscripts commonly truncate (rather than round) reported metrics.
        # Accept one unit in the final displayed decimal place for either form.
        tolerance = 10 ** -decimals + 1e-10
        return any(abs(value - candidate) <= tolerance for candidate in observed)

    def _metric_consistency_review(
        self, ctx: PaperSkillContext, sections_content: Dict[str, str],
    ) -> Dict[str, Any]:
        metrics = self._executed_metrics(ctx)
        if not metrics:
            return {"source": "metric_consistency", "passed": True, "issues": [], "targets": []}

        observed_values = [value for variant in metrics.values() for value in variant.values()]
        # Absolute deltas are legitimate reported quantities even though they do
        # not appear as standalone rows in metrics.json.
        metrics_by_name: Dict[str, List[float]] = {}
        for variant_metrics in metrics.values():
            for metric_name, value in variant_metrics.items():
                metrics_by_name.setdefault(metric_name, []).append(value)
        for values in metrics_by_name.values():
            observed_values.extend(
                abs(left - right)
                for index, left in enumerate(values)
                for right in values[index + 1:]
            )
        baseline = metrics.get("baseline", {})
        method = metrics.get("method", {})
        ablations = {
            name: values for name, values in metrics.items() if name.startswith("ablation_")
        }
        has_inferential_statistics = any(
            token in metric_name
            for values in metrics.values()
            for metric_name in values
            for token in ("p_value", "confidence_interval", "standard_deviation", "std")
        )
        issues: List[Dict[str, Any]] = []
        targets: List[Dict[str, Any]] = []
        numeric_pattern = re.compile(r"\b0\.\d{3,}\b")
        metric_pattern = re.compile(r"\b(?:auroc|f1(?:-score)?|brier|ece|calibration)\b", re.IGNORECASE)

        for section_id, content in sections_content.items():
            section_messages: List[str] = []
            statements = [
                statement
                for raw_line in content.splitlines()
                for statement in re.split(r"(?<=[.!?])\s+", raw_line)
                if statement.strip()
            ]
            for line in statements:
                is_variant_table_row = (
                    "&" in line
                    and re.search(
                        r"\b(?:baseline|fcs|method|proposed|ablation)\b",
                        line,
                        re.IGNORECASE,
                    )
                )
                if not metric_pattern.search(line) and not is_variant_table_row:
                    continue
                unsupported = sorted({
                    token
                    for token in numeric_pattern.findall(line)
                    if not self._matches_observed_rounding(token, observed_values)
                })
                if unsupported:
                    section_messages.append(
                        "contains metric-like values absent from executed evidence: "
                        + ", ".join(unsupported)
                    )

                lowered = line.lower()
                current_comparison = (
                    any(token in lowered for token in ("fcs", "our method", "proposed method", "method"))
                    and "baseline" in lowered
                )
                self_direction_context = any(
                    token in lowered
                    for token in ("fcs", "our method", "proposed method", "the method", "concurrent")
                )
                for metric_name, lower_is_better in (
                    ("brier_score", True),
                    ("f1_score", False),
                    ("auroc", False),
                ):
                    display_token = "brier" if metric_name == "brier_score" else (
                        "f1" if metric_name == "f1_score" else "auroc"
                    )
                    direction_verbs = (
                        r"(?:improv\w*|outperform\w*|superior|reduc\w*)"
                        if lower_is_better
                        else r"(?:improv\w*|outperform\w*|superior)"
                    )
                    direction_near_metric = re.search(
                        rf"{direction_verbs}[^.!?]{{0,20}}\b{display_token}\b|"
                        rf"\b{display_token}\b[^.!?]{{0,20}}{direction_verbs}",
                        lowered,
                    )
                    if (
                        (current_comparison or self_direction_context)
                        and "ablat" not in lowered
                        and display_token in lowered
                        and direction_near_metric
                        and metric_name in baseline
                        and metric_name in method
                    ):
                        improved = (
                            method[metric_name] < baseline[metric_name]
                            if lower_is_better
                            else method[metric_name] > baseline[metric_name]
                        )
                        if not improved:
                            section_messages.append(
                                f"claims {display_token.upper()} improvement although executed method="
                                f"{method[metric_name]:.6g} and baseline={baseline[metric_name]:.6g}"
                            )

                if (
                    not has_inferential_statistics
                    and re.search(
                        r"(?:\bsignificantly\s+(?:improv|reduc|lower|higher|better|worse|outperform)|"
                        r"\bstatistically significant\b|"
                        r"\b(?:improvement|reduction|difference|gain)\b[^.!?]{0,60}\bsignificant\b)",
                        lowered,
                    )
                    and metric_pattern.search(lowered)
                ):
                    section_messages.append(
                        "uses statistical-significance language without repeated-run inferential statistics"
                    )

                if ablations and re.search(
                    r"(?:ablat|remov|without|w/o).*(?:degrad(?:e|ation)|worse|improv(?:e|ement)).*all metrics",
                    lowered,
                ):
                    section_messages.append(
                        "makes a blanket all-metrics ablation claim; executed ablation directions are mixed"
                    )

            if not section_messages:
                continue
            unique_messages = list(dict.fromkeys(section_messages))
            if section_id == "__main__" and "abstract" in sections_content:
                # main.tex embeds the outline abstract. Route scientific-content
                # feedback through the editable abstract section; the writing
                # agent synchronizes that rewrite back into the template shell.
                path = "sections/abstract.tex"
            else:
                path = "main.tex" if section_id == "__main__" else f"sections/{section_id}.tex"
            issues.append({
                "severity": "blocking",
                "path": path,
                "message": "Metric consistency gate: " + "; ".join(unique_messages[:4]),
            })
            targets.append({
                "path": path,
                "instruction": (
                    "Rewrite every quantitative statement and table from the authoritative executed metrics. "
                    "Lower ECE/Brier is better; higher F1/AUROC is better. Describe mixed directions as "
                    "trade-offs and remove significance language unless inferential statistics are supplied. "
                    f"Authoritative metrics: {metrics}"
                ),
            })

        return {
            "source": "metric_consistency",
            "passed": not issues,
            "issues": issues,
            "targets": targets,
            "observedMetrics": metrics,
        }

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

        metric_sources = dict(sections_content)
        main_tex = self._read_main_tex(ctx)
        if main_tex:
            metric_sources["__main__"] = main_tex
        metric_review = self._metric_consistency_review(ctx, metric_sources)
        issues.extend(metric_review.get("issues", []))
        targets.extend(metric_review.get("targets", []))

        constraints = " ".join(
            str(item) for item in (ctx.paper.get("evidenceConstraints") or [])
        ).lower()
        strict_local_scope = any(
            marker in constraints
            for marker in ("synthetic", "local", "do not claim external", "do not claim real-world", "do not claim human")
        )
        if strict_local_scope:
            forbidden_patterns = (
                r"\bhuman annotat(?:or|ors|ed|ion)",
                r"\bdomain experts?\b",
                r"\bexpert annotat(?:or|ors|ed|ion)",
                r"\breal[- ]world (?:dataset|corpus|validation|evaluation)",
                r"\bexternal validation\b",
                r"\bstatistically significant\b",
            )
            for section_id, content in sections_content.items():
                if not self._contains_positive_scope_claim(content, forbidden_patterns):
                    continue
                path = f"sections/{section_id}.tex"
                issues.append({
                    "severity": "blocking",
                    "path": path,
                    "message": (
                        "The manuscript claims human, external, real-world, or statistical validation "
                        "that is forbidden by the linked experiment evidence scope."
                    ),
                })
                targets.append({
                    "path": path,
                    "instruction": (
                        "Rewrite the validation description to match the executed fixed-seed local synthetic "
                        "experiment. Remove human annotator, domain-expert, external dataset, real-world, and "
                        "statistical-significance claims, and state the external-validity limitation explicitly."
                    ),
                })

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
            "passed": not any(
                str(issue.get("severity") or "").lower() in {"blocking", "major"}
                for issue in issues
            ),
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
            evidence_usage_rewrites: List[Dict[str, Any]] = []
            evidence_usage_compile_repairs: List[Dict[str, Any]] = []
            if evidence_usage_review.get("targets") and writing_agent:
                try:
                    rewrite_result = writing_agent.apply_feedback(
                        ctx,
                        "evidence_usage",
                        [evidence_usage_review],
                        feedback_round=1,
                    )
                except TypeError:
                    rewrite_result = writing_agent.apply_feedback(ctx, "evidence_usage", [evidence_usage_review])
                evidence_usage_rewrites = rewrite_result.data.get("evidence_usage_writing_rewrites", [])
                writing_rewrites.extend(evidence_usage_rewrites)
                if evidence_usage_rewrites:
                    compile_agent = LatexCompileAgent(self.paper_id, self.log)
                    try:
                        repair = compile_agent.run(
                            ctx,
                            step_id="10_simple_review_compile_agent",
                            writing_agent=writing_agent,
                            feedback_round=1,
                        )
                    except TypeError:
                        repair = compile_agent.run(
                            ctx,
                            step_id="10_simple_review_compile_agent",
                            writing_agent=writing_agent,
                        )
                    evidence_usage_compile_repairs.append(
                        {
                            "summary": repair.summary,
                            "artifacts": repair.artifacts,
                            "compileStatus": ctx.get("compile_status"),
                        }
                    )
                    compile_repair_results.extend(evidence_usage_compile_repairs)
            artifacts.extend(self._write_review_artifact(
                ctx,
                evidence_usage_review,
                1,
                [
                    "# Simple Review Agent",
                    "source: evidence_usage",
                    f"issues: {len(evidence_usage_review.get('issues', []))}",
                ],
                writing_rewrites=evidence_usage_rewrites,
                compile_repair_results=evidence_usage_compile_repairs,
            ))

            # A model rewrite can preserve the exact unsupported phrase it was
            # asked to remove. Re-run the deterministic evidence gate once and
            # give targeted feedback a second chance before general peer review.
            post_evidence_review = self._evidence_usage_review(ctx)
            if post_evidence_review.get("passed") is False and writing_agent:
                simple_reviews.append(post_evidence_review)
                second_rewrites: List[Dict[str, Any]] = []
                second_compile_repairs: List[Dict[str, Any]] = []
                if post_evidence_review.get("targets"):
                    try:
                        rewrite_result = writing_agent.apply_feedback(
                            ctx,
                            "evidence_usage",
                            [post_evidence_review],
                            feedback_round=2,
                        )
                    except TypeError:
                        rewrite_result = writing_agent.apply_feedback(
                            ctx, "evidence_usage", [post_evidence_review]
                        )
                    second_rewrites = rewrite_result.data.get(
                        "evidence_usage_writing_rewrites", []
                    )
                    writing_rewrites.extend(second_rewrites)
                    if second_rewrites:
                        compile_agent = LatexCompileAgent(self.paper_id, self.log)
                        repair = compile_agent.run(
                            ctx,
                            step_id="10_simple_review_compile_agent",
                            writing_agent=writing_agent,
                            feedback_round=2,
                        )
                        second_compile_repairs.append({
                            "summary": repair.summary,
                            "artifacts": repair.artifacts,
                            "compileStatus": ctx.get("compile_status"),
                        })
                        compile_repair_results.extend(second_compile_repairs)
                artifacts.extend(self._write_review_artifact(
                    ctx,
                    post_evidence_review,
                    2,
                    [
                        "# Simple Review Agent",
                        "source: evidence_usage_recheck",
                        f"issues: {len(post_evidence_review.get('issues', []))}",
                    ],
                    writing_rewrites=second_rewrites,
                    compile_repair_results=second_compile_repairs,
                ))
        passed = True

        for iteration in range(1, MAX_REVIEW_ITERATIONS + 1):
            self.log(f"{self.name}: requesting feedback round {iteration}")
            start = time.time()
            review = get_simple_review_feedback(ctx, iteration)
            review = self._filter_source_inconsistent_issues(
                review,
                self._read_sections(ctx),
                self._read_main_tex(ctx),
                str(ctx.get("compile_status") or ""),
            )
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

        final_scope_review = self._evidence_usage_review(ctx)
        final_scope_repairs = self._apply_final_scope_deterministic_repairs(
            ctx, final_scope_review
        )
        if final_scope_repairs:
            writing_rewrites.extend(final_scope_repairs)
            compile_agent = LatexCompileAgent(self.paper_id, self.log)
            repair = compile_agent.run(
                ctx,
                step_id="10_simple_review_compile_agent",
                writing_agent=None,
            )
            compile_repair_results.append({
                "summary": repair.summary,
                "artifacts": repair.artifacts,
                "compileStatus": ctx.get("compile_status"),
                "deterministic": True,
            })
            final_scope_review = self._evidence_usage_review(ctx)
        if final_scope_review.get("passed") is False:
            passed = False
            simple_reviews.append(final_scope_review)
            artifacts.extend(self._write_review_artifact(
                ctx,
                final_scope_review,
                MAX_REVIEW_ITERATIONS + 1,
                [
                    "# Simple Review Agent",
                    "source: final_evidence_scope",
                    f"issues: {len(final_scope_review.get('issues', []))}",
                ],
            ))

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
