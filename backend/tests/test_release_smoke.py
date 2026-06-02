import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

pytest.importorskip("sqlmodel")

from fastapi.testclient import TestClient

from app.main import app
from app.modules.paper.skills.constants import TEMPLATE_ROOT
from app.modules.paper.skills.utils import normalize_section_figure_references
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


def test_paper_latex_templates_support_generated_algorithm_keywords():
    for venue in ["generic", "icml", "iclr", "neurips", "acl"]:
        template = (TEMPLATE_ROOT / venue / "main.tex").read_text(encoding="utf-8")
        assert "algorithm2e" in template
        assert r"\SetKw{KwAnd}{and}" in template
        assert r"\SetKw{Return}{return}" in template
