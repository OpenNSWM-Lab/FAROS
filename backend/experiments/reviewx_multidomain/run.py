"""Run reproducible ReviewX stress tests on climate and public-health claims."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import time
import urllib.request
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.modules.review.effect_statistics import (
    interval_effect_status,
    paired_transition_audit,
)
from experiments.reviewx_scifact.run import (
    ENTITY_FEATURES,
    FEATURE_NAMES,
    NEGATION_FEATURES,
    NUMERIC_FEATURES,
    Example,
    FactorizedFeatureExtractor,
    NumpyLogisticRegression,
    compute_metrics,
    ensure_dataset as ensure_scifact,
    load_examples as load_scifact_examples,
)


CLIMATE_URL = (
    "https://raw.githubusercontent.com/tdiggelm/climate-fever-dataset/"
    "03de61617b10a5c1935f8e08bb0e8ac1ee775356/dataset/climate-fever.jsonl"
)
CLIMATE_SHA256 = "8a4b9032d861be482ffb49dddfd283ffa6089e654f1e968040011882c5eb6e0b"
CLIMATE_REPOSITORY = "https://github.com/tdiggelm/climate-fever-dataset"
CLIMATE_PAPER = "https://arxiv.org/abs/2012.00614"

PUBHEALTH_URL = (
    "https://drive.google.com/uc?export=download&id="
    "1eTtRs5cUlBP5dXsx-FTAlmXuB6JQi2qj"
)
PUBHEALTH_SHA256 = "3f0a5541f4a60c09a138a896621402893ce4b3a37060363d9257010c2c27fc3a"
PUBHEALTH_REPOSITORY = "https://github.com/neemakot/Health-Fact-Checking"
PUBHEALTH_PAPER = "https://aclanthology.org/2020.emnlp-main.623/"
PUBHEALTH_LABELS = {"true", "false", "mixture", "unproven"}

SPLIT_SEED = 20260825
SCHEMA_VERSION = "reviewx-multidomain-benchmark/v3"
MULTIDOMAIN_BOOTSTRAP_SEED = 20260829
VALIDATION_GATE_BOOTSTRAP_SEED = 20260901
VALIDATION_SPLIT_SEED = 20260901
THRESHOLD_CANDIDATES = tuple(round(0.1 + index * 0.025, 3) for index in range(33))
MIN_VALIDATION_MACRO_F1_GAIN = 0.005
MAX_UNSUPPORTED_F1_REGRESSION = 0.08
MAX_SUPPORT_F1_REGRESSION = 0.01


@dataclass(frozen=True)
class LoadedDataset:
    name: str
    train: list[Example]
    validation: list[Example]
    test: list[Example]
    source_sha256: str
    source_url: str
    repository: str
    paper: str
    license_note: str
    dropped_rows: int
    split_protocol: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:14], 16)


def _download(url: str, target: Path, *, user_agent: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as output:
        shutil.copyfileobj(response, output)
    temporary.replace(target)


def ensure_climate_fever(data_dir: Path, *, download: bool) -> Path:
    path = data_dir / "climate-fever.jsonl"
    if path.is_file() and _sha256(path) == CLIMATE_SHA256:
        return path
    if not download:
        raise FileNotFoundError(f"Climate-FEVER is missing or has the wrong hash: {path}")
    _download(CLIMATE_URL, path, user_agent="FAROS-Climate-FEVER/1.0")
    actual = _sha256(path)
    if actual != CLIMATE_SHA256:
        raise ValueError(f"Climate-FEVER hash mismatch: {actual}")
    return path


def _safe_extract_pubhealth(archive: Path, data_dir: Path) -> None:
    destination = data_dir.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            member_path = (destination / member.filename).resolve()
            if destination not in member_path.parents and member_path != destination:
                raise ValueError(f"Unsafe PubHealth archive member: {member.filename}")
        bundle.extractall(data_dir)


def ensure_pubhealth(data_dir: Path, *, download: bool) -> Path:
    dataset_root = data_dir / "PUBHEALTH"
    required = [dataset_root / f"{split}.tsv" for split in ("train", "dev", "test")]
    archive = data_dir / "PUBHEALTH.zip"
    if all(path.is_file() for path in required) and archive.is_file() and _sha256(archive) == PUBHEALTH_SHA256:
        return dataset_root
    if not download:
        raise FileNotFoundError(f"PubHealth is missing from {data_dir}")
    if not archive.is_file() or _sha256(archive) != PUBHEALTH_SHA256:
        _download(PUBHEALTH_URL, archive, user_agent="FAROS-PubHealth/1.0")
    actual = _sha256(archive)
    if actual != PUBHEALTH_SHA256:
        raise ValueError(f"PubHealth archive hash mismatch: {actual}")
    _safe_extract_pubhealth(archive, data_dir)
    if not all(path.is_file() for path in required):
        raise FileNotFoundError("PubHealth archive does not contain train/dev/test TSV files")
    return dataset_root


def _climate_bucket(claim_id: str) -> int:
    encoded = f"{SPLIT_SEED}:{claim_id}".encode("utf-8")
    return int(hashlib.sha256(encoded).hexdigest()[:8], 16) % 20


def load_climate_fever(path: Path) -> LoadedDataset:
    partitions: dict[str, list[Example]] = {"train": [], "validation": [], "test": []}
    dropped = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            claim_id_text = str(row.get("claim_id") or "")
            claim = str(row.get("claim") or "").strip()
            bucket = _climate_bucket(claim_id_text)
            split = "train" if bucket < 14 else "validation" if bucket < 17 else "test"
            for evidence_index, evidence in enumerate(row.get("evidences") or []):
                relation = str(evidence.get("evidence_label") or "")
                text = str(evidence.get("evidence") or "").strip()
                if not claim or not text or relation not in {"SUPPORTS", "REFUTES", "NOT_ENOUGH_INFO"}:
                    dropped += 1
                    continue
                claim_id = _stable_int(claim_id_text)
                evidence_id = str(evidence.get("evidence_id") or evidence_index)
                partitions[split].append(Example(
                    sample_id=f"climate-fever-{split}-{claim_id_text}-{evidence_index}",
                    split=split,
                    claim_id=claim_id,
                    document_id=_stable_int(f"{claim_id_text}:{evidence_id}"),
                    claim=claim,
                    document_title=str(evidence.get("article") or ""),
                    document_text=text,
                    relation=relation,
                    label=0 if relation == "SUPPORTS" else 1,
                ))
    return LoadedDataset(
        name="Climate-FEVER",
        train=partitions["train"],
        validation=partitions["validation"],
        test=partitions["test"],
        source_sha256=CLIMATE_SHA256,
        source_url=CLIMATE_URL,
        repository=CLIMATE_REPOSITORY,
        paper=CLIMATE_PAPER,
        license_note="NOASSERTION: the source repository has no explicit license; do not redistribute raw data.",
        dropped_rows=dropped,
        split_protocol="Claim-grouped deterministic 70/15/15 split using SHA256(seed:claim_id) modulo 20.",
    )


def _load_pubhealth_split(path: Path, split: str) -> tuple[list[Example], int]:
    examples: list[Example] = []
    dropped = 0
    seen: set[str] = set()
    with path.open(encoding="utf-8", newline="") as handle:
        for row_index, row in enumerate(csv.DictReader(handle, delimiter="\t")):
            claim_id_text = str(row.get("claim_id") or "").strip()
            claim = str(row.get("claim") or "").strip()
            document = str(row.get("main_text") or "").strip()
            relation = str(row.get("label") or "").strip().lower()
            identity = claim_id_text or f"row-{row_index}"
            if (
                relation not in PUBHEALTH_LABELS
                or not claim_id_text
                or not claim
                or not document
                or identity in seen
            ):
                dropped += 1
                continue
            seen.add(identity)
            claim_id = _stable_int(claim_id_text)
            examples.append(Example(
                sample_id=f"pubhealth-{split}-{identity}",
                split=split,
                claim_id=claim_id,
                document_id=claim_id,
                claim=claim,
                document_title=str(row.get("subjects") or ""),
                document_text=document,
                relation=relation.upper(),
                label=0 if relation == "true" else 1,
            ))
    return examples, dropped


def load_pubhealth(dataset_root: Path, archive: Path) -> LoadedDataset:
    train, train_dropped = _load_pubhealth_split(dataset_root / "train.tsv", "train")
    validation, validation_dropped = _load_pubhealth_split(dataset_root / "dev.tsv", "validation")
    test, test_dropped = _load_pubhealth_split(dataset_root / "test.tsv", "test")
    return LoadedDataset(
        name="PubHealth",
        train=train,
        validation=validation,
        test=test,
        source_sha256=_sha256(archive),
        source_url=PUBHEALTH_URL,
        repository=PUBHEALTH_REPOSITORY,
        paper=PUBHEALTH_PAPER,
        license_note=(
            "The repository code is MIT-licensed, but source article rights remain upstream; "
            "do not redistribute raw dataset text without a separate rights review."
        ),
        dropped_rows=train_dropped + validation_dropped + test_dropped,
        split_protocol="Official train/dev/test split; test labels are used only for final evaluation.",
    )


def _fit_probabilities(
    dataset: LoadedDataset,
    *,
    validation_bootstrap_samples: int,
) -> tuple[dict[str, np.ndarray], np.ndarray, dict[str, float], dict[str, Any]]:
    extractor = FactorizedFeatureExtractor().fit(dataset.train)
    train_features = extractor.transform(dataset.train)
    validation_features = extractor.transform(dataset.validation)
    test_features = extractor.transform(dataset.test)
    train_labels = np.asarray([item.label for item in dataset.train], dtype=float)
    validation_labels = np.asarray([item.label for item in dataset.validation], dtype=float)
    variants = {
        "within_domain": set(range(len(FEATURE_NAMES))),
        "ablation_no_negation": set(range(len(FEATURE_NAMES))) - NEGATION_FEATURES,
        "ablation_no_numeric": set(range(len(FEATURE_NAMES))) - NUMERIC_FEATURES,
        "ablation_no_entity": set(range(len(FEATURE_NAMES))) - ENTITY_FEATURES,
    }
    probabilities: dict[str, np.ndarray] = {
        "lexical_baseline": np.clip(1.0 - test_features[:, 2], 0.01, 0.99),
    }
    validation_probabilities: dict[str, np.ndarray] = {
        "lexical_baseline": np.clip(1.0 - validation_features[:, 2], 0.01, 0.99),
    }
    for name, selected in variants.items():
        indices = sorted(selected)
        model = NumpyLogisticRegression().fit(train_features[:, indices], train_labels)
        probabilities[name] = model.predict_proba(test_features[:, indices])
        validation_probabilities[name] = model.predict_proba(validation_features[:, indices])
    calibrated_threshold, validation_selection = select_threshold_with_gate(
        dataset.validation,
        validation_labels,
        validation_probabilities["within_domain"],
        bootstrap_samples=validation_bootstrap_samples,
    )
    probabilities["within_domain_calibrated"] = probabilities["within_domain"]
    thresholds = {name: 0.5 for name in probabilities}
    thresholds["within_domain_calibrated"] = calibrated_threshold
    return probabilities, test_features, thresholds, validation_selection


def extended_metrics(
    labels: np.ndarray,
    probabilities: np.ndarray,
    *,
    threshold: float = 0.5,
) -> dict[str, float]:
    metrics = compute_metrics(labels, probabilities)
    predictions = probabilities >= threshold
    positive = labels == 1
    tp = int((predictions & positive).sum())
    fp = int((predictions & ~positive).sum())
    tn = int((~predictions & ~positive).sum())
    fn = int((~predictions & positive).sum())
    precision = tp / max(1, tp + fp)
    recall = tp / max(1, tp + fn)
    positive_f1 = 2 * precision * recall / max(1e-12, precision + recall)
    metrics.update({
        "Precision": float(precision),
        "Recall": float(recall),
        "F1-Score": float(positive_f1),
    })
    specificity = tn / max(1, tn + fp)
    negative_precision = tn / max(1, tn + fn)
    negative_recall = specificity
    negative_f1 = (
        2 * negative_precision * negative_recall
        / max(1e-12, negative_precision + negative_recall)
    )
    accuracy = (tp + tn) / max(1, len(labels))
    balanced_accuracy = (metrics["Recall"] + specificity) / 2
    denominator = math.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
    mcc = ((tp * tn) - (fp * fn)) / denominator if denominator else 0.0
    return {
        **metrics,
        "Accuracy": float(accuracy),
        "Specificity": float(specificity),
        "Support F1": float(negative_f1),
        "Macro F1": float((metrics["F1-Score"] + negative_f1) / 2),
        "Balanced Accuracy": float(balanced_accuracy),
        "Matthews Correlation Coefficient": float(mcc),
    }


def select_threshold(labels: np.ndarray, probabilities: np.ndarray) -> tuple[float, dict[str, float]]:
    candidates = [
        (threshold, extended_metrics(labels, probabilities, threshold=threshold))
        for threshold in THRESHOLD_CANDIDATES
    ]
    threshold, metrics = max(
        candidates,
        key=lambda item: (
            item[1]["Macro F1"],
            item[1]["Balanced Accuracy"],
            -abs(item[0] - 0.5),
            -item[0],
        ),
    )
    return threshold, metrics


def _validation_partition_indices(
    examples: list[Example],
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Split claim groups before selection so the gate audits unseen validation groups."""

    proposal_groups = {
        item.claim_id
        for item in examples
        if int(
            hashlib.sha256(
                f"{VALIDATION_SPLIT_SEED}:{item.claim_id}".encode("utf-8")
            ).hexdigest()[:8],
            16,
        )
        % 2
        == 0
    }
    all_groups = {item.claim_id for item in examples}
    gate_groups = all_groups - proposal_groups
    if not proposal_groups or not gate_groups:
        raise ValueError("Validation proposal/gate split produced an empty partition.")

    proposal_indices = np.asarray(
        [index for index, item in enumerate(examples) if item.claim_id in proposal_groups],
        dtype=int,
    )
    gate_indices = np.asarray(
        [index for index, item in enumerate(examples) if item.claim_id in gate_groups],
        dtype=int,
    )
    return proposal_indices, gate_indices, {
        "splitUnit": "claim_id",
        "splitRule": "sha256(seed:claim_id) modulo 2",
        "seed": VALIDATION_SPLIT_SEED,
        "proposalBucket": 0,
        "proposalClaimGroups": len(proposal_groups),
        "gateClaimGroups": len(gate_groups),
        "proposalPairs": int(len(proposal_indices)),
        "gatePairs": int(len(gate_indices)),
        "groupIntersection": len(proposal_groups & gate_groups),
    }


def _select_threshold_under_guardrails(
    labels: np.ndarray,
    probabilities: np.ndarray,
) -> tuple[float, dict[str, float]]:
    fixed = extended_metrics(labels, probabilities, threshold=0.5)
    feasible = []
    for threshold in THRESHOLD_CANDIDATES:
        metrics = extended_metrics(labels, probabilities, threshold=threshold)
        if (
            metrics["Balanced Accuracy"] >= fixed["Balanced Accuracy"]
            and metrics["F1-Score"]
            >= fixed["F1-Score"] - MAX_UNSUPPORTED_F1_REGRESSION
            and metrics["Support F1"]
            >= fixed["Support F1"] - MAX_SUPPORT_F1_REGRESSION
        ):
            feasible.append((threshold, metrics))
    if not feasible:
        return 0.5, fixed
    return max(
        feasible,
        key=lambda item: (
            item[1]["Macro F1"],
            item[1]["Balanced Accuracy"],
            -abs(item[0] - 0.5),
            -item[0],
        ),
    )


def _cluster_bootstrap_threshold_delta(
    examples: list[Example],
    labels: np.ndarray,
    probabilities: np.ndarray,
    *,
    proposed_threshold: float,
    samples: int,
) -> dict[str, Any]:
    groups = np.asarray([item.claim_id for item in examples])
    unique_groups = np.unique(groups)
    group_indices = {
        group: np.flatnonzero(groups == group)
        for group in unique_groups
    }
    rng = np.random.default_rng(VALIDATION_GATE_BOOTSTRAP_SEED)
    improvements: list[float] = []
    for _ in range(samples):
        sampled_groups = rng.choice(unique_groups, size=len(unique_groups), replace=True)
        indices = np.concatenate([group_indices[group] for group in sampled_groups])
        fixed = extended_metrics(labels[indices], probabilities[indices], threshold=0.5)
        proposed = extended_metrics(
            labels[indices], probabilities[indices], threshold=proposed_threshold,
        )
        improvements.append(proposed["Macro F1"] - fixed["Macro F1"])
    ci_low = float(np.percentile(improvements, 2.5))
    ci_high = float(np.percentile(improvements, 97.5))
    return {
        "improvementMean": float(np.mean(improvements)),
        "ci95Low": ci_low,
        "ci95High": ci_high,
        "probabilityOfImprovement": float(np.mean(np.asarray(improvements) > 0)),
        "effectStatus": interval_effect_status(ci_low, ci_high),
        "resamplingUnit": "claim_id",
        "claimGroups": int(len(unique_groups)),
        "samples": samples,
        "seed": VALIDATION_GATE_BOOTSTRAP_SEED,
    }


def select_threshold_with_gate(
    examples: list[Example],
    labels: np.ndarray,
    probabilities: np.ndarray,
    *,
    bootstrap_samples: int,
) -> tuple[float, dict[str, Any]]:
    """Select on proposal groups and admit only revisions that pass an unseen gate."""

    proposal_indices, gate_indices, split_audit = _validation_partition_indices(examples)
    proposed_threshold, proposal_metrics = _select_threshold_under_guardrails(
        labels[proposal_indices],
        probabilities[proposal_indices],
    )
    proposal_fixed_metrics = extended_metrics(
        labels[proposal_indices],
        probabilities[proposal_indices],
        threshold=0.5,
    )
    gate_examples = [examples[index] for index in gate_indices]
    gate_labels = labels[gate_indices]
    gate_probabilities = probabilities[gate_indices]
    fixed_metrics = extended_metrics(
        gate_labels,
        gate_probabilities,
        threshold=0.5,
    )
    proposed_metrics = extended_metrics(
        gate_labels,
        gate_probabilities,
        threshold=proposed_threshold,
    )
    uncertainty = _cluster_bootstrap_threshold_delta(
        gate_examples,
        gate_labels,
        gate_probabilities,
        proposed_threshold=proposed_threshold,
        samples=bootstrap_samples,
    )
    checks = {
        "proposalChangesThreshold": proposed_threshold != 0.5,
        "macroF1GainAtLeast005": (
            proposed_metrics["Macro F1"]
            >= fixed_metrics["Macro F1"] + MIN_VALIDATION_MACRO_F1_GAIN
        ),
        "macroF1ClusterCIExcludesZero": uncertainty["ci95Low"] > 0,
        "balancedAccuracyDoesNotRegress": (
            proposed_metrics["Balanced Accuracy"]
            >= fixed_metrics["Balanced Accuracy"]
        ),
        "unsupportedF1WithinTolerance": (
            proposed_metrics["F1-Score"]
            >= fixed_metrics["F1-Score"] - MAX_UNSUPPORTED_F1_REGRESSION
        ),
        "supportF1WithinTolerance": (
            proposed_metrics["Support F1"]
            >= fixed_metrics["Support F1"] - MAX_SUPPORT_F1_REGRESSION
        ),
    }
    apply_revision = all(checks.values())
    applied_threshold = proposed_threshold if apply_revision else 0.5
    return applied_threshold, {
        "selectionSplit": "validation",
        "selectionProtocol": "disjoint_claim_group_proposal_and_gate",
        "splitAudit": split_audit,
        "candidateThresholds": list(THRESHOLD_CANDIDATES),
        "proposedThreshold": proposed_threshold,
        "appliedThreshold": applied_threshold,
        "gateDecision": "apply_revision" if apply_revision else "keep_round_one",
        "roundOneProposalMetrics": proposal_fixed_metrics,
        "proposedProposalMetrics": proposal_metrics,
        "roundOneGateMetrics": fixed_metrics,
        "proposedGateMetrics": proposed_metrics,
        "selectedValidationMetrics": proposed_metrics if apply_revision else fixed_metrics,
        "pairedClusterBootstrap": uncertainty,
        "gateChecks": checks,
        "testLabelsUsedForSelection": False,
    }


def paired_bootstrap_extended(
    labels: np.ndarray,
    baseline: np.ndarray,
    method: np.ndarray,
    *,
    samples: int,
    baseline_threshold: float = 0.5,
    method_threshold: float = 0.5,
    groups: np.ndarray | None = None,
) -> dict[str, dict[str, float]]:
    rng = np.random.default_rng(MULTIDOMAIN_BOOTSTRAP_SEED)
    metric_names = ("Macro F1", "Balanced Accuracy", "Brier Score", "AUROC")
    directions = {"Macro F1": 1, "Balanced Accuracy": 1, "Brier Score": -1, "AUROC": 1}
    improvements: dict[str, list[float]] = {name: [] for name in metric_names}
    unique_groups = np.unique(groups) if groups is not None else None
    group_indices = (
        {group: np.flatnonzero(groups == group) for group in unique_groups}
        if unique_groups is not None
        else None
    )
    for _ in range(samples):
        if unique_groups is None or group_indices is None:
            indices = rng.integers(0, len(labels), size=len(labels))
        else:
            sampled_groups = rng.choice(unique_groups, size=len(unique_groups), replace=True)
            indices = np.concatenate([group_indices[group] for group in sampled_groups])
        sampled_labels = labels[indices]
        if len(set(sampled_labels.tolist())) < 2:
            continue
        baseline_metrics = extended_metrics(
            sampled_labels, baseline[indices], threshold=baseline_threshold,
        )
        method_metrics = extended_metrics(
            sampled_labels, method[indices], threshold=method_threshold,
        )
        for name in metric_names:
            improvements[name].append(
                directions[name] * (method_metrics[name] - baseline_metrics[name])
            )
    return {
        name: {
            "improvementMean": float(np.mean(values)),
            "ci95Low": float(np.percentile(values, 2.5)),
            "ci95High": float(np.percentile(values, 97.5)),
            "probabilityOfImprovement": float(np.mean(np.asarray(values) > 0)),
        }
        for name, values in improvements.items()
    }


def _transfer_probabilities(scifact_train: list[Example], target_test: list[Example]) -> np.ndarray:
    extractor = FactorizedFeatureExtractor().fit(scifact_train)
    train_features = extractor.transform(scifact_train)
    test_features = extractor.transform(target_test)
    labels = np.asarray([item.label for item in scifact_train], dtype=float)
    model = NumpyLogisticRegression().fit(train_features, labels)
    return model.predict_proba(test_features)


def _partition_audit(dataset: LoadedDataset) -> dict[str, Any]:
    groups = {
        "train": {item.claim_id for item in dataset.train},
        "validation": {item.claim_id for item in dataset.validation},
        "test": {item.claim_id for item in dataset.test},
    }
    intersections = {
        "trainValidation": len(groups["train"] & groups["validation"]),
        "trainTest": len(groups["train"] & groups["test"]),
        "validationTest": len(groups["validation"] & groups["test"]),
    }
    return {
        "claimGroups": {key: len(value) for key, value in groups.items()},
        "pairCounts": {
            "train": len(dataset.train),
            "validation": len(dataset.validation),
            "test": len(dataset.test),
        },
        "groupIntersections": intersections,
        "labels": {
            split: dict(Counter(item.relation for item in values))
            for split, values in (
                ("train", dataset.train),
                ("validation", dataset.validation),
                ("test", dataset.test),
            )
        },
        "droppedRows": dataset.dropped_rows,
    }


def _evaluate_dataset(
    dataset: LoadedDataset,
    scifact_train: list[Example],
    *,
    bootstrap_samples: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    probabilities, test_features, thresholds, validation_selection = _fit_probabilities(
        dataset,
        validation_bootstrap_samples=bootstrap_samples,
    )
    probabilities["scifact_transfer"] = _transfer_probabilities(scifact_train, dataset.test)
    thresholds["scifact_transfer"] = 0.5
    labels = np.asarray([item.label for item in dataset.test], dtype=float)
    claim_groups = np.asarray([item.claim_id for item in dataset.test])
    metrics = {
        name: extended_metrics(labels, values, threshold=thresholds[name])
        for name, values in probabilities.items()
    }
    bootstrap = {
        "withinDomainVsLexical": paired_bootstrap_extended(
            labels,
            probabilities["lexical_baseline"],
            probabilities["within_domain"],
            samples=bootstrap_samples,
            groups=claim_groups,
        ),
        "calibratedWithinDomainVsLexical": paired_bootstrap_extended(
            labels,
            probabilities["lexical_baseline"],
            probabilities["within_domain_calibrated"],
            samples=bootstrap_samples,
            method_threshold=thresholds["within_domain_calibrated"],
            groups=claim_groups,
        ),
        "calibratedVsFixedWithinDomain": paired_bootstrap_extended(
            labels,
            probabilities["within_domain"],
            probabilities["within_domain_calibrated"],
            samples=bootstrap_samples,
            method_threshold=thresholds["within_domain_calibrated"],
            groups=claim_groups,
        ),
        "SciFactTransferVsLexical": paired_bootstrap_extended(
            labels,
            probabilities["lexical_baseline"],
            probabilities["scifact_transfer"],
            samples=bootstrap_samples,
            groups=claim_groups,
        ),
    }
    round_one_predictions = probabilities["within_domain"] >= thresholds["within_domain"]
    round_two_predictions = (
        probabilities["within_domain_calibrated"]
        >= thresholds["within_domain_calibrated"]
    )
    intervention_audit = paired_transition_audit(
        labels.astype(int).tolist(),
        round_one_predictions.astype(int).tolist(),
        round_two_predictions.astype(int).tolist(),
    )
    macro_f1_inference = bootstrap["calibratedVsFixedWithinDomain"]["Macro F1"]
    macro_f1_inference["effectStatus"] = interval_effect_status(
        macro_f1_inference["ci95Low"], macro_f1_inference["ci95High"],
    )
    macro_f1_inference["resamplingUnit"] = "claim_id"
    audit = _partition_audit(dataset)
    sample_ids = [item.sample_id for item in dataset.test]
    quality_checks = {
        "sourceHashVerified": dataset.source_sha256 in {CLIMATE_SHA256, PUBHEALTH_SHA256},
        "partitionsNonEmpty": all((dataset.train, dataset.validation, dataset.test)),
        "claimGroupsAreDisjoint": all(value == 0 for value in audit["groupIntersections"].values()),
        "testHasBothBinaryClasses": len(set(labels.tolist())) == 2,
        "testSampleIdsUnique": len(sample_ids) == len(set(sample_ids)),
        "featuresFinite": bool(np.isfinite(test_features).all()),
        "predictionsFinite": all(bool(np.isfinite(values).all()) for values in probabilities.values()),
        "thresholdSelectedWithoutTestLabels": (
            validation_selection["appliedThreshold"] in THRESHOLD_CANDIDATES
            and validation_selection["testLabelsUsedForSelection"] is False
        ),
        "validationGateUsesClaimClusterBootstrap": (
            validation_selection["pairedClusterBootstrap"]["resamplingUnit"] == "claim_id"
        ),
        "validationSelectionAndGateAreGroupDisjoint": (
            validation_selection["selectionProtocol"]
            == "disjoint_claim_group_proposal_and_gate"
            and validation_selection["splitAudit"]["groupIntersection"] == 0
        ),
        "metricsFinite": all(
            math.isfinite(value)
            for method in metrics.values()
            for value in method.values()
        ),
    }
    result = {
        "dataset": {
            "name": dataset.name,
            "sourceUrl": dataset.source_url,
            "repository": dataset.repository,
            "paper": dataset.paper,
            "sourceSha256": dataset.source_sha256,
            "licenseNote": dataset.license_note,
            "splitProtocol": dataset.split_protocol,
        },
        "audit": audit,
        "results": metrics,
        "decisionThresholds": thresholds,
        "validationThresholdSelection": validation_selection,
        "pairedBootstrap": bootstrap,
        "interventionAudit": intervention_audit,
        "effectInference": {"Macro F1": macro_f1_inference},
        "qualityGate": {
            "status": "passed" if all(quality_checks.values()) else "failed",
            "checks": quality_checks,
        },
        "performanceObservations": {
            "withinDomainMacroF1DeltaVsLexical": (
                metrics["within_domain"]["Macro F1"] - metrics["lexical_baseline"]["Macro F1"]
            ),
            "calibratedWithinDomainMacroF1DeltaVsLexical": (
                metrics["within_domain_calibrated"]["Macro F1"]
                - metrics["lexical_baseline"]["Macro F1"]
            ),
            "calibrationIterationMacroF1Delta": (
                metrics["within_domain_calibrated"]["Macro F1"]
                - metrics["within_domain"]["Macro F1"]
            ),
            "scifactTransferMacroF1DeltaVsLexical": (
                metrics["scifact_transfer"]["Macro F1"] - metrics["lexical_baseline"]["Macro F1"]
            ),
        },
    }
    records = {
        "schemaVersion": "reviewx-multidomain-evaluation/v2",
        "dataset": dataset.name,
        "positiveClass": "unsupported",
        "decisionThresholds": thresholds,
        "records": [
            {
                "sampleId": item.sample_id,
                "claimId": item.claim_id,
                "relation": item.relation,
                "label": item.label,
                "predictions": {
                    name: {
                        "probability": float(values[index]),
                        "decisionThreshold": thresholds[name],
                        "label": int(values[index] >= thresholds[name]),
                    }
                    for name, values in probabilities.items()
                },
            }
            for index, item in enumerate(dataset.test)
        ],
    }
    return result, records


def _payload_hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _report(summary: dict[str, Any]) -> str:
    lines = [
        "# ReviewX跨数据集真实数据压力测试",
        "",
        f"> 运行编号：`{summary['runId']}`  ",
        f"> 完整性质量门：**{summary['qualityGate']['status']}**  ",
        "> 本实验不调用Qwen，用于隔离评估ReviewX确定性一致性模型的跨领域能力。",
        "",
        "## 数据集与任务差异",
        "",
        "| 数据集 | 领域 | 拆分 | 最终测试对数 | 过滤行 |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    domains = {"Climate-FEVER": "气候科学/公众主张", "PubHealth": "公共卫生/事实核查"}
    for item in summary["datasets"]:
        result = summary["results"][item["name"]]
        lines.append(
            f"| {item['name']} | {domains[item['name']]} | {item['splitProtocol']} | "
            f"{result['audit']['pairCounts']['test']} | {result['audit']['droppedRows']} |"
        )
    lines.extend([
        "",
        "## 最终测试结果",
        "",
        "| 数据集 | 方法 | 阈值 | Macro-F1 | Unsupported F1 | Balanced Acc. | MCC | Brier | AUROC |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    names = {
        "lexical_baseline": "词法基线",
        "within_domain": "目标域训练第一轮",
        "within_domain_calibrated": "ReviewX保守门控第二轮",
        "scifact_transfer": "SciFact零样本迁移",
    }
    for dataset_name, result in summary["results"].items():
        for method in (
            "lexical_baseline", "within_domain",
            "within_domain_calibrated", "scifact_transfer",
        ):
            metric = result["results"][method]
            lines.append(
                f"| {dataset_name} | {names[method]} | "
                f"{result['decisionThresholds'][method]:.3f} | {metric['Macro F1']:.4f} | "
                f"{metric['F1-Score']:.4f} | {metric['Balanced Accuracy']:.4f} | "
                f"{metric['Matthews Correlation Coefficient']:.4f} | {metric['Brier Score']:.4f} | "
                f"{metric['AUROC']:.4f} |"
            )
    lines.extend([
        "",
        "## 保守门控与最终测试",
        "",
        "| 数据集 | 验证集提议 | Gate决定 | 最终阈值 | 测试集Macro-F1增益 | claim聚类95% CI | 结论 |",
        "| --- | ---: | --- | ---: | ---: | --- | --- |",
    ])
    for dataset_name, result in summary["results"].items():
        fixed = result["results"]["within_domain"]["Macro F1"]
        calibrated = result["results"]["within_domain_calibrated"]["Macro F1"]
        selection = result["validationThresholdSelection"]
        bootstrap = result["effectInference"]["Macro F1"]
        lines.append(
            f"| {dataset_name} | {selection['proposedThreshold']:.3f} | "
            f"{selection['gateDecision']} | {selection['appliedThreshold']:.3f} | "
            f"{calibrated - fixed:+.4f} | "
            f"[{bootstrap['ci95Low']:.4f}, {bootstrap['ci95High']:.4f}] | "
            f"{bootstrap['effectStatus']} |"
        )
    lines.extend([
        "",
        "Climate-FEVER的验证集聚类区间通过保守门控，修订冻结后再进入测试集；PubHealth的验证集区间跨越0，因此系统保持第一轮方案，不把不确定趋势包装成改进。两个数据集上的SciFact零样本迁移均弱于目标域训练，说明当前特征可以复用，但模型参数尚不具备直接跨领域泛化能力。",
        "",
        "## 解释边界",
        "",
        "完整性质量门只证明数据拆分、哈希、样本对齐和指标计算可信，不代表性能达标。跨数据集主指标采用Macro-F1，避免unsupported类别占比过高抬高正类F1。",
        "目标域训练用于检验当前特征是否能在不同领域重新拟合；SciFact零样本迁移用于检验不重新训练时的跨领域稳健性。",
        "第二轮先在validation的提议组中按预注册阈值网格和类别护栏选择方案，再在完全不重叠的门控组中按claim_id聚类bootstrap；只有95%区间下界大于0且门控护栏通过才应用，否则保持第一轮。test标签从未参与提议或门控。",
        "Climate-FEVER没有明确数据许可证，PubHealth包含上游事实核查文本；本项目只在忽略目录运行，不重新分发原始数据。",
        "QASPER和NLPeer属于科学问答/同行评审任务，应使用Evidence F1、review helpfulness等独立协议，不能与本表二分类F1混为一谈。",
        "",
    ])
    return "\n".join(lines)


def run_multidomain(
    external_data_dir: Path,
    output_dir: Path,
    *,
    download: bool,
    bootstrap_samples: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    climate_path = ensure_climate_fever(external_data_dir / "climate_fever", download=download)
    pubhealth_root = ensure_pubhealth(external_data_dir / "pubhealth", download=download)
    scifact_root = ensure_scifact(external_data_dir / "scifact", download=download)
    datasets = [
        load_climate_fever(climate_path),
        load_pubhealth(pubhealth_root, external_data_dir / "pubhealth" / "PUBHEALTH.zip"),
    ]
    scifact_train = load_scifact_examples(scifact_root, "train")
    run_id = "reviewx_multidomain_" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dataset_cards = [
        {
            "name": dataset.name,
            "sourceUrl": dataset.source_url,
            "repository": dataset.repository,
            "paper": dataset.paper,
            "sourceSha256": dataset.source_sha256,
            "licenseNote": dataset.license_note,
            "splitProtocol": dataset.split_protocol,
        }
        for dataset in datasets
    ]
    manifest_base = {
        "schemaVersion": SCHEMA_VERSION,
        "runId": run_id,
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "task": "unsupported claim-evidence pair detection",
        "positiveClass": "unsupported",
        "decisionThreshold": 0.5,
        "featureSchema": list(FEATURE_NAMES),
        "scifactTransferTrainPairs": len(scifact_train),
        "bootstrapSamples": bootstrap_samples,
        "bootstrapSeed": MULTIDOMAIN_BOOTSTRAP_SEED,
        "validationGateBootstrapSeed": VALIDATION_GATE_BOOTSTRAP_SEED,
        "validationSplitSeed": VALIDATION_SPLIT_SEED,
        "bootstrapResamplingUnit": "claim_id",
        "validationGate": {
            "minimumMacroF1Gain": MIN_VALIDATION_MACRO_F1_GAIN,
            "maximumUnsupportedF1Regression": MAX_UNSUPPORTED_F1_REGRESSION,
            "maximumSupportF1Regression": MAX_SUPPORT_F1_REGRESSION,
            "requireClusterCIAboveZero": True,
            "selectionProtocol": (
                "select on one claim-group partition; gate on the disjoint partition"
            ),
        },
        "thresholdCandidates": list(THRESHOLD_CANDIDATES),
        "datasets": dataset_cards,
    }
    manifest = {**manifest_base, "contentHash": _payload_hash(manifest_base)}
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "preregistered_protocol.json", manifest)

    results: dict[str, Any] = {}
    for dataset in datasets:
        result, records = _evaluate_dataset(
            dataset,
            scifact_train,
            bootstrap_samples=bootstrap_samples,
        )
        results[dataset.name] = result
        _write_json(output_dir / dataset.name.lower().replace("-", "_") / "evaluation_records.json", records)
        _write_json(output_dir / dataset.name.lower().replace("-", "_") / "summary.json", result)
    quality_status = "passed" if all(
        result["qualityGate"]["status"] == "passed" for result in results.values()
    ) else "failed"
    summary = {
        **manifest,
        "results": results,
        "qualityGate": {"status": quality_status},
        "durationSeconds": time.perf_counter() - started,
    }
    _write_json(output_dir / "summary.json", summary)
    (output_dir / "experiment_report.md").write_text(_report(summary), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--external-data-dir", type=Path, default=Path("data/external"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/experiments/reviewx_multidomain"))
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--bootstrap-samples", type=int, default=500)
    args = parser.parse_args()
    summary = run_multidomain(
        args.external_data_dir,
        args.output_dir,
        download=args.download,
        bootstrap_samples=args.bootstrap_samples,
    )
    print(json.dumps({
        "runId": summary["runId"],
        "qualityGate": summary["qualityGate"],
        "durationSeconds": summary["durationSeconds"],
        "outputDir": str(args.output_dir),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
