"""Code-stage evidence collection for paper writing."""

from __future__ import annotations

import ast
import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional


def _data_dir() -> str:
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
    return os.path.join(base, "data")


def _compact(value: Any, max_chars: int = 1200) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _load_json(path: str) -> Optional[Any]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


def _read_text(path: str, max_chars: int = 4000) -> str:
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read(max_chars)
    except Exception:
        return ""


def _safe_repo_path(project_id: str, rel_path: str) -> Optional[str]:
    repo = os.path.realpath(os.path.join(_data_dir(), "code_projects", project_id, "repo"))
    target = os.path.realpath(os.path.join(repo, rel_path))
    if target == repo or target.startswith(repo + os.sep):
        return target
    return None


def _repo_file(project_id: str, rel_path: str, max_chars: int = 4000) -> str:
    path = _safe_repo_path(project_id, rel_path)
    return _read_text(path, max_chars) if path else ""


def _parse_python_list_constant(text: str, name: str) -> List[str]:
    match = re.search(rf"^{re.escape(name)}\s*=\s*(\[[^\n]+\])", text, flags=re.MULTILINE)
    if not match:
        return []
    try:
        value = ast.literal_eval(match.group(1))
    except Exception:
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def _collect_repo_evidence(project_id: Optional[str]) -> Dict[str, Any]:
    if not project_id:
        return {"projectId": None, "available": False}

    repo_dir = os.path.join(_data_dir(), "code_projects", project_id, "repo")
    files: List[str] = []
    if os.path.isdir(repo_dir):
        for root, _dirs, names in os.walk(repo_dir):
            for name in names:
                rel = os.path.relpath(os.path.join(root, name), repo_dir)
                if "__pycache__" in rel or rel.endswith(".pyc"):
                    continue
                files.append(rel)
    files = sorted(files)

    metrics = _load_json(os.path.join(repo_dir, "metrics.json"))
    report = _repo_file(project_id, "experiment_report.md", 5000)
    readme = _repo_file(project_id, "README.md", 3000)
    synthetic_runner = _repo_file(project_id, "evaluation/synthetic_runner.py", 3000)
    inferred_design: Dict[str, Any] = {}
    if synthetic_runner:
        inferred_design = {
            "datasets": _parse_python_list_constant(synthetic_runner, "DATASETS"),
            "methods": _parse_python_list_constant(synthetic_runner, "METHODS"),
            "isSynthetic": "is_synthetic" in synthetic_runner or "synthetic" in synthetic_runner.lower(),
            "source": "evaluation/synthetic_runner.py",
        }

    return {
        "projectId": project_id,
        "available": os.path.isdir(repo_dir),
        "fileCount": len(files),
        "files": files[:80],
        "readme": _compact(readme, 3000) if readme else "",
        "experimentReport": _compact(report, 5000) if report else "",
        "metrics": metrics if isinstance(metrics, (dict, list)) else None,
        "inferredExperimentDesign": inferred_design,
    }


def _iter_faros_run_dirs() -> Iterable[str]:
    runs_dir = os.path.join(_data_dir(), "faros", "runs")
    if not os.path.isdir(runs_dir):
        return
    for name in sorted(os.listdir(runs_dir), reverse=True):
        path = os.path.join(runs_dir, name)
        if os.path.isdir(path):
            yield path


def _memory_data(run_dir: str) -> Dict[str, Any]:
    memory = _load_json(os.path.join(run_dir, "memory.json"))
    if isinstance(memory, dict) and isinstance(memory.get("data"), dict):
        return memory["data"]
    return {}


def _discover_run_dirs(paper: Dict[str, Any], max_runs: int = 8) -> List[str]:
    data_root = _data_dir()
    run_ids = [str(item) for item in paper.get("runIds", []) if item]
    project_id = paper.get("projectId")
    selected: List[str] = []
    seen = set()

    def add(run_dir: str) -> None:
        if len(selected) >= max_runs:
            return
        key = os.path.basename(run_dir)
        if key in seen:
            return
        seen.add(key)
        selected.append(run_dir)

    for run_id in run_ids:
        add(os.path.join(data_root, "faros", "runs", run_id))

    if project_id:
        for run_dir in _iter_faros_run_dirs() or []:
            data = _memory_data(run_dir)
            if data.get("projectId") == project_id:
                add(run_dir)
    return [path for path in selected if os.path.isdir(path)]


def _resolve_data_rel_path(rel_path: Any) -> str:
    if not rel_path:
        return ""
    path = str(rel_path)
    if os.path.isabs(path):
        return path
    return os.path.join(_data_dir(), path)


def _collect_run_evidence(paper: Dict[str, Any]) -> List[Dict[str, Any]]:
    runs: List[Dict[str, Any]] = []
    for run_dir in _discover_run_dirs(paper):
        run_id = os.path.basename(run_dir)
        data = _memory_data(run_dir)
        artifacts = _load_json(os.path.join(run_dir, "artifacts.json"))
        report_path = _resolve_data_rel_path(data.get("reportMdPath"))
        metrics_path = _resolve_data_rel_path(data.get("metricsJsonPath"))
        run_entry = {
            "runId": run_id,
            "projectId": data.get("projectId"),
            "projectTitle": data.get("projectTitle"),
            "experimentIds": data.get("experimentIds") or ([data.get("experimentId")] if data.get("experimentId") else []),
            "experimentStatus": data.get("experimentStatus"),
            "experimentDesign": data.get("experimentDesign"),
            "executionSummary": data.get("executionSummary"),
            "metrics": data.get("metrics") or _load_json(metrics_path),
            "metricsJsonPath": data.get("metricsJsonPath"),
            "reportMdPath": data.get("reportMdPath"),
            "experimentReport": _compact(_read_text(report_path, 5000), 5000),
            "figures": data.get("figures") or [],
            "figurePaths": data.get("figurePaths") or [],
            "artifacts": artifacts if isinstance(artifacts, list) else [],
        }
        runs.append(run_entry)
    return runs


def _collect_experiment_storage_evidence(paper: Dict[str, Any]) -> List[Dict[str, Any]]:
    experiment_ids = list(dict.fromkeys(str(item) for item in paper.get("experimentIds", []) if item))
    project_id = paper.get("projectId")
    try:
        from app.storage.experiment_storage import get_experiment, get_metrics, list_experiments

        if project_id:
            for exp in list_experiments(str(project_id)):
                exp_id = exp.get("id")
                if exp_id:
                    experiment_ids.append(str(exp_id))
        entries: List[Dict[str, Any]] = []
        for exp_id in list(dict.fromkeys(experiment_ids))[:8]:
            exp = get_experiment(exp_id)
            if not exp:
                continue
            entries.append({
                "experimentId": exp_id,
                "record": exp,
                "metrics": get_metrics(exp_id)[:50],
            })
        return entries
    except Exception:
        return []


def _list_files(base_dir: str, max_files: int = 80) -> List[Dict[str, Any]]:
    files: List[Dict[str, Any]] = []
    if not os.path.isdir(base_dir):
        return files
    for root, _dirs, names in os.walk(base_dir):
        for name in sorted(names):
            if len(files) >= max_files:
                return files
            path = os.path.join(root, name)
            rel = os.path.relpath(path, base_dir)
            if "__pycache__" in rel or rel.endswith(".pyc"):
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            files.append({"path": rel, "size": size})
    return files


def _trace_summary(cart_dir: str, node_id: str) -> Dict[str, Any]:
    trace_dir = os.path.join(cart_dir, "trace", node_id)
    if not os.path.isdir(trace_dir):
        return {}
    return {
        "command": _compact(_read_text(os.path.join(trace_dir, "cmd.txt"), 1200), 1200),
        "task": _compact(_read_text(os.path.join(trace_dir, "task.md"), 2000), 2000),
        "stdout": _compact(_read_text(os.path.join(trace_dir, "stdout.log"), 2000), 2000),
        "stderr": _compact(_read_text(os.path.join(trace_dir, "stderr.log"), 2000), 2000),
        "exitCode": _compact(_read_text(os.path.join(trace_dir, "exit_code.txt"), 80), 80),
    }


def _node_result_summary(cart_dir: str, node_id: str) -> Optional[Dict[str, Any]]:
    node_dir = os.path.join(cart_dir, "data", node_id)
    result = _load_json(os.path.join(node_dir, "result.json"))
    if not isinstance(result, dict):
        return None
    output_metrics = result.get("outputs", {}).get("metrics") if isinstance(result.get("outputs"), dict) else None
    return {
        "nodeId": result.get("node_id") or node_id,
        "success": result.get("success"),
        "status": "success" if result.get("success") is True else "failed" if result.get("success") is False else None,
        "durationMs": result.get("duration_ms"),
        "message": _compact(result.get("message"), 500),
        "outputs": result.get("outputs"),
        "metrics": output_metrics,
        "artifacts": result.get("artifacts") or [],
        "error": result.get("error"),
        "nodeInfo": result.get("node_info"),
        "experimentPlan": _compact(result.get("experiment_plan"), 1200),
        "dataset": result.get("dataset"),
        "baseline": _compact(result.get("baseline"), 800),
        "metricsDeclaration": _compact(result.get("metrics_declaration"), 1000),
        "figuresAndTables": result.get("figures_and_tables") or [],
        "resultAnalysis": _compact(result.get("result_analysis"), 1400),
        "dataFiles": _list_files(node_dir, max_files=40),
        "trace": _trace_summary(cart_dir, node_id),
    }


def _cart_node_results(cart_dir: str, cart_summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    node_ids: List[str] = []
    blueprint = _load_json(os.path.join(cart_dir, "blueprint_state.json"))
    if isinstance(blueprint, dict):
        node_ids.extend(str(node_id) for node_id in blueprint.keys())
    data_dir = os.path.join(cart_dir, "data")
    if os.path.isdir(data_dir):
        node_ids.extend(
            name for name in sorted(os.listdir(data_dir))
            if os.path.isdir(os.path.join(data_dir, name))
        )
    for stage in cart_summary.get("stages", []) if isinstance(cart_summary.get("stages"), list) else []:
        for step in stage.get("steps", []) if isinstance(stage, dict) and isinstance(stage.get("steps"), list) else []:
            if isinstance(step, dict) and step.get("node_id"):
                node_ids.append(str(step["node_id"]))

    results: List[Dict[str, Any]] = []
    for node_id in list(dict.fromkeys(node_ids)):
        summary = _node_result_summary(cart_dir, node_id)
        if summary:
            results.append(summary)
    return results


def _cart_summary(cart_dir: str) -> Optional[Dict[str, Any]]:
    result = _load_json(os.path.join(cart_dir, "cart_results.json"))
    if not isinstance(result, dict):
        return None
    event_log = _load_json(os.path.join(cart_dir, "event_log.json"))
    blueprint = _load_json(os.path.join(cart_dir, "blueprint_state.json"))
    node_results = _cart_node_results(cart_dir, result)
    project_dir = os.path.join(cart_dir, "project")
    return {
        "cartId": result.get("cart_id") or os.path.basename(cart_dir),
        "cartDir": os.path.relpath(cart_dir, _data_dir()),
        "packageId": result.get("package_id"),
        "projectId": result.get("project_id"),
        "researchQuestion": result.get("research_question"),
        "hypothesis": result.get("hypothesis"),
        "proposedMethod": _compact(result.get("proposed_method"), 1600),
        "overallStatus": result.get("overall_status"),
        "durationMs": result.get("total_duration_ms"),
        "metrics": result.get("all_metrics"),
        "artifacts": result.get("all_artifacts") or [],
        "stages": result.get("stages") or [],
        "constants": result.get("constants") or {},
        "nodeResults": node_results,
        "blueprintState": blueprint if isinstance(blueprint, dict) else {},
        "eventLogSummary": [
            {
                "eventType": event.get("event_type"),
                "nodeId": event.get("node_id"),
                "status": event.get("status"),
                "message": _compact(event.get("message"), 300),
                "timestamp": event.get("timestamp"),
            }
            for event in (event_log if isinstance(event_log, list) else [])[:80]
            if isinstance(event, dict)
        ],
        "projectFiles": _list_files(project_dir, max_files=80),
    }


def _iter_cart_dirs() -> Iterable[str]:
    for root_name in ("code_artifact", "cart_artifacts"):
        root = os.path.join(_data_dir(), root_name)
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root), reverse=True):
            path = os.path.join(root, name)
            if os.path.isdir(path) and name.startswith("cart_"):
                yield path


def _collect_cart_evidence(project_id: Optional[str], max_entries: int = 4) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for cart_dir in _iter_cart_dirs():
        if len(entries) >= max_entries:
            break
        summary = _cart_summary(cart_dir)
        if not summary:
            continue
        if project_id and summary.get("projectId") != project_id:
            continue
        entries.append(summary)
    return entries


def collect_code_evidence_for_paper(paper: Dict[str, Any]) -> Dict[str, Any]:
    project_id = paper.get("projectId")
    repo = _collect_repo_evidence(str(project_id) if project_id else None)
    runs = _collect_run_evidence(paper)
    experiments = _collect_experiment_storage_evidence(paper)
    carts = _collect_cart_evidence(str(project_id) if project_id else None)

    warnings: List[str] = []
    if repo.get("inferredExperimentDesign", {}).get("isSynthetic"):
        warnings.append("Linked code project appears to use synthetic/local validation outputs; do not present them as full benchmark results.")
    if not any([repo.get("available"), runs, experiments, carts]):
        warnings.append("No linked code-stage evidence could be resolved from project, runs, experiments, or CART artifacts.")

    status = "collected" if any([repo.get("available"), runs, experiments, carts]) else "missing"
    return {
        "schemaVersion": "paper-code-evidence/v1",
        "status": status,
        "projectId": project_id,
        "repo": repo,
        "runs": runs,
        "experiments": experiments,
        "cartResults": carts,
        "warnings": warnings,
    }
