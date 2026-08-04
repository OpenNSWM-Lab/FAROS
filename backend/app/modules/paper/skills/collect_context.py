from typing import Dict

from .base import PaperSkillContext, PaperSkillResult
from .utils import collect_context, write_artifact


STEP_ID = "01_collect_context"


def run(ctx: PaperSkillContext) -> PaperSkillResult:
    context = collect_context(ctx.paper)
    summary_lines = [
        "# Collect Context",
        f"plan_context: {'yes' if context['plan_context'] != 'N/A' else 'no'}",
        f"project_summary: {'yes' if context['project_summary'] != 'N/A' else 'no'}",
        f"metrics_summary: {'yes' if context['metrics_summary'] != 'N/A' else 'no'}",
        f"runs_summary: {'yes' if context['runs_summary'] != 'N/A' else 'no'}",
        f"figures_summary: {'yes' if context['figures_summary'] != 'N/A' else 'no'}",
    ]
    artifacts = write_artifact(ctx.paper_id, STEP_ID, context, summary_lines)
    return PaperSkillResult(
        name="collect_context",
        summary="context collected",
        artifacts=artifacts,
        data={"context": context},
    )
