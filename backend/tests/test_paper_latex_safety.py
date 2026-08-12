import os
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
    preflight_latex_project,
    _replace_unicode_latex_chars,
)
from app.modules.paper.skills.constants import TEMPLATE_ROOT
from app.modules.paper.skills.outline import _fallback_outline, _normalize_outline
from app.modules.paper.skills.section_writers.dispatcher import classify_section
from app.modules.paper.skills.section_rewrite import rewrite_section
from app.modules.paper.skills.section_rewrite import sanitize_markdown_emphasis_for_latex
from app.modules.paper.skills.section_rewrite import sanitize_markdown_inline_code_for_latex
from app.modules.paper.skills.utils import (
    get_linked_figure_entries,
    normalize_duplicate_latex_labels,
    sanitize_latex_text_specials,
)
from app.modules.paper.storage import create_paper, delete_paper, get_paper_latex_dir, read_paper_file, write_paper_file
from app.services.pdf_renderer import _requires_xelatex, _strip_latex


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


def test_duplicate_label_rewrite_updates_later_refs_in_same_section():
    sections = {
        "intro": r"\begin{equation} a=1 \label{eq:main} \end{equation}",
        "results": r"\begin{equation} b=2 \label{eq:main} \end{equation} See \eqref{eq:main}.",
    }

    normalized, rewrites = normalize_duplicate_latex_labels(sections)

    assert rewrites == [{"section": "results", "from": "eq:main", "to": "eq:main:results"}]
    assert r"\label{eq:main:results}" in normalized["results"]
    assert r"\eqref{eq:main:results}" in normalized["results"]


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
