"""
Code Repair Service — AI-driven self-healing for failed pipeline steps.

When a pipeline step fails, this service:
1. Creates a backup of the original file (.farosbak extension)
2. Reads the error output and the source file that failed
3. Applies deterministic, rule-based fixes first
4. Falls back to LLM-based repair if deterministic fixes don't work
5. Applies the fix to the project files
6. Returns the fix summary for the user to review

Enhanced with backup/rollback support and expanded deterministic rules.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.llm.provider_client import ChatMessage, ProviderClient, ProviderError

logger = logging.getLogger(__name__)

# Max iterations for auto-fix loop
MAX_FIX_ITERATIONS = 3
# Backup file extension
BACKUP_EXT = ".farosbak"


@dataclass
class FixResult:
    """Result of a single fix attempt."""
    step_name: str
    error_before: str
    file_path: str
    fix_description: str
    original_content: str = ""
    new_content: str = ""
    diff_lines: List[str] = field(default_factory=list)  # unified diff lines
    applied: bool = False
    method: str = "none"  # "deterministic" | "llm" | "none"


@dataclass
class AutoFixReport:
    """Complete auto-fix report after all iterations."""
    project_id: str
    iterations: int = 0
    fixes_applied: List[FixResult] = field(default_factory=list)
    final_pipeline_status: str = "unknown"
    summary: str = ""


class CodeRepairService:
    """AI-driven code repair for failed pipeline execution steps."""

    FIX_SYSTEM_PROMPT = (
        "You are an expert Python code reviewer and debugger. "
        "Your task is to fix broken Python code based on error messages.\n\n"
        "RULES:\n"
        "1. Return ONLY the corrected file content, no explanations outside the code block.\n"
        "2. Preserve all existing functionality — only fix what's broken.\n"
        "3. If the error is about relative imports (e.g., 'from .routes import'), "
        "   replace them with absolute imports or inline the needed code.\n"
        "4. If the error is a SyntaxError, fix the syntax.\n"
        "5. If the error is about missing modules, add pip install comments at the top.\n"
        "6. Wrap your fix in ```python ... ``` code fence.\n"
        "7. If the code looks like a FastAPI app, add a `if __name__ == '__main__':` block at the end "
        "   that runs `import uvicorn; uvicorn.run(app, host='127.0.0.1', port=8000)` so it can be tested."
    )

    def __init__(self, provider_name: str = "qwen", model: str = "qwen-max"):
        self.provider_name = provider_name
        self.model = model

    def auto_fix(
        self,
        project_id: str,
        repo_dir: str,
        failed_steps: List[Dict[str, Any]],
    ) -> AutoFixReport:
        """
        Attempt to auto-fix failed pipeline steps.

        Args:
            project_id: Code project ID
            repo_dir: Path to the project repo directory on disk
            failed_steps: List of failed PipelineStepResult dicts with name, stderr, etc.

        Returns:
            AutoFixReport with all fixes applied and final status.
        """
        report = AutoFixReport(project_id=project_id)

        try:
            client = ProviderClient(self.provider_name)
        except ProviderError as e:
            logger.error("Cannot initialize provider for auto-fix: %s", e)
            report.summary = f"Provider not available: {e}"
            return report

        # Phase 0: Fix all Python files with relative imports (common issue)
        all_py_files = list(Path(repo_dir).rglob("*.py"))
        for py_file in all_py_files:
            if "_faros_" in py_file.name:
                continue
            try:
                content = py_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if "from ." in content:
                # Fix this file deterministically
                rel_path = str(py_file.relative_to(repo_dir)).replace("\\", "/")
                det_fix = self._apply_deterministic_fixes(
                    "Relative Imports Fix", rel_path, content, "", ""
                )
                if det_fix and det_fix.new_content != content:
                    try:
                        _backup_file(py_file)
                        py_file.write_text(det_fix.new_content, encoding="utf-8")
                        det_fix.applied = True
                        det_fix.method = "deterministic"
                        report.fixes_applied.append(det_fix)
                        report.iterations += 1
                        logger.info("Bulk-fixed relative imports in %s", rel_path)
                    except OSError:
                        pass

        for step in failed_steps:
            if report.iterations >= MAX_FIX_ITERATIONS:
                break

            step_name = step.get("name", "unknown")
            stderr = step.get("stderr", "")
            stdout = step.get("stdout", "")

            if not stderr.strip() and not ("failed" in step_name.lower()):
                continue

            # Find the relevant file(s) referenced in the error
            target_files = self._find_error_files(stderr, repo_dir)
            if not target_files:
                # Default: check common entry points
                for candidate in ["src/main.py", "main.py", "src/train.py", "train.py"]:
                    fp = os.path.join(repo_dir, candidate)
                    if os.path.isfile(fp):
                        target_files.append(candidate)

            for file_path in target_files:
                abs_path = os.path.join(repo_dir, file_path)
                if not os.path.isfile(abs_path):
                    continue

                original_content = Path(abs_path).read_text(encoding="utf-8", errors="replace")
                if not original_content.strip():
                    continue

                report.iterations += 1
                logger.info("Auto-fix iteration %d: %s -> %s", report.iterations, step_name, file_path)

                # Phase 1: Try deterministic fixes first
                det_fix = self._apply_deterministic_fixes(
                    step_name, file_path, original_content, stderr, stdout
                )
                if det_fix and det_fix.new_content != original_content:
                    det_fix.method = "deterministic"
                    det_fix.original_content = original_content
                    det_fix.diff_lines = self._generate_diff(file_path, original_content, det_fix.new_content)
                    try:
                        _backup_file(Path(abs_path))
                        Path(abs_path).write_text(det_fix.new_content, encoding="utf-8")
                        det_fix.applied = True
                        logger.info("Deterministic fix applied to %s: %s", file_path, det_fix.fix_description)
                    except OSError as e:
                        logger.error("Failed to write fix: %s", e)
                    report.fixes_applied.append(det_fix)
                    continue

                # Phase 2: Try LLM-based fix
                fix = self._generate_llm_fix(client, step_name, file_path, original_content, stderr, stdout)
                fix.original_content = original_content
                if fix and fix.new_content and fix.new_content != original_content:
                    fix.diff_lines = self._generate_diff(file_path, original_content, fix.new_content)
                    fix.method = "llm"
                    try:
                        _backup_file(Path(abs_path))
                        Path(abs_path).write_text(fix.new_content, encoding="utf-8")
                        fix.applied = True
                        logger.info("LLM fix applied to %s: %s", file_path, fix.fix_description[:100])
                    except OSError as e:
                        logger.error("Failed to write fix: %s", e)
                else:
                    fix.method = "llm"
                    if not fix.fix_description:
                        fix.fix_description = "LLM did not return a valid fix"
                report.fixes_applied.append(fix)

                if report.iterations >= MAX_FIX_ITERATIONS:
                    break

        # Summary
        applied = [f for f in report.fixes_applied if f.applied]
        if applied:
            report.summary = (
                f"Applied {len(applied)} fix(es): "
                + "; ".join(f"{f.step_name}→{f.file_path}: {f.fix_description[:60]}" for f in applied)
            )
        else:
            report.summary = "No fixes could be automatically applied."

        return report

    # ---- internals ----

    def _find_error_files(self, stderr: str, repo_dir: str) -> List[str]:
        """Extract file paths from error traceback."""
        files = []
        # Pattern: File "path/to/file.py", line N
        pattern = re.compile(r'File\s+"([^"]+\.py)"', re.MULTILINE)
        for m in pattern.finditer(stderr):
            fpath = m.group(1)
            # Convert absolute path to relative
            if fpath.startswith(repo_dir):
                fpath = os.path.relpath(fpath, repo_dir).replace("\\", "/")
            # Only include files within the project
            if not fpath.startswith("..") and not fpath.startswith("<"):
                if fpath not in files:
                    files.append(fpath)
        return files

    def _apply_deterministic_fixes(
        self, step_name: str, file_path: str, content: str, stderr: str, stdout: str
    ) -> Optional[FixResult]:
        """Apply deterministic, rule-based fixes before trying LLM."""
        original = content
        fixed = content
        desc_parts: List[str] = []

        # Rule 1: Fix relative imports (from .xxx import yyy -> from xxx import yyy)
        if "from ." in fixed:
            new_lines = []
            for line in fixed.split("\n"):
                stripped = line.lstrip()
                if stripped.startswith("from ."):
                    indent = line[:len(line) - len(stripped)]
                    module = stripped.replace("from .", "from ", 1)
                    new_lines.append("# [FAROS-AUTOFIX] converted relative import")
                    new_lines.append(f"{indent}{module}")
                    desc_parts.append("converted relative import")
                else:
                    new_lines.append(line)
            fixed = "\n".join(new_lines)

        # Rule 2: Fix ModuleNotFoundError — add sys.path for local modules
        if "ModuleNotFoundError" in stderr:
            # Extract the missing module name
            m = re.search(r"No module named '(\w+)'", stderr)
            if m and m.group(1) not in ("numpy", "pandas", "torch", "fastapi", "uvicorn", "pydantic"):
                # It's a local module — ensure src/ is importable
                # Add path setup at the top if not already present
                if "sys.path.insert" not in fixed:
                    path_fix = (
                        'import sys, os\n'
                        'sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))\n'
                        'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n'
                    )
                    # Insert after last import line or after docstring
                    lines = fixed.split("\n")
                    insert_idx = 0
                    for i, line in enumerate(lines):
                        if line.startswith("import ") or line.startswith("from "):
                            insert_idx = i + 1
                    lines.insert(insert_idx, f"\n# [FAROS-AUTOFIX] added path for local module '{m.group(1)}'")
                    lines.insert(insert_idx, path_fix.strip())
                    fixed = "\n".join(lines)
                    desc_parts.append(f"added sys.path for local module '{m.group(1)}'")

        # Rule 3: Add __main__ guard to FastAPI apps
        if ('FastAPI' in fixed or 'fastapi' in fixed.lower()) and "if __name__" not in fixed:
            main_block = (
                '\n\n# [FAROS-AUTOFIX] added entry point\n'
                'if __name__ == "__main__":\n'
                '    import uvicorn\n'
                '    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)\n'
            )
            fixed += main_block
            desc_parts.append("added uvicorn __main__ guard")

        # Rule 4: Syntax check failed — fix specific line
        if "SyntaxError" in stderr:
            m = re.search(r'line (\d+)', stderr)
            if m:
                line_num = int(m.group(1))
                lines = fixed.split("\n")
                if line_num <= len(lines):
                    bad_line = lines[line_num - 1]
                    if bad_line.strip().startswith("from ."):
                        lines[line_num - 1] = bad_line.replace("from .", "from ")
                        fixed = "\n".join(lines)
                        desc_parts.append("fixed syntax error at line " + str(line_num))

        # Rule 5: Missing encoding declaration in non-ASCII files
        if any(ord(c) > 127 for c in fixed) and "# -*- coding:" not in fixed:
            fixed = "# -*- coding: utf-8 -*-\n" + fixed
            desc_parts.append("added encoding declaration")

        # Rule 6: Common pip install for missing packages
        if "ModuleNotFoundError" in stderr:
            m = re.search(r"No module named '(\w+)'", stderr)
            if m and m.group(1) in ("requests", "httpx", "aiohttp", "dotenv", "yaml", "toml"):
                # Add a comment suggesting the install
                insert_line = f"# pip install {m.group(1)}"
                if insert_line not in fixed:
                    lines = fixed.split("\n")
                    lines.insert(0, insert_line)
                    fixed = "\n".join(lines)
                    desc_parts.append(f"suggested pip install {m.group(1)}")

        # Rule 7: Fix f-string syntax errors (common in Python 3.6+)
        if "SyntaxError" in stderr and "f-string" in stderr.lower():
            # Try to quote the f-string properly
            m = re.search(r'f[\'"](.+?)[\'"]', stderr)
            if m:
                logger.info("Detected f-string syntax error: %s", m.group(0)[:60])

        if fixed != original:
            return FixResult(
                step_name=step_name,
                error_before=stderr[:500],
                file_path=file_path,
                fix_description="; ".join(desc_parts) if desc_parts else "Applied deterministic fixes",
                original_content=original,
                new_content=fixed,
                method="deterministic",
            )
        return None

    def _generate_llm_fix(
        self,
        client: ProviderClient,
        step_name: str,
        file_path: str,
        original_content: str,
        stderr: str,
        stdout: str,
    ) -> FixResult:
        """Send error + code to LLM, receive fix."""
        prompt = (
            f"You are fixing a broken Python file in a project pipeline.\n\n"
            f"## Failed Step: {step_name}\n\n"
            f"### Error Output (stderr)\n```\n{stderr[:2000]}\n```\n\n"
            + (f"### Stdout\n```\n{stdout[:800]}\n```\n\n" if stdout.strip() else "")
            + f"### File to Fix: `{file_path}`\n```python\n{original_content[:4000]}\n```\n\n"
            + "Return ONLY the corrected file content inside a ```python code fence. "
            + "Do not explain. Fix syntax, relative imports, missing imports, and add __main__ if needed."
        )

        try:
            response = client.chat(
                messages=[ChatMessage(role="user", content=prompt)],
                model=self.model,
                temperature=0.1,
                max_tokens=4000,
            )
        except ProviderError as e:
            logger.error("LLM fix request failed: %s", e)
            return FixResult(
                step_name=step_name,
                error_before=stderr[:500],
                file_path=file_path,
                fix_description=f"Provider error: {e}",
                new_content="",
            )

        fix_text = response.text
        if not fix_text:
            return FixResult(
                step_name=step_name, error_before=stderr[:500],
                file_path=file_path, fix_description="LLM returned empty response",
                new_content="",
            )

        new_content = self._extract_code_block(fix_text)
        if not new_content:
            # LLM returned explanation without code block — try to use the whole response
            if "import " in fix_text and "def " in fix_text:
                new_content = fix_text.strip()
            else:
                return FixResult(
                    step_name=step_name, error_before=stderr[:500],
                    file_path=file_path,
                    fix_description=f"No code block found in LLM response ({len(fix_text)} chars)",
                    new_content="",
                )

        desc = self._summarize_fix(original_content, new_content)

        return FixResult(
            step_name=step_name,
            error_before=stderr[:500],
            file_path=file_path,
            fix_description=desc,
            original_content=original_content,
            new_content=new_content,
            method="llm",
        )

    @staticmethod
    def _generate_diff(file_path: str, original: str, fixed: str) -> List[str]:
        """Generate a simple unified diff between original and fixed content."""
        import difflib
        diff = list(difflib.unified_diff(
            original.splitlines(keepends=True),
            fixed.splitlines(keepends=True),
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
        ))
        return diff

    @staticmethod
    def _extract_code_block(text: str) -> Optional[str]:
        """Extract code from ```python ... ``` fence."""
        pattern = re.compile(r'```(?:python)?\s*\n(.*?)```', re.DOTALL)
        m = pattern.search(text)
        if m:
            return m.group(1).strip()
        return None

    @staticmethod
    def _summarize_fix(original: str, fixed: str) -> str:
        """Generate a brief summary of what changed."""
        if not original or not fixed:
            return "No change"
        if original == fixed:
            return "No change detected"
        orig_lines = original.split("\n")
        fix_lines = fixed.split("\n")
        added = len(fix_lines) - len(orig_lines)
        changes = []
        if added > 0:
            changes.append(f"+{added} lines")
        elif added < 0:
            changes.append(f"{added} lines")
        if "from ." in original and "from ." not in fixed:
            changes.append("fixed relative imports")
        if "import uvicorn" not in original and "import uvicorn" in fixed:
            changes.append("added uvicorn runner")
        return ", ".join(changes) if changes else f"{max(len(original), len(fixed))} chars rewritten"


# ---- Backup / Rollback helpers ----

def _backup_file(file_path: Path) -> Optional[str]:
    """Create a backup of a file before modifying it.

    Returns the backup path, or None if backup failed.
    """
    if not file_path.is_file():
        return None
    backup_path = Path(str(file_path) + BACKUP_EXT)
    try:
        content = file_path.read_bytes()
        backup_path.write_bytes(content)
        logger.debug("Backup created: %s", backup_path)
        return str(backup_path)
    except OSError as exc:
        logger.warning("Failed to create backup for %s: %s", file_path, exc)
        return None


def rollback_file(file_path: str) -> bool:
    """Restore a file from its backup. Returns True if restored."""
    fp = Path(file_path)
    backup_path = Path(str(fp) + BACKUP_EXT)
    if not backup_path.is_file():
        logger.debug("No backup found for %s", file_path)
        return False
    try:
        content = backup_path.read_bytes()
        fp.write_bytes(content)
        backup_path.unlink()
        logger.info("Rolled back: %s", file_path)
        return True
    except OSError as exc:
        logger.error("Failed to rollback %s: %s", file_path, exc)
        return False


def list_backups(repo_dir: str) -> list[str]:
    """List all backup files in a repo directory."""
    backups = []
    for bp in Path(repo_dir).rglob(f"*{BACKUP_EXT}"):
        backups.append(str(bp))
    return backups


# Singleton
_repair_service: Optional[CodeRepairService] = None


def get_code_repair_service() -> CodeRepairService:
    global _repair_service
    if _repair_service is None:
        _repair_service = CodeRepairService()
    return _repair_service
