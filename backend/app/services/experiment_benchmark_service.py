"""Frozen benchmark contracts for controlled FAROS experiment iterations."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Mapping


FROZEN_BENCHMARK_RELATIVE_PATH = Path("data/frozen_benchmark.json")
FROZEN_BENCHMARK_SCHEMA_VERSION = "faros-benchmark/v1"


def _canonical_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "fingerprint"}


def frozen_benchmark_fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _canonical_payload(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def validate_frozen_benchmark(payload: Any) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return {
            "status": "failed",
            "schemaVersion": FROZEN_BENCHMARK_SCHEMA_VERSION,
            "recordCount": 0,
            "fingerprint": "",
            "errors": ["Frozen benchmark must contain a JSON object."],
        }

    schema_version = str(payload.get("schema_version") or "")
    benchmark_id = str(payload.get("benchmark_id") or "").strip()
    task = str(payload.get("task") or "").strip()
    positive_class = str(payload.get("positive_class") or "").strip().lower()
    positive_label = payload.get("positive_label")
    seed = payload.get("seed")
    generator_version = str(payload.get("generator_version") or "").strip()
    feature_schema = payload.get("feature_schema")
    records = payload.get("records")

    if schema_version != FROZEN_BENCHMARK_SCHEMA_VERSION:
        errors.append(
            f"Frozen benchmark must declare schema_version={FROZEN_BENCHMARK_SCHEMA_VERSION}."
        )
    if not benchmark_id:
        errors.append("Frozen benchmark must declare benchmark_id.")
    if not task:
        errors.append("Frozen benchmark must declare task.")
    if positive_label is None:
        errors.append("Frozen benchmark must declare positive_label.")
    if not positive_class:
        errors.append("Frozen benchmark must declare positive_class.")
    if not isinstance(seed, int) or isinstance(seed, bool):
        errors.append("Frozen benchmark seed must be an integer.")
    if not generator_version:
        errors.append("Frozen benchmark must declare generator_version.")
    if not isinstance(feature_schema, (list, dict)) or not feature_schema:
        errors.append("Frozen benchmark feature_schema must be a non-empty list or object.")
    if not isinstance(records, list) or not records:
        errors.append("Frozen benchmark must contain a non-empty records list.")
        records = []

    seen_ids: set[str] = set()
    labels: set[Any] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            errors.append(f"Frozen benchmark record {index} must be an object.")
            continue
        sample_id = str(record.get("sample_id") or "").strip()
        if not sample_id:
            errors.append(f"Frozen benchmark record {index} is missing sample_id.")
        elif sample_id in seen_ids:
            errors.append(f"Frozen benchmark sample_id '{sample_id}' is duplicated.")
        else:
            seen_ids.add(sample_id)
        if not str(record.get("split") or "").strip():
            errors.append(f"Frozen benchmark record {index} is missing split.")
        features = record.get("features")
        if not isinstance(features, (list, dict)) or not features:
            errors.append(f"Frozen benchmark record {index} must contain non-empty features.")
        if "label" not in record:
            errors.append(f"Frozen benchmark record {index} is missing label.")
        else:
            try:
                labels.add(record["label"])
            except TypeError:
                errors.append(f"Frozen benchmark record {index} label must be scalar.")

    if records and positive_label not in labels:
        errors.append("Frozen benchmark contains no positive-label records.")
    if records and len(labels) < 2:
        errors.append("Frozen benchmark must contain at least two label classes.")

    fingerprint = frozen_benchmark_fingerprint(payload)
    declared_fingerprint = str(payload.get("fingerprint") or "").strip()
    if declared_fingerprint and declared_fingerprint != fingerprint:
        errors.append("Frozen benchmark fingerprint does not match its canonical content.")

    return {
        "status": "passed" if not errors else "failed",
        "schemaVersion": schema_version or FROZEN_BENCHMARK_SCHEMA_VERSION,
        "benchmarkId": benchmark_id,
        "task": task,
        "positiveLabel": positive_label,
        "positiveClass": positive_class,
        "seed": seed,
        "generatorVersion": generator_version,
        "recordCount": len(records),
        "fingerprint": fingerprint,
        "errors": errors,
    }


def load_frozen_benchmark(repo_dir: str | Path) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    path = Path(repo_dir) / FROZEN_BENCHMARK_RELATIVE_PATH
    if not path.is_file():
        return None, {
            "status": "missing",
            "schemaVersion": FROZEN_BENCHMARK_SCHEMA_VERSION,
            "sourcePath": FROZEN_BENCHMARK_RELATIVE_PATH.as_posix(),
            "recordCount": 0,
            "fingerprint": "",
            "errors": ["Classification experiments require data/frozen_benchmark.json."],
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, {
            "status": "failed",
            "schemaVersion": FROZEN_BENCHMARK_SCHEMA_VERSION,
            "sourcePath": FROZEN_BENCHMARK_RELATIVE_PATH.as_posix(),
            "recordCount": 0,
            "fingerprint": "",
            "errors": [f"Frozen benchmark is invalid JSON: {exc}"],
        }
    result = validate_frozen_benchmark(payload)
    result["sourcePath"] = FROZEN_BENCHMARK_RELATIVE_PATH.as_posix()
    return payload if isinstance(payload, dict) else None, result


def validate_evaluation_alignment(
    evaluation_payload: Mapping[str, Any],
    benchmark_payload: Mapping[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    benchmark_records = benchmark_payload.get("records")
    evaluation_records = evaluation_payload.get("records")
    if not isinstance(benchmark_records, list) or not isinstance(evaluation_records, list):
        return {
            "status": "failed",
            "matchedRecordCount": 0,
            "errors": ["Benchmark and evaluation records must both be lists."],
        }

    benchmark_by_id = {
        str(record.get("sample_id")): record
        for record in benchmark_records
        if isinstance(record, dict) and record.get("sample_id")
    }
    evaluation_by_id: dict[str, Mapping[str, Any]] = {}
    for index, record in enumerate(evaluation_records):
        if not isinstance(record, dict):
            errors.append(f"Evaluation record {index} must be an object.")
            continue
        sample_id = str(record.get("sample_id") or "").strip()
        if not sample_id:
            errors.append(f"Evaluation record {index} is missing sample_id.")
            continue
        if sample_id in evaluation_by_id:
            errors.append(f"Evaluation sample_id '{sample_id}' is duplicated.")
            continue
        evaluation_by_id[sample_id] = record

    missing_ids = sorted(set(benchmark_by_id) - set(evaluation_by_id))
    extra_ids = sorted(set(evaluation_by_id) - set(benchmark_by_id))
    if missing_ids:
        errors.append(f"Evaluation records omit {len(missing_ids)} frozen benchmark samples.")
    if extra_ids:
        errors.append(f"Evaluation records add {len(extra_ids)} samples outside the frozen benchmark.")

    for sample_id in sorted(set(benchmark_by_id) & set(evaluation_by_id)):
        benchmark_record = benchmark_by_id[sample_id]
        evaluation_record = evaluation_by_id[sample_id]
        if evaluation_record.get("label") != benchmark_record.get("label"):
            errors.append(f"Evaluation label changed for frozen sample '{sample_id}'.")
        if str(evaluation_record.get("split") or "") != str(benchmark_record.get("split") or ""):
            errors.append(f"Evaluation split changed for frozen sample '{sample_id}'.")

    for field in ("positive_label", "positive_class"):
        if evaluation_payload.get(field) != benchmark_payload.get(field):
            errors.append(f"Evaluation {field} does not match the frozen benchmark.")

    return {
        "status": "passed" if not errors else "failed",
        "matchedRecordCount": len(set(benchmark_by_id) & set(evaluation_by_id)),
        "errors": errors,
    }


def inherit_frozen_benchmark(
    *,
    source_repo_dir: str | Path,
    target_repo_dir: str | Path,
    expected_fingerprint: str = "",
) -> dict[str, Any]:
    payload, result = load_frozen_benchmark(source_repo_dir)
    if payload is None or result.get("status") != "passed":
        return result
    if expected_fingerprint and result.get("fingerprint") != expected_fingerprint:
        return {
            **result,
            "status": "failed",
            "errors": ["Parent frozen benchmark does not match the recorded fingerprint."],
        }

    source = Path(source_repo_dir) / FROZEN_BENCHMARK_RELATIVE_PATH
    target = Path(target_repo_dir) / FROZEN_BENCHMARK_RELATIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return {**result, "inherited": True, "sourcePath": target.relative_to(target_repo_dir).as_posix()}
