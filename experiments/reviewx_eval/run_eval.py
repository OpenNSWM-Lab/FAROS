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


def run_reviewx(api_base: str, paper_id: str, mode: str) -> None:
    fetch_json(
        api_base,
        "/api/v1/reviews/reviewx/run",
        method="POST",
        body={"paperId": paper_id, "budgetMode": mode},
        timeout=240,
    )


def load_eval_record(api_base: str, paper_id: str) -> dict[str, Any]:
    return fetch_json(api_base, "/api/v1/reviews/reviewx/eval-record", {"paperId": paper_id})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base", default="http://localhost:8005")
    parser.add_argument("--papers", help="Optional JSONL file with sampleId and paperId")
    parser.add_argument("--modes", default="local_only,balanced,deep")
    parser.add_argument("--output", default="experiments/reviewx_eval/outputs/reviewx_runs.jsonl")
    parser.add_argument("--sleep", type=float, default=0.0, help="Seconds to sleep between runs")
    parser.add_argument("--continue-on-error", action="store_true")
    args = parser.parse_args()

    modes = [mode.strip() for mode in args.modes.split(",") if mode.strip()]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    try:
        samples = load_samples(args.api_base, args.papers)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        print(f"Failed to load samples: {exc}", file=sys.stderr)
        return 1

    exported = 0
    failed = 0
    with output.open("w", encoding="utf-8") as handle:
        for sample in samples:
            sample_id = sample.get("sampleId") or sample.get("paperId")
            paper_id = sample.get("paperId")
            if not paper_id:
                print(f"skip sample without paperId: {sample}", file=sys.stderr)
                continue

            for mode in modes:
                method = f"ReviewX-{mode}"
                started = time.time()
                try:
                    run_reviewx(args.api_base, paper_id, mode)
                    record = load_eval_record(args.api_base, paper_id)
                    elapsed_ms = int((time.time() - started) * 1000)
                    record.update({
                        "sampleId": sample_id,
                        "sampleType": sample.get("sampleType", "faros_paper"),
                        "method": method,
                        "runnerMode": mode,
                        "runnerElapsedMs": elapsed_ms,
                        "paperTitle": sample.get("title"),
                    })
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    handle.flush()
                    exported += 1
                    print(f"ok {sample_id} {method} review={record.get('reviewId')} elapsedMs={elapsed_ms}")
                except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
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
