"""
Code Generation Agent Service

Orchestrates multi-step code generation from a CandidatePlan using real LLM.
Pipeline stages:
1) Requirements extraction
2) Repo blueprint (file tree + modules)
3) Code synthesis (multi-file, >= 12 files)
4) Self-check (required files exist)
5) Persist to filesystem + DB

Skills abstraction: searchWeb, summarize, planRepoStructure, synthesizeCode
(Web search / GitHub gracefully degrade if unavailable)
"""

import ast
import json
import re
import os
import sys
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from app.llm.provider_client import get_provider_client, ChatMessage, ProviderError
from app.services.code_project_service import (
    create_project,
    write_project_files,
    CODE_PROJECTS_DIR,
)
from app.models.plan_session import CandidatePlan, PlanSession, PlanSessionConfig, PlanSessionStatus
from app.storage.plan_session_storage import (
    generate_candidate_plan_id,
    generate_plan_session_id,
    get_candidate_storage,
    get_session_storage,
)
from app.db.engine import get_session_context

logger = logging.getLogger(__name__)


# ── Generation status tracking (in-memory) ──────────────────────

_generation_status: Dict[str, Dict[str, Any]] = {}


def get_generation_status(project_id: str) -> Optional[Dict[str, Any]]:
    return _generation_status.get(project_id)


def _set_status(project_id: str, step: str, status: str, detail: str = "", logs: List[str] = None):
    if project_id not in _generation_status:
        _generation_status[project_id] = {
            "projectId": project_id,
            "status": "running",
            "steps": [],
            "logs": [],
            "startedAt": datetime.utcnow().isoformat(),
        }
    entry = _generation_status[project_id]
    entry["steps"].append({"step": step, "status": status, "detail": detail, "timestamp": datetime.utcnow().isoformat()})
    if logs:
        entry["logs"].extend(logs)
    if status == "failed":
        entry["status"] = "failed"
    entry["currentStep"] = step


def _complete_status(project_id: str):
    if project_id in _generation_status:
        _generation_status[project_id]["status"] = "completed"
        _generation_status[project_id]["completedAt"] = datetime.utcnow().isoformat()


# ── JSON extraction helper ──────────────────────────────────────

def _extract_json(text: str) -> Optional[Dict]:
    text = text.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1]
        if "```" in text:
            text = text.rsplit("```", 1)[0]
    elif "```" in text:
        parts = text.split("```")
        if len(parts) >= 3:
            text = parts[1]
        elif len(parts) >= 2:
            text = parts[1]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None


# ── Prompt templates ────────────────────────────────────────────

BLUEPRINT_PROMPT = """You are a senior software architect. Given the following research plan, design a complete project repository structure.

**Plan:**
- Title: {title}
- Abstract: {abstract}
- Method: {method}
- Research Question: {research_question}
- Language: {language}
- Framework: {framework}

**Requirements:**
1. Design a production-quality project with at least 15 files
2. Include: README.md, requirements.txt (or package.json), config files, source modules, tests, docs
3. Organize code into logical modules/packages
4. Include a main entry point
5. This is a scientific experiment, not a web application. Include executable baseline and evaluation code
6. The main entry point must compute results from data or simulation and write metrics.json; never hard-code claimed outcomes
7. Every metrics.json item must contain name, value, unit, definition, and split

**Output (strict JSON):**
```json
{{
  "projectName": "project-name",
  "description": "Brief description",
  "files": [
    {{
      "path": "relative/path/to/file.py",
      "description": "What this file does",
      "type": "source|config|test|doc|data"
    }}
  ]
}}
```

Return ONLY valid JSON. At least 15 files.
"""

CODE_SYNTHESIS_PROMPT = """You are an expert {language} developer. Generate the complete content for the following file in a research project.

**Project:** {project_name}
**Description:** {project_description}
**Research Plan:** {plan_summary}

**File to generate:**
- Path: {file_path}
- Description: {file_description}
- Type: {file_type}

**Other files in project (for context):**
{other_files_context}

**Requirements:**
- Write production-quality, well-structured code
- Include proper imports, docstrings, and type hints
- Make the code functional and runnable
- For config files, use sensible defaults
- For README, include setup instructions, usage, and project structure

Return ONLY the file content. No markdown fences, no explanations.
"""

BATCH_SYNTHESIS_PROMPT = """You are an expert {language} developer. Generate complete file contents for a research project.

**Project:** {project_name} — {project_description}
**Plan:** {plan_summary}

Generate the COMPLETE content for ALL of the following files. Return strict JSON.

**Files to generate:**
{files_list}

**Output format (strict JSON):**
```json
{{
  "files": [
    {{
      "path": "exact/path/from/above",
      "content": "complete file content here"
    }}
  ]
}}
```

Requirements:
- Production-quality code with imports, docstrings, type hints
- Functional and runnable
- Implement the declared scientific baseline and evaluation rather than returning placeholder metrics
- The main entry point must write metrics.json with name, value, unit, definition, and split for each measured metric
- Never invent or hard-code successful metric values
- README with setup/usage/structure
- Config files with sensible defaults
- Test files with real test cases

Return ONLY valid JSON.
"""

SCIENTIFIC_ENTRYPOINT_PROMPT = """Generate one self-contained Python scientific experiment entry point.

Research title: {title}
Research question: {abstract}
Method: {method}
Baseline(s): {baselines}
Required metrics: {metrics}
Datasets: {datasets}

Strict requirements:
1. Return only complete Python source code for src/main.py, without Markdown fences.
2. Use only the Python standard library and NumPy. Do not import any local project module.
3. Use a fixed random seed and a deterministic synthetic-data or computational experiment.
4. Implement both the declared method and baseline. Compute all values at runtime; never hard-code outcomes.
5. Evaluate on a held-out test split when data is involved.
6. Write repo-root metrics.json as a JSON list. Every item must contain name, numeric value, unit, definition, and split.
7. Metric names or splits must distinguish the baseline from the proposed method.
8. Print the same metric list as JSON and exit nonzero on an invalid or non-finite result.
9. Include a main() function and an if __name__ == "__main__" guard.
"""


# ── Agent pipeline ──────────────────────────────────────────────

def generate_project_from_research_candidate(
    candidate: Dict[str, Any],
    *,
    idea_session_id: Optional[str] = None,
    provider_name: str = "qwen",
    model: str = "qwen-max",
    language: str = "python",
    framework: str = "numpy",
    existing_project_id: Optional[str] = None,
) -> str:
    """Bridge an Idea candidate into the established Plan-based Code agent."""

    session_id = generate_plan_session_id()
    candidate_id = generate_candidate_plan_id()
    title = str(candidate.get("title") or "Research Project")
    problem = str(candidate.get("problem") or candidate.get("hypothesisStatement") or "")
    method = str(candidate.get("proposedMethod") or candidate.get("keyInsight") or "")
    specs = candidate.get("experimentSpecs") or candidate.get("requiredExperiments") or []
    metrics = [
        str(metric)
        for spec in specs
        if isinstance(spec, dict)
        for metric in (spec.get("metrics") or [])
    ]
    datasets = [
        str(dataset)
        for spec in specs
        if isinstance(spec, dict)
        for dataset in (spec.get("datasets") or [])
    ]

    plan_candidate = CandidatePlan(
        id=candidate_id,
        sessionId=session_id,
        indexNumber=1,
        title=title,
        planAbstract=problem,
        method=method,
        experimentDesign={
            "research_question": problem,
            "hypothesis": str(candidate.get("hypothesisStatement") or candidate.get("keyInsight") or problem),
            "variables": {},
            "methodology": {"description": method, "metrics": metrics, "datasets": datasets},
            "expected_outcomes": {"metrics": metrics},
        },
        evaluationProtocol={"metrics": metrics, "datasets": datasets},
        baselines=[str(item) for item in (candidate.get("baselines") or [])],
    )
    session = PlanSession(
        id=session_id,
        config=PlanSessionConfig(
            providerName=provider_name,
            model=model,
            ideaSessionId=idea_session_id,
            ideaCandidateId=str(candidate.get("id") or "") or None,
            ideaCandidateTitle=title,
            ideaSeedQuery=problem,
            maxCandidates=1,
        ),
        status=PlanSessionStatus.COMPLETED,
        candidateIds=[candidate_id],
        selectedCandidateId=candidate_id,
    )
    get_session_storage().create(session)
    get_candidate_storage().create(plan_candidate)

    return generate_project_from_plan(
        plan_session_id=session_id,
        candidate_id=candidate_id,
        provider_name=provider_name,
        model=model,
        language=language,
        framework=framework,
        enable_web_search=False,
        enable_github=False,
        existing_project_id=existing_project_id,
        scientific_mode=True,
    )

def generate_project_from_plan(
    plan_session_id: str,
    candidate_id: str,
    provider_name: str = "moonshot",
    model: str = "moonshot-v1-8k",
    language: str = "python",
    framework: str = "FastAPI",
    enable_web_search: bool = False,
    enable_github: bool = False,
    existing_project_id: Optional[str] = None,
    scientific_mode: bool = False,
) -> str:
    """
    Main entry point: generate a code project from a plan candidate.
    Returns project_id. Runs synchronously (call from background task).
    If existing_project_id is provided, reuses that project instead of creating new.
    """
    # Load plan data
    sess_storage = get_session_storage()
    cand_storage = get_candidate_storage()

    plan_session = sess_storage.get(plan_session_id)
    candidate = cand_storage.get(candidate_id)

    if not plan_session:
        raise ValueError(f"Plan session {plan_session_id} not found")
    if not candidate:
        raise ValueError(f"Candidate {candidate_id} not found")

    title = candidate.title or "Research Project"
    abstract = candidate.planAbstract or ""
    method = candidate.method or ""
    rq = ""
    if candidate.experimentDesign and hasattr(candidate.experimentDesign, 'research_question'):
        rq = candidate.experimentDesign.research_question
    elif isinstance(candidate.experimentDesign, dict):
        rq = candidate.experimentDesign.get("research_question", "")

    if existing_project_id:
        project_id = existing_project_id
        # Update project title/description in DB
        from app.db import crud as _crud
        with get_session_context() as db:
            _crud.update_project_v2(db, project_id, {
                "title": f"{title} [{language}]",
                "description": f"{abstract}\n\nMethod: {method}",
            })
    else:
        with get_session_context() as db:
            project = create_project(
                db=db,
                title=f"{title} [{language}]",
                language=language,
                description=f"{abstract}\n\nMethod: {method}",
            )
            project_id = project.id

    _set_status(project_id, "init", "ok", f"Project {project_id} created")

    try:
        client = get_provider_client(provider_name)

        # Step 1: Requirements extraction (implicit from plan)
        _set_status(project_id, "requirements", "ok", "Extracted requirements from plan")

        # Step 2: Optional web search (graceful degradation)
        if enable_web_search:
            try:
                _set_status(project_id, "web_search", "running", "Searching for references...")
                search_result = _skill_web_search(client, model, title, abstract)
                _set_status(project_id, "web_search", "ok", f"Found {len(search_result)} references")
            except Exception as e:
                _set_status(project_id, "web_search", "skipped", f"Web search unavailable: {e}")

        # Step 3: Optional GitHub exploration (graceful degradation)
        if enable_github:
            try:
                _set_status(project_id, "github_explore", "running", "Exploring reference repos...")
                _set_status(project_id, "github_explore", "skipped", "GitHub exploration not available in current environment")
            except Exception as e:
                _set_status(project_id, "github_explore", "skipped", str(e))

        # FAROS scientific runs favor one coherent executable over a large set
        # of independently generated modules. The general Code workspace keeps
        # the established multi-file path.
        if scientific_mode and language.lower() == "python":
            _set_status(project_id, "blueprint", "ok", "Using compact scientific execution blueprint")
            _set_status(project_id, "synthesis", "running", "Generating scientific entrypoint...")
            files_dict = _read_existing_project_files(project_id)
            files_dict["src/main.py"] = _step_synthesize_scientific_entrypoint(
                client=client,
                model=model,
                title=title,
                abstract=abstract,
                method=method,
                candidate=candidate,
            )
        else:
            _set_status(project_id, "blueprint", "running", "Designing project structure...")
            blueprint = _step_blueprint(client, model, title, abstract, method, rq, language, framework)
            _set_status(project_id, "blueprint", "ok", f"Designed {len(blueprint.get('files', []))} files")
            _set_status(project_id, "synthesis", "running", "Generating code...")
            files_dict = _step_synthesize(client, model, project_id, blueprint, title, abstract, method, language)
        _set_status(project_id, "synthesis", "ok", f"Generated {len(files_dict)} files")

        # Step 6: Self-check
        _set_status(project_id, "self_check", "running", "Validating project structure...")
        warnings = _step_self_check(files_dict, language)
        if warnings:
            _set_status(project_id, "self_check", "ok", f"Passed with {len(warnings)} warnings", warnings)
        else:
            _set_status(project_id, "self_check", "ok", "All checks passed")

        # Step 7: Persist to filesystem + DB index
        _set_status(project_id, "persist", "running", "Writing files to disk...")
        files_list = [{"path": p, "content": c} for p, c in files_dict.items()]
        with get_session_context() as db:
            write_project_files(db, project_id, files_list)
        _set_status(project_id, "persist", "ok", f"Wrote {len(files_dict)} files to disk")

        _complete_status(project_id)
        return project_id

    except Exception as e:
        logger.error(f"Code generation failed for project {project_id}: {e}", exc_info=True)
        _set_status(project_id, "error", "failed", str(e))
        raise


# ── Skills ──────────────────────────────────────────────────────

def _skill_web_search(client, model: str, title: str, abstract: str) -> List[str]:
    """Minimal web search skill using LLM to generate reference suggestions."""
    messages = [
        ChatMessage(role="user", content=(
            f"List 3-5 relevant Python libraries, APIs, or tools for a project about: {title}\n"
            f"Context: {abstract[:300]}\n"
            "Return as a simple JSON array of strings."
        ))
    ]
    resp = client.chat(messages=messages, model=model, temperature=0.3, max_tokens=256)
    try:
        parsed = json.loads(resp.text.strip().strip('```json').strip('```'))
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _step_blueprint(client, model: str, title: str, abstract: str, method: str, rq: str, language: str, framework: str) -> Dict:
    """Generate repo blueprint."""
    prompt = BLUEPRINT_PROMPT.format(
        title=title, abstract=abstract, method=method,
        research_question=rq, language=language, framework=framework,
    )
    messages = [ChatMessage(role="user", content=prompt)]
    resp = client.chat(messages=messages, model=model, temperature=0.5, max_tokens=3000)

    parsed = _extract_json(resp.text)
    if not parsed or "files" not in parsed:
        # Retry
        repair_msg = [ChatMessage(role="user", content=f"Fix this JSON to have a 'files' array:\n{resp.text[:2000]}")]
        resp2 = client.chat(messages=repair_msg, model=model, temperature=0, max_tokens=3000)
        parsed = _extract_json(resp2.text)

    if not parsed or "files" not in parsed:
        # Fallback: generate a default blueprint
        parsed = _default_blueprint(title, language)

    return parsed


def _default_blueprint(title: str, language: str) -> Dict:
    """Fallback blueprint if LLM fails."""
    ext = "py" if language.lower() == "python" else "ts"
    return {
        "projectName": title.lower().replace(" ", "-")[:30],
        "description": title,
        "files": [
            {"path": "README.md", "description": "Project readme", "type": "doc"},
            {"path": "requirements.txt" if language.lower() == "python" else "package.json", "description": "Dependencies", "type": "config"},
            {"path": ".gitignore", "description": "Git ignore rules", "type": "config"},
            {"path": "setup.py" if language.lower() == "python" else "tsconfig.json", "description": "Project setup", "type": "config"},
            {"path": f"src/__init__.{ext}" if ext == "py" else "src/index.ts", "description": "Package init", "type": "source"},
            {"path": f"src/main.{ext}", "description": "Main entry point", "type": "source"},
            {"path": f"src/config.{ext}", "description": "Configuration", "type": "source"},
            {"path": f"src/models.{ext}", "description": "Data models", "type": "source"},
            {"path": f"src/service.{ext}", "description": "Core service logic", "type": "source"},
            {"path": f"src/utils.{ext}", "description": "Utility functions", "type": "source"},
            {"path": f"src/api.{ext}", "description": "API endpoints", "type": "source"},
            {"path": f"src/pipeline.{ext}", "description": "Processing pipeline", "type": "source"},
            {"path": f"tests/__init__.{ext}" if ext == "py" else "tests/setup.ts", "description": "Test init", "type": "test"},
            {"path": f"tests/test_main.{ext}", "description": "Main tests", "type": "test"},
            {"path": f"tests/test_service.{ext}", "description": "Service tests", "type": "test"},
            {"path": "docs/architecture.md", "description": "Architecture docs", "type": "doc"},
            {"path": "Makefile" if language.lower() == "python" else "Dockerfile", "description": "Build automation", "type": "config"},
        ],
    }


def _read_existing_project_files(project_id: str) -> Dict[str, str]:
    """Load the small, source-controlled portion of an existing workspace."""
    root = Path(CODE_PROJECTS_DIR) / project_id / "repo"
    result: Dict[str, str] = {}
    skipped_parts = {".git", "artifacts", "outputs", "results", "__pycache__"}
    skipped_names = {".env", "metrics.json", "experiment_report.md"}
    if not root.is_dir():
        return result
    for path in root.rglob("*"):
        if not path.is_file() or path.name in skipped_names:
            continue
        relative = path.relative_to(root)
        if any(part in skipped_parts for part in relative.parts) or path.stat().st_size > 2_000_000:
            continue
        try:
            result[relative.as_posix()] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return result


def _clean_generated_source(text: str) -> str:
    content = text.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines).strip()
    return content + "\n"


def _scientific_entrypoint_issues(content: str) -> List[str]:
    issues: List[str] = []
    tree = None
    try:
        tree = ast.parse(content, "src/main.py")
        compile(tree, "src/main.py", "exec")
    except SyntaxError as exc:
        issues.append(f"syntax error: {exc.msg}")
    if tree is not None:
        imported_roots = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_roots.update(
            (node.module or "").split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level == 0
        )
        unsupported = sorted(
            item
            for item in imported_roots
            if item and item != "numpy" and item not in sys.stdlib_module_names
        )
        if unsupported:
            issues.append("unsupported dependencies: " + ", ".join(unsupported))
    if re.search(r"^\s*(?:from\s+src\b|import\s+src\b)", content, re.MULTILINE):
        issues.append("local src imports are forbidden")
    if "metrics.json" not in content or "json.dump" not in content:
        issues.append("the program must write metrics.json with json.dump")
    if "if __name__" not in content or "def main" not in content:
        issues.append("main() and the __main__ guard are required")
    return issues


def _step_synthesize_scientific_entrypoint(
    *, client, model: str, title: str, abstract: str, method: str, candidate: CandidatePlan,
) -> str:
    protocol = candidate.evaluationProtocol or {}
    prompt = SCIENTIFIC_ENTRYPOINT_PROMPT.format(
        title=title,
        abstract=abstract,
        method=method,
        baselines=json.dumps(candidate.baselines, ensure_ascii=False),
        metrics=json.dumps(protocol.get("metrics") or [], ensure_ascii=False),
        datasets=json.dumps(protocol.get("datasets") or [], ensure_ascii=False),
    )
    last_issues: List[str] = []
    for attempt in range(2):
        request = prompt
        if attempt and last_issues:
            request += "\nThe previous response was rejected for: " + "; ".join(last_issues)
        response = client.chat(
            messages=[ChatMessage(role="user", content=request)],
            model=model,
            temperature=0.2,
            max_tokens=4000,
        )
        content = _clean_generated_source(response.text)
        last_issues = _scientific_entrypoint_issues(content)
        if not last_issues:
            return content
    raise ValueError("Invalid scientific entrypoint: " + "; ".join(last_issues))


def _step_synthesize(client, model: str, project_id: str, blueprint: Dict, title: str, abstract: str, method: str, language: str) -> Dict[str, str]:
    """Generate file contents using batched LLM calls."""
    files = blueprint.get("files", [])
    project_name = blueprint.get("projectName", title)
    project_desc = blueprint.get("description", abstract)
    plan_summary = f"{title}. {abstract[:200]}. Method: {method[:200]}"

    # Build files list for batch prompt
    files_list_str = "\n".join([
        f"- {f['path']}: {f.get('description', '')} ({f.get('type', 'source')})"
        for f in files
    ])

    # Try batch synthesis first (more efficient)
    prompt = BATCH_SYNTHESIS_PROMPT.format(
        language=language,
        project_name=project_name,
        project_description=project_desc,
        plan_summary=plan_summary,
        files_list=files_list_str,
    )

    messages = [ChatMessage(role="user", content=prompt)]
    resp = client.chat(messages=messages, model=model, temperature=0.4, max_tokens=8000)

    parsed = _extract_json(resp.text)
    result = {}

    if parsed and "files" in parsed:
        for f in parsed["files"]:
            path = f.get("path", "")
            content = f.get("content", "")
            if path and content:
                result[path] = content

    # Fill in any missing files with individual generation
    for f in files:
        path = f["path"]
        if path not in result:
            try:
                other_ctx = "\n".join([f"- {p}" for p in list(result.keys())[:10]])
                single_prompt = CODE_SYNTHESIS_PROMPT.format(
                    language=language,
                    project_name=project_name,
                    project_description=project_desc,
                    plan_summary=plan_summary,
                    file_path=path,
                    file_description=f.get("description", ""),
                    file_type=f.get("type", "source"),
                    other_files_context=other_ctx,
                )
                msgs = [ChatMessage(role="user", content=single_prompt)]
                r = client.chat(messages=msgs, model=model, temperature=0.3, max_tokens=2000)
                content = r.text.strip()
                # Remove markdown fences if present
                if content.startswith("```"):
                    lines = content.split("\n")
                    if len(lines) > 2:
                        content = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
                result[path] = content
            except Exception as e:
                logger.warning(f"Failed to generate {path}: {e}")
                result[path] = f"# {path}\n# TODO: Auto-generation failed — {e}\n"

    return result


def _step_self_check(files_dict: Dict[str, str], language: str) -> List[str]:
    """Validate project structure."""
    warnings = []
    paths = set(files_dict.keys())

    if "README.md" not in paths:
        warnings.append("Missing README.md")

    if language.lower() == "python":
        if "requirements.txt" not in paths and "setup.py" not in paths and "pyproject.toml" not in paths:
            warnings.append("Missing dependency file (requirements.txt/setup.py/pyproject.toml)")
    else:
        if "package.json" not in paths:
            warnings.append("Missing package.json")

    if len(paths) < 8:
        warnings.append(f"Only {len(paths)} files generated (expected >= 12)")

    return warnings
