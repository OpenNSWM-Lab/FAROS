"""
Skills Registry — Stable interface for agent tools/skills.

Each skill has a name, description, and execute() method.
Skills degrade gracefully if network/resources are unavailable.
"""

import json
import os
import re
import logging
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Workspace root for security boundary
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
WORKSPACE_ROOT = os.path.join(_BASE_DIR, "data")

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
    description = "Plan a project file tree from plan context (project-grade, 45+ files)"

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
            resp = self.client.chat(messages=msgs, model=self.model, temperature=0.4, max_tokens=6000)
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

    def execute(self, project_root: str = "", language: str = "python", files: Dict[str, str] = None, **kwargs) -> SkillResult:
        if not files:
            return SkillResult(ok=False, error="No files provided")

        issues = []
        paths = set(files.keys())
        score = 100  # quality score, deduct for issues

        # ── Required files check ──
        required_root = ["README.md"]
        if language.lower() == "python":
            required_root.extend(["requirements.txt", ".gitignore"])
        else:
            required_root.extend(["package.json", ".gitignore"])

        for req in required_root:
            if req not in paths:
                issues.append({"file": req, "severity": "warning", "message": f"Missing required root file: {req}"})
                score -= 3

        # ── Structure categories ──
        test_files = [p for p in paths if "test" in p.lower() and p.endswith(".py" if language.lower() == "python" else ".ts")]
        config_files = [p for p in paths if any(p.endswith(e) for e in [".yml", ".yaml", ".toml", ".cfg", ".ini", ".json", ".env", ".example"])]
        doc_files = [p for p in paths if p.endswith(".md") or p.startswith("docs/")]
        ci_files = [p for p in paths if ".github" in p or "ci" in p.lower()]
        db_files = [p for p in paths if "db" in p.lower() or "model" in p.lower() or "migration" in p.lower()]
        source_files = [p for p in paths if p.endswith(".py" if language.lower() == "python" else ".ts") and "test" not in p.lower()]

        # ── Category thresholds ──
        if len(test_files) < 3:
            issues.append({"file": "*", "severity": "warning", "message": f"Only {len(test_files)} test files (expected >= 5)"})
            score -= 5
        if len(doc_files) < 2:
            issues.append({"file": "*", "severity": "warning", "message": f"Only {len(doc_files)} doc files (expected >= 3)"})
            score -= 3
        if len(ci_files) < 1:
            issues.append({"file": "*", "severity": "warning", "message": "No CI/CD configuration found"})
            score -= 5
        if len(db_files) < 1:
            issues.append({"file": "*", "severity": "warning", "message": "No database layer files found"})
            score -= 5

        # ── File count check ──
        total = len(paths)
        if total < 20:
            issues.append({"file": "*", "severity": "error", "message": f"Only {total} files (expected >= 40 for project-grade)"})
            score -= 20
        elif total < 40:
            issues.append({"file": "*", "severity": "warning", "message": f"Only {total} files (target >= 45 for project-grade)"})
            score -= 10

        # ── Python-specific checks ──
        if language.lower() == "python":
            for path, content in files.items():
                if not path.endswith(".py"):
                    continue
                lines = content.split("\n")

                # Broken import check
                for i, line in enumerate(lines[:60], 1):
                    stripped = line.strip()
                    if stripped.startswith("import ") or stripped.startswith("from "):
                        if stripped.endswith(",") or stripped.count("(") != stripped.count(")"):
                            issues.append({"file": path, "line": i, "severity": "error", "message": f"Broken import: {stripped[:80]}"})
                            score -= 5

                # TODO/placeholder check
                for i, line in enumerate(lines, 1):
                    stripped = line.strip()
                    if stripped == "pass" and i > 3 and not path.endswith("__init__.py"):
                        ctx = lines[max(0, i-3):i]
                        if any("def " in c or "class " in c for c in ctx):
                            issues.append({"file": path, "line": i, "severity": "warning", "message": "Function/class body is just 'pass' (stub)"})
                            score -= 1
                    # Detect explicit placeholder markers in comments. This catches
                    # `TODO: implement ...`, `FIXME: ...`, `XXX`, `HACK: ...`
                    # comments that signal unfinished work — these are easy to
                    # miss in a glance-through and slow down downstream codegen.
                    if (stripped.startswith("#")
                            and any(marker in stripped for marker in ("TODO:", "FIXME:", "XXX", "HACK:"))):
                        issues.append({
                            "file": path,
                            "line": i,
                            "severity": "info",
                            "message": f"Comment marks unfinished work: {stripped[:80]}",
                        })
        else:
            if "package.json" not in paths:
                issues.append({"file": "package.json", "severity": "warning", "message": "Missing package.json"})

        # ── Empty file check ──
        for path, content in files.items():
            if not content.strip() and not path.endswith("__init__.py") and not path.endswith(".gitkeep"):
                issues.append({"file": path, "severity": "info", "message": "File is empty"})

        # ── README quality ──
        readme = files.get("README.md", "")
        if readme and len(readme.split()) < 50:
            issues.append({"file": "README.md", "severity": "warning", "message": f"README too short ({len(readme.split())} words, expected >= 200)"})
            score -= 5

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
                "db": len(db_files),
            },
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
