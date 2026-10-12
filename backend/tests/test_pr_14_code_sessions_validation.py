"""Tests for code_sessions_api CreateSessionRequest validation (PR-B14)."""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.modules.code.code_sessions_api import CreateSessionRequest


class TestCreateSessionRequestRepoPathMaxLength:
    """repoPath must reject values longer than 1024 characters."""

    def test_normal_repopath_accepted(self):
        req = CreateSessionRequest(
            repoPath="/home/user/repos/my-project",
            goal="Add feature X",
        )
        assert req.repoPath == "/home/user/repos/my-project"

    def test_repopath_at_max_length_accepted(self):
        path = "a" * 1024
        req = CreateSessionRequest(repoPath=path, goal="test")
        assert len(req.repoPath) == 1024

    def test_repopath_over_max_length_rejected(self):
        from pydantic import ValidationError
        path = "a" * 1025
        with pytest.raises(ValidationError) as exc_info:
            CreateSessionRequest(repoPath=path, goal="test")
        errors = exc_info.value.errors()
        assert any("repoPath" in str(e.get("loc", "")) for e in errors)

    def test_repopath_way_over_max_length_rejected(self):
        from pydantic import ValidationError
        path = "x" * 100000
        with pytest.raises(ValidationError):
            CreateSessionRequest(repoPath=path, goal="test")
