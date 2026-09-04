import hashlib
import json
from pathlib import Path

from app.modules.platform.verified_histories_api import load_verified_histories


def _digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def test_verified_history_checks_stages_and_artifact_digests(tmp_path: Path):
    history_id = "real-data-case"
    entity_ids = {
        "idea": "idea_1",
        "plan": "plan_1",
        "code": "code_1",
        "experiment": "experiment_1",
        "paper": "paper_1",
        "reviewx": "review_1",
    }
    stage_paths = {
        "idea": tmp_path / "ideas" / "sessions" / "idea_1.json",
        "plan": tmp_path / "plan_packages" / "plan_1.json",
        "code": tmp_path / "code_projects" / "code_1" / "repo",
        "experiment": tmp_path / "experiments" / "experiment_1" / "experiment.json",
        "paper": tmp_path / "papers" / "paper_1" / "meta.json",
        "reviewx": tmp_path / "reviews" / "review_1" / "meta.json",
    }
    for stage_id, path in stage_paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if stage_id == "code":
            path.mkdir()
        else:
            path.write_text("{}\n", encoding="utf-8")

    artifact_path = tmp_path / "experiments" / "experiment_1" / "evaluation_records.json"
    artifact_path.write_text('[{"label":"supported"}]\n', encoding="utf-8")
    manifest = {
        "schemaVersion": "faros-verified-workflow-history/v1",
        "id": history_id,
        "completedAt": "2026-09-04T00:00:00+00:00",
        "stages": [
            {
                "id": stage_id,
                "entityId": entity_id,
                "url": (
                    f"/research/pipeline?ideaSessionId=idea_1&ideaCandidateId=candidate_1"
                    if stage_id in {"idea", "plan"}
                    else f"/{stage_id}/{entity_id}"
                ),
            }
            for stage_id, entity_id in entity_ids.items()
        ],
        "artifacts": [{
            "id": "evaluation-records",
            "path": str(artifact_path.relative_to(tmp_path)),
            "sha256": _digest(artifact_path),
        }],
    }
    manifest_dir = tmp_path / "verified_workflow_histories"
    manifest_dir.mkdir()
    (manifest_dir / f"{history_id}.json").write_text(json.dumps(manifest), encoding="utf-8")

    histories = load_verified_histories(tmp_path)

    assert len(histories) == 1
    assert histories[0]["integrity"]["status"] == "verified"
    assert all(stage["status"] == "passed" for stage in histories[0]["stages"])
    stage_urls = {stage["id"]: stage["url"] for stage in histories[0]["stages"]}
    assert stage_urls["idea"].endswith("&phase=idea")
    assert stage_urls["plan"].endswith("&phase=plan")
    assert histories[0]["artifacts"][0]["verified"] is True
    assert "path" not in histories[0]["artifacts"][0]

    artifact_path.write_text('[{"label":"tampered"}]\n', encoding="utf-8")
    tampered = load_verified_histories(tmp_path)[0]
    assert tampered["integrity"]["status"] == "incomplete"
    assert tampered["integrity"]["brokenArtifacts"] == ["evaluation-records"]
