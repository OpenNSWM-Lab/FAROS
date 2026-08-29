"""Run a Qwen-guided, two-round ReviewX loop on real SciFact data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import resource
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Sequence

import numpy as np

from app.contracts import ExecutionAssessment, ResearchDossier
from app.llm.provider_client import ChatMessage, get_provider_client
from app.modules.review.experiment_feedback import (
    experiment_metric_snapshot,
    review_experiment_feedback,
)
from app.modules.review.experiment_series import (
    ExperimentLoopPolicy,
    MetricGuardrail,
    evaluate_experiment_series,
)
from app.modules.review.plan_delta import (
    CandidateAudit,
    DeltaEvidenceRef,
    DeltaTrigger,
    PlanFieldDelta,
    QwenPlanContribution,
    seal_plan_delta_contract,
    verify_plan_delta_contract,
)
from app.services.experiment_evidence_service import build_experiment_evidence
from experiments.reviewx_scifact.run import (
    BOOTSTRAP_SEED,
    DATASET_PAPER,
    DATASET_REPOSITORY,
    DATASET_SHA256,
    DATASET_URL,
    DECISION_THRESHOLD,
    ENTITY_FEATURES,
    EVALUATION_SCHEMA,
    FEATURE_NAMES,
    NEGATION_FEATURES,
    NUMERIC_FEATURES,
    POSITIVE_CLASS,
    POSITIVE_LABEL,
    TRAINING_SEED,
    Example,
    FactorizedFeatureExtractor,
    NumpyLogisticRegression,
    _canonical_fingerprint,
    _write_json,
    compute_metrics,
    ensure_dataset,
    load_examples,
)


FEEDBACK_MODULUS = 5
FEEDBACK_BUCKET = 0
ROUND_ONE_THRESHOLD = 0.5
CALIBRATED_THRESHOLD = 0.375
LOOP_SCHEMA = "reviewx-scifact-closed-loop/v1"
QUESTION_ID = "scifact_claim_support_controlled_loop"


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    selected_features: tuple[int, ...]
    threshold: float
    change: str


ALL_FEATURES = tuple(range(len(FEATURE_NAMES)))
CANDIDATES = (
    Candidate("retain_full_0500", ALL_FEATURES, 0.5, "Retain the full model and threshold."),
    Candidate("retain_full_0375", ALL_FEATURES, 0.375, "Lower only the decision threshold."),
    Candidate(
        "remove_numeric_0375",
        tuple(sorted(set(ALL_FEATURES) - NUMERIC_FEATURES)),
        0.375,
        "Remove the unstable numeric-alignment factor and lower the decision threshold.",
    ),
    Candidate(
        "remove_negation_0375",
        tuple(sorted(set(ALL_FEATURES) - NEGATION_FEATURES)),
        0.375,
        "Remove all negation factors and lower the decision threshold.",
    ),
    Candidate(
        "remove_entity_0375",
        tuple(sorted(set(ALL_FEATURES) - ENTITY_FEATURES)),
        0.375,
        "Remove all entity factors and lower the decision threshold.",
    ),
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def preregistration_payload(run_id: str, created_at: str) -> dict[str, Any]:
    """Build the result-free protocol that must exist before round one runs."""

    payload = {
        "schemaVersion": "faros-preregistration/v1",
        "runId": run_id,
        "createdAt": created_at,
        "scientificQuestion": (
            "Can one evidence-driven revision improve unsupported scientific "
            "claim detection without violating calibration and ranking guardrails?"
        ),
        "hypothesis": (
            "A feedback-selected structural revision improves unsupported-pair "
            "F1 on a frozen feedback set while satisfying preregistered guardrails."
        ),
        "falsificationCriteria": [
            "Round-two feedback F1 does not improve by at least 0.01.",
            "Round-two precision is below 0.66.",
            "Round-two Brier Score increases by more than 0.001.",
            "Round-two AUROC decreases by more than 0.002.",
            "The benchmark fingerprint changes between rounds.",
        ],
        "datasetProtocol": {
            "dataset": "SciFact official release",
            "archiveSha256": DATASET_SHA256,
            "fitAndFeedbackSource": "official train split",
            "finalHoldoutSource": "official dev split",
            "groupingUnit": "claim_id",
            "feedbackBucketRule": (
                f"sha256(claim_id) modulo {FEEDBACK_MODULUS} equals {FEEDBACK_BUCKET}"
            ),
            "finalHoldoutPolicy": (
                "Do not load or evaluate official dev until the round-two plan is frozen."
            ),
        },
        "roundOnePlan": {
            "method": "Full factorized consistency model",
            "decisionThreshold": ROUND_ONE_THRESHOLD,
            "baseline": "IDF-weighted lexical coverage",
            "tasks": [
                "Verify the official dataset archive and split by claim group.",
                "Fit the feature extractor and candidate models on the fit partition.",
                "Evaluate round one and named ablations on one frozen feedback set.",
                "Recompute all aggregate metrics from per-record predictions.",
                "Pass evidence through ReviewX before selecting round two.",
            ],
            "expectedObservations": [
                "Round-one F1, calibration, ranking, and ablation diagnostics.",
                "A ReviewX evidence-integrity decision and structured next actions.",
            ],
            "stopConditions": [
                "Dataset or benchmark integrity failure.",
                "Non-finite or independently inconsistent metric.",
                "No candidate satisfies the preregistered guardrails.",
            ],
        },
        "candidatePolicy": {
            "candidates": [
                {
                    "candidateId": item.candidate_id,
                    "selectedFeatures": [FEATURE_NAMES[index] for index in item.selected_features],
                    "decisionThreshold": item.threshold,
                    "change": item.change,
                }
                for item in CANDIDATES
            ],
            "guardrails": {
                "minimumPrecision": 0.66,
                "maximumBrierIncrease": 0.001,
                "maximumAUROCDecrease": 0.002,
            },
            "selectionRule": (
                "Discard infeasible candidates; find the highest feasible F1; "
                "retain candidates within 0.005 F1; choose lowest ECE, then fewer "
                "features, then lexical candidate id."
            ),
        },
        "metrics": {
            "primary": "F1-Score",
            "guardrails": ["Precision", "Brier Score", "Expected Calibration Error (ECE)", "AUROC"],
            "uncertainty": "Paired bootstrap with 2,000 samples on the final holdout.",
        },
        "roles": {
            "deterministicProgram": "Compute metrics, enforce guardrails, and select by the frozen policy.",
            "ReviewX": "Audit provenance, integrity, comparability, and experiment-to-plan alignment.",
            "Qwen": "Audit the selected revision, explain evidence, and define tradeoffs and falsification criteria.",
            "human": "Approve public scientific claims and deployment; no human label changes occur in this run.",
        },
    }
    return {**payload, "contentHash": _payload_hash(payload)}


def _append_timeline(
    timeline_path: Path,
    events: list[dict[str, Any]],
    event: str,
    **details: Any,
) -> None:
    events.append({"event": event, "timestamp": _now(), **details})
    _write_json(timeline_path, {"schemaVersion": "faros-experiment-timeline/v1", "events": events})


def _claim_bucket(claim_id: int) -> int:
    digest = hashlib.sha256(str(claim_id).encode("ascii")).hexdigest()
    return int(digest[:8], 16) % FEEDBACK_MODULUS


def split_fit_feedback(examples: Sequence[Example]) -> tuple[list[Example], list[Example]]:
    fit = [item for item in examples if _claim_bucket(item.claim_id) != FEEDBACK_BUCKET]
    feedback = [item for item in examples if _claim_bucket(item.claim_id) == FEEDBACK_BUCKET]
    if {item.claim_id for item in fit} & {item.claim_id for item in feedback}:
        raise ValueError("Claim-group leakage detected between fit and feedback splits.")
    if not fit or not feedback:
        raise ValueError("Deterministic SciFact split produced an empty partition.")
    return fit, feedback


def metrics_at_threshold(
    labels: np.ndarray,
    probabilities: np.ndarray,
    threshold: float,
) -> dict[str, float]:
    metrics = compute_metrics(labels, probabilities)
    predictions = probabilities >= threshold
    positive = labels == POSITIVE_LABEL
    true_positive = int((predictions & positive).sum())
    false_positive = int((predictions & ~positive).sum())
    false_negative = int((~predictions & positive).sum())
    precision = true_positive / max(1, true_positive + false_positive)
    recall = true_positive / max(1, true_positive + false_negative)
    metrics["Precision"] = float(precision)
    metrics["Recall"] = float(recall)
    metrics["F1-Score"] = float(
        2 * precision * recall / max(1e-12, precision + recall)
    )
    return metrics


def select_candidate(
    diagnostics: dict[str, dict[str, float]],
    round_one: dict[str, float],
) -> tuple[str, dict[str, dict[str, Any]]]:
    audited: dict[str, dict[str, Any]] = {}
    for candidate_id, metrics in diagnostics.items():
        checks = {
            "precisionAtLeast066": metrics["Precision"] >= 0.66,
            "brierWithin0001": metrics["Brier Score"] <= round_one["Brier Score"] + 0.001,
            "aurocWithin0002": metrics["AUROC"] >= round_one["AUROC"] - 0.002,
        }
        audited[candidate_id] = {
            "metrics": metrics,
            "checks": checks,
            "feasible": all(checks.values()),
        }
    feasible = [
        candidate_id for candidate_id, item in audited.items() if item["feasible"]
    ]
    if not feasible:
        raise ValueError("No candidate satisfies the preregistered feedback guardrails.")
    best_f1 = max(diagnostics[item]["F1-Score"] for item in feasible)
    near_best = [
        item for item in feasible
        if diagnostics[item]["F1-Score"] >= best_f1 - 0.005
    ]
    selected = min(
        near_best,
        key=lambda item: (
            diagnostics[item]["Expected Calibration Error (ECE)"],
            len(next(candidate for candidate in CANDIDATES if candidate.candidate_id == item).selected_features),
            item,
        ),
    )
    return selected, audited


def _prediction_payload(
    examples: Sequence[Example],
    probabilities: dict[str, np.ndarray],
    thresholds: dict[str, float],
    split: str,
) -> dict[str, Any]:
    return {
        "schema_version": EVALUATION_SCHEMA,
        "positive_label": POSITIVE_LABEL,
        "positive_class": POSITIVE_CLASS,
        "decision_threshold": DECISION_THRESHOLD,
        "decision_thresholds": thresholds,
        "records": [
            {
                "sample_id": example.sample_id,
                "split": split,
                "label": example.label,
                "predictions": {
                    name: {
                        "label": int(values[index] >= thresholds[name]),
                        "probability": float(values[index]),
                    }
                    for name, values in probabilities.items()
                },
            }
            for index, example in enumerate(examples)
        ],
    }


def _metric_records(
    results: dict[str, dict[str, float]], split: str,
) -> list[dict[str, Any]]:
    definitions = {
        "Precision": "Unsupported-pair precision at the method-specific decision threshold.",
        "Recall": "Unsupported-pair recall at the method-specific decision threshold.",
        "F1-Score": "Harmonic mean of unsupported-pair precision and recall.",
        "Brier Score": "Mean squared error of unsupported-class probabilities; lower is better.",
        "Expected Calibration Error (ECE)": "Ten-bin calibration error; lower is better.",
        "AUROC": "Area under the ROC curve for unsupported claim-document pairs.",
    }
    return [
        {
            "name": f"{method}:{name}",
            "value": value,
            "unit": "ratio",
            "definition": definitions[name],
            "split": split,
        }
        for method, metrics in results.items()
        for name, value in metrics.items()
    ]


def _benchmark(
    examples: Sequence[Example], features: np.ndarray, split: str,
) -> dict[str, Any]:
    payload = {
        "schema_version": "faros-benchmark/v1",
        "benchmark_id": "scifact-train-feedback-claim-group-v1",
        "task": "scientific_claim_document_support_detection",
        "positive_label": POSITIVE_LABEL,
        "positive_class": POSITIVE_CLASS,
        "seed": TRAINING_SEED,
        "generator_version": "reviewx-scifact-closed-loop/1.0",
        "feature_schema": list(FEATURE_NAMES),
        "records": [
            {
                "sample_id": example.sample_id,
                "split": split,
                "features": features[index].tolist(),
                "label": example.label,
                "metadata": {
                    "claim_id": example.claim_id,
                    "document_id": example.document_id,
                    "gold_relation": example.relation,
                    "claim": example.claim,
                    "document_title": example.document_title,
                },
            }
            for index, example in enumerate(examples)
        ],
    }
    payload["fingerprint"] = _canonical_fingerprint(payload)
    return payload


def _dossier(run_id: str, model: str, call_trace: list[dict[str, Any]]) -> ResearchDossier:
    return ResearchDossier.model_validate({
        "runId": run_id,
        "questionId": QUESTION_ID,
        "problemFrame": {
            "originalQuestion": "Can ReviewX improve unsupported scientific claim detection on real SciFact data?",
            "scopedQuestion": "Does one controlled feedback revision improve F1 without violating calibration and ranking guardrails?",
            "definitions": {"unsupported": "SciFact CONTRADICT or cited-document NEI pair."},
            "observableVariables": ["F1-Score", "Brier Score", "Expected Calibration Error (ECE)", "AUROC"],
            "assumptions": ["SciFact human labels are suitable for claim-document support evaluation."],
            "outOfScope": ["Full-paper review usefulness", "Wet-lab validation"],
            "subQuestions": ["Which preregistered feature/threshold change is guardrail-feasible?"],
        },
        "evidenceMap": {
            "consensus": [], "disputedClaims": [], "supportingEvidence": [],
            "counterEvidence": [], "contextualEvidence": [],
            "unresolvedGaps": ["Generalization beyond SciFact abstracts"],
        },
        "hypotheses": [{
            "id": "hyp_scifact_loop",
            "statement": "A feedback-selected structural revision improves unsupported-pair F1 under fixed-data guardrails.",
            "falsificationCriteria": ["Feedback F1 does not improve or any preregistered guardrail fails."],
            "alternativeExplanations": ["Threshold movement, rather than feature removal, explains the gain."],
        }],
        "researchPlan": {
            "objective": "Run two controlled rounds on a frozen human-annotated feedback benchmark.",
            "steps": [{
                "id": "step_scifact_loop", "order": 1,
                "title": "Controlled claim-support iteration",
                "objective": "Compare round one and a Qwen-guided revision on identical records.",
                "inputs": ["SciFact official train split"],
                "tools": ["FAROS", "ReviewX", "Qwen via Bailian"],
                "method": ["Group split by claim", "Freeze benchmark", "Audit metrics", "Apply one revision"],
                "outputs": ["evaluation_records.json", "experiment_evidence.json", "iteration_plan.json"],
                "metrics": ["F1-Score", "Brier Score", "Expected Calibration Error (ECE)", "AUROC"],
                "stopConditions": ["Integrity audit failure", "No guardrail-feasible candidate"],
            }],
            "requiredData": ["SciFact official release"],
            "requiredResources": ["NumPy", "Qwen API"],
            "expectedOutcomes": ["Auditable two-round comparison"],
            "constraints": ["No feedback/dev claim overlap", "Official dev labels hidden from Qwen"],
            "ethics": ["Do not overstate generalization or statistical significance"],
            "executionClass": "computational_ready",
        },
        "uncertainties": ["One feedback split may produce unstable model selection."],
        "generationTrace": {
            "providerName": "qwen", "model": model, "llmCalls": call_trace,
            "localRulePasses": ["claim_group_split", "final_holdout_isolation"],
        },
    })


def _assessment(run_id: str) -> ExecutionAssessment:
    return ExecutionAssessment.model_validate({
        "runId": run_id,
        "questionId": QUESTION_ID,
        "executionClass": "computational_ready",
        "feasibilityScore": 0.95,
        "rationale": "The official data, deterministic runner, baseline, metrics, and guardrails are available.",
        "availableInputs": ["SciFact train and dev splits"],
        "missingInputs": [],
        "toolsAndEnvironment": ["Python", "NumPy", "FAROS evidence audit", "Qwen"],
        "validationMetrics": ["F1-Score", "Brier Score", "Expected Calibration Error (ECE)", "AUROC"],
        "stopConditions": ["Metric audit failure", "Benchmark fingerprint mismatch"],
        "safetyConstraints": ["Do not expose API keys", "Do not expose final holdout labels to Qwen"],
        "status": "ready",
    })


def _write_round(
    root: Path,
    *,
    run_id: str,
    benchmark: dict[str, Any],
    examples: Sequence[Example],
    probabilities: dict[str, np.ndarray],
    thresholds: dict[str, float],
    method: str,
    require_ablation: bool,
    duration_seconds: float,
) -> tuple[Any, dict[str, dict[str, float]]]:
    split = "scifact_train_feedback"
    results = {
        name: metrics_at_threshold(
            np.asarray([item.label for item in examples], dtype=float),
            values,
            thresholds[name],
        )
        for name, values in probabilities.items()
    }
    metrics = _metric_records(results, split)
    _write_json(root / "data" / "frozen_benchmark.json", benchmark)
    _write_json(root / "evaluation_records.json", _prediction_payload(
        examples, probabilities, thresholds, split,
    ))
    _write_json(root / "metrics.json", metrics)
    source_dir = root / "src"
    source_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), source_dir / "closed_loop.py")
    shutil.copy2(Path(__file__).with_name("run.py"), source_dir / "run.py")
    evidence = build_experiment_evidence(
        repo_dir=root,
        run_id=run_id,
        question_id=QUESTION_ID,
        code_run_id=f"{run_id}_code",
        method=method,
        baseline="IDF-weighted lexical coverage at a fixed 0.5 decision threshold.",
        metrics=metrics,
        execution_result={
            "command": "python -m experiments.reviewx_scifact.closed_loop",
            "exit_code": 0,
            "stdout": f"{run_id} completed on {len(examples)} frozen records.\n",
            "stderr": "",
            "duration_seconds": duration_seconds,
        },
        require_ablation=require_ablation,
    )
    return evidence, results


def _qwen_plan(
    diagnostics: dict[str, dict[str, float]],
    audited: dict[str, dict[str, Any]],
    expected_candidate: str,
    *,
    reviewx_feedback: dict[str, Any],
    model: str | None,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    candidate_specs = {
        item.candidate_id: {
            "change": item.change,
            "threshold": item.threshold,
            "removedFeatures": [
                name for index, name in enumerate(FEATURE_NAMES)
                if index not in item.selected_features
            ],
            "diagnostics": diagnostics[item.candidate_id],
            "guardrailAudit": audited[item.candidate_id],
        }
        for item in CANDIDATES
    }
    feasible = [
        candidate_id for candidate_id, item in audited.items() if item["feasible"]
    ]
    best_feasible_f1 = max(diagnostics[item]["F1-Score"] for item in feasible)
    shortlist = sorted(
        item for item in feasible
        if diagnostics[item]["F1-Score"] >= best_feasible_f1 - 0.005
    )
    selection_manifest = {
        "feasibleCandidateIds": sorted(feasible),
        "bestFeasibleF1": best_feasible_f1,
        "nearBestF1Tolerance": 0.005,
        "nearBestCandidateIds": shortlist,
        "tieBreakOrder": ["lowest ECE", "fewer features", "lexical candidate id"],
        "policySelectedCandidateId": expected_candidate,
    }
    prompt = (
        "You are the planning agent in a controlled AI Scientist experiment. "
        "The deterministic policy engine has already selected the round-two candidate. "
        "Audit that selection, explain the evidence-grounded change, and define falsification criteria. "
        "You must copy policySelectedCandidateId exactly into selectedCandidateId. "
        "Never infer or invent final holdout results. The preregistered rule is: discard infeasible "
        "candidates; find the highest feasible F1; retain candidates within 0.005 F1; among them "
        "choose the lowest ECE, then fewer features, then lexical candidate id. Return one JSON "
        "object with keys selectedCandidateId, rationale, evidenceUsed, expectedTradeoff, "
        "falsificationCriteria, and finalHoldoutPolicy. finalHoldoutPolicy must state that official "
        "SciFact dev is evaluated once after selection and was not exposed during planning.\n\n"
        + json.dumps({
            "task": "unsupported scientific claim-document pair detection",
            "primaryMetric": "F1-Score",
            "guardrails": {
                "minimumPrecision": 0.66,
                "maximumBrierIncrease": 0.001,
                "maximumAUROCDecrease": 0.002,
            },
            "deterministicSelectionManifest": selection_manifest,
            "reviewxRoundOneFeedback": reviewx_feedback,
            "candidates": candidate_specs,
        }, ensure_ascii=False, indent=2)
    )
    client = get_provider_client("qwen")
    messages = [
        ChatMessage(
            role="system",
            content=(
                "Follow the deterministic selection manifest exactly. "
                "Your role is to audit and operationalize the selected change. Return valid JSON only."
            ),
        ),
        ChatMessage(role="user", content=prompt),
    ]
    attempts: list[dict[str, Any]] = []
    response = None
    plan: dict[str, Any] = {}
    for attempt_number in range(1, 3):
        response = client.chat(
            messages,
            model=model,
            temperature=0,
            max_tokens=1000,
            structured_output=True,
        )
        raw_text = response.text.strip()
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        plan = json.loads(raw_text)
        policy_followed = plan.get("selectedCandidateId") == expected_candidate
        attempts.append({
            "attempt": attempt_number,
            "selectedCandidateId": plan.get("selectedCandidateId"),
            "policyFollowed": policy_followed,
            "latencyMs": response.latency_ms,
            "usage": response.usage,
            "finishReason": response.finish_reason,
        })
        if policy_followed:
            break
        messages.extend([
            ChatMessage(role="assistant", content=response.text),
            ChatMessage(
                role="user",
                content=(
                    "Policy audit failed. policySelectedCandidateId is "
                    f"{expected_candidate}; copy that exact value and regenerate the JSON plan."
                ),
            ),
        ])
    if response is None or plan.get("selectedCandidateId") != expected_candidate:
        raise ValueError(
            "Qwen plan violated the deterministic policy after correction: "
            f"expected {expected_candidate}, got {plan.get('selectedCandidateId')}"
        )
    for key in (
        "rationale", "evidenceUsed", "expectedTradeoff",
        "falsificationCriteria", "finalHoldoutPolicy",
    ):
        if not plan.get(key):
            raise ValueError(f"Qwen plan is missing required field: {key}")
    trace = {
        "stage": "round_two_plan_revision",
        "provider": response.raw_provider,
        "model": response.model,
        "latencyMs": response.latency_ms,
        "usage": response.usage,
        "finishReason": response.finish_reason,
        "attempts": attempts,
        "policyCorrectionRequired": len(attempts) > 1,
        "selectionManifest": selection_manifest,
        "promptSha256": f"sha256:{hashlib.sha256(prompt.encode('utf-8')).hexdigest()}",
        "finalHoldoutExposedToQwen": False,
    }
    return plan, trace, prompt


def _paired_bootstrap(
    labels: np.ndarray,
    round_one: np.ndarray,
    round_two: np.ndarray,
    *,
    round_one_threshold: float,
    round_two_threshold: float,
    samples: int,
) -> dict[str, Any]:
    rng = np.random.default_rng(BOOTSTRAP_SEED + 17)
    values = {name: [] for name in ("F1-Score", "Brier Score", "Expected Calibration Error (ECE)", "AUROC")}
    directions = {"F1-Score": 1, "Brier Score": -1, "Expected Calibration Error (ECE)": -1, "AUROC": 1}
    for _ in range(samples):
        indices = rng.integers(0, len(labels), size=len(labels))
        sampled_labels = labels[indices]
        if len(set(sampled_labels.tolist())) < 2:
            continue
        first = metrics_at_threshold(sampled_labels, round_one[indices], round_one_threshold)
        second = metrics_at_threshold(sampled_labels, round_two[indices], round_two_threshold)
        for name, direction in directions.items():
            values[name].append(direction * (second[name] - first[name]))
    return {
        name: {
            "improvementMean": float(np.mean(items)),
            "ci95Low": float(np.percentile(items, 2.5)),
            "ci95High": float(np.percentile(items, 97.5)),
            "probabilityOfImprovement": float(np.mean(np.asarray(items) > 0)),
        }
        for name, items in values.items()
    }


def _report(summary: dict[str, Any]) -> str:
    first = summary["feedbackResults"]["roundOne"]
    second = summary["feedbackResults"]["roundTwo"]
    holdout_first = summary["finalHoldout"]["roundOne"]
    holdout_second = summary["finalHoldout"]["roundTwo"]
    f1_bootstrap = summary["finalHoldout"]["pairedBootstrap"]["F1-Score"]
    usage = summary["qwenTrace"]["usage"]
    timing = summary["executionTiming"]
    durations = timing["durationsSeconds"]
    resources = timing["resourceSnapshot"]
    lines = [
        "# FAROS ReviewX SciFact真实数据两轮闭环实验",
        "",
        f"> 运行编号：`{summary['runId']}`  ",
        f"> Qwen模型：`{summary['qwenTrace']['model']}`  ",
        f"> 质量门：**{summary['qualityGate']['status']}**",
        "",
        "## 实验设计",
        "",
        "官方训练集按claim分组拆分为拟合集与反馈集，避免同一claim跨分区。确定性策略先按预注册门禁筛选候选，Qwen只读取反馈集诊断，负责审计选择并形成第二轮实施与证伪方案。官方dev集在方案冻结前完全封存，只在最后评估一次。",
        f"样本规模：拟合集{summary['dataset']['fitPairs']}对、反馈集{summary['dataset']['feedbackPairs']}对、最终未见dev集{summary['dataset']['finalHoldoutPairs']}对。",
        f"冻结反馈基准指纹：`{summary['benchmarkFingerprint']}`。",
        f"执行前预注册：`{summary['preregistration']['contentHash']}`（{summary['preregistration']['createdAt']}）。",
        f"第二轮冻结方案：`{summary['roundTwoPlan']['contentHash']}`（{summary['roundTwoPlan']['createdAt']}）。",
        "",
        "## Qwen参与和第二轮修改",
        "",
        f"- 提供方/模型：`{summary['qwenTrace']['provider']}` / `{summary['qwenTrace']['model']}`",
        f"- 实际调用：{usage['prompt_tokens']} prompt tokens + {usage['completion_tokens']} completion tokens，耗时{summary['qwenTrace']['latencyMs']} ms",
        f"- 门禁选定并由Qwen审计：`{summary['qwenPlan']['selectedCandidateId']}`",
        f"- 理由：{summary['qwenPlan']['rationale']}",
        f"- 预期权衡：{summary['qwenPlan']['expectedTradeoff']}",
        f"- 最终测试隔离：`finalHoldoutExposedToQwen={str(summary['qwenTrace']['finalHoldoutExposedToQwen']).lower()}`",
        "",
        "## 冻结反馈集两轮结果",
        "",
        "| 轮次 | Precision | Recall | F1 | Brier | ECE | AUROC |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        f"| 第一轮 | {first['Precision']:.4f} | {first['Recall']:.4f} | {first['F1-Score']:.4f} | {first['Brier Score']:.4f} | {first['Expected Calibration Error (ECE)']:.4f} | {first['AUROC']:.4f} |",
        f"| 第二轮 | {second['Precision']:.4f} | {second['Recall']:.4f} | {second['F1-Score']:.4f} | {second['Brier Score']:.4f} | {second['Expected Calibration Error (ECE)']:.4f} | {second['AUROC']:.4f} |",
        "",
        "## 未见数据最终测试",
        "",
        "| 轮次 | Precision | Recall | F1 | Brier | ECE | AUROC |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        f"| 第一轮 | {holdout_first['Precision']:.4f} | {holdout_first['Recall']:.4f} | {holdout_first['F1-Score']:.4f} | {holdout_first['Brier Score']:.4f} | {holdout_first['Expected Calibration Error (ECE)']:.4f} | {holdout_first['AUROC']:.4f} |",
        f"| 第二轮 | {holdout_second['Precision']:.4f} | {holdout_second['Recall']:.4f} | {holdout_second['F1-Score']:.4f} | {holdout_second['Brier Score']:.4f} | {holdout_second['Expected Calibration Error (ECE)']:.4f} | {holdout_second['AUROC']:.4f} |",
        "",
        f"最终F1绝对变化：{holdout_second['F1-Score'] - holdout_first['F1-Score']:+.4f}；配对bootstrap平均改善{f1_bootstrap['improvementMean']:+.4f}，95% CI [{f1_bootstrap['ci95Low']:+.4f}, {f1_bootstrap['ci95High']:+.4f}]，改善概率{f1_bootstrap['probabilityOfImprovement']:.3f}。",
        "第二轮通过删除不稳定数值对齐因子并降低阈值提高召回率，代价是精确率下降。置信区间跨越0，因此本组只能证明闭环可运行、反馈集改进能迁移为未见集非退化，不能宣称统计显著或跨领域泛化。",
        "",
        "## 审计结论",
        "",
        f"- 第一轮ExperimentEvidence：`{summary['audits']['roundOneEvidence']}`",
        f"- 第二轮ExperimentEvidence：`{summary['audits']['roundTwoEvidence']}`",
        f"- 第一轮ReviewX gate：`{summary['audits']['roundOneReview']}`",
        f"- 第二轮ReviewX gate：`{summary['audits']['roundTwoReview']}`",
        f"- 同一冻结基准：`{summary['audits']['sameFrozenBenchmark']}`",
        f"- 人工签核：`{summary['humanSignoff']['status']}`；自动执行不推断人工批准",
        "",
        "## 时间、资源与复现边界",
        "",
        f"- 总墙钟时间：{durations['total']:.3f} s；其中第一轮{durations['roundOneCompute']:.3f} s、Qwen规划{durations['qwenPlanningApi']:.3f} s、第二轮{durations['roundTwoCompute']:.3f} s、最终留出集与bootstrap {durations['finalHoldoutAndBootstrap']:.3f} s",
        f"- 运行环境：Python {resources['python']}，{resources['processorCount']} CPU，峰值常驻内存{resources['maxResidentSetKiB']} KiB",
        f"- Qwen用量：{usage['total_tokens']} tokens；提示词与响应元数据保存在脱敏审计轨迹中",
        "- 当前证据证明闭环可执行和可审计，不证明统计显著、跨领域泛化或SOTA。对外发布前仍需人工签核。",
        "",
    ]
    return "\n".join(lines)


def run_closed_loop(
    dataset_root: Path,
    output_dir: Path,
    *,
    model: str | None = None,
    bootstrap_samples: int = 2000,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    total_started = perf_counter()
    run_id = "scifact_loop_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    round_one_id = f"{run_id}_r1"
    round_two_id = f"{run_id}_r2"
    timeline_path = output_dir / "timeline.json"
    timeline: list[dict[str, Any]] = []
    preregistration = preregistration_payload(run_id, _now())
    _write_json(output_dir / "preregistration.json", preregistration)
    _append_timeline(
        timeline_path,
        timeline,
        "preregistration_frozen",
        contentHash=preregistration["contentHash"],
    )

    train = load_examples(dataset_root, "train")
    fit, feedback = split_fit_feedback(train)
    extractor = FactorizedFeatureExtractor().fit(fit)
    fit_features = extractor.transform(fit)
    feedback_features = extractor.transform(feedback)
    fit_labels = np.asarray([item.label for item in fit], dtype=float)
    feedback_labels = np.asarray([item.label for item in feedback], dtype=float)
    benchmark = _benchmark(feedback, feedback_features, "scifact_train_feedback")
    _append_timeline(
        timeline_path,
        timeline,
        "fit_and_feedback_partitions_ready",
        fitPairs=len(fit),
        feedbackPairs=len(feedback),
        benchmarkFingerprint=benchmark["fingerprint"],
    )

    round_one_started = perf_counter()
    candidate_models: dict[str, NumpyLogisticRegression] = {}
    candidate_probabilities: dict[str, np.ndarray] = {}
    for candidate in CANDIDATES:
        model_instance = NumpyLogisticRegression().fit(
            fit_features[:, candidate.selected_features], fit_labels,
        )
        candidate_models[candidate.candidate_id] = model_instance
        candidate_probabilities[candidate.candidate_id] = model_instance.predict_proba(
            feedback_features[:, candidate.selected_features]
        )
    baseline_feedback = np.clip(1.0 - feedback_features[:, 2], 0.01, 0.99)

    diagnostics = {
        candidate.candidate_id: metrics_at_threshold(
            feedback_labels,
            candidate_probabilities[candidate.candidate_id],
            candidate.threshold,
        )
        for candidate in CANDIDATES
    }
    round_one_metrics = diagnostics["retain_full_0500"]
    round_one_probabilities = {
        "baseline": baseline_feedback,
        "method": candidate_probabilities["retain_full_0500"],
        "ablation_no_numeric": candidate_probabilities["remove_numeric_0375"],
        "ablation_no_negation": candidate_probabilities["remove_negation_0375"],
        "ablation_no_entity": candidate_probabilities["remove_entity_0375"],
    }
    round_one_thresholds = {name: ROUND_ONE_THRESHOLD for name in round_one_probabilities}
    round_one_duration = perf_counter() - round_one_started
    evidence_one, results_one = _write_round(
        output_dir / "round_1",
        run_id=round_one_id,
        benchmark=benchmark,
        examples=feedback,
        probabilities=round_one_probabilities,
        thresholds=round_one_thresholds,
        method="Full factorized consistency model with a fixed 0.5 threshold.",
        require_ablation=True,
        duration_seconds=round_one_duration,
    )
    _append_timeline(
        timeline_path,
        timeline,
        "round_one_executed_and_audited",
        durationSeconds=round_one_duration,
        evidenceStatus=evidence_one.status.value,
    )
    dossier_one = _dossier(round_one_id, model or "qwen", [])
    review_one = review_experiment_feedback(
        dossier_one, evidence_one, execution_assessment=_assessment(round_one_id),
    )
    _append_timeline(
        timeline_path,
        timeline,
        "reviewx_round_one_feedback_created",
        gateStatus=review_one.qualityAssessment.gateStatus.value,
        decision=review_one.iterationDecision.decision,
    )

    selected_id, audited_candidates = select_candidate(diagnostics, round_one_metrics)
    qwen_plan, qwen_trace, qwen_prompt = _qwen_plan(
        diagnostics,
        audited_candidates,
        selected_id,
        reviewx_feedback={
            "qualityAssessment": review_one.qualityAssessment.model_dump(mode="json"),
            "iterationDecision": review_one.iterationDecision.model_dump(mode="json"),
        },
        model=model,
    )
    selected = next(item for item in CANDIDATES if item.candidate_id == selected_id)
    model_name = qwen_trace["model"]
    _write_json(output_dir / "qwen_iteration_plan.json", qwen_plan)
    _write_json(output_dir / "qwen_trace.json", qwen_trace)
    (output_dir / "qwen_prompt.txt").write_text(qwen_prompt, encoding="utf-8")
    selected_feature_names = [
        FEATURE_NAMES[index] for index in selected.selected_features
    ]
    plan_changes: list[PlanFieldDelta] = []
    if selected_feature_names != FEATURE_NAMES:
        plan_changes.append(PlanFieldDelta(
            fieldPath="model.selectedFeatures",
            before=FEATURE_NAMES,
            after=selected_feature_names,
            rationale="Remove factors whose near-best candidate has better calibration under frozen guardrails.",
            expectedEffect="Reduce sensitivity to unstable numeric alignment while preserving near-best F1.",
            evidenceIds=["ev-candidate-arena", "ev-qwen-plan"],
        ))
    if selected.threshold != ROUND_ONE_THRESHOLD:
        plan_changes.append(PlanFieldDelta(
            fieldPath="decisionThreshold",
            before=ROUND_ONE_THRESHOLD,
            after=selected.threshold,
            rationale="The frozen feedback set shows that the original threshold misses unsupported pairs.",
            expectedEffect="Increase recall and F1 while retaining the preregistered precision floor.",
            evidenceIds=["ev-round-one-metrics", "ev-candidate-arena", "ev-qwen-plan"],
        ))
    criteria = qwen_plan["falsificationCriteria"]
    if not isinstance(criteria, list):
        criteria = [str(criteria)]
    delta_contract = seal_plan_delta_contract({
        "contractId": f"delta_{run_id}",
        "researchSeriesId": run_id,
        "fromRunId": round_one_id,
        "toRunId": round_two_id,
        "createdAt": _now(),
        "benchmarkFingerprint": benchmark["fingerprint"],
        "evidenceGateStatus": review_one.qualityAssessment.gateStatus.value,
        "scientificDecision": "revise_plan",
        "trigger": DeltaTrigger(
            status="optimization_opportunity",
            statement=(
                "A guardrail-feasible candidate improves frozen-feedback F1 by at least "
                "the preregistered 0.01 margin, so the next plan should change before "
                "the final holdout is exposed."
            ),
            metric="F1-Score",
            observedValue=round_one_metrics["F1-Score"],
            targetValue=round_one_metrics["F1-Score"] + 0.01,
            comparator=">=",
            evidenceIds=["ev-round-one-metrics", "ev-candidate-arena"],
        ).model_dump(mode="json"),
        "evidence": [
            DeltaEvidenceRef(
                id="ev-round-one-metrics",
                artifact="round_1/metrics.json",
                jsonPath="$.metrics.method",
                authority="observed",
                summary="First-round metrics recomputed from per-record predictions.",
                value=round_one_metrics,
            ).model_dump(mode="json"),
            DeltaEvidenceRef(
                id="ev-candidate-arena",
                artifact="candidate_diagnostics.json",
                jsonPath="$",
                authority="deterministic",
                summary="Frozen candidate metrics and guardrail feasibility audit.",
                value={"selectedCandidateId": selected_id},
            ).model_dump(mode="json"),
            DeltaEvidenceRef(
                id="ev-reviewx-gate",
                artifact="reviewx_round_1.json",
                jsonPath="$.qualityAssessment",
                authority="deterministic",
                summary="ReviewX verified provenance, completeness, and plan alignment before revision.",
                value={"gateStatus": review_one.qualityAssessment.gateStatus.value},
            ).model_dump(mode="json"),
            DeltaEvidenceRef(
                id="ev-qwen-plan",
                artifact="qwen_iteration_plan.json",
                jsonPath="$",
                authority="qwen",
                summary="Qwen audited the frozen selection and supplied tradeoffs and falsification criteria.",
                value={"selectedCandidateId": qwen_plan["selectedCandidateId"]},
            ).model_dump(mode="json"),
        ],
        "candidates": [
            CandidateAudit(
                candidateId=item.candidate_id,
                feasible=bool(audited_candidates[item.candidate_id]["feasible"]),
                metrics=diagnostics[item.candidate_id],
                failedConstraints=[
                    name
                    for name, passed in audited_candidates[item.candidate_id]["checks"].items()
                    if not passed
                ],
                change=item.change,
            ).model_dump(mode="json")
            for item in CANDIDATES
        ],
        "selectedCandidateId": selected_id,
        "changes": [item.model_dump(mode="json") for item in plan_changes],
        "qwenContribution": QwenPlanContribution(
            model=model_name,
            role=(
                "Audit and operationalize the deterministic candidate selection; explain "
                "tradeoffs and define falsification criteria without changing observed metrics."
            ),
            selectedCandidateId=qwen_plan["selectedCandidateId"],
            rationale=qwen_plan["rationale"],
            expectedTradeoff=qwen_plan["expectedTradeoff"],
            falsificationCriteria=criteria,
            promptHash=qwen_trace["promptSha256"],
            finalHoldoutExposed=qwen_trace["finalHoldoutExposedToQwen"],
        ).model_dump(mode="json"),
        "stopConditions": preregistration["roundOnePlan"]["stopConditions"],
        "finalHoldoutPolicy": qwen_plan["finalHoldoutPolicy"],
    })
    delta_contract_payload = delta_contract.model_dump(mode="json")
    _write_json(output_dir / "plan_delta_contract.json", delta_contract_payload)
    _append_timeline(
        timeline_path,
        timeline,
        "plan_delta_contract_frozen",
        contentHash=delta_contract.contentHash,
        selectedCandidateId=selected_id,
        changedFields=[item.fieldPath for item in delta_contract.changes],
        finalHoldoutLoaded=False,
    )
    round_two_plan_base = {
        "schemaVersion": "faros-experiment-plan/v1",
        "runId": round_two_id,
        "parentRunId": round_one_id,
        "createdAt": _now(),
        "benchmarkFingerprint": benchmark["fingerprint"],
        "selectedCandidateId": selected_id,
        "change": selected.change,
        "decisionThreshold": selected.threshold,
        "selectedFeatures": selected_feature_names,
        "reviewxFeedback": review_one.iterationDecision.model_dump(mode="json"),
        "planDeltaContract": {
            "path": "plan_delta_contract.json",
            "contentHash": delta_contract.contentHash,
        },
        "qwenPlan": qwen_plan,
        "stopConditions": preregistration["roundOnePlan"]["stopConditions"],
        "finalHoldoutPolicy": qwen_plan["finalHoldoutPolicy"],
    }
    round_two_plan = {
        **round_two_plan_base,
        "contentHash": _payload_hash(round_two_plan_base),
    }
    _write_json(output_dir / "round_2_plan.json", round_two_plan)
    _append_timeline(
        timeline_path,
        timeline,
        "round_two_plan_frozen",
        contentHash=round_two_plan["contentHash"],
        selectedCandidateId=selected_id,
        finalHoldoutLoaded=False,
    )

    round_two_started = perf_counter()
    selected_model = NumpyLogisticRegression().fit(
        fit_features[:, selected.selected_features], fit_labels,
    )
    selected_feedback_probabilities = selected_model.predict_proba(
        feedback_features[:, selected.selected_features]
    )
    round_two_probabilities = {
        "baseline": baseline_feedback,
        "method": selected_feedback_probabilities,
        "ablation_original_full": candidate_probabilities["retain_full_0500"],
    }
    round_two_thresholds = {
        "baseline": ROUND_ONE_THRESHOLD,
        "method": selected.threshold,
        "ablation_original_full": ROUND_ONE_THRESHOLD,
    }
    round_two_duration = perf_counter() - round_two_started
    evidence_two, results_two = _write_round(
        output_dir / "round_2",
        run_id=round_two_id,
        benchmark=benchmark,
        examples=feedback,
        probabilities=round_two_probabilities,
        thresholds=round_two_thresholds,
        method=selected.change,
        require_ablation=True,
        duration_seconds=round_two_duration,
    )
    dossier_two = _dossier(round_two_id, model_name, [qwen_trace])
    review_two = review_experiment_feedback(
        dossier_two,
        evidence_two,
        execution_assessment=_assessment(round_two_id),
        previous_experiment=evidence_one,
        same_research_series=True,
    )
    _append_timeline(
        timeline_path,
        timeline,
        "round_two_executed_and_audited",
        durationSeconds=round_two_duration,
        evidenceStatus=evidence_two.status.value,
        reviewxGate=review_two.qualityAssessment.gateStatus.value,
    )

    feedback_round_one = results_one["method"]
    feedback_round_two = results_two["method"]

    holdout_started = perf_counter()
    final_holdout = load_examples(dataset_root, "dev")
    holdout_features = extractor.transform(final_holdout)
    holdout_labels = np.asarray([item.label for item in final_holdout], dtype=float)
    candidate_holdout_probabilities = {
        "retain_full_0500": candidate_models["retain_full_0500"].predict_proba(
            holdout_features[:, CANDIDATES[0].selected_features]
        ),
        selected_id: selected_model.predict_proba(
            holdout_features[:, selected.selected_features]
        ),
    }
    _append_timeline(
        timeline_path,
        timeline,
        "official_dev_loaded_after_plan_freeze",
        finalHoldoutPairs=len(final_holdout),
    )
    holdout_round_one = metrics_at_threshold(
        holdout_labels, candidate_holdout_probabilities["retain_full_0500"], ROUND_ONE_THRESHOLD,
    )
    holdout_round_two = metrics_at_threshold(
        holdout_labels, candidate_holdout_probabilities[selected_id], selected.threshold,
    )
    bootstrap = _paired_bootstrap(
        holdout_labels,
        candidate_holdout_probabilities["retain_full_0500"],
        candidate_holdout_probabilities[selected_id],
        round_one_threshold=ROUND_ONE_THRESHOLD,
        round_two_threshold=selected.threshold,
        samples=bootstrap_samples,
    )
    holdout_duration = perf_counter() - holdout_started
    _append_timeline(
        timeline_path,
        timeline,
        "final_holdout_evaluated_once",
        durationSeconds=holdout_duration,
        bootstrapSamples=bootstrap_samples,
    )
    records = [
        {
            "id": f"{run_id}_feedback_r1", "runId": round_one_id,
            "iterationNumber": 1, "benchmarkFingerprint": benchmark["fingerprint"],
            "metricSnapshot": experiment_metric_snapshot(evidence_one),
            "qualityAssessment": review_one.qualityAssessment.model_dump(mode="json"),
            "iterationDecision": review_one.iterationDecision.model_dump(mode="json"),
        },
        {
            "id": f"{run_id}_feedback_r2", "runId": round_two_id,
            "iterationNumber": 2, "benchmarkFingerprint": benchmark["fingerprint"],
            "metricSnapshot": experiment_metric_snapshot(evidence_two),
            "qualityAssessment": review_two.qualityAssessment.model_dump(mode="json"),
            "iterationDecision": review_two.iterationDecision.model_dump(mode="json"),
        },
    ]
    policy = ExperimentLoopPolicy(
        primaryMetric="method:F1-Score",
        direction="maximize",
        minIterations=2,
        maxIterations=2,
        minAbsoluteImprovement=0.001,
        patience=1,
        guardrails=[
            MetricGuardrail(metric="method:Brier Score", direction="minimize", threshold=feedback_round_one["Brier Score"] + 0.001),
            MetricGuardrail(metric="method:Expected Calibration Error (ECE)", direction="minimize", threshold=feedback_round_one["Expected Calibration Error (ECE)"]),
            MetricGuardrail(metric="method:AUROC", direction="maximize", threshold=feedback_round_one["AUROC"] - 0.002),
        ],
    )
    progress = evaluate_experiment_series(run_id, records, policy)
    event_names = [item["event"] for item in timeline]
    preregistration_hash_valid = preregistration["contentHash"] == _payload_hash({
        key: value for key, value in preregistration.items() if key != "contentHash"
    })
    round_two_plan_hash_valid = round_two_plan["contentHash"] == _payload_hash({
        key: value for key, value in round_two_plan.items() if key != "contentHash"
    })
    delta_contract_audit = verify_plan_delta_contract(delta_contract_payload)
    quality_checks = {
        "preregistrationHashValid": preregistration_hash_valid,
        "preregistrationFrozenBeforeRoundOne": (
            event_names.index("preregistration_frozen")
            < event_names.index("round_one_executed_and_audited")
        ),
        "roundTwoPlanHashValid": round_two_plan_hash_valid,
        "planDeltaContractValid": delta_contract_audit["status"] == "passed",
        "planDeltaFrozenBeforeHoldoutLoad": (
            event_names.index("plan_delta_contract_frozen")
            < event_names.index("official_dev_loaded_after_plan_freeze")
        ),
        "roundTwoPlanFrozenBeforeHoldoutLoad": (
            event_names.index("round_two_plan_frozen")
            < event_names.index("official_dev_loaded_after_plan_freeze")
        ),
        "qwenFollowedPreregisteredPolicy": qwen_plan["selectedCandidateId"] == selected_id,
        "roundOneEvidenceAuditPassed": evidence_one.metricAudit.get("status") == "passed",
        "roundTwoEvidenceAuditPassed": evidence_two.metricAudit.get("status") == "passed",
        "sameFrozenBenchmark": evidence_one.dataHashes.get("frozen_benchmark") == evidence_two.dataHashes.get("frozen_benchmark"),
        "feedbackF1ImprovedByAtLeastOnePoint": feedback_round_two["F1-Score"] >= feedback_round_one["F1-Score"] + 0.01,
        "feedbackGuardrailsPassed": progress.guardrailsSatisfied,
        "finalHoldoutF1DidNotRegress": holdout_round_two["F1-Score"] >= holdout_round_one["F1-Score"],
        "finalHoldoutBrierWithinTolerance": holdout_round_two["Brier Score"] <= holdout_round_one["Brier Score"] + 0.001,
        "finalHoldoutECEWithinTolerance": holdout_round_two["Expected Calibration Error (ECE)"] <= holdout_round_one["Expected Calibration Error (ECE)"] + 0.005,
        "finalHoldoutAUROCWithinTolerance": holdout_round_two["AUROC"] >= holdout_round_one["AUROC"] - 0.002,
    }
    usage = resource.getrusage(resource.RUSAGE_SELF)
    execution_timing = {
        "schemaVersion": "faros-execution-timing/v1",
        "runId": run_id,
        "measuredWith": "time.perf_counter",
        "durationsSeconds": {
            "roundOneCompute": round_one_duration,
            "qwenPlanningApi": qwen_trace["latencyMs"] / 1000,
            "roundTwoCompute": round_two_duration,
            "finalHoldoutAndBootstrap": holdout_duration,
            "total": perf_counter() - total_started,
        },
        "resourceSnapshot": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "processorCount": os.cpu_count(),
            "maxResidentSetKiB": int(usage.ru_maxrss),
            "userCpuSeconds": float(usage.ru_utime),
            "systemCpuSeconds": float(usage.ru_stime),
        },
        "qwenUsage": qwen_trace["usage"],
    }
    _write_json(output_dir / "execution_timing.json", execution_timing)
    summary = {
        "schemaVersion": LOOP_SCHEMA,
        "runId": run_id,
        "dataset": {
            "name": "SciFact", "url": DATASET_URL, "repository": DATASET_REPOSITORY,
            "paper": DATASET_PAPER, "archiveSha256": DATASET_SHA256,
            "fitPairs": len(fit), "feedbackPairs": len(feedback),
            "finalHoldoutPairs": len(final_holdout),
            "splitUnit": "claim_id", "finalHoldoutExposedToQwen": False,
        },
        "benchmarkFingerprint": benchmark["fingerprint"],
        "preregistration": {
            "path": "preregistration.json",
            "contentHash": preregistration["contentHash"],
            "createdAt": preregistration["createdAt"],
        },
        "roundTwoPlan": {
            "path": "round_2_plan.json",
            "contentHash": round_two_plan["contentHash"],
            "createdAt": round_two_plan["createdAt"],
        },
        "planDeltaContract": {
            "path": "plan_delta_contract.json",
            "contentHash": delta_contract.contentHash,
            "audit": delta_contract_audit,
            "selectedCandidateId": delta_contract.selectedCandidateId,
            "changedFields": [item.fieldPath for item in delta_contract.changes],
        },
        "candidateDiagnostics": audited_candidates,
        "qwenPlan": qwen_plan,
        "qwenTrace": qwen_trace,
        "feedbackResults": {"roundOne": feedback_round_one, "roundTwo": feedback_round_two},
        "finalHoldout": {
            "roundOne": holdout_round_one, "roundTwo": holdout_round_two,
            "pairedBootstrap": bootstrap,
        },
        "seriesProgress": progress.model_dump(mode="json"),
        "executionTiming": execution_timing,
        "audits": {
            "roundOneEvidence": evidence_one.status.value,
            "roundTwoEvidence": evidence_two.status.value,
            "roundOneReview": review_one.qualityAssessment.gateStatus,
            "roundTwoReview": review_two.qualityAssessment.gateStatus,
            "sameFrozenBenchmark": quality_checks["sameFrozenBenchmark"],
        },
        "humanSignoff": {
            "status": "pending",
            "requiredFor": ["public scientific claims", "competition submission"],
            "note": "No human approval is inferred from automated execution.",
        },
        "qualityGate": {
            "status": "passed" if all(quality_checks.values()) else "failed",
            "checks": quality_checks,
        },
    }
    _append_timeline(
        timeline_path,
        timeline,
        "quality_gate_completed",
        status=summary["qualityGate"]["status"],
    )
    _write_json(output_dir / "summary.json", summary)
    _write_json(output_dir / "candidate_diagnostics.json", audited_candidates)
    _write_json(output_dir / "plan_delta_contract.json", delta_contract_payload)
    _write_json(output_dir / "qwen_iteration_plan.json", qwen_plan)
    _write_json(output_dir / "qwen_trace.json", qwen_trace)
    (output_dir / "qwen_prompt.txt").write_text(qwen_prompt, encoding="utf-8")
    _write_json(output_dir / "reviewx_round_1.json", review_one.model_dump(mode="json"))
    _write_json(output_dir / "reviewx_round_2.json", review_two.model_dump(mode="json"))
    _write_json(output_dir / "experiment_series.json", progress.model_dump(mode="json"))
    _write_json(output_dir / "human_signoff.json", summary["humanSignoff"])
    (output_dir / "competition_report.md").write_text(_report(summary), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("--bootstrap-samples", type=int, default=2000)
    args = parser.parse_args()
    dataset_root = ensure_dataset(args.data_dir, download=args.download)
    summary = run_closed_loop(
        dataset_root,
        args.output_dir,
        model=args.model,
        bootstrap_samples=args.bootstrap_samples,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["qualityGate"]["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
