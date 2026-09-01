import os
import json
import sys
import types
import asyncio
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.modules.paper.skills.base import PaperSkillContext
from app.modules.paper.papers_api import (
    UpdatePaperContextRequest,
    _build_sections_for_fallback_pdf,
    _record_pdf_render_status,
    update_paper_context_endpoint,
)
from app.modules.paper.skills.latex_compile_support import (
    _ensure_required_packages,
    _ensure_xcolor_table_option,
    _redirect_unique_missing_refs,
    _restore_corrupted_superscript_stars,
    preflight_latex_project,
    _replace_unicode_latex_chars,
    latex_log_quality_errors,
)
from app.modules.paper.agents.simple_review import SimpleReviewAgent
from app.modules.paper.agents.writing import PaperWritingAgent
from app.modules.paper.skills.constants import TEMPLATE_ROOT
from app.modules.paper.skills.outline import _fallback_outline, _normalize_outline
from app.modules.paper.skills import plan_evidence
from app.modules.paper.skills.section_writers.dispatcher import classify_section
from app.modules.paper.skills.section_rewrite import rewrite_section
from app.modules.paper.skills.section_rewrite import sanitize_markdown_emphasis_for_latex
from app.modules.paper.skills.section_rewrite import sanitize_markdown_inline_code_for_latex
from app.modules.paper.skills.utils import (
    ensure_section_label,
    get_linked_figure_entries,
    normalize_duplicate_latex_labels,
    sanitize_latex_text_specials,
)
from app.modules.paper.storage import create_paper, delete_paper, get_paper_latex_dir, read_paper_file, write_paper_file
from app.services.pdf_renderer import (
    _register_unicode_font,
    _requires_xelatex,
    _strip_latex,
)


class FakeChatResponse:
    def __init__(self, text: str):
        self.text = text


class FakeClient:
    config = types.SimpleNamespace(timeout=60)

    def __init__(self, text: str):
        self.text = text
        self.messages = []

    def chat(self, messages, **kwargs):
        self.messages = messages
        return FakeChatResponse(self.text)


def _ctx_for_paper(paper, client):
    return PaperSkillContext(
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


def test_sanitize_latex_text_specials_preserves_display_math_subscripts():
    content = r"\begin{equation} E_i = x_i + y_j \end{equation}"

    assert sanitize_latex_text_specials(content) == content


def test_sanitize_latex_text_specials_escapes_text_snake_case_metrics():
    content = "F1_drop and steps_reduction columns remain in prose, but $E_i$ stays math."

    sanitized = sanitize_latex_text_specials(content)

    assert "F1\\_drop" in sanitized
    assert "steps\\_reduction" in sanitized
    assert "$E_i$" in sanitized


def test_preflight_latex_project_escapes_text_snake_case_metrics(tmp_path: Path):
    main_tex = tmp_path / "main.tex"
    main_tex.write_text(
        r"\documentclass{article}\begin{document}\input{sections/results.tex}\end{document}",
        encoding="utf-8",
    )
    sections_dir = tmp_path / "sections"
    sections_dir.mkdir()
    results_tex = sections_dir / "results.tex"
    results_tex.write_text(r"\section{Results} F1_drop and steps_reduction are reported.", encoding="utf-8")

    rewrites = preflight_latex_project(str(tmp_path))

    assert any(rewrite["kind"] == "text_specials" for rewrite in rewrites)
    assert "F1\\_drop" in results_tex.read_text(encoding="utf-8")
    assert "steps\\_reduction" in results_tex.read_text(encoding="utf-8")


def test_preflight_latex_project_preserves_template_macros(tmp_path: Path):
    main_tex = tmp_path / "main.tex"
    source = (
        "\\documentclass{article}\n"
        "\\IfFileExists{missing.sty}{%\n"
        "}{% template fallback\n"
        "  \\providecommand{\\SetKw}[2]{##1: ##2}\n"
        "}\n"
        "\\begin{document}Ok\\end{document}\n"
    )
    main_tex.write_text(source, encoding="utf-8")

    rewrites = preflight_latex_project(str(tmp_path))

    assert main_tex.read_text(encoding="utf-8") == source
    assert not any(rewrite.get("kind") == "text_specials" for rewrite in rewrites)


def test_preflight_latex_project_skips_artifacts(tmp_path: Path):
    main_tex = tmp_path / "main.tex"
    main_tex.write_text(r"\documentclass{article}\begin{document}Ok\end{document}", encoding="utf-8")
    artifacts_dir = tmp_path / "artifacts" / "section_rewrites"
    artifacts_dir.mkdir(parents=True)
    artifact_tex = artifacts_dir / "results.before.tex"
    artifact_tex.write_text("F1_drop should stay as historical output.", encoding="utf-8")

    rewrites = preflight_latex_project(str(tmp_path))

    assert not any("artifacts" in rewrite["file"] for rewrite in rewrites)
    assert artifact_tex.read_text(encoding="utf-8") == "F1_drop should stay as historical output."


def test_section_rewrite_sanitizes_markdown_inline_latex_commands():
    content = r"每个`\If`都有`\EndIf`，并符合`algorithm2e`规范。"

    sanitized, warnings = sanitize_markdown_inline_code_for_latex(content)

    assert "`" not in sanitized
    assert r"\texttt{\textbackslash{}If}" in sanitized
    assert r"\texttt{\textbackslash{}EndIf}" in sanitized
    assert r"\texttt{algorithm2e}" in sanitized
    assert warnings


def test_section_rewrite_sanitizes_markdown_emphasis():
    content = "**Result analysis** shows *stable* behavior."

    sanitized, warnings = sanitize_markdown_emphasis_for_latex(content)

    assert sanitized == r"\textbf{Result analysis} shows \emph{stable} behavior."
    assert warnings


def test_section_rewrite_preserves_math_superscript_stars():
    content = r"Select $e^* \in \mathcal{E}$ and compare $a_i$ with $e^*$."

    sanitized, warnings = sanitize_markdown_emphasis_for_latex(content)

    assert sanitized == content
    assert not warnings


def test_scope_claim_detector_respects_explicit_refrain_from_claiming():
    pattern = (r"\bexternal validation\b",)

    assert SimpleReviewAgent._contains_positive_scope_claim(
        "We completed external validation on a held-out corpus.", pattern,
    ) is True
    assert SimpleReviewAgent._contains_positive_scope_claim(
        "We explicitly refrain from claiming external validation beyond this controlled setting.",
        pattern,
    ) is False


def test_final_scope_repair_removes_unsupported_significance_wording():
    paper = create_paper({"title": "Final scope repair"})
    try:
        write_paper_file(
            paper["id"],
            "sections/results.tex",
            r"\section{Results} FCS significantly improves ECE on this run.",
        )
        ctx = _ctx_for_paper(paper, FakeClient(""))
        review = {
            "issues": [{
                "severity": "blocking",
                "path": "sections/results.tex",
                "message": "Metric consistency gate: uses statistical-significance language without repeated-run inferential statistics",
            }],
        }

        repairs = SimpleReviewAgent._apply_final_scope_deterministic_repairs(ctx, review)

        rewritten = read_paper_file(paper["id"], "sections/results.tex")
        assert "significantly" not in rewritten
        assert "improves ECE" in rewritten
        assert repairs[0]["deterministic"] is True
    finally:
        delete_paper(paper["id"])


def test_preflight_restores_superscript_stars_corrupted_by_markdown_emphasis(tmp_path: Path):
    corrupted = (
        r"Select $e^\emph{ \in \mathcal{E}$. The relevance of $e^} "
        r"is scored, then compare $a_i$ with $e^*$."
    )
    restored, rewrites = _restore_corrupted_superscript_stars(corrupted)

    assert restored == (
        r"Select $e^* \in \mathcal{E}$. The relevance of $e^* "
        r"is scored, then compare $a_i$ with $e^*$."
    )
    assert rewrites[0]["kind"] == "restore_superscript_star"

    (tmp_path / "main.tex").write_text(
        r"\documentclass{article}\begin{document}\input{sections/method.tex}\end{document}",
        encoding="utf-8",
    )
    sections = tmp_path / "sections"
    sections.mkdir()
    method = sections / "method.tex"
    method.write_text(corrupted, encoding="utf-8")

    project_rewrites = preflight_latex_project(str(tmp_path))

    assert any(item["kind"] == "restore_superscript_star" for item in project_rewrites)
    assert r"^\emph{" not in method.read_text(encoding="utf-8")


def test_preflight_redirects_dangling_ref_to_one_suffixed_label(tmp_path: Path):
    sections = {
        "results": r"See Table~\ref{tab:main_results}.",
        "analysis": r"\begin{table}\label{tab:main_results:analysis}Data\end{table}",
    }

    normalized, rewrites = _redirect_unique_missing_refs(sections)

    assert r"\ref{tab:main_results:analysis}" in normalized["results"]
    assert rewrites == [{
        "section": "results",
        "from": "tab:main_results",
        "to": "tab:main_results:analysis",
    }]

    (tmp_path / "main.tex").write_text(
        r"\documentclass{article}\begin{document}\input{sections/results}\input{sections/analysis}\end{document}",
        encoding="utf-8",
    )
    section_dir = tmp_path / "sections"
    section_dir.mkdir()
    for section_id, content in sections.items():
        (section_dir / f"{section_id}.tex").write_text(content, encoding="utf-8")

    project_rewrites = preflight_latex_project(str(tmp_path))

    assert any(item["kind"] == "dangling_ref" for item in project_rewrites)
    assert r"\ref{tab:main_results:analysis}" in (
        section_dir / "results.tex"
    ).read_text(encoding="utf-8")


def test_preflight_unicode_replacement_respects_display_math():
    content = r"\begin{equation} α_i = β_i \end{equation}"

    normalized, rewrites = _replace_unicode_latex_chars(content)

    assert normalized == r"\begin{equation} \alpha_i = \beta_i \end{equation}"
    assert rewrites


def test_section_rewrite_preserve_options_block_dropped_citations_and_figures():
    paper = create_paper({
        "title": "Preserve rewrite paper",
        "outlineJson": {
            "title": "Preserve rewrite paper",
            "abstract": "Abstract",
            "sections": [{"id": "method", "title": "Method", "minWords": 200}],
            "references": [{"key": "smith2024", "title": "Smith"}],
        },
    })
    original = (
        r"\section{Method}"
        "\n"
        r"Important result \cite{smith2024}."
        "\n"
        r"\includegraphics{figures/kept.pdf}"
    )
    write_paper_file(paper["id"], "sections/method.tex", original)
    ctx = _ctx_for_paper(paper, FakeClient(r"\section{Method} Rewritten without protected material."))

    with pytest.raises(ValueError, match="dropped citation keys"):
        rewrite_section(ctx, "method", preserve_citations=True, preserve_figures=True)

    assert read_paper_file(paper["id"], "sections/method.tex") == original


def test_write_paper_file_rejects_paths_outside_latex_dir(tmp_path: Path):
    paper = create_paper({"title": "Storage boundary paper"})
    try:
        write_paper_file(paper["id"], "sections/method.tex", "ok")
        assert read_paper_file(paper["id"], "sections/method.tex") == "ok"

        with pytest.raises(ValueError, match="outside paper LaTeX directory"):
            write_paper_file(paper["id"], "../escape.tex", "bad")

        outside = tmp_path / "escape.tex"
        with pytest.raises(ValueError, match="outside paper LaTeX directory"):
            write_paper_file(paper["id"], str(outside), "bad")

        assert not outside.exists()
        assert not os.path.exists(os.path.join(os.path.dirname(get_paper_latex_dir(paper["id"])), "escape.tex"))
    finally:
        delete_paper(paper["id"])


def test_section_rewrite_does_not_block_dropped_figures(tmp_path: Path):
    paper = create_paper({
        "title": "Dropped figure rewrite paper",
        "outlineJson": {
            "title": "Dropped figure rewrite paper",
            "abstract": "Abstract",
            "sections": [{"id": "method", "title": "Method", "minWords": 200}],
            "references": [],
        },
    })
    original = (
        r"\section{Method}"
        "\n"
        r"\includegraphics{figures/existing.pdf}"
    )
    write_paper_file(paper["id"], "sections/method.tex", original)
    ctx = _ctx_for_paper(paper, FakeClient(r"\section{Method} Rewritten without the unavailable placeholder."))
    ctx.latex_dir = str(tmp_path)
    (tmp_path / "figures").mkdir()
    (tmp_path / "figures" / "existing.pdf").write_bytes(b"%PDF-1.4\n")

    result = rewrite_section(ctx, "method", preserve_citations=True, preserve_figures=True)

    assert result.data["content"] == r"\section{Method} Rewritten without the unavailable placeholder."
    assert "existing.pdf" not in read_paper_file(paper["id"], "sections/method.tex")


def test_section_rewrite_escapes_text_snake_case_metrics():
    paper = create_paper({
        "title": "Snake case rewrite paper",
        "outlineJson": {
            "title": "Snake case rewrite paper",
            "abstract": "Abstract",
            "sections": [{"id": "results", "title": "Results", "minWords": 200}],
            "references": [],
        },
    })
    write_paper_file(paper["id"], "sections/results.tex", r"\section{Results} Old text.")
    ctx = _ctx_for_paper(paper, FakeClient(r"\section{Results} F1_drop and steps_reduction remain prose."))

    result = rewrite_section(ctx, "results", preserve_citations=True, preserve_figures=True)

    assert "F1\\_drop" in result.data["content"]
    assert "steps\\_reduction" in read_paper_file(paper["id"], "sections/results.tex")


def test_section_rewrite_sends_the_complete_section_to_the_model():
    paper = create_paper({
        "title": "Long rewrite paper",
        "outlineJson": {
            "title": "Long rewrite paper",
            "abstract": "Abstract",
            "sections": [{"id": "method", "title": "Method", "minWords": 200}],
            "references": [],
        },
    })
    tail_marker = "TAIL_MARKER_MUST_REACH_MODEL"
    original = r"\section{Method}" + "\n" + ("Long section content. " * 500) + tail_marker
    write_paper_file(paper["id"], "sections/method.tex", original)
    client = FakeClient(r"\section{Method} Complete rewritten section.")
    ctx = _ctx_for_paper(paper, client)

    rewrite_section(ctx, "method", preserve_citations=False, preserve_figures=False)

    assert tail_marker in client.messages[0].content


def test_feedback_rewrite_synchronizes_abstract_into_main_tex():
    paper = create_paper({
        "title": "Abstract synchronization paper",
        "outlineJson": {
            "title": "Abstract synchronization paper",
            "abstract": "Stale abstract.",
            "sections": [{"id": "abstract", "title": "Abstract", "minWords": 50}],
            "references": [],
        },
    })
    try:
        write_paper_file(paper["id"], "sections/abstract.tex", r"\section{Abstract} Stale abstract.")
        write_paper_file(
            paper["id"],
            "main.tex",
            r"\begin{abstract}Stale abstract.\end{abstract}",
        )
        ctx = _ctx_for_paper(
            paper,
            FakeClient(r"\section{Abstract}\label{sec:abstract} Corrected evidence-grounded abstract."),
        )
        agent = PaperWritingAgent(paper["id"], lambda _message: None)

        agent.apply_feedback(ctx, "evidence_usage", [{
            "issues": [{
                "path": "sections/abstract.tex",
                "severity": "blocking",
                "message": "Correct the metric statement.",
            }],
        }])

        main_tex = read_paper_file(paper["id"], "main.tex")
        assert "Corrected evidence-grounded abstract." in main_tex
        assert "Stale abstract." not in main_tex
        assert r"\section{Abstract}" not in main_tex
        assert r"\label{sec:abstract}" not in main_tex
    finally:
        delete_paper(paper["id"])


def test_evidence_feedback_removes_unsupported_significance_wording():
    paper = create_paper({
        "title": "Significance wording paper",
        "outlineJson": {
            "title": "Significance wording paper",
            "abstract": "Abstract",
            "sections": [{"id": "results", "title": "Results", "minWords": 50}],
            "references": [],
        },
    })
    try:
        write_paper_file(paper["id"], "sections/results.tex", r"\section{Results} Old text.")
        ctx = _ctx_for_paper(
            paper,
            FakeClient(r"\section{Results} FCS significantly improves calibration."),
        )
        agent = PaperWritingAgent(paper["id"], lambda _message: None)

        agent.apply_feedback(ctx, "evidence_usage", [{
            "issues": [{
                "path": "sections/results.tex",
                "severity": "blocking",
                "message": "Uses statistical-significance language without inferential statistics.",
            }],
        }])

        rewritten = read_paper_file(paper["id"], "sections/results.tex")
        assert "significantly" not in rewritten
        assert "improves calibration" in rewritten
    finally:
        delete_paper(paper["id"])


def test_duplicate_label_rewrite_updates_later_refs_in_same_section():
    sections = {
        "intro": r"\begin{equation} a=1 \label{eq:main} \end{equation}",
        "results": r"\begin{equation} b=2 \label{eq:main} \end{equation} See \eqref{eq:main}.",
    }

    normalized, rewrites = normalize_duplicate_latex_labels(sections)

    assert rewrites == [{"section": "results", "from": "eq:main", "to": "eq:main:results"}]
    assert r"\label{eq:main:results}" in normalized["results"]
    assert r"\eqref{eq:main:results}" in normalized["results"]


def test_generated_section_gets_stable_cross_reference_label():
    normalized, changed = ensure_section_label(
        "\\section{Experiments}\nBody.", "experiments",
    )

    assert changed is True
    assert "\\section{Experiments}\n\\label{sec:experiments}" in normalized
    assert ensure_section_label(normalized, "experiments") == (normalized, False)


def test_latex_quality_gate_detects_unresolved_references():
    errors = latex_log_quality_errors(
        "LaTeX Warning: Reference `sec:missing' on page 1 undefined.\n"
        "LaTeX Warning: There were undefined references.\n"
    )

    assert errors == [
        "undefined references or citations remain",
        "undefined references remain",
    ]


def test_simple_review_drops_duplicate_label_claim_not_present_in_sources():
    review = {
        "passed": False,
        "issues": [{
            "severity": "major",
            "path": "sections/results.tex",
            "message": "Duplicated label 'tab:main_results' is defined twice.",
        }],
        "targets": [{"path": "sections/results.tex", "instruction": "Rename it."}],
    }

    filtered = SimpleReviewAgent._filter_source_inconsistent_issues(
        review,
        {"results": "\\section{Results}\\label{tab:results_main}"},
    )

    assert filtered["passed"] is True
    assert filtered["issues"] == []
    assert filtered["targets"] == []


def test_simple_review_drops_abstract_inclusion_claim_when_main_does_not_include_it():
    review = {
        "passed": False,
        "issues": [{
            "severity": "blocking",
            "path": "sections/abstract.tex",
            "message": "Malformed LaTeX structure: duplicate Abstract section.",
        }],
        "targets": [{"path": "sections/abstract.tex", "instruction": "Remove it."}],
    }

    filtered = SimpleReviewAgent._filter_source_inconsistent_issues(
        review,
        {"abstract": "\\section{Abstract} Body."},
        "\\begin{abstract}Inline body.\\end{abstract}\n\\input{sections/introduction.tex}",
    )

    assert filtered["passed"] is True
    assert filtered["issues"] == []
    assert filtered["targets"] == []


def test_simple_review_drops_main_abstract_input_claim_when_source_has_no_input():
    review = {
        "passed": False,
        "issues": [{
            "severity": "minor",
            "path": "main.tex",
            "message": "Redundant input: sections/abstract.tex is included twice.",
        }],
        "targets": [],
    }

    filtered = SimpleReviewAgent._filter_source_inconsistent_issues(
        review,
        {"abstract": r"\section{Abstract} Body."},
        r"\begin{abstract}Inline body.\end{abstract}",
    )

    assert filtered["passed"] is True
    assert filtered["issues"] == []


def test_simple_review_drops_compile_claim_after_successful_latexmk():
    review = {
        "passed": False,
        "issues": [{
            "severity": "blocking",
            "path": "main.tex",
            "message": "Undefined control sequence would cause a compilation error.",
        }],
        "targets": [{"path": "main.tex", "instruction": "Repair the command."}],
    }

    filtered = SimpleReviewAgent._filter_source_inconsistent_issues(
        review, {}, "", compile_status="latexmk",
    )

    assert filtered["passed"] is True
    assert filtered["issues"] == []
    assert filtered["targets"] == []


def test_paper_evidence_scope_blocks_invented_human_validation(tmp_path: Path):
    agent = SimpleReviewAgent("paper_scope", lambda _message: None)
    ctx = PaperSkillContext(
        paper_id="paper_scope",
        paper={
            "evidenceConstraints": [
                "Use a deterministic fixed-seed synthetic benchmark generated locally",
                "Do not claim external, real-world, or human validation",
            ],
        },
        settings=types.SimpleNamespace(),
        provider_name="fake",
        model="fake",
        paper_type="algorithm",
        venue="generic",
        venue_cfg={"name": "Generic"},
        client=FakeClient("{}"),
        latex_dir=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
    )
    sections = tmp_path / "sections"
    sections.mkdir()
    (sections / "experiments.tex").write_text(
        "\\section{Experiments} Human annotators possessing domain expertise labeled each claim.",
        encoding="utf-8",
    )

    review = agent._evidence_usage_review(ctx)

    assert review["passed"] is False
    assert review["issues"][0]["severity"] == "blocking"
    assert review["targets"][0]["path"] == "sections/experiments.tex"


def test_paper_evidence_scope_allows_negated_limits_and_future_validation(tmp_path: Path):
    agent = SimpleReviewAgent("paper_scope_limits", lambda _message: None)
    ctx = PaperSkillContext(
        paper_id="paper_scope_limits",
        paper={
            "evidenceConstraints": [
                "Use a deterministic fixed-seed synthetic benchmark generated locally",
                "Do not claim external, real-world, or human validation",
            ],
        },
        settings=types.SimpleNamespace(),
        provider_name="fake",
        model="fake",
        paper_type="algorithm",
        venue="generic",
        venue_cfg={"name": "Generic"},
        client=FakeClient("{}"),
        latex_dir=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
    )
    sections = tmp_path / "sections"
    sections.mkdir()
    (sections / "experiments.tex").write_text(
        "\\section{Experiments} This does not constitute external validation. "
        "No human annotations or real-world datasets were used. "
        "The absence of human annotation is a limitation of this experiment. "
        "Future work must evaluate on human-annotated datasets. "
        "The method can be tested against diverse real-world datasets in future work. "
        "We use controlled synthetic artifacts rather than external, real-world datasets. "
        "Deployment is pending validation on diverse real-world datasets.",
        encoding="utf-8",
    )

    review = agent._evidence_usage_review(ctx)

    assert review["passed"] is True
    assert review["issues"] == []


def test_paper_evidence_scope_allows_human_annotation_as_prior_work(tmp_path: Path):
    agent = SimpleReviewAgent("paper_scope_prior_work", lambda _message: None)
    ctx = PaperSkillContext(
        paper_id="paper_scope_prior_work",
        paper={
            "evidenceConstraints": [
                "Use a deterministic fixed-seed synthetic benchmark generated locally",
                "Do not claim external, real-world, or human validation",
            ],
        },
        settings=types.SimpleNamespace(),
        provider_name="fake",
        model="fake",
        paper_type="algorithm",
        venue="generic",
        venue_cfg={"name": "Generic"},
        client=FakeClient("{}"),
        latex_dir=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
    )
    sections = tmp_path / "sections"
    sections.mkdir()
    (sections / "related_work.tex").write_text(
        "\\section{Related Work} Prior methods require extensive human annotation "
        "to define atomic claims \\cite{prior2025}. Many studies rely on external "
        "real-world datasets. Our evaluation instead uses a local synthetic benchmark.",
        encoding="utf-8",
    )

    review = agent._evidence_usage_review(ctx)

    assert review["passed"] is True
    assert review["issues"] == []


def test_paper_metric_gate_rejects_invented_values_and_wrong_direction(tmp_path: Path):
    agent = SimpleReviewAgent("paper_metric_gate", lambda _message: None)
    metrics = [
        {"name": "baseline:Brier Score", "value": 0.101843},
        {"name": "method:Brier Score", "value": 0.107222},
        {"name": "baseline:Expected Calibration Error (ECE)", "value": 0.269982},
        {"name": "method:Expected Calibration Error (ECE)", "value": 0.225950},
        {"name": "ablation_numeric:Brier Score", "value": 0.098054},
        {"name": "ablation_numeric:Expected Calibration Error (ECE)", "value": 0.219825},
    ]
    ctx = PaperSkillContext(
        paper_id="paper_metric_gate",
        paper={
            "evidenceJson": {
                "codeEvidence": {"runs": [{"metrics": metrics}]},
            },
        },
        settings=types.SimpleNamespace(),
        provider_name="fake",
        model="fake",
        paper_type="algorithm",
        venue="generic",
        venue_cfg={"name": "Generic"},
        client=FakeClient("{}"),
        latex_dir=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
    )
    sections = tmp_path / "sections"
    sections.mkdir()
    (sections / "results.tex").write_text(
        "\\section{Results}\n"
        "FCS achieves a concurrent improvement in Brier Score over the baseline.\n"
        "Ablation (No Numeric) & 0.1150 & 0.2450 \\\\\n"
        "The ECE was significantly reduced to 0.2259.\n",
        encoding="utf-8",
    )
    (tmp_path / "main.tex").write_text(
        "Our method shows a concurrent improvement in Brier Score over the baseline.",
        encoding="utf-8",
    )

    review = agent._metric_consistency_review(ctx, agent._read_sections(ctx))

    assert review["passed"] is False
    message = review["issues"][0]["message"]
    assert "0.1150" in message and "0.2450" in message
    assert "BRIER improvement" in message
    assert "statistical-significance" in message
    evidence_review = agent._evidence_usage_review(ctx)
    assert any(issue["path"] == "main.tex" for issue in evidence_review["issues"])


def test_paper_metric_gate_accepts_observed_tradeoff(tmp_path: Path):
    agent = SimpleReviewAgent("paper_metric_tradeoff", lambda _message: None)
    metrics = [
        {"name": "baseline:Brier Score", "value": 0.101843},
        {"name": "method:Brier Score", "value": 0.107222},
        {"name": "baseline:Expected Calibration Error (ECE)", "value": 0.269982},
        {"name": "method:Expected Calibration Error (ECE)", "value": 0.225950},
    ]
    ctx = PaperSkillContext(
        paper_id="paper_metric_tradeoff",
        paper={"evidenceJson": {"codeEvidence": {"runs": [{"metrics": metrics}]}}},
        settings=types.SimpleNamespace(),
        provider_name="fake",
        model="fake",
        paper_type="algorithm",
        venue="generic",
        venue_cfg={"name": "Generic"},
        client=FakeClient("{}"),
        latex_dir=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
    )
    sections = tmp_path / "sections"
    sections.mkdir()
    (sections / "results.tex").write_text(
        "\\section{Results} The method lowers ECE from 0.2700 to 0.2259, "
        "while Brier Score worsens from 0.1018 to 0.1072.",
        encoding="utf-8",
    )

    review = agent._metric_consistency_review(ctx, agent._read_sections(ctx))

    assert review["passed"] is True


def test_paper_metric_gate_accepts_truncated_values_and_derived_delta(tmp_path: Path):
    agent = SimpleReviewAgent("paper_metric_delta", lambda _message: None)
    metrics = [
        {"name": "baseline:F1-Score", "value": 0.9043869516310461},
        {"name": "method:F1-Score", "value": 0.7855361596009975},
        {"name": "baseline:AUROC", "value": 0.9996637727103522},
        {"name": "method:AUROC", "value": 0.999599729417086},
    ]
    ctx = PaperSkillContext(
        paper_id="paper_metric_delta",
        paper={"evidenceJson": {"codeEvidence": {"runs": [{"metrics": metrics}]}}},
        settings=types.SimpleNamespace(),
        provider_name="fake",
        model="fake",
        paper_type="algorithm",
        venue="generic",
        venue_cfg={"name": "Generic"},
        client=FakeClient("{}"),
        latex_dir=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
    )
    sections = tmp_path / "sections"
    sections.mkdir()
    (sections / "results.tex").write_text(
        "\\section{Results} F1 decreases from 0.904 to 0.785, a delta of 0.1189. "
        "AUROC remains approximately 0.999.",
        encoding="utf-8",
    )

    review = agent._metric_consistency_review(ctx, agent._read_sections(ctx))

    assert review["passed"] is True


def test_paper_metric_gate_does_not_treat_ablation_brier_as_method_claim(tmp_path: Path):
    agent = SimpleReviewAgent("paper_metric_ablation", lambda _message: None)
    metrics = [
        {"name": "baseline:Brier Score", "value": 0.101843},
        {"name": "method:Brier Score", "value": 0.107222},
        {"name": "ablation_numeric:Brier Score", "value": 0.098054},
    ]
    ctx = PaperSkillContext(
        paper_id="paper_metric_ablation",
        paper={"evidenceJson": {"codeEvidence": {"runs": [{"metrics": metrics}]}}},
        settings=types.SimpleNamespace(),
        provider_name="fake",
        model="fake",
        paper_type="algorithm",
        venue="generic",
        venue_cfg={"name": "Generic"},
        client=FakeClient("{}"),
        latex_dir=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
    )
    sections = tmp_path / "sections"
    sections.mkdir()
    (sections / "results.tex").write_text(
        "\\section{Results} The Brier score improved in the ablation (0.0981) "
        "compared with the full method (0.1072) and baseline (0.1018).",
        encoding="utf-8",
    )

    review = agent._metric_consistency_review(ctx, agent._read_sections(ctx))

    assert review["passed"] is True


def test_paper_metric_gate_routes_inline_main_abstract_to_abstract_section(tmp_path: Path):
    agent = SimpleReviewAgent("paper_metric_main_abstract", lambda _message: None)
    metrics = [
        {"name": "baseline:Expected Calibration Error (ECE)", "value": 0.269982},
        {"name": "method:Expected Calibration Error (ECE)", "value": 0.225950},
    ]
    ctx = PaperSkillContext(
        paper_id="paper_metric_main_abstract",
        paper={"evidenceJson": {"codeEvidence": {"runs": [{"metrics": metrics}]}}},
        settings=types.SimpleNamespace(), provider_name="fake", model="fake",
        paper_type="algorithm", venue="generic", venue_cfg={"name": "Generic"},
        client=FakeClient("{}"), latex_dir=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
    )
    review = agent._metric_consistency_review(ctx, {
        "abstract": r"\section{Abstract} ECE decreases from 0.270 to 0.226.",
        "__main__": (
            r"\begin{abstract}The method significantly reduces ECE from 0.270 to 0.226."
            r"\end{abstract}"
        ),
    })

    assert review["passed"] is False
    assert {issue["path"] for issue in review["issues"]} == {"sections/abstract.tex"}


def test_paper_evidence_falls_back_to_faros_research_dossier(
    tmp_path: Path, monkeypatch,
):
    dossier_path = tmp_path / "research_dossier.json"
    dossier_path.write_text(json.dumps({
        "runId": "faros_test",
        "questionId": "question_test",
        "problemFrame": {"scopedQuestion": "Does hallucination detection improve calibration?"},
        "evidenceMap": {
            "consensus": ["Calibration should be evaluated."],
            "disputedClaims": ["External validity is unresolved."],
            "supportingEvidence": [{
                "id": "lit_irrelevant",
                "title": "AI Generated Portfolio Selection",
                "authors": ["I. Author"],
                "year": 2025,
                "url": "https://example.test/portfolio",
                "verified": True,
                "relevanceScore": 1.0,
            }, {
                "id": "lit_support",
                "title": "Calibrated Hallucination Detection Methods",
                "authors": ["A. Author"],
                "year": 2024,
                "doi": "10.1000/support",
                "verified": True,
                "relevanceScore": 0.9,
            }],
            "counterEvidence": [{
                "id": "lit_counter",
                "title": "Counter Paper",
                "authors": ["B. Author"],
                "year": 2023,
                "url": "https://example.test/counter",
                "verified": True,
                "relevanceScore": 0.8,
            }],
            "contextualEvidence": [],
            "unresolvedGaps": ["Human validation remains future work."],
        },
        "hypotheses": [{"statement": "Calibration improves."}],
        "researchPlan": {"steps": [{"id": "evaluate"}]},
    }), encoding="utf-8")
    monkeypatch.setattr(plan_evidence, "_data_dir", lambda: str(tmp_path))

    evidence = plan_evidence.collect_plan_evidence_for_paper({
        "researchDossierPath": str(dossier_path),
    })

    assert evidence["status"] == "collected"
    assert evidence["resolution"]["source"] == "faros_research_dossier"
    assert evidence["researchQuestion"] == "Does hallucination detection improve calibration?"
    assert evidence["hypothesis"] == "Calibration improves."
    assert evidence["literature"]["keyPapers"][0]["title"] == (
        "Calibrated Hallucination Detection Methods"
    )
    assert evidence["literature"]["keyPapers"][0]["url"] == (
        "https://doi.org/10.1000/support"
    )


def test_challenge_cup_template_uses_ctex_not_cjkutf8():
    template = (TEMPLATE_ROOT / "challenge_cup" / "main.tex").read_text(encoding="utf-8")

    assert r"\documentclass[UTF8,12pt]{ctexart}" in template
    assert "CJKutf8" not in template
    assert r"\begin{CJK" not in template


def test_ctex_template_triggers_xelatex(tmp_path: Path):
    main_tex = tmp_path / "main.tex"
    main_tex.write_text(r"\documentclass[UTF8,12pt]{ctexart}\begin{document}中文\end{document}", encoding="utf-8")

    assert _requires_xelatex(str(main_tex))


def test_fallback_latex_stripper_can_preserve_chinese_text():
    content = r"\section{方法} 本研究提出中文方案。"

    assert "中文方案" in _strip_latex(content, preserve_unicode=True)
    assert "?" in _strip_latex(content, preserve_unicode=False)


def test_pdf_renderer_combines_dejavu_and_droid_fallback_fonts(monkeypatch):
    available = {
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    }

    class FakePDF:
        def __init__(self):
            self.fonts = []
            self.fallback = None

        def add_font(self, family, style, path):
            self.fonts.append((family, style, path))

        def set_fallback_fonts(self, families, exact_match=True):
            self.fallback = (families, exact_match)

    monkeypatch.delenv("FAROS_PDF_FONT", raising=False)
    monkeypatch.setattr(os.path, "isfile", lambda path: path in available)
    pdf = FakePDF()

    assert _register_unicode_font(pdf) == "FarosUnicode"
    assert pdf.fonts == [
        (
            "FarosUnicode",
            "",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ),
        (
            "FarosCJKFallback",
            "",
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
        ),
    ]
    assert pdf.fallback == (["FarosCJKFallback"], False)


def test_explicit_selected_figures_do_not_fallback_to_figure_ids_when_all_excluded():
    paper = {
        "id": "paper_test",
        "figureIds": ["fig_should_not_return"],
        "selectedFiguresExplicit": True,
        "selectedFigures": [
            {"figureId": "fig_a", "filename": "fig_a", "caption": "A", "include": False},
        ],
    }

    assert get_linked_figure_entries(paper) == []


def test_empty_explicit_selected_figures_do_not_fallback_to_figure_ids():
    paper = {
        "id": "paper_test",
        "figureIds": ["fig_should_not_return"],
        "selectedFiguresExplicit": True,
        "selectedFigures": [],
    }

    assert get_linked_figure_entries(paper) == []


def test_xcolor_table_preflight_upgrades_existing_package_instead_of_duplicating():
    main_tex = r"\documentclass{article}" "\n" r"\usepackage{xcolor}" "\n" r"\begin{document}"
    combined_tex = main_tex + "\n" + r"\rowcolor{gray!10}"

    upgraded, rewrites = _ensure_xcolor_table_option(main_tex, combined_tex)
    normalized, package_rewrites = _ensure_required_packages(upgraded, combined_tex)

    assert r"\usepackage[table]{xcolor}" in normalized
    assert normalized.count("xcolor") == 1
    assert rewrites
    assert not package_rewrites


def test_preflight_normalizes_duplicate_labels_after_section_rewrite(tmp_path: Path):
    sections = tmp_path / "sections"
    sections.mkdir()
    (tmp_path / "main.tex").write_text(
        r"\documentclass{article}\begin{document}\input{sections/a}\input{sections/b}\end{document}",
        encoding="utf-8",
    )
    (sections / "a.tex").write_text(
        r"\section{A}\begin{table}\label{tab:result}A\end{table}", encoding="utf-8",
    )
    (sections / "b.tex").write_text(
        r"\section{B}\begin{table}\label{tab:result}B\end{table} See \ref{tab:result}.",
        encoding="utf-8",
    )

    rewrites = preflight_latex_project(str(tmp_path))
    rewritten = (sections / "b.tex").read_text(encoding="utf-8")

    assert r"\label{tab:result:b}" in rewritten
    assert r"\ref{tab:result:b}" in rewritten
    assert any(item.get("kind") == "duplicate_label" for item in rewrites)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("引言", "introduction"),
        ("相关工作", "related_work"),
        ("技术路线与总体设计", "method"),
        ("实验结果与验证", "experiments"),
        ("结果分析与讨论", "experiments"),
        ("结论与展望", "conclusion"),
    ],
)
def test_chinese_section_titles_route_to_specific_writers(title: str, expected: str):
    assert classify_section({"title": title}) == expected


def test_outline_normalization_preserves_outline_authors_when_record_has_none():
    paper = {"id": "paper_author", "title": "Author Paper", "authors": []}
    ctx = _ctx_for_paper(paper, FakeClient(""))

    normalized = _normalize_outline({"title": "Author Paper", "authors": ["Alice", "Bob"]}, ctx)

    assert normalized["authors"] == ["Alice", "Bob"]


def test_fallback_outline_does_not_inject_default_nlp_references():
    paper = {"id": "paper_refs", "title": "Robotics Scheduling", "authors": []}
    ctx = _ctx_for_paper(paper, FakeClient(""))

    outline = _fallback_outline(ctx, {"research_question": "robot scheduling"}, [])

    assert outline["references"] == []


def test_context_patch_does_not_clear_authors_when_omitted():
    paper = create_paper({
        "title": "Context patch paper",
        "authors": ["Alice", "Bob"],
        "experimentIds": ["exp_old"],
    })

    updated = asyncio.run(update_paper_context_endpoint(
        paper["id"],
        UpdatePaperContextRequest(notes="Keep the authors"),
    ))

    assert updated["notes"] == "Keep the authors"
    assert updated["authors"] == ["Alice", "Bob"]
    assert updated["experimentIds"] == ["exp_old"]


def test_fallback_pdf_sections_follow_outline_titles_and_order():
    outline = {
        "sections": [
            {"id": "method", "title": "研究方法"},
            {"id": "intro", "title": "引言"},
        ]
    }
    files = [
        {"path": "sections/intro.tex", "isDir": False},
        {"path": "sections/extra.tex", "isDir": False},
        {"path": "sections/method.tex", "isDir": False},
    ]
    contents = {
        "sections/intro.tex": "Intro content",
        "sections/method.tex": "Method content",
        "sections/extra.tex": "Extra content",
    }

    sections = _build_sections_for_fallback_pdf(
        outline,
        "paper_fallback",
        files,
        lambda _paper_id, path: contents[path],
    )

    assert sections == [
        {"title": "研究方法", "content": "Method content"},
        {"title": "引言", "content": "Intro content"},
        {"title": "Extra", "content": "Extra content"},
    ]


def test_pdf_render_status_distinguishes_fallback_preview():
    paper = create_paper({"title": "PDF status paper"})
    try:
        _record_pdf_render_status(
            paper["id"],
            pdf_available=True,
            compile_status="failed",
            pdf_render_mode="fallback",
            compile_errors="latexmk failed",
        )

        from app.modules.paper.storage import get_paper

        updated = get_paper(paper["id"])
        assert updated["pdfAvailable"] is True
        assert updated["compileStatus"] == "failed"
        assert updated["pdfRenderMode"] == "fallback"
        assert updated["compileErrors"] == "latexmk failed"
    finally:
        delete_paper(paper["id"])
