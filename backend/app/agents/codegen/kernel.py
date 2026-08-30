"""
AgentKernel — OpenClaw-like orchestration for code generation.

Components:
- Planner: breaks task into steps based on plan context
- ToolRouter: chooses skills/tools for each step
- MemoryStore: persists intermediate artifacts
- Verifier: enforces constraints and quality gates
- PatchApplier: applies file diffs for repair cycles

The kernel runs a multi-phase pipeline:
1. Research & Context Gathering (web search, github, summarize)
2. Architecture Planning (file tree, module design)
3. Code Synthesis (batch + individual file generation)
4. Verification (compile check, structure validation, import sanity)
5. Repair Loop (detect issues → patch → re-verify, max 2 cycles)
6. Persist (write files, index, finalize)
"""

import json
import os
import re
import ast
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field

from app.llm.provider_client import get_provider_client, ChatMessage, ProviderClient
from app.core.paths import get_data_dir
from app.agents.codegen.skills.registry import SkillsRegistry, SkillResult

logger = logging.getLogger(__name__)

# Storage for session traces and resumable generation checkpoints.
_SESSIONS_DIR = str(get_data_dir() / "codegen_sessions")
_CHECKPOINTS_DIR = os.path.join(_SESSIONS_DIR, "checkpoints")
os.makedirs(_SESSIONS_DIR, exist_ok=True)
os.makedirs(_CHECKPOINTS_DIR, exist_ok=True)
_GENERATION_BATCH_SIZE = max(2, int(os.getenv("FAROS_CODEGEN_BATCH_SIZE", "4")))


@dataclass
class StepResult:
    name: str
    status: str  # "pending" | "running" | "ok" | "failed" | "skipped"
    detail: str = ""
    durationMs: int = 0
    toolCalls: List[Dict[str, Any]] = field(default_factory=list)
    artifacts: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryStore:
    """Persists intermediate artifacts across agent steps."""
    references: List[Dict] = field(default_factory=list)
    github_repos: List[Dict] = field(default_factory=list)
    summaries: List[str] = field(default_factory=list)
    design_doc: Optional[str] = None
    file_tree: Optional[Dict] = None
    generated_files: Dict[str, str] = field(default_factory=dict)
    verification_results: List[Dict] = field(default_factory=list)
    verification_summary: Dict[str, Any] = field(default_factory=dict)
    execution_result: Dict[str, Any] = field(default_factory=dict)
    patches_applied: int = 0

    def to_dict(self) -> Dict:
        return {
            "referenceCount": len(self.references),
            "githubRepoCount": len(self.github_repos),
            "summaryCount": len(self.summaries),
            "hasDesignDoc": self.design_doc is not None,
            "fileTreePlanned": self.file_tree is not None,
            "generatedFileCount": len(self.generated_files),
            "verificationCount": len(self.verification_results),
            "verificationSummary": self.verification_summary,
            "executionStatus": self.execution_result.get("status", "not_run"),
            "executionTestStatus": self.execution_result.get("testStatus", "not_run"),
            "executionCommand": self.execution_result.get("command"),
            "executionDurationMs": self.execution_result.get("durationMs", 0),
            "patchesApplied": self.patches_applied,
        }

    def to_checkpoint_dict(self) -> Dict:
        return {
            "references": self.references,
            "githubRepos": self.github_repos,
            "summaries": self.summaries,
            "designDoc": self.design_doc,
            "fileTree": self.file_tree,
            "generatedFiles": self.generated_files,
            "verificationResults": self.verification_results,
            "verificationSummary": self.verification_summary,
            "executionResult": self.execution_result,
            "patchesApplied": self.patches_applied,
        }

    @staticmethod
    def from_checkpoint_dict(data: Dict) -> "MemoryStore":
        return MemoryStore(
            references=list(data.get("references") or []),
            github_repos=list(data.get("githubRepos") or []),
            summaries=list(data.get("summaries") or []),
            design_doc=data.get("designDoc"),
            file_tree=data.get("fileTree"),
            generated_files=dict(data.get("generatedFiles") or {}),
            verification_results=list(data.get("verificationResults") or []),
            verification_summary=dict(data.get("verificationSummary") or {}),
            execution_result=dict(data.get("executionResult") or {}),
            patches_applied=int(data.get("patchesApplied") or 0),
        )

    @staticmethod
    def from_dict(d: Dict) -> "MemoryStore":
        """Reconstruct MemoryStore from saved dict."""
        m = MemoryStore()
        m.references = [{} for _ in range(d.get("referenceCount", 0))]
        m.github_repos = [{} for _ in range(d.get("githubRepoCount", 0))]
        m.summaries = ["" for _ in range(d.get("summaryCount", 0))]
        m.design_doc = "(restored)" if d.get("hasDesignDoc") else None
        m.file_tree = {"files": []} if d.get("fileTreePlanned") else None
        m.generated_files = {f"file_{i}": "" for i in range(d.get("generatedFileCount", 0))}
        m.verification_results = [{} for _ in range(d.get("verificationCount", 0))]
        m.verification_summary = dict(d.get("verificationSummary") or {})
        m.execution_result = {"status": d.get("executionStatus", "not_run")}
        m.patches_applied = d.get("patchesApplied", 0)
        return m


@dataclass
class CodeGenSession:
    """Tracks a single code generation run."""
    id: str
    projectId: str
    planLinkId: Optional[str]
    providerName: str
    model: str
    status: str = "pending"  # pending | running | completed | failed
    steps: List[StepResult] = field(default_factory=list)
    memory: MemoryStore = field(default_factory=MemoryStore)
    config: Dict[str, Any] = field(default_factory=dict)
    createdAt: str = ""
    startedAt: Optional[str] = None
    completedAt: Optional[str] = None
    errorMessage: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "projectId": self.projectId,
            "planLinkId": self.planLinkId,
            "providerName": self.providerName,
            "model": self.model,
            "status": self.status,
            "steps": [
                {
                    "name": s.name,
                    "status": s.status,
                    "detail": s.detail,
                    "durationMs": s.durationMs,
                    "toolCalls": s.toolCalls,
                }
                for s in self.steps
            ],
            "memory": self.memory.to_dict(),
            "config": self.config,
            "createdAt": self.createdAt,
            "startedAt": self.startedAt,
            "completedAt": self.completedAt,
            "errorMessage": self.errorMessage,
        }


# In-memory session store (also persisted to JSON files)
_sessions: Dict[str, CodeGenSession] = {}


def _gen_id() -> str:
    import uuid
    return f"cgs_{uuid.uuid4().hex[:12]}"


def _write_json_atomic(path: str, payload: Dict) -> None:
    temp_path = f"{path}.{os.getpid()}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, path)


def _save_session(session: CodeGenSession):
    """Persist the public trace and private resumable memory atomically."""
    path = os.path.join(_SESSIONS_DIR, f"{session.id}.json")
    checkpoint_path = os.path.join(_CHECKPOINTS_DIR, f"{session.id}.json")
    _write_json_atomic(path, session.to_dict())
    _write_json_atomic(checkpoint_path, session.memory.to_checkpoint_dict())


def get_session(session_id: str) -> Optional[CodeGenSession]:
    """Get session from memory or load from disk."""
    if session_id in _sessions:
        return _sessions[session_id]
    path = os.path.join(_SESSIONS_DIR, f"{session_id}.json")
    if os.path.isfile(path):
        with open(path) as f:
            data = json.load(f)
        session = CodeGenSession(
            id=data["id"],
            projectId=data["projectId"],
            planLinkId=data.get("planLinkId"),
            providerName=data["providerName"],
            model=data["model"],
            status=data["status"],
            createdAt=data["createdAt"],
            startedAt=data.get("startedAt"),
            completedAt=data.get("completedAt"),
            errorMessage=data.get("errorMessage"),
            config=data.get("config", {}),
        )
        checkpoint_path = os.path.join(_CHECKPOINTS_DIR, f"{session_id}.json")
        if os.path.isfile(checkpoint_path):
            with open(checkpoint_path, encoding="utf-8") as handle:
                session.memory = MemoryStore.from_checkpoint_dict(json.load(handle))
        else:
            mem_data = data.get("memory", {})
            if mem_data:
                session.memory = MemoryStore.from_dict(mem_data)
        for s in data.get("steps", []):
            session.steps.append(StepResult(
                name=s["name"], status=s["status"],
                detail=s.get("detail", ""), durationMs=s.get("durationMs", 0),
                toolCalls=s.get("toolCalls", []),
            ))
        if session.status == "running":
            session.status = "failed"
            session.errorMessage = "Generation was interrupted by a server restart; restart the session to resume from its checkpoint."
            for step in reversed(session.steps):
                if step.status == "running":
                    step.status = "failed"
                    step.detail = "Interrupted by server restart"
                    break
            _save_session(session)
        _sessions[session_id] = session
        return session
    return None


def list_sessions(project_id: Optional[str] = None) -> List[Dict]:
    """List all sessions, optionally filtered by project."""
    # Load from disk
    results = []
    if os.path.isdir(_SESSIONS_DIR):
        for fname in sorted(os.listdir(_SESSIONS_DIR), reverse=True):
            if fname.endswith(".json"):
                try:
                    with open(os.path.join(_SESSIONS_DIR, fname)) as f:
                        data = json.load(f)
                    if project_id and data.get("projectId") != project_id:
                        continue
                    results.append(data)
                except Exception:
                    pass
    return results


def _extract_json(text: str) -> Optional[Dict]:
    """Extract JSON from LLM response text."""
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


# ── Prompt templates ──────────────────────────────────────────────────

DESIGN_DOC_PROMPT = """You are a senior research software architect creating a REPRODUCIBLE EXPERIMENT design.

**Plan Context:**
- Title: {title}
- Abstract: {abstract}
- Method: {method}
- Research Question: {research_question}
- Gap Analysis: {gap_analysis}

**References found:** {references}
**Similar repos:** {repos}

Write a focused design document (600-900 words) covering ALL of the following:

1. **Research contract** — operational hypothesis, independent/dependent variables, controls, and falsification criteria.
2. **Data contract** — real dataset source, immutable split policy, schema validation, checksums, and leakage prevention.
3. **Method** — the smallest implementation that tests the proposed mechanism without unrelated product infrastructure.
4. **Baselines and ablations** — comparable controls and one-variable-at-a-time interventions.
5. **Evaluation** — preregistered primary/guardrail metrics, uncertainty or confidence intervals, and failure analysis.
6. **Reproducibility** — deterministic seeds, frozen config, environment capture, one-command smoke/full runs.
7. **Evidence outputs** — machine-readable metrics, run manifest, logs, plots, and artifact hashes for FAROS/ReviewX ingestion.
8. **Module inventory** — dataset, method, baseline, evaluation, orchestration, and reporting responsibilities.
9. **Testing and CI** — unit tests, a tiny offline fixture, integration smoke test, lint/type checks.
10. **Safety and honesty** — no fabricated measurements, explicit limitations, no secrets in logs or artifacts.

Do not add a web API, database, cache, or deployment layer unless the research method genuinely requires it.

Return plain text Markdown (no JSON).
"""

FILE_TREE_PROMPT = """You are a senior research software architect. Design a compact, executable experiment repository.

**Design Document:**
{design_doc}

**Language:** {language}
**Framework:** {framework}

**MANDATORY structure (22-30 files total):**

1. Root: README, dependency lock/config, pyproject/tool config, .gitignore, Makefile, and LICENSE.
2. Frozen experiment config: dataset URI/version/checksum, split policy, seeds, method parameters, baselines, ablations, metrics, and stop conditions.
3. Source: dataset validation/loading, proposed method, baseline, ablation switches, metrics/statistics, orchestration, provenance, and artifact writing.
4. Scripts: one smoke command and one full experiment command using the same code path.
5. Tests: at least three focused test files plus a tiny explicitly synthetic fixture.
6. Evidence: JSON schemas or examples for run manifest and metrics. Never include invented final results.
7. Docs: protocol, reproducibility, and limitations.
8. CI: one workflow that installs, lints, and runs offline tests.

Every test must import and exercise production modules; never duplicate the implementation inside
the test file. Dataset URIs and checksums must be authentic and versioned, not invented examples.

Prefer a CLI experiment package. Do not add FastAPI, a database, or generic CRUD unless required by the method.

**Output (strict JSON):**
```json
{{
  "projectName": "name",
  "description": "brief",
  "files": [
    {{"path": "relative/path", "description": "purpose", "type": "source|test|config|doc|ci|data|script|eval"}}
  ]
}}
```

Return ONLY valid JSON. Keep the repository focused and internally coherent.
"""

BATCH_CODE_PROMPT = """You are an expert {language} developer building a PRODUCTION-GRADE research project.

**Project:** {project_name}
**Description:** {description}
**Design:** {design_summary}

The design block includes the authoritative repository file map and interfaces already generated.
Imports and commands MUST reference only paths/modules in that map. Keep public names consistent across batches.

Generate content for ALL files below. Return strict JSON.

**Files:**
{files_list}

**Output (strict JSON):**
```json
{{
  "files": [
    {{"path": "exact/path", "content": "full file content"}}
  ]
}}
```

**MANDATORY quality requirements:**
- Every source file is complete, typed where supported, and import-compatible with the other requested files.
- The smoke mode runs offline on the synthetic fixture; the full mode validates a real dataset checksum before use.
- The package and its tests MUST import in a lightweight CPU-only environment. Heavy ML packages
  (for example torch, transformers, tensorflow, or sentence-transformers) are optional full-mode
  extras only: import them lazily inside the concrete full-mode implementation and provide a
  deterministic lightweight implementation/test double for smoke tests. Smoke tests must never
  download a model, dataset, or other network resource.
- The smoke script and every module it imports must use the Python standard library only so it can
  execute in a clean `python:3.12-slim` container with networking disabled. Put optional scientific
  and model dependencies behind full-mode entry points and optional dependency groups.
- Method, baseline, and ablation share the same split and evaluation code.
- Deterministic seeds and frozen configuration are recorded in a run manifest.
- Metrics include the primary endpoint, guardrails, and uncertainty when sample size permits.
- Result writers emit machine-readable JSON/CSV plus plot-ready data; never invent final measurements.
- Every `test_*` function contains a real assertion (or an explicit expected-exception assertion)
  for schema, leakage prevention, metrics, or the end-to-end smoke run. No empty/pass-only tests.
- Tests import production code and use the production schema; never reimplement metrics, loaders,
  or methods inside test files merely to make tests pass.
- README contains exact install, smoke, full-run, artifact, and ReviewX handoff commands.
- Synthetic smoke data proves wiring only: label it synthetic and never report its hand-crafted
  difference as scientific evidence or a supported hypothesis.
- No placeholder TODOs, empty-file checksums, fabricated dataset URIs, fake downloads,
  hard-coded secrets, or claims of unmeasured improvement.

Return ONLY valid JSON.
"""

SINGLE_FILE_PROMPT = """Generate the COMPLETE content for this file in a {language} research project.

**Project:** {project_name} — {description}
**File:** {file_path} ({file_type}) — {file_description}
**Context files:** {context}

Write production-quality code. Return ONLY the file content, no markdown fences.
"""

REPAIR_PROMPT = """You are a code repair assistant. Fix the issues below in the project files.

**Issues found:**
{issues}

**Current file contents:**
{file_contents}

**Authoritative repository contract:**
{repository_contract}

For each issue, provide a fix. The repaired project and all tests must import and run in a
lightweight CPU-only offline environment. Heavy ML dependencies must be lazy optional imports;
tests and smoke mode must use deterministic lightweight implementations and must never download
models or datasets. Replace pass-only tests with meaningful assertions. Return strict JSON:
```json
{{
  "patches": [
    {{"path": "file/path", "content": "complete corrected file content"}}
  ]
}}
```

Return ONLY valid JSON.
"""


# ── AgentKernel ──────────────────────────────────────────────────────

class AgentKernel:
    """OpenClaw-like agent orchestrator for code generation."""

    def __init__(
        self,
        provider_name: str = "moonshot",
        model: str = "moonshot-v1-8k",
        language: str = "python",
        framework: str = "FastAPI",
        enable_web_search: bool = True,
        enable_github: bool = True,
        max_repair_cycles: int = 2,
    ):
        self.provider_name = provider_name
        self.model = model
        self.language = language
        self.framework = framework
        self.enable_web_search = enable_web_search
        self.enable_github = enable_github
        self.max_repair_cycles = max_repair_cycles

        self.client: ProviderClient = get_provider_client(provider_name)
        self.skills = SkillsRegistry(self.client, model)
        if not enable_web_search:
            self.skills.disable("webSearch")
        if not enable_github:
            self.skills.disable("githubSearch")
            self.skills.disable("githubFetchRepo")

    def _run_step(self, session: CodeGenSession, name: str, fn, *args, **kwargs) -> Any:
        """Execute a step with timing and status tracking."""
        step = StepResult(name=name, status="running")
        session.steps.append(step)
        _save_session(session)

        t0 = time.time()
        try:
            result = fn(session, *args, **kwargs)
            step.status = "ok"
            step.durationMs = int((time.time() - t0) * 1000)
            _save_session(session)
            return result
        except Exception as e:
            step.status = "failed"
            step.detail = str(e)[:500]
            step.durationMs = int((time.time() - t0) * 1000)
            _save_session(session)
            raise

    def run(
        self,
        session: CodeGenSession,
        title: str,
        abstract: str,
        method: str,
        research_question: str,
        gap_analysis: str = "",
    ) -> str:
        """
        Execute the full code generation pipeline.
        Returns project_id.
        """
        session.status = "running"
        session.startedAt = datetime.utcnow().isoformat()
        _save_session(session)

        try:
            if session.memory.design_doc and session.memory.file_tree:
                self._run_step(session, "resume_checkpoint", self._step_resume_checkpoint, session)
            else:
                # Phase 1: Research & Context
                self._run_step(session, "research_web_search", self._step_web_search, title, abstract)
                self._run_step(session, "research_github_search", self._step_github_search, title)
                self._run_step(session, "research_summarize", self._step_summarize, session, title, abstract)

                # Phase 2: Architecture
                self._run_step(session, "design_document", self._step_design_doc, session, title, abstract, method, research_question, gap_analysis)
                self._run_step(session, "plan_file_tree", self._step_plan_tree, session)

            # Phase 3: Code Synthesis
            self._run_step(session, "code_synthesis_batch", self._step_synthesize_batch, session, title)
            self._run_step(session, "code_synthesis_fill", self._step_synthesize_fill, session, title)

            # Phase 4: Verification
            self._run_step(session, "verify_structure", self._step_verify, session)

            # Phase 5: Repair Loop (max 2 cycles)
            for cycle in range(self.max_repair_cycles):
                issues = session.memory.verification_results
                error_issues = [i for i in issues if i.get("severity") == "error"] if issues else []
                if not error_issues:
                    break
                self._run_step(session, f"repair_cycle_{cycle + 1}", self._step_repair, session, error_issues)
                self._run_step(session, f"re_verify_{cycle + 1}", self._step_verify, session)

            # Phase 6: Persist
            self._run_step(session, "persist_files", self._step_persist, session)
            remaining_errors = [
                item for item in session.memory.verification_results
                if item.get("severity") == "error"
            ]
            if remaining_errors:
                raise ValueError(
                    f"Generated project failed the executable quality gate with {len(remaining_errors)} error(s)"
                )

            # Phase 7: isolated, offline runtime proof. A structural pass alone
            # must never be presented as executable evidence.
            self._run_step(session, "execute_offline_smoke", self._step_smoke, session)
            for cycle in range(self.max_repair_cycles):
                runtime_errors = [
                    item for item in session.memory.verification_results
                    if item.get("severity") == "error"
                ]
                if not runtime_errors:
                    break
                self._run_step(session, f"runtime_repair_{cycle + 1}", self._step_repair, session, runtime_errors)
                self._run_step(session, f"runtime_static_verify_{cycle + 1}", self._step_verify, session)
                static_errors = [
                    item for item in session.memory.verification_results
                    if item.get("severity") == "error"
                ]
                if static_errors:
                    continue
                self._run_step(session, f"persist_runtime_repair_{cycle + 1}", self._step_persist, session)
                self._run_step(session, f"execute_offline_smoke_{cycle + 1}", self._step_smoke, session)

            runtime_errors = [
                item for item in session.memory.verification_results
                if item.get("severity") == "error"
            ]
            if runtime_errors or session.memory.execution_result.get("status") != "passed":
                raise ValueError(
                    f"Generated project failed isolated offline execution with {len(runtime_errors)} error(s)"
                )

            session.status = "completed"
            session.completedAt = datetime.utcnow().isoformat()
            _save_session(session)
            return session.projectId

        except Exception as e:
            logger.error(f"Agent kernel failed: {e}", exc_info=True)
            session.status = "failed"
            session.errorMessage = str(e)[:1000]
            session.completedAt = datetime.utcnow().isoformat()
            _save_session(session)
            raise

    def repair(self, session: CodeGenSession) -> str:
        """Revalidate and repair a persisted generated project from its checkpoint."""
        if not session.memory.generated_files:
            raise ValueError("No generated-file checkpoint is available for repair")
        session.status = "running"
        session.errorMessage = None
        session.startedAt = datetime.utcnow().isoformat()
        _save_session(session)
        try:
            self._run_step(session, "sync_persisted_workspace", self._step_sync_workspace, session)
            self._run_step(session, "manual_revalidate", self._step_verify, session)
            for cycle in range(self.max_repair_cycles):
                errors = [
                    item for item in session.memory.verification_results
                    if item.get("severity") == "error"
                ]
                if not errors:
                    break
                self._run_step(
                    session,
                    f"manual_repair_{cycle + 1}",
                    self._step_repair,
                    session,
                    errors,
                )
                self._run_step(
                    session,
                    f"manual_reverify_{cycle + 1}",
                    self._step_verify,
                    session,
                )
            remaining = [
                item for item in session.memory.verification_results
                if item.get("severity") == "error"
            ]
            if remaining:
                raise ValueError(
                    f"Project still has {len(remaining)} executable quality-gate error(s) after bounded repair"
                )
            self._run_step(session, "persist_repaired_files", self._step_persist, session)
            self._run_step(session, "manual_execute_offline_smoke", self._step_smoke, session)
            for cycle in range(self.max_repair_cycles):
                errors = [
                    item for item in session.memory.verification_results
                    if item.get("severity") == "error"
                ]
                if not errors:
                    break
                self._run_step(session, f"manual_runtime_repair_{cycle + 1}", self._step_repair, session, errors)
                self._run_step(session, f"manual_runtime_static_verify_{cycle + 1}", self._step_verify, session)
                static_errors = [
                    item for item in session.memory.verification_results
                    if item.get("severity") == "error"
                ]
                if static_errors:
                    continue
                self._run_step(session, f"manual_persist_runtime_repair_{cycle + 1}", self._step_persist, session)
                self._run_step(session, f"manual_execute_offline_smoke_{cycle + 1}", self._step_smoke, session)
            remaining = [
                item for item in session.memory.verification_results
                if item.get("severity") == "error"
            ]
            if remaining or session.memory.execution_result.get("status") != "passed":
                raise ValueError(
                    f"Project still fails isolated offline execution after bounded repair ({len(remaining)} error(s))"
                )
            session.status = "completed"
            session.completedAt = datetime.utcnow().isoformat()
            _save_session(session)
            return session.projectId
        except Exception as exc:
            session.status = "failed"
            session.errorMessage = str(exc)[:1000]
            session.completedAt = datetime.utcnow().isoformat()
            _save_session(session)
            raise

    # ── Step implementations ──────────────────────────────────────

    def _step_sync_workspace(self, session: CodeGenSession, _session_arg):
        """Adopt persisted editor/repair changes before revalidation."""
        root = get_data_dir() / "code_projects" / session.projectId / "repo"
        if not root.is_dir():
            session.steps[-1].detail = "No persisted workspace found; using checkpoint files"
            return

        excluded_parts = {
            ".git", ".venv", "venv", "__pycache__", ".pytest_cache",
            ".mypy_cache", ".ruff_cache", "node_modules", "artifacts",
        }
        excluded_names = {"uv.lock"}
        loaded: Dict[str, str] = {}
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.name in excluded_names:
                continue
            relative = path.relative_to(root)
            if any(part in excluded_parts or part.endswith(".egg-info") for part in relative.parts):
                continue
            try:
                if path.stat().st_size > 1_000_000:
                    continue
                loaded[str(relative)] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
        if loaded:
            session.memory.generated_files = loaded
            session.steps[-1].detail = f"Synchronized {len(loaded)} persisted project files"
        else:
            session.steps[-1].detail = "Persisted workspace contained no readable project files"

    def _step_resume_checkpoint(self, session: CodeGenSession, _session_arg):
        step = session.steps[-1]
        planned = len((session.memory.file_tree or {}).get("files") or [])
        generated = len(session.memory.generated_files)
        step.detail = f"Resumed {generated}/{planned} generated files from an atomic checkpoint"

    def _step_web_search(self, session: CodeGenSession, title: str, abstract: str):
        result = self.skills.execute("webSearch", query=f"{title} {abstract[:100]}")
        step = session.steps[-1]
        step.toolCalls.append({"skill": "webSearch", "ok": result.ok})
        if result.ok and result.data:
            session.memory.references = result.data if isinstance(result.data, list) else []
            step.detail = f"Found {len(session.memory.references)} references"
        else:
            step.status = "skipped"
            step.detail = result.error or "No results"

    def _step_github_search(self, session: CodeGenSession, title: str):
        result = self.skills.execute("githubSearch", query=title)
        step = session.steps[-1]
        step.toolCalls.append({"skill": "githubSearch", "ok": result.ok})
        if result.ok and result.data:
            session.memory.github_repos = result.data if isinstance(result.data, list) else []
            step.detail = f"Found {len(session.memory.github_repos)} repos"
        else:
            step.status = "skipped"
            step.detail = result.error or "No results"

    def _step_summarize(self, session: CodeGenSession, _session_arg: CodeGenSession, title: str, abstract: str):
        text_to_summarize = f"Title: {title}\nAbstract: {abstract}\nReferences: {json.dumps(session.memory.references[:3], default=str)}"
        result = self.skills.execute("summarize", text=text_to_summarize)
        step = session.steps[-1]
        step.toolCalls.append({"skill": "summarize", "ok": result.ok})
        if result.ok:
            session.memory.summaries.append(result.data)
            step.detail = "Summarized plan context"
        else:
            step.status = "skipped"
            step.detail = result.error or "Failed"

    def _step_design_doc(self, session: CodeGenSession, _session_arg, title, abstract, method, rq, gap):
        refs_str = json.dumps(session.memory.references[:5], default=str)[:500]
        repos_str = json.dumps(session.memory.github_repos[:3], default=str)[:500]
        prompt = DESIGN_DOC_PROMPT.format(
            title=title, abstract=abstract, method=method,
            research_question=rq, gap_analysis=gap,
            references=refs_str, repos=repos_str,
        )
        resp = self.client.chat(messages=[ChatMessage(role="user", content=prompt)], model=self.model, temperature=0.4, max_tokens=2000)
        session.memory.design_doc = resp.text.strip()
        step = session.steps[-1]
        step.detail = f"Design doc: {len(session.memory.design_doc)} chars"

    def _step_plan_tree(self, session: CodeGenSession, _session_arg):
        result = self.skills.execute(
            "planFileTree",
            plan_context=session.memory.design_doc or "Research project",
            language=self.language,
            framework=self.framework,
        )
        step = session.steps[-1]
        step.toolCalls.append({"skill": "planFileTree", "ok": result.ok})
        if result.ok and result.data:
            session.memory.file_tree = result.data
            file_count = len(result.data.get("files", []))
            step.detail = f"Planned {file_count} files"
        else:
            # Fallback to default tree
            session.memory.file_tree = self._default_file_tree()
            step.detail = f"Used fallback tree ({len(session.memory.file_tree['files'])} files)"

    def _step_synthesize_batch(self, session: CodeGenSession, _session_arg, title: str):
        tree = session.memory.file_tree
        if not tree:
            raise ValueError("No file tree planned")

        files = [
            item for item in tree.get("files", [])
            if item.get("path") and item["path"] not in session.memory.generated_files
        ]
        files.sort(key=self._generation_priority)
        project_name = tree.get("projectName", title)
        description = tree.get("description", "")

        failed_batches = 0
        for start in range(0, len(files), _GENERATION_BATCH_SIZE):
            try:
                self._generate_file_batch(
                    session,
                    files[start:start + _GENERATION_BATCH_SIZE],
                    project_name=project_name,
                    description=description,
                    design_summary=self._generation_context(session),
                )
            except Exception as exc:
                failed_batches += 1
                logger.warning("Code generation batch failed and will be retried in fill: %s", exc)
            _save_session(session)

        step = session.steps[-1]
        step.detail = (
            f"Batch synthesized {len(session.memory.generated_files)} files; "
            f"{failed_batches} batch(es) deferred to bounded fill"
        )

    def _step_synthesize_fill(self, session: CodeGenSession, _session_arg, title: str):
        tree = session.memory.file_tree
        if not tree:
            return

        files = tree.get("files", [])
        project_name = tree.get("projectName", title)
        description = tree.get("description", "")
        missing = [f for f in files if f["path"] not in session.memory.generated_files]

        filled = 0
        for start in range(0, len(missing), _GENERATION_BATCH_SIZE):
            batch = missing[start:start + _GENERATION_BATCH_SIZE]
            before = len(session.memory.generated_files)
            try:
                self._generate_file_batch(
                    session,
                    batch,
                    project_name=project_name,
                    description=description,
                    design_summary=self._generation_context(session),
                )
            except Exception as exc:
                logger.warning("Failed to fill code batch: %s", exc)
            for item in batch:
                path = item["path"]
                if path not in session.memory.generated_files:
                    session.memory.generated_files[path] = self._fallback_file_content(item)
            filled += len(session.memory.generated_files) - before
            _save_session(session)

        step = session.steps[-1]
        step.detail = f"Filled {filled} missing files (total: {len(session.memory.generated_files)})"

    def _generate_file_batch(
        self,
        session: CodeGenSession,
        files: List[Dict[str, Any]],
        *,
        project_name: str,
        description: str,
        design_summary: str,
    ) -> int:
        if not files:
            return 0
        files_list = "\n".join(
            f"- {item['path']}: {item.get('description', '')} ({item.get('type', 'source')})"
            for item in files
        )
        prompt = BATCH_CODE_PROMPT.format(
            language=self.language,
            project_name=project_name,
            description=description,
            design_summary=design_summary,
            files_list=files_list,
        )
        response = self.client.chat(
            messages=[ChatMessage(role="user", content=prompt)],
            model=self.model,
            temperature=0.3,
            max_tokens=4500,
            structured_output=True,
            request_max_retries=0,
        )
        parsed = _extract_json(response.text) or {}
        allowed = {item["path"] for item in files}
        written = 0
        for item in parsed.get("files") or []:
            path = str(item.get("path") or "")
            content = item.get("content")
            if path in allowed and isinstance(content, str) and content.strip():
                session.memory.generated_files[path] = content
                written += 1
        return written

    @staticmethod
    def _generation_priority(item: Dict[str, Any]) -> tuple[int, str]:
        path = str(item.get("path") or "")
        if path.startswith(("configs/", "schemas/", "data/")):
            return (0, path)
        if path.startswith("src/"):
            return (1, path)
        if path.startswith("scripts/"):
            return (2, path)
        if path.startswith("tests/"):
            return (3, path)
        if path.startswith("docs/") or path == "README.md":
            return (4, path)
        return (5, path)

    @staticmethod
    def _interface_index(files: Dict[str, str]) -> str:
        rows = []
        for path, content in sorted(files.items()):
            if not path.endswith(".py"):
                continue
            try:
                tree = ast.parse(content, filename=path)
            except SyntaxError:
                continue
            names = []
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    fields = [
                        child.target.id
                        for child in node.body
                        if isinstance(child, ast.AnnAssign)
                        and isinstance(child.target, ast.Name)
                    ]
                    field_text = f"({', '.join(fields)})" if fields else ""
                    names.append(f"class {node.name}{field_text}")
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    try:
                        arguments = ast.unparse(node.args)
                    except Exception:
                        arguments = "..."
                    names.append(f"{node.name}({arguments})")
            if names:
                rows.append(f"- {path}: {', '.join(names)}")
        return "\n".join(rows)

    def _generation_context(self, session: CodeGenSession) -> str:
        planned = [
            str(item.get("path"))
            for item in (session.memory.file_tree or {}).get("files") or []
            if item.get("path")
        ]
        file_map = "\n".join(f"- {path}" for path in planned)
        interfaces = self._interface_index(session.memory.generated_files) or "- No interfaces generated yet"
        return (
            f"{(session.memory.design_doc or '')[:1800]}\n\n"
            f"AUTHORITATIVE FILE MAP:\n{file_map}\n\n"
            f"EXISTING PYTHON INTERFACES:\n{interfaces}"
        )[:7000]

    @staticmethod
    def _fallback_file_content(item: Dict[str, Any]) -> str:
        path = str(item.get("path") or "artifact")
        purpose = str(item.get("description") or "Generated experiment artifact")
        if path.endswith(".py"):
            return f'"""{purpose}."""\n'
        if path.endswith(".md"):
            title = Path(path).stem.replace("-", " ").replace("_", " ").title()
            return f"# {title}\n\n{purpose}.\n"
        if path.endswith((".yaml", ".yml", ".toml", ".ini", ".cfg")):
            return f"# {purpose}\n"
        return f"{purpose}\n"

    def _step_verify(self, session: CodeGenSession, _session_arg):
        result = self.skills.execute(
            "compileCheck",
            project_root="",
            language=self.language,
            files=session.memory.generated_files,
        )
        step = session.steps[-1]
        step.toolCalls.append({"skill": "compileCheck", "ok": result.ok})
        if result.data:
            issues = result.data.get("issues", []) if result.data else []
            session.memory.verification_results = issues
            session.memory.verification_summary = {
                key: result.data.get(key)
                for key in ("qualityScore", "fileCount", "errorCount", "warningCount", "categories")
            }
            step.detail = f"Verified: {result.data.get('fileCount', 0)} files, {result.data.get('errorCount', 0)} errors"
        else:
            session.memory.verification_results = []
            step.detail = result.error or "Verification failed"

    def _step_smoke(self, session: CodeGenSession, _session_arg):
        result = self.skills.execute(
            "executionSmokeCheck",
            files=session.memory.generated_files,
        )
        data = dict(result.data or {})
        session.memory.execution_result = data
        session.memory.verification_results = list(data.get("issues") or [])
        step = session.steps[-1]
        step.toolCalls.append({"skill": "executionSmokeCheck", "ok": result.ok})
        status = data.get("status", "failed")
        duration = data.get("durationMs", 0)
        errors = data.get("errorCount", len(session.memory.verification_results))
        step.detail = f"Offline Docker smoke: {status}, {errors} errors, {duration}ms"

    def _step_repair(self, session: CodeGenSession, _session_arg, error_issues: List[Dict]):
        runtime_failure = any(
            "Offline Docker" in str(issue.get("message") or "")
            for issue in error_issues
        )
        affected_files = [
            str(issue.get("file") or "")
            for issue in error_issues
            if issue.get("file") not in (None, "", "*")
            and issue.get("file") in session.memory.generated_files
        ]
        affected_files = list(dict.fromkeys(affected_files))

        if runtime_failure and len(affected_files) > 2:
            grouped: Dict[str, List[str]] = {}
            for path in affected_files:
                stem = Path(path).stem.removeprefix("test_")
                grouped.setdefault(stem, []).append(path)
            repair_groups = list(grouped.values())[:3]
        else:
            repair_groups = [affected_files[:5]]

        shared_issues = json.dumps(error_issues[:10], indent=2)
        patches_applied = 0
        repair_calls = 0
        for group in repair_groups:
            file_contents_str = ""
            for path in group:
                content = session.memory.generated_files.get(path, "")
                file_contents_str += f"\n--- {path} ---\n{content[:7000]}\n"
            scoped_contract = self._generation_context(session)
            if group:
                scoped_contract += (
                    "\n\nPATCH SCOPE: Return complete corrected content only for these files: "
                    + ", ".join(group)
                )
            prompt = REPAIR_PROMPT.format(
                issues=shared_issues,
                file_contents=file_contents_str,
                repository_contract=scoped_contract,
            )
            resp = self.client.chat(
                messages=[ChatMessage(role="user", content=prompt)],
                model=self.model,
                temperature=0.2,
                max_tokens=7000 if runtime_failure else 4500,
                structured_output=True,
                request_max_retries=0,
            )
            repair_calls += 1
            parsed = _extract_json(resp.text)
            if not parsed or "patches" not in parsed:
                continue
            for patch in parsed["patches"]:
                path = patch.get("path", "")
                content = patch.get("content", "")
                if path and content and path in group and path in session.memory.generated_files:
                    session.memory.generated_files[path] = content
                    patches_applied += 1

        if not patches_applied:
            raise ValueError("Qwen returned no applicable bounded repair patches")

        session.memory.patches_applied += patches_applied
        step = session.steps[-1]
        step.detail = f"Applied {patches_applied} patches in {repair_calls} bounded call(s)"

    def _step_persist(self, session: CodeGenSession, _session_arg):
        files = session.memory.generated_files
        if not files:
            raise ValueError("No files to persist")

        files_list = [{"path": p, "content": c} for p, c in files.items()]
        result = self.skills.execute("writeProjectFiles", project_id=session.projectId, files=files_list)

        step = session.steps[-1]
        step.toolCalls.append({"skill": "writeProjectFiles", "ok": result.ok})
        if result.ok:
            step.detail = f"Wrote {result.data['fileCount']} files ({result.data['totalBytes']} bytes)"
        else:
            raise ValueError(f"Failed to persist: {result.error}")

    def _default_file_tree(self) -> Dict:
        """Fallback to a focused, reproducible experiment repository."""
        ext = "py" if self.language.lower() == "python" else "ts"
        py = ext == "py"
        return {
            "projectName": "research-project",
            "description": "Reproducible research experiment with frozen protocol and evidence outputs",
            "files": [
                {"path": "README.md", "description": "Exact setup, smoke/full runs, artifacts, and ReviewX handoff", "type": "doc"},
                {"path": "requirements.txt" if py else "package.json", "description": "Dependencies", "type": "config"},
                {"path": "pyproject.toml" if py else "tsconfig.json", "description": "Lint, test, and package configuration", "type": "config"},
                {"path": ".gitignore", "description": "Git ignore rules", "type": "config"},
                {"path": "Makefile", "description": "Install, lint, test, smoke, and full-run targets", "type": "config"},
                {"path": ".github/workflows/ci.yml", "description": "Offline lint and test workflow", "type": "ci"},
                {"path": "configs/experiment.yaml", "description": "Frozen seeds, method, baselines, ablations, metrics, and stop conditions", "type": "config"},
                {"path": "configs/dataset.yaml", "description": "Dataset URI, version, checksum, schema, and split policy", "type": "config"},
                {"path": f"src/__init__.{ext}" if py else "src/index.ts", "description": "Package init", "type": "source"},
                {"path": f"src/config.{ext}", "description": "Validated frozen experiment configuration", "type": "source"},
                {"path": f"src/data.{ext}", "description": "Checksum, schema, split, and leakage-safe dataset loader", "type": "source"},
                {"path": f"src/method.{ext}", "description": "Proposed research method", "type": "source"},
                {"path": f"src/baseline.{ext}", "description": "Comparable baseline implementation", "type": "source"},
                {"path": f"src/metrics.{ext}", "description": "Primary and guardrail metrics", "type": "source"},
                {"path": f"src/statistics.{ext}", "description": "Confidence intervals and paired comparisons", "type": "source"},
                {"path": f"src/provenance.{ext}", "description": "Run manifest and artifact hashing", "type": "source"},
                {"path": f"src/pipeline.{ext}", "description": "Shared smoke and full experiment pipeline", "type": "source"},
                {"path": f"src/cli.{ext}", "description": "Command-line entry point", "type": "source"},
                {"path": f"scripts/run_smoke.{ext}", "description": "Offline synthetic smoke run", "type": "script"},
                {"path": f"scripts/run_full.{ext}", "description": "Checksum-validated full experiment run", "type": "script"},
                {"path": "tests/conftest.py" if py else "tests/helpers.ts", "description": "Shared fixtures", "type": "test"},
                {"path": f"tests/test_data.{ext}", "description": "Schema, checksum, and leakage tests", "type": "test"},
                {"path": f"tests/test_metrics.{ext}", "description": "Metric and uncertainty tests", "type": "test"},
                {"path": f"tests/test_smoke.{ext}", "description": "End-to-end offline smoke test", "type": "test"},
                {"path": "tests/fixtures/synthetic.json", "description": "Tiny explicitly synthetic offline fixture", "type": "data"},
                {"path": "schemas/run_manifest.schema.json", "description": "Machine-readable provenance contract", "type": "data"},
                {"path": "docs/protocol.md", "description": "Preregistered hypothesis, controls, metrics, and stop conditions", "type": "doc"},
                {"path": "docs/reproducibility.md", "description": "Environment, seeds, dataset, and rerun instructions", "type": "doc"},
                {"path": "docs/limitations.md", "description": "Known validity limits and prohibited claims", "type": "doc"},
            ],
        }
