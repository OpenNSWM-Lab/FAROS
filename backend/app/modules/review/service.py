"""
Review Service — LLM-driven paper review generation.

Pipeline:
1. Load paper content (main.tex + sections)
2. LLM generates structured review
3. Parse into actionable items
4. Persist review
"""

import json
import logging
from typing import Optional, Dict, Any, List

from app.core.settings import get_settings
from app.llm.provider_client import get_provider_client, ChatMessage
from app.modules.review.artifact_collector import collect_reviewx_artifacts
from app.modules.review.cem_guidance import annotate_risk_tree_with_mismatch
from app.modules.review.claim_extractor import extract_claims
from app.modules.review.evidence_graph import build_evidence, link_claims_to_evidence
from app.modules.review.evidence_verifier import verify_claim_evidence
from app.modules.review.model_router import refine_findings_with_budget
from app.modules.review.mismatch_scorer import build_mismatch_report
from app.modules.review.risk_analyzer import analyze_reviewx_risks
from app.modules.review.revision_planner import findings_to_action_items
from app.modules.review.revision_feedback import attach_revision_feedback
from app.modules.review.reviewx_models import ReviewXReport
from app.modules.review.storage import get_paper, read_paper_file, list_paper_files
from app.modules.review.storage import get_review, update_review

logger = logging.getLogger(__name__)

REVIEW_PROMPT = """You are a senior ML conference reviewer (ACL/NeurIPS/ICML caliber).
Review the following paper submission rigorously.

**Paper Title:** {title}
**Paper Type:** {paper_type}

**LaTeX Content:**
{latex_content}

**Your review MUST include ALL of the following:**

1. **Overall Assessment** (2-3 sentences)
2. **Score Suggestion** (1-10 scale, 6+ = accept)
3. **Strengths** (at least 3 specific points)
4. **Weaknesses** (at least 3 specific points)
5. **Questions for Authors** (at least 3)
6. **Missing Experiments** (at least 2 suggestions)
7. **Writing Issues** (grammar, clarity, structure — at least 2)
8. **Action Items** — EXACTLY 12 concrete, actionable items, each with:
   - description: what needs to be done
   - section: which section of the paper this applies to (e.g. "Method", "Experiments", "Introduction")
   - severity: one of BLOCKER, MAJOR, MINOR
   - targetModule: one of "papers" (rewrite section), "experiments" (new figure/table), "code" (code improvement)
   - suggestedEdit: brief description of the fix

**Return strict JSON:**
```json
{{
  "overallAssessment": "...",
  "scoreSuggestion": 5,
  "strengths": ["...", "...", "..."],
  "weaknesses": ["...", "...", "..."],
  "questions": ["...", "...", "..."],
  "missingExperiments": ["...", "..."],
  "writingIssues": ["...", "..."],
  "actionItems": [
    {{
      "description": "...",
      "section": "Method",
      "severity": "MAJOR",
      "targetModule": "papers",
      "suggestedEdit": "..."
    }}
  ]
}}
```

Be thorough, critical but constructive. Return ONLY valid JSON.
"""


def _extract_json(text: str) -> Optional[Dict]:
    import re
    text = text.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1]
        if "```" in text:
            text = text.rsplit("```", 1)[0]
    elif "```" in text:
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1]
        elif len(parts) >= 2:
            text = parts[1]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None


def _collect_paper_content(paper_id: str) -> str:
    """Collect all LaTeX content from a paper."""
    files = list_paper_files(paper_id)
    tex_files = [f for f in files if not f["isDir"] and (f["name"].endswith(".tex") or f["name"].endswith(".bib"))]

    content_parts = []
    # main.tex first
    main = read_paper_file(paper_id, "main.tex")
    if main:
        content_parts.append(f"=== main.tex ===\n{main}")

    for f in tex_files:
        if f["path"] == "main.tex":
            continue
        c = read_paper_file(paper_id, f["path"])
        if c:
            content_parts.append(f"=== {f['path']} ===\n{c}")

    return "\n\n".join(content_parts)[:8000]


def generate_review(review_id: str) -> Dict[str, Any]:
    """
    Full pipeline: load paper → LLM review → parse → persist.
    Returns updated review record.
    """
    review = get_review(review_id)
    if not review:
        raise ValueError(f"Review not found: {review_id}")

    paper_id = review.get("paperId")
    paper = get_paper(paper_id)
    if not paper:
        raise ValueError(f"Paper not found: {paper_id}")

    settings = get_settings()
    provider_name = review.get("providerName") or settings.get_active_provider()
    model = review.get("model") or settings.get_active_model(provider_name)

    update_review(review_id, {"status": "generating"})

    try:
        client = get_provider_client(provider_name)

        # Collect paper content
        latex_content = _collect_paper_content(paper_id)
        if not latex_content:
            raise ValueError("Paper has no LaTeX content. Generate the paper first.")

        prompt = REVIEW_PROMPT.format(
            title=paper.get("title", "Untitled"),
            paper_type=paper.get("paperType", "algorithm"),
            latex_content=latex_content,
        )

        resp = client.chat(
            messages=[ChatMessage(role="user", content=prompt)],
            model=model, temperature=0.4, max_tokens=6000,
        )

        report = _extract_json(resp.text)
        if not report or "actionItems" not in report:
            raise ValueError(f"LLM returned invalid review: {resp.text[:300]}")

        # Build markdown report
        md_parts = [
            f"# Paper Review: {paper.get('title', 'Untitled')}",
            f"\n## Overall Assessment\n{report.get('overallAssessment', 'N/A')}",
            f"\n**Score Suggestion:** {report.get('scoreSuggestion', 'N/A')}/10",
            "\n## Strengths",
        ]
        for s in report.get("strengths", []):
            md_parts.append(f"- {s}")
        md_parts.append("\n## Weaknesses")
        for w in report.get("weaknesses", []):
            md_parts.append(f"- {w}")
        md_parts.append("\n## Questions for Authors")
        for q in report.get("questions", []):
            md_parts.append(f"- {q}")
        md_parts.append("\n## Missing Experiments")
        for m in report.get("missingExperiments", []):
            md_parts.append(f"- {m}")
        md_parts.append("\n## Writing Issues")
        for w in report.get("writingIssues", []):
            md_parts.append(f"- {w}")
        md_parts.append("\n## Action Items")
        for i, item in enumerate(report.get("actionItems", []), 1):
            md_parts.append(f"\n### {i}. [{item.get('severity', 'MAJOR')}] {item.get('description', '')}")
            md_parts.append(f"- **Section:** {item.get('section', 'N/A')}")
            md_parts.append(f"- **Target:** {item.get('targetModule', 'papers')}")
            md_parts.append(f"- **Suggested Edit:** {item.get('suggestedEdit', 'N/A')}")

        markdown_report = "\n".join(md_parts)

        update_review(review_id, {
            "status": "completed",
            "scoreSuggestion": report.get("scoreSuggestion"),
            "jsonReport": report,
            "markdownReport": markdown_report,
            "actionItems": report.get("actionItems", []),
        })

    except Exception as e:
        logger.error(f"Review generation failed: {e}", exc_info=True)
        update_review(review_id, {"status": "failed", "markdownReport": f"Generation failed: {str(e)[:500]}"})
        raise

    return get_review(review_id)


def generate_reviewx(review_id: str) -> Dict[str, Any]:
    """
    Evidence-grounded ReviewX pipeline.

    This first version is deterministic and local-first:
    1. Collect FAROS artifacts for a paper.
    2. Extract claims from briefJson and LaTeX.
    3. Build local evidence objects from brief, citations, experiments, metrics, and code exports.
    4. Link claims to evidence and turn risk gaps into actionable findings.
    5. Persist ReviewX data on the review record.
    """
    review = get_review(review_id)
    if not review:
        raise ValueError(f"Review not found: {review_id}")

    paper_id = review.get("paperId")
    paper = get_paper(paper_id)
    if not paper:
        raise ValueError(f"Paper not found: {paper_id}")

    update_review(review_id, {"status": "generating"})

    try:
        ablation_mode = str(review.get("ablationMode") or "full")
        ablations = {item.strip() for item in ablation_mode.split(",") if item.strip() and item.strip() != "full"}
        artifacts = collect_reviewx_artifacts(paper_id)
        claims = extract_claims(artifacts)
        evidence = build_evidence(artifacts)
        links = link_claims_to_evidence(claims, evidence)
        verifications = [] if "no_verifier" in ablations else verify_claim_evidence(
            paper,
            claims,
            evidence,
            links,
            calibrate_external="no_external_calibration" not in ablations,
        )
        if "no_citation_semantic" in ablations:
            verifications = [
                verification for verification in verifications
                if verification.verifierType != "citation_semantic"
            ]
        risk_paper = paper
        if "no_external_calibration" in ablations and paper.get("externalPaper"):
            risk_paper = {key: value for key, value in paper.items() if key != "externalPaper"}
        findings, risk_tree = analyze_reviewx_risks(risk_paper, claims, evidence, links, verifications)
        preliminary_mismatch = build_mismatch_report(claims, evidence, links, verifications, findings)
        if "no_risk_tree" in ablations:
            risk_tree = []
        else:
            risk_tree = annotate_risk_tree_with_mismatch(risk_tree, preliminary_mismatch)
        settings = get_settings()
        provider_name = review.get("providerName") or settings.get_active_provider()
        model = review.get("model") or settings.get_active_model(provider_name)
        budget_mode = review.get("budgetMode", "balanced")
        findings, routing_trace = refine_findings_with_budget(
            paper=paper,
            claims=claims,
            evidence=evidence,
            findings=findings,
            provider_name=provider_name,
            model=model,
            budget_mode=budget_mode,
            mismatch_report=preliminary_mismatch,
            routing_strategy="severity" if "no_mismatch_routing" in ablations else "cem",
        )
        if "no_llm_calibration" in ablations:
            for finding in findings:
                finding.reviewerDecision = None
                finding.reviewerAssessment = None
                finding.reviewerModel = None
                finding.cemCalibration = {
                    key: value
                    for key, value in finding.cemCalibration.items()
                    if not str(key).startswith("llm")
                }
        revision_feedback = {"matchedRequestCount": 0, "statusCounts": {}} if "no_revision_feedback" in ablations else attach_revision_feedback(paper_id, findings)
        action_items = findings_to_action_items(findings)
        mismatch_report = build_mismatch_report(claims, evidence, links, verifications, findings)
        if risk_tree:
            risk_tree = annotate_risk_tree_with_mismatch(risk_tree, mismatch_report)
        evidence_graph = mismatch_report.get("graph", {})

        severity_counts: Dict[str, int] = {"blocker": 0, "major": 0, "minor": 0, "info": 0}
        for finding in findings:
            severity_counts[finding.severity] = severity_counts.get(finding.severity, 0) + 1

        valid_evidence_links = sum(1 for ids in links.values() if ids)
        support_counts: Dict[str, int] = {}
        for verification in verifications:
            support_counts[verification.supportStatus] = support_counts.get(verification.supportStatus, 0) + 1
        summary = {
            "mode": "reviewx_local_mvp",
            "ablationMode": ablation_mode,
            "ablations": sorted(ablations),
            "paperTitle": paper.get("title", "Untitled"),
            "claimCount": len(claims),
            "evidenceCount": len(evidence),
            "verificationCount": len(verifications),
            "findingCount": len(findings),
            "riskQuestionCount": len(risk_tree),
            "evidenceLinkedClaimCount": valid_evidence_links,
            "severityCounts": severity_counts,
            "supportCounts": support_counts,
            "coverage": round(valid_evidence_links / len(claims), 3) if claims else 0,
            "mismatch": mismatch_report.get("aggregate", {}),
            "cemMethod": mismatch_report.get("method", {}),
            "cemBudgetPolicy": routing_trace.get("budgetPolicy"),
            "revisionFeedback": revision_feedback,
        }
        model_trace = {
            "routingMode": budget_mode,
            "ablationMode": ablation_mode,
            "localRulePasses": [
                "artifact_collection",
                "claim_extraction",
                "evidence_linking",
                "evidence_verification",
                "cem_mismatch_scoring",
                "risk_analysis",
                "cem_guided_risk_question_tree",
                "revision_feedback_calibration",
                "revision_planning",
            ],
            "llmRouting": routing_trace,
            "llmCalls": routing_trace.get("llmCalls", []),
            "estimatedTokenCost": routing_trace.get("estimatedTokenCost", 0),
            "note": "ReviewX runs deterministic local checks first, then optionally escalates high-risk findings.",
        }

        report = ReviewXReport(
            reviewId=review_id,
            paperId=paper_id,
            claims=claims,
            evidence=evidence,
            verifications=verifications,
            findings=findings,
            riskTree=risk_tree,
            actionItems=action_items,
            summary=summary,
            modelTrace=model_trace,
            mismatchReport=mismatch_report,
            evidenceGraph=evidence_graph,
        ).to_dict()

        markdown_report = _build_reviewx_markdown(paper, report)

        update_review(review_id, {
            "status": "completed",
            "reviewKind": "reviewx",
            "scoreSuggestion": _score_from_findings(severity_counts),
            "jsonReport": report,
            "markdownReport": markdown_report,
            "actionItems": action_items,
            "claims": report["claims"],
            "evidence": report["evidence"],
            "verifications": report["verifications"],
            "findings": report["findings"],
            "riskTree": report["riskTree"],
            "modelTrace": model_trace,
            "mismatchReport": report["mismatchReport"],
            "evidenceGraph": report["evidenceGraph"],
        })
    except Exception as e:
        logger.error(f"ReviewX generation failed: {e}", exc_info=True)
        update_review(review_id, {"status": "failed", "markdownReport": f"ReviewX failed: {str(e)[:500]}"})
        raise

    return get_review(review_id)


def _score_from_findings(severity_counts: Dict[str, int]) -> int:
    score = 8
    score -= min(4, severity_counts.get("blocker", 0) * 2)
    score -= min(3, severity_counts.get("major", 0))
    score -= min(1, severity_counts.get("minor", 0) // 3)
    return max(1, min(10, score))


def _build_reviewx_markdown(paper: Dict[str, Any], report: Dict[str, Any]) -> str:
    summary = report.get("summary", {})
    findings = report.get("findings", [])
    claims = report.get("claims", [])
    evidence = report.get("evidence", [])
    risk_tree = report.get("riskTree", [])
    md = [
        f"# ReviewX Evidence Audit: {paper.get('title', 'Untitled')}",
        "",
        "## Summary",
        f"- Claims extracted: {summary.get('claimCount', 0)}",
        f"- Evidence artifacts: {summary.get('evidenceCount', 0)}",
        f"- Evidence verifications: {summary.get('verificationCount', 0)}",
        f"- Risk questions: {summary.get('riskQuestionCount', 0)}",
        f"- Findings: {summary.get('findingCount', 0)}",
        f"- Claim evidence coverage: {summary.get('coverage', 0)}",
        f"- Severity counts: {json.dumps(summary.get('severityCounts', {}), ensure_ascii=False)}",
        f"- Support counts: {json.dumps(summary.get('supportCounts', {}), ensure_ascii=False)}",
        f"- Mismatch: {json.dumps(summary.get('mismatch', {}), ensure_ascii=False)}",
        "",
        "## Risk Question Tree",
        "",
    ]
    for node in risk_tree[:20]:
        indent = "  " * int(node.get("level", 0))
        md.append(
            f"{indent}- `{node.get('id')}` {node.get('question')} "
            f"(risk={node.get('riskScore')}, mismatch={node.get('mismatchScore')}, "
            f"policy={node.get('expansionPolicy')}, status={node.get('status')}, "
            f"model={node.get('assignedModel')})"
        )
    md.extend([
        "",
        "## Highest-Risk Findings",
    ])
    for finding in findings[:12]:
        ev = ", ".join(finding.get("evidenceIds", [])) or "missing evidence"
        md.extend([
            f"### [{finding.get('severity', 'major').upper()}] {finding.get('title', '')}",
            finding.get("description", ""),
            f"- Target module: {finding.get('targetModule', 'papers')}",
            f"- Evidence: {ev}",
            f"- Suggested fix: {finding.get('suggestedFix', '')}",
            "",
        ])
    md.extend(["## Claims", ""])
    for claim in claims[:20]:
        source = claim.get("sourceSpan", {})
        md.append(f"- `{claim.get('id')}` [{claim.get('claimType')}] {claim.get('text')} ({source.get('file')}:{source.get('line', '')})")
    md.extend(["", "## Evidence", ""])
    for ev in evidence[:20]:
        md.append(f"- `{ev.get('id')}` [{ev.get('sourceModule')}/{ev.get('evidenceType')}] {ev.get('summary')}")
    return "\n".join(md)
