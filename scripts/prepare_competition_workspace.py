#!/usr/bin/env python3
"""Build a clean, credential-free workspace from verified FAROS artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = REPOSITORY_ROOT / "backend" / "data"
DEFAULT_TARGET = REPOSITORY_ROOT / "backend" / "runtime" / "competition-data"
BENCHMARK_DIRS = (
    "reviewx_reliability",
    "reviewx_planning",
    "reviewx_multidomain",
    "reviewx_peerqa",
)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _copy_file(source: Path, target: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _copy_tree_if_present(source: Path, target: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, target, dirs_exist_ok=True)


def _latest_approved_package(source: Path, package_id: str | None) -> tuple[Path, dict[str, Any]]:
    package_dir = source / "plan_packages"
    paths = [package_dir / f"{package_id}.json"] if package_id else list(package_dir.glob("ppkg_*.json"))
    candidates = []
    for path in paths:
        if not path.is_file():
            continue
        payload = _read_json(path)
        if str(payload.get("status")) == "approved":
            candidates.append((str(payload.get("createdAt") or ""), path, payload))
    if not candidates:
        raise RuntimeError("No approved PlanPackage was found")
    _, path, payload = max(candidates, key=lambda item: item[0])
    return path, payload


def _latest_verified_job(source: Path, job_id: str | None) -> tuple[Path, dict[str, Any]]:
    case_root = source / "competition_cases" / "reviewx_scifact"
    job_dir = case_root / "jobs"
    paths = [job_dir / f"{job_id}.json"] if job_id else list(job_dir.glob("*.json"))
    candidates = []
    for path in paths:
        if not path.is_file():
            continue
        payload = _read_json(path)
        resolved_id = str(payload.get("jobId") or path.stem)
        summary_path = case_root / "runs" / resolved_id / "summary.json"
        if str(payload.get("status")) != "completed" or not summary_path.is_file():
            continue
        summary = _read_json(summary_path)
        quality_gate = summary.get("qualityGate") or {}
        if str(quality_gate.get("status") or "").lower() != "passed":
            continue
        candidates.append((str(payload.get("updatedAt") or payload.get("createdAt") or ""), path, payload))
    if not candidates:
        raise RuntimeError("No completed SciFact job with a passing quality gate was found")
    _, path, payload = max(candidates, key=lambda item: item[0])
    return path, payload


def _prune_idea_records(ideas_root: Path, session_id: str) -> None:
    sessions = ideas_root / "sessions"
    for path in sessions.glob("*.json"):
        if path.stem != session_id:
            path.unlink()

    candidates = ideas_root / "candidates"
    for path in candidates.glob("*.json"):
        try:
            if str(_read_json(path).get("sessionId") or "") != session_id:
                path.unlink()
        except (ValueError, json.JSONDecodeError):
            path.unlink()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--package-id")
    parser.add_argument("--job-id")
    parser.add_argument("--force", action="store_true", help="Replace an existing generated workspace")
    parser.add_argument("--without-dataset", action="store_true", help="Skip the local SciFact archive")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    source = args.source.expanduser().resolve()
    target = args.target.expanduser().resolve()
    if not source.is_dir():
        raise FileNotFoundError(source)
    if source == target or source in target.parents:
        raise ValueError("Target must not be the source data directory or one of its children")
    if target.exists():
        if not args.force:
            raise FileExistsError(f"Target already exists; pass --force to replace it: {target}")
        shutil.rmtree(target)

    package_path, package = _latest_approved_package(source, args.package_id)
    job_path, job = _latest_verified_job(source, args.job_id)
    package_id = str(package["packageId"])
    idea_session_id = str((package.get("source") or {})["ideaSessionId"])
    idea_candidate_id = str((package.get("source") or {})["ideaCandidateId"])
    job_id = str(job["jobId"])
    feedback_id = str(job.get("feedbackId") or "")
    if not feedback_id:
        raise RuntimeError(f"SciFact job {job_id} is not linked to human-review feedback")

    staging = target.with_name(f".{target.name}.building")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        _copy_tree_if_present(source / "ideas", staging / "ideas")
        _prune_idea_records(staging / "ideas", idea_session_id)
        _copy_file(package_path, staging / "plan_packages" / package_path.name)

        case_source = source / "competition_cases" / "reviewx_scifact"
        _copy_file(job_path, staging / "competition_cases" / "reviewx_scifact" / "jobs" / job_path.name)
        _copy_tree_if_present(
            case_source / "runs" / job_id,
            staging / "competition_cases" / "reviewx_scifact" / "runs" / job_id,
        )
        feedback_path = source / "reviewx_experiment_feedback" / f"{feedback_id}.json"
        _copy_file(feedback_path, staging / "reviewx_experiment_feedback" / feedback_path.name)

        for directory in BENCHMARK_DIRS:
            _copy_tree_if_present(
                source / "experiments" / directory,
                staging / "experiments" / directory,
            )
        if not args.without_dataset:
            _copy_tree_if_present(source / "external" / "scifact", staging / "external" / "scifact")

        feedback = _read_json(feedback_path)
        signoffs = feedback.get("humanSignoffs") or {}
        manifest = {
            "schemaVersion": "faros-competition-workspace/v1",
            "generatedAt": datetime.now(UTC).isoformat(),
            "credentialPolicy": "No API keys or provider configuration are copied.",
            "researchDesign": {
                "ideaSessionId": idea_session_id,
                "ideaCandidateId": idea_candidate_id,
                "planPackageId": package_id,
                "planStatus": package.get("status"),
                "planQualityScore": (package.get("qualityGate") or {}).get("overallScore"),
            },
            "verifiedClosedLoop": {
                "dataset": "SciFact",
                "jobId": job_id,
                "runId": job.get("runId"),
                "feedbackId": feedback_id,
                "qualityGate": "passed",
                "qwenModel": job.get("model"),
                "publicationReady": all(
                    str((signoffs.get(stage) or {}).get("status")) == "approved"
                    for stage in ("plan", "conclusion")
                ),
                "reviewerPolicy": feedback.get("reviewerPolicy", "single_accountable_reviewer"),
            },
            "integrity": {
                "planPackageSha256": _sha256(package_path),
                "jobSha256": _sha256(job_path),
                "feedbackSha256": _sha256(feedback_path),
            },
        }
        (staging / "competition_workspace_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        staging.replace(target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    print(json.dumps({"target": str(target), **manifest}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"competition workspace preparation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
