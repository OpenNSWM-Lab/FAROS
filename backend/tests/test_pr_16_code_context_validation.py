"""Tests for code_context_api BuildContextRequest repoPath validation (PR-B16)."""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.modules.code.code_context_api import BuildContextRequest


class TestBuildContextRequestRepoPathMaxLength:
    """repoPath must reject values longer than 1024 characters."""

    def test_normal_repopath_accepted(self):
        req = BuildContextRequest(repoPath="/home/user/repos/project")
        assert req.repoPath == "/home/user/repos/project"

    def test_repopath_at_max_length_accepted(self):
        path = "a" * 1024
        req = BuildContextRequest(repoPath=path)
        assert len(req.repoPath) == 1024

    def test_repopath_over_max_length_rejected(self):
        from pydantic import ValidationError
        path = "a" * 1025
        with pytest.raises(ValidationError) as exc_info:
            BuildContextRequest(repoPath=path)
        errors = exc_info.value.errors()
        assert any("repoPath" in str(e.get("loc", "")) for e in errors)

    def test_repopath_massively_over_rejected(self):
        from pydantic import ValidationError
        path = "z" * 50000
        with pytest.raises(ValidationError):
            BuildContextRequest(repoPath=path)
