"""Test that plan_session_storage.py handles non-ASCII content via encoding=utf-8."""

from datetime import datetime

import pytest

from app.models.plan_session import (
    PlanSession,
    PlanSessionConfig,
    PlanSessionStatus,
    CandidatePlan,
)
from app.storage.plan_session_storage import PlanSessionStorage, CandidatePlanStorage


@pytest.fixture
def tmp_data_dir(tmp_path):
    return str(tmp_path)


def test_session_roundtrip_non_ascii(tmp_data_dir):
    """Create a PlanSession with non-ASCII config and verify round-trip."""
    storage = PlanSessionStorage(tmp_data_dir)
    config = PlanSessionConfig(
        userNotes="优化模型性能并提高代码覆盖率",
        paperType="algorithmic_method",
    )
    session = PlanSession(
        id="psess_test001",
        config=config,
        status=PlanSessionStatus.PENDING,
        createdAt=datetime.now(),
    )

    storage.create(session)
    fetched = storage.get("psess_test001")

    assert fetched is not None
    assert fetched.config.userNotes == "优化模型性能并提高代码覆盖率"


def test_list_all_non_ascii(tmp_data_dir):
    """Verify list_all returns sessions with non-ASCII content correctly."""
    storage = PlanSessionStorage(tmp_data_dir)

    for i, note in enumerate(["First note", "Deuxième note", "第三笔记"]):
        config = PlanSessionConfig(userNotes=note, paperType="algorithmic_method")
        session = PlanSession(
            id=f"psess_test{i:03d}",
            config=config,
            status=PlanSessionStatus.PENDING,
            createdAt=datetime.now(),
        )
        storage.create(session)

    sessions = storage.list_all()
    assert len(sessions) == 3
    notes = {s.config.userNotes for s in sessions}
    assert "First note" in notes
    assert "Deuxième note" in notes
    assert "第三笔记" in notes


def test_candidate_roundtrip_non_ascii(tmp_data_dir):
    """Create a CandidatePlan with non-ASCII content and verify round-trip."""
    storage = CandidatePlanStorage(tmp_data_dir)
    candidate = CandidatePlan(
        id="cplan_test001",
        sessionId="psess_001",
        indexNumber=1,
        title="使用遗传算法优化超参数配置",
        planAbstract="本文提出了一种新的方法",
        createdAt=datetime.now(),
    )

    storage.create(candidate)
    fetched = storage.get("cplan_test001")

    assert fetched is not None
    assert fetched.title == "使用遗传算法优化超参数配置"
    assert fetched.planAbstract == "本文提出了一种新的方法"
