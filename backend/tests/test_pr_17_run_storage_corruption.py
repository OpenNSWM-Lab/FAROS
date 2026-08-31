"""
Test for PR-B17: run_storage.py list_all and list_by_plan should
gracefully handle corrupted JSON files instead of crashing.
"""

import json
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.models.run import Run, RunStatus, RunConfig
from app.storage.run_storage import RunStorage


def _make_run(run_id: str = None, plan_id: str = None, status: RunStatus = RunStatus.PENDING) -> Run:
    """Create a minimal Run for testing."""
    return Run(
        id=run_id or str(uuid.uuid4()),
        planId=plan_id or str(uuid.uuid4()),
        status=status,
        createdAt=datetime.now(timezone.utc),
        startedAt=None,
        endedAt=None,
        type="plan",
        config=RunConfig(model="test-model"),
    )


def test_list_all_skips_corrupted_files():
    """list_all should skip corrupted JSON files and return valid runs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = RunStorage(storage_dir=tmpdir)

        # Create 3 valid runs
        for _ in range(3):
            storage.create(_make_run())

        # Create a corrupted file
        corrupted = Path(tmpdir) / "corrupted.json"
        corrupted.write_text("{not valid json!!!", encoding="utf-8")

        # list_all should return the 3 valid runs without raising
        runs = storage.list_all()
        assert len(runs) == 3


def test_list_all_skips_empty_file():
    """list_all should skip empty JSON files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = RunStorage(storage_dir=tmpdir)

        storage.create(_make_run())

        # Create an empty file
        empty = Path(tmpdir) / "empty.json"
        empty.write_text("", encoding="utf-8")

        runs = storage.list_all()
        assert len(runs) == 1


def test_list_by_plan_skips_corrupted_files():
    """list_by_plan should skip corrupted JSON files."""
    plan_id = "plan-123"

    with tempfile.TemporaryDirectory() as tmpdir:
        storage = RunStorage(storage_dir=tmpdir)

        # Create 2 runs for this plan
        for _ in range(2):
            storage.create(_make_run(plan_id=plan_id))

        # Create 1 run for a different plan
        storage.create(_make_run(plan_id="other-plan"))

        # Create a corrupted file
        corrupted = Path(tmpdir) / "corrupted.json"
        corrupted.write_text("}{invalid", encoding="utf-8")

        runs = storage.list_by_plan(plan_id)
        assert len(runs) == 2


def test_list_all_all_valid():
    """list_all should work normally when all files are valid."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = RunStorage(storage_dir=tmpdir)

        for _ in range(5):
            storage.create(_make_run())

        runs = storage.list_all()
        assert len(runs) == 5
