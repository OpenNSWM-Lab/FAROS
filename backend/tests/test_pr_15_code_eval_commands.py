"""Tests for code_eval_api EvalRepoRequest commands max_length (PR-B15)."""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.modules.code.code_eval_api import EvalRepoRequest


class TestEvalRepoRequestCommandsMaxLength:
    """commands list must be capped at 20 items."""

    def test_empty_commands_accepted(self):
        req = EvalRepoRequest(repoPath="/tmp/repo", commands=[])
        assert req.commands == []

    def test_commands_at_max_length_accepted(self):
        cmds = ["echo " + str(i) for i in range(20)]
        req = EvalRepoRequest(repoPath="/tmp/repo", commands=cmds)
        assert len(req.commands) == 20

    def test_commands_over_max_length_rejected(self):
        from pydantic import ValidationError
        cmds = ["echo " + str(i) for i in range(25)]
        with pytest.raises(ValidationError) as exc_info:
            EvalRepoRequest(repoPath="/tmp/repo", commands=cmds)
        errors = exc_info.value.errors()
        assert any("commands" in str(e.get("loc", "")) for e in errors)

    def test_default_commands_empty(self):
        req = EvalRepoRequest(repoPath="/tmp/repo")
        assert req.commands == []

    def test_single_command_accepted(self):
        req = EvalRepoRequest(repoPath="/tmp/repo", commands=["pytest"])
        assert req.commands == ["pytest"]
