"""Run a reproducible ReviewX experiment on the human-annotated SciFact corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import tarfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np


DATASET_URL = "https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz"
DATASET_SHA256 = "11c621288d41ac144d29b13b0f8503b3820b7d6e8b1f6ff24dff335c196d76be"
DATASET_PAPER = "https://aclanthology.org/2020.emnlp-main.609/"
DATASET_REPOSITORY = "https://github.com/allenai/scifact"
BENCHMARK_SCHEMA = "faros-benchmark/v1"
EVALUATION_SCHEMA = "faros-evaluation/v1"
POSITIVE_CLASS = "unsupported"
POSITIVE_LABEL = 1
DECISION_THRESHOLD = 0.5
TRAINING_SEED = 20260825
BOOTSTRAP_SEED = 20260826

STOPWORDS = set(
    "a an the and or of to in on for with by from as at is are was were be been being "
    "that this these those it its their there which who whom can could may might will "
    "would should have has had do does did than into between among about after before "
    "during through using use used we our they".split()
)
NEGATION_TERMS = set(
    "no not never neither nor without absent lack lacks lacked insufficient fail fails "
    "failed cannot unlikely unchanged".split()
)
FEATURE_NAMES = (
    "claim_token_coverage",
    "token_jaccard",
    "idf_weighted_coverage",
    "claim_bigram_coverage",
    "numeric_alignment",
    "negation_alignment",
    "entity_alignment",
    "claim_has_negation",
    "document_has_negation",
    "coverage_x_negation",
    "idf_coverage_x_negation",
    "coverage_x_entity",
)
NEGATION_FEATURES = {5, 7, 8, 9, 10}
NUMERIC_FEATURES = {4}
ENTITY_FEATURES = {6, 11}


@dataclass(frozen=True)
class Example:
    sample_id: str
    split: str
    claim_id: int
    document_id: int
    claim: str
    document_title: str
    document_text: str
    relation: str
    label: int


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_dataset(data_dir: Path, *, download: bool) -> Path:
    dataset_root = data_dir / "data"
    required = [
        dataset_root / "corpus.jsonl",
        dataset_root / "claims_train.jsonl",
        dataset_root / "claims_dev.jsonl",
    ]
    if all(path.is_file() for path in required):
        return dataset_root
    if not download:
        raise FileNotFoundError(
            f"SciFact is missing from {data_dir}. Run again with --download."
        )

    data_dir.mkdir(parents=True, exist_ok=True)
    archive = data_dir / "data.tar.gz"
    if not archive.is_file() or _sha256(archive) != DATASET_SHA256:
        request = urllib.request.Request(DATASET_URL, headers={"User-Agent": "FAROS-SciFact/1.0"})
        with urllib.request.urlopen(request, timeout=60) as response, archive.open("wb") as target:
            shutil.copyfileobj(response, target)
    actual_hash = _sha256(archive)
    if actual_hash != DATASET_SHA256:
        raise ValueError(f"SciFact archive hash mismatch: {actual_hash}")

    with tarfile.open(archive, "r:gz") as bundle:
        destination = data_dir.resolve()
        for member in bundle.getmembers():
            member_path = (destination / member.name).resolve()
            if destination not in member_path.parents and member_path != destination:
                raise ValueError(f"Unsafe dataset archive member: {member.name}")
        bundle.extractall(data_dir, filter="data")
    if not all(path.is_file() for path in required):
        raise FileNotFoundError("SciFact archive did not contain the documented files.")
    return dataset_root


def load_examples(dataset_root: Path, split: str) -> list[Example]:
    corpus = {int(item["doc_id"]): item for item in _read_jsonl(dataset_root / "corpus.jsonl")}
    examples: list[Example] = []
    seen_pairs: set[tuple[int, int]] = set()
    for claim in _read_jsonl(dataset_root / f"claims_{split}.jsonl"):
        evidence = claim.get("evidence") or {}
        for document_id_raw in claim.get("cited_doc_ids") or []:
            document_id = int(document_id_raw)
            pair = (int(claim["id"]), document_id)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            annotations = evidence.get(str(document_id)) or []
            relation = str(annotations[0].get("label")) if annotations else "NEI"
            document = corpus[document_id]
            examples.append(Example(
                sample_id=f"scifact-{split}-{claim['id']}-{document_id}",
                split=split,
                claim_id=int(claim["id"]),
                document_id=document_id,
                claim=str(claim["claim"]),
                document_title=str(document.get("title") or ""),
                document_text=" ".join(str(item) for item in document.get("abstract") or []),
                relation=relation,
                label=0 if relation == "SUPPORT" else POSITIVE_LABEL,
            ))
    return examples


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", text.lower())


def _content_tokens(text: str) -> list[str]:
    return [token for token in _tokens(text) if token not in STOPWORDS and len(token) > 1]


class FactorizedFeatureExtractor:
    def __init__(self) -> None:
        self.document_count = 0
        self.document_frequency: dict[str, int] = {}

    def fit(self, examples: Sequence[Example]) -> "FactorizedFeatureExtractor":
        self.document_count = len(examples)
        frequency: dict[str, int] = {}
        for example in examples:
            for token in set(_content_tokens(example.document_title + " " + example.document_text)):
                frequency[token] = frequency.get(token, 0) + 1
        self.document_frequency = frequency
        return self

    def _idf(self, token: str) -> float:
        return math.log(
            (self.document_count + 1) / (self.document_frequency.get(token, 0) + 1)
        ) + 1.0

    def transform_one(self, example: Example) -> np.ndarray:
        claim_tokens = _content_tokens(example.claim)
        document = example.document_title + " " + example.document_text
        document_tokens = _content_tokens(document)
        claim_set = set(claim_tokens)
        document_set = set(document_tokens)
        overlap = claim_set & document_set
        union = claim_set | document_set
        coverage = len(overlap) / max(1, len(claim_set))
        jaccard = len(overlap) / max(1, len(union))
        idf_total = sum(self._idf(token) for token in claim_set)
        idf_coverage = sum(self._idf(token) for token in overlap) / max(1e-12, idf_total)
        claim_bigrams = set(zip(claim_tokens, claim_tokens[1:]))
        document_bigrams = set(zip(document_tokens, document_tokens[1:]))
        bigram_coverage = len(claim_bigrams & document_bigrams) / max(1, len(claim_bigrams))
        claim_numbers = set(re.findall(r"\b\d+(?:\.\d+)?%?\b", example.claim))
        document_numbers = set(re.findall(r"\b\d+(?:\.\d+)?%?\b", document))
        numeric_alignment = (
            1.0 if not claim_numbers else len(claim_numbers & document_numbers) / len(claim_numbers)
        )
        claim_negation = any(token in NEGATION_TERMS for token in _tokens(example.claim))
        document_negation = any(token in NEGATION_TERMS for token in _tokens(document))
        negation_alignment = float(claim_negation == document_negation)
        claim_entities = set(re.findall(r"\b(?:[A-Z][A-Za-z0-9-]{2,}|[A-Z]{2,})\b", example.claim))
        document_entities = set(re.findall(r"\b(?:[A-Z][A-Za-z0-9-]{2,}|[A-Z]{2,})\b", document))
        entity_alignment = (
            1.0 if not claim_entities else len(claim_entities & document_entities) / len(claim_entities)
        )
        return np.asarray([
            coverage,
            jaccard,
            idf_coverage,
            bigram_coverage,
            numeric_alignment,
            negation_alignment,
            entity_alignment,
            float(claim_negation),
            float(document_negation),
            coverage * negation_alignment,
            idf_coverage * negation_alignment,
            coverage * entity_alignment,
        ], dtype=float)

    def transform(self, examples: Sequence[Example]) -> np.ndarray:
        return np.vstack([self.transform_one(example) for example in examples])


class NumpyLogisticRegression:
    def __init__(self, *, learning_rate: float = 0.04, iterations: int = 6000, l2: float = 0.02):
        self.learning_rate = learning_rate
        self.iterations = iterations
        self.l2 = l2
        self.mean: np.ndarray | None = None
        self.scale: np.ndarray | None = None
        self.weights: np.ndarray | None = None

    def fit(self, features: np.ndarray, labels: np.ndarray) -> "NumpyLogisticRegression":
        self.mean = features.mean(axis=0)
        self.scale = features.std(axis=0) + 1e-8
        standardized = (features - self.mean) / self.scale
        design = np.column_stack([np.ones(len(standardized)), standardized])
        weights = np.zeros(design.shape[1], dtype=float)
        for _ in range(self.iterations):
            logits = np.clip(design @ weights, -30.0, 30.0)
            probabilities = 1.0 / (1.0 + np.exp(-logits))
            gradient = design.T @ (probabilities - labels) / len(labels)
            gradient[1:] += self.l2 * weights[1:]
            weights -= self.learning_rate * gradient
        self.weights = weights
        return self

    def predict_proba(self, features: np.ndarray) -> np.ndarray:
        if self.mean is None or self.scale is None or self.weights is None:
            raise RuntimeError("Model must be fitted before prediction.")
        standardized = (features - self.mean) / self.scale
        design = np.column_stack([np.ones(len(standardized)), standardized])
        logits = np.clip(design @ self.weights, -30.0, 30.0)
        return 1.0 / (1.0 + np.exp(-logits))


def _auroc(labels: np.ndarray, probabilities: np.ndarray) -> float:
    positive_scores = probabilities[labels == POSITIVE_LABEL]
    negative_scores = probabilities[labels != POSITIVE_LABEL]
    if not len(positive_scores) or not len(negative_scores):
        return float("nan")
    comparisons = positive_scores[:, None] - negative_scores[None, :]
    wins = float((comparisons > 0).sum())
    ties = float((comparisons == 0).sum())
    return (wins + 0.5 * ties) / comparisons.size


def compute_metrics(labels: np.ndarray, probabilities: np.ndarray) -> dict[str, float]:
    predictions = probabilities >= DECISION_THRESHOLD
    positive = labels == POSITIVE_LABEL
    true_positive = int((predictions & positive).sum())
    false_positive = int((predictions & ~positive).sum())
    false_negative = int((~predictions & positive).sum())
    precision = true_positive / max(1, true_positive + false_positive)
    recall = true_positive / max(1, true_positive + false_negative)
    f1 = 2 * precision * recall / max(1e-12, precision + recall)
    brier = float(np.mean((probabilities - labels) ** 2))
    ece = 0.0
    for index in range(10):
        lower, upper = index / 10, (index + 1) / 10
        mask = (probabilities >= lower) & (
            probabilities <= upper if index == 9 else probabilities < upper
        )
        if mask.any():
            ece += float(mask.mean()) * abs(
                float(probabilities[mask].mean()) - float(labels[mask].mean())
            )
    return {
        "Precision": float(precision),
        "Recall": float(recall),
        "F1-Score": float(f1),
        "Brier Score": brier,
        "Expected Calibration Error (ECE)": float(ece),
        "AUROC": _auroc(labels, probabilities),
    }


def paired_bootstrap(
    labels: np.ndarray,
    baseline: np.ndarray,
    method: np.ndarray,
    *,
    samples: int,
) -> dict[str, dict[str, float]]:
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    values: dict[str, list[float]] = {
        "F1-Score": [],
        "Brier Score": [],
        "Expected Calibration Error (ECE)": [],
        "AUROC": [],
    }
    directions = {
        "F1-Score": 1.0,
        "Brier Score": -1.0,
        "Expected Calibration Error (ECE)": -1.0,
        "AUROC": 1.0,
    }
    for _ in range(samples):
        indices = rng.integers(0, len(labels), size=len(labels))
        sample_labels = labels[indices]
        if len(set(sample_labels.tolist())) < 2:
            continue
        baseline_metrics = compute_metrics(sample_labels, baseline[indices])
        method_metrics = compute_metrics(sample_labels, method[indices])
        for metric, direction in directions.items():
            values[metric].append(
                direction * (method_metrics[metric] - baseline_metrics[metric])
            )
    result: dict[str, dict[str, float]] = {}
    for metric, improvements in values.items():
        array = np.asarray(improvements)
        result[metric] = {
            "improvementMean": float(array.mean()),
            "ci95Low": float(np.percentile(array, 2.5)),
            "ci95High": float(np.percentile(array, 97.5)),
            "probabilityOfImprovement": float((array > 0).mean()),
        }
    return result


def _canonical_fingerprint(payload: dict[str, Any]) -> str:
    canonical = {key: value for key, value in payload.items() if key != "fingerprint"}
    encoded = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _metric_records(results: dict[str, dict[str, float]]) -> list[dict[str, Any]]:
    definitions = {
        "Precision": "Unsupported-pair precision at decision threshold 0.5.",
        "Recall": "Unsupported-pair recall at decision threshold 0.5.",
        "F1-Score": "Harmonic mean of unsupported-pair precision and recall.",
        "Brier Score": "Mean squared error of unsupported-class probabilities; lower is better.",
        "Expected Calibration Error (ECE)": "Ten equal-width-bin calibration error; lower is better.",
        "AUROC": "Area under the ROC curve for unsupported claim-document pairs.",
    }
    return [
        {
            "name": f"{method}:{metric}",
            "value": value,
            "unit": "ratio",
            "definition": definitions[metric],
            "split": "scifact_dev",
        }
        for method, metrics in results.items()
        for metric, value in metrics.items()
    ]


def _report(summary: dict[str, Any]) -> str:
    lines = [
        "# ReviewX SciFact Real-Data Experiment",
        "",
        "## Scope",
        "",
        "This experiment evaluates claim-document support detection on the official SciFact train/dev split.",
        "CONTRADICT and NEI pairs are grouped as the positive `unsupported` class. No test labels or dev labels are used for training.",
        "",
        f"- Train pairs: {summary['dataset']['trainPairs']}",
        f"- Dev pairs: {summary['dataset']['devPairs']}",
        f"- Dataset SHA-256: `{summary['dataset']['archiveSha256']}`",
        "",
        "## Results",
        "",
        "| Method | F1 | Brier | ECE | AUROC |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for method, metrics in summary["results"].items():
        lines.append(
            f"| {method} | {metrics['F1-Score']:.4f} | {metrics['Brier Score']:.4f} | "
            f"{metrics['Expected Calibration Error (ECE)']:.4f} | {metrics['AUROC']:.4f} |"
        )
    lines.extend([
        "",
        "## Paired bootstrap",
        "",
        "Positive values mean that the full method improves over the lexical baseline after accounting for metric direction.",
        "",
        "| Metric | Mean improvement | 95% CI | P(improvement) |",
        "| --- | ---: | ---: | ---: |",
    ])
    for metric, result in summary["pairedBootstrap"].items():
        lines.append(
            f"| {metric} | {result['improvementMean']:.4f} | "
            f"[{result['ci95Low']:.4f}, {result['ci95High']:.4f}] | "
            f"{result['probabilityOfImprovement']:.3f} |"
        )
    lines.extend([
        "",
        "## Interpretation boundary",
        "",
        "The result supports a real-data proof of concept on SciFact abstracts. It does not establish generalization to full papers, other domains, or human review usefulness.",
        "SciFact does not expose a standard SPDX license in its source repository; this experiment does not redistribute the dataset.",
        "",
        f"Quality gate: **{summary['qualityGate']['status']}**",
        "",
    ])
    return "\n".join(lines)


def run_experiment(
    dataset_root: Path,
    output_dir: Path,
    *,
    bootstrap_samples: int = 2000,
) -> dict[str, Any]:
    started = time.monotonic()
    train = load_examples(dataset_root, "train")
    dev = load_examples(dataset_root, "dev")
    extractor = FactorizedFeatureExtractor().fit(train)
    train_features = extractor.transform(train)
    dev_features = extractor.transform(dev)
    train_labels = np.asarray([example.label for example in train], dtype=float)
    dev_labels = np.asarray([example.label for example in dev], dtype=float)

    baseline = np.clip(1.0 - dev_features[:, 2], 0.01, 0.99)
    variants = {
        "method": set(range(len(FEATURE_NAMES))),
        "ablation_no_negation": set(range(len(FEATURE_NAMES))) - NEGATION_FEATURES,
        "ablation_no_numeric": set(range(len(FEATURE_NAMES))) - NUMERIC_FEATURES,
        "ablation_no_entity": set(range(len(FEATURE_NAMES))) - ENTITY_FEATURES,
    }
    probabilities: dict[str, np.ndarray] = {"baseline": baseline}
    for name, selected in variants.items():
        indices = sorted(selected)
        model = NumpyLogisticRegression().fit(train_features[:, indices], train_labels)
        probabilities[name] = model.predict_proba(dev_features[:, indices])

    results = {
        name: compute_metrics(dev_labels, values)
        for name, values in probabilities.items()
    }
    bootstrap = paired_bootstrap(
        dev_labels,
        probabilities["baseline"],
        probabilities["method"],
        samples=bootstrap_samples,
    )
    baseline_metrics = results["baseline"]
    method_metrics = results["method"]
    gate_checks = {
        "f1ImprovesByAtLeastOnePoint": method_metrics["F1-Score"] >= baseline_metrics["F1-Score"] + 0.01,
        "brierImproves": method_metrics["Brier Score"] < baseline_metrics["Brier Score"],
        "eceImproves": method_metrics["Expected Calibration Error (ECE)"] < baseline_metrics["Expected Calibration Error (ECE)"],
        "aurocDoesNotRegressByMoreThanOnePoint": method_metrics["AUROC"] >= baseline_metrics["AUROC"] - 0.01,
        "f1BootstrapImprovementProbabilityAtLeast80Percent": bootstrap["F1-Score"]["probabilityOfImprovement"] >= 0.8,
    }

    benchmark = {
        "schema_version": BENCHMARK_SCHEMA,
        "benchmark_id": "scifact-claim-document-support-dev-v1",
        "task": "scientific_claim_document_support_detection",
        "positive_label": POSITIVE_LABEL,
        "positive_class": POSITIVE_CLASS,
        "seed": TRAINING_SEED,
        "generator_version": "reviewx-scifact/1.0",
        "feature_schema": list(FEATURE_NAMES),
        "records": [
            {
                "sample_id": example.sample_id,
                "split": "scifact_dev",
                "features": dev_features[index].tolist(),
                "label": example.label,
                "metadata": {
                    "claim_id": example.claim_id,
                    "document_id": example.document_id,
                    "gold_relation": example.relation,
                    "claim": example.claim,
                    "document_title": example.document_title,
                },
            }
            for index, example in enumerate(dev)
        ],
    }
    benchmark["fingerprint"] = _canonical_fingerprint(benchmark)
    evaluation = {
        "schema_version": EVALUATION_SCHEMA,
        "positive_label": POSITIVE_LABEL,
        "positive_class": POSITIVE_CLASS,
        "decision_threshold": DECISION_THRESHOLD,
        "records": [
            {
                "sample_id": example.sample_id,
                "split": "scifact_dev",
                "label": example.label,
                "predictions": {
                    name: {
                        "label": int(values[index] >= DECISION_THRESHOLD),
                        "probability": float(values[index]),
                    }
                    for name, values in probabilities.items()
                },
            }
            for index, example in enumerate(dev)
        ],
    }
    relation_counts = {
        relation: sum(example.relation == relation for example in dev)
        for relation in ("SUPPORT", "CONTRADICT", "NEI")
    }
    summary = {
        "schemaVersion": "reviewx-real-experiment/v1",
        "dataset": {
            "name": "SciFact",
            "url": DATASET_URL,
            "repository": DATASET_REPOSITORY,
            "paper": DATASET_PAPER,
            "archiveSha256": DATASET_SHA256,
            "license": "NOASSERTION; consult the source repository before redistribution",
            "trainPairs": len(train),
            "devPairs": len(dev),
            "devRelations": relation_counts,
        },
        "protocol": {
            "trainSplit": "official claims_train.jsonl",
            "evaluationSplit": "official claims_dev.jsonl",
            "positiveClass": POSITIVE_CLASS,
            "positiveClassDefinition": "SciFact CONTRADICT or no-evidence (NEI) claim-document pair",
            "devLabelsUsedForTraining": False,
            "decisionThreshold": DECISION_THRESHOLD,
            "bootstrapSamples": bootstrap_samples,
            "bootstrapSeed": BOOTSTRAP_SEED,
        },
        "results": results,
        "pairedBootstrap": bootstrap,
        "qualityGate": {
            "status": "passed" if all(gate_checks.values()) else "failed",
            "checks": gate_checks,
        },
        "benchmarkFingerprint": benchmark["fingerprint"],
        "durationSeconds": time.monotonic() - started,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "data" / "frozen_benchmark.json", benchmark)
    _write_json(output_dir / "evaluation_records.json", evaluation)
    _write_json(output_dir / "metrics.json", _metric_records(results))
    _write_json(output_dir / "summary.json", summary)
    (output_dir / "experiment_report.md").write_text(_report(summary), encoding="utf-8")
    source_target = output_dir / "src" / "run_scifact_experiment.py"
    source_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), source_target)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--bootstrap-samples", type=int, default=2000)
    args = parser.parse_args()
    dataset_root = ensure_dataset(args.data_dir, download=args.download)
    summary = run_experiment(
        dataset_root,
        args.output_dir,
        bootstrap_samples=args.bootstrap_samples,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["qualityGate"]["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
