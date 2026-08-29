from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import random
import urllib.request
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from app.modules.code.challenge_cases.runtime import finalize_case_cart
from app.modules.code.execution_assessment import ExecutionClass


SOURCE_URL = "https://archive.ics.uci.edu/static/public/53/iris.zip"
SOURCE_DOI = "10.24432/C56C76"
ARCHIVE_SHA256 = "d11fe30213d36434a0879aab7cb00ce3c812eb7ba2495874438abff7b7b762e9"
SEED = 42


def _download() -> bytes:
    with urllib.request.urlopen(SOURCE_URL, timeout=30) as response:
        payload = response.read()
    actual = hashlib.sha256(payload).hexdigest()
    if actual != ARCHIVE_SHA256:
        raise RuntimeError(f"UCI archive hash changed: expected {ARCHIVE_SHA256}, got {actual}")
    return payload


def _load_rows(archive_bytes: bytes) -> list[tuple[list[float], str]]:
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        raw = archive.read("bezdekIris.data").decode("utf-8")
    rows = []
    for row in csv.reader(io.StringIO(raw)):
        if len(row) == 5:
            rows.append(([float(value) for value in row[:4]], row[4]))
    if len(rows) != 150:
        raise RuntimeError(f"Expected 150 Iris records, got {len(rows)}")
    return rows


def run(output_root: Path) -> Path:
    archive = _download()
    rows = _load_rows(archive)
    grouped: dict[str, list[tuple[list[float], str]]] = defaultdict(list)
    for item in rows:
        grouped[item[1]].append(item)
    rng = random.Random(SEED)
    train, test = [], []
    for label in sorted(grouped):
        items = list(grouped[label])
        rng.shuffle(items)
        test.extend(items[:10])
        train.extend(items[10:])

    centroids = {}
    for label in sorted(grouped):
        vectors = [features for features, target in train if target == label]
        centroids[label] = [sum(column) / len(vectors) for column in zip(*vectors)]

    def predict(features: list[float]) -> str:
        return min(
            centroids,
            key=lambda label: sum((value - center) ** 2 for value, center in zip(features, centroids[label])),
        )

    predictions = [(target, predict(features)) for features, target in test]
    accuracy = sum(actual == predicted for actual, predicted in predictions) / len(predictions)
    majority = Counter(target for _, target in train).most_common(1)[0][0]
    baseline_accuracy = sum(actual == majority for _, actual in test) / len(test)
    improvement = accuracy - baseline_accuracy

    normalized = io.StringIO()
    writer = csv.writer(normalized, lineterminator="\n")
    writer.writerow(["sepal_length", "sepal_width", "petal_length", "petal_width", "class"])
    for features, label in rows:
        writer.writerow([*features, label])
    prediction_csv = io.StringIO()
    prediction_writer = csv.writer(prediction_csv, lineterminator="\n")
    prediction_writer.writerow(["actual", "predicted"])
    prediction_writer.writerows(predictions)

    metrics = {
        "accuracy": round(accuracy, 6),
        "majority_baseline_accuracy": round(baseline_accuracy, 6),
        "accuracy_improvement": round(improvement, 6),
        "test_samples": len(test),
    }
    config = {
        "sourceUrl": SOURCE_URL,
        "sourceDoi": SOURCE_DOI,
        "archiveSha256": f"sha256:{ARCHIVE_SHA256}",
        "datasetFile": "bezdekIris.data",
        "seed": SEED,
        "trainSamples": len(train),
        "testSamples": len(test),
        "method": "nearest centroid",
        "baseline": "majority class",
        "stopCondition": "one deterministic 120/30 split",
    }
    return finalize_case_cart(
        output_root=output_root,
        case_id="case_01_iris",
        project_source=Path(__file__).parent,
        execution_class=ExecutionClass.COMPUTATIONAL_READY,
        metrics=metrics,
        artifacts={
            "iris.csv": normalized.getvalue(),
            "predictions.csv": prediction_csv.getvalue(),
            "source_archive.zip": archive,
        },
        config=config,
        method="Deterministic nearest-centroid classification on the UCI Iris dataset.",
        baseline="Majority-class classifier on the same fixed test split.",
        log_text=json.dumps(metrics, ensure_ascii=False) + "\n",
        expected=[{"metric": "accuracy_improvement", "target": "> 0"}],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(run(args.output))
