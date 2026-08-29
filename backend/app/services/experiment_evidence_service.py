"""Build reproducible ExperimentEvidence from an executed Code project."""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
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
from app.services.experiment_benchmark_service import (
    FROZEN_BENCHMARK_RELATIVE_PATH,
    load_frozen_benchmark,
    validate_evaluation_alignment,
)


PROCESS_METRICS = {
    "exit_code",
    "duration_seconds",
    "duration_ms",
    "wall_time",
    "runtime",
    "status",
}

_CLASSIFICATION_SUFFIXES = (
    "expected_calibration_error",
    "unsupported_claim_rate",
    "brier_score",
    "f1_score",
    "auroc",
    "precision",
    "recall",
)
_CLASSIFICATION_TRIGGER_METRICS = {
    "expected_calibration_error",
    "brier_score",
    "f1_score",
    "auroc",
    "precision",
    "recall",
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


def _evaluation_inputs_hash(path: Path) -> str:
    """Hash evaluation inputs and labels without model predictions."""
    if not path.is_file():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        return ""
    frozen_payload = {
        "schema_version": payload.get("schema_version"),
        "positive_label": payload.get("positive_label"),
        "positive_class": payload.get("positive_class"),
        "records": [
            {key: value for key, value in record.items() if key != "predictions"}
            if isinstance(record, dict) else record
            for record in payload["records"]
        ],
    }
    encoded = json.dumps(
        frozen_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(encoded)


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


def _normalize_metric_name(name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    aliases = {
        "ece": "expected_calibration_error",
        "expected_calibration_error_ece": "expected_calibration_error",
        "f1": "f1_score",
        "roc_auc": "auroc",
        "auc_roc": "auroc",
    }
    return aliases.get(normalized, normalized)


def _method_from_split(split: str) -> str:
    normalized = _normalize_metric_name(split)
    tokens = normalized.split("_")
    if "baseline" in tokens:
        return "baseline"
    if "method" in tokens or "proposed" in tokens:
        return "method"
    return "method"


def _classification_metric_parts(name: str, split: str = "") -> tuple[str, str] | None:
    normalized = _normalize_metric_name(name)
    for suffix in _CLASSIFICATION_SUFFIXES:
        if normalized == suffix:
            return _method_from_split(split), suffix
        marker = f"_{suffix}"
        if normalized.endswith(marker):
            return normalized[:-len(marker)], suffix
    return None


def _binary_metrics(labels: Sequence[bool], predictions: Sequence[bool]) -> dict[str, float]:
    true_positive = sum(label and prediction for label, prediction in zip(labels, predictions))
    false_positive = sum(not label and prediction for label, prediction in zip(labels, predictions))
    false_negative = sum(label and not prediction for label, prediction in zip(labels, predictions))
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    f1_score = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1_score": f1_score,
        "unsupported_claim_rate": sum(predictions) / len(predictions),
    }


def _expected_calibration_error(
    labels: Sequence[bool], probabilities: Sequence[float], bins: int = 10,
) -> float:
    bucket_labels: list[list[float]] = [[] for _ in range(bins)]
    bucket_probabilities: list[list[float]] = [[] for _ in range(bins)]
    for label, probability in zip(labels, probabilities):
        bucket = min(int(probability * bins), bins - 1)
        bucket_labels[bucket].append(float(label))
        bucket_probabilities[bucket].append(probability)
    total = len(labels)
    return sum(
        len(label_bucket) / total
        * abs(
            sum(label_bucket) / len(label_bucket)
            - sum(probability_bucket) / len(probability_bucket)
        )
        for label_bucket, probability_bucket in zip(bucket_labels, bucket_probabilities)
        if label_bucket
    )


def _brier_score(labels: Sequence[bool], probabilities: Sequence[float]) -> float:
    return sum((probability - float(label)) ** 2 for label, probability in zip(labels, probabilities)) / len(labels)


def _auroc(labels: Sequence[bool], probabilities: Sequence[float]) -> float:
    positive = [probability for label, probability in zip(labels, probabilities) if label]
    negative = [probability for label, probability in zip(labels, probabilities) if not label]
    if not positive or not negative:
        return 0.0
    wins = sum(
        1.0 if pos > neg else 0.5 if pos == neg else 0.0
        for pos in positive
        for neg in negative
    )
    return wins / (len(positive) * len(negative))


def _audit_classification_metrics(
    root: Path,
    scientific_metrics: Sequence[MetricEvidence],
    *,
    require_ablation: bool = False,
) -> dict[str, Any]:
    parsed_metrics = [
        (metric, parts)
        for metric in scientific_metrics
        if (parts := _classification_metric_parts(metric.name, metric.split)) is not None
    ]
    canonical_names = {parts[1] for _metric, parts in parsed_metrics}
    if not canonical_names.intersection(_CLASSIFICATION_TRIGGER_METRICS):
        return {"status": "not_applicable", "errors": [], "recomputedMetrics": {}}

    records_path = root / "evaluation_records.json"
    errors: list[str] = []
    if not records_path.is_file():
        return {
            "status": "failed",
            "schemaVersion": "faros-evaluation/v1",
            "sourcePath": "evaluation_records.json",
            "errors": [
                "Classification metrics require evaluation_records.json for independent recomputation."
            ],
            "recomputedMetrics": {},
        }
    try:
        payload = json.loads(records_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "failed",
            "schemaVersion": "faros-evaluation/v1",
            "sourcePath": "evaluation_records.json",
            "errors": [f"evaluation_records.json is invalid: {exc}"],
            "recomputedMetrics": {},
        }

    if not isinstance(payload, dict):
        payload = {}
        errors.append("evaluation_records.json must contain a JSON object.")
    benchmark_payload, benchmark_audit = load_frozen_benchmark(root)
    errors.extend(str(item) for item in benchmark_audit.get("errors", []))
    benchmark_alignment = {
        "status": "not_checked",
        "matchedRecordCount": 0,
        "errors": [],
    }
    if benchmark_payload is not None and benchmark_audit.get("status") == "passed":
        benchmark_alignment = validate_evaluation_alignment(payload, benchmark_payload)
        errors.extend(str(item) for item in benchmark_alignment.get("errors", []))
    schema_version = str(payload.get("schema_version") or "")
    positive_class = str(payload.get("positive_class") or "").strip().lower()
    positive_label = payload.get("positive_label")
    decision_threshold = payload.get("decision_threshold", 0.5)
    decision_thresholds = payload.get("decision_thresholds") or {}
    records = payload.get("records")
    if schema_version != "faros-evaluation/v1":
        errors.append("evaluation_records.json must declare schema_version=faros-evaluation/v1.")
    if not positive_class:
        errors.append("evaluation_records.json must declare positive_class.")
    if positive_label is None:
        errors.append("evaluation_records.json must declare positive_label.")
    if not isinstance(decision_threshold, (int, float)) or not 0 <= decision_threshold <= 1:
        errors.append("evaluation_records.json decision_threshold must be between 0 and 1.")
        decision_threshold = 0.5
    if not isinstance(decision_thresholds, dict):
        errors.append("evaluation_records.json decision_thresholds must be an object when provided.")
        decision_thresholds = {}
    else:
        for method_name, threshold in decision_thresholds.items():
            if not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
                errors.append(
                    "evaluation_records.json decision_thresholds values must be between 0 and 1 "
                    f"('{method_name}' is invalid)."
                )
    if "unsupported_claim_rate" in canonical_names and positive_class != "unsupported":
        errors.append(
            "unsupported_claim_rate requires positive_class='unsupported'; the label polarity is inconsistent."
        )
    if not isinstance(records, list) or not records:
        errors.append("evaluation_records.json must contain a non-empty records list.")
        records = []

    method_names = sorted({parts[0] for _metric, parts in parsed_metrics})
    ablation_names = [name for name in method_names if name.startswith("ablation_")]
    if require_ablation:
        missing_variants = [name for name in ("baseline", "method") if name not in method_names]
        if missing_variants:
            errors.append(
                "Inherited-benchmark iterations require exact metric/prediction prefixes: "
                + ", ".join(missing_variants)
                + "."
            )
        if not ablation_names:
            errors.append(
                "Inherited-benchmark iterations require at least one named ablation_<component> variant."
            )
    labels: list[bool] = []
    predictions: dict[str, list[bool]] = {name: [] for name in method_names}
    probabilities: dict[str, list[float]] = {name: [] for name in method_names}
    missing_probabilities: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict) or "label" not in record:
            errors.append(f"Record {index} is missing label.")
            continue
        record_predictions = record.get("predictions")
        if not isinstance(record_predictions, dict):
            errors.append(f"Record {index} is missing predictions.")
            continue
        labels.append(record.get("label") == positive_label)
        for method_name in method_names:
            prediction = record_predictions.get(method_name)
            prediction_label = None
            has_prediction_label = False
            if isinstance(prediction, dict):
                for label_key in ("label", "predicted_label", "predictedLabel"):
                    if label_key in prediction:
                        prediction_label = prediction[label_key]
                        has_prediction_label = True
                        break
            if not isinstance(prediction, dict) or not has_prediction_label:
                errors.append(f"Record {index} is missing prediction label for '{method_name}'.")
                predictions[method_name].append(False)
                missing_probabilities.add(method_name)
                continue
            predictions[method_name].append(prediction_label == positive_label)
            probability = prediction.get("probability")
            if isinstance(probability, (int, float)) and math.isfinite(probability) and 0 <= probability <= 1:
                probabilities[method_name].append(float(probability))
                predicts_positive = prediction_label == positive_label
                method_threshold = decision_thresholds.get(method_name, decision_threshold)
                if predicts_positive != (probability >= method_threshold):
                    errors.append(
                        f"Record {index} prediction label for '{method_name}' is inconsistent "
                        "with its probability and configured decision threshold."
                    )
            else:
                missing_probabilities.add(method_name)

    recomputed: dict[str, float] = {}
    polarity_diagnostics: dict[str, dict[str, Any]] = {}
    if records and len(labels) == len(records):
        for method_name in method_names:
            if len(predictions[method_name]) != len(labels):
                continue
            calculated = _binary_metrics(labels, predictions[method_name])
            for canonical_name, value in calculated.items():
                recomputed[f"{method_name}_{canonical_name}"] = value
            if method_name not in missing_probabilities and len(probabilities[method_name]) == len(labels):
                recomputed[f"{method_name}_expected_calibration_error"] = (
                    _expected_calibration_error(labels, probabilities[method_name])
                )
                recomputed[f"{method_name}_brier_score"] = _brier_score(
                    labels, probabilities[method_name]
                )
                recomputed[f"{method_name}_auroc"] = _auroc(
                    labels, probabilities[method_name]
                )
                observed_auroc = recomputed[f"{method_name}_auroc"]
                inverted_auroc = _auroc(
                    labels, [1.0 - value for value in probabilities[method_name]]
                )
                suspected_inversion = (
                    len(labels) >= 20
                    and observed_auroc <= 0.2
                    and inverted_auroc >= 0.8
                )
                polarity_diagnostics[method_name] = {
                    "observedAuroc": observed_auroc,
                    "invertedAuroc": inverted_auroc,
                    "suspectedInversion": suspected_inversion,
                }
                if suspected_inversion:
                    errors.append(
                        f"Probability polarity for '{method_name}' is likely reversed: "
                        f"AUROC={observed_auroc:.6g}, while 1-probability gives "
                        f"AUROC={inverted_auroc:.6g} for positive_class='{positive_class}'."
                    )

    for metric, (method_name, canonical_name) in parsed_metrics:
        audit_name = f"{method_name}_{canonical_name}"
        if canonical_name == "expected_calibration_error" and method_name in missing_probabilities:
            errors.append(
                f"Metric '{metric.name}' cannot be audited because '{method_name}' probabilities are missing or invalid."
            )
            continue
        expected = recomputed.get(audit_name)
        try:
            reported = float(metric.value)
        except (TypeError, ValueError):
            errors.append(f"Metric '{metric.name}' is not numeric.")
            continue
        if expected is None:
            errors.append(f"Metric '{metric.name}' could not be independently recomputed.")
        elif not math.isclose(reported, expected, rel_tol=1e-6, abs_tol=1e-8):
            errors.append(
                f"Metric '{metric.name}' reports {reported:.10g}, but FAROS recomputed {expected:.10g}."
            )

    return {
        "status": "passed" if not errors else "failed",
        "schemaVersion": schema_version or "faros-evaluation/v1",
        "sourcePath": "evaluation_records.json",
        "positiveLabel": positive_label,
        "positiveClass": positive_class,
        "decisionThreshold": decision_threshold,
        "decisionThresholds": {
            name: decision_thresholds.get(name, decision_threshold)
            for name in method_names
        },
        "recordCount": len(records),
        "benchmarkAudit": benchmark_audit,
        "benchmarkAlignment": benchmark_alignment,
        "benchmarkFingerprint": benchmark_audit.get("fingerprint", ""),
        "ablationAudit": {
            "required": require_ablation,
            "status": "passed" if not require_ablation or bool(ablation_names) else "failed",
            "variants": ablation_names,
        },
        "polarityDiagnostics": polarity_diagnostics,
        "recomputedMetrics": recomputed,
        "errors": errors,
    }


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
    require_ablation: bool = False,
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
    evaluation_records_path = root / "evaluation_records.json"
    frozen_benchmark_path = root / FROZEN_BENCHMARK_RELATIVE_PATH
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
    evaluation_inputs_hash = _evaluation_inputs_hash(evaluation_records_path)
    if evaluation_inputs_hash:
        data_hashes["evaluation_inputs"] = evaluation_inputs_hash
    _benchmark_payload, benchmark_audit = load_frozen_benchmark(root)
    if benchmark_audit.get("status") == "passed" and benchmark_audit.get("fingerprint"):
        data_hashes["frozen_benchmark"] = str(benchmark_audit["fingerprint"])

    scientific_metrics = _metric_evidence(metrics, metrics_path.relative_to(root).as_posix())
    metric_audit = _audit_classification_metrics(
        root,
        scientific_metrics,
        require_ablation=require_ablation,
    )
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
    failures.extend(
        str(item)
        for item in execution_result.get("integrity_failures", [])
        if str(item).strip()
    )
    failures.extend(str(item) for item in metric_audit.get("errors", []))

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
    if evaluation_records_path.is_file():
        artifact_refs.append(ArtifactRef(
            id=f"{code_run_id}:evaluation-records",
            kind=ArtifactKind.OTHER,
            sourceModule=TargetModule.CODE,
            uri=evaluation_records_path.relative_to(root).as_posix(),
            contentHash=_sha256_file(evaluation_records_path),
            version="1",
            createdAt=now,
        ))
    if frozen_benchmark_path.is_file():
        artifact_refs.append(ArtifactRef(
            id=f"{code_run_id}:frozen-benchmark",
            kind=ArtifactKind.OTHER,
            sourceModule=TargetModule.CODE,
            uri=frozen_benchmark_path.relative_to(root).as_posix(),
            contentHash=_sha256_file(frozen_benchmark_path),
            version="1",
            createdAt=now,
        ))

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
        metricAudit=metric_audit,
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
    if evaluation_records_path.is_file():
        hash_targets.append(evaluation_records_path)
    if frozen_benchmark_path.is_file():
        hash_targets.append(frozen_benchmark_path)
    _write_json(
        artifact_hashes_path,
        {path.relative_to(root).as_posix(): _sha256_file(path) for path in hash_targets if path.is_file()},
    )
    return evidence
