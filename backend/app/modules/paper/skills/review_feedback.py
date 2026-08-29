from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from app.llm.provider_client import ChatMessage
from app.modules.paper.storage import read_paper_file
from .base import PaperSkillContext
from .utils import _extract_json


MAX_FILE_CHARS = 14000
TEXT_FILE_EXTENSIONS = {".tex", ".bib", ".bst", ".cls", ".sty", ".md"}


def iter_reviewable_files(ctx: PaperSkillContext) -> List[str]:
    files: List[str] = []
    for root, _dirs, names in os.walk(ctx.latex_dir):
        if "artifacts" in os.path.relpath(root, ctx.latex_dir).split(os.sep):
            continue
        for name in names:
            ext = os.path.splitext(name)[1].lower()
            if ext not in TEXT_FILE_EXTENSIONS:
                continue
            rel_path = os.path.relpath(os.path.join(root, name), ctx.latex_dir)
            files.append(rel_path)
    return sorted(files)


def read_reviewable_file(ctx: PaperSkillContext, path: str) -> Optional[str]:
    if path.startswith("/") or ".." in path.split("/"):
        return None
    return read_paper_file(ctx.paper_id, path)


def source_bundle(ctx: PaperSkillContext, max_files: int = 24) -> str:
    chunks: List[str] = []
    for rel_path in iter_reviewable_files(ctx)[:max_files]:
        content = read_reviewable_file(ctx, rel_path)
        if content is None:
            continue
        truncated = content[:MAX_FILE_CHARS]
        if len(content) > MAX_FILE_CHARS:
            truncated += "\n% ... truncated for review agent ...\n"
        chunks.append(f"### FILE: {rel_path}\n```latex\n{truncated}\n```")
    return "\n\n".join(chunks)


def brief_usage_bundle(ctx: PaperSkillContext) -> str:
    brief = ctx.paper.get("briefJson") or ctx.get("paper_brief") or {}
    if not isinstance(brief, dict):
        return "N/A"
    focused = {
        "research_question": brief.get("research_question"),
        "core_claim": brief.get("core_claim"),
        "contributions": brief.get("contributions", []),
        "must_use_evidence": brief.get("must_use_evidence", []),
        "must_use_figures": brief.get("must_use_figures", []),
        "section_priorities": brief.get("section_priorities", {}),
        "avoid_claims": brief.get("avoid_claims", []),
    }
    return json.dumps(focused, ensure_ascii=False, indent=2)[:5000]


def _review_json(ctx: PaperSkillContext, prompt: str, max_tokens: int = 6000) -> Dict[str, Any]:
    response = ctx.client.chat(
        messages=[ChatMessage(role="user", content=prompt)],
        model=ctx.model,
        temperature=0.15,
        max_tokens=max_tokens,
        timeout=ctx.llm_timeout(),
        structured_output=True,
    )
    parsed = _extract_json(response.text)
    if not isinstance(parsed, dict):
        return {
            "passed": False,
            "issues": [{"severity": "major", "message": "Review agent did not return valid JSON."}],
            "targets": [],
            "raw": response.text[:2000],
        }
    parsed.setdefault("issues", [])
    parsed.setdefault("targets", [])
    return parsed


def get_latex_compile_feedback(ctx: PaperSkillContext, iteration: int) -> Dict[str, Any]:
    prompt = f"""You are the FAROS LaTeX compile agent.

Your job is narrow: inspect LaTeX build errors and the LaTeX source, then propose concrete source fixes.
Do not review the paper's scientific idea, evidence chain, novelty, or method correctness.
You must return feedback for the writing agent only. Do not include replacement content, manuscript prose, or revision-history wording.

Paper title: {ctx.paper.get("title", "Untitled")}
Venue: {ctx.venue_cfg.get("name", ctx.venue)}
Compile status: {ctx.get("compile_status")}
Compile errors:
{ctx.get("compile_errors") or "N/A"}

LaTeX source bundle:
{source_bundle(ctx)}

Return JSON only with this schema:
{{
  "passed": boolean,
  "issues": [{{"severity": "blocking|major|minor", "path": "file path", "message": "specific compile problem"}}],
  "targets": [{{"path": "file path", "instruction": "local writing instruction for the writing agent"}}]
}}

Do not return replacement file content. You are not allowed to edit.
Prefer paths under sections/*.tex when the issue can be fixed by local paper writing revision.
When the log points to main.tex because a section was included there, identify the underlying sections/*.tex file from the source bundle.
Targets must describe the smallest compile-safe local edit. Tell the writing agent not to quote logs, feedback, command names in Markdown backticks, or agent names in the manuscript.
If no writing-agent revision target is appropriate, return issues with an empty targets array."""
    result = _review_json(ctx, prompt)
    result["iteration"] = iteration
    return result


def get_simple_review_feedback(ctx: PaperSkillContext, iteration: int) -> Dict[str, Any]:
    prompt = f"""You are the FAROS simple review agent.

Review only presentation and submission-readiness issues:
- LaTeX formatting, malformed environments, overlong tables, bad float placement, duplicated labels, missing captions or labels.
- Citation command formatting and reference-list hygiene.
- Figure/table references, numbering consistency, section ordering, obvious template/style violations.
- PDF/build availability and artifact hygiene.
- Whether idea-stage or code-stage artifacts explicitly declared, linked, or available as evidence are visibly used in the manuscript, or whether the manuscript explains why they are not used. This is an evidence-usage check only; do not judge whether the science is correct.

Do not judge the paper's scientific idea, evidence chain, theory, novelty, experimental validity, or whether claims are true.

Paper title: {ctx.paper.get("title", "Untitled")}
Venue: {ctx.venue_cfg.get("name", ctx.venue)}
Compile status: {ctx.get("compile_status")}
PDF available: {ctx.get("pdf_available")}

Writing brief requirements to audit:
{brief_usage_bundle(ctx)}

LaTeX source bundle:
{source_bundle(ctx)}

Return JSON only with this schema:
{{
  "passed": boolean,
  "issues": [{{"severity": "blocking|major|minor", "path": "file path", "message": "format/spec/figure/table issue"}}],
  "targets": [{{"path": "file path", "instruction": "local writing instruction for the writing agent"}}]
}}

Mark passed=true unless there are blocking or major LaTeX/presentation/submission-readiness problems.
Do not return replacement file content. You are not allowed to edit.
Prefer paths under sections/*.tex when the issue can be fixed by local paper writing revision.
Do not require a minimum number of figures, tables, equations, or references.
If a declared, linked, or available idea/code artifact is absent from the manuscript, return only a minor evidence-usage suggestion and target the most relevant section with an instruction to incorporate that artifact locally or explain why it is omitted. Missing declared/available artifacts are not blocking issues and must not be reported as major presentation failures.
Do not ask for generic figures or tables; request only declared, linked, or produced evidence that already exists."""
    result = _review_json(ctx, prompt)
    result["iteration"] = iteration
    return result
