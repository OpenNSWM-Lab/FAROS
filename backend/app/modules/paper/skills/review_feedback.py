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


def _review_json(ctx: PaperSkillContext, prompt: str, max_tokens: int = 6000) -> Dict[str, Any]:
    response = ctx.client.chat(
        messages=[ChatMessage(role="user", content=prompt)],
        model=ctx.model,
        temperature=0.15,
        max_tokens=max_tokens,
        timeout=ctx.llm_timeout(),
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

Do not judge the paper's scientific idea, evidence chain, theory, novelty, experimental validity, or whether claims are true.

Paper title: {ctx.paper.get("title", "Untitled")}
Venue: {ctx.venue_cfg.get("name", ctx.venue)}
Compile status: {ctx.get("compile_status")}
PDF available: {ctx.get("pdf_available")}

LaTeX source bundle:
{source_bundle(ctx)}

Return JSON only with this schema:
{{
  "passed": boolean,
  "issues": [{{"severity": "blocking|major|minor", "path": "file path", "message": "format/spec/figure/table issue"}}],
  "targets": [{{"path": "file path", "instruction": "local writing instruction for the writing agent"}}]
}}

Mark passed=true only when there are no blocking or major presentation/submission-readiness issues.
Do not return replacement file content. You are not allowed to edit.
Prefer paths under sections/*.tex when the issue can be fixed by local paper writing revision."""
    result = _review_json(ctx, prompt)
    result["iteration"] = iteration
    return result
