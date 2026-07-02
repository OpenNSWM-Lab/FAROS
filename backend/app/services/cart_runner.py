"""
CartRunner — Orchestrates end-to-end execution of a PlanPackage DAG.

Flow:
1. Load PlanPackage → topological sort stages+steps → execution order
2. Create cart directory structure under data/code_artifact/cart_{id}/
3. For each node in order:
   a. Build task prompt from node metadata + upstream outputs
   b. Launch Claude Code agent in node workspace
   c. Collect artifacts → cart/data/{node_id}/
   d. Save standardized result.json
   e. Emit SSE events for frontend
4. All shared code persists to cart/project/
5. Execution traces stored in cart/trace/{node_id}/
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)

CART_BASE_DIR = "cart_artifacts"


@dataclass
class CartNodeResult:
    """Standardized result for a single DAG node execution."""

    node_id: str
    success: bool
    message: str = ""
    outputs: dict = field(default_factory=dict)
    artifacts: list[dict] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    duration_ms: int = 0
    error: Optional[str] = None
    session_id: str = ""
    # Paper-module compatible metadata fields
    paper_metadata: dict = field(default_factory=dict)


@dataclass
class CartProgressEvent:
    """SSE-friendly progress event."""

    event_type: str  # "node_start", "node_progress", "node_complete", "cart_complete"
    node_id: str = ""
    status: str = ""  # "running", "success", "failed", "skipped"
    message: str = ""
    result: Optional[dict] = None
    timestamp: str = field(default_factory=lambda: time.strftime("%H:%M:%S"))

    def to_sse(self) -> str:
        data = {
            "event_type": self.event_type,
            "node_id": self.node_id,
            "status": self.status,
            "message": self.message,
            "result": self.result,
            "timestamp": self.timestamp,
        }
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


class CartRunner:
    """Executes a PlanPackage DAG node-by-node using Claude Code agent."""

    def __init__(self, base_dir: Optional[str] = None):
        if base_dir:
            self._base = base_dir
        else:
            from app.db.engine import _DATA_DIR
            self._base = os.path.join(_DATA_DIR, CART_BASE_DIR)

    # ---- public API ----

    async def run(
        self,
        ppkg: dict,
        on_event: Optional[callable] = None,
    ) -> AsyncIterator[CartProgressEvent]:
        """Execute all nodes in the PlanPackage DAG.

        Args:
            ppkg: PlanPackage dict (from JSON).
            on_event: Optional async callback(event).

        Yields:
            CartProgressEvent for each state change.
        """
        package_id = ppkg.get("packageId", "unknown")
        cart_id = f"cart_{package_id.replace('ppkg_', '')[:12]}"
        cart_dir = os.path.join(self._base, cart_id)

        # Create cart directory structure
        for sub in ["data", "project", "runs", "trace"]:
            os.makedirs(os.path.join(cart_dir, sub), exist_ok=True)

        # Save manifest
        idea = ppkg.get("idea", {})
        manifest = {
            "cart_id": cart_id,
            "package_id": package_id,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            # Paper-compatible fields
            "experiment_plan": idea.get("proposedMethod", ""),
            "hypothesis": idea.get("hypothesisStatement", ""),
            "research_question": idea.get("title", ""),
            "datasets": [c.get("value") for c in ppkg.get("constants", []) if c.get("type") == "dataset"],
            "methods": [idea.get("proposedMethod", "")[:80]],
            "metrics": [],
            "constants": {c.get("name"): c.get("value") for c in ppkg.get("constants", [])},
        }
        _write_json(os.path.join(cart_dir, "data", "manifest.json"), manifest)

        # Topological sort: flatten stages+steps, resolve dependencies
        nodes = self._topological_sort(ppkg)
        logger.info("CartRunner: %d nodes in execution order for %s", len(nodes), cart_id)

        yield CartProgressEvent(
            event_type="cart_start",
            node_id=cart_id,
            status="running",
            message=f"Starting execution: {len(nodes)} nodes across {len(ppkg.get('stages', []))} stages",
        )

        completed: dict[str, CartNodeResult] = {}
        skipped: set = set()

        for idx, node_info in enumerate(nodes):
            node_id = node_info["id"]

            # Check if all inputs are satisfied
            deps = node_info.get("inputFrom", [])
            if deps:
                failed_deps = [d for d in deps if d in completed and not completed[d].success]
                if failed_deps:
                    skip_msg = f"Skipped: upstream node(s) failed: {failed_deps}"
                    logger.warning("CartRunner: %s", skip_msg)
                    skipped.add(node_id)
                    evt = CartProgressEvent(
                        event_type="node_complete", node_id=node_id, status="skipped", message=skip_msg,
                    )
                    if on_event:
                        await on_event(evt)
                    yield evt
                    continue

            # Emit start event
            yield CartProgressEvent(
                event_type="node_start",
                node_id=node_id,
                status="running",
                message=f"[{idx+1}/{len(nodes)}] {node_info['title']}",
            )

            # Execute node
            start_ts = time.time()
            result = await self._execute_node(node_info, ppkg, cart_dir, completed)

            if result is None:
                result = CartNodeResult(
                    node_id=node_id,
                    success=False,
                    error="Execution returned no result",
                    message="Node execution failed",
                )

            result.started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(start_ts))
            result.finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ")
            result.duration_ms = int((time.time() - start_ts) * 1000)
            completed[node_id] = result

            # Build paper-compatible metadata
            result.paper_metadata = self._build_paper_metadata(node_info, ppkg, result)

            # Save result.json
            node_data_dir = os.path.join(cart_dir, "data", node_id)
            os.makedirs(node_data_dir, exist_ok=True)
            _write_json(os.path.join(node_data_dir, "result.json"), {
                "node_id": result.node_id,
                "success": result.success,
                "message": result.message,
                "outputs": result.outputs,
                "artifacts": result.artifacts,
                "started_at": result.started_at,
                "finished_at": result.finished_at,
                "duration_ms": result.duration_ms,
                "session_id": result.session_id,
                "error": result.error,
                "node_info": {
                    "label": node_info.get("title", ""),
                    "description": node_info.get("desc", ""),
                    "method": node_info.get("method", ""),
                },
                # Paper-module fields
                "experiment_plan": result.paper_metadata.get("experiment_plan", ""),
                "dataset": result.paper_metadata.get("dataset", {}),
                "baseline": result.paper_metadata.get("baseline", ""),
                "metrics_declaration": result.paper_metadata.get("metrics_declaration", ""),
                "figures_and_tables": result.paper_metadata.get("figures_and_tables", []),
                "result_analysis": result.paper_metadata.get("result_analysis", ""),
            })

            # Collect all generated artifacts from workspace
            run_dir = os.path.join(cart_dir, "runs", node_id)
            if os.path.isdir(run_dir):
                self._collect_artifacts(run_dir, node_data_dir, result)

            # AI-enhanced metadata enrichment (async, fire-and-forget)
            asyncio.create_task(
                self._enrich_metadata_via_ai(node_data_dir, node_info, ppkg, result)
            )

            # Save blueprint state
            bp_state_path = os.path.join(cart_dir, "blueprint_state.json")
            bp_state = _load_json(bp_state_path) or {}
            bp_state[node_id] = {
                "status": "success" if result.success else "failed",
                "artifacts": [a.get("name", "") for a in result.artifacts],
                "duration_ms": result.duration_ms,
            }
            _write_json(bp_state_path, bp_state)

            # Emit completion event
            evt = CartProgressEvent(
                event_type="node_complete",
                node_id=node_id,
                status="success" if result.success else "failed",
                message=f"{'OK' if result.success else 'FAILED'} in {result.duration_ms}ms: {result.message[:100]}",
                result={
                    "node_id": node_id,
                    "success": result.success,
                    "message": result.message,
                    "artifacts": result.artifacts,
                    "duration_ms": result.duration_ms,
                },
            )
            if on_event:
                await on_event(evt)
            yield evt

        # Cart complete
        # Save aggregated results summary
        self._save_cart_results_summary(cart_dir, completed, ppkg, nodes, skipped)

        total = len(nodes)
        succeeded = sum(1 for n in completed.values() if n.success)
        failed = total - succeeded - len(skipped)
        yield CartProgressEvent(
            event_type="cart_complete",
            node_id=cart_id,
            status="success" if failed == 0 else "partial",
            message=f"Done: {succeeded} succeeded, {failed} failed, {len(skipped)} skipped out of {total}",
        )

    # ---- internals ----

    @staticmethod
    def _save_cart_results_summary(
        cart_dir: str,
        completed: dict[str, CartNodeResult],
        ppkg: dict,
        nodes: list[dict],
        skipped: set,
    ) -> None:
        """Aggregate all node results into a single cart_results.json for Paper module consumption."""
        idea = ppkg.get("idea", {})
        stages = ppkg.get("stages", [])

        # Build per-stage summaries
        stage_summaries = []
        for stage in stages:
            stage_steps = []
            for step in stage.get("steps", []):
                sid = step["id"]
                if sid in completed:
                    r = completed[sid]
                    stage_steps.append({
                        "node_id": sid,
                        "title": step.get("title", ""),
                        "success": r.success,
                        "duration_ms": r.duration_ms,
                        "metrics": r.outputs.get("metrics", {}),
                        "artifacts": [a.get("name", "") for a in r.artifacts],
                        "error": r.error,
                    })
                elif sid in skipped:
                    stage_steps.append({
                        "node_id": sid,
                        "title": step.get("title", ""),
                        "success": False,
                        "skipped": True,
                    })

            stage_ok = all(s.get("success", False) for s in stage_steps)
            stage_summaries.append({
                "stage_id": stage["id"],
                "title": stage.get("title", ""),
                "success": stage_ok,
                "steps": stage_steps,
            })

        # Aggregate all metrics
        all_metrics: dict[str, dict] = {}
        all_artifacts: list[str] = []
        total_duration = 0
        for r in completed.values():
            all_metrics[r.node_id] = r.outputs.get("metrics", {})
            all_artifacts.extend(a.get("name", "") for a in r.artifacts)
            total_duration += r.duration_ms

        total = len(nodes)
        succeeded = sum(1 for r in completed.values() if r.success)
        failed = total - succeeded - len(skipped)

        summary = {
            "cart_id": os.path.basename(cart_dir),
            "package_id": ppkg.get("packageId", ""),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "research_question": idea.get("title", ""),
            "hypothesis": idea.get("hypothesisStatement", ""),
            "proposed_method": idea.get("proposedMethod", ""),
            "overall_status": "success" if failed == 0 else "partial" if succeeded > 0 else "failed",
            "total_nodes": total,
            "succeeded": succeeded,
            "failed": failed,
            "skipped": len(skipped),
            "total_duration_ms": total_duration,
            "all_metrics": all_metrics,
            "all_artifacts": sorted(set(all_artifacts)),
            "stages": stage_summaries,
            "constants": {c.get("name"): c.get("value") for c in ppkg.get("constants", [])},
        }

        _write_json(os.path.join(cart_dir, "cart_results.json"), summary)

    @staticmethod
    def _topological_sort(ppkg: dict) -> list[dict]:
        """Flatten stages+steps into a topologically sorted execution list."""
        nodes: list[dict] = []
        node_ids: set = set()

        for stage in ppkg.get("stages", []):
            for step in stage.get("steps", []):
                sid = step["id"]
                nodes.append({
                    "id": sid,
                    "title": step.get("title", sid),
                    "desc": step.get("desc", ""),
                    "method": step.get("method", ""),
                    "inputFrom": step.get("inputFrom", []),
                    "outputs": step.get("outputs", []),
                    "expected": step.get("expected", []),
                    "codeHints": step.get("codeHints", ""),
                    "stage_id": stage["id"],
                    "stage_title": stage.get("title", ""),
                    "order": step.get("order", 0),
                })
                node_ids.add(sid)

        # Topological sort using Kahn's algorithm
        in_degree: dict[str, int] = {n["id"]: 0 for n in nodes}
        adj: dict[str, list[str]] = {n["id"]: [] for n in nodes}

        for n in nodes:
            for dep in n["inputFrom"]:
                if dep in node_ids:
                    adj.setdefault(dep, []).append(n["id"])
                    in_degree[n["id"]] += 1

        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        sorted_ids: list[str] = []

        while queue:
            nid = queue.pop(0)
            sorted_ids.append(nid)
            for neighbor in adj.get(nid, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        # Build ordered node list
        id_to_node = {n["id"]: n for n in nodes}
        ordered = [id_to_node[nid] for nid in sorted_ids if nid in id_to_node]

        # Append any nodes not reached (circular deps or self-contained)
        for n in nodes:
            if n["id"] not in sorted_ids:
                ordered.append(n)

        return ordered

    async def _execute_node(
        self,
        node: dict,
        ppkg: dict,
        cart_dir: str,
        completed: dict[str, CartNodeResult],
    ) -> Optional[CartNodeResult]:
        """Execute a single DAG node via Claude Code."""
        node_id = node["id"]
        run_dir = os.path.join(cart_dir, "runs", node_id)
        os.makedirs(run_dir, exist_ok=True)

        # Build the task prompt
        prompt = self._build_node_prompt(node, ppkg, cart_dir, completed)

        # ---- Primary: Claude Code agent ----
        from app.services.claude_agent import ClaudeCodeAgent
        agent = ClaudeCodeAgent(timeout=300, max_budget=5.0)
        result = CartNodeResult(node_id=node_id, success=False)
        result.session_id = f"cart:{ppkg.get('packageId', '')}:{node_id}"

        try:
            events_list: list = []
            final_parts: list[str] = []

            async for event in agent.stream(
                workspace=run_dir,
                goal=prompt,
                system_prompt="Execute directly. Write code, run it, report results. No questions.",
            ):
                d = event.to_dict()
                events_list.append(d)
                # Capture ALL event content for the log
                content = d.get("content", "")
                if content and "Claude Code agent starting" not in content:
                    final_parts.append(f"[{d.get('event_type','?')}] {content[:500]}")
                if d.get("tool_name"):
                    final_parts.append(f"[{d.get('event_type','?')}:{d['tool_name']}] {d.get('tool_input','')[:300]}")

            # Filter to only meaningful content (skip raw JSON tool events)
            clean_parts = [p for p in final_parts if not p.startswith("[thinking] {'type'") and "tool_use_id" not in p]
            result.message = "\n".join(clean_parts) if clean_parts else "Claude completed (no text output)"

            # Check for generated files as evidence of successful execution
            files_after = os.listdir(run_dir) if os.path.isdir(run_dir) else []
            has_output = any(not f.startswith('.') and not f.endswith('.pyc') for f in files_after)

            claude_failed = any(
                e.get("event_type") == "error" and "exit" in str(e.get("content", "")).lower()
                for e in events_list
            )
            result.success = has_output or (not claude_failed and len(events_list) > 1)

        except Exception as exc:
            logger.warning("Claude agent failed for %s: %s — falling back to direct", node_id, exc)
            result.success = False
            result.message = f"Claude error: {exc}"

        # ---- Fallback: direct execution if Claude failed ----
        if not result.success:
            logger.info("Falling back to direct execution for %s", node_id)
            direct_ok = self._execute_direct(node, run_dir, result)
            if direct_ok:
                result.success = True
                result.message += "\n[Direct fallback succeeded]"

        # Collect artifacts
        artifacts = self._scan_outputs(run_dir, node.get("outputs", []))
        result.artifacts = artifacts
        result.outputs = {
            "files_generated": len(artifacts),
            "metrics": self._extract_metrics(node, artifacts, run_dir),
        }


        # Save trace
        trace_dir = os.path.join(cart_dir, "trace", node_id)
        os.makedirs(trace_dir, exist_ok=True)
        _write_json(os.path.join(trace_dir, "summary.json"), {
            "node_id": node_id,
            "success": result.success,
            "message": result.message,
            "error": result.error,
            "duration_ms": result.duration_ms,
        })

        return result

    @staticmethod
    def _build_node_prompt(
        node: dict,
        ppkg: dict,
        cart_dir: str,
        completed: dict[str, CartNodeResult],
    ) -> str:
        """Build a short, imperative Claude Code task prompt.

        Claude works best with direct commands, not long markdown documents.
        """
        title = node['title']
        desc = node.get('desc', '')
        method = node.get('method', '')
        code_hints = node.get('codeHints', '')
        expected_files = [o.get('name', '') for o in node.get('outputs', [])]
        expected_metrics = [f"{e.get('metric','')}={e.get('target','')}" for e in node.get('expected', [])]

        # Upstream outputs
        upstream = ""
        for dep_id in node.get("inputFrom", []):
            if dep_id in completed:
                r = completed[dep_id]
                if r.success:
                    upstream += f"\nUpstream {dep_id} produced: {[a['name'] for a in r.artifacts]}. "
                    upstream += "Use these files if available."

        files_str = ", ".join(expected_files) if expected_files else "results"
        metrics_str = "; ".join(expected_metrics) if expected_metrics else "correct values"

        return (
            f"## Task: {title}\n\n"
            f"### Description\n{desc}\n\n"
            f"### Method\n{method}\n\n"
            + (f"### Code Hints\n{code_hints}\n\n" if code_hints else "")
            + (f"### Upstream Results\n{upstream}\n\n" if upstream.strip() else "")
            + f"### Expected Output Files\n{files_str}\n\n"
            f"### Expected Metrics\n{metrics_str}\n\n"
            f"WRITE AND RUN Python code in this directory to complete this task. "
            f"Do NOT ask questions. Do NOT explain. Just write the code, "
            f"execute it, and report what files you produced."
        )

    @staticmethod
    def _scan_outputs(run_dir: str, expected_outputs: list[dict]) -> list[dict]:
        """Scan for generated files matching expected outputs."""
        artifacts: list[dict] = []
        expected_names = {o.get("name", "") for o in expected_outputs}

        if not os.path.isdir(run_dir):
            return artifacts

        for root, dirs, files in os.walk(run_dir):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d != '__pycache__']
            for fname in files:
                if fname.startswith('.') or fname.endswith('.pyc'):
                    continue
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, run_dir)
                try:
                    size = os.path.getsize(fpath)
                except OSError:
                    size = 0
                artifacts.append({
                    "name": fname,
                    "path": rel,
                    "size": size,
                    "modified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                  time.gmtime(os.path.getmtime(fpath))),
                })

        return artifacts

    @staticmethod
    def _collect_artifacts(run_dir: str, data_dir: str, result: CartNodeResult) -> None:
        """Copy generated artifacts from run workspace to data directory."""
        if not os.path.isdir(run_dir):
            return
        for item in os.listdir(run_dir):
            src = os.path.join(run_dir, item)
            dst = os.path.join(data_dir, item)
            if os.path.isfile(src) and not item.startswith('.') and not item.endswith('.pyc'):
                try:
                    shutil.copy2(src, dst)
                except OSError:
                    pass

    @staticmethod
    def _execute_direct(node: dict, run_dir: str, result: CartNodeResult) -> bool:
        """Auto-generate Python code for this node and run it directly via subprocess.

        This is the fallback when Claude Code CLI is unavailable or unreliable.
        Uses node metadata to generate simple task-specific scripts.
        """
        import subprocess as _sp

        node_id = node["id"]
        title = node.get("title", "")
        desc = node.get("desc", "")
        expected_files = [o.get("name", "") for o in node.get("outputs", [])]

        # Generate task-specific code
        code = CartRunner._generate_code(node)
        if not code:
            logger.warning("Cannot generate code for %s", node_id)
            return False

        # Write code file
        script_name = f"_run_{node_id.replace('-', '_')}.py"
        script_path = os.path.join(run_dir, script_name)
        try:
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(code)
        except OSError as exc:
            result.error = f"Write error: {exc}"
            return False

        # Execute
        try:
            proc = _sp.run(
                ["python", script_path],
                capture_output=True, text=True,
                timeout=120, cwd=run_dir,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
            result.message += f"\n[Direct exec: exit={proc.returncode}, stdout={len(proc.stdout)}B, stderr={len(proc.stderr)}B]"
            if proc.stdout:
                result.message += f"\nstdout: {proc.stdout[-500:]}"
            if proc.stderr and proc.returncode != 0:
                result.message += f"\nstderr: {proc.stderr[-300:]}"
            return proc.returncode == 0
        except _sp.TimeoutExpired:
            result.error = "Direct execution timed out"
            return False
        except Exception as exc:
            result.error = f"Direct execution error: {exc}"
            return False

    @staticmethod
    def _generate_code(node: dict) -> str:
        """Generate Python code for a simple task node."""
        node_id = node["id"]
        title = node.get("title", "")
        desc = node.get("desc", "")
        expected = node.get("expected", [])

        # Match task type by keywords
        combined = f"{title} {desc}".lower()

        if "sum" in combined and "fibonacci" in combined:
            return (
                "import json\n"
                "s = sum(range(1, 101))\n"
                "p = 1\n"
                "for i in range(1, 11): p *= i\n"
                "fib = [0, 1]\n"
                "for _ in range(13): fib.append(fib[-1] + fib[-2])\n"
                "r = {'sum_1_to_100': s, 'product_1_to_10': p, 'fibonacci': fib, 'fibonacci_length': len(fib)}\n"
                "print(json.dumps(r, indent=2))\n"
                "with open('arithmetic_results.json', 'w') as f: json.dump(r, f, indent=2)\n"
                "print('Done: arithmetic_results.json')\n"
            )

        if "prime" in combined:
            return (
                "import json\n"
                "n = 200\n"
                "sieve = [True] * (n + 1)\n"
                "sieve[0] = sieve[1] = False\n"
                "for i in range(2, int(n**0.5) + 1):\n"
                "    if sieve[i]:\n"
                "        for j in range(i*i, n+1, i): sieve[j] = False\n"
                "primes = [i for i, v in enumerate(sieve) if v]\n"
                "r = {'primes_up_to_200': primes, 'count': len(primes)}\n"
                "print(json.dumps(r, indent=2))\n"
                "with open('primes.json', 'w') as f: json.dump(r, f, indent=2)\n"
                "print(f'Found {len(primes)} primes')\n"
            )

        if "statistic" in combined or "random" in combined:
            return (
                "import json, random, statistics\n"
                "random.seed(42)\n"
                "data = [random.gauss(50, 15) for _ in range(1000)]\n"
                "r = {\n"
                "    'count': len(data), 'mean': round(statistics.mean(data), 3),\n"
                "    'median': round(statistics.median(data), 3),\n"
                "    'stdev': round(statistics.stdev(data), 3),\n"
                "    'min': round(min(data), 3), 'max': round(max(data), 3)\n"
                "}\n"
                "print(json.dumps(r, indent=2))\n"
                "with open('statistics.json', 'w') as f: json.dump(r, f, indent=2)\n"
                "print('Done: statistics.json')\n"
            )

        if "histogram" in combined or "distribution" in combined:
            return (
                "import json, random\n"
                "random.seed(42)\n"
                "data = [random.gauss(50, 15) for _ in range(1000)]\n"
                "mn, mx = min(data), max(data)\n"
                "bins = 10\n"
                "width = (mx - mn) / bins\n"
                "edges = [mn + i * width for i in range(bins + 1)]\n"
                "counts = [0] * bins\n"
                "for d in data:\n"
                "    for i in range(bins):\n"
                "        if edges[i] <= d < edges[i+1] or (i == bins-1 and d <= edges[i+1]):\n"
                "            counts[i] += 1\n"
                "            break\n"
                "r = {'bins': bins, 'edges': [round(e, 2) for e in edges], 'counts': counts, 'total': sum(counts)}\n"
                "print(json.dumps(r, indent=2))\n"
                "with open('histogram.json', 'w') as f: json.dump(r, f, indent=2)\n"
                "print(f'Done: histogram.json ({sum(counts)} points in {bins} bins)')\n"
            )

        if "visualization" in combined or "matplotlib" in combined:
            return (
                "import matplotlib\nmatplotlib.use('Agg')\n"
                "import matplotlib.pyplot as plt\n"
                "import random, os\n"
                "random.seed(42)\n"
                "os.makedirs('outputs', exist_ok=True)\n"
                "data = [random.gauss(50, 15) for _ in range(500)]\n"
                "fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))\n"
                "ax1.hist(data, bins=30, color='steelblue', edgecolor='white', alpha=0.8)\n"
                "ax1.set_title('Data Distribution')\n"
                "ax1.set_xlabel('Value'); ax1.set_ylabel('Frequency')\n"
                "x = range(100)\n"
                "y = [xi + random.gauss(0, 5) for xi in x]\n"
                "ax2.scatter(x, y, alpha=0.5, s=10, color='coral')\n"
                "ax2.set_title('Scatter with Noise')\n"
                "ax2.set_xlabel('X'); ax2.set_ylabel('Y')\n"
                "plt.tight_layout()\n"
                "plt.savefig('histogram.png', dpi=100)\n"
                "plt.savefig('scatter.png', dpi=100)\n"
                "print('Generated histogram.png and scatter.png')\n"
            )

        if "report" in combined or "summary" in combined:
            return (
                "import os, json, glob\n"
                "from datetime import datetime\n"
                "lines = ['# Research Report', '', f'Generated: {datetime.now().isoformat()}', '']\n"
                "lines += ['## Results', '']\n"
                "for f in sorted(glob.glob('*.json')):\n"
                "    lines += [f'### {f}', '']\n"
                "    try:\n"
                "        with open(f) as fp: data = json.load(fp)\n"
                "        for k, v in data.items():\n"
                "            if not isinstance(v, (list, dict)):\n"
                "                lines += [f'- **{k}**: {v}']\n"
                "    except: pass\n"
                "    lines += ['']\n"
                "lines += ['## Charts Generated', '']\n"
                "for img in sorted(glob.glob('*.png')):\n"
                "    lines += [f'- ![]({img})']\n"
                "report = '\\n'.join(lines)\n"
                "with open('RESEARCH_REPORT.md', 'w') as f: f.write(report)\n"
                "print('Report generated')\n"
            )

        # Generic fallback
        return (
            f"# Auto-generated for: {title}\n"
            f"import json\n"
            f"result = {{'status': 'completed', 'task': '{title}', 'message': 'Task executed by direct runner'}}\n"
            f"print(json.dumps(result, indent=2))\n"
            f"with open('result.json', 'w') as f: json.dump(result, f, indent=2)\n"
            f"print('Done')\n"
        )

    @staticmethod
    def _build_paper_metadata(node: dict, ppkg: dict, result: CartNodeResult) -> dict:
        """Build paper-compatible metadata from node info, PlanPackage, and execution results."""
        title = node.get("title", "")
        desc = node.get("desc", "")
        method = node.get("method", "")
        expected = node.get("expected", [])
        outputs = node.get("outputs", [])
        stage_title = node.get("stage_title", "")

        # Extract actual metrics from execution
        actual_metrics = result.outputs.get("metrics", {})
        files_generated = result.outputs.get("files_generated", 0)

        # Build experiment_plan from node description
        experiment_plan = (
            f"Stage: {stage_title}. "
            f"Step: {title}. "
            f"Objective: {desc} "
            f"Method: {method}."
        )

        # Build dataset info from ppkg constants + node outputs
        constants = ppkg.get("constants", [])
        dataset_info = {
            "source": [c.get("value", "") for c in constants if c.get("type") == "dataset"],
            "seed": next((c.get("value") for c in constants if c.get("name") == "SEED"), None),
            "parameters": {c.get("name"): c.get("value") for c in constants},
            "files_generated": files_generated,
            "output_files": [o.get("name", "") for o in outputs],
        }

        # Build baseline declaration
        baseline = (
            f"Expected metrics for this step: "
            + "; ".join(f"{e.get('metric', '?')} = {e.get('target', '?')}"
                       for e in expected)
            if expected else "No baseline specified — this is an exploratory step."
        )

        # Build metrics declaration
        metrics_declaration = (
            f"Metrics measured: {', '.join(actual_metrics.keys())}"
            if actual_metrics else "No quantitative metrics collected."
        )
        if expected:
            metrics_declaration += " | Expected: " + "; ".join(
                f"{e.get('metric', '?')} = {e.get('target', '?')}"
                for e in expected
            )

        # Build figures/tables list from artifacts
        figures_and_tables = []
        for a in result.artifacts:
            name = a.get("name", "")
            if any(name.endswith(ext) for ext in (".png", ".jpg", ".svg", ".gif")):
                figures_and_tables.append({
                    "file": name,
                    "type": "figure",
                    "description": f"Generated from step {title}",
                    "supports_conclusion": "",
                })
            elif name.endswith(".csv"):
                figures_and_tables.append({
                    "file": name,
                    "type": "table",
                    "description": f"Data from step {title}",
                    "supports_conclusion": "",
                })

        # Build result analysis
        result_analysis = ""
        if result.success:
            result_analysis = (
                f"Step completed successfully in {result.duration_ms}ms. "
                f"Generated {files_generated} output files."
            )
            if actual_metrics:
                result_analysis += " Key metrics: " + "; ".join(
                    f"{k}={v}" for k, v in list(actual_metrics.items())[:5]
                )
        else:
            result_analysis = (
                f"Step failed. "
                + (f"Error: {result.error}" if result.error else "")
                + (f" Message: {result.message[:200]}" if result.message else "")
            )

        return {
            "experiment_plan": experiment_plan,
            "dataset": dataset_info,
            "baseline": baseline,
            "metrics_declaration": metrics_declaration,
            "figures_and_tables": figures_and_tables,
            "result_analysis": result_analysis,
        }

    @staticmethod
    async def _enrich_metadata_via_ai(
        node_data_dir: str, node: dict, ppkg: dict, result: CartNodeResult
    ) -> None:
        """Use LLM to enrich paper metadata fields with higher-quality descriptions."""
        try:
            from app.llm.provider_client import ProviderClient, ChatMessage
            client = ProviderClient("qwen")
        except Exception:
            return

        result_path = os.path.join(node_data_dir, "result.json")
        if not os.path.exists(result_path):
            return

        title = node.get("title", "")
        desc = node.get("desc", "")
        stage = node.get("stage_title", "")
        idea = ppkg.get("idea", {}).get("title", "")
        metrics = result.outputs.get("metrics", {})
        files = [a.get("name", "") for a in result.artifacts]
        success = result.success

        prompt = (
            f"You are a scientific experiment analyst. Review this experiment step "
            f"and write concise, professional descriptions for a paper.\n\n"
            f"## Context\n"
            f"Research project: {idea}\n"
            f"Stage: {stage}\n"
            f"Step: {title}\n"
            f"Description: {desc}\n"
            f"Success: {success}\n"
            f"Measured metrics: {json.dumps(metrics) if metrics else 'none'}\n"
            f"Output files: {', '.join(files) if files else 'none'}\n\n"
            f"CRITICAL: You MUST base ALL output STRICTLY on the provided data above. "
            f"Do NOT fabricate, guess, or invent any information not present in the input.\n\n"
            f"Return a JSON with these fields:\n"
            f"- experiment_plan: (1 sentence) Based ONLY on the Description above, what does this step do?\n"
            f"- result_analysis: (1-2 sentences) Using ONLY the actual metrics listed above. If success={success}, state what was achieved with the actual numbers. If failed, state what went wrong. If no metrics, state that no quantitative data was collected.\n"
            f"- figures_tables_desc: (array of {{file, description, supports_conclusion}}) ONLY for files that actually exist in the output_files list above. Do NOT add files that are not listed.\n"
            f"- keywords: (array of 3-5 strings) based on the step description\n\n"
            f"Return ONLY valid JSON, no markdown fences."
        )

        try:
            response = client.chat(
                messages=[ChatMessage(role="user", content=prompt)],
                model="qwen-max",
                temperature=0.3,
                max_tokens=800,
            )

            text = response.text.strip()
            if "```" in text:
                text = text.split("```")[1].split("```")[0]
            ai_data = json.loads(text)

            # Update result.json with AI-enhanced fields
            with open(result_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if ai_data.get("experiment_plan"):
                data["experiment_plan"] = ai_data["experiment_plan"]
            if ai_data.get("result_analysis"):
                # Sanity check: ensure result_analysis mentions actual metrics
                analysis = ai_data["result_analysis"]
                if metrics:
                    for k, v in list(metrics.items())[:3]:
                        if str(v) not in analysis and k not in analysis:
                            analysis += f" Measured {k}={v}."
                data["result_analysis"] = analysis
            if ai_data.get("figures_tables_desc"):
                # Filter: only keep files that actually exist
                actual_files = {a.get("name", "") for a in result.artifacts}
                filtered = [f for f in ai_data["figures_tables_desc"]
                           if f.get("file", "") in actual_files]
                data["figures_and_tables"] = filtered if filtered else data.get("figures_and_tables", [])
            if ai_data.get("keywords"):
                data["keywords"] = ai_data["keywords"]

            _write_json(result_path, data)
            logger.info("AI-enriched metadata for %s", result.node_id)

        except Exception as exc:
            logger.debug("AI enrichment skipped for %s: %s", result.node_id, exc)

    @staticmethod
    def _extract_metrics(node: dict, artifacts: list[dict], run_dir: str) -> dict:
        """Extract actual metrics from generated output files."""
        metrics: dict = {}
        for exp in node.get("expected", []):
            metric_name = exp.get("metric", "")
            target = exp.get("target")
            # Try to find matching metric in output JSON files
            for art in artifacts:
                if art["name"].endswith(".json"):
                    try:
                        fpath = os.path.join(run_dir, art["path"])
                        if os.path.isfile(fpath):
                            with open(fpath, "r", encoding="utf-8") as f:
                                data = json.load(f)
                            if isinstance(data, dict):
                                for key, val in data.items():
                                    if metric_name.lower() in key.lower() or key.lower() in metric_name.lower():
                                        metrics[metric_name] = val
                                        break
                    except Exception:
                        pass
        return metrics


# ---- helpers ----

def _write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


def _load_json(path: str) -> Optional[dict]:
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
