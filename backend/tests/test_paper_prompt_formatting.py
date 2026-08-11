import json
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.modules.paper.skills.base import PaperSkillContext
from app.modules.paper.skills.constants import (
    MIN_ALGORITHMS,
    MIN_EQUATIONS,
    MIN_FIGURES,
    MIN_REFERENCES,
    MIN_TABLES,
)
from app.modules.paper.skills.outline import OUTLINE_PROMPT, build_outline
from app.modules.paper.skills.paper_brief import BRIEF_PROMPT, build_brief
from app.modules.paper.skills.section_writers.base import (
    EQUATION_TEMPLATE,
    TABLE_TEMPLATE,
    render_prompt,
)
from app.modules.paper.skills.section_writers.method import MethodWriter
from app.modules.paper.storage import create_paper


class FakeChatResponse:
    def __init__(self, text):
        self.text = text


class FakeClient:
    config = types.SimpleNamespace(timeout=60)

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return FakeChatResponse(json.dumps(self.payload))


def _ctx_for_paper(paper, client, context, paper_brief=None):
    ctx = PaperSkillContext(
        paper_id=paper["id"],
        paper=paper,
        settings=types.SimpleNamespace(PAPER_GENERATION_TIMEOUT=300),
        provider_name="fake",
        model="fake-model",
        paper_type=paper.get("paperType", "algorithm"),
        venue=paper.get("targetVenue", "generic"),
        venue_cfg={"name": "Generic"},
        client=client,
        latex_dir="/tmp",
        artifacts_dir="/tmp",
    )
    ctx.update("context", context)
    if paper_brief is not None:
        ctx.update("paper_brief", paper_brief)
    return ctx


def _plan_evidence(package_id="ppkg_test"):
    return {
        "schemaVersion": "paper-plan-evidence/v1",
        "status": "collected",
        "package": {"packageId": package_id},
        "idea": {"title": "Evidence Topic", "problem": "Evidence problem"},
        "researchQuestion": "Evidence research question?",
        "hypothesis": "Evidence hypothesis.",
        "literature": {"keyPapers": []},
        "validationPlan": [],
        "contributionStatement": [],
    }


def test_outline_prompt_formats_json_examples_without_key_error():
    rendered = OUTLINE_PROMPT.format(
        paper_type="algorithm",
        venue_name="ICML",
        title="Example Paper",
        venue_style_guide="Style",
        plan_context="Plan context",
        plan_evidence="Plan evidence",
        metrics_summary="Metrics",
        runs_summary="Runs",
        figures_summary="Figures",
        paper_brief="Brief",
        user_notes="Notes",
        min_refs=MIN_REFERENCES,
        min_algos=MIN_ALGORITHMS,
        min_eqs=MIN_EQUATIONS,
        min_tables=MIN_TABLES,
        min_figs=MIN_FIGURES,
    )

    assert '"key": "vaswani2017attention"' in rendered
    assert '"id": "alg1"' in rendered
    assert "Plan evidence" in rendered


def test_brief_prompt_includes_plan_evidence():
    rendered = BRIEF_PROMPT.format(
        title="Example Paper",
        paper_type="algorithm",
        venue_name="ICML",
        venue_style_guide="Style",
        plan_context="Plan context",
        plan_evidence="Authoritative package evidence",
        project_summary="Project",
        metrics_summary="Metrics",
        runs_summary="Runs",
        figures_summary="Figures",
        user_notes="Notes",
        brief_user_edits="N/A",
    )

    assert "Authoritative package evidence" in rendered
    assert "authoritative source" in rendered


def test_brief_regenerates_when_existing_is_not_bound_to_plan_evidence():
    evidence = _plan_evidence("ppkg_new")
    paper = create_paper({
        "title": "Stale brief paper",
        "briefJson": {"research_question": "Old unrelated question"},
    })
    client = FakeClient({
        "research_question": "Evidence research question?",
        "core_claim": "Evidence hypothesis.",
        "paper_angle": "algorithm",
        "target_audience": "reviewers",
        "contributions": ["Evidence contribution"],
        "must_use_evidence": [],
        "must_use_figures": [],
        "section_priorities": {},
        "avoid_claims": [],
    })
    ctx = _ctx_for_paper(
        paper,
        client,
        {"plan_evidence": json.dumps(evidence), "plan_context": "N/A"},
    )

    result = build_brief(ctx, force=False)

    assert client.calls
    assert result.data["paper_brief"]["_evidencePackageId"] == "ppkg_new"
    assert result.data["paper_brief"]["research_question"] == "Evidence research question?"


def test_outline_regenerates_when_existing_is_not_bound_to_plan_evidence():
    evidence = _plan_evidence("ppkg_outline")
    paper = create_paper({
        "title": "Stale outline paper",
        "outlineJson": {
            "title": "Old unrelated outline",
            "sections": [{"id": "intro", "title": "Introduction", "keyPoints": []}],
        },
    })
    client = FakeClient({
        "title": "Evidence aligned outline",
        "authors": ["Auto"],
        "abstract": "This outline follows the evidence package research question and hypothesis.",
        "sections": [
            {
                "id": "intro",
                "title": "Introduction",
                "keyPoints": ["Evidence research question?"],
                "minWords": 600,
                "hasAlgorithm": False,
                "hasEquations": False,
                "numEquations": 0,
                "hasTables": False,
                "hasFigures": False,
                "figureDescriptions": [],
            }
        ],
        "references": [],
        "algorithms": [],
        "contributions": ["Evidence contribution"],
    })
    ctx = _ctx_for_paper(
        paper,
        client,
        {"plan_evidence": json.dumps(evidence), "plan_context": "N/A"},
        paper_brief={"research_question": "Evidence research question?", "_evidencePackageId": "ppkg_outline"},
    )

    result = build_outline(ctx, force=False)

    assert client.calls
    assert result.data["outline"]["_evidencePackageId"] == "ppkg_outline"
    assert result.data["outline"]["title"] == "Evidence aligned outline"


def test_section_prompt_formats_latex_examples_without_key_error():
    eq_req = EQUATION_TEMPLATE.format(n=2)
    table_req = TABLE_TEMPLATE.format(n=1)
    rendered = render_prompt(MethodWriter.prompt_template, {
        "section_title": "Method",
        "section_id": "method",
        "section_kind": "method",
        "section_index": "1",
        "total_sections": "1",
        "paper_type": "algorithm",
        "title": "Example Paper",
        "venue_name": "ICML",
        "abstract": "Abstract",
        "key_points": "[]",
        "contributions": "[]",
        "requirements": "requirements",
        "metrics_data": "metrics",
        "runs_data": "runs",
        "prev_context": "",
        "refs_summary": "vaswani2017attention: Attention is All You Need",
        "min_words": "500",
        "algo_req": "",
        "eq_req": eq_req,
        "table_req": table_req,
        "fig_req": "",
        "figures_data": "N/A",
        "section_figures_data": "N/A",
        "venue_style_guide": "Style",
    })

    assert "\\begin{equation}" in rendered
    assert "\\begin{table}" in rendered
    assert "Write COMPLETE LaTeX content for the method-oriented section" in rendered


def test_paper_skill_context_uses_extended_llm_timeout():
    ctx = PaperSkillContext(
        paper_id="paper_test",
        paper={},
        settings=types.SimpleNamespace(PAPER_GENERATION_TIMEOUT=300),
        provider_name="moonshot",
        model="moonshot-v1-8k",
        paper_type="algorithm",
        venue="generic",
        venue_cfg={"name": "Generic"},
        client=types.SimpleNamespace(config=types.SimpleNamespace(timeout=60)),
        latex_dir="/tmp",
        artifacts_dir="/tmp",
    )

    assert ctx.llm_timeout() == 300
