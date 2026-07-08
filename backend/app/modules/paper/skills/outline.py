import json
import re
from typing import Any, Dict, List

from app.llm.provider_client import ChatMessage
from app.modules.paper.storage import update_paper
from .base import PaperSkillContext, PaperSkillResult
from .constants import MIN_ALGORITHMS, MIN_EQUATIONS, MIN_FIGURES, MIN_REFERENCES, MIN_TABLES
from .utils import _extract_json, load_venue_style_guide, normalize_paper_authors, write_artifact


STEP_ID = "03_outline"

OUTLINE_PROMPT = """You are a senior ML researcher writing a {paper_type} paper for {venue_name}.

**Title:** {title}
**Venue style guide:** {venue_style_guide}
**Context from plan/project:** {plan_context}
**Plan evidence package:** {plan_evidence}
**Experiment metrics:** {metrics_summary}
**Run execution results:** {runs_summary}
**Available paper figures:** {figures_summary}
**Paper writing brief:** {paper_brief}
**User notes:** {user_notes}

Generate a DETAILED paper outline. You MUST include:
- For the challenge_cup / 挑战杯 venue only, the title, abstract, section titles, key points, contribution text, and explanatory prose MUST be written in Chinese. Keep dataset/model/metric names in English only when they are proper nouns or standard technical terms. Do not use bilingual section titles such as "Introduction / 引言". For other venues, follow the venue style guide's normal language.
- Use the Venue style guide as the authoritative structure contract. If it defines mandatory standardized fields, sections, or an ordered content structure, the sections array MUST preserve those exact fields in that order. Do not merge, rename, translate, omit, or replace them with a generic ML-paper structure.
- If the Venue style guide does not define mandatory fields or sections, create a venue-appropriate academic structure with enough sections to cover motivation, method, evidence, evaluation, analysis, limitations, and conclusion.
- If the Venue style guide says to combine multiple planning fields into a single overview section, do so. Do not expand compact planning fields into redundant top-level sections.
- References: if the Plan evidence package includes literature.keyPapers, the references array MUST contain only those evidence papers. Otherwise include at least {min_refs} real, well-known papers in the field using authors, title, venue, and year; do not invent DOIs, and add "note": "to verify" if uncertain.
- Mark which sections need: algorithms (at least {min_algos}), equations (at least {min_eqs}), tables (at least {min_tables}), figures (at least {min_figs})
- If Available paper figures are listed, assign them to the most relevant sections using their exact path, label, and caption. Do not invent alternate filenames.
- Preserve the Paper writing brief's research question, core claim, must-use evidence, and avoid-claims constraints. If a Plan evidence package is present, keep the outline aligned to its research question, hypothesis, gap, principle, contribution statements, literature, and planned validation stages.
- Do not invent author names. If explicit authors are not provided by the paper record or user notes, use ["Anonymous"].

Return strict JSON:
{{
  "title": "...",
  "authors": ["Anonymous"],
  "abstract": "200-300 word abstract covering motivation, method, results, and significance",
  "sections": [
    {{
      "id": "venue_required_field_1",
      "title": "Exact required field or section title from the venue style guide",
      "keyPoints": ["Field-specific content requirements grounded in the brief, evidence, and venue structure"],
      "minWords": 600,
      "hasAlgorithm": false,
      "hasEquations": true,
      "numEquations": 1,
      "hasTables": false,
      "hasFigures": true,
      "figureDescriptions": ["Overview figure showing the proposed framework"]
    }}
  ],
  "references": [
    {{"key": "vaswani2017attention", "authors": "Vaswani, A. et al.", "title": "Attention is All You Need", "venue": "NeurIPS", "year": 2017}}
  ],
  "algorithms": [
    {{"id": "alg1", "name": "Main Algorithm Name", "inSection": "method"}},
    {{"id": "alg2", "name": "Training Procedure", "inSection": "method"}}
  ],
  "contributions": ["Contribution 1", "Contribution 2", "Contribution 3"]
}}
Return ONLY valid JSON, no markdown fences.
"""

def _clean_section_id(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", value or "").strip("_").lower()
    return cleaned or fallback


def _normalize_string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _normalize_sections(sections: Any) -> List[Dict[str, Any]]:
    if not isinstance(sections, list):
        return []

    normalized: List[Dict[str, Any]] = []
    for idx, raw_section in enumerate(sections, start=1):
        if not isinstance(raw_section, dict):
            continue
        title = str(raw_section.get("title") or f"Section {idx}").strip()
        section_id = str(raw_section.get("id") or "").strip()
        section_id = _clean_section_id(section_id or title, f"section_{idx}")
        min_words = raw_section.get("minWords", 500)
        num_equations = raw_section.get("numEquations", 0)
        try:
            min_words = int(min_words)
        except (TypeError, ValueError):
            min_words = 500
        try:
            num_equations = int(num_equations)
        except (TypeError, ValueError):
            num_equations = 0

        normalized.append({
            **raw_section,
            "id": section_id,
            "title": title,
            "keyPoints": _normalize_string_list(raw_section.get("keyPoints")),
            "minWords": max(150, min_words),
            "hasAlgorithm": bool(raw_section.get("hasAlgorithm", False)),
            "hasEquations": bool(raw_section.get("hasEquations", False)),
            "numEquations": max(0, num_equations),
            "hasTables": bool(raw_section.get("hasTables", False)),
            "hasFigures": bool(raw_section.get("hasFigures", False)),
            "figureDescriptions": _normalize_string_list(raw_section.get("figureDescriptions")),
        })
    return normalized


def _normalize_outline(outline: Dict[str, Any], ctx: PaperSkillContext) -> Dict[str, Any]:
    normalized = dict(outline)
    normalized["title"] = str(normalized.get("title") or ctx.paper.get("title") or "Untitled Paper").strip()
    normalized["authors"] = normalize_paper_authors(ctx.paper.get("authors"))
    normalized["abstract"] = str(normalized.get("abstract") or "").strip()
    normalized["sections"] = _normalize_sections(normalized.get("sections"))
    normalized["references"] = normalized.get("references") if isinstance(normalized.get("references"), list) else []
    normalized["algorithms"] = normalized.get("algorithms") if isinstance(normalized.get("algorithms"), list) else []
    normalized["contributions"] = _normalize_string_list(normalized.get("contributions"))
    return normalized


def _parse_plan_evidence(context: Dict[str, str]) -> Dict[str, Any]:
    raw = context.get("plan_evidence", "N/A")
    if not raw or raw == "N/A":
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) and parsed.get("status") == "collected" else {}


def _resolve_plan_evidence(context: Dict[str, str], paper: Dict[str, Any]) -> Dict[str, Any]:
    evidence = _parse_plan_evidence(context)
    if evidence:
        return evidence
    stored = paper.get("evidenceJson")
    return stored if isinstance(stored, dict) and stored.get("status") == "collected" else {}


def _evidence_package_id(context: Dict[str, str]) -> str:
    evidence = _parse_plan_evidence(context)
    package = evidence.get("package") if isinstance(evidence.get("package"), dict) else {}
    return str(package.get("packageId") or "")


def _reference_key_from_paper(paper: Dict[str, Any], index: int) -> str:
    paper_id = str(paper.get("paperId") or paper.get("structuredPaperId") or "").strip()
    if paper_id:
        return re.sub(r"[^A-Za-z0-9_:-]+", "_", paper_id).strip("_") or f"evidence_ref_{index}"
    title = str(paper.get("title") or "").lower()
    slug = re.sub(r"[^a-z0-9]+", "_", title).strip("_")[:40]
    return slug or f"evidence_ref_{index}"


def _references_from_plan_evidence(evidence: Dict[str, Any]) -> List[Dict[str, Any]]:
    literature = evidence.get("literature") if isinstance(evidence.get("literature"), dict) else {}
    key_papers = literature.get("keyPapers") if isinstance(literature.get("keyPapers"), list) else []
    references: List[Dict[str, Any]] = []
    seen_keys: set[str] = set()
    for index, paper in enumerate(key_papers, start=1):
        if not isinstance(paper, dict):
            continue
        title = str(paper.get("title") or "").strip()
        if not title:
            continue
        key = _reference_key_from_paper(paper, index)
        if key in seen_keys:
            key = f"{key}_{index}"
        seen_keys.add(key)
        references.append({
            "key": key,
            "authors": paper.get("authors") or "Unknown",
            "title": title,
            "venue": paper.get("venue") or "arXiv preprint",
            "year": paper.get("year") or 2024,
            "url": paper.get("url") or "",
            "source": "plan_evidence",
            "paperId": paper.get("paperId") or paper.get("structuredPaperId") or "",
        })
    return references


def _existing_matches_evidence(existing: Any, evidence_package_id: str) -> bool:
    if not evidence_package_id:
        return True
    if not isinstance(existing, dict):
        return False
    return existing.get("_evidencePackageId") == evidence_package_id


def build_outline(ctx: PaperSkillContext, force: bool = False) -> PaperSkillResult:
    context = ctx.get("context", {})
    paper_brief = ctx.get("paper_brief", {})
    existing = ctx.paper.get("outlineJson")
    evidence = _resolve_plan_evidence(context, ctx.paper)
    evidence_package = evidence.get("package") if isinstance(evidence.get("package"), dict) else {}
    evidence_package_id = str(evidence_package.get("packageId") or _evidence_package_id(context))
    evidence_references = _references_from_plan_evidence(evidence)
    source = "existing"

    if existing and not force and _existing_matches_evidence(existing, evidence_package_id):
        source = ctx.paper.get("outlineStatus") or "existing"
        outline = _normalize_outline(existing, ctx)
    else:
        source = "generated"
        outline_prompt = OUTLINE_PROMPT.format(
            paper_type=ctx.paper_type,
            venue_name=ctx.venue_cfg["name"],
            title=ctx.paper.get("title", "Untitled"),
            venue_style_guide=load_venue_style_guide(ctx.venue)[:3000],
            plan_context=context.get("plan_context", "N/A")[:1500],
            plan_evidence=context.get("plan_evidence", "N/A")[:7000],
            metrics_summary=context.get("metrics_summary", "N/A")[:1500],
            runs_summary=context.get("runs_summary", "N/A")[:1500],
            figures_summary=context.get("figures_summary", "N/A")[:1500],
            paper_brief=json.dumps(paper_brief, ensure_ascii=False)[:2000] if paper_brief else "N/A",
            user_notes=context.get("user_notes", "N/A"),
            min_refs=MIN_REFERENCES,
            min_algos=MIN_ALGORITHMS,
            min_eqs=MIN_EQUATIONS,
            min_tables=MIN_TABLES,
            min_figs=MIN_FIGURES,
        )

        resp = ctx.client.chat(
            messages=[ChatMessage(role="user", content=outline_prompt)],
            model=ctx.model, temperature=0.4, max_tokens=8000, timeout=ctx.llm_timeout(),
        )
        parsed = _extract_json(resp.text)
        if not parsed or "sections" not in parsed:
            raise ValueError(f"LLM returned invalid outline: {resp.text[:500]}")
        outline = _normalize_outline(parsed, ctx)
        if evidence_package_id:
            outline["_evidencePackageId"] = evidence_package_id

    if evidence_references:
        outline["references"] = evidence_references
    if source == "generated" or evidence_references:
        update_paper(ctx.paper_id, {"outlineJson": outline, "outlineStatus": source})

    summary_lines = [
        "# Outline",
        f"source: {source}",
        f"sections: {len(outline.get('sections', []))}",
        f"references: {len(outline.get('references', []))}",
        f"contributions: {len(outline.get('contributions', []))}",
    ]
    artifacts = write_artifact(ctx.paper_id, STEP_ID, outline, summary_lines)
    return PaperSkillResult(
        name="outline",
        summary=f"{source} outline",
        artifacts=artifacts,
        data={"outline": outline},
    )


def run(ctx: PaperSkillContext) -> PaperSkillResult:
    return build_outline(ctx, force=False)
