from app.modules.paper.storage import update_paper

from .base import PaperSkillContext, PaperSkillResult
from .plan_evidence import collect_plan_evidence_for_paper
from .utils import write_artifact


STEP_ID = "00_plan_evidence"


def run(ctx: PaperSkillContext) -> PaperSkillResult:
    evidence = collect_plan_evidence_for_paper(ctx.paper)
    status = "collected" if evidence.get("status") == "collected" else "missing"
    summary_lines = [
        "# Paper Evidence",
        f"status: {status}",
        f"package: {evidence.get('package', {}).get('packageId', 'N/A')}",
        f"key_papers: {len(evidence.get('literature', {}).get('keyPapers', []))}",
        f"validation_stages: {len(evidence.get('validationPlan', []))}",
    ]
    artifacts = write_artifact(ctx.paper_id, STEP_ID, evidence, summary_lines)
    updated = update_paper(ctx.paper_id, {
        "evidenceJson": evidence,
        "evidenceStatus": status,
    })
    if updated:
        ctx.paper = updated
    return PaperSkillResult(
        name="evidence_collect",
        summary=f"plan evidence {status}",
        artifacts=artifacts,
        data={"plan_evidence": evidence},
        warnings=evidence.get("warnings", []),
    )
