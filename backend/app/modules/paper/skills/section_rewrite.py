import json
import os
import re
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional, Tuple

from app.llm.provider_client import ChatMessage
from app.modules.paper.storage import read_paper_file, write_paper_file
from .base import PaperSkillContext, PaperSkillResult
from .utils import (
    get_linked_figure_entries,
    load_venue_style_guide,
    normalize_section_citations,
    normalize_section_figure_references,
)


STEP_ID = "section_rewrite"

REWRITE_PROMPT = """You are revising exactly one LaTeX section of an academic paper.

**Paper title:** {title}
**Venue:** {venue_name}
**Paper type:** {paper_type}
**Rewrite mode:** {mode_instruction}
**User instruction:** {instruction}
**Target length:** {target_length}
**Venue style guide:** {venue_style_guide}

**Outline section JSON:** {section_json}
**Paper abstract:** {abstract}
**Paper writing brief:** {paper_brief}
**Metrics data:** {metrics_data}
**Run evidence:** {runs_data}
**Selected figures for this section:** {section_figures}
**References available:** {refs_summary}

**Current LaTeX section:**
{current_content}

Return a complete replacement for this one section only.
Mandatory requirements:
- Start with \\section{{{section_title}}}
- Keep the section focused on the same outline section and do not add other sections.
- Preserve citation keys from the current section when they still support the rewritten text: {preserve_citations}
- Preserve existing figure references and include selected section figures with exact path, label, and caption when relevant: {preserve_figures}
- Use only supported claims from the brief, metrics, runs, figures, and references above.
- Keep valid LaTeX. Do not use markdown fences or explanatory prose outside LaTeX.
"""


def _safe_section_id(section_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", section_id or "").strip("_")
    if not cleaned:
        raise ValueError("section_id is required")
    return cleaned


def _strip_markdown_fences(content: str) -> str:
    content = (content or "").strip()
    if content.startswith("```"):
        lines = content.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines).strip()
    return content


def _extract_section_title(content: str) -> Optional[str]:
    match = re.search(r"\\section\*?\{([^}]+)\}", content or "")
    if not match:
        return None
    return match.group(1).strip()


def _word_count(content: str) -> int:
    without_commands = re.sub(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?(?:\{[^}]*\})?", " ", content or "")
    return len(re.findall(r"\b[A-Za-z][A-Za-z0-9'-]*\b", without_commands))


def _find_outline_section(outline: Dict[str, Any], section_id: str, current_content: str) -> Tuple[Dict[str, Any], str]:
    sections = outline.get("sections", []) if isinstance(outline, dict) else []
    for section in sections:
        if not isinstance(section, dict):
            continue
        if str(section.get("id", "")) == section_id:
            title = str(section.get("title") or _extract_section_title(current_content) or section_id)
            return section, title
    title = _extract_section_title(current_content) or section_id.replace("_", " ").title()
    return {"id": section_id, "title": title}, title


def _section_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _extract_json_figures(figures_summary: str) -> List[Dict[str, Any]]:
    if not figures_summary or figures_summary == "N/A":
        return []
    try:
        parsed = json.loads(figures_summary)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict) and item.get("include", True)]


def _figures_for_section(
    figures_summary: str,
    section: Dict[str, Any],
    section_title: str,
) -> List[Dict[str, Any]]:
    figures = _extract_json_figures(figures_summary)
    if not figures:
        return []
    keys = {
        _section_key(str(section.get("id", ""))),
        _section_key(section_title),
    }
    selected = []
    untargeted = []
    for fig in figures:
        target = _section_key(str(fig.get("targetSection") or fig.get("target_section") or ""))
        if not target:
            untargeted.append(fig)
            continue
        if any(target == key or target in key or key in target for key in keys if key):
            selected.append(fig)
    return selected or untargeted[:2]


def _extract_citation_keys(content: str) -> List[str]:
    keys: List[str] = []
    for match in re.finditer(r"\\cite[a-zA-Z*]*\{([^}]+)\}", content or ""):
        for key in match.group(1).split(","):
            key = key.strip()
            if key and key not in keys:
                keys.append(key)
    return keys


def _extract_includegraphics(content: str) -> List[str]:
    paths: List[str] = []
    for match in re.finditer(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", content or ""):
        path = match.group(1).strip()
        if path and path not in paths:
            paths.append(path)
    return paths


def _mode_instruction(mode: str) -> str:
    modes = {
        "improve": "Improve clarity, technical depth, evidence use, and academic flow without changing the section purpose.",
        "expand": "Add technical detail, evidence-backed interpretation, and stronger transitions while avoiding unsupported claims.",
        "condense": "Make the section tighter and more direct while preserving essential evidence, citations, and figures.",
        "align": "Align the section with the writing brief, outline, selected figures, and venue expectations.",
    }
    return modes.get((mode or "improve").strip().lower(), mode or modes["improve"])


def rewrite_section(
    ctx: PaperSkillContext,
    section_id: str,
    instruction: str = "",
    mode: str = "improve",
    preserve_citations: bool = True,
    preserve_figures: bool = True,
    target_length: Optional[int] = None,
) -> PaperSkillResult:
    safe_section_id = _safe_section_id(section_id)
    section_path = f"sections/{safe_section_id}.tex"
    current_content = read_paper_file(ctx.paper_id, section_path)
    if current_content is None:
        raise FileNotFoundError(f"Section file not found: {section_path}")

    outline = ctx.paper.get("outlineJson") or ctx.get("outline", {}) or {}
    section, section_title = _find_outline_section(outline, safe_section_id, current_content)
    context = ctx.get("context", {})
    paper_brief = ctx.paper.get("briefJson") or ctx.get("paper_brief", {})
    refs = outline.get("references", []) if isinstance(outline, dict) else []
    refs_summary = ", ".join(
        f"{r.get('key', 'ref')}: {r.get('title', '')[:50]}" for r in refs[:20] if isinstance(r, dict)
    )
    figures_summary = context.get("figures_summary", "N/A")
    section_figures = _figures_for_section(figures_summary, section, section_title)
    effective_target_length = target_length or section.get("minWords") or max(250, _word_count(current_content))

    prompt = REWRITE_PROMPT.format(
        title=ctx.paper.get("title", "Untitled"),
        venue_name=ctx.venue_cfg["name"],
        paper_type=ctx.paper_type,
        mode_instruction=_mode_instruction(mode),
        instruction=instruction.strip() or "Improve this section for paper quality, evidence grounding, and readability.",
        target_length=f"about {effective_target_length} words",
        venue_style_guide=load_venue_style_guide(ctx.venue)[:2500],
        section_json=json.dumps(section, ensure_ascii=False)[:1200],
        abstract=str(outline.get("abstract", ""))[:800] if isinstance(outline, dict) else "",
        paper_brief=json.dumps(paper_brief, ensure_ascii=False)[:1800] if paper_brief else "N/A",
        metrics_data=context.get("metrics_summary", "N/A")[:1200],
        runs_data=context.get("runs_summary", "N/A")[:1200],
        section_figures=json.dumps(section_figures, ensure_ascii=False)[:1200] if section_figures else "N/A",
        refs_summary=refs_summary or "N/A",
        current_content=current_content[:9000],
        section_title=section_title,
        preserve_citations="yes" if preserve_citations else "no",
        preserve_figures="yes" if preserve_figures else "no",
    )

    response = ctx.client.chat(
        messages=[ChatMessage(role="user", content=prompt)],
        model=ctx.model,
        temperature=0.25,
        max_tokens=5000,
        timeout=ctx.llm_timeout(),
    )
    rewritten = _strip_markdown_fences(response.text)
    warnings: List[str] = []
    if not rewritten:
        raise ValueError("LLM returned an empty section rewrite")
    if "\\section" not in rewritten:
        rewritten = f"\\section{{{section_title}}}\n\n{rewritten}"
        warnings.append("Added missing section heading to rewrite output.")
    else:
        rewritten = re.sub(
            r"\\section\*?\{[^}]*\}",
            f"\\section{{{section_title}}}",
            rewritten,
            count=1,
        )

    if "```" in rewritten:
        rewritten = rewritten.replace("```latex", "").replace("```", "").strip()
        warnings.append("Removed markdown fence markers from rewrite output.")

    figure_entries = get_linked_figure_entries(ctx.paper, ensure_copied=True)
    figures_dir = os.path.join(ctx.latex_dir, "figures")
    rewritten, figure_rewrites = normalize_section_figure_references(
        rewritten,
        figure_entries,
        figures_dir,
    )
    rewritten, citation_rewrites = normalize_section_citations(rewritten, refs)

    if preserve_citations:
        before_cites = set(_extract_citation_keys(current_content))
        after_cites = set(_extract_citation_keys(rewritten))
        missing_cites = sorted(before_cites - after_cites)
        if missing_cites:
            warnings.append(f"Rewrite dropped citation keys: {', '.join(missing_cites[:8])}")

    if preserve_figures:
        before_figures = set(_extract_includegraphics(current_content))
        after_figures = set(_extract_includegraphics(rewritten))
        missing_figures = sorted(before_figures - after_figures)
        if missing_figures:
            warnings.append(f"Rewrite dropped figure paths: {', '.join(missing_figures[:5])}")

    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    artifact_prefix = f"artifacts/section_rewrites/{safe_section_id}_{timestamp}"
    before_path = f"{artifact_prefix}.before.tex"
    after_path = f"{artifact_prefix}.after.tex"
    meta_path = f"{artifact_prefix}.json"
    write_paper_file(ctx.paper_id, before_path, current_content)
    write_paper_file(ctx.paper_id, after_path, rewritten)
    write_paper_file(ctx.paper_id, meta_path, json.dumps({
        "paperId": ctx.paper_id,
        "sectionId": safe_section_id,
        "sectionPath": section_path,
        "mode": mode,
        "instruction": instruction,
        "preserveCitations": preserve_citations,
        "preserveFigures": preserve_figures,
        "targetLength": effective_target_length,
        "beforeWordCount": _word_count(current_content),
        "afterWordCount": _word_count(rewritten),
        "figureRewrites": figure_rewrites,
        "citationRewrites": citation_rewrites,
        "warnings": warnings,
    }, ensure_ascii=False, indent=2))

    write_paper_file(ctx.paper_id, section_path, rewritten)

    return PaperSkillResult(
        name=STEP_ID,
        summary=f"rewrote {section_path}",
        artifacts=[before_path, after_path, meta_path],
        warnings=warnings,
        data={
            "sectionId": safe_section_id,
            "path": section_path,
            "content": rewritten,
            "beforeWordCount": _word_count(current_content),
            "afterWordCount": _word_count(rewritten),
            "warnings": warnings,
            "artifacts": [before_path, after_path, meta_path],
        },
    )
