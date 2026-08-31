import csv
import json
from pathlib import Path

import numpy as np

from experiments.reviewx_multidomain.run import (
    CLIMATE_SHA256,
    PUBHEALTH_LABELS,
    _climate_bucket,
    _load_pubhealth_split,
    extended_metrics,
    load_climate_fever,
    select_threshold,
    select_threshold_with_gate,
)
from experiments.reviewx_scifact.run import Example


def test_climate_loader_keeps_all_evidence_for_one_claim_in_one_partition(tmp_path: Path):
    source = tmp_path / "climate-fever.jsonl"
    source.write_text(json.dumps({
        "claim_id": "42",
        "claim": "Warming changes ecosystems.",
        "claim_label": "SUPPORTS",
        "evidences": [
            {"evidence_id": "a:1", "evidence_label": "SUPPORTS", "article": "A", "evidence": "Warming changes ecosystems."},
            {"evidence_id": "b:2", "evidence_label": "REFUTES", "article": "B", "evidence": "Observed ecosystems did not change."},
        ],
    }) + "\n", encoding="utf-8")

    dataset = load_climate_fever(source)
    partitions = [items for items in (dataset.train, dataset.validation, dataset.test) if items]

    assert len(partitions) == 1
    assert len(partitions[0]) == 2
    assert {item.label for item in partitions[0]} == {0, 1}
    assert dataset.source_sha256 == CLIMATE_SHA256


def test_climate_split_is_deterministic_and_claim_grouped():
    assert _climate_bucket("same-claim") == _climate_bucket("same-claim")
    assert 0 <= _climate_bucket("same-claim") < 20


def test_pubhealth_loader_filters_malformed_and_missing_rows(tmp_path: Path):
    path = tmp_path / "train.tsv"
    fieldnames = ["claim_id", "claim", "main_text", "label", "subjects"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerow({"claim_id": "1", "claim": "Claim", "main_text": "Evidence", "label": "true", "subjects": "health"})
        writer.writerow({"claim_id": "2", "claim": "Claim", "main_text": "", "label": "false", "subjects": "health"})
        writer.writerow({"claim_id": "3", "claim": "Claim", "main_text": "Evidence", "label": "bad-label", "subjects": "health"})

    examples, dropped = _load_pubhealth_split(path, "train")

    assert PUBHEALTH_LABELS == {"true", "false", "mixture", "unproven"}
    assert len(examples) == 1
    assert examples[0].label == 0
    assert dropped == 2


def test_extended_metrics_expose_imbalanced_all_positive_prediction():
    labels = np.asarray([1, 1, 1, 0], dtype=float)
    probabilities = np.asarray([0.9, 0.9, 0.9, 0.9], dtype=float)

    metrics = extended_metrics(labels, probabilities)

    assert metrics["F1-Score"] > 0.8
    assert metrics["Balanced Accuracy"] == 0.5
    assert metrics["Support F1"] == 0.0
    assert metrics["Macro F1"] < 0.5
    assert metrics["Matthews Correlation Coefficient"] == 0.0


def test_threshold_selection_uses_validation_labels_without_changing_probabilities():
    labels = np.asarray([0, 0, 1, 1], dtype=float)
    probabilities = np.asarray([0.2, 0.3, 0.4, 0.6], dtype=float)

    threshold, metrics = select_threshold(labels, probabilities)

    assert threshold == 0.4
    assert metrics["Macro F1"] == 1.0


def _validation_examples(labels: list[int]) -> list[Example]:
    return [
        Example(
            sample_id=f"sample-{index}",
            split="validation",
            claim_id=index,
            document_id=index,
            claim=f"claim {index}",
            document_title="title",
            document_text="evidence",
            relation="UNSUPPORTED" if label else "SUPPORTS",
            label=label,
        )
        for index, label in enumerate(labels)
    ]


def test_validation_gate_applies_only_cluster_significant_threshold():
    labels = np.asarray(([0] * 20) + ([1] * 20), dtype=float)
    probabilities = np.asarray(([0.6] * 20) + ([0.8] * 20), dtype=float)

    threshold, selection = select_threshold_with_gate(
        _validation_examples(labels.astype(int).tolist()),
        labels,
        probabilities,
        bootstrap_samples=200,
    )

    assert threshold > 0.6
    assert selection["gateDecision"] == "apply_revision"
    assert selection["pairedClusterBootstrap"]["ci95Low"] > 0
    assert selection["selectionProtocol"] == "disjoint_claim_group_proposal_and_gate"
    assert selection["splitAudit"]["groupIntersection"] == 0
    assert (
        selection["splitAudit"]["proposalPairs"]
        + selection["splitAudit"]["gatePairs"]
        == len(labels)
    )
    assert selection["testLabelsUsedForSelection"] is False


def test_validation_gate_keeps_round_one_when_interval_crosses_zero(monkeypatch):
    labels = np.asarray([0, 0, 1, 1], dtype=float)
    probabilities = np.asarray([0.2, 0.3, 0.4, 0.6], dtype=float)
    monkeypatch.setattr(
        "experiments.reviewx_multidomain.run._cluster_bootstrap_threshold_delta",
        lambda *args, **kwargs: {
            "improvementMean": 0.01,
            "ci95Low": -0.01,
            "ci95High": 0.03,
            "probabilityOfImprovement": 0.8,
            "effectStatus": "inconclusive",
            "resamplingUnit": "claim_id",
            "claimGroups": 4,
            "samples": 200,
            "seed": 20260901,
        },
    )

    threshold, selection = select_threshold_with_gate(
        _validation_examples(labels.astype(int).tolist()),
        labels,
        probabilities,
        bootstrap_samples=200,
    )

    assert threshold == 0.5
    assert selection["gateDecision"] == "keep_round_one"
    assert selection["gateChecks"]["macroF1ClusterCIExcludesZero"] is False
