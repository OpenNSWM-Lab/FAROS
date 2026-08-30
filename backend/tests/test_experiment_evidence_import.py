import asyncio
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.core.paths import get_data_dir
from app.modules.platform.experiments_api import (
    ImportProjectEvidenceRequest,
    _parse_uploaded_dataset,
    import_project_evidence,
)
from app.storage.experiment_storage import (
    create_experiment,
    get_execution_evidence,
    get_experiment,
    get_metrics,
    list_datasets,
)


def test_jsonl_upload_parses_each_object_and_reports_the_bad_line():
    rows, file_format = _parse_uploaded_dataset(
        "predictions.jsonl",
        b'{"id": 1}\n{"id": 2}\n',
    )

    assert file_format == "jsonl"
    assert rows == [{"id": 1}, {"id": 2}]

    with pytest.raises(ValueError, match="line 2"):
        _parse_uploaded_dataset("broken.jsonl", b'{"id": 1}\nnot-json\n')


def _write_evidence_bundle(project_id: str, *, prediction_rows: int = 2) -> str:
    relative = "artifacts/verified-run"
    root = get_data_dir() / "code_projects" / project_id / "repo" / relative
    root.mkdir(parents=True, exist_ok=True)
    metrics = {
        "evidence_status": "executed",
        "holdout_records": prediction_rows,
        "holdout_f1_delta": 0.12,
        "holdout_method": {"f1": 0.82},
        "bootstrap_f1_delta_ci95": [0.03, 0.19],
        "limitations": ["A documented limitation."],
    }
    manifest = {
        "inputs": {
            "train_sha256": "a" * 64,
            "dev_sha256": "b" * 64,
        },
    }
    predictions = [
        {
            "claim_id": index,
            "gold_mismatch": index % 2,
            "mismatch_score": index / max(1, prediction_rows),
            "prediction": index % 2,
        }
        for index in range(prediction_rows)
    ]
    (root / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    (root / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "predictions.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in predictions),
        encoding="utf-8",
    )
    return relative


def test_import_project_evidence_verifies_and_deduplicates_the_bundle():
    project_id = "cproj_evidence_test"
    artifact_path = _write_evidence_bundle(project_id)
    experiment = create_experiment({
        "name": "Verified experiment",
        "projectId": project_id,
        "status": "created",
    })
    request = ImportProjectEvidenceRequest(
        projectId=project_id,
        artifactPath=artifact_path,
    )

    first = asyncio.run(import_project_evidence(experiment["id"], request))
    metric_count = len(get_metrics(experiment["id"]))
    second = asyncio.run(import_project_evidence(experiment["id"], request))

    assert first["status"] == "verified"
    assert all(first["checks"].values())
    assert first["predictionRows"] == 2
    assert len(first["bundleSha256"]) == 64
    assert metric_count > 0
    assert len(get_metrics(experiment["id"])) == metric_count
    assert len(list_datasets(experiment["id"])) == 1
    assert second["duplicate"] is True
    assert get_execution_evidence(experiment["id"])["bundleSha256"] == first["bundleSha256"]
    assert get_experiment(experiment["id"])["evidenceStatus"] == "verified"


def test_import_project_evidence_rejects_path_traversal():
    project_id = "cproj_boundary_test"
    experiment = create_experiment({"name": "Boundary", "projectId": project_id})
    request = ImportProjectEvidenceRequest(projectId=project_id, artifactPath="../../outside")

    with pytest.raises(HTTPException, match="inside the linked project"):
        asyncio.run(import_project_evidence(experiment["id"], request))
