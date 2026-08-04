#!/usr/bin/env python3
"""Export latest ReviewX eval records for all papers as JSONL."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def fetch_json(api_base: str, path: str, query: dict[str, str] | None = None):
    url = api_base.rstrip("/") + path
    if query:
        url += "?" + urlencode(query)
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base", default="http://localhost:8005")
    parser.add_argument("--output", default="experiments/reviewx_eval/reviewx_eval.jsonl")
    args = parser.parse_args()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    try:
        papers_payload = fetch_json(args.api_base, "/api/v1/papers")
    except (HTTPError, URLError, TimeoutError) as exc:
        print(f"Failed to fetch papers: {exc}", file=sys.stderr)
        return 1

    papers = papers_payload.get("papers", papers_payload if isinstance(papers_payload, list) else [])
    exported = 0
    skipped = 0
    with output.open("w", encoding="utf-8") as handle:
        for paper in papers:
            paper_id = paper.get("id")
            if not paper_id:
                continue
            try:
                record = fetch_json(args.api_base, "/api/v1/reviews/reviewx/eval-record", {"paperId": paper_id})
            except HTTPError as exc:
                skipped += 1
                print(f"skip {paper_id}: HTTP {exc.code}", file=sys.stderr)
                continue
            except (URLError, TimeoutError) as exc:
                skipped += 1
                print(f"skip {paper_id}: {exc}", file=sys.stderr)
                continue
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            exported += 1

    print(f"exported={exported} skipped={skipped} output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
