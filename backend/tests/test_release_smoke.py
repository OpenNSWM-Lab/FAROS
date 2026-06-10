import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

pytest.importorskip("sqlmodel")

from fastapi.testclient import TestClient

from app.main import app
from app.modules.paper.skills.constants import TEMPLATE_ROOT
from app.modules.paper.skills.utils import (
    build_bibtex,
    dedupe_figure_entries,
    figure_record_to_entry,
    load_venue_style_guide,
    normalize_bibtex_authors,
    normalize_section_figure_references,
)
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


def test_bibtex_author_strings_are_normalized_for_bst_files():
    assert normalize_bibtex_authors("Reddi, S. S., Kale, S., and Kumar, S.") == (
        "Reddi, S. S. and Kale, S. and Kumar, S."
    )
    assert normalize_bibtex_authors("Vaswani, A. et al.") == "Vaswani, A. and others"

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


def test_paper_render_pdf_uses_modular_template_helper():
    source = Path(__file__).parents[1] / "app" / "modules" / "paper" / "papers_api.py"
    content = source.read_text(encoding="utf-8")

    assert "_copy_template_assets" not in content
    assert "copy_template_assets" in content


def test_paper_latex_templates_support_generated_algorithm_keywords():
    for venue in ["generic", "iclr", "neurips", "acl"]:
        template = (TEMPLATE_ROOT / venue / "main.tex").read_text(encoding="utf-8")
        assert "algorithm2e" in template
        assert r"\SetKw{KwAnd}{and}" in template
        assert r"\SetKw{Return}{return}" in template


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
    assert {"icml", "neurips", "iclr", "acl", "generic"}.issubset(template_ids)


def test_icml_template_includes_prompt_style_guide():
    template_dir = TEMPLATE_ROOT / "icml"

    assert (template_dir / "main.tex").is_file()
    assert (template_dir / "style_guide.md").is_file()

    guide = load_venue_style_guide("icml")
    assert "Reviewer Expectations" in guide
    assert "Outline Guidance" in guide
