import json
import sys
from pathlib import Path

import numpy as np
import pytest


sys.path.insert(0, str(Path(__file__).parent.parent))

from experiments.reviewx_scifact.run import (
    Example,
    FactorizedFeatureExtractor,
    NumpyLogisticRegression,
    _auroc,
    compute_metrics,
    load_examples,
    paired_bootstrap,
)


def _example(sample_id: str, claim: str, document: str, label: int) -> Example:
    return Example(
        sample_id=sample_id,
        split="train",
        claim_id=int(sample_id),
        document_id=int(sample_id),
        claim=claim,
        document_title="Study",
        document_text=document,
        relation="SUPPORT" if label == 0 else "CONTRADICT",
        label=label,
    )


def test_factorized_features_are_finite_and_capture_negation_alignment():
    examples = [
        _example("1", "Treatment reduces mortality", "Treatment reduces mortality", 0),
        _example("2", "Treatment does not reduce mortality", "Treatment reduces mortality", 1),
    ]
    extractor = FactorizedFeatureExtractor().fit(examples)
    features = extractor.transform(examples)

    assert features.shape == (2, 12)
    assert np.isfinite(features).all()
    assert features[0, 5] == 1.0
    assert features[1, 5] == 0.0


def test_numpy_logistic_regression_is_deterministic():
    features = np.asarray([[0.0], [0.1], [0.9], [1.0]])
    labels = np.asarray([0.0, 0.0, 1.0, 1.0])
    first = NumpyLogisticRegression(iterations=1000).fit(features, labels).predict_proba(features)
    second = NumpyLogisticRegression(iterations=1000).fit(features, labels).predict_proba(features)

    assert np.allclose(first, second)
    assert first[0] < first[-1]


def test_metrics_and_bootstrap_use_improvement_direction_consistently():
    labels = np.asarray([0.0, 0.0, 1.0, 1.0] * 20)
    baseline = np.asarray([0.4, 0.6, 0.4, 0.6] * 20)
    method = np.asarray([0.1, 0.2, 0.8, 0.9] * 20)

    metrics = compute_metrics(labels, method)
    bootstrap = paired_bootstrap(labels, baseline, method, samples=100)

    assert metrics["F1-Score"] == 1.0
    assert bootstrap["F1-Score"]["probabilityOfImprovement"] > 0.95
    assert bootstrap["Brier Score"]["improvementMean"] > 0


def test_scifact_loader_maps_support_vs_unsupported_pairs(tmp_path):
    corpus = [
        {"doc_id": 10, "title": "A", "abstract": ["Evidence sentence."]},
        {"doc_id": 11, "title": "B", "abstract": ["Other sentence."]},
    ]
    claims = [{
        "id": 1,
        "claim": "A scientific claim.",
        "cited_doc_ids": [10, 11],
        "evidence": {"10": [{"label": "SUPPORT", "sentences": [0]}]},
    }]
    (tmp_path / "corpus.jsonl").write_text(
        "\n".join(json.dumps(item) for item in corpus), encoding="utf-8"
    )
    (tmp_path / "claims_dev.jsonl").write_text(
        "\n".join(json.dumps(item) for item in claims), encoding="utf-8"
    )

    examples = load_examples(tmp_path, "dev")

    assert [(item.relation, item.label) for item in examples] == [
        ("SUPPORT", 0),
        ("NEI", 1),
    ]


def test_scifact_loader_deduplicates_claim_document_pairs(tmp_path):
    corpus = [{"doc_id": 10, "title": "A", "abstract": ["Evidence sentence."]}]
    claims = [{
        "id": 1,
        "claim": "A scientific claim.",
        "cited_doc_ids": [10, 10],
        "evidence": {"10": [{"label": "SUPPORT", "sentences": [0]}]},
    }]
    (tmp_path / "corpus.jsonl").write_text(
        "\n".join(json.dumps(item) for item in corpus), encoding="utf-8"
    )
    (tmp_path / "claims_dev.jsonl").write_text(
        "\n".join(json.dumps(item) for item in claims), encoding="utf-8"
    )

    examples = load_examples(tmp_path, "dev")

    assert [item.sample_id for item in examples] == ["scifact-dev-1-10"]


def test_auroc_counts_equal_scores_as_half_a_win():
    labels = np.asarray([1, 1, 0, 0], dtype=float)
    probabilities = np.asarray([0.8, 0.5, 0.5, 0.2], dtype=float)

    assert _auroc(labels, probabilities) == pytest.approx(0.875)
