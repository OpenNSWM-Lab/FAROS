import json
import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.modules.paper import papers_api
from app.modules.paper.skills import code_evidence
from app.modules.paper.storage import create_paper, get_paper, update_paper
from app.storage import paper_storage


client = TestClient(app)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _cart(tmp_path: Path, *, project_id: str = "project_1") -> Path:
    cart = tmp_path / "data" / "cart_artifacts" / "cart_test"
    _write_json(cart / "cart_results.json", {
        "cart_id": "cart_test",
        "project_id": project_id,
        "overall_status": "success",
        "all_metrics": {"accuracy": 0.99},
    })
    _write_json(cart / "data" / "step" / "result.json", {
        "node_id": "step",
        "success": True,
        "outputs": {"metrics": {"accuracy": 0.99}},
        "result_analysis": "The method is highly accurate.",
        "artifacts": [{"name": "result.csv", "path": "data/step/result.csv"}],
    })
    (cart / "data" / "step" / "result.csv").write_text("accuracy\n0.99\n", encoding="utf-8")
    return cart


def test_unlinked_paper_does_not_collect_recent_carts(monkeypatch):
    monkeypatch.setattr(
        code_evidence,
        "_iter_cart_dirs",
        lambda: (_ for _ in ()).throw(AssertionError("unlinked carts must not be scanned")),
    )

    assert code_evidence._collect_cart_evidence(None) == []


def test_unverified_cart_results_are_diagnostic_only(tmp_path, monkeypatch):
    monkeypatch.setattr(code_evidence, "_data_dir", lambda: str(tmp_path / "data"))
    cart = _cart(tmp_path)

    summary = code_evidence._cart_summary(str(cart))

    assert summary is not None
    assert summary["claimEligible"] is False
    assert summary["evidenceStatus"] == "missing"
    assert summary["metrics"] is None
    assert summary["codeTables"] == []
    assert summary["nodeResults"][0]["metrics"] is None
    assert summary["nodeResults"][0]["resultAnalysis"] == ""


def test_executed_cart_evidence_allows_result_context(tmp_path, monkeypatch):
    monkeypatch.setattr(code_evidence, "_data_dir", lambda: str(tmp_path / "data"))
    cart = _cart(tmp_path)
    fixture_path = Path(__file__).parent / "fixtures" / "scientific_research" / "experiment_evidence.json"
    evidence = json.loads(fixture_path.read_text(encoding="utf-8"))
    evidence["codeRunId"] = "cart_test"
    artifact_path = cart / "data" / "step" / "result.csv"
    evidence["artifactRefs"] = [{
        **evidence["artifactRefs"][0],
        "uri": "data/step/result.csv",
        "contentHash": f"sha256:{hashlib.sha256(artifact_path.read_bytes()).hexdigest()}",
    }]
    _write_json(cart / "data" / "experiment_evidence.json", evidence)

    summary = code_evidence._cart_summary(str(cart))

    assert summary is not None
    assert summary["claimEligible"] is True
    assert summary["evidenceStatus"] == "executed"
    assert summary["metrics"] == {"accuracy": 0.99}
    assert summary["nodeResults"][0]["metrics"] == {"accuracy": 0.99}

    artifact_path.write_text("accuracy\n0.12\n", encoding="utf-8")
    stale = code_evidence._cart_summary(str(cart))
    assert stale["claimEligible"] is False
    assert stale["evidenceStatus"] == "stale"


def test_project_id_cannot_escape_code_projects_root(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    outside_repo = tmp_path / "secret" / "repo"
    outside_repo.mkdir(parents=True)
    (outside_repo / "README.md").write_text("private", encoding="utf-8")
    monkeypatch.setattr(code_evidence, "_data_dir", lambda: str(data_dir))

    result = code_evidence._collect_repo_evidence("../../secret")

    assert result == {"projectId": "../../secret", "available": False}
    assert code_evidence._resolve_data_rel_path(str(outside_repo / "README.md")) == ""


def test_generate_endpoint_claims_paper_before_background_thread(monkeypatch):
    paper = create_paper({"title": "Single generation"})

    class DeferredThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(papers_api.threading, "Thread", DeferredThread)

    first = client.post(f"/api/v1/papers/{paper['id']}/generate")
    second = client.post(f"/api/v1/papers/{paper['id']}/generate")

    assert first.status_code == 202
    assert second.status_code == 409
    stored = get_paper(paper["id"])
    assert stored["status"] == "generating"
    assert stored["pdfAvailable"] is False
    assert stored["simpleReviewPassed"] is False


def test_manual_file_edit_invalidates_previous_review():
    paper = create_paper({"title": "Editable paper"})
    update_paper(paper["id"], {
        "status": "completed",
        "pdfAvailable": True,
        "compileStatus": "latexmk",
        "simpleReviewPassed": True,
    })

    response = client.post(
        f"/api/v1/papers/{paper['id']}/files",
        json={"path": "sections/results.tex", "content": "Updated results."},
    )

    assert response.status_code == 200
    stored = get_paper(paper["id"])
    assert stored["status"] == "created"
    assert stored["pdfAvailable"] is False
    assert stored["compileStatus"] is None
    assert stored["simpleReviewPassed"] is False


def test_metadata_edit_invalidates_stale_pdf():
    paper = create_paper({"title": "Old title"})
    update_paper(paper["id"], {
        "status": "completed",
        "pdfAvailable": True,
        "compileStatus": "latexmk",
        "simpleReviewPassed": True,
    })

    response = client.patch(
        f"/api/v1/papers/{paper['id']}/metadata",
        json={"title": "New title"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "created"
    assert response.json()["pdfAvailable"] is False
    assert response.json()["simpleReviewPassed"] is False


def test_read_paper_file_rejects_similarly_prefixed_sibling(tmp_path, monkeypatch):
    monkeypatch.setattr(paper_storage, "PAPERS_DIR", str(tmp_path / "papers"))
    paper = paper_storage.create_paper({"title": "Path boundary"})
    latex_dir = Path(paper_storage.get_paper_latex_dir(paper["id"]))
    sibling = latex_dir.parent / "latex-secret"
    sibling.mkdir()
    (sibling / "secret.tex").write_text("secret", encoding="utf-8")

    assert paper_storage.read_paper_file(paper["id"], "../latex-secret/secret.tex") is None
