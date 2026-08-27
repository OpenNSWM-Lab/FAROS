import json
from typing import Any, Dict

from app.llm.provider_client import ChatMessage
from app.modules.paper.storage import update_paper
from .base import PaperSkillContext, PaperSkillResult
from .utils import _extract_json, load_venue_style_guide, stable_context_fingerprint, write_artifact


STEP_ID = "02_paper_brief"

BRIEF_PROMPT = """You are preparing a writing brief before drafting an academic paper.

**Title:** {title}
**Paper type:** {paper_type}
**Venue:** {venue_name}
**Venue style guide:** {venue_style_guide}
**Plan context:** {plan_context}
**Plan evidence package:** {plan_evidence}
**Code-stage evidence:** {code_evidence}
**Project summary:** {project_summary}
**Experiment metrics:** {metrics_summary}
**Run evidence:** {runs_summary}
**Available figures:** {figures_summary}
**User notes:** {user_notes}
**User brief edits:** {brief_user_edits}

Create a concise, concrete paper writing brief. The brief must guide the outline and section writing. It must not invent unsupported experiments, datasets, baselines, or claims. If a Plan evidence package is present, treat it as the authoritative source for research question, hypothesis, gap, method principle, contribution statements, related work, and planned validation. Treat Code-stage evidence, experiment metrics, and run evidence as observed implementation/experiment evidence when present; keep planned validation targets separate from observed results. Use the Venue style guide to adapt the paper angle, content ordering, evidence emphasis, and tone to the target venue.

Metric direction is mandatory: lower ECE and Brier Score are better; higher F1 and AUROC are better. Compare the exact baseline and method values before using words such as improve, reduce, outperform, or superior. If metric directions disagree, describe the result as a trade-off and list both improved and degraded metrics. Never call a change statistically significant unless repeated-run inferential statistics are present in the supplied artifacts.

Return strict JSON:
{{
  "research_question": "...",
  "core_claim": "...",
  "paper_angle": "system | algorithm | benchmark | survey | application | security | position",
  "target_audience": "...",
  "contributions": ["...", "...", "..."],
  "must_use_evidence": [
    "metric or run evidence that must be discussed",
    {{"kind": "code_table|code_figure", "label": "...", "file": "...", "location": "...", "target_section": "Experiments", "analysis": "what the artifact supports, without copying table/body content"}}
  ],
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
        if item.get("include") is False:
            continue
        figures.append({
            "label": item.get("label") or "",
            "path": item.get("path") or "",
            "caption": item.get("caption") or item.get("title") or "",
            "target_section": item.get("targetSection") or item.get("target_section") or "Experiments",
        })
    return [fig for fig in figures if fig["label"] or fig["path"] or fig["caption"]]


def _parse_context_json(context: Dict[str, str], key: str) -> Any:
    raw = context.get(key, "N/A")
    if not raw or raw == "N/A":
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _parse_plan_evidence(context: Dict[str, str]) -> Dict[str, Any]:
    parsed = _parse_context_json(context, "plan_evidence")
    return parsed if isinstance(parsed, dict) and parsed.get("status") == "collected" else {}


def _artifact_reference(kind: str, node_id: str, artifact: Dict[str, Any], analysis: str = "") -> Dict[str, Any]:
    label = (
        artifact.get("label")
        or artifact.get("tableId")
        or artifact.get("figureId")
        or artifact.get("id")
        or artifact.get("name")
        or artifact.get("path")
        or "code-artifact"
    )
    file_name = artifact.get("name") or artifact.get("filename") or artifact.get("path") or artifact.get("cartRelativePath")
    location = artifact.get("cartRelativePath") or artifact.get("sourcePath") or artifact.get("path") or artifact.get("location")
    target_section = artifact.get("targetSection") or artifact.get("target_section") or "Experiments"
    return {
        "kind": kind,
        "label": str(label),
        "file": str(file_name or ""),
        "location": str(location or ""),
        "node_id": str(artifact.get("nodeId") or node_id or ""),
        "target_section": str(target_section),
        "analysis": str(analysis or artifact.get("analysis") or artifact.get("caption") or "Discuss the artifact only as supporting evidence; do not copy its raw table or chart body into the brief."),
        "instruction": "Reference this code artifact by label/file/location and use its analysis to guide local writing; do not paste raw table rows, chart data, or artifact body content into the brief.",
    }


def _dedupe_requirement_items(items: list[Any]) -> list[Any]:
    deduped: list[Any] = []
    seen: set[str] = set()
    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str) if isinstance(item, dict) else str(item)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _code_brief_requirements(context: Dict[str, str], code_evidence_obj: Any = None) -> Dict[str, list[Any]]:
    requirements: Dict[str, list[Any]] = {
        "must_use_evidence": [],
        "must_use_figures": [],
    }
    code_evidence = code_evidence_obj if isinstance(code_evidence_obj, dict) else _parse_context_json(context, "code_evidence")
    metrics_summary = _parse_context_json(context, "metrics_summary")
    cart_sources: list[Dict[str, Any]] = []
    if isinstance(code_evidence, dict):
        cart_sources.extend([
            cart for cart in code_evidence.get("cartResults", [])[:3]
            if isinstance(cart, dict) and cart.get("claimEligible") is True
        ])
    if isinstance(metrics_summary, dict):
        cart_sources.extend([
            cart for cart in metrics_summary.get("cartMetrics", [])[:3]
            if isinstance(cart, dict) and cart.get("claimEligible") is True
        ])

    seen_cart_ids = set()
    for cart in cart_sources:
        cart_id = str(cart.get("cartId") or id(cart))
        if cart_id in seen_cart_ids:
            continue
        seen_cart_ids.add(cart_id)
        for node in cart.get("nodeResults", [])[:8]:
            if not isinstance(node, dict):
                continue
            node_id = node.get("nodeId", "code node")
            metrics = node.get("metrics")
            if isinstance(metrics, dict) and metrics:
                requirements["must_use_evidence"].append(
                    f"Use code result metrics from {node_id}: {json.dumps(metrics, ensure_ascii=False)[:500]}"
                )
            if node.get("resultAnalysis"):
                requirements["must_use_evidence"].append(
                    f"Use code result analysis from {node_id}: {str(node.get('resultAnalysis'))[:500]}"
                )
            analysis = str(node.get("resultAnalysis") or "")
            for table in node.get("codeTables", [])[:3] if isinstance(node.get("codeTables"), list) else []:
                if isinstance(table, dict):
                    requirements["must_use_evidence"].append(
                        _artifact_reference("code_table", str(node_id), table, analysis)
                    )
            for figure in node.get("codeFigures", [])[:3] if isinstance(node.get("codeFigures"), list) else []:
                if isinstance(figure, dict):
                    requirements["must_use_evidence"].append(
                        _artifact_reference("code_figure", str(node_id), figure, analysis)
                    )
        cart_analysis = str(cart.get("resultAnalysis") or cart.get("analysis") or "")
        for figure in cart.get("codeFigures", [])[:5] if isinstance(cart.get("codeFigures"), list) else []:
            if isinstance(figure, dict):
                requirements["must_use_evidence"].append(
                    _artifact_reference("code_figure", str(figure.get("nodeId") or ""), figure, cart_analysis)
                )
        for table in cart.get("codeTables", [])[:5] if isinstance(cart.get("codeTables"), list) else []:
            if isinstance(table, dict):
                requirements["must_use_evidence"].append(
                    _artifact_reference("code_table", str(table.get("nodeId") or ""), table, cart_analysis)
                )
    for fig in _figures_from_summary(context.get("figures_summary", "N/A"))[:8]:
        if fig.get("path"):
            requirements["must_use_figures"].append(fig)
    return requirements


def _evidence_package_id(context: Dict[str, str]) -> str:
    evidence = _parse_plan_evidence(context)
    package = evidence.get("package") if isinstance(evidence.get("package"), dict) else {}
    return str(package.get("packageId") or "")


def _brief_context_fingerprint(ctx: PaperSkillContext, context: Dict[str, str], brief_user_edits: str) -> str:
    return stable_context_fingerprint(
        {
            "title": ctx.paper.get("title"),
            "paperType": ctx.paper_type,
            "venue": ctx.venue,
            "authors": ctx.paper.get("authors") or [],
            "briefUserEdits": brief_user_edits,
        },
        {
            key: context.get(key, "N/A")
            for key in (
                "plan_evidence",
                "code_evidence",
                "metrics_summary",
                "runs_summary",
                "figures_summary",
                "code_tables_summary",
                "user_notes",
            )
        },
    )


def _context_is_empty(context: Dict[str, str], brief_user_edits: str) -> bool:
    context_keys = (
        "plan_evidence",
        "code_evidence",
        "metrics_summary",
        "runs_summary",
        "figures_summary",
        "code_tables_summary",
        "user_notes",
    )
    return not brief_user_edits and all(context.get(key, "N/A") in {"", "N/A", None} for key in context_keys)


def _existing_matches_context(
    existing: Any,
    evidence_package_id: str,
    context_fingerprint: str,
    context_empty: bool = False,
) -> bool:
    if not isinstance(existing, dict):
        return False
    if existing.get("_contextFingerprint"):
        return (
            existing.get("_evidencePackageId", "") == evidence_package_id
            and existing.get("_contextFingerprint") == context_fingerprint
        )
    return context_empty and not evidence_package_id


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
            "Do not generalize a result beyond the exact evaluated baseline and benchmark.",
            "Do not use literature citations as evidence for this project's observed metric values.",
        ],
        "user_brief_edits": brief_user_edits,
    }


def _normalize_brief(brief: Dict[str, Any], ctx: PaperSkillContext, brief_user_edits: str, context: Dict[str, str] | None = None) -> Dict[str, Any]:
    brief.setdefault("research_question", f"What does {ctx.paper.get('title', 'this paper')} investigate?")
    brief.setdefault("core_claim", "Claims should be grounded in the linked context and evidence.")
    brief.setdefault("paper_angle", ctx.paper_type)
    brief.setdefault("target_audience", ctx.venue_cfg["name"])
    brief.setdefault("contributions", [])
    brief.setdefault("must_use_evidence", [])
    brief.setdefault("must_use_figures", [])
    brief.setdefault("section_priorities", {})
    brief.setdefault("avoid_claims", [])
    if context:
        code_requirements = _code_brief_requirements(context, ctx.get("code_evidence"))
        existing_evidence = brief.get("must_use_evidence", [])
        if not isinstance(existing_evidence, list):
            existing_evidence = []
        brief["must_use_evidence"] = _dedupe_requirement_items(
            existing_evidence + code_requirements["must_use_evidence"]
        )
        existing_figures = brief.get("must_use_figures", [])
        if not isinstance(existing_figures, list):
            existing_figures = []
        seen_figures = {
            str(fig.get("path") or fig.get("label") or fig)
            for fig in existing_figures
            if isinstance(fig, dict)
        }
        for fig in code_requirements["must_use_figures"]:
            key = str(fig.get("path") or fig.get("label"))
            if key and key not in seen_figures:
                existing_figures.append(fig)
                seen_figures.add(key)
        brief["must_use_figures"] = existing_figures
    if brief_user_edits:
        brief["user_brief_edits"] = brief_user_edits
    return brief


def build_brief(ctx: PaperSkillContext, force: bool = False) -> PaperSkillResult:
    context = ctx.get("context", {})
    brief_user_edits = (ctx.paper.get("briefUserEdits") or "").strip()
    existing = ctx.paper.get("briefJson")
    evidence_package_id = _evidence_package_id(context)
    context_fingerprint = _brief_context_fingerprint(ctx, context, brief_user_edits)
    source = "existing"

    if existing and not force and _existing_matches_context(
        existing,
        evidence_package_id,
        context_fingerprint,
        _context_is_empty(context, brief_user_edits),
    ):
        brief = _normalize_brief(existing, ctx, brief_user_edits, context)
    else:
        prompt = BRIEF_PROMPT.format(
            title=ctx.paper.get("title", "Untitled"),
            paper_type=ctx.paper_type,
            venue_name=ctx.venue_cfg["name"],
            venue_style_guide=load_venue_style_guide(ctx.venue)[:2500],
            plan_context=context.get("plan_context", "N/A")[:1500],
            plan_evidence=context.get("plan_evidence", "N/A")[:6000],
            code_evidence=context.get("code_evidence", "N/A")[:5000],
            project_summary=context.get("project_summary", "N/A")[:1500],
            metrics_summary=context.get("metrics_summary", "N/A")[:4000],
            runs_summary=context.get("runs_summary", "N/A")[:2500],
            figures_summary=context.get("figures_summary", "N/A")[:2500],
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
                structured_output=True,
            )
            parsed = _extract_json(resp.text)
            if not parsed:
                raise ValueError(f"LLM returned invalid brief: {resp.text[:300]}")
            brief = _normalize_brief(parsed, ctx, brief_user_edits, context)
            if evidence_package_id:
                brief["_evidencePackageId"] = evidence_package_id
            brief["_contextFingerprint"] = context_fingerprint
            source = "generated"
        except Exception:
            brief = _normalize_brief(_fallback_brief(ctx, context, brief_user_edits), ctx, brief_user_edits, context)
            source = "fallback"
        if evidence_package_id:
            brief["_evidencePackageId"] = evidence_package_id
        brief["_contextFingerprint"] = context_fingerprint

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
