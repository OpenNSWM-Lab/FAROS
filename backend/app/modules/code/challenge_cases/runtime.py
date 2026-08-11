from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Mapping

from app.modules.code.execution_assessment import (
    ExecutionAssessment,
    ExecutionClass,
    ExecutionStatus,
)
from app.modules.code.experiment_evidence_service import (
    build_experiment_evidence,
    build_experiment_feedback,
    save_evidence,
)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def finalize_case_cart(
    *,
    output_root: Path,
    case_id: str,
    project_source: Path,
    execution_class: ExecutionClass,
    metrics: Mapping[str, Any],
    artifacts: Mapping[str, str | bytes],
    config: Mapping[str, Any],
    method: str,
    baseline: str,
    log_text: str,
    expected: list[dict[str, str]],
) -> Path:
    cart = output_root / f"cart_{case_id}"
    if cart.exists():
        raise FileExistsError(f"Refusing to overwrite existing case cart: {cart}")
    data_dir = cart / "data" / "step-1"
    data_dir.mkdir(parents=True)
    shutil.copytree(
        project_source,
        cart / "project",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    for name, content in artifacts.items():
        target = data_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
    write_json(data_dir / "config.json", dict(config))
    write_json(data_dir / "metrics.json", dict(metrics))
    declared = [
        {"name": name, "path": f"data/step-1/{name}"}
        for name in [*artifacts, "config.json", "metrics.json"]
    ]
    write_json(data_dir / "result.json", {
        "node_id": "step-1",
        "success": True,
        "message": "Representative case completed with measured outputs.",
        "outputs": {"metrics": dict(metrics)},
        "artifacts": declared,
        "baseline": baseline,
        "node_info": {"label": case_id, "method": method},
        "duration_ms": 0,
    })
    write_json(cart / "data" / "manifest.json", {
        "cart_id": cart.name,
        "package_id": f"ppkg_{case_id}",
        "source": "challenge_cup_representative_case",
        "execution_class": execution_class.value,
        "config": dict(config),
    })
    trace_dir = cart / "trace" / "step-1"
    trace_dir.mkdir(parents=True)
    (trace_dir / "stdout.log").write_text(log_text, encoding="utf-8")
    (trace_dir / "stderr.log").write_text("", encoding="utf-8")
    (trace_dir / "exit_code.txt").write_text("0\n", encoding="utf-8")
    write_json(cart / "event_log.json", [
        {"event_type": "cart_start", "node_id": cart.name, "status": "running"},
        {"event_type": "node_complete", "node_id": "step-1", "status": "success"},
        {"event_type": "cart_complete", "node_id": cart.name, "status": "success"},
    ])
    write_json(cart / "blueprint_state.json", {"step-1": {"status": "success"}})
    write_json(cart / "cart_results.json", {
        "cart_id": cart.name,
        "package_id": f"ppkg_{case_id}",
        "overall_status": "success",
        "total_nodes": 1,
        "succeeded": 1,
        "failed": 0,
        "skipped": 0,
        "total_duration_ms": 0,
        "proposed_method": method,
        "all_metrics": {"step-1": dict(metrics)},
    })

    assessment = ExecutionAssessment(
        runId=f"run_{case_id}",
        questionId=f"question_{case_id}",
        planPackageId=f"ppkg_{case_id}",
        executionClass=execution_class,
        feasibilityScore=0.9,
        rationale="Versioned inputs, fixed configuration, metrics and stop conditions are present.",
        availableInputs=list(artifacts),
        toolsAndEnvironment=["Python standard library", "FAROS cart runtime"],
        validationMetrics=list(metrics),
        stopConditions=["Stop after the fixed configuration completes"],
        status=ExecutionStatus.READY,
    )
    write_json(cart / "data" / "execution_assessment.json", assessment.model_dump(mode="json"))
    evidence = build_experiment_evidence(cart, assessment, code_run_id=cart.name)
    if evidence.status != ExecutionStatus.EXECUTED:
        raise RuntimeError(f"Representative case is not reproducible: {evidence.failures}")
    save_evidence(cart / "data" / "experiment_evidence.json", evidence)
    feedback = build_experiment_feedback(evidence, {
        "stages": [{"steps": [{"id": "step-1", "expected": expected}]}],
    })
    write_json(cart / "data" / "experiment_feedback.json", feedback.model_dump(mode="json"))
    return cart
