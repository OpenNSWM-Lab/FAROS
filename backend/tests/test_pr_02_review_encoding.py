"""Test that review_storage.py handles non-ASCII content via encoding=utf-8."""

import json
import os
import tempfile
import shutil
from unittest import mock

import pytest


@pytest.fixture
def tmp_reviews_dir(tmp_path):
    d = tmp_path / "reviews"
    d.mkdir()
    return str(d)


@pytest.fixture
def tmp_impr_dir(tmp_path):
    d = tmp_path / "improvement_requests"
    d.mkdir()
    return str(d)


def test_review_roundtrip_non_ascii(tmp_reviews_dir, tmp_impr_dir):
    """Create a review with Chinese characters and verify round-trip integrity."""
    with mock.patch("app.storage.review_storage.REVIEWS_DIR", tmp_reviews_dir), \
         mock.patch("app.storage.review_storage.IMPROVEMENT_REQUESTS_DIR", tmp_impr_dir):
        from app.storage import review_storage

        data = {
            "paperId": "paper_test",
            "reviewerProfile": "senior_reviewer",
            "providerName": "moonshot",
            "model": "moonshot-v1-8k",
            "reviewKind": "standard",
            "budgetMode": "balanced",
            "ablationMode": "full",
        }
        record = review_storage.create_review(data)
        review_id = record["id"]

        # Update with non-ASCII content
        review_storage.update_review(review_id, {
            "markdownReport": "# 审阅报告\n\n这是一个测试。",
            "findings": [{"text": "发现了严重的逻辑错误"}],
        })

        # Read back
        fetched = review_storage.get_review(review_id)
        assert fetched is not None
        assert "审阅报告" in fetched["markdownReport"]
        assert "发现了严重的逻辑错误" in fetched["findings"][0]["text"]


def test_list_reviews_non_ascii(tmp_reviews_dir, tmp_impr_dir):
    """Verify list_reviews returns non-ASCII content correctly."""
    with mock.patch("app.storage.review_storage.REVIEWS_DIR", tmp_reviews_dir), \
         mock.patch("app.storage.review_storage.IMPROVEMENT_REQUESTS_DIR", tmp_impr_dir):
        from app.storage import review_storage

        data = {"paperId": "paper_unicode"}
        record = review_storage.create_review(data)
        review_storage.update_review(record["id"], {
            "markdownReport": "éèê üöä 世界"
        })

        reviews = review_storage.list_reviews(paper_id="paper_unicode")
        assert len(reviews) == 1
        assert "éèê" in reviews[0]["markdownReport"]
        assert "世界" in reviews[0]["markdownReport"]


def test_improvement_request_non_ascii(tmp_reviews_dir, tmp_impr_dir):
    """Create an improvement request with non-ASCII fields and verify round-trip."""
    with mock.patch("app.storage.review_storage.REVIEWS_DIR", tmp_reviews_dir), \
         mock.patch("app.storage.review_storage.IMPROVEMENT_REQUESTS_DIR", tmp_impr_dir):
        from app.storage import review_storage

        # First create a review so the index dir exists
        review_record = review_storage.create_review({"paperId": "p1"})

        req = review_storage.create_improvement_request({
            "reviewId": review_record["id"],
            "paperId": "p1",
            "description": "改善建议：添加更多的测试用例",
            "suggestedEdit": "Replace with: Überprüfen Sie die Eingabe",
        })

        fetched = review_storage.get_improvement_request(req["id"])
        assert fetched is not None
        assert "改善建议" in fetched["description"]
        assert "Überprüfen" in fetched["suggestedEdit"]
