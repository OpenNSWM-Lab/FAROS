"""Build reproducible ExperimentEvidence from an executed Code project."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.contracts import (
    ArtifactKind,
    ArtifactRef,
    ExecutionStatus,
    ExperimentEvidence,
    MetricEvidence,
    TargetModule,
)


PROCESS_METRICS = {
    "exit_code",
    "duration_seconds",
    "duration_ms",
    "wall_time",
    "runtime",
    "status",
}


def _sha256_bytes(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _aggregate_hash(paths: Sequence[Path], root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}" if paths else ""


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def _metric_evidence(metrics: Sequence[Mapping[str, Any]], metrics_path: str) -> list[MetricEvidence]:
    result: list[MetricEvidence] = []
    for item in metrics:
        name = str(item.get("name") or "").strip()
        if not name or name.lower() in PROCESS_METRICS or "value" not in item:
            continue
        result.append(
            MetricEvidence(
                name=name,
                value=item.get("value"),
                unit=str(item.get("unit") or ""),
                definition=str(item.get("definition") or item.get("description") or ""),
                split=str(item.get("split") or ""),
                sourcePath=metrics_path,
            )
        )
    return result


def build_experiment_evidence(
    *,
    repo_dir: str | Path,
    run_id: str,
    question_id: str,
    code_run_id: str,
    method: str,
    baseline: str,
    metrics: Sequence[Mapping[str, Any]],
    execution_result: Mapping[str, Any],
    expected_claims: Sequence[str] | None = None,
) -> ExperimentEvidence:
    """Persist a reproducibility bundle and return its validated contract."""

    root = Path(repo_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    evidence_dir = root / "artifacts" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    stdout_path = evidence_dir / "stdout.log"
    stderr_path = evidence_dir / "stderr.log"
    environment_path = evidence_dir / "environment.json"
    run_manifest_path = evidence_dir / "run_manifest.json"
    metrics_path = root / "metrics.json"
    evidence_path = evidence_dir / "experiment_evidence.json"
    artifact_hashes_path = evidence_dir / "artifact_hashes.json"

    _write_text(stdout_path, str(execution_result.get("stdout") or ""))
    _write_text(stderr_path, str(execution_result.get("stderr") or ""))
    _write_json(metrics_path, list(metrics))

    environment = {
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "requirementsHash": _sha256_file(root / "requirements.txt") if (root / "requirements.txt").is_file() else "",
    }
    _write_json(environment_path, environment)

    code_paths = [
        path
        for folder in (root / "src", root / "scripts", root / "tests")
        if folder.exists()
        for path in folder.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    ]
    code_hash = _aggregate_hash(code_paths, root)
    environment_hash = _sha256_file(environment_path)

    data_hashes: dict[str, str] = {}
    for folder_name in ("data", "datasets", "fixtures"):
        folder = root / folder_name
        if not folder.exists():
            continue
        for path in folder.rglob("*"):
            if path.is_file() and path.stat().st_size <= 100 * 1024 * 1024:
                data_hashes[path.relative_to(root).as_posix()] = _sha256_file(path)

    scientific_metrics = _metric_evidence(metrics, metrics_path.relative_to(root).as_posix())
    exit_code = execution_result.get("exit_code")
    failures: list[str] = []
    if not execution_result.get("command"):
        failures.append("No supported experiment entrypoint was executed.")
    if exit_code != 0:
        failures.append(f"Experiment process exited with code {exit_code}.")
    if not scientific_metrics:
        failures.append("Execution produced no scientific metric; process metadata is not experimental evidence.")
    if scientific_metrics and any(not item.definition.strip() for item in scientific_metrics):
        failures.append("At least one scientific metric is missing a definition.")
    if not method.strip():
        failures.append("The executed method is not documented.")
    if not baseline.strip():
        failures.append("The experiment does not declare a baseline or control.")
    if not code_hash:
        failures.append("No executable source files were available for hashing.")

    status = ExecutionStatus.EXECUTED if not failures else ExecutionStatus.FAILED
    now = datetime.now(UTC)
    artifact_refs = [
        ArtifactRef(
            id=f"{code_run_id}:metrics",
            kind=ArtifactKind.METRIC,
            sourceModule=TargetModule.CODE,
            uri=metrics_path.relative_to(root).as_posix(),
            contentHash=_sha256_file(metrics_path),
            version="1",
            createdAt=now,
        ),
        ArtifactRef(
            id=f"{code_run_id}:environment",
            kind=ArtifactKind.OTHER,
            sourceModule=TargetModule.CODE,
            uri=environment_path.relative_to(root).as_posix(),
            contentHash=environment_hash,
            version="1",
            createdAt=now,
        ),
        ArtifactRef(
            id=f"{code_run_id}:stdout",
            kind=ArtifactKind.LOG,
            sourceModule=TargetModule.CODE,
            uri=stdout_path.relative_to(root).as_posix(),
            contentHash=_sha256_file(stdout_path),
            version="1",
            createdAt=now,
        ),
    ]

    evidence = ExperimentEvidence(
        runId=run_id,
        questionId=question_id,
        codeRunId=code_run_id,
        status=status,
        dataHashes=data_hashes,
        environmentHash=environment_hash,
        codeHash=code_hash,
        method=method,
        baseline=baseline,
        metrics=scientific_metrics,
        logRefs=[stdout_path.relative_to(root).as_posix(), stderr_path.relative_to(root).as_posix()],
        artifactRefs=artifact_refs,
        supportedClaims=[],
        # Successful execution validates artifacts and metrics, not arbitrary
        # caller-supplied scientific claims. ReviewX performs claim support later.
        unsupportedClaims=list(expected_claims or []),
        failures=failures,
        durationSeconds=float(execution_result.get("duration_seconds") or 0),
    )

    _write_json(evidence_path, evidence.model_dump(mode="json"))
    manifest = {
        "runId": run_id,
        "questionId": question_id,
        "codeRunId": code_run_id,
        "status": evidence.status.value,
        "command": execution_result.get("command"),
        "exitCode": exit_code,
        "durationSeconds": evidence.durationSeconds,
        "metricNames": [item.name for item in scientific_metrics],
        "createdAt": now.isoformat(),
    }
    _write_json(run_manifest_path, manifest)

    hash_targets = [metrics_path, environment_path, stdout_path, stderr_path, evidence_path, run_manifest_path]
    _write_json(
        artifact_hashes_path,
        {path.relative_to(root).as_posix(): _sha256_file(path) for path in hash_targets if path.is_file()},
    )
    return evidence
