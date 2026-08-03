#!/usr/bin/env python3
"""Run ReviewX modes in batch and export eval records as JSONL."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from .freeze_experiment import resolve_stored_path, verify_manifest
except ImportError:
    from freeze_experiment import resolve_stored_path, verify_manifest


def fetch_json(
    api_base: str,
    path: str,
    query: dict[str, str] | None = None,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    timeout: int = 120,
):
    url = api_base.rstrip("/") + path
    if query:
        url += "?" + urlencode(query)
    payload = None
    headers = {"Accept": "application/json"}
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=payload, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def load_samples(api_base: str, sample_path: str | None) -> list[dict[str, Any]]:
    if sample_path:
        samples = []
        with Path(sample_path).open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                samples.append(json.loads(line))
        return samples

    payload = fetch_json(api_base, "/api/v1/papers")
    papers = payload.get("papers", payload if isinstance(payload, list) else [])
    return [
        {
            "sampleId": paper.get("id"),
            "paperId": paper.get("id"),
            "title": paper.get("title"),
            "sampleType": "faros_paper",
        }
        for paper in papers
        if paper.get("id")
    ]


def run_reviewx(
    api_base: str,
    paper_id: str,
    mode: str,
    ablation: str,
    timeout: int,
    provider_name: str | None = None,
    model: str | None = None,
) -> None:
    body = {"paperId": paper_id, "budgetMode": mode, "ablationMode": ablation}
    if provider_name:
        body["providerName"] = provider_name
    if model:
        body["model"] = model
    fetch_json(
        api_base,
        "/api/v1/reviews/reviewx/run",
        method="POST",
        body=body,
        timeout=timeout,
    )


def load_eval_record(api_base: str, paper_id: str, timeout: int) -> dict[str, Any]:
    return fetch_json(api_base, "/api/v1/reviews/reviewx/eval-record", {"paperId": paper_id}, timeout=timeout)


def validate_record_config(record: dict[str, Any], method_config: dict[str, Any]) -> None:
    expected = {
        "budgetMode": method_config.get("budgetMode") or "local_only",
        "ablationMode": method_config.get("ablationMode") or "full",
    }
    if method_config.get("providerName"):
        expected["providerName"] = method_config["providerName"]
    if method_config.get("model"):
        expected["model"] = method_config["model"]
    mismatches = [
        f"{key}: expected {value!r}, got {record.get(key)!r}"
        for key, value in expected.items()
        if record.get(key) != value
    ]
    if mismatches:
        raise ValueError("API returned a run with mismatched config: " + "; ".join(mismatches))


def load_completed_runs(
    path: Path,
    expected_fingerprint: str | None = None,
) -> set[tuple[str, str, int]]:
    completed: set[tuple[str, str, int]] = set()
    if not path.exists():
        return completed
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
            sample_id = str(row.get("sampleId") or row.get("paperId") or "")
            method = str(row.get("method") or "")
            if expected_fingerprint and row.get("experimentFingerprint") != expected_fingerprint:
                raise ValueError(
                    f"experiment fingerprint mismatch at {path}:{line_number}: "
                    f"expected {expected_fingerprint}, got {row.get('experimentFingerprint')}"
                )
            if sample_id and method:
                completed.add((sample_id, method, int(row.get("runnerRepetition", 0) or 0)))
    return completed


def legacy_method_matrix(modes: list[str], ablations: list[str]) -> list[dict[str, Any]]:
    methods = []
    for mode in modes:
        for ablation in ablations:
            method_id = f"ReviewX-{mode}" if ablation == "full" else f"ReviewX-{mode}-{ablation}"
            methods.append({
                "id": method_id,
                "kind": "reviewx",
                "budgetMode": mode,
                "ablationMode": ablation,
                "providerName": None,
                "model": None,
                "repetitions": 1,
                "maxEstimatedTokens": None,
            })
    return methods


def load_experiment_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != "reviewx_experiment_manifest_v1":
        raise ValueError(f"unsupported experiment manifest schema: {manifest.get('schemaVersion')}")
    errors = verify_manifest(manifest)
    if errors:
        raise ValueError("experiment manifest drift detected: " + "; ".join(errors))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base")
    parser.add_argument("--experiment-manifest", help="Frozen experiment manifest from freeze_experiment.py")
    parser.add_argument("--verify-only", action="store_true", help="Verify the manifest and exit without API calls")
    parser.add_argument("--papers", help="Optional JSONL file with sampleId and paperId")
    parser.add_argument("--modes", default="local_only,balanced,deep")
    parser.add_argument(
        "--ablations",
        default="full",
        help=(
            "Comma-separated ablation modes. Supported by the API: full, no_verifier, "
            "no_citation_semantic, no_external_calibration, no_mismatch_routing, no_risk_tree, "
            "no_revision_feedback, no_llm_calibration"
        ),
    )
    parser.add_argument("--output", default="experiments/reviewx_eval/outputs/reviewx_runs.jsonl")
    parser.add_argument("--sleep", type=float, default=0.0, help="Seconds to sleep between runs")
    parser.add_argument("--run-timeout", type=int, help="Seconds to wait for each ReviewX run request")
    parser.add_argument("--fetch-timeout", type=int, help="Seconds to wait for each eval-record request")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--limit-samples", type=int, default=0, help="Development smoke-test limit; 0 runs all samples")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Append to an existing output and skip completed sample/method pairs",
    )
    args = parser.parse_args()

    manifest = None
    try:
        if args.experiment_manifest:
            manifest = load_experiment_manifest(Path(args.experiment_manifest))
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"Failed to load experiment manifest: {exc}", file=sys.stderr)
        return 1
    if args.verify_only:
        if not manifest:
            print("--verify-only requires --experiment-manifest", file=sys.stderr)
            return 1
        print(f"manifest valid fingerprint={manifest['contentFingerprint']}")
        return 0

    if manifest:
        methods = manifest["matrix"]["methods"]
        sample_path = str(resolve_stored_path(manifest["dataset"]["samples"]["path"]))
        api_base = args.api_base or manifest["runner"]["apiBase"]
        run_timeout = args.run_timeout or int(manifest["runner"]["runTimeoutSeconds"])
        fetch_timeout = args.fetch_timeout or int(manifest["runner"]["fetchTimeoutSeconds"])
        experiment_fingerprint = manifest["contentFingerprint"]
    else:
        modes = [mode.strip() for mode in args.modes.split(",") if mode.strip()]
        ablations = [mode.strip() for mode in args.ablations.split(",") if mode.strip()]
        methods = legacy_method_matrix(modes, ablations)
        sample_path = args.papers
        api_base = args.api_base or "http://localhost:8005"
        run_timeout = args.run_timeout or 240
        fetch_timeout = args.fetch_timeout or 120
        experiment_fingerprint = None
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    try:
        completed_runs = load_completed_runs(output, experiment_fingerprint) if args.resume else set()
    except (OSError, ValueError) as exc:
        print(f"Failed to resume from output: {exc}", file=sys.stderr)
        return 1

    try:
        samples = load_samples(api_base, sample_path)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        print(f"Failed to load samples: {exc}", file=sys.stderr)
        return 1
    if args.limit_samples < 0:
        print("--limit-samples cannot be negative", file=sys.stderr)
        return 1
    if args.limit_samples:
        samples = samples[:args.limit_samples]

    exported = 0
    failed = 0
    output_mode = "a" if args.resume else "w"
    with output.open(output_mode, encoding="utf-8") as handle:
        for sample in samples:
            sample_id = sample.get("sampleId") or sample.get("paperId")
            paper_id = sample.get("paperId")
            if not paper_id:
                print(f"skip sample without paperId: {sample}", file=sys.stderr)
                continue

            for method_config in methods:
                method = str(method_config["id"])
                mode = str(method_config.get("budgetMode") or "local_only")
                ablation = str(method_config.get("ablationMode") or "full")
                provider_name = method_config.get("providerName")
                model = method_config.get("model")
                repetitions = int(method_config.get("repetitions", 1))
                for repetition in range(repetitions):
                    if (str(sample_id), method, repetition) in completed_runs:
                        print(f"skip completed {sample_id} {method} repetition={repetition}")
                        continue
                    started = time.time()
                    try:
                        run_reviewx(
                            api_base,
                            paper_id,
                            mode,
                            ablation,
                            run_timeout,
                            provider_name=provider_name,
                            model=model,
                        )
                        record = load_eval_record(api_base, paper_id, fetch_timeout)
                        validate_record_config(record, method_config)
                        elapsed_ms = int((time.time() - started) * 1000)
                        estimated_tokens = float((record.get("modelTrace") or {}).get("estimatedTokenCost", 0) or 0)
                        max_estimated_tokens = method_config.get("maxEstimatedTokens")
                        if max_estimated_tokens is not None and estimated_tokens > float(max_estimated_tokens):
                            raise ValueError(
                                f"token budget exceeded for {method}: {estimated_tokens} > {max_estimated_tokens}"
                            )
                        record.update({
                            "sampleId": sample_id,
                            "sampleType": sample.get("sampleType", "faros_paper"),
                            "sourcePaperId": sample.get("sourcePaperId"),
                            "method": method,
                            "runnerMode": mode,
                            "runnerAblation": ablation,
                            "runnerRepetition": repetition,
                            "runnerElapsedMs": elapsed_ms,
                            "paperTitle": sample.get("title"),
                            "experimentFingerprint": experiment_fingerprint,
                            "methodConfig": method_config,
                        })
                        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                        handle.flush()
                        exported += 1
                        print(
                            f"ok {sample_id} {method} repetition={repetition} "
                            f"review={record.get('reviewId')} elapsedMs={elapsed_ms}"
                        )
                    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
                        failed += 1
                        print(f"failed {sample_id} {method}: {exc}", file=sys.stderr)
                        if not args.continue_on_error:
                            return 1
                    if args.sleep:
                        time.sleep(args.sleep)

    print(f"exported={exported} failed={failed} output={output}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
