"""
PR-B11: _parse_json_response raises ValueError on invalid JSON
instead of silently returning {}.
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def runner():
    from app.code.pipeline.pipeline_runner import PipelineRunner
    from unittest.mock import MagicMock

    session = MagicMock()
    session.config = MagicMock()
    session.config.providerName = "test"
    session.config.model = "test"
    session.config.repoPath = "/tmp"
    session.config.goal = "test"
    session.config.constraints = None
    session.config.maxCandidates = 1
    session.trace = []
    session.candidateIds = []
    storage = MagicMock()
    return PipelineRunner(session, storage)


class TestParseJsonResponse:

    def test_valid_json_object(self, runner):
        result = runner._parse_json_response('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_inside_code_fence(self, runner):
        text = "Here is the result:\\n```json\\n{\"a\": 1}\\n```\\nDone."
        assert runner._parse_json_response(text) == {"a": 1}

    def test_json_embedded_in_prose(self, runner):
        text = "Some preamble {\"x\": 42} and trailing text"
        assert runner._parse_json_response(text) == {"x": 42}

    def test_invalid_text_raises_value_error(self, runner):
        with pytest.raises(ValueError, match="Failed to parse JSON"):
            runner._parse_json_response("this is not json at all")

    def test_empty_string_raises_value_error(self, runner):
        with pytest.raises(ValueError, match="Failed to parse JSON"):
            runner._parse_json_response("")

    def test_malformed_json_raises_value_error(self, runner):
        with pytest.raises(ValueError, match="Failed to parse JSON"):
            runner._parse_json_response('{"broken": ')
