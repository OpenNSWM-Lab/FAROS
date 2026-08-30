"""Canonical runtime paths with an overridable data root."""

from __future__ import annotations

import os
from pathlib import Path


_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_REPOSITORY_ROOT = _BACKEND_ROOT.parent


def get_data_dir(*, create: bool = True) -> Path:
    configured = os.getenv("DATA_DIR", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            base = _REPOSITORY_ROOT if path.parts and path.parts[0] == "backend" else _BACKEND_ROOT
            path = base / path
    else:
        path = _BACKEND_ROOT / "data"
    path = path.resolve()
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def data_path(*parts: str, create_parent: bool = False) -> Path:
    path = get_data_dir() / Path(*parts)
    if create_parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    return path
