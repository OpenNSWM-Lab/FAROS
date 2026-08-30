"""Test that code_projects_api.py uses context managers for file reads.

Verifies no open().read() patterns remain that would leave file handles unclosed.
"""
import re
import os


def test_no_unclosed_file_reads():
    """Verify no open(...).read() patterns remain in code_projects_api.py."""
    module_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "app",
        "modules",
        "code",
        "code_projects_api.py",
    )
    with open(module_path, "r", encoding="utf-8") as f:
        source = f.read()

    unclosed = re.findall(r"open\([^)]+\)\.read\(\)", source)
    assert len(unclosed) == 0, (
        f"Found {len(unclosed)} unclosed file read(s) in code_projects_api.py"
    )


def test_results_path_uses_context_manager():
    """Verify that results_path file reads use context managers."""
    module_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "app",
        "modules",
        "code",
        "code_projects_api.py",
    )
    with open(module_path, "r", encoding="utf-8") as f:
        source = f.read()

    context_reads = len(re.findall(r"with open\(results_path", source))
    assert context_reads >= 3, (
        f"Expected at least 3 context-managed opens of results_path, found {context_reads}"
    )
