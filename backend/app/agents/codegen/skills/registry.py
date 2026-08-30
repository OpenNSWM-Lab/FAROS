"""
Skills Registry — Stable interface for agent tools/skills.

Each skill has a name, description, and execute() method.
Skills degrade gracefully if network/resources are unavailable.
"""

import json
import os
import re
import ast
import tomllib
import logging
import shutil
import shlex
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Workspace root for security boundary
from app.core.paths import get_data_dir

WORKSPACE_ROOT = str(get_data_dir())

# LLM-simulated search skills return JSON arrays; keep output budget generous to avoid truncation.
_JSON_ARRAY_MAX_TOKENS = 2048


def _strip_json_from_markdown(text: str) -> str:
    """Extract JSON from model output, stripping optional markdown fences."""
    t = (text or "").strip()
    if not t:
        return t
    if "```json" in t.lower():
        lower = t.lower()
        idx = lower.find("```json")
        inner = t[idx + 7 :]
        end = inner.find("```")
        if end >= 0:
            inner = inner[:end]
        return inner.strip()
    if "```" in t:
        parts = t.split("```", 2)
        if len(parts) >= 2:
            inner = parts[1].strip()
            if inner.lower().startswith("json"):
                inner = inner[4:].lstrip()
            return inner.strip()
    return t


def _parse_llm_json_array(text: str) -> Optional[List[Any]]:
    """Parse a JSON array from LLM text; tolerate fences and a single object."""
    candidate = _strip_json_from_markdown(text)
    if not candidate.strip():
        return None
    for blob in (candidate, (text or "").strip()):
        if not blob.strip():
            continue
        try:
            data = json.loads(blob)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return [data]
        except json.JSONDecodeError:
            continue
    m = re.search(r"\[[\s\S]*\]", candidate)
    if m:
        try:
            data = json.loads(m.group())
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return [data]
        except json.JSONDecodeError:
            pass
    return None


def _repair_json_array_llm(client, model: str, raw_response: str, key_hint: str) -> Optional[List[Any]]:
    """Ask the model to emit valid JSON only (one repair pass)."""
    if not client or not (raw_response or "").strip():
        return None
    try:
        from app.llm.provider_client import ChatMessage

        fragment = (raw_response or "")[:12000]
        msg = (
            "The following text was supposed to be ONLY a valid JSON array [...] of objects. "
            "It is malformed or truncated.\n\n"
            "Output ONLY the corrected JSON array. Rules:\n"
            "- No markdown, no code fences, no explanation before or after.\n"
            f"- Each object must include: {key_hint}\n"
            "- Use ASCII double quotes for all keys and string values.\n"
            "- Escape any double quote inside a string as \\\".\n"
            "- Keep descriptions short (one line each) so the array is complete.\n\n"
            f"{fragment}"
        )
        resp = client.chat(
            messages=[ChatMessage(role="user", content=msg)],
            model=model,
            temperature=0,
            max_tokens=_JSON_ARRAY_MAX_TOKENS,
        )
        return _parse_llm_json_array(resp.text or "")
    except Exception as e:
        logger.warning(f"JSON repair LLM call failed: {e}")
        return None


@dataclass
class SkillResult:
    ok: bool
    data: Any = None
    error: Optional[str] = None


class BaseSkill:
    name: str = ""
    description: str = ""

    def execute(self, **kwargs) -> SkillResult:
        raise NotImplementedError


class WebSearchSkill(BaseSkill):
    name = "webSearch"
    description = "Search the web for relevant information (LLM-simulated)"

    def __init__(self, llm_client=None, model: str = ""):
        self.client = llm_client
        self.model = model

    def execute(self, query: str = "", **kwargs) -> SkillResult:
        if not self.client:
            return SkillResult(ok=False, error="No LLM client for web search simulation")
        try:
            from app.llm.provider_client import ChatMessage

            q = (query or "").strip()[:2000]
            msgs = [
                ChatMessage(
                    role="user",
                    content=(
                        "You are a research assistant. For the query below, suggest exactly 5 relevant "
                        "academic papers, libraries, or tools.\n\n"
                        f"Query: {q}\n\n"
                        "Reply with ONLY a JSON array (no markdown fences, no other text). "
                        "Each element must be an object with keys \"name\" and \"description\" (strings). "
                        "Keep each description under 200 characters. "
                        "Use ASCII double quotes only; escape any \" inside a value as \\\"."
                    ),
                )
            ]
            resp = self.client.chat(
                messages=msgs,
                model=self.model,
                temperature=0.3,
                max_tokens=_JSON_ARRAY_MAX_TOKENS,
            )
            raw = (resp.text or "").strip()
            parsed = _parse_llm_json_array(raw)
            if parsed is None:
                parsed = _repair_json_array_llm(
                    self.client,
                    self.model,
                    raw,
                    '"name" (string), "description" (string)',
                )
            if parsed is None:
                return SkillResult(
                    ok=False,
                    error="Web search simulation: model did not return parseable JSON array",
                )
            return SkillResult(ok=True, data=parsed)
        except Exception as e:
            logger.warning(f"WebSearch skill degraded: {e}")
            return SkillResult(ok=False, error=f"Web search unavailable: {e}")


class GithubSearchSkill(BaseSkill):
    name = "githubSearch"
    description = "Search GitHub for relevant repositories (LLM-simulated)"

    def __init__(self, llm_client=None, model: str = ""):
        self.client = llm_client
        self.model = model

    def execute(self, query: str = "", **kwargs) -> SkillResult:
        if not self.client:
            return SkillResult(ok=False, error="No LLM client for GitHub search simulation")
        try:
            from app.llm.provider_client import ChatMessage

            q = (query or "").strip()[:2000]
            msgs = [
                ChatMessage(
                    role="user",
                    content=(
                        "Suggest 3 to 5 relevant open-source GitHub repositories for the topic below.\n\n"
                        f"Topic: {q}\n\n"
                        "Reply with ONLY a JSON array (no markdown fences, no other text). "
                        "Each element must be an object with keys \"repo\" (owner/name), "
                        "\"description\" (string), and \"language\" (string). "
                        "Keep descriptions short. "
                        "Use ASCII double quotes only; escape any \" inside a value as \\\"."
                    ),
                )
            ]
            resp = self.client.chat(
                messages=msgs,
                model=self.model,
                temperature=0.3,
                max_tokens=_JSON_ARRAY_MAX_TOKENS,
            )
            raw = (resp.text or "").strip()
            parsed = _parse_llm_json_array(raw)
            if parsed is None:
                parsed = _repair_json_array_llm(
                    self.client,
                    self.model,
                    raw,
                    '"repo" (string), "description" (string), "language" (string)',
                )
            if parsed is None:
                return SkillResult(
                    ok=False,
                    error="GitHub search simulation: model did not return parseable JSON array",
                )
            return SkillResult(ok=True, data=parsed)
        except Exception as e:
            logger.warning(f"GithubSearch skill degraded: {e}")
            return SkillResult(ok=False, error=f"GitHub search unavailable: {e}")


class GithubFetchRepoSkill(BaseSkill):
    name = "githubFetchRepo"
    description = "Fetch public GitHub repo structure (LLM-simulated)"

    def __init__(self, llm_client=None, model: str = ""):
        self.client = llm_client
        self.model = model

    def execute(self, url: str = "", **kwargs) -> SkillResult:
        return SkillResult(ok=False, error="GitHub repo fetch not available in current environment (graceful degradation)")


class ReadLocalFileSkill(BaseSkill):
    name = "readLocalFile"
    description = "Read a file from the allowed workspace"

    def execute(self, path: str = "", **kwargs) -> SkillResult:
        if not path:
            return SkillResult(ok=False, error="No path provided")
        abs_path = os.path.realpath(os.path.join(WORKSPACE_ROOT, path))
        if not abs_path.startswith(os.path.realpath(WORKSPACE_ROOT)):
            return SkillResult(ok=False, error="Path outside workspace root")
        if not os.path.isfile(abs_path):
            return SkillResult(ok=False, error=f"File not found: {path}")
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(100_000)
            return SkillResult(ok=True, data={"path": path, "content": content, "size": os.path.getsize(abs_path)})
        except Exception as e:
            return SkillResult(ok=False, error=str(e))


class WriteProjectFilesSkill(BaseSkill):
    name = "writeProjectFiles"
    description = "Atomically write files to a code project"

    def execute(self, project_id: str = "", files: List[Dict] = None, **kwargs) -> SkillResult:
        if not project_id or not files:
            return SkillResult(ok=False, error="project_id and files required")
        try:
            from app.services.code_project_service import write_project_files
            from app.db.engine import get_session_context
            with get_session_context() as db:
                file_count, total_bytes = write_project_files(db, project_id, files)
            return SkillResult(ok=True, data={"fileCount": file_count, "totalBytes": total_bytes})
        except Exception as e:
            return SkillResult(ok=False, error=str(e))


class SummarizeSkill(BaseSkill):
    name = "summarize"
    description = "Summarize text using LLM"

    def __init__(self, llm_client=None, model: str = ""):
        self.client = llm_client
        self.model = model

    def execute(self, text: str = "", **kwargs) -> SkillResult:
        if not self.client or not text:
            return SkillResult(ok=False, error="No client or text")
        try:
            from app.llm.provider_client import ChatMessage
            msgs = [ChatMessage(role="user", content=f"Summarize in 2-3 sentences:\n\n{text[:3000]}")]
            resp = self.client.chat(messages=msgs, model=self.model, temperature=0.2, max_tokens=256)
            return SkillResult(ok=True, data=resp.text.strip())
        except Exception as e:
            return SkillResult(ok=False, error=str(e))


class PlanFileTreeSkill(BaseSkill):
    name = "planFileTree"
    description = "Plan a focused, reproducible scientific experiment repository"

    def __init__(self, llm_client=None, model: str = ""):
        self.client = llm_client
        self.model = model

    def execute(self, plan_context: str = "", language: str = "python", framework: str = "", **kwargs) -> SkillResult:
        if not self.client:
            return SkillResult(ok=False, error="No LLM client")
        try:
            from app.llm.provider_client import ChatMessage
            from app.agents.codegen.kernel import FILE_TREE_PROMPT
            prompt = FILE_TREE_PROMPT.format(
                design_doc=plan_context[:3000],
                language=language,
                framework=framework,
            )
            msgs = [ChatMessage(role="user", content=prompt)]
            resp = self.client.chat(
                messages=msgs,
                model=self.model,
                temperature=0.3,
                max_tokens=5000,
                structured_output=True,
            )
            text = resp.text.strip()
            if "```" in text:
                text = text.split("```json")[-1].split("```")[0] if "```json" in text else text.split("```")[1].split("```")[0]
            parsed = json.loads(text.strip())
            return SkillResult(ok=True, data=parsed)
        except Exception as e:
            return SkillResult(ok=False, error=str(e))


class CompileCheckSkill(BaseSkill):
    name = "compileCheck"
    description = "Thorough structural + quality verification of project files"

    _HEAVY_OPTIONAL_MODULES = {
        "torch",
        "tensorflow",
        "transformers",
        "sentence_transformers",
        "spacy",
    }

    @staticmethod
    def _test_has_assertion(node: ast.AST) -> bool:
        """Recognise plain asserts, pytest.raises, and unittest-style assertions."""
        for child in ast.walk(node):
            if isinstance(child, ast.Assert):
                return True
            if not isinstance(child, ast.Call):
                continue
            target = child.func
            if isinstance(target, ast.Attribute):
                if target.attr == "raises" or target.attr.startswith("assert"):
                    return True
        return False

    def execute(self, project_root: str = "", language: str = "python", files: Dict[str, str] = None, **kwargs) -> SkillResult:
        if not files:
            return SkillResult(ok=False, error="No files provided")

        issues = []
        paths = set(files.keys())
        score = 100  # quality score, deduct for issues

        # ── Required files check ──
        required_root = ["README.md"]
        if language.lower() == "python":
            required_root.extend([".gitignore"])
        else:
            required_root.extend(["package.json", ".gitignore"])

        for req in required_root:
            if req not in paths:
                issues.append({"file": req, "severity": "error", "message": f"Missing required root file: {req}"})
                score -= 10

        if language.lower() == "python":
            has_requirements = bool((files.get("requirements.txt") or "").strip())
            pyproject = files.get("pyproject.toml") or ""
            pyproject_valid = False
            parsed_pyproject = {}
            if pyproject.strip():
                try:
                    parsed_pyproject = tomllib.loads(pyproject)
                    pyproject_valid = bool(parsed_pyproject.get("project") or parsed_pyproject.get("build-system"))
                except tomllib.TOMLDecodeError as exc:
                    issues.append({"file": "pyproject.toml", "severity": "error", "message": f"Invalid TOML: {exc}"})
                    score -= 10
            if not has_requirements and not pyproject_valid:
                issues.append({
                    "file": "pyproject.toml",
                    "severity": "error",
                    "message": "Python dependencies are not installable: provide requirements.txt or a valid pyproject project/build-system table",
                })
                score -= 15

            core_dependencies = list((parsed_pyproject.get("project") or {}).get("dependencies") or [])
            core_dependencies.extend(
                line.strip()
                for line in (files.get("requirements.txt") or "").splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            )
            for dependency in core_dependencies:
                package = re.split(r"[<>=!~;\[\s]", str(dependency), maxsplit=1)[0]
                normalized = package.strip().lower().replace("-", "_")
                if normalized in self._HEAVY_OPTIONAL_MODULES:
                    issues.append({
                        "file": "pyproject.toml" if pyproject.strip() else "requirements.txt",
                        "severity": "error",
                        "message": (
                            f"Heavy dependency '{package}' is required by the default install; "
                            "move it to an optional full-mode extra so the offline smoke path stays lightweight"
                        ),
                    })
                    score -= 10

        # ── Structure categories ──
        test_files = [p for p in paths if "test" in p.lower() and p.endswith(".py" if language.lower() == "python" else ".ts")]
        config_files = [p for p in paths if any(p.endswith(e) for e in [".yml", ".yaml", ".toml", ".cfg", ".ini", ".json", ".env", ".example"])]
        doc_files = [p for p in paths if p.endswith(".md") or p.startswith("docs/")]
        ci_files = [
            p for p in paths
            if p.startswith(".github/workflows/") or p.startswith("ci/")
        ]
        source_files = [p for p in paths if p.endswith(".py" if language.lower() == "python" else ".ts") and "test" not in p.lower()]

        # ── Category thresholds ──
        if len(test_files) < 3:
            issues.append({"file": "*", "severity": "warning", "message": f"Only {len(test_files)} test files (expected >= 3)"})
            score -= 5
        if len(doc_files) < 2:
            issues.append({"file": "*", "severity": "warning", "message": f"Only {len(doc_files)} doc files (expected >= 3)"})
            score -= 3
        if len(ci_files) < 1:
            issues.append({"file": "*", "severity": "warning", "message": "No CI/CD configuration found"})
            score -= 5
        evidence_files = [p for p in paths if any(token in p.lower() for token in ("metric", "evaluation", "manifest", "provenance"))]
        if len(evidence_files) < 2:
            issues.append({"file": "*", "severity": "warning", "message": "Experiment evidence outputs are underspecified"})
            score -= 5

        # ── File count check ──
        total = len(paths)
        if total < 15:
            issues.append({"file": "*", "severity": "error", "message": f"Only {total} files (expected >= 15 for a reproducible experiment)"})
            score -= 20
        elif total < 20:
            issues.append({"file": "*", "severity": "warning", "message": f"Only {total} files (target >= 20 for a reproducible experiment)"})
            score -= 10

        # ── Python-specific checks ──
        if language.lower() == "python":
            parsed_trees = {}
            for path, content in files.items():
                if not path.endswith(".py"):
                    continue
                lines = content.split("\n")

                try:
                    tree = ast.parse(content, filename=path)
                    parsed_trees[path] = tree
                except SyntaxError as exc:
                    issues.append({
                        "file": path,
                        "line": exc.lineno,
                        "severity": "error",
                        "message": f"Python syntax error: {exc.msg}",
                    })
                    score -= 10
                    continue

                abstract_stub_lines = {
                    child.lineno
                    for node in ast.walk(tree)
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and any(
                        (isinstance(decorator, ast.Name) and decorator.id == "abstractmethod")
                        or (isinstance(decorator, ast.Attribute) and decorator.attr == "abstractmethod")
                        for decorator in node.decorator_list
                    )
                    for child in node.body
                    if isinstance(child, ast.Pass)
                }

                # Importing the package and running smoke tests must not require a
                # heavyweight model stack. Full-mode implementations can still use
                # these packages through lazy imports inside methods/functions.
                for node in tree.body:
                    imported_root = None
                    if isinstance(node, ast.Import) and node.names:
                        imported_root = node.names[0].name.split(".", 1)[0]
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        imported_root = node.module.split(".", 1)[0]
                    if imported_root in self._HEAVY_OPTIONAL_MODULES:
                        issues.append({
                            "file": path,
                            "line": getattr(node, "lineno", None),
                            "severity": "error",
                            "message": (
                                f"Heavy optional dependency '{imported_root}' is imported at module load; "
                                "move it inside the full-mode implementation so offline smoke tests can import"
                            ),
                        })
                        score -= 10

                if path.startswith("tests/") or "/tests/" in path:
                    imports_project_code = any(
                        isinstance(node, ast.ImportFrom)
                        and bool(node.module)
                        and (node.module == "src" or node.module.startswith("src."))
                        for node in ast.walk(tree)
                    )
                    invokes_project_script = "scripts/" in content and "subprocess" in content
                    if not imports_project_code and not invokes_project_script:
                        issues.append({
                            "file": path,
                            "severity": "error",
                            "message": "Test file does not import or execute production project code",
                        })
                        score -= 10
                    for node in tree.body:
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            is_fixture = any(
                                (isinstance(decorator, ast.Name) and decorator.id == "fixture")
                                or (isinstance(decorator, ast.Attribute) and decorator.attr == "fixture")
                                for decorator in node.decorator_list
                            )
                            if not node.name.startswith(("test_", "_")) and not is_fixture:
                                issues.append({
                                    "file": path,
                                    "line": getattr(node, "lineno", None),
                                    "severity": "error",
                                    "message": (
                                        f"Test helper '{node.name}' looks like a reimplementation; "
                                        "exercise production code or make a narrowly scoped private helper"
                                    ),
                                })
                                score -= 5
                    for node in ast.walk(tree):
                        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            continue
                        if not node.name.startswith("test_"):
                            continue
                        if not self._test_has_assertion(node):
                            issues.append({
                                "file": path,
                                "line": getattr(node, "lineno", None),
                                "severity": "error",
                                "message": f"Test '{node.name}' has no executable assertion",
                            })
                            score -= 5

                # TODO/placeholder check
                for i, line in enumerate(lines, 1):
                    stripped = line.strip()
                    if stripped == "pass" and i > 3 and not path.endswith("__init__.py"):
                        if i in abstract_stub_lines:
                            continue
                        ctx = lines[max(0, i-3):i]
                        if any("def " in c or "class " in c for c in ctx):
                            severity = "error" if path.startswith("tests/") else "warning"
                            issues.append({"file": path, "line": i, "severity": severity, "message": "Function/class body is just 'pass' (stub)"})
                            score -= 5 if severity == "error" else 1

            internal_roots = {"src"}
            internal_roots.update(
                path.split("/", 2)[1]
                for path in paths
                if path.startswith("src/") and path.count("/") >= 2
            )

            def module_exists(module: str) -> bool:
                module_path = module.replace(".", "/")
                candidates = {
                    f"{module_path}.py",
                    f"{module_path}/__init__.py",
                    f"src/{module_path}.py",
                    f"src/{module_path}/__init__.py",
                }
                return bool(candidates & paths)

            for path, tree in parsed_trees.items():
                package_parts = path.removesuffix(".py").split("/")[:-1]
                for node in ast.walk(tree):
                    if not isinstance(node, ast.ImportFrom) or not node.module:
                        continue
                    module = node.module
                    if node.level:
                        keep = max(0, len(package_parts) - node.level + 1)
                        module = ".".join(package_parts[:keep] + module.split("."))
                        is_internal = True
                    else:
                        is_internal = module.split(".", 1)[0] in internal_roots
                    if is_internal and not module_exists(module):
                        issues.append({
                            "file": path,
                            "line": getattr(node, "lineno", None),
                            "severity": "error",
                            "message": f"Internal import does not resolve to a planned file: {module}",
                        })
                        score -= 10
        else:
            if "package.json" not in paths:
                issues.append({"file": "package.json", "severity": "warning", "message": "Missing package.json"})

        # ── Empty file check ──
        for path, content in files.items():
            if not content.strip() and not path.endswith("__init__.py") and not path.endswith(".gitkeep"):
                issues.append({"file": path, "severity": "info", "message": "File is empty"})

        # ── README quality ──
        readme = files.get("README.md", "")
        if readme and len(readme.split()) < 100:
            issues.append({"file": "README.md", "severity": "error", "message": f"README too short ({len(readme.split())} words, expected >= 100)"})
            score -= 10

        for path in ci_files:
            content = files.get(path, "")
            if path.endswith((".yml", ".yaml")) and "jobs:" not in content:
                issues.append({"file": path, "severity": "error", "message": "CI workflow has no jobs definition"})
                score -= 10

        for path, content in files.items():
            lowered = content.lower()
            if "generation failed:" in lowered:
                issues.append({"file": path, "severity": "error", "message": "File contains a generation-failure fallback"})
                score -= 10
            is_experiment_metadata = path.startswith(("configs/", "data/", "evidence/"))
            if is_experiment_metadata and any(
                marker in lowered
                for marker in (
                    "placeholder",
                    "replace with actual",
                    "s3://scirev-benchmark",
                )
            ):
                issues.append({"file": path, "severity": "error", "message": "Experiment metadata contains placeholder or fabricated provenance"})
                score -= 10
            if is_experiment_metadata and "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" in lowered:
                issues.append({"file": path, "severity": "error", "message": "Dataset manifest uses the SHA-256 checksum of an empty file"})
                score -= 10
            if path.endswith((".py", ".ts")) and any(
                marker in lowered
                for marker in (
                    "placeholder implementation",
                    "example path",
                    "simplified scoring logic for demonstration",
                )
            ):
                issues.append({"file": path, "severity": "error", "message": "Executable source still contains placeholder experiment logic"})
                score -= 10

        has_errors = any(i["severity"] == "error" for i in issues)
        score = max(0, min(100, score))

        return SkillResult(ok=not has_errors, data={
            "issues": issues,
            "fileCount": len(files),
            "errorCount": sum(1 for i in issues if i["severity"] == "error"),
            "warningCount": sum(1 for i in issues if i["severity"] == "warning"),
            "qualityScore": score,
            "categories": {
                "source": len(source_files),
                "tests": len(test_files),
                "config": len(config_files),
                "docs": len(doc_files),
                "ci": len(ci_files),
                "evidence": len(evidence_files),
            },
        })


class ExecutionSmokeCheckSkill(BaseSkill):
    """Execute the generated smoke path in an offline, resource-limited container."""

    name = "executionSmokeCheck"
    description = "Run the generated offline smoke script in a locked-down Docker sandbox"

    _SMOKE_CANDIDATES = ("scripts/smoke_test.py", "scripts/run_smoke.py")

    def execute(self, files: Dict[str, str] = None, **kwargs) -> SkillResult:
        files = files or {}
        smoke_path = next((path for path in self._SMOKE_CANDIDATES if path in files), None)
        if not smoke_path:
            issue = {
                "file": "scripts/run_smoke.py",
                "severity": "error",
                "message": "No executable offline smoke script was generated",
            }
            return SkillResult(ok=False, data={"status": "failed", "issues": [issue], "errorCount": 1})

        docker = shutil.which("docker")
        if not docker:
            issue = {
                "file": smoke_path,
                "severity": "error",
                "message": "Docker is required for the isolated executable smoke gate but is unavailable",
            }
            return SkillResult(ok=False, data={"status": "unavailable", "issues": [issue], "errorCount": 1})

        smoke_image = os.getenv("FAROS_CODEGEN_SMOKE_IMAGE", "python:3.12-slim")
        test_image = os.getenv("FAROS_CODEGEN_TEST_IMAGE", "faros/codegen-test:3.12")
        image_probe = subprocess.run(
            [docker, "image", "inspect", test_image],
            capture_output=True,
            timeout=10,
            check=False,
        )
        run_full_tests = image_probe.returncode == 0
        if os.getenv("FAROS_CODEGEN_REQUIRE_TESTS", "0") == "1" and not run_full_tests:
            issue = {
                "file": smoke_path,
                "severity": "error",
                "message": (
                    f"Required code-generation test image '{test_image}' is unavailable. "
                    "Build it with scripts/build_codegen_test_image.sh"
                ),
            }
            return SkillResult(ok=False, data={"status": "unavailable", "issues": [issue], "errorCount": 1})

        image = test_image if run_full_tests else smoke_image
        smoke_command = f"python {shlex.quote(smoke_path)}"
        execution_args = (
            ["sh", "-c", f"{smoke_command} && python -m pytest -q"]
            if run_full_tests
            else ["python", smoke_path]
        )
        display_command = (
            f"{smoke_command} && python -m pytest -q"
            if run_full_tests
            else smoke_command
        )
        container_name = f"faros-codegen-smoke-{uuid.uuid4().hex[:12]}"
        started = time.monotonic()
        with tempfile.TemporaryDirectory(
            prefix="faros-codegen-smoke-",
            ignore_cleanup_errors=True,
        ) as temp_dir:
            root = Path(temp_dir)
            # Docker Desktop may remap container root to an unprivileged host
            # UID. The directory is an isolated disposable copy, so allow the
            # smoke run to emit evidence artifacts inside that copy.
            root.chmod(0o777)
            for relative, content in files.items():
                target = (root / relative).resolve()
                if root.resolve() not in target.parents:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                target.chmod(0o644)

            command = [
                docker,
                "run",
                "--rm",
                "--name",
                container_name,
                "--network",
                "none",
                "--memory",
                "512m",
                "--cpus",
                "0.5",
                "--pids-limit",
                "64",
                "--security-opt",
                "no-new-privileges",
                "--cap-drop",
                "ALL",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=64m",
                "--volume",
                f"{root}:/workspace:rw",
                "--workdir",
                "/workspace",
                image,
                *execution_args,
            ]
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                    env={"PATH": os.environ.get("PATH", "")},
                )
            except subprocess.TimeoutExpired as exc:
                subprocess.run(
                    [docker, "rm", "-f", container_name],
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
                output = ((exc.stdout or "") + "\n" + (exc.stderr or ""))[-4000:]
                issue = {
                    "file": smoke_path,
                    "severity": "error",
                    "message": f"Offline smoke execution timed out after 60 seconds. Output:\n{output}",
                }
                return SkillResult(ok=False, data={
                    "status": "timeout",
                    "command": display_command,
                    "durationMs": int((time.monotonic() - started) * 1000),
                    "testStatus": "timeout" if run_full_tests else "not_run",
                    "issues": [issue],
                    "errorCount": 1,
                })

        duration_ms = int((time.monotonic() - started) * 1000)
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if completed.returncode == 0:
            return SkillResult(ok=True, data={
                "status": "passed",
                "command": display_command,
                "durationMs": duration_ms,
                "exitCode": 0,
                "testStatus": "passed" if run_full_tests else "not_run",
                "stdoutTail": stdout[-2000:],
                "stderrTail": stderr[-2000:],
                "issues": [],
                "errorCount": 0,
            })

        combined = (stdout + "\n" + stderr)[-5000:]
        traceback_paths = []
        for path in re.findall(r'File "/workspace/([^"\n]+)"', combined):
            if path in files and path not in traceback_paths:
                traceback_paths.append(path)
        for path in re.findall(r"(?:^|\n)((?:src|tests|scripts)/[^:\n]+\.py):\d+", combined):
            if path in files and path not in traceback_paths:
                traceback_paths.append(path)
        missing_call = re.search(r"TypeError: ([A-Za-z_]\w*)\(\) missing", combined)
        if missing_call:
            function_name = missing_call.group(1)
            for path, content in files.items():
                if path.endswith(".py") and re.search(rf"^def\s+{re.escape(function_name)}\s*\(", content, re.MULTILINE):
                    if path not in traceback_paths:
                        traceback_paths.append(path)
        class_constructor = re.search(r"TypeError: ([A-Za-z_]\w*)\.__init__\(\)", combined)
        if class_constructor:
            class_name = class_constructor.group(1)
            for path, content in files.items():
                if path.endswith(".py") and re.search(rf"^class\s+{re.escape(class_name)}\b", content, re.MULTILINE):
                    if path not in traceback_paths:
                        traceback_paths.append(path)
        affected = traceback_paths[:5] or [smoke_path]
        issues = []
        for index, path in enumerate(affected):
            detail = combined if index == 0 else "See the runtime traceback attached to the first affected file."
            issues.append({
                "file": path,
                "severity": "error",
                "message": (
                    f"Offline Docker {'test suite' if run_full_tests else 'smoke'} failed with exit code "
                    f"{completed.returncode}. Fix the runtime contract, not just the assertion. Output:\n{detail}"
                ),
            })
        return SkillResult(ok=False, data={
            "status": "failed",
            "command": display_command,
            "durationMs": duration_ms,
            "exitCode": completed.returncode,
            "testStatus": "failed" if run_full_tests else "not_run",
            "stdoutTail": stdout[-2000:],
            "stderrTail": stderr[-2000:],
            "issues": issues,
            "errorCount": len(issues),
        })


# ── NEW SKILLS: skill-creator, document-skills, find-skill, frontend-design, code-simplifier, ralph-loop ──

class SkillCreatorSkill(BaseSkill):
    """Generate modular functional units of code from a specification."""
    name = "skill-creator"
    description = "Generate modular functional code units (functions, classes, modules) from a natural-language spec"

    def __init__(self, llm_client=None, model: str = ""):
        self.client = llm_client
        self.model = model

    def execute(self, spec: str = "", language: str = "python", module_name: str = "module", **kwargs) -> SkillResult:
        if not self.client or not spec:
            return SkillResult(ok=False, error="LLM client and spec required")
        try:
            from app.llm.provider_client import ChatMessage
            prompt = (
                f"You are an expert {language} developer. Generate a complete, production-ready "
                f"module called `{module_name}` based on this specification:\n\n{spec[:4000]}\n\n"
                f"Requirements:\n"
                f"- Include all imports\n- Add type hints and docstrings\n"
                f"- Include error handling\n- Make it self-contained and testable\n\n"
                f"Return strict JSON: {{\"module_name\": \"{module_name}\", "
                f"\"code\": \"full code\", \"exports\": [\"list of public names\"], "
                f"\"dependencies\": [\"list of pip packages\"]}}\n\nReturn ONLY valid JSON."
            )
            msgs = [ChatMessage(role="user", content=prompt)]
            resp = self.client.chat(messages=msgs, model=self.model, temperature=0.3, max_tokens=4000)
            text = resp.text.strip()
            if "```" in text:
                text = text.split("```json")[-1].split("```")[0] if "```json" in text else text.split("```")[1].split("```")[0]
            parsed = json.loads(text.strip())
            return SkillResult(ok=True, data=parsed)
        except Exception as e:
            logger.warning(f"skill-creator failed: {e}")
            return SkillResult(ok=False, error=str(e))


class DocumentSkillsSkill(BaseSkill):
    """Summarize, restructure, or analyze documentation and text."""
    name = "document-skills"
    description = "Summarize, restructure, extract info from, or generate documentation"

    def __init__(self, llm_client=None, model: str = ""):
        self.client = llm_client
        self.model = model

    def execute(self, text: str = "", action: str = "summarize", **kwargs) -> SkillResult:
        """Actions: summarize, restructure, extract_sections, generate_readme, generate_docstring"""
        if not self.client or not text:
            return SkillResult(ok=False, error="LLM client and text required")
        try:
            from app.llm.provider_client import ChatMessage
            action_prompts = {
                "summarize": f"Summarize the following text concisely in 3-5 sentences:\n\n{text[:6000]}",
                "restructure": f"Restructure the following text into clear sections with headers:\n\n{text[:6000]}",
                "extract_sections": (
                    f"Extract all section headings and their summaries from:\n\n{text[:6000]}\n\n"
                    f"Return JSON array: [{{\"heading\": \"...\", \"summary\": \"...\"}}]"
                ),
                "generate_readme": (
                    f"Generate a comprehensive README.md from this project description:\n\n{text[:6000]}"
                ),
                "generate_docstring": (
                    f"Generate Python docstrings for all functions/classes in:\n\n{text[:4000]}\n\n"
                    f"Return the code with docstrings added."
                ),
            }
            prompt = action_prompts.get(action, action_prompts["summarize"])
            msgs = [ChatMessage(role="user", content=prompt)]
            resp = self.client.chat(messages=msgs, model=self.model, temperature=0.3, max_tokens=3000)
            result_text = resp.text.strip()
            if action == "extract_sections":
                try:
                    if "```" in result_text:
                        result_text = result_text.split("```json")[-1].split("```")[0] if "```json" in result_text else result_text.split("```")[1].split("```")[0]
                    return SkillResult(ok=True, data=json.loads(result_text.strip()))
                except Exception:
                    pass
            return SkillResult(ok=True, data={"action": action, "result": result_text})
        except Exception as e:
            return SkillResult(ok=False, error=str(e))


class FindSkill(BaseSkill):
    """Search for code patterns, functions, classes, or text in project files."""
    name = "find-skill"
    description = "Search for code patterns, symbols, or text across project files"

    def execute(self, pattern: str = "", files: Dict[str, str] = None, regex: bool = False, **kwargs) -> SkillResult:
        if not pattern or not files:
            return SkillResult(ok=False, error="pattern and files required")
        try:
            matches = []
            for path, content in files.items():
                lines = content.split("\n")
                for i, line in enumerate(lines, 1):
                    if regex:
                        if re.search(pattern, line):
                            matches.append({"file": path, "line": i, "text": line.strip()[:200]})
                    else:
                        if pattern.lower() in line.lower():
                            matches.append({"file": path, "line": i, "text": line.strip()[:200]})
            return SkillResult(ok=True, data={"pattern": pattern, "matchCount": len(matches), "matches": matches[:100]})
        except Exception as e:
            return SkillResult(ok=False, error=str(e))


class FrontendDesignSkill(BaseSkill):
    """Generate UI/UX components and layout specifications."""
    name = "frontend-design"
    description = "Generate React/TSX UI components, layout specs, and styling from requirements"

    def __init__(self, llm_client=None, model: str = ""):
        self.client = llm_client
        self.model = model

    def execute(self, requirements: str = "", framework: str = "react", **kwargs) -> SkillResult:
        if not self.client or not requirements:
            return SkillResult(ok=False, error="LLM client and requirements needed")
        try:
            from app.llm.provider_client import ChatMessage
            prompt = (
                f"You are an expert UI/UX engineer. Generate a complete {framework} component "
                f"based on these requirements:\n\n{requirements[:4000]}\n\n"
                f"Use Tailwind CSS for styling. Include proper TypeScript types. "
                f"Make it accessible and responsive.\n\n"
                f"Return strict JSON: {{\"componentName\": \"...\", \"code\": \"full TSX code\", "
                f"\"props\": [{{\"name\": \"...\", \"type\": \"...\", \"description\": \"...\"}}], "
                f"\"dependencies\": [\"npm packages\"]}}\n\nReturn ONLY valid JSON."
            )
            msgs = [ChatMessage(role="user", content=prompt)]
            resp = self.client.chat(messages=msgs, model=self.model, temperature=0.4, max_tokens=4000)
            text = resp.text.strip()
            if "```" in text:
                text = text.split("```json")[-1].split("```")[0] if "```json" in text else text.split("```")[1].split("```")[0]
            parsed = json.loads(text.strip())
            return SkillResult(ok=True, data=parsed)
        except Exception as e:
            return SkillResult(ok=False, error=str(e))


class CodeSimplifierSkill(BaseSkill):
    """Optimize, clean, and simplify generated code."""
    name = "code-simplifier"
    description = "Refactor and simplify code: remove dead code, improve naming, reduce complexity"

    def __init__(self, llm_client=None, model: str = ""):
        self.client = llm_client
        self.model = model

    def execute(self, code: str = "", language: str = "python", focus: str = "readability", **kwargs) -> SkillResult:
        if not self.client or not code:
            return SkillResult(ok=False, error="LLM client and code required")
        try:
            from app.llm.provider_client import ChatMessage
            prompt = (
                f"You are an expert code reviewer. Simplify and improve this {language} code.\n\n"
                f"Focus: {focus}\n\nOriginal code:\n```{language}\n{code[:6000]}\n```\n\n"
                f"Return strict JSON: {{\"simplified_code\": \"...\", "
                f"\"changes\": [\"list of changes made\"], "
                f"\"complexity_before\": \"high/medium/low\", "
                f"\"complexity_after\": \"high/medium/low\"}}\n\nReturn ONLY valid JSON."
            )
            msgs = [ChatMessage(role="user", content=prompt)]
            resp = self.client.chat(messages=msgs, model=self.model, temperature=0.2, max_tokens=4000)
            text = resp.text.strip()
            if "```" in text:
                text = text.split("```json")[-1].split("```")[0] if "```json" in text else text.split("```")[1].split("```")[0]
            parsed = json.loads(text.strip())
            return SkillResult(ok=True, data=parsed)
        except Exception as e:
            return SkillResult(ok=False, error=str(e))


class RalphLoopSkill(BaseSkill):
    """Agent orchestration reasoning loop with iterative feedback."""
    name = "ralph-loop"
    description = "Iterative reasoning loop: plan -> execute -> evaluate -> refine, with structured feedback"

    def __init__(self, llm_client=None, model: str = ""):
        self.client = llm_client
        self.model = model

    def execute(
        self, task: str = "", context: str = "", previous_attempts: List[Dict] = None,
        max_iterations: int = 3, **kwargs
    ) -> SkillResult:
        if not self.client or not task:
            return SkillResult(ok=False, error="LLM client and task required")
        attempts = previous_attempts or []
        try:
            from app.llm.provider_client import ChatMessage
            for iteration in range(max_iterations):
                history_text = ""
                if attempts:
                    history_text = "\n\nPrevious attempts:\n"
                    for i, att in enumerate(attempts[-3:], 1):
                        history_text += f"\nAttempt {i}:\n- Plan: {att.get('plan', 'N/A')}\n- Result: {att.get('result', 'N/A')[:500]}\n- Feedback: {att.get('feedback', 'N/A')}\n"

                prompt = (
                    f"You are an expert AI agent using iterative reasoning (Ralph Loop).\n\n"
                    f"Task: {task}\n\nContext: {context[:3000]}{history_text}\n\n"
                    f"Iteration {iteration + 1}/{max_iterations}. "
                    f"Produce a plan, execute your reasoning, evaluate the result, "
                    f"and decide if refinement is needed.\n\n"
                    f"Return strict JSON: {{\"plan\": \"...\", \"reasoning\": \"...\", "
                    f"\"result\": \"...\", \"confidence\": 0.0-1.0, "
                    f"\"needs_refinement\": true/false, \"feedback\": \"...\"}}\n\nReturn ONLY valid JSON."
                )
                msgs = [ChatMessage(role="user", content=prompt)]
                resp = self.client.chat(messages=msgs, model=self.model, temperature=0.4, max_tokens=2000)
                text = resp.text.strip()
                if "```" in text:
                    text = text.split("```json")[-1].split("```")[0] if "```json" in text else text.split("```")[1].split("```")[0]
                parsed = json.loads(text.strip())
                attempts.append(parsed)

                if not parsed.get("needs_refinement", False) or parsed.get("confidence", 0) >= 0.85:
                    break

            return SkillResult(ok=True, data={
                "iterations": len(attempts),
                "finalResult": attempts[-1] if attempts else {},
                "allAttempts": attempts,
            })
        except Exception as e:
            return SkillResult(ok=False, error=str(e))


class SkillsRegistry:
    """Central registry of all available skills."""

    def __init__(self, llm_client=None, model: str = ""):
        self._skills: Dict[str, BaseSkill] = {}
        self._disabled: set = set()

        # Register core skills
        self.register(WebSearchSkill(llm_client, model))
        self.register(GithubSearchSkill(llm_client, model))
        self.register(GithubFetchRepoSkill(llm_client, model))
        self.register(ReadLocalFileSkill())
        self.register(WriteProjectFilesSkill())
        self.register(SummarizeSkill(llm_client, model))
        self.register(PlanFileTreeSkill(llm_client, model))
        self.register(CompileCheckSkill())
        self.register(ExecutionSmokeCheckSkill())

        # Register imported agent skills
        self.register(SkillCreatorSkill(llm_client, model))
        self.register(DocumentSkillsSkill(llm_client, model))
        self.register(FindSkill())
        self.register(FrontendDesignSkill(llm_client, model))
        self.register(CodeSimplifierSkill(llm_client, model))
        self.register(RalphLoopSkill(llm_client, model))

    def register(self, skill: BaseSkill):
        self._skills[skill.name] = skill

    def disable(self, name: str):
        self._disabled.add(name)

    def enable(self, name: str):
        self._disabled.discard(name)

    def get(self, name: str) -> Optional[BaseSkill]:
        if name in self._disabled:
            return None
        return self._skills.get(name)

    def execute(self, name: str, **kwargs) -> SkillResult:
        skill = self.get(name)
        if not skill:
            return SkillResult(ok=False, error=f"Skill '{name}' not found or disabled")
        try:
            return skill.execute(**kwargs)
        except Exception as e:
            logger.error(f"Skill {name} failed: {e}")
            return SkillResult(ok=False, error=str(e))

    def list_skills(self) -> List[Dict[str, str]]:
        return [
            {"name": s.name, "description": s.description, "enabled": s.name not in self._disabled}
            for s in self._skills.values()
        ]
