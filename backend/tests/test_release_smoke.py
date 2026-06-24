import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

pytest.importorskip("sqlmodel")

from fastapi.testclient import TestClient

from app.main import app
from app.modules.paper.storage import (
    create_paper,
    get_selected_figures,
    remove_selected_figure,
    select_figure_for_paper,
    update_selected_figures,
)
from app.modules.paper.skills.constants import TEMPLATE_ROOT
from app.modules.paper.skills.section_writers import classify_section, get_section_writer, split_figures_for_section
from app.modules.paper.skills.section_writers.base import render_prompt
from app.modules.paper.skills.utils import (
    build_bibtex,
    dedupe_figure_entries,
    figure_record_to_entry,
    load_venue_style_guide,
    normalize_duplicate_latex_labels,
    normalize_bibtex_authors,
    sanitize_latex_text_specials,
    normalize_section_citations,
    normalize_section_figure_references,
)
from app.storage.experiment_storage import create_experiment, save_figure_artifact
from app.version import APP_NAME, APP_VERSION, API_VERSION, CAPABILITIES, RELEASE_PHASE, SERVICE_NAME

client = TestClient(app)


def test_app_metadata_matches_version_module():
    assert app.title == APP_NAME
    assert app.version == APP_VERSION


def test_health_endpoint_version_is_consistent():
    response = client.get("/api/system/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == SERVICE_NAME
    assert payload["version"] == APP_VERSION


def test_version_endpoint_payload_is_consistent():
    response = client.get("/api/system/version")
    assert response.status_code == 200
    payload = response.json()
    assert payload["api_version"] == API_VERSION
    assert payload["backend_version"] == APP_VERSION
    assert payload["phase"] == RELEASE_PHASE
    assert payload["capabilities"] == CAPABILITIES


def test_core_domain_routes_are_mounted():
    paths = {route.path for route in app.routes}
    expected_prefixes = [
        "/api/faros",
        "/api/v1/ideas",
        "/api/v1/code/sessions",
        "/api/v1/code/projects",
        "/api/v1/papers",
        "/api/v1/reviews",
        "/api/v1/runs",
    ]
    for prefix in expected_prefixes:
        assert any(path == prefix or path.startswith(prefix + "/") for path in paths), prefix


def test_paper_selected_figure_and_rewrite_routes_are_mounted():
    paths = {route.path for route in app.routes}

    assert "/api/v1/papers/{paper_id}/selected-figures" in paths
    assert "/api/v1/papers/{paper_id}/figures/{figure_id}/select" in paths
    assert "/api/v1/papers/{paper_id}/sections/{section_id}/rewrite" in paths


def test_paper_selected_figures_storage_round_trip():
    experiment = create_experiment({"name": "Selected figure smoke"})
    figure = save_figure_artifact(
        experiment["id"],
        "line",
        {"title": "Accuracy curve"},
        b"png-bytes",
        b"%PDF-1.4\n",
        "Accuracy improves over training steps.",
        "plot accuracy",
        "test-model",
    )
    paper = create_paper({"title": "Selected figure paper"})

    selected = select_figure_for_paper(
        paper["id"],
        figure["id"],
        {
            "targetSection": "results",
            "notes": "Discuss the late-stage improvement.",
        },
    )

    assert selected["figureId"] == figure["id"]
    assert selected["targetSection"] == "results"
    assert selected["path"].startswith("figures/")

    stored = get_selected_figures(paper["id"])
    assert len(stored) == 1
    assert stored[0]["label"] == f"fig:{figure['id']}"

    update_selected_figures(paper["id"], [{
        **stored[0],
        "caption": "Updated accuracy curve caption.",
        "include": False,
    }])
    updated = get_selected_figures(paper["id"])
    assert updated[0]["caption"] == "Updated accuracy curve caption."
    assert updated[0]["include"] is False

    remove_selected_figure(paper["id"], figure["id"])
    assert get_selected_figures(paper["id"]) == []


def test_paper_section_write_dispatches_to_specialized_writers():
    cases = [
        ({"id": "intro", "title": "Introduction"}, "introduction"),
        ({"id": "related_work", "title": "Related Work"}, "related_work"),
        ({"id": "prelim", "title": "Background and Preliminaries"}, "background"),
        ({"id": "method", "title": "Method"}, "method"),
        ({"id": "experiments", "title": "Experiments"}, "experiments"),
        ({"id": "analysis", "title": "Analysis and Limitations"}, "analysis"),
        ({"id": "conclusion", "title": "Conclusion"}, "conclusion"),
    ]

    for section, expected in cases:
        assert classify_section(section) == expected
        assert get_section_writer(section).kind == expected

    assert render_prompt(r"\section{{{section_title}}}", {"section_title": "Introduction"}) == (
        r"\section{Introduction}"
    )


def test_paper_section_writer_filters_targeted_figures_by_section():
    figures = [
        {"figureId": "fig_results", "targetSection": "experiments", "include": True},
        {"figureId": "fig_global", "include": True},
        {"figureId": "fig_analysis", "targetSection": "analysis", "include": True},
    ]

    figure_ctx = split_figures_for_section(
        "[]",
        figures,
        {"id": "experiments", "title": "Experiments"},
        "Experiments",
    )

    assert [fig["figureId"] for fig in figure_ctx["section_figures"]] == ["fig_results"]
    assert [fig["figureId"] for fig in figure_ctx["figures_for_prompt"]] == ["fig_results"]

    figure_ctx = split_figures_for_section(
        "[]",
        figures,
        {"id": "method", "title": "Method"},
        "Method",
    )
    assert [fig["figureId"] for fig in figure_ctx["figures_for_prompt"]] == ["fig_global"]


def test_paper_latex_rewrites_missing_figure_references(tmp_path):
    figures_dir = tmp_path / "figures"
    figures_dir.mkdir()
    (figures_dir / "fig_performance.pdf").write_text("pdf")

    content = r"\includegraphics[width=\linewidth]{figures/framework.pdf}"
    normalized, rewrites = normalize_section_figure_references(
        content,
        [{"filename": "fig_performance", "ext": "pdf"}],
        str(figures_dir),
    )

    assert r"\includegraphics[width=\linewidth]{figures/fig_performance.pdf}" in normalized
    assert rewrites == [{"from": "figures/framework.pdf", "to": "figures/fig_performance.pdf"}]


def test_paper_experiment_figure_records_normalize_to_entries():
    entry = figure_record_to_entry({
        "id": "fig_abc123",
        "experimentId": "exp_1",
        "figureType": "bar",
        "title": "Router pass rate",
        "caption": "Pass-rate comparison across routing policies.",
        "fileNamePdf": "fig_abc123_bar_router_pass_rate.pdf",
        "fileNamePng": "fig_abc123_bar_router_pass_rate.png",
    })

    assert entry["figureId"] == "fig_abc123"
    assert entry["filename"] == "fig_abc123_bar_router_pass_rate"
    assert entry["ext"] == "pdf"
    assert entry["path"] == "figures/fig_abc123_bar_router_pass_rate.pdf"
    assert entry["label"] == "fig:fig_abc123"
    assert entry["caption"] == "Pass-rate comparison across routing policies."

    unique = dedupe_figure_entries([
        entry,
        {**entry, "ext": "png", "path": "figures/fig_abc123_bar_router_pass_rate.png"},
    ])
    assert len(unique) == 1


def test_paper_latex_rewrites_unknown_citation_keys():
    normalized, rewrites = normalize_section_citations(
        r"Known \cite{known1,missing1}. Unknown \cite{missing2}.",
        [{"key": "known1"}, {"key": "known2"}],
    )

    assert r"\cite{known1}" in normalized
    assert r"\cite{missing1}" not in normalized
    assert r"\cite{missing2}" not in normalized
    assert rewrites == [
        {"from": "known1,missing1", "to": "known1"},
        {"from": "missing2", "to": ""},
    ]


def test_paper_latex_escapes_text_specials_without_breaking_commands_or_math():
    content = (
        r"类别 nonmotor_vehicle 占比 32.1% #1 "
        r"\cite{known_key} \label{fig:raw_label} "
        r"$D_{\text{max}}<0.023$"
    )
    normalized = sanitize_latex_text_specials(content)

    assert r"nonmotor\_vehicle" in normalized
    assert r"32.1\%" in normalized
    assert r"\#1" in normalized
    assert r"\cite{known_key}" in normalized
    assert r"\label{fig:raw_label}" in normalized
    assert r"$D_{\text{max}}<0.023$" in normalized


def test_paper_latex_renames_duplicate_label_definitions():
    normalized, rewrites = normalize_duplicate_latex_labels({
        "methods": r"\label{fig:main}",
        "results": r"\label{fig:main}",
    })

    assert normalized["methods"] == r"\label{fig:main}"
    assert normalized["results"] == r"\label{fig:main:results}"
    assert rewrites == [{"section": "results", "from": "fig:main", "to": "fig:main:results"}]


def test_bibtex_author_strings_are_normalized_for_bst_files():
    assert normalize_bibtex_authors("Reddi, S. S., Kale, S., and Kumar, S.") == (
        "Reddi, S. S. and Kale, S. and Kumar, S."
    )
    assert normalize_bibtex_authors("Vaswani, A. et al.") == "Vaswani, A. and others"
    assert normalize_bibtex_authors(
        "Jocher, G. and Stoken, A. and Chaurasia, A. and & Qiu, J."
    ) == "Jocher, G. and Stoken, A. and Chaurasia, A. and Qiu, J."

    bibtex = build_bibtex([
        {
            "key": "reddi2018adaptive",
            "authors": "Reddi, S. S., Kale, S., and Kumar, S.",
            "title": "On the Convergence of Adam and Beyond",
            "venue": "ICLR",
            "year": 2018,
        }
    ])
    assert "author = {Reddi, S. S. and Kale, S. and Kumar, S.}" in bibtex

    bibtex = build_bibtex([
        {
            "key": "jocher2020yolov5",
            "authors": "Jocher, G. and Stoken, A. and Chaurasia, A. and & Qiu, J.",
            "title": "YOLOv5 & Edge_Deployment",
            "venue": "GitHub Repository",
            "year": 2020,
        }
    ])
    assert "author = {Jocher, G. and Stoken, A. and Chaurasia, A. and Qiu, J.}" in bibtex
    assert r"title = {YOLOv5 \& Edge\_Deployment}" in bibtex


def test_paper_render_pdf_uses_modular_template_helper():
    source = Path(__file__).parents[1] / "app" / "modules" / "paper" / "papers_api.py"
    content = source.read_text(encoding="utf-8")

    assert "_copy_template_assets" not in content
    assert "copy_template_assets" in content


def test_paper_latex_templates_support_generated_algorithm_keywords():
    for venue in ["generic", "iclr", "neurips", "acl", "challenge_cup"]:
        template = (TEMPLATE_ROOT / venue / "main.tex").read_text(encoding="utf-8")
        assert "algorithm2e" in template
        assert r"\SetKw{KwAnd}{and}" in template
        assert r"\SetKw{Return}{return}" in template


def test_latex_templates_include_section_input_anchor():
    for venue in ["generic", "icml", "iclr", "neurips", "acl", "challenge_cup"]:
        template = (TEMPLATE_ROOT / venue / "main.tex").read_text(encoding="utf-8")
        assert "%%SECTION_INPUTS%%" in template


def test_challenge_cup_template_supports_generated_table_commands():
    template = (TEMPLATE_ROOT / "challenge_cup" / "main.tex").read_text(encoding="utf-8")

    assert r"\usepackage{booktabs}" in template
    assert r"\usepackage{longtable}" in template
    assert r"\usepackage{multirow}" in template


def test_icml_template_uses_bundled_algorithm_package():
    template = (TEMPLATE_ROOT / "icml" / "main.tex").read_text(encoding="utf-8")
    style = (TEMPLATE_ROOT / "icml" / "icml2025.sty").read_text(encoding="utf-8")

    assert "algorithm2e" not in template
    assert r"\RequirePackage{algorithm}" in style
    assert r"\RequirePackage{algorithmic}" in style


def test_templates_api_lists_latex_templates():
    response = client.get("/api/v1/templates")

    assert response.status_code == 200
    payload = response.json()
    template_ids = {template["id"] for template in payload["templates"]}
    assert {"icml", "neurips", "iclr", "acl", "generic", "challenge_cup"}.issubset(template_ids)


def test_icml_template_includes_prompt_style_guide():
    for venue in ["icml", "neurips", "iclr", "acl", "generic", "challenge_cup"]:
        template_dir = TEMPLATE_ROOT / venue

        assert (template_dir / "main.tex").is_file()
        assert (template_dir / "style_guide.md").is_file()

        guide = load_venue_style_guide(venue)
        assert "Reviewer Expectations" in guide
        assert "Outline Guidance" in guide
