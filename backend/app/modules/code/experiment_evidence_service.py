"""Build reproducible ExperimentEvidence from files that actually exist.

The service never trusts a stored ``executed`` flag.  It rechecks code,
environment, data/config, metrics, logs, terminal state, and declared artifact
paths each time evidence is produced, so deleting an artifact downgrades the
result to ``failed``.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from pydantic import Field, model_validator

from app.modules.code.execution_assessment import (
    ArtifactKind,
    ArtifactRef,
    ContractModel,
    ExecutionAssessment,
    ExecutionStatus,
    TargetModule,
    execution_gate,
)


class MetricEvidence(ContractModel):
    name: str = Field(min_length=1)
    value: Any
    unit: str = ""
    definition: str = ""
    split: str = ""
    sourcePath: str = ""

    @model_validator(mode="after")
    def validate_scientific_metric(self) -> "MetricEvidence":
        if (
            isinstance(self.value, bool)
            or not isinstance(self.value, (int, float))
            or not math.isfinite(float(self.value))
        ):
            raise ValueError("metric value must be a finite number")
        if not self.definition.strip():
            raise ValueError("metric definition is required")
        if not self.sourcePath.strip():
            raise ValueError("metric sourcePath is required")
        return self


class ExperimentEvidence(ContractModel):
    schemaVersion: str = "scientific-research/v1"
    runId: str = Field(min_length=1)
    questionId: str = Field(min_length=1)
    codeRunId: str = Field(min_length=1)
    status: ExecutionStatus
    dataHashes: Dict[str, str] = Field(default_factory=dict)
    environmentHash: str = ""
    codeHash: str = ""
    method: str = ""
    baseline: str = ""
    metrics: List[MetricEvidence] = Field(default_factory=list)
    logRefs: List[str] = Field(default_factory=list)
    artifactRefs: List[ArtifactRef] = Field(default_factory=list)
    supportedClaims: List[str] = Field(default_factory=list)
    unsupportedClaims: List[str] = Field(default_factory=list)
    failures: List[str] = Field(default_factory=list)
    durationSeconds: Optional[float] = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def validate_executed_evidence(self) -> "ExperimentEvidence":
        if self.status == ExecutionStatus.EXECUTED:
            missing = []
            if not self.codeHash:
                missing.append("codeHash")
            if not self.environmentHash:
                missing.append("environmentHash")
            if not self.artifactRefs:
                missing.append("artifactRefs")
            if not self.method.strip():
                missing.append("method")
            if not self.baseline.strip():
                missing.append("baseline")
            if not self.metrics:
                missing.append("metrics")
            if missing:
                raise ValueError(f"executed evidence requires: {', '.join(missing)}")
            if self.failures:
                raise ValueError("executed evidence cannot contain failures")
        return self


class PlanAdjustment(ContractModel):
    fieldPath: str
    action: str
    reason: str
    observedValue: Any = None
    targetValue: Any = None


class ExperimentFeedback(ContractModel):
    runId: str
    codeRunId: str
    status: ExecutionStatus
    observations: List[str] = Field(default_factory=list)
    anomalies: List[str] = Field(default_factory=list)
    failures: List[str] = Field(default_factory=list)
    planAdjustments: List[PlanAdjustment] = Field(default_factory=list)
    recommendedNextSteps: List[str] = Field(default_factory=list)


_ENVIRONMENT_NAMES = {
    "requirements.txt", "requirements-dev.txt", "pyproject.toml", "poetry.lock",
    "pipfile", "pipfile.lock", "environment.yml", "environment.yaml",
    "dockerfile", "docker-compose.yml", "docker-compose.yaml", "package.json",
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "go.mod", "go.sum",
    "cargo.toml", "cargo.lock", "runtime.txt",
}
_CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".c",
    ".cc", ".cpp", ".h", ".hpp", ".r", ".jl", ".m", ".scala", ".sh",
}
_LOG_EXTENSIONS = {".log", ".out", ".err"}
_IGNORED_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv"}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def _files_under(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or any(part in _IGNORED_DIRS for part in path.parts):
            continue
        if path.name.endswith((".pyc", ".farosbak", ".bak")):
            continue
        files.append(path)
    return sorted(files)


def _composite_hash(files: Iterable[Path], base: Path) -> str:
    items = list(files)
    if not items:
        return ""
    digest = hashlib.sha256()
    for path in sorted(items):
        relative = path.relative_to(base).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256_file(path).encode("ascii"))
        digest.update(b"\n")
    return f"sha256:{digest.hexdigest()}"


def _artifact_kind(path: Path, cart_root: Path) -> ArtifactKind:
    relative = path.relative_to(cart_root)
    if "trace" in relative.parts or path.suffix.lower() in _LOG_EXTENSIONS or path.name == "event_log.json":
        return ArtifactKind.LOG
    if path.suffix.lower() in _CODE_EXTENSIONS and "project" in relative.parts:
        return ArtifactKind.CODE
    if "metrics" in path.name.lower():
        return ArtifactKind.METRIC
    if path.suffix.lower() in {".md", ".html", ".pdf"}:
        return ArtifactKind.REPORT
    if "data" in relative.parts:
        return ArtifactKind.DATASET
    return ArtifactKind.OTHER


def _artifact_ref(path: Path, cart_root: Path) -> ArtifactRef:
    relative = path.relative_to(cart_root).as_posix()
    digest = _sha256_file(path)
    return ArtifactRef(
        id=f"artifact_{hashlib.sha256(relative.encode('utf-8')).hexdigest()[:16]}",
        kind=_artifact_kind(path, cart_root),
        sourceModule=TargetModule.CODE,
        uri=relative,
        contentHash=digest,
        version="1",
        createdAt=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
        metadata={"sizeBytes": path.stat().st_size},
    )


def _metric_items(cart_root: Path) -> tuple[list[MetricEvidence], list[str]]:
    metrics: list[MetricEvidence] = []
    failures: list[str] = []
    seen: set[tuple[str, str]] = set()
    for result_path in sorted((cart_root / "data").glob("*/result.json")):
        try:
            result = _read_json(result_path)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(result, Mapping):
            continue
        node_id = str(result.get("node_id") or result_path.parent.name)
        outputs = result.get("outputs", {})
        if not isinstance(outputs, Mapping):
            continue
        values = outputs.get("metrics", {})
        if not isinstance(values, Mapping):
            continue
        for name, value in values.items():
            key = (node_id, str(name))
            if key in seen:
                continue
            seen.add(key)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                failures.append(
                    f"Metric {node_id}:{name} must be a finite numeric value, got {value!r}"
                )
                continue
            metrics.append(MetricEvidence(
                name=str(name),
                value=value,
                definition=f"Metric emitted by Cart node {node_id}.",
                split=node_id,
                sourcePath=result_path.relative_to(cart_root).as_posix(),
            ))
    return metrics, failures


def _declared_artifact_failures(cart_root: Path) -> list[str]:
    failures: list[str] = []
    for result_path in sorted((cart_root / "data").glob("*/result.json")):
        try:
            result = _read_json(result_path)
        except (OSError, json.JSONDecodeError) as exc:
            failures.append(f"Unreadable node result {result_path.name}: {exc}")
            continue
        if not isinstance(result, Mapping):
            failures.append(f"Node result {result_path.name} must be a JSON object")
            continue
        if not result.get("success", False):
            failures.append(f"Node {result.get('node_id', result_path.parent.name)} failed: {result.get('error') or result.get('message', '')}")
        for artifact in result.get("artifacts", []):
            if not isinstance(artifact, Mapping) or not artifact.get("path"):
                failures.append(f"Node {result.get('node_id', result_path.parent.name)} has an invalid artifact declaration")
                continue
            path = (cart_root / str(artifact["path"])).resolve()
            try:
                path.relative_to(cart_root)
            except ValueError:
                failures.append(f"Artifact escapes cart root: {artifact['path']}")
                continue
            if not path.is_file():
                failures.append(f"Declared artifact is missing: {artifact['path']}")
    return failures


def _terminal_failures(cart_root: Path) -> tuple[list[str], Optional[float]]:
    event_path = cart_root / "event_log.json"
    if not event_path.is_file():
        return ["Missing event_log.json; terminal execution state cannot be verified"], None
    try:
        events = _read_json(event_path)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"Unreadable event_log.json: {exc}"], None
    if not isinstance(events, list) or not events:
        return ["Empty event log; terminal execution state cannot be verified"], None
    terminal = events[-1]
    if not isinstance(terminal, Mapping):
        return ["Cart terminal event must be a JSON object"], None
    if terminal.get("event_type") != "cart_complete":
        return ["Cart has no terminal cart_complete event"], None
    if terminal.get("status") not in {"success", "succeeded"}:
        return [f"Cart terminal status is {terminal.get('status', 'unknown')}: {terminal.get('message', '')}"], None

    result_path = cart_root / "cart_results.json"
    if not result_path.is_file():
        return ["Missing cart_results.json"], None
    try:
        summary = _read_json(result_path)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"Unreadable cart_results.json: {exc}"], None
    if not isinstance(summary, Mapping):
        return ["cart_results.json must be a JSON object"], None
    if summary.get("failed", 0) or summary.get("skipped", 0):
        return [f"Cart is incomplete: failed={summary.get('failed', 0)}, skipped={summary.get('skipped', 0)}"], None
    duration_ms = summary.get("total_duration_ms")
    return [], float(duration_ms) / 1000 if isinstance(duration_ms, (int, float)) else None


def _method_and_baseline(cart_root: Path) -> tuple[str, str]:
    summary_path = cart_root / "cart_results.json"
    summary = _read_json(summary_path) if summary_path.is_file() else {}
    if not isinstance(summary, Mapping):
        summary = {}
    method = str(summary.get("proposed_method") or "")
    baselines: list[str] = []
    for result_path in sorted((cart_root / "data").glob("*/result.json")):
        try:
            result = _read_json(result_path)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(result, Mapping):
            continue
        node_info = result.get("node_info", {})
        node_method = node_info.get("method") if isinstance(node_info, Mapping) else None
        if node_method and not method:
            method = str(node_method)
        baseline = result.get("baseline")
        if baseline:
            baselines.append(str(baseline))
    return method, "; ".join(dict.fromkeys(baselines))


def build_experiment_evidence(
    cart_dir: str | Path,
    assessment: ExecutionAssessment | Mapping[str, Any],
    *,
    code_run_id: Optional[str] = None,
    supported_claims: Optional[list[str]] = None,
    unsupported_claims: Optional[list[str]] = None,
) -> ExperimentEvidence:
    """Derive evidence from a completed cart; never synthesize missing values."""

    value = assessment if isinstance(assessment, ExecutionAssessment) else ExecutionAssessment.model_validate(assessment)
    cart_root = Path(cart_dir).resolve()
    resolved_code_run_id = code_run_id or cart_root.name or "unknown_code_run"
    gate = execution_gate(value)
    if not gate.allowed:
        return ExperimentEvidence(
            runId=value.runId,
            questionId=value.questionId,
            codeRunId=resolved_code_run_id,
            status=ExecutionStatus.NOT_APPLICABLE,
            method="",
            failures=[f"Execution gate blocked the task as {value.executionClass.value}: {gate.reason}"],
            unsupportedClaims=list(dict.fromkeys([*(unsupported_claims or []), *(supported_claims or [])])),
        )
    if not cart_root.is_dir():
        return ExperimentEvidence(
            runId=value.runId,
            questionId=value.questionId,
            codeRunId=resolved_code_run_id,
            status=ExecutionStatus.FAILED,
            failures=[f"Cart artifact directory does not exist: {cart_root}"],
            unsupportedClaims=list(dict.fromkeys([*(unsupported_claims or []), *(supported_claims or [])])),
        )

    all_files = _files_under(cart_root)
    project_files = _files_under(cart_root / "project")
    code_files = [path for path in project_files if path.suffix.lower() in _CODE_EXTENSIONS]
    environment_files = [path for path in project_files if path.name.lower() in _ENVIRONMENT_NAMES]
    data_files = _files_under(cart_root / "data")
    config_files = [
        path for path in all_files
        if "config" in path.name.lower() or path.name in {"manifest.json", "blueprint_state.json"}
    ]
    log_files = [
        path for path in all_files
        if "trace" in path.relative_to(cart_root).parts
        or path.suffix.lower() in _LOG_EXTENSIONS
        or path.name == "event_log.json"
    ]
    metrics, metric_failures = _metric_items(cart_root)
    failures = _declared_artifact_failures(cart_root)
    failures.extend(metric_failures)
    terminal_failures, duration = _terminal_failures(cart_root)
    failures.extend(terminal_failures)

    prerequisite_groups = {
        "code": code_files,
        "environment descriptor": environment_files,
        "data/result": data_files,
        "config/manifest": config_files,
        "log/trace": log_files,
    }
    for label, files in prerequisite_groups.items():
        if not files:
            failures.append(f"Missing reproducibility prerequisite: {label}")
    if not metrics:
        failures.append("No structured metric was emitted by any node result")

    refs = [_artifact_ref(path, cart_root) for path in all_files]
    data_hashes = {
        path.relative_to(cart_root).as_posix(): _sha256_file(path)
        for path in sorted(set(data_files + config_files))
    }
    method, baseline = _method_and_baseline(cart_root)
    if not method.strip():
        failures.append("Missing documented experimental method")
    if not baseline.strip():
        failures.append("Missing documented baseline or control")
    status = ExecutionStatus.EXECUTED if not failures else ExecutionStatus.FAILED
    # Claims supplied by an API caller are candidates, not verified bindings.
    # Until a claim-to-metric verifier produces an explicit binding, keep them
    # unsupported even when the experiment itself executed successfully.
    verified_supported: list[str] = []
    verified_unsupported = [*(unsupported_claims or []), *(supported_claims or [])]

    return ExperimentEvidence(
        runId=value.runId,
        questionId=value.questionId,
        codeRunId=resolved_code_run_id,
        status=status,
        dataHashes=data_hashes,
        environmentHash=_composite_hash(environment_files, cart_root),
        codeHash=_composite_hash(code_files, cart_root),
        method=method,
        baseline=baseline,
        metrics=metrics,
        logRefs=[path.relative_to(cart_root).as_posix() for path in log_files],
        artifactRefs=refs,
        supportedClaims=list(dict.fromkeys(verified_supported)),
        unsupportedClaims=list(dict.fromkeys(verified_unsupported)),
        failures=list(dict.fromkeys(failures)),
        durationSeconds=duration,
    )


def _as_source_dict(source: Any) -> dict[str, Any]:
    if isinstance(source, Mapping):
        return dict(source)
    if hasattr(source, "model_dump"):
        return source.model_dump(mode="json")
    return {}


def _expected_targets(source: Any) -> list[tuple[str, str, str]]:
    data = _as_source_dict(source)
    targets = []
    for stage in data.get("stages", []):
        for step in stage.get("steps", []):
            for expected in step.get("expected", []):
                if isinstance(expected, Mapping):
                    targets.append((
                        str(step.get("id", "unknown")),
                        str(expected.get("metric", "")),
                        str(expected.get("target", "")),
                    ))
    research_plan = data.get("researchPlan") or {}
    if isinstance(research_plan, Mapping):
        for step in research_plan.get("steps", []):
            for metric in step.get("metrics", []):
                targets.append((str(step.get("id", "unknown")), str(metric), ""))
    return targets


def _target_satisfied(value: Any, target: str) -> Optional[bool]:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return None
    match = re.search(r"(<=|>=|<|>)\s*(-?\d+(?:\.\d+)?)", target)
    if not match:
        return None
    threshold = float(match.group(2))
    return {
        "<": value < threshold,
        ">": value > threshold,
        "<=": value <= threshold,
        ">=": value >= threshold,
    }[match.group(1)]


def build_experiment_feedback(evidence: ExperimentEvidence, plan_source: Any = None) -> ExperimentFeedback:
    observations = [
        f"{metric.split or 'run'}:{metric.name}={metric.value}{metric.unit}"
        for metric in evidence.metrics
    ]
    anomalies: list[str] = []
    adjustments: list[PlanAdjustment] = []
    metric_index = {metric.name.lower(): metric for metric in evidence.metrics}
    for step_id, name, target in _expected_targets(plan_source):
        metric = metric_index.get(name.lower())
        if not metric:
            anomalies.append(f"Expected metric '{name}' was not emitted")
            adjustments.append(PlanAdjustment(
                fieldPath=f"stages[].steps[{step_id}].expected",
                action="add_or_fix_metric_emission",
                reason=f"The experiment did not emit required metric '{name}'.",
                targetValue=target,
            ))
            continue
        satisfied = _target_satisfied(metric.value, target)
        if satisfied is False:
            anomalies.append(f"Metric '{name}'={metric.value} did not satisfy target {target}")
            adjustments.append(PlanAdjustment(
                fieldPath=f"stages[].steps[{step_id}]",
                action="revise_parameters_or_hypothesis",
                reason=f"Observed metric '{name}' missed its predeclared target.",
                observedValue=metric.value,
                targetValue=target,
            ))

    if evidence.failures:
        adjustments.append(PlanAdjustment(
            fieldPath="stages",
            action="repair_or_degrade_execution_plan",
            reason="Execution produced reproducibility or runtime failures.",
            observedValue=evidence.failures,
        ))

    next_steps = []
    if evidence.status == ExecutionStatus.EXECUTED and not anomalies:
        next_steps.append("Bind verified metrics and artifact hashes to the next Idea/Paper version.")
    if anomalies:
        next_steps.append("Create a child plan version that addresses unmet metrics before rerunning.")
    if evidence.failures:
        next_steps.append("Resolve listed failures and rerun with the same fixed inputs and configuration.")

    return ExperimentFeedback(
        runId=evidence.runId,
        codeRunId=evidence.codeRunId,
        status=evidence.status,
        observations=observations,
        anomalies=anomalies,
        failures=evidence.failures,
        planAdjustments=adjustments,
        recommendedNextSteps=next_steps,
    )


def save_evidence(path: str | Path, evidence: ExperimentEvidence) -> Path:
    """Atomically persist evidence under caller-controlled artifact storage."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(evidence.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return target


def validate_with_public_contract(evidence: ExperimentEvidence) -> Any:
    try:
        from app.contracts.scientific_research import ExperimentEvidence as PublicEvidence
    except ImportError:
        return ExperimentEvidence.model_validate(evidence.model_dump(mode="json"))
    return PublicEvidence.model_validate(evidence.model_dump(mode="json"))


__all__ = [
    "ExperimentEvidence",
    "ExperimentFeedback",
    "MetricEvidence",
    "PlanAdjustment",
    "build_experiment_evidence",
    "build_experiment_feedback",
    "save_evidence",
    "validate_with_public_contract",
]
