"""Test that paper_storage.py get_paper/list_papers handle non-ASCII content."""

import json
import os
from unittest import mock

import pytest


@pytest.fixture
def tmp_papers_dir(tmp_path):
    d = tmp_path / "papers"
    d.mkdir()
    return str(d)


def test_get_paper_non_ascii(tmp_papers_dir):
    """Create a paper with Chinese title/authors and verify get_paper returns it intact."""
    with mock.patch("app.storage.paper_storage.PAPERS_DIR", tmp_papers_dir):
        from app.storage import paper_storage

        data = {
            "title": "基于深度学习的自然语言处理综述",
            "authors": ["张三", "李四"],
            "paperType": "survey",
            "targetVenue": "ACL",
        }
        record = paper_storage.create_paper(data)
        paper_id = record["id"]

        fetched = paper_storage.get_paper(paper_id)
        assert fetched is not None
        assert fetched["title"] == "基于深度学习的自然语言处理综述"
        assert fetched["authors"] == ["张三", "李四"]


def test_list_papers_non_ascii(tmp_papers_dir):
    """Verify list_papers returns non-ASCII titles correctly."""
    with mock.patch("app.storage.paper_storage.PAPERS_DIR", tmp_papers_dir):
        from app.storage import paper_storage

        paper_storage.create_paper({"title": "Premier article"})
        paper_storage.create_paper({"title": "第二篇论文"})
        paper_storage.create_paper({"title": "Übung macht den Meister"})

        papers = paper_storage.list_papers()
        assert len(papers) == 3
        titles = {p["title"] for p in papers}
        assert "Premier article" in titles
        assert "第二篇论文" in titles
        assert "Übung macht den Meister" in titles
