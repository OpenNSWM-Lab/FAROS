#!/usr/bin/env python3
"""Freeze and verify reproducible ReviewX experiment inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BACKEND_DATA = ROOT / "backend" / "data"
CODE_PATTERNS = (
    "backend/app/modules/review/*.py",
    "backend/app/storage/review_storage.py",
    "experiments/reviewx_eval/*.py",
)
ALLOWED_BUDGET_MODES = {"local_only", "balanced", "deep"}
ALLOWED_ABLATIONS = {
    "full",
    "no_verifier",
    "no_citation_semantic",
    "no_external_calibration",
    "no_mismatch_routing",
    "no_risk_tree",
    "no_revision_feedback",
    "no_llm_calibration",
}


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(payload: Any) -> str:
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_bytes(content.encode("utf-8"))


def stored_path(path: Path) -> str:
    path = path.resolve()
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def resolve_stored_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"JSONL row must be an object: {path}:{line_number}")
            rows.append(row)
    return rows


def file_record(path: Path) -> dict[str, Any]:
    return {
        "path": stored_path(path),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def tree_record(path: Path) -> dict[str, Any]:
    files = [file_record(item) for item in sorted(path.rglob("*")) if item.is_file()]
    return {
        "root": stored_path(path),
        "fileCount": len(files),
        "treeSha256": canonical_sha256(files),
        "files": files,
    }


def code_snapshot() -> dict[str, Any]:
    paths: set[Path] = set()
    for pattern in CODE_PATTERNS:
        paths.update(path for path in ROOT.glob(pattern) if path.is_file())
    files = [file_record(path) for path in sorted(paths)]
    return {
        "patterns": list(CODE_PATTERNS),
        "fileCount": len(files),
        "treeSha256": canonical_sha256(files),
        "files": files,
    }


def validate_methods(payload: Any) -> list[dict[str, Any]]:
    methods = payload.get("methods") if isinstance(payload, dict) else None
    if not isinstance(methods, list) or not methods:
        raise ValueError("matrix must contain a non-empty methods array")
    result = []
    seen = set()
    for raw in methods:
        if not isinstance(raw, dict):
            raise ValueError("each method must be an object")
        method_id = str(raw.get("id") or "").strip()
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{1,79}", method_id):
            raise ValueError(f"invalid method id: {method_id!r}")
        if method_id in seen:
            raise ValueError(f"duplicate method id: {method_id}")
        seen.add(method_id)
        kind = str(raw.get("kind") or "reviewx")
        if kind != "reviewx":
            raise ValueError(f"unsupported method kind {kind!r}; currently supported: reviewx")
        budget_mode = str(raw.get("budgetMode") or "local_only")
        if budget_mode not in ALLOWED_BUDGET_MODES:
            raise ValueError(f"invalid budgetMode for {method_id}: {budget_mode}")
        ablation_mode = str(raw.get("ablationMode") or "full")
        ablations = {item.strip() for item in ablation_mode.split(",") if item.strip()}
        unknown = ablations - ALLOWED_ABLATIONS
        if unknown:
            raise ValueError(f"invalid ablations for {method_id}: {sorted(unknown)}")
        repetitions = int(raw.get("repetitions", 1))
        if not 1 <= repetitions <= 100:
            raise ValueError(f"repetitions must be in [1, 100] for {method_id}")
        result.append({
            "id": method_id,
            "kind": kind,
            "budgetMode": budget_mode,
            "ablationMode": ablation_mode,
            "providerName": raw.get("providerName"),
            "model": raw.get("model"),
            "repetitions": repetitions,
            "maxEstimatedTokens": raw.get("maxEstimatedTokens"),
            "notes": raw.get("notes"),
        })
    return result


def git_snapshot() -> dict[str, Any]:
    def command(*args: str) -> str:
        try:
            return subprocess.run(
                ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return ""

    return {
        "branch": command("branch", "--show-current"),
        "commit": command("rev-parse", "HEAD"),
        "isDirty": bool(command("status", "--porcelain")),
    }


def paper_input_record(paper_id: str, backend_data: Path) -> dict[str, Any]:
    paper_dir = backend_data / "papers" / paper_id
    if not paper_dir.is_dir():
        raise FileNotFoundError(f"paper directory not found: {paper_dir}")
    roots = [tree_record(paper_dir)]
    meta_path = paper_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    for experiment_id in meta.get("experimentIds", []) or []:
        path = backend_data / "experiments" / str(experiment_id)
        if path.is_dir():
            roots.append(tree_record(path))
    project_id = meta.get("projectId")
    if project_id:
        path = backend_data / "code_projects" / str(project_id) / "exports"
        if path.is_dir():
            roots.append(tree_record(path))
    return {
        "paperId": paper_id,
        "artifactRootCount": len(roots),
        "artifactSha256": canonical_sha256(roots),
        "artifactRoots": roots,
    }


def build_manifest(
    *,
    samples_path: Path,
    gold_path: Path | None,
    matrix_path: Path,
    backend_data: Path,
    api_base: str,
    run_timeout: int,
    fetch_timeout: int,
) -> dict[str, Any]:
    samples = read_jsonl(samples_path)
    gold = read_jsonl(gold_path) if gold_path else []
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    methods = validate_methods(matrix)
    if any(not row.get("paperId") for row in samples):
        raise ValueError("every sample must have paperId")
    sample_ids = [str(row.get("sampleId") or row.get("paperId")) for row in samples]
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("sampleId values must be unique")
    paper_ids = sorted({str(row["paperId"]) for row in samples})
    paper_inputs = [paper_input_record(paper_id, backend_data) for paper_id in paper_ids]
    frozen = {
        "dataset": {
            "samples": file_record(samples_path),
            "sampleCount": len(samples),
            "gold": file_record(gold_path) if gold_path else None,
            "goldCount": len(gold),
            "paperCount": len(paper_ids),
            "paperInputs": paper_inputs,
        },
        "matrix": {
            "source": file_record(matrix_path),
            "methods": methods,
            "methodRunCountPerSample": sum(method["repetitions"] for method in methods),
        },
        "runner": {
            "apiBase": api_base,
            "runTimeoutSeconds": run_timeout,
            "fetchTimeoutSeconds": fetch_timeout,
        },
        "code": code_snapshot(),
    }
    return {
        "schemaVersion": "reviewx_experiment_manifest_v1",
        "createdAt": datetime.now(UTC).isoformat(),
        "git": git_snapshot(),
        **frozen,
        "contentFingerprint": canonical_sha256(frozen),
    }


def verify_manifest(manifest: dict[str, Any]) -> list[str]:
    errors = []

    def verify_file(record: dict[str, Any]) -> None:
        path = resolve_stored_path(str(record.get("path") or ""))
        if not path.is_file():
            errors.append(f"missing file: {record.get('path')}")
            return
        actual = sha256_file(path)
        if actual != record.get("sha256"):
            errors.append(f"changed file: {record.get('path')}")

    verify_file(manifest["dataset"]["samples"])
    if manifest["dataset"].get("gold"):
        verify_file(manifest["dataset"]["gold"])
    verify_file(manifest["matrix"]["source"])
    for paper in manifest["dataset"].get("paperInputs", []):
        for root_record in paper.get("artifactRoots", []):
            root = resolve_stored_path(str(root_record.get("root") or ""))
            if not root.is_dir():
                errors.append(f"missing artifact root: {root_record.get('root')}")
                continue
            current = tree_record(root)
            if current["treeSha256"] != root_record.get("treeSha256"):
                errors.append(f"changed artifact root: {root_record.get('root')}")
    current_code = code_snapshot()
    if current_code["treeSha256"] != manifest["code"].get("treeSha256"):
        errors.append("changed ReviewX evaluation code snapshot")
    frozen = {
        key: manifest[key]
        for key in ("dataset", "matrix", "runner", "code")
    }
    if canonical_sha256(frozen) != manifest.get("contentFingerprint"):
        errors.append("manifest contentFingerprint is invalid")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples")
    parser.add_argument("--gold")
    parser.add_argument("--matrix")
    parser.add_argument("--output")
    parser.add_argument("--backend-data", default=str(DEFAULT_BACKEND_DATA))
    parser.add_argument("--api-base", default="http://localhost:8005")
    parser.add_argument("--run-timeout", type=int, default=240)
    parser.add_argument("--fetch-timeout", type=int, default=120)
    parser.add_argument("--verify", help="Verify an existing manifest instead of creating one")
    args = parser.parse_args()

    if args.verify:
        manifest = json.loads(Path(args.verify).read_text(encoding="utf-8"))
        errors = verify_manifest(manifest)
        if errors:
            print("manifest verification failed:")
            for error in errors:
                print(f"- {error}")
            return 2
        print(f"manifest valid fingerprint={manifest['contentFingerprint']}")
        return 0

    missing = [name for name in ("samples", "matrix", "output") if not getattr(args, name)]
    if missing:
        parser.error(f"required when creating a manifest: {', '.join('--' + name for name in missing)}")
    manifest = build_manifest(
        samples_path=Path(args.samples).resolve(),
        gold_path=Path(args.gold).resolve() if args.gold else None,
        matrix_path=Path(args.matrix).resolve(),
        backend_data=Path(args.backend_data).resolve(),
        api_base=args.api_base,
        run_timeout=args.run_timeout,
        fetch_timeout=args.fetch_timeout,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    run_count = manifest["dataset"]["sampleCount"] * manifest["matrix"]["methodRunCountPerSample"]
    print(
        f"samples={manifest['dataset']['sampleCount']} methods={len(manifest['matrix']['methods'])} "
        f"plannedRuns={run_count} fingerprint={manifest['contentFingerprint']} output={output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
