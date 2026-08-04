from .base import PaperSkillContext, PaperSkillResult
from .utils import write_artifact


STEP_ID = "10_qa_audit"


def run(ctx: PaperSkillContext) -> PaperSkillResult:
    outline_issues = ctx.get("outline_gate_issues", [])
    evidence_gates = ctx.get("evidence_gates", {})
    paper_brief = ctx.get("paper_brief", {})
    summary_lines = [
        "# QA / Audit",
        f"brief_core_claim: {paper_brief.get('core_claim', 'N/A') if isinstance(paper_brief, dict) else 'N/A'}",
        f"outline_issues: {len(outline_issues)}",
        f"evidence_all_pass: {evidence_gates.get('all_pass')}",
    ]
    artifacts = write_artifact(
        ctx.paper_id,
        STEP_ID,
        {
            "paper_brief": paper_brief,
            "outline_issues": outline_issues,
            "evidence_gates": evidence_gates,
        },
        summary_lines,
    )
    return PaperSkillResult(
        name="qa_audit",
        summary="complete",
        artifacts=artifacts,
        data={"qa_summary": {"paper_brief": paper_brief, "outline_issues": outline_issues, "evidence_gates": evidence_gates}},
    )
