"""
Shared test fixtures for FAROS backend tests.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest


# Ensure the backend package is importable
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def temp_repo_dir():
    """Create a temporary directory with a minimal Python project for testing."""
    with tempfile.TemporaryDirectory(prefix="faros_test_") as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir(parents=True)

        # Create a simple working Python file
        src = repo / "src"
        src.mkdir(exist_ok=True)
        (src / "__init__.py").write_text("")
        (src / "main.py").write_text(
            '"""Simple test main module."""\n'
            'import sys\n'
            'def main():\n'
            '    print("Hello from test project")\n'
            '    return 0\n'
            'if __name__ == "__main__":\n'
            '    sys.exit(main())\n'
        )

        # Create requirements.txt
        (repo / "requirements.txt").write_text("# test deps\n")

        yield str(repo)


@pytest.fixture
def temp_broken_repo():
    """Create a temp dir with a broken Python project (syntax error)."""
    with tempfile.TemporaryDirectory(prefix="faros_test_") as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir(parents=True)

        src = repo / "src"
        src.mkdir(exist_ok=True)
        # Deliberate syntax error: missing colon
        (src / "main.py").write_text(
            '"""Broken module with syntax error."""\n'
            'import sys\n'
            'def main()\n'  # missing colon!
            '    print("This will fail")\n'
            '    return 0\n'
            'if __name__ == "__main__":\n'
            '    sys.exit(main())\n'
        )

        yield str(repo)


@pytest.fixture
def temp_import_error_repo():
    """Create a temp dir with a Python project that has an import error."""
    with tempfile.TemporaryDirectory(prefix="faros_test_") as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir(parents=True)

        (repo / "main.py").write_text(
            '"""Module with missing dependency."""\n'
            'import sys\n'
            'from nonexistent.module import Something\n'  # will fail
            'def main():\n'
            '    print("This will fail with import error")\n'
            '    return 0\n'
            'if __name__ == "__main__":\n'
            '    sys.exit(main())\n'
        )

        yield str(repo)
