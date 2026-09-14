"""Regression: FAROS state store must persist non-ASCII run data on any locale.

read_text()/write_text() in state_store omitted encoding="utf-8", so run
records containing Chinese paper titles or Unicode agent outputs raised
UnicodeEncodeError/UnicodeDecodeError on hosts whose default locale is
not UTF-8 (Windows cp1252, minimal C/POSIX containers).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.faros.runtime.state_store import FarosStateStore


def _make_run(store):
    return store.create_run(
        blueprint_id="bp_test",
        profile_id="pf_test",
        execution_mode="sequential",
        inputs={"topic": "基于检索增强的科学写作框架研究"},
        steps=[],
    )


def test_run_roundtrip_preserves_chinese_inputs(tmp_path):
    store = FarosStateStore(root=tmp_path)
    run = _make_run(store)

    loaded = store.get_run(run["id"])

    assert loaded is not None
    assert loaded["inputs"]["topic"] == "基于检索增强的科学写作框架研究"


def test_list_runs_survives_nonascii_records(tmp_path):
    store = FarosStateStore(root=tmp_path)
    _make_run(store)

    runs = store.list_runs()

    assert len(runs) == 1
    assert runs[0]["inputs"]["topic"] == "基于检索增强的科学写作框架研究"


def test_memory_and_events_preserve_unicode(tmp_path):
    store = FarosStateStore(root=tmp_path)
    run = _make_run(store)

    store.save_memory(run["id"], {"summary": "实验结果表明方法有效 ✓"})
    store.append_event(run["id"], {"type": "log", "message": "步骤完成：数据预处理"})
    store.append_artifacts(run["id"], [{"name": "图表", "path": "/tmp/图表.png"}])

    assert store.get_memory(run["id"])["summary"] == "实验结果表明方法有效 ✓"
    assert store.list_events(run["id"])[0]["message"] == "步骤完成：数据预处理"
    assert store.list_artifacts(run["id"])[0]["name"] == "图表"

    print("PASS: state store non-ASCII round-trips")
