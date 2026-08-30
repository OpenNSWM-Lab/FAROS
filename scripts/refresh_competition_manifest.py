#!/usr/bin/env python3
"""Validate the competition workspace and atomically refresh its public manifest."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
MODULE_PATH = BACKEND_ROOT / "app" / "modules" / "review" / "competition_workspace.py"
MODULE_SPEC = importlib.util.spec_from_file_location("faros_competition_workspace", MODULE_PATH)
if MODULE_SPEC is None or MODULE_SPEC.loader is None:
    raise RuntimeError(f"Unable to load competition workspace validator: {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(MODULE)
build_competition_workspace_dashboard = MODULE.build_competition_workspace_dashboard


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=BACKEND_ROOT / "runtime" / "competition-data",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate without replacing the manifest",
    )
    args = parser.parse_args()
    data_dir = args.data_dir.expanduser().resolve()
    payload = build_competition_workspace_dashboard(data_dir)
    if not payload["status"]["ready"]:
        raise RuntimeError(f"Competition chain is blocked: {payload['status']['blockers']}")

    if not args.check:
        target = data_dir / "competition_workspace_manifest.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"competition manifest refresh failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
