"""Regression: experiment storage must round-trip non-ASCII text on any locale.

open() calls in experiment_storage previously omitted encoding="utf-8",
so on hosts whose default locale is not UTF-8 (Windows cp1252, minimal
containers with POSIX/C locale) writing or reading JSON with Chinese
experiment metadata raised UnicodeDecodeError / UnicodeEncodeError.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.storage import experiment_storage


def test_experiment_roundtrip_preserves_chinese_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment_storage, "_EXPERIMENTS_DIR", str(tmp_path / "experiments"))

    created = experiment_storage.create_experiment(
        {
            "name": "消融实验：检索增强对生成质量的影响",
            "description": "对比基线与加入检索模块后的效果，记录显著性检验结果。",
            "tags": ["消融", "检索增强"],
        }
    )

    loaded = experiment_storage.get_experiment(created["id"])

    assert loaded is not None
    assert loaded["name"] == "消融实验：检索增强对生成质量的影响"
    assert "显著性检验" in loaded["description"]
    assert loaded["tags"] == ["消融", "检索增强"]


def test_listing_survives_nonascii_experiment_names(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment_storage, "_EXPERIMENTS_DIR", str(tmp_path / "experiments"))

    experiment_storage.create_experiment({"name": "实验一：数据预处理"})
    experiment_storage.create_experiment({"name": "Experiment 2: baseline"})

    names = {e["name"] for e in experiment_storage.list_experiments()}

    assert "实验一：数据预处理" in names
    assert "Experiment 2: baseline" in names


def test_figure_spec_preserves_chinese_caption(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment_storage, "_FIGURES_DIR", str(tmp_path / "figures"))

    artifact = experiment_storage.save_figure_artifact(
        exp_id="exp_test",
        figure_type="line",
        spec={"title": "训练曲线对比"},
        png_bytes=b"\x89PNG fake",
        pdf_bytes=None,
        caption="各基线在验证集上的收敛曲线",
        prompt_used="绘制训练曲线",
        model_used="test-model",
    )

    figures = experiment_storage.list_figures(artifact["experimentId"])
    assert figures, "figure artifact should be listed back"
    assert figures[0]["caption"] == "各基线在验证集上的收敛曲线"
    assert figures[0]["title"] == "训练曲线对比"

    print("PASS: non-ASCII storage round-trips")
