from .base import PaperSkillContext, PaperSkillResult
from .utils import gate_outline, write_artifact


STEP_ID = "04_outline_gate"


def run(ctx: PaperSkillContext) -> PaperSkillResult:
    outline = ctx.get("outline", {})
    issues = gate_outline(outline)
    data = {"issues": issues}
    summary_lines = ["# Outline Gate"]
    if issues:
        summary_lines.append("issues:")
        summary_lines.extend([f"- {i}" for i in issues])
        summary = f"{len(issues)} issue(s)"
    else:
        summary_lines.append("PASS")
        summary = "PASS"
    artifacts = write_artifact(ctx.paper_id, STEP_ID, data, summary_lines)
    return PaperSkillResult(
        name="outline_gate",
        summary=summary,
        artifacts=artifacts,
        data={"outline_gate_issues": issues},
        warnings=issues,
    )
