from app.modules.paper.storage import update_paper

from .base import PaperSkillContext, PaperSkillResult
from .code_evidence import collect_code_evidence_for_paper
from .plan_evidence import collect_plan_evidence_for_paper
from .utils import write_artifact


STEP_ID = "00_plan_evidence"


def run(ctx: PaperSkillContext) -> PaperSkillResult:
    evidence = collect_plan_evidence_for_paper(ctx.paper)
    plan_status = evidence.get("status", "missing")
    code_evidence = collect_code_evidence_for_paper(ctx.paper)
    evidence["codeEvidence"] = code_evidence
    evidence["warnings"] = list(dict.fromkeys(
        [str(item) for item in evidence.get("warnings", [])]
        + [str(item) for item in code_evidence.get("warnings", [])]
    ))
    status = "collected" if plan_status == "collected" or code_evidence.get("status") == "collected" else "missing"
    evidence["planEvidenceStatus"] = plan_status
    evidence["status"] = status
    summary_lines = [
        "# Paper Evidence",
        f"status: {status}",
        f"package: {evidence.get('package', {}).get('packageId', 'N/A')}",
        f"code_evidence: {code_evidence.get('status', 'missing')}",
        f"code_runs: {len(code_evidence.get('runs', []))}",
        f"cart_results: {len(code_evidence.get('cartResults', []))}",
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
        summary=f"paper evidence {status}; code evidence {code_evidence.get('status', 'missing')}",
        artifacts=artifacts,
        data={"plan_evidence": evidence, "code_evidence": code_evidence},
        warnings=evidence.get("warnings", []),
    )
