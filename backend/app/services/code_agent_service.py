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
Frozen benchmark contract: {benchmark_contract}

Strict requirements:
1. Return only complete Python source code for src/main.py, without Markdown fences.
2. Use only the Python standard library and NumPy. Do not import any local project module.
3. Use a fixed random seed and a deterministic synthetic-data or computational experiment.
4. Implement both the declared method and baseline. Compute all values at runtime; never hard-code outcomes.
5. Evaluate on a held-out test split when data is involved.
6. Write metrics.json in the current working directory (the repository root) as a JSON list. Open exactly "metrics.json", not a path derived from __file__. Every item must contain the exact keys name, value, unit, definition, and split; value must be numeric.
7. Metric names or splits must distinguish the baseline from the proposed method.
8. Print the same metric list as JSON and exit nonzero on an invalid or non-finite result.
9. Include a main() function and an if __name__ == "__main__" guard.
10. If the required metrics include precision, recall, F1, unsupported-claim rate, calibration error, Brier score, or AUROC, also write evaluation_records.json. It must be a JSON object with schema_version "faros-evaluation/v1", positive_label, positive_class, decision_threshold (normally 0.5), and records. Each record's top-level label is the ground-truth class. Its predictions object must be keyed by the exact metric method prefixes (for example baseline and method); every prediction must use exactly {{"label": <predicted class>, "probability": <positive-class probability>}}. Derive each prediction label from that method's probability and decision_threshold; never copy the ground-truth label into a prediction. Do not use predicted_label or predictedLabel as the output key. Use positive_class "unsupported" for unsupported-claim detection. Compute expected calibration error with 10 equal-width bins. FAROS will independently recompute the aggregate metrics from these records.
11. For those classification metrics, use data/frozen_benchmark.json as the only evaluation set. If it exists, load it unchanged and never regenerate, relabel, reorder, omit, or add evaluation samples. If it does not exist, create it once with schema_version "faros-benchmark/v1", benchmark_id, task, positive_label, positive_class, integer seed, generator_version, feature_schema, and records. Every frozen record must contain sample_id, split, features, and label. evaluation_records.json must contain the exact same sample_id, split, and label values plus predictions, but no generated replacement samples. Probabilities must always mean the declared positive_class; invert consistency/support scores when positive_class is "unsupported".
12. In an inherited-benchmark iteration, make a substantive method revision tied to the ReviewX feedback and include at least one named ablation prediction alongside the exact "baseline" and "method" predictions. The ablation must remove or neutralize a specific method component, use the same frozen records, and emit the same audited classification metrics with its own prefix. If the implemented method contains genuine stochastic fitting or sampling, evaluate deterministic seeds 13, 37, and 73 and expose their per-record predictions; otherwise state the deterministic design in code and do not fabricate seed variation.
13. Keep experimental conclusions identifiable from metric names: use "baseline", "method", and "ablation_<component>" prefixes consistently in both metrics.json and evaluation_records.json. Do not label a tuned variant as a baseline, and do not claim improvement when the computed metric is unchanged or worse.
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
    iteration_feedback: Optional[Dict[str, Any]] = None,
    frozen_benchmark: Optional[Dict[str, Any]] = None,
) -> str:
    """Bridge an Idea candidate into the established Plan-based Code agent."""

    session_id = generate_plan_session_id()
    candidate_id = generate_candidate_plan_id()
    title = str(candidate.get("title") or "Research Project")
    problem = str(candidate.get("problem") or candidate.get("hypothesisStatement") or "")
    method = str(candidate.get("proposedMethod") or candidate.get("keyInsight") or "")
    iteration_feedback = iteration_feedback or {}
    feedback_actions = [
        str(item).strip()
        for item in iteration_feedback.get("nextActions", [])
        if str(item).strip()
    ]
    feedback_comment = str(iteration_feedback.get("feedbackComment") or "").strip()
    optimization_policy = iteration_feedback.get("optimizationPolicy") or {}
    guardrail_violations = iteration_feedback.get("guardrailViolations") or []
    human_feedback = iteration_feedback.get("humanFeedback") or {}
    if feedback_comment or feedback_actions or human_feedback:
        feedback_lines = [
            "ReviewX iteration requirements (must change the next executable experiment):"
        ]
        if feedback_comment:
            feedback_lines.append(feedback_comment)
        feedback_lines.extend(f"- {item}" for item in feedback_actions)
        if optimization_policy:
            feedback_lines.append(
                "Treat optimizationPolicy as a machine-checkable objective; do not trade off "
                "a guardrail metric to improve the primary metric: "
                f"{json.dumps(optimization_policy, ensure_ascii=True, sort_keys=True)}"
            )
        if guardrail_violations:
            feedback_lines.append(
                "The previous round violated these hard constraints and the executable method "
                "must address them explicitly: "
                f"{json.dumps(guardrail_violations, ensure_ascii=True, sort_keys=True)}"
            )
        if human_feedback:
            feedback_lines.append(
                "The following constraints came from an explicit human review and must be "
                "implemented and made verifiable in the next evidence bundle: "
                f"{json.dumps(human_feedback, ensure_ascii=True, sort_keys=True)}"
            )
        method = f"{method}\n\n" + "\n".join(feedback_lines)
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
        evaluationProtocol={
            "metrics": metrics,
            "datasets": datasets,
            "iterationFeedback": iteration_feedback,
            "frozenBenchmark": frozen_benchmark or {},
        },
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
        frozen_benchmark=frozen_benchmark,
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
    frozen_benchmark: Optional[Dict[str, Any]] = None,
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
                frozen_benchmark=frozen_benchmark,
                existing_source=files_dict.get("src/main.py"),
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


def _scientific_entrypoint_issues(
    content: str,
    *,
    require_classification_records: bool = False,
    frozen_benchmark: Optional[Dict[str, Any]] = None,
) -> List[str]:
    issues: List[str] = []
    tree = None
    try:
        tree = ast.parse(content, "src/main.py")
        compile(tree, "src/main.py", "exec")
    except SyntaxError as exc:
        issues.append(f"syntax error: {exc.msg}")
    if tree is not None:
        literal_bindings = {
            target.id: node.value.value
            for node in ast.walk(tree)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            if isinstance(target, ast.Name)
            and isinstance(node.value, ast.Constant)
        }

        def resolve_literal(node: ast.AST | None) -> Any:
            if isinstance(node, ast.Constant):
                return node.value
            if isinstance(node, ast.Name):
                return literal_bindings.get(node.id)
            return None

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
        expected_positive_class = (
            str(frozen_benchmark.get("positiveClass") or "")
            if frozen_benchmark
            else ""
        )
        expected_positive_label = (
            frozen_benchmark.get("positiveLabel") if frozen_benchmark else None
        )
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            fields = {
                key.value: value
                for key, value in zip(node.keys, node.values)
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
            declared_class = resolve_literal(fields.get("positive_class"))
            if (
                expected_positive_class
                and declared_class is not None
                and str(declared_class) != expected_positive_class
            ):
                issues.append(
                    "positive_class must match the inherited frozen benchmark exactly: "
                    f"{expected_positive_class}"
                )
            declared_label = resolve_literal(fields.get("positive_label"))
            if (
                expected_positive_label is not None
                and declared_label is not None
                and declared_label != expected_positive_label
            ):
                issues.append(
                    "positive_label must match the inherited frozen benchmark exactly: "
                    f"{expected_positive_label}"
                )
            truth_label = fields.get("label")
            prediction_groups = fields.get("predictions")
            if truth_label is None or not isinstance(prediction_groups, ast.Dict):
                continue
            truth_expression = ast.dump(truth_label, include_attributes=False)
            for prediction in prediction_groups.values:
                if not isinstance(prediction, ast.Dict):
                    continue
                prediction_fields = {
                    key.value: value
                    for key, value in zip(prediction.keys, prediction.values)
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                }
                predicted_label = prediction_fields.get("label")
                if predicted_label is None:
                    issues.append(
                        "each prediction object must use the exact key 'label' for its predicted class"
                    )
                    break
                if (
                    ast.dump(predicted_label, include_attributes=False) == truth_expression
                ):
                    issues.append(
                        "prediction labels must be derived from model probabilities, not copied from ground truth"
                    )
                    break
        if frozen_benchmark:
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                if not isinstance(node.func, ast.Name) or node.func.id != "open":
                    continue
                path = str(resolve_literal(node.args[0]) or "")
                mode_node = node.args[1] if len(node.args) > 1 else None
                if mode_node is None:
                    mode_node = next(
                        (item.value for item in node.keywords if item.arg == "mode"),
                        None,
                    )
                mode = str(resolve_literal(mode_node) or "r")
                if path.endswith("frozen_benchmark.json") and any(
                    marker in mode for marker in ("w", "a", "x", "+")
                ):
                    issues.append(
                        "inherited frozen_benchmark.json must be opened read-only; remove all "
                        "creation/write fallback code and raise an error if the file is missing"
                    )
    if re.search(r"^\s*(?:from\s+src\b|import\s+src\b)", content, re.MULTILINE):
        issues.append("local src imports are forbidden")
    if "metrics.json" not in content or "json.dump" not in content:
        issues.append("the program must write metrics.json with json.dump")
    if require_classification_records:
        required_record_tokens = (
            "evaluation_records.json",
            "faros-evaluation/v1",
            "frozen_benchmark.json",
            "sample_id",
            "features",
            "split",
            "positive_label",
            "positive_class",
            "predictions",
            "probability",
        )
        creation_tokens = (
            ()
            if frozen_benchmark
            else ("faros-benchmark/v1", "benchmark_id", "generator_version", "feature_schema")
        )
        missing = [
            token for token in (*required_record_tokens, *creation_tokens)
            if token not in content
        ]
        if missing:
            issues.append(
                "classification experiments must write auditable evaluation records; missing: "
                + ", ".join(missing)
            )
    if "if __name__" not in content or "def main" not in content:
        issues.append("main() and the __main__ guard are required")
    return issues


def _step_synthesize_scientific_entrypoint(
    *, client, model: str, title: str, abstract: str, method: str, candidate: CandidatePlan,
    frozen_benchmark: Optional[Dict[str, Any]] = None,
    existing_source: Optional[str] = None,
) -> str:
    protocol = candidate.evaluationProtocol or {}
    requested_metrics = [str(item).lower() for item in protocol.get("metrics") or []]
    require_classification_records = any(
        token in metric
        for metric in requested_metrics
        for token in ("precision", "recall", "f1", "unsupported", "calibration", "brier", "auroc", "roc_auc")
    )
    prompt = SCIENTIFIC_ENTRYPOINT_PROMPT.format(
        title=title,
        abstract=abstract,
        method=method,
        baselines=json.dumps(candidate.baselines, ensure_ascii=False),
        metrics=json.dumps(protocol.get("metrics") or [], ensure_ascii=False),
        datasets=json.dumps(protocol.get("datasets") or [], ensure_ascii=False),
        benchmark_contract=(
            "An inherited immutable benchmark is already present at data/frozen_benchmark.json. "
            "Open it read-only and evaluate every record exactly once. Do not generate, rewrite, "
            "replace, hash-check, or repair the benchmark; FAROS validates its canonical fingerprint "
            f"externally as {frozen_benchmark.get('fingerprint', '')}. "
            f"It declares positive_label={frozen_benchmark.get('positiveLabel')!r} and "
            f"positive_class={frozen_benchmark.get('positiveClass')!r}; copy both values "
            "exactly into evaluation_records.json and make every probability mean that class. "
            "Its feature_schema is "
            f"{json.dumps(frozen_benchmark.get('featureSchema'), ensure_ascii=False)} and one exact "
            "record shape example is "
            f"{json.dumps(frozen_benchmark.get('sampleRecord'), ensure_ascii=False)}. "
            "SOURCE-LEVEL RULE: do not include any fallback branch that creates or opens "
            "data/frozen_benchmark.json in write/append mode, even if that branch seems "
            "unreachable. If the file is missing, raise FileNotFoundError."
            if frozen_benchmark
            else "No inherited benchmark is present. Create the versioned benchmark once, then evaluate it."
        ),
    )
    if frozen_benchmark and existing_source:
        prompt += (
            "\nRevise the inherited executable source below instead of redesigning the experiment. "
            "Preserve its frozen-record mapping, metric implementations, baseline definitions, and "
            "working outputs. Make the smallest substantive method/calibration change needed by the "
            "ReviewX objective, then return the complete revised source.\n\n"
            "INHERITED SOURCE:\n" + existing_source
        )
    last_issues: List[str] = []
    max_attempts = 3 if frozen_benchmark else 2
    for attempt in range(max_attempts):
        request = prompt
        if attempt and last_issues:
            request += "\nThe previous response was rejected for: " + "; ".join(last_issues)
        response = client.chat(
            messages=[ChatMessage(role="user", content=request)],
            model=model,
            temperature=0.2,
            max_tokens=7000,
        )
        content = _clean_generated_source(response.text)
        last_issues = _scientific_entrypoint_issues(
            content,
            require_classification_records=require_classification_records,
            frozen_benchmark=frozen_benchmark,
        )
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
