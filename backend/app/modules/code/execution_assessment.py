"""Deterministic scientific execution assessment for Code handoff.

The assessor is intentionally conservative. It decides whether FAROS may run
code; it does not infer that missing data, instruments, approvals, or proofs
can be replaced by generated software.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from app.contracts import ExecutionAssessment, ExecutionClass, ExecutionStatus


_ETHICS_TERMS = {
    "human subject", "human participant", "patient", "clinical trial", "medical record",
    "animal experiment", "animal study", "informed consent", "irb", "ethics approval",
    "人体", "患者", "临床试验", "动物实验", "伦理审批", "知情同意",
}
_INSTRUMENT_TERMS = {
    "laboratory experiment", "wet lab", "microscope", "spectrometer", "telescope",
    "sensor deployment", "field experiment", "chemical synthesis", "cell culture",
    "实验室实验", "湿实验", "显微镜", "光谱仪", "望远镜", "传感器部署", "化学合成",
}
_PROOF_TERMS = {
    "formal proof", "mathematical proof", "prove theorem", "proof assistant",
    "theorem proving", "严格证明", "数学证明", "证明定理", "形式化证明",
}
_SIMULATION_TERMS = {
    "simulation", "simulate", "synthetic data", "monte carlo", "agent-based model",
    "numerical model", "仿真", "模拟", "合成数据", "蒙特卡洛", "数值模型",
}
_DATA_TERMS = {
    "dataset", "corpus", "benchmark", "annotated", "annotation", "records", "samples",
    "train/test", "training data", "survey data", "数据集", "语料库", "基准", "标注",
    "样本", "训练数据", "调查数据",
}


def _flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return " ".join(_flatten_text(item) for item in value.values())
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        return " ".join(_flatten_text(item) for item in value)
    return str(value)


def _contains(text: str, terms: set[str]) -> bool:
    return any(term in text for term in terms)


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, Mapping):
        return [str(key) for key, item in value.items() if item not in (None, "", [], {})]
    if isinstance(value, Iterable):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _experiment_specs(candidate: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = candidate.get("experimentSpecs") or candidate.get("requiredExperiments") or []
    return [item for item in raw if isinstance(item, Mapping)]


def _validation_metrics(candidate: Mapping[str, Any], inputs: Mapping[str, Any]) -> list[str]:
    metrics: list[str] = []
    for spec in _experiment_specs(candidate):
        metrics.extend(_string_list(spec.get("metrics")))
    metrics.extend(_string_list(candidate.get("expectedMetrics")))
    metrics.extend(_string_list(inputs.get("validationMetrics")))
    return list(dict.fromkeys(item.strip() for item in metrics if item.strip()))[:20]


def _required_datasets(candidate: Mapping[str, Any], inputs: Mapping[str, Any]) -> list[str]:
    datasets: list[str] = []
    for spec in _experiment_specs(candidate):
        datasets.extend(_string_list(spec.get("datasets")))
    datasets.extend(_string_list(inputs.get("requiredDatasets")))
    return list(dict.fromkeys(item.strip() for item in datasets if item.strip()))[:20]


def _dataset_is_available(dataset: str, available_inputs: list[str]) -> bool:
    normalized = dataset.lower().strip()
    if any(term in normalized for term in ("synthetic", "generated", "built-in", "toy", "合成", "生成")):
        return True
    dataset_terms = set(re.findall(r"[a-z0-9_\-]+", normalized))
    for available in available_inputs:
        available_terms = set(re.findall(r"[a-z0-9_\-]+", available.lower()))
        if normalized in available.lower() or (dataset_terms and len(dataset_terms & available_terms) >= min(2, len(dataset_terms))):
            return True
    return False


def assess_execution(
    *,
    run_id: str,
    question_id: str,
    research_question: str,
    candidate: Mapping[str, Any] | None = None,
    inputs: Mapping[str, Any] | None = None,
    plan_package_id: str | None = None,
) -> ExecutionAssessment:
    """Classify a research task before any code or sandbox is started."""

    candidate = candidate or {}
    inputs = inputs or {}
    text = _flatten_text({"question": research_question, "candidate": candidate, "inputs": inputs}).lower()
    available_inputs = _string_list(inputs.get("availableInputs"))
    required_datasets = _required_datasets(candidate, inputs)
    missing_datasets = [item for item in required_datasets if not _dataset_is_available(item, available_inputs)]
    metrics = _validation_metrics(candidate, inputs)

    execution_class = ExecutionClass.COMPUTATIONAL_READY
    missing_inputs: list[str] = []
    warnings: list[str] = []
    rationale = "The task can be evaluated with software using the declared inputs and metrics."
    feasibility = 0.82
    status = ExecutionStatus.READY

    if _contains(text, _ETHICS_TERMS):
        execution_class = ExecutionClass.ETHICS_REVIEW_REQUIRED
        missing_inputs = ["documented ethics approval and an authorized data-access protocol"]
        rationale = "The task involves human, clinical, or animal data and cannot be auto-executed without approval."
        feasibility = 0.2
        status = ExecutionStatus.NOT_APPLICABLE
    elif _contains(text, _INSTRUMENT_TERMS):
        execution_class = ExecutionClass.INSTRUMENT_REQUIRED
        missing_inputs = ["instrument access, calibration records, and a physical experiment protocol"]
        rationale = "The proposed validation requires physical instruments or laboratory execution."
        feasibility = 0.25
        status = ExecutionStatus.NOT_APPLICABLE
    elif _contains(text, _PROOF_TERMS):
        execution_class = ExecutionClass.PROOF_REQUIRED
        missing_inputs = ["a formal proof obligation and proof-checking environment"]
        rationale = "Numerical tests may probe the claim but cannot replace the requested mathematical proof."
        feasibility = 0.35
        status = ExecutionStatus.NOT_APPLICABLE
    elif missing_datasets:
        execution_class = ExecutionClass.DATA_REQUIRED
        missing_inputs = [f"dataset: {item}" for item in missing_datasets]
        rationale = "The plan names datasets or annotations that are not present in the declared available inputs."
        feasibility = 0.4
        status = ExecutionStatus.NOT_APPLICABLE
    elif _contains(text, _SIMULATION_TERMS):
        execution_class = ExecutionClass.SIMULATION_READY
        rationale = "The task declares a software simulation or synthetic-data validation path."
        feasibility = 0.86
    elif _contains(text, _DATA_TERMS) and not available_inputs and not required_datasets:
        execution_class = ExecutionClass.DATA_REQUIRED
        missing_inputs = ["a resolvable dataset path, URL, or artifact reference"]
        rationale = "The task depends on empirical data but does not declare an available dataset."
        feasibility = 0.42
        status = ExecutionStatus.NOT_APPLICABLE

    if status == ExecutionStatus.READY and not metrics:
        execution_class = ExecutionClass.PROTOCOL_ONLY
        missing_inputs.append("at least one named validation metric with a definition")
        rationale = "Code could be generated, but the plan lacks a metric that can validate the hypothesis."
        feasibility = min(feasibility, 0.5)
        status = ExecutionStatus.NOT_APPLICABLE

    if not available_inputs:
        warnings.append("No explicit availableInputs were declared; only self-contained computation is allowed.")

    return ExecutionAssessment(
        runId=run_id,
        questionId=question_id,
        planPackageId=plan_package_id,
        executionClass=execution_class,
        feasibilityScore=feasibility,
        rationale=rationale,
        availableInputs=available_inputs,
        missingInputs=missing_inputs,
        toolsAndEnvironment=["Python", "FAROS sandbox", "Qwen code generation"],
        validationMetrics=metrics,
        stopConditions=["non-zero process exit", "execution timeout", "missing scientific metrics"],
        safetyConstraints=["No physical, clinical, or ethics-sensitive execution without approval"],
        estimatedRuntimeSeconds=inputs.get("estimatedRuntimeSeconds"),
        estimatedCost=inputs.get("estimatedCost"),
        status=status,
        warnings=warnings,
    )

