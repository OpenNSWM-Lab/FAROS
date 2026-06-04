from app.modules.paper.storage import update_paper
from .base import PaperSkillContext, PaperSkillResult
from .utils import gate_evidence, write_artifact


STEP_ID = "06_evidence_gate"


def run(ctx: PaperSkillContext) -> PaperSkillResult:
    sections_content = ctx.get("sections_content", {})
    evidence_gates = gate_evidence(sections_content)
    update_paper(ctx.paper_id, {"evidenceGates": evidence_gates})

    summary_lines = ["# Evidence Gate"]
    for key, gate in evidence_gates.items():
        if key == "all_pass":
            continue
        summary_lines.append(
            f"{key}: {gate['count']}/{gate['required']} {'PASS' if gate['pass'] else 'WARN'}"
        )
    summary_lines.append(f"all_pass: {evidence_gates.get('all_pass')}")

    artifacts = write_artifact(ctx.paper_id, STEP_ID, evidence_gates, summary_lines)
    return PaperSkillResult(
        name="evidence_gate",
        summary="PASS" if evidence_gates.get("all_pass") else "WARN",
        artifacts=artifacts,
        data={"evidence_gates": evidence_gates},
    )
