"""Evaluate whether ReviewX rejects controlled faults in real experiment artifacts."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from app.llm.provider_client import ChatMessage, get_provider_client
from experiments.reviewx_multidomain.run import (
    extended_metrics,
    load_climate_fever,
    load_pubhealth,
)


SCHEMA_VERSION = "reviewx-scientific-reliability/v1"
BENCHMARK_SEED = 20260825
FAULT_TYPES = (
    "reported_metric_fabrication",
    "holdout_tuning",
    "claim_group_leakage",
    "selective_metric_reporting",
    "artifact_hash_tampering",
)
REQUIRED_METRICS = (
    "F1-Score",
    "Macro F1",
    "Balanced Accuracy",
    "Matthews Correlation Coefficient",
    "Brier Score",
    "AUROC",
)
FAULT_CODES = {
    "reported_metric_fabrication": "REPORTED_METRIC_MISMATCH",
    "holdout_tuning": "TEST_LABEL_SELECTION_LEAKAGE",
    "claim_group_leakage": "CLAIM_GROUP_SPLIT_LEAKAGE",
    "selective_metric_reporting": "REQUIRED_METRICS_MISSING",
    "artifact_hash_tampering": "EVALUATION_RECORD_HASH_MISMATCH",
}


@dataclass(frozen=True)
class BaseArtifact:
    dataset: str
    source_run: str
    records: list[dict[str, Any]]
    threshold: float
    split_audit: dict[str, Any]
    fit_group_ids: list[str]
    evaluation_group_ids: list[str]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _blind_case_id(pair_id: str, variant: str) -> str:
    digest = hashlib.sha256(f"{BENCHMARK_SEED}:{pair_id}:{variant}".encode("utf-8")).hexdigest()
    return f"sr-{digest[:16]}"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_record(
    record: dict[str, Any],
    *,
    method: str,
    threshold: float,
) -> dict[str, Any]:
    predictions = record["predictions"][method]
    sample_id = str(record.get("sampleId") or record.get("sample_id"))
    claim_id = record.get("claimId")
    if claim_id is None:
        parts = sample_id.split("-")
        claim_id = parts[-2] if len(parts) >= 2 else sample_id
    probability = float(predictions["probability"])
    return {
        "sampleId": sample_id,
        "claimId": str(claim_id),
        "label": int(record["label"]),
        "probability": probability,
        "prediction": int(probability >= threshold),
    }


def load_real_artifacts(experiment_root: Path) -> list[BaseArtifact]:
    scifact_path = experiment_root / "reviewx_scifact" / "evaluation_records.json"
    scifact = _read_json(scifact_path)
    scifact_threshold = float(scifact.get("decision_threshold", 0.5))
    scifact_fit_path = experiment_root / "reviewx_scifact_closed_loop_v2" / "round_2" / "evaluation_records.json"
    scifact_fit = _read_json(scifact_fit_path)
    scifact_fit_groups = sorted({
        str(item["sample_id"]).split("-")[-2] for item in scifact_fit["records"]
    })
    scifact_eval_groups = sorted({
        str(item["sample_id"]).split("-")[-2] for item in scifact["records"]
    })
    artifacts = [
        BaseArtifact(
            dataset="SciFact",
            source_run=str(scifact_path),
            records=[
                _normalize_record(item, method="method", threshold=scifact_threshold)
                for item in scifact["records"]
            ],
            threshold=scifact_threshold,
            split_audit={
                "selectionSplit": "train",
                "evaluationSplit": "official_dev",
                "testLabelsUsedForSelection": False,
                "groupIntersections": {"trainTest": 0},
            },
            fit_group_ids=scifact_fit_groups,
            evaluation_group_ids=scifact_eval_groups,
        )
    ]
    external_root = experiment_root.parent / "external"
    climate_dataset = load_climate_fever(
        external_root / "climate_fever" / "climate-fever.jsonl",
    )
    pubhealth_dataset = load_pubhealth(
        external_root / "pubhealth" / "PUBHEALTH",
        external_root / "pubhealth" / "PUBHEALTH.zip",
    )
    domain_groups = {
        "Climate-FEVER": (
            sorted({str(item.claim_id) for item in climate_dataset.train}),
            sorted({str(item.claim_id) for item in climate_dataset.test}),
        ),
        "PubHealth": (
            sorted({str(item.claim_id) for item in pubhealth_dataset.train}),
            sorted({str(item.claim_id) for item in pubhealth_dataset.test}),
        ),
    }
    for directory, name in (("climate_fever", "Climate-FEVER"), ("pubhealth", "PubHealth")):
        records_path = experiment_root / "reviewx_multidomain" / directory / "evaluation_records.json"
        summary_path = experiment_root / "reviewx_multidomain" / directory / "summary.json"
        payload = _read_json(records_path)
        source_summary = _read_json(summary_path)
        threshold = float(payload["decisionThresholds"]["within_domain_calibrated"])
        artifacts.append(BaseArtifact(
            dataset=name,
            source_run=str(records_path),
            records=[
                _normalize_record(item, method="within_domain_calibrated", threshold=threshold)
                for item in payload["records"]
            ],
            threshold=threshold,
            split_audit={
                "selectionSplit": source_summary["validationThresholdSelection"]["selectionSplit"],
                "evaluationSplit": "test",
                "testLabelsUsedForSelection": source_summary["validationThresholdSelection"][
                    "testLabelsUsedForSelection"
                ],
                "groupIntersections": source_summary["audit"]["groupIntersections"],
            },
            fit_group_ids=domain_groups[name][0],
            evaluation_group_ids=domain_groups[name][1],
        ))
    return artifacts


def _metrics(records: Sequence[dict[str, Any]], threshold: float) -> dict[str, float]:
    labels = np.asarray([item["label"] for item in records], dtype=float)
    probabilities = np.asarray([item["probability"] for item in records], dtype=float)
    values = extended_metrics(labels, probabilities, threshold=threshold)
    return {name: float(values[name]) for name in REQUIRED_METRICS}


def clean_package(base: BaseArtifact, *, case_id: str, replica: int) -> dict[str, Any]:
    records = copy.deepcopy(base.records)
    metrics = _metrics(records, base.threshold)
    return {
        "schemaVersion": "reviewx-reliability-case/v1",
        "caseId": case_id,
        "dataset": base.dataset,
        "replica": replica,
        "sourceRun": base.source_run,
        "sampleCount": len(records),
        "threshold": base.threshold,
        "selectionAudit": copy.deepcopy(base.split_audit),
        "groupMembership": {
            "fit": list(base.fit_group_ids),
            "evaluation": list(base.evaluation_group_ids),
        },
        "reportedMetrics": metrics,
        "records": records,
        "declaredRecordHash": _canonical_hash(records),
        "narrative": (
            "The result is limited to the named dataset and frozen evaluation split. "
            "All required discrimination, balance, ranking, and calibration metrics are reported."
        ),
    }


def inject_fault(package: dict[str, Any], fault_type: str, replica: int) -> dict[str, Any]:
    if fault_type not in FAULT_TYPES:
        raise ValueError(f"Unknown fault type: {fault_type}")
    injected = copy.deepcopy(package)
    injected["injectionMetadata"] = {
        "faultType": fault_type,
        "expectedIssueCode": FAULT_CODES[fault_type],
        "replica": replica,
    }
    if fault_type == "reported_metric_fabrication":
        delta = 0.06 + (0.01 * replica)
        for name in ("F1-Score", "Macro F1"):
            injected["reportedMetrics"][name] = min(
                0.999, injected["reportedMetrics"][name] + delta,
            )
        injected["narrative"] = "The reported F1 gains establish a successful experiment."
    elif fault_type == "holdout_tuning":
        injected["selectionAudit"]["selectionSplit"] = "test"
        injected["selectionAudit"]["testLabelsUsedForSelection"] = True
        injected["narrative"] = "The decision threshold was optimized on the final test labels."
    elif fault_type == "claim_group_leakage":
        leaked_groups = injected["groupMembership"]["evaluation"][:replica + 1]
        injected["groupMembership"]["fit"].extend(leaked_groups)
        injected["narrative"] = "Some claim groups occur in both model fitting and final evaluation."
    elif fault_type == "selective_metric_reporting":
        injected["reportedMetrics"] = {
            "F1-Score": injected["reportedMetrics"]["F1-Score"],
        }
        injected["narrative"] = "F1 alone is sufficient to declare the experiment successful."
    elif fault_type == "artifact_hash_tampering":
        injected["declaredRecordHash"] = _canonical_hash({
            "caseId": injected["caseId"], "replica": replica, "artifact": "different-records",
        })
        injected["narrative"] = "The aggregate report is ready for acceptance."
    return injected


def build_cases(artifacts: Sequence[BaseArtifact], *, replicas: int) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for base in artifacts:
        slug = base.dataset.lower().replace("-", "_")
        for fault_type in FAULT_TYPES:
            for replica in range(replicas):
                pair_id = f"{slug}-{fault_type}-r{replica + 1}"
                clean = clean_package(
                    base, case_id=_blind_case_id(pair_id, "control"), replica=replica,
                )
                faulty_base = clean_package(
                    base, case_id=_blind_case_id(pair_id, "injected"), replica=replica,
                )
                cases.extend([
                    {
                        "caseId": clean["caseId"],
                        "pairId": pair_id,
                        "dataset": base.dataset,
                        "faultType": "none",
                        "isFaulty": False,
                        "expectedIssueCode": None,
                        "package": clean,
                    },
                    {
                        "caseId": faulty_base["caseId"],
                        "pairId": pair_id,
                        "dataset": base.dataset,
                        "faultType": fault_type,
                        "isFaulty": True,
                        "expectedIssueCode": FAULT_CODES[fault_type],
                        "package": inject_fault(faulty_base, fault_type, replica),
                    },
                ])
    return cases


def rules_only_audit(package: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    selection = package.get("selectionAudit") or {}
    if selection.get("selectionSplit") in {"test", "official_dev", "final_holdout"} or selection.get(
        "testLabelsUsedForSelection"
    ) is True:
        issues.append("TEST_LABEL_SELECTION_LEAKAGE")
    membership = package.get("groupMembership") or {}
    fit_groups = set(membership.get("fit") or [])
    evaluation_groups = set(membership.get("evaluation") or [])
    declared_intersections = selection.get("groupIntersections") or {}
    if (fit_groups & evaluation_groups) or any(int(value) > 0 for value in declared_intersections.values()):
        issues.append("CLAIM_GROUP_SPLIT_LEAKAGE")
    missing = set(REQUIRED_METRICS) - set(package.get("reportedMetrics") or {})
    if missing:
        issues.append("REQUIRED_METRICS_MISSING")
    if package.get("declaredRecordHash") != _canonical_hash(package.get("records") or []):
        issues.append("EVALUATION_RECORD_HASH_MISMATCH")
    return issues


def faros_audit(package: dict[str, Any], *, tolerance: float = 1e-9) -> dict[str, Any]:
    issues = rules_only_audit(package)
    recomputed = _metrics(package.get("records") or [], float(package.get("threshold", 0.5)))
    reported = package.get("reportedMetrics") or {}
    mismatches = {
        name: {"reported": reported.get(name), "recomputed": value}
        for name, value in recomputed.items()
        if name in reported and (
            not isinstance(reported[name], (int, float))
            or not math.isfinite(float(reported[name]))
            or abs(float(reported[name]) - value) > tolerance
        )
    }
    if mismatches:
        issues.append("REPORTED_METRIC_MISMATCH")
    return {
        "decision": "reject" if issues else "accept",
        "issues": sorted(set(issues)),
        "recomputedMetrics": recomputed,
        "metricMismatches": mismatches,
        "recordHash": _canonical_hash(package.get("records") or []),
    }


def repair_package(
    package: dict[str, Any],
    issues: Iterable[str],
    *,
    clean_reference: dict[str, Any] | None = None,
) -> dict[str, Any]:
    repaired = copy.deepcopy(package)
    issue_set = set(issues)
    requires_reexecution = bool(
        issue_set & {"TEST_LABEL_SELECTION_LEAKAGE", "CLAIM_GROUP_SPLIT_LEAKAGE"}
    )
    if requires_reexecution:
        if clean_reference is None:
            raise ValueError("Leakage repair requires a clean paired re-execution reference")
        original_case_id = repaired["caseId"]
        repaired = copy.deepcopy(clean_reference)
        repaired["caseId"] = original_case_id
    if "REQUIRED_METRICS_MISSING" in issue_set or "REPORTED_METRIC_MISMATCH" in issue_set:
        repaired["reportedMetrics"] = _metrics(
            repaired["records"], float(repaired.get("threshold", 0.5)),
        )
    if "EVALUATION_RECORD_HASH_MISMATCH" in issue_set:
        repaired["declaredRecordHash"] = _canonical_hash(repaired["records"])
    repaired["narrative"] = (
        "The corrected result is limited to the named dataset and frozen evaluation split; "
        "all required metrics are independently recomputed."
    )
    repaired["repairTrace"] = {
        "addressedIssues": sorted(issue_set),
        "mode": "paired_clean_reexecution" if requires_reexecution else "artifact_recompute",
        "metadataOnlyRepairForbidden": requires_reexecution,
    }
    return repaired


def _qwen_case_view(case: dict[str, Any]) -> dict[str, Any]:
    package = case["package"]
    return {
        "caseId": case["caseId"],
        "dataset": package["dataset"],
        "sampleCount": package["sampleCount"],
        "threshold": package["threshold"],
        "selectionAudit": package["selectionAudit"],
        "groupCounts": {
            key: len(values) for key, values in package["groupMembership"].items()
        },
        "reportedMetrics": package["reportedMetrics"],
        "declaredRecordHash": package["declaredRecordHash"],
        "narrative": package["narrative"],
    }


def _parse_qwen_predictions(text: str, expected_ids: set[str]) -> dict[str, dict[str, Any]]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    payload = json.loads(cleaned)
    rows = payload.get("assessments") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Qwen response must contain an assessments array")
    parsed: dict[str, dict[str, Any]] = {}
    for row in rows:
        case_id = str(row.get("caseId", ""))
        decision = str(row.get("decision", "")).lower()
        if case_id in expected_ids and decision in {"accept", "reject"}:
            parsed[case_id] = {
                "decision": decision,
                "issues": [str(item) for item in row.get("issues") or []],
                "rationale": str(row.get("rationale", ""))[:500],
            }
    missing = expected_ids - set(parsed)
    if missing:
        raise ValueError(f"Qwen response omitted case ids: {sorted(missing)}")
    return parsed


def qwen_only_audits(
    cases: Sequence[dict[str, Any]],
    *,
    model: str | None,
    batch_size: int,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    client = get_provider_client("qwen")
    predictions: dict[str, dict[str, Any]] = {}
    calls: list[dict[str, Any]] = []
    for offset in range(0, len(cases), batch_size):
        batch = cases[offset:offset + batch_size]
        visible = [_qwen_case_view(case) for case in batch]
        base_prompt = (
            "Act as a scientific experiment reviewer. For each case, decide accept or reject. "
            "Reject test-label tuning, split leakage, missing balance/calibration/ranking metrics, "
            "mismatched evidence hashes, fabricated aggregate metrics, or unsupported conclusions. "
            "You cannot access hidden labels beyond the supplied package and must not invent checks. "
            "Use only these canonical issue codes when applicable: REPORTED_METRIC_MISMATCH, "
            "TEST_LABEL_SELECTION_LEAKAGE, CLAIM_GROUP_SPLIT_LEAKAGE, REQUIRED_METRICS_MISSING, "
            "EVALUATION_RECORD_HASH_MISMATCH. "
            "Return JSON only as {\"assessments\":[{\"caseId\":...,\"decision\":\"accept|reject\","
            "\"issues\":[...],\"rationale\":...}]} using each caseId exactly once. "
            "Keep every rationale to at most 12 English words so the complete batch fits.\n\n"
            + json.dumps(visible, ensure_ascii=False, separators=(",", ":"))
        )
        expected_ids = {case["caseId"] for case in batch}
        parsed = None
        for attempt in range(1, 3):
            prompt = base_prompt + (
                "\nPrevious output was incomplete. Return shorter rationales and include every case."
                if attempt > 1 else ""
            )
            response = client.chat(
                [
                    ChatMessage(role="system", content="Return a complete, valid JSON object only."),
                    ChatMessage(role="user", content=prompt),
                ],
                model=model,
                temperature=0,
                max_tokens=max(2400, 180 * len(batch)),
                structured_output=True,
            )
            parse_error = None
            try:
                parsed = _parse_qwen_predictions(response.text, expected_ids)
            except (json.JSONDecodeError, ValueError) as exc:
                parse_error = f"{type(exc).__name__}: {exc}"[:500]
            calls.append({
                "batchIndex": (offset // batch_size) + 1,
                "attempt": attempt,
                "caseIds": sorted(expected_ids),
                "provider": response.raw_provider,
                "model": response.model,
                "usage": response.usage,
                "latencyMs": response.latency_ms,
                "finishReason": response.finish_reason,
                "promptHash": _canonical_hash(prompt),
                "responseHash": _canonical_hash(response.text),
                "parseStatus": "passed" if parse_error is None else "failed",
                "parseError": parse_error,
            })
            if parsed is not None:
                break
        if parsed is None:
            raise ValueError(f"Qwen failed to return a complete batch for ids: {sorted(expected_ids)}")
        predictions.update(parsed)
    return predictions, {
        "provider": calls[0]["provider"] if calls else "qwen",
        "model": calls[0]["model"] if calls else model,
        "calls": calls,
        "totalUsage": {
            key: sum(int(call["usage"].get(key, 0)) for call in calls)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        },
        "totalLatencyMs": sum(call["latencyMs"] for call in calls),
    }


def _wilson(successes: int, total: int) -> list[float]:
    if total == 0:
        return [0.0, 0.0]
    z = 1.959963984540054
    p = successes / total
    denominator = 1 + (z * z / total)
    center = (p + (z * z / (2 * total))) / denominator
    margin = z * math.sqrt((p * (1 - p) / total) + (z * z / (4 * total * total))) / denominator
    return [max(0.0, center - margin), min(1.0, center + margin)]


def score_predictions(
    cases: Sequence[dict[str, Any]], predictions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    faulty = [case for case in cases if case["isFaulty"]]
    clean = [case for case in cases if not case["isFaulty"]]
    true_positive = sum(predictions[case["caseId"]]["decision"] == "reject" for case in faulty)
    false_positive = sum(predictions[case["caseId"]]["decision"] == "reject" for case in clean)
    true_negative = len(clean) - false_positive
    false_negative = len(faulty) - true_positive
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / len(faulty) if faulty else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    localization = sum(
        case["expectedIssueCode"] in predictions[case["caseId"]].get("issues", [])
        for case in faulty
    )
    by_fault = {}
    for fault_type in FAULT_TYPES:
        subset = [case for case in faulty if case["faultType"] == fault_type]
        detected = sum(predictions[case["caseId"]]["decision"] == "reject" for case in subset)
        by_fault[fault_type] = {
            "cases": len(subset),
            "detected": detected,
            "detectionRate": detected / len(subset) if subset else 0.0,
        }
    return {
        "faultyCases": len(faulty),
        "cleanCases": len(clean),
        "confusionMatrix": {"tp": true_positive, "fp": false_positive, "tn": true_negative, "fn": false_negative},
        "faultDetectionRate": recall,
        "faultDetectionRateWilson95": _wilson(true_positive, len(faulty)),
        "normalFalseRejectRate": false_positive / len(clean) if clean else 0.0,
        "normalFalseRejectRateWilson95": _wilson(false_positive, len(clean)),
        "precision": precision,
        "f1": f1,
        "issueLocalizationRate": localization / len(faulty) if faulty else 0.0,
        "byFaultType": by_fault,
    }


def _deterministic_predictions(
    cases: Sequence[dict[str, Any]], *, full: bool,
) -> dict[str, dict[str, Any]]:
    predictions = {}
    for case in cases:
        audit = faros_audit(case["package"]) if full else {
            "issues": rules_only_audit(case["package"]),
        }
        issues = audit["issues"]
        predictions[case["caseId"]] = {
            "decision": "reject" if issues else "accept",
            "issues": issues,
            "rationale": "Deterministic FAROS evidence audit" if full else "Static structure rules",
        }
    return predictions


def _report(summary: dict[str, Any]) -> str:
    lines = [
        "# ReviewX科研可靠性陷阱基准",
        "",
        f"> 运行编号：`{summary['runId']}`  ",
        f"> 完整性质量门：**{summary['qualityGate']['status']}**  ",
        f"> 案例：{summary['caseAudit']['total']}个，其中故障{summary['caseAudit']['faulty']}个、配对正常对照{summary['caseAudit']['clean']}个",
        "",
        "## 三组方法结果",
        "",
        "| 方法 | 故障检出率 | 95% CI | 正常误拒率 | Precision | F1 | 问题定位率 |",
        "| --- | ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    names = {"qwen_only": "Qwen-only", "rules_only": "结构规则", "faros_full": "完整FAROS+ReviewX"}
    for method, score in summary["scores"].items():
        detection_ci = score["faultDetectionRateWilson95"]
        lines.append(
            f"| {names[method]} | {score['faultDetectionRate']:.3f} | "
            f"[{detection_ci[0]:.3f}, {detection_ci[1]:.3f}] | "
            f"{score['normalFalseRejectRate']:.3f} | {score['precision']:.3f} | "
            f"{score['f1']:.3f} | {score['issueLocalizationRate']:.3f} |"
        )
    lines.extend([
        "",
        "## 分故障类型检出率",
        "",
        "| 故障类型 | Qwen-only | 结构规则 | 完整FAROS |",
        "| --- | ---: | ---: | ---: |",
    ])
    for fault_type in FAULT_TYPES:
        lines.append(
            f"| {fault_type} | "
            f"{summary['scores']['qwen_only']['byFaultType'][fault_type]['detectionRate']:.3f} | "
            f"{summary['scores']['rules_only']['byFaultType'][fault_type]['detectionRate']:.3f} | "
            f"{summary['scores']['faros_full']['byFaultType'][fault_type]['detectionRate']:.3f} |"
        )
    lines.extend([
        "",
        "## Qwen-only漏检案例",
        "",
        "| 数据集 | 故障类型 | Qwen判断理由 |",
        "| --- | --- | --- |",
    ])
    for item in summary["qwenMisses"]:
        rationale = str(item["rationale"]).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {item['dataset']} | {item['faultType']} | {rationale} |")
    repair = summary["repairEvaluation"]
    lines.extend([
        "",
        "## 自动修复与边界",
        "",
        f"完整FAROS拒绝后生成修复动作并重新审计：{repair['passed']}/{repair['attempted']}通过，修复回放成功率{repair['successRate']:.3f}。",
        "指标、报告或哈希问题由证据复算修复；测试集调参与分组泄漏必须使用配对干净协议重新执行，禁止只修改元数据。",
        "本基准采用真实逐样本预测，但故障为受控注入，因此评价的是科研产物审计能力，不等同于真实同行评审有用性。",
        "Qwen-only只看到报告级摘要；完整FAROS可访问逐样本记录并复算，这正是系统工具增强与证据合同的能力差异。",
        "Wilson区间仅描述本轮受控变体的不确定性；同一数据集内的注入变体并非完全独立科学实验，不能据此宣称总体显著性。",
        "故障检出率不能表述为模型准确率、论文质量或跨领域科学发现能力。",
        "",
    ])
    return "\n".join(lines)


def run_benchmark(
    experiment_root: Path,
    output_dir: Path,
    *,
    replicas: int,
    call_qwen: bool,
    model: str | None,
    qwen_batch_size: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    run_id = "reviewx_reliability_" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    artifacts = load_real_artifacts(experiment_root)
    protocol_base = {
        "schemaVersion": SCHEMA_VERSION,
        "runId": run_id,
        "createdAt": _now(),
        "seed": BENCHMARK_SEED,
        "datasets": [item.dataset for item in artifacts],
        "sourceRuns": {item.dataset: item.source_run for item in artifacts},
        "faultTypes": list(FAULT_TYPES),
        "replicasPerDatasetAndFault": replicas,
        "pairedCleanControls": True,
        "methods": ["qwen_only", "rules_only", "faros_full"],
        "primaryMetrics": ["faultDetectionRate", "normalFalseRejectRate", "repairSuccessRate"],
        "qwenReceivesRawRecords": False,
        "farosRecomputesFromRawRecords": True,
    }
    protocol = {**protocol_base, "contentHash": _canonical_hash(protocol_base)}
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "preregistered_protocol.json", protocol)

    cases = build_cases(artifacts, replicas=replicas)
    rules_predictions = _deterministic_predictions(cases, full=False)
    faros_predictions = _deterministic_predictions(cases, full=True)
    if call_qwen:
        # Paired controls and injected variants are evaluated in separate calls so
        # the language model cannot infer labels by comparing near-duplicate cases.
        qwen_cases = sorted(
            cases,
            key=lambda case: (
                bool(case["isFaulty"]),
                hashlib.sha256(case["caseId"].encode("utf-8")).hexdigest(),
            ),
        )
        qwen_predictions, qwen_trace = qwen_only_audits(
            qwen_cases, model=model, batch_size=qwen_batch_size,
        )
    else:
        qwen_predictions = {
            case["caseId"]: {"decision": "accept", "issues": [], "rationale": "Qwen call skipped"}
            for case in cases
        }
        qwen_trace = {"skipped": True, "reason": "--qwen was not supplied"}

    method_predictions = {
        "qwen_only": qwen_predictions,
        "rules_only": rules_predictions,
        "faros_full": faros_predictions,
    }
    qwen_misses = [
        {
            "caseId": case["caseId"],
            "dataset": case["dataset"],
            "faultType": case["faultType"],
            "rationale": qwen_predictions[case["caseId"]].get("rationale", ""),
            "issues": qwen_predictions[case["caseId"]].get("issues", []),
        }
        for case in cases
        if case["isFaulty"] and qwen_predictions[case["caseId"]]["decision"] != "reject"
    ]
    repair_attempts = []
    clean_by_pair = {
        case["pairId"]: case for case in cases if not case["isFaulty"]
    }
    for case in cases:
        if not case["isFaulty"]:
            continue
        audit = faros_audit(case["package"])
        repaired = repair_package(
            case["package"],
            audit["issues"],
            clean_reference=clean_by_pair[case["pairId"]]["package"],
        )
        repaired_audit = faros_audit(repaired)
        repair_attempts.append({
            "caseId": case["caseId"],
            "issues": audit["issues"],
            "repairDecision": repaired_audit["decision"],
            "remainingIssues": repaired_audit["issues"],
            "repairMode": repaired["repairTrace"]["mode"],
        })
    repair_passed = sum(item["repairDecision"] == "accept" for item in repair_attempts)
    quality_checks = {
        "threeDatasetsLoaded": len(artifacts) == 3,
        "allDatasetsHaveRecords": all(item.records for item in artifacts),
        "balancedFaultAndControlCases": (
            sum(case["isFaulty"] for case in cases) == sum(not case["isFaulty"] for case in cases)
        ),
        "allFaultTypesCoveredPerDataset": all(
            {case["faultType"] for case in cases if case["dataset"] == item.dataset and case["isFaulty"]}
            == set(FAULT_TYPES)
            for item in artifacts
        ),
        "caseIdsUnique": len(cases) == len({case["caseId"] for case in cases}),
        "pairedControlsPresent": all(
            len([case for case in cases if case["pairId"] == pair_id]) == 2
            for pair_id in {case["pairId"] for case in cases}
        ),
        "allMethodsReturnedEveryCase": all(
            set(predictions) == {case["caseId"] for case in cases}
            for predictions in method_predictions.values()
        ),
        "sourceGroupMembershipDisjoint": all(
            not (set(item.fit_group_ids) & set(item.evaluation_group_ids))
            for item in artifacts
        ),
        "evaluationRecordsMatchDeclaredGroups": all(
            {str(record["claimId"]) for record in item.records}
            <= set(item.evaluation_group_ids)
            for item in artifacts
        ),
        "qwenCaseIdsAreOpaque": all(
            case["caseId"].startswith("sr-")
            and case["faultType"] not in case["caseId"]
            and "clean" not in case["caseId"]
            and "fault" not in case["caseId"]
            for case in cases
        ),
    }
    summary = {
        **protocol,
        "caseAudit": {
            "total": len(cases),
            "faulty": sum(case["isFaulty"] for case in cases),
            "clean": sum(not case["isFaulty"] for case in cases),
            "byDataset": {
                item.dataset: sum(case["dataset"] == item.dataset for case in cases)
                for item in artifacts
            },
        },
        "scores": {
            method: score_predictions(cases, predictions)
            for method, predictions in method_predictions.items()
        },
        "repairEvaluation": {
            "attempted": len(repair_attempts),
            "passed": repair_passed,
            "successRate": repair_passed / len(repair_attempts) if repair_attempts else 0.0,
        },
        "qwenMisses": qwen_misses,
        "qwenTrace": qwen_trace,
        "qualityGate": {
            "status": "passed" if all(quality_checks.values()) else "failed",
            "checks": quality_checks,
        },
        "durationSeconds": time.perf_counter() - started,
    }
    public_cases = [{key: case[key] for key in (
        "caseId", "pairId", "dataset", "faultType", "isFaulty", "expectedIssueCode",
    )} for case in cases]
    evaluation = {
        "schemaVersion": SCHEMA_VERSION,
        "cases": public_cases,
        "predictions": method_predictions,
        "repairAttempts": repair_attempts,
    }
    _write_json(output_dir / "evaluation_records.json", evaluation)
    _write_json(output_dir / "qwen_trace.json", qwen_trace)
    _write_json(output_dir / "summary.json", summary)
    (output_dir / "experiment_report.md").write_text(_report(summary), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-root", type=Path, default=Path("data/experiments"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/experiments/reviewx_reliability"))
    parser.add_argument("--replicas", type=int, default=3)
    parser.add_argument("--qwen", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("--qwen-batch-size", type=int, default=15)
    args = parser.parse_args()
    if args.replicas < 1:
        parser.error("--replicas must be at least 1")
    summary = run_benchmark(
        args.experiment_root,
        args.output_dir,
        replicas=args.replicas,
        call_qwen=args.qwen,
        model=args.model,
        qwen_batch_size=args.qwen_batch_size,
    )
    print(json.dumps({
        "runId": summary["runId"],
        "qualityGate": summary["qualityGate"],
        "scores": summary["scores"],
        "repairEvaluation": summary["repairEvaluation"],
        "outputDir": str(args.output_dir),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
