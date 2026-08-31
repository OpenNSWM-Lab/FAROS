"""Test that pdf_renderer.py uses context managers for file reads.

Verifies that _requires_xelatex correctly detects XeTeX markers and that
no unclosed file reads remain in the module.
"""
import os
import re
import tempfile


def test_requires_xelatex_with_ctexart():
    """_requires_xelatex should return True for files using ctexart."""
    from app.services.pdf_renderer import _requires_xelatex

    with tempfile.NamedTemporaryFile(mode="w", suffix=".tex", delete=False, encoding="utf-8") as f:
        f.write("\\documentclass{ctexart}\n\\begin{document}\nHello\n\\end{document}\n")
        path = f.name

    try:
        assert _requires_xelatex(path) is True
    finally:
        os.unlink(path)


def test_requires_xelatex_with_standard_latex():
    """_requires_xelatex should return False for standard LaTeX without CJK markers."""
    from app.services.pdf_renderer import _requires_xelatex

    with tempfile.NamedTemporaryFile(mode="w", suffix=".tex", delete=False, encoding="utf-8") as f:
        f.write("\\documentclass{article}\n\\begin{document}\nHello world\n\\end{document}\n")
        path = f.name

    try:
        assert _requires_xelatex(path) is False
    finally:
        os.unlink(path)


def test_requires_xelatex_with_fontspec():
    """_requires_xelatex should return True for files using fontspec."""
    from app.services.pdf_renderer import _requires_xelatex

    with tempfile.NamedTemporaryFile(mode="w", suffix=".tex", delete=False, encoding="utf-8") as f:
        f.write("\\documentclass{article}\n\\usepackage{fontspec}\n\\begin{document}\nTest\n\\end{document}\n")
        path = f.name

    try:
        assert _requires_xelatex(path) is True
    finally:
        os.unlink(path)


def test_requires_xelatex_missing_file():
    """_requires_xelatex should return False for nonexistent files."""
    from app.services.pdf_renderer import _requires_xelatex

    assert _requires_xelatex("/nonexistent/path/to/file.tex") is False


def test_no_unclosed_file_reads():
    """Verify no open(...).read() patterns remain in pdf_renderer.py."""
    module_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "app",
        "services",
        "pdf_renderer.py",
    )
    with open(module_path, "r", encoding="utf-8") as f:
        source = f.read()

    unclosed = re.findall(r"open\([^)]+\)\.read\([^)]*\)", source)
    assert len(unclosed) == 0, (
        f"Found {len(unclosed)} unclosed file read(s) in pdf_renderer.py: {unclosed}"
    )
