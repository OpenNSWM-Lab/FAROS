import json
from typing import Any, Dict

from app.llm.provider_client import ChatMessage
from app.modules.paper.storage import update_paper
from .base import PaperSkillContext, PaperSkillResult
from .utils import _extract_json, load_venue_style_guide, write_artifact


STEP_ID = "02_paper_brief"

BRIEF_PROMPT = """You are preparing a writing brief before drafting an academic paper.

**Title:** {title}
**Paper type:** {paper_type}
**Venue:** {venue_name}
**Venue style guide:** {venue_style_guide}
**Plan context:** {plan_context}
**Plan evidence package:** {plan_evidence}
**Project summary:** {project_summary}
**Experiment metrics:** {metrics_summary}
**Run evidence:** {runs_summary}
**Available figures:** {figures_summary}
**User notes:** {user_notes}
**User brief edits:** {brief_user_edits}

Create a concise, concrete paper writing brief. The brief must guide the outline and section writing. It must not invent unsupported experiments, datasets, baselines, or claims. If a Plan evidence package is present, treat it as the authoritative source for research question, hypothesis, gap, method principle, contribution statements, related work, and planned validation. Use the Venue style guide to adapt the paper angle, content ordering, evidence emphasis, and tone to the target venue.

Return strict JSON:
{{
  "research_question": "...",
  "core_claim": "...",
  "paper_angle": "system | algorithm | benchmark | survey | application | security | position",
  "target_audience": "...",
  "contributions": ["...", "...", "..."],
  "must_use_evidence": ["metric or run evidence that must be discussed"],
  "must_use_figures": [
    {{"label": "fig:...", "path": "figures/...", "caption": "...", "target_section": "Experiments"}}
  ],
  "section_priorities": {{
    "Introduction": ["..."],
    "Method": ["..."],
    "Experiments": ["..."],
    "Analysis": ["..."]
  }},
  "avoid_claims": ["Do not claim ..."]
}}
Return ONLY valid JSON, no markdown fences.
"""


def _figures_from_summary(figures_summary: str) -> list[Dict[str, Any]]:
    if not figures_summary or figures_summary == "N/A":
        return []
    try:
        parsed = json.loads(figures_summary)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []

    figures: list[Dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        figures.append({
            "label": item.get("label") or "",
            "path": item.get("path") or "",
            "caption": item.get("caption") or item.get("title") or "",
            "target_section": item.get("targetSection") or item.get("target_section") or "Experiments",
        })
    return [fig for fig in figures if fig["label"] or fig["path"] or fig["caption"]]


def _parse_plan_evidence(context: Dict[str, str]) -> Dict[str, Any]:
    raw = context.get("plan_evidence", "N/A")
    if not raw or raw == "N/A":
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) and parsed.get("status") == "collected" else {}


def _evidence_package_id(context: Dict[str, str]) -> str:
    evidence = _parse_plan_evidence(context)
    package = evidence.get("package") if isinstance(evidence.get("package"), dict) else {}
    return str(package.get("packageId") or "")


def _existing_matches_evidence(existing: Any, evidence_package_id: str) -> bool:
    if not evidence_package_id:
        return True
    if not isinstance(existing, dict):
        return False
    return existing.get("_evidencePackageId") == evidence_package_id


def _brief_from_plan_evidence(
    ctx: PaperSkillContext,
    evidence: Dict[str, Any],
    brief_user_edits: str,
) -> Dict[str, Any]:
    idea = evidence.get("idea") if isinstance(evidence.get("idea"), dict) else {}
    package = evidence.get("package") if isinstance(evidence.get("package"), dict) else {}
    literature = evidence.get("literature") if isinstance(evidence.get("literature"), dict) else {}
    key_papers = literature.get("keyPapers") if isinstance(literature.get("keyPapers"), list) else []
    validation_plan = evidence.get("validationPlan") if isinstance(evidence.get("validationPlan"), list) else []
    principle = evidence.get("principle") if isinstance(evidence.get("principle"), dict) else {}
    gap = evidence.get("gap") if isinstance(evidence.get("gap"), dict) else {}
    contributions = [
        item.get("statement", "")
        for item in evidence.get("contributionStatement", [])
        if isinstance(item, dict) and item.get("statement")
    ]

    must_use_evidence: list[str] = []
    for paper in key_papers[:5]:
        if not isinstance(paper, dict):
            continue
        title = paper.get("title", "")
        summary = paper.get("summary", "")
        if title:
            must_use_evidence.append(f"Use related-work evidence from {title}: {summary[:240]}")
    for stage in validation_plan[:4]:
        if not isinstance(stage, dict):
            continue
        expected = []
        for step in stage.get("steps", [])[:3]:
            if isinstance(step, dict):
                expected.extend(step.get("expected", [])[:3])
        if expected:
            must_use_evidence.append(
                f"Planned validation for {stage.get('title', stage.get('id', 'stage'))}: "
                f"{json.dumps(expected[:4], ensure_ascii=False)}"
            )

    brief = {
        "research_question": evidence.get("researchQuestion") or idea.get("problem") or f"What does {ctx.paper.get('title', 'this paper')} investigate?",
        "core_claim": evidence.get("hypothesis") or idea.get("hypothesisStatement") or "Claims should be grounded in the PlanPackage evidence.",
        "paper_angle": ctx.paper_type,
        "target_audience": f"{ctx.venue_cfg['name']} reviewers and researchers in the paper topic area.",
        "contributions": contributions or [
            item for item in [
                idea.get("keyInsight", ""),
                idea.get("proposedMethod", ""),
                idea.get("expectedOutcome", ""),
            ] if item
        ],
        "must_use_evidence": must_use_evidence,
        "must_use_figures": [],
        "section_priorities": {
            "Introduction": [
                item for item in [
                    evidence.get("researchQuestion", ""),
                    evidence.get("hypothesis", ""),
                    gap.get("summary", ""),
                ] if item
            ],
            "Related Work": [
                item for item in [
                    literature.get("summary", ""),
                    "Compare against the key papers in the Plan evidence package.",
                ] if item
            ],
            "Method": [
                item for item in [
                    principle.get("summary", ""),
                    principle.get("mechanism", ""),
                ] if item
            ],
            "Experiments": [
                "Describe planned validation as planned checks, not completed results.",
                "Do not convert expected targets into observed findings.",
            ],
            "Analysis": [
                "Discuss assumptions, risks, and limitations from the Plan evidence package.",
            ],
        },
        "avoid_claims": [
            "Do not present PlanPackage expected metrics or targets as observed experimental results.",
            "Do not introduce datasets, baselines, or performance numbers absent from the PlanPackage or linked run artifacts.",
            "Do not drift away from the PlanPackage research question and hypothesis.",
        ],
        "_evidencePackageId": package.get("packageId", ""),
        "_evidenceIdeaTitle": idea.get("title", ""),
    }
    if brief_user_edits:
        brief["user_brief_edits"] = brief_user_edits
    return brief


def _fallback_brief(ctx: PaperSkillContext, context: Dict[str, str], brief_user_edits: str) -> Dict[str, Any]:
    evidence = _parse_plan_evidence(context)
    if evidence:
        return _brief_from_plan_evidence(ctx, evidence, brief_user_edits)

    title = ctx.paper.get("title", "Untitled Paper")
    figures = context.get("figures_summary", "N/A")
    metrics = context.get("metrics_summary", "N/A")
    must_use_evidence = []
    if metrics != "N/A":
        must_use_evidence.append("Use the linked experiment metrics when discussing results.")
    if context.get("runs_summary", "N/A") != "N/A":
        must_use_evidence.append("Use linked run evidence for implementation or execution claims.")

    must_use_figures = _figures_from_summary(figures)
    if figures != "N/A" and not must_use_figures:
        must_use_figures.append({
            "label": "linked paper figures",
            "path": "figures/",
            "caption": "Use linked experiment figure captions where relevant.",
            "target_section": "Experiments",
        })

    return {
        "research_question": f"What problem does {title} solve, and what evidence supports the proposed approach?",
        "core_claim": "The paper should make only claims supported by linked plans, experiments, runs, figures, or user notes.",
        "paper_angle": ctx.paper_type,
        "target_audience": f"{ctx.venue_cfg['name']} reviewers and researchers in the paper topic area.",
        "contributions": [
            "Define the research problem and motivation clearly.",
            "Describe the proposed method or system in enough technical detail.",
            "Ground the evaluation in linked metrics, runs, and figures where available.",
        ],
        "must_use_evidence": must_use_evidence,
        "must_use_figures": must_use_figures,
        "section_priorities": {
            "Introduction": ["motivation", "problem statement", "contributions"],
            "Method": ["technical design", "assumptions", "implementation details"],
            "Experiments": ["linked metrics", "baselines", "figure discussion"],
            "Analysis": ["trade-offs", "limitations", "evidence-backed interpretation"],
        },
        "avoid_claims": [
            "Do not invent datasets, baselines, or experimental results.",
            "Do not claim state-of-the-art performance without explicit evidence.",
        ],
        "user_brief_edits": brief_user_edits,
    }


def _normalize_brief(brief: Dict[str, Any], ctx: PaperSkillContext, brief_user_edits: str) -> Dict[str, Any]:
    brief.setdefault("research_question", f"What does {ctx.paper.get('title', 'this paper')} investigate?")
    brief.setdefault("core_claim", "Claims should be grounded in the linked context and evidence.")
    brief.setdefault("paper_angle", ctx.paper_type)
    brief.setdefault("target_audience", ctx.venue_cfg["name"])
    brief.setdefault("contributions", [])
    brief.setdefault("must_use_evidence", [])
    brief.setdefault("must_use_figures", [])
    brief.setdefault("section_priorities", {})
    brief.setdefault("avoid_claims", [])
    if brief_user_edits:
        brief["user_brief_edits"] = brief_user_edits
    return brief


def build_brief(ctx: PaperSkillContext, force: bool = False) -> PaperSkillResult:
    context = ctx.get("context", {})
    brief_user_edits = (ctx.paper.get("briefUserEdits") or "").strip()
    existing = ctx.paper.get("briefJson")
    evidence_package_id = _evidence_package_id(context)
    source = "existing"

    if existing and not force and _existing_matches_evidence(existing, evidence_package_id):
        brief = _normalize_brief(existing, ctx, brief_user_edits)
    else:
        prompt = BRIEF_PROMPT.format(
            title=ctx.paper.get("title", "Untitled"),
            paper_type=ctx.paper_type,
            venue_name=ctx.venue_cfg["name"],
            venue_style_guide=load_venue_style_guide(ctx.venue)[:2500],
            plan_context=context.get("plan_context", "N/A")[:1500],
            plan_evidence=context.get("plan_evidence", "N/A")[:6000],
            project_summary=context.get("project_summary", "N/A")[:1500],
            metrics_summary=context.get("metrics_summary", "N/A")[:1500],
            runs_summary=context.get("runs_summary", "N/A")[:1500],
            figures_summary=context.get("figures_summary", "N/A")[:1500],
            user_notes=context.get("user_notes", "N/A"),
            brief_user_edits=brief_user_edits or "N/A",
        )
        try:
            resp = ctx.client.chat(
                messages=[ChatMessage(role="user", content=prompt)],
                model=ctx.model,
                temperature=0.25,
                max_tokens=3000,
                timeout=ctx.llm_timeout(),
            )
            parsed = _extract_json(resp.text)
            if not parsed:
                raise ValueError(f"LLM returned invalid brief: {resp.text[:300]}")
            brief = _normalize_brief(parsed, ctx, brief_user_edits)
            if evidence_package_id:
                brief["_evidencePackageId"] = evidence_package_id
            source = "generated"
        except Exception:
            brief = _fallback_brief(ctx, context, brief_user_edits)
            source = "fallback"
        if evidence_package_id:
            brief["_evidencePackageId"] = evidence_package_id

        update_paper(ctx.paper_id, {
            "briefJson": brief,
            "briefStatus": source,
            "briefUserEdits": brief_user_edits,
        })

    summary_lines = [
        "# Paper Brief",
        f"source: {source}",
        f"research_question: {brief.get('research_question', '')}",
        f"core_claim: {brief.get('core_claim', '')}",
        f"contributions: {len(brief.get('contributions', []))}",
        f"must_use_evidence: {len(brief.get('must_use_evidence', []))}",
        f"must_use_figures: {len(brief.get('must_use_figures', []))}",
        f"user_edits: {'yes' if brief_user_edits else 'no'}",
    ]
    artifacts = write_artifact(
        ctx.paper_id,
        STEP_ID,
        {"brief": brief, "source": source, "briefUserEdits": brief_user_edits},
        summary_lines,
    )
    return PaperSkillResult(
        name="paper_brief",
        summary=f"{source} brief",
        artifacts=artifacts,
        data={"paper_brief": brief},
    )


def run(ctx: PaperSkillContext) -> PaperSkillResult:
    return build_brief(ctx, force=False)
