"""Test that bare except clauses in code_jobs_api.py are replaced with typed exceptions.

Verifies that job_to_response handles invalid JSON in env_vars without raising,
and that valid JSON is parsed correctly.
"""
import json
import sys
import os
import types
from datetime import datetime
from unittest.mock import MagicMock

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_code_job(env_vars=None):
    """Create a minimal CodeJob-like mock."""
    job = types.SimpleNamespace()
    job.id = "job-123"
    job.session_id = "sess-1"
    job.project_id = None
    job.candidate_id = None
    job.env_vars = env_vars
    job.status = "pending"
    job.mode = "quick"
    job.command = "echo hello"
    job.cwd_rel = None
    job.timeout_sec = 300
    job.workspace_path = None
    job.pid = None
    job.exit_code = None
    job.stdout_path = None
    job.stderr_path = None
    job.created_at = datetime(2026, 1, 1)
    job.started_at = None
    job.ended_at = None
    job.duration_sec = None
    return job


def test_job_to_response_invalid_json_env_vars():
    """job_to_response should not raise on invalid JSON in env_vars."""
    from app.modules.code.code_jobs_api import job_to_response

    job = _make_code_job(env_vars="{invalid json!!}")
    resp = job_to_response(job)
    assert resp.envVars is None
    assert resp.id == "job-123"


def test_job_to_response_valid_json_env_vars():
    """job_to_response should parse valid JSON env_vars correctly."""
    from app.modules.code.code_jobs_api import job_to_response

    env = {"KEY": "value", "OTHER": "123"}
    job = _make_code_job(env_vars=json.dumps(env))
    resp = job_to_response(job)
    assert resp.envVars == env


def test_job_to_response_none_env_vars():
    """job_to_response should handle None env_vars."""
    from app.modules.code.code_jobs_api import job_to_response

    job = _make_code_job(env_vars=None)
    resp = job_to_response(job)
    assert resp.envVars is None


def test_job_to_response_empty_string_env_vars():
    """job_to_response should handle empty string env_vars (falsy)."""
    from app.modules.code.code_jobs_api import job_to_response

    job = _make_code_job(env_vars="")
    resp = job_to_response(job)
    assert resp.envVars is None


def test_no_bare_except_in_module():
    """Verify no bare except: clauses remain in the module source."""
    module_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "app",
        "modules",
        "code",
        "code_jobs_api.py",
    )
    with open(module_path, "r", encoding="utf-8") as f:
        source = f.read()

    import re
    # Match except: that is not followed by any exception type
    bare_excepts = re.findall(r"^\s*except\s*:\s*$", source, re.MULTILINE)
    assert len(bare_excepts) == 0, (
        f"Found {len(bare_excepts)} bare except: clause(s) remaining in code_jobs_api.py"
    )
