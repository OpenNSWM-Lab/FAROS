from .base import PaperSkillContext, PaperSkillResult
from .utils import write_artifact


STEP_ID = "10_qa_audit"


def run(ctx: PaperSkillContext) -> PaperSkillResult:
    outline_issues = ctx.get("outline_gate_issues", [])
    evidence_usage = {
        "reviews": ctx.get("simple_reviews", []),
        "suggestions": [
            issue
            for review in ctx.get("simple_reviews", [])
            if isinstance(review, dict) and review.get("source") == "evidence_usage"
            for issue in review.get("issues", [])
            if isinstance(issue, dict)
        ],
    }
    paper_brief = ctx.get("paper_brief", {})
    summary_lines = [
        "# QA / Audit",
        f"brief_core_claim: {paper_brief.get('core_claim', 'N/A') if isinstance(paper_brief, dict) else 'N/A'}",
        f"legacy_outline_issues: {len(outline_issues)}",
        f"evidence_usage_suggestions: {len(evidence_usage['suggestions'])}",
    ]
    artifacts = write_artifact(
        ctx.paper_id,
        STEP_ID,
        {
            "paper_brief": paper_brief,
            "legacy_outline_issues": outline_issues,
            "evidence_usage": evidence_usage,
        },
        summary_lines,
    )
    return PaperSkillResult(
        name="qa_audit",
        summary="complete",
        artifacts=artifacts,
        data={"qa_summary": {"paper_brief": paper_brief, "legacy_outline_issues": outline_issues, "evidence_usage": evidence_usage}},
    )
