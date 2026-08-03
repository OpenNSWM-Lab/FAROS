import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.modules.paper.skills.base import PaperSkillContext
from app.modules.paper.skills.compile_pdf import _replace_unicode_latex_chars
from app.modules.paper.skills.section_rewrite import rewrite_section
from app.modules.paper.skills.utils import (
    normalize_duplicate_latex_labels,
    sanitize_latex_text_specials,
)
from app.modules.paper.storage import create_paper, read_paper_file, write_paper_file


class FakeChatResponse:
    def __init__(self, text: str):
        self.text = text


class FakeClient:
    config = types.SimpleNamespace(timeout=60)

    def __init__(self, text: str):
        self.text = text

    def chat(self, messages, **kwargs):
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


def test_duplicate_label_rewrite_updates_later_refs_in_same_section():
    sections = {
        "intro": r"\begin{equation} a=1 \label{eq:main} \end{equation}",
        "results": r"\begin{equation} b=2 \label{eq:main} \end{equation} See \eqref{eq:main}.",
    }

    normalized, rewrites = normalize_duplicate_latex_labels(sections)

    assert rewrites == [{"section": "results", "from": "eq:main", "to": "eq:main:results"}]
    assert r"\label{eq:main:results}" in normalized["results"]
    assert r"\eqref{eq:main:results}" in normalized["results"]
