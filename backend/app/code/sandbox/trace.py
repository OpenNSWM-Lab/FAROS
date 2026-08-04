"""
ExecutionTrace — Structured, append-only event log for agent runs.

Provides a lightweight execution provenance system that records every
step of the autonomous agent loop (plan, execute, observe, repair, complete).
Events are persisted as JSON files under data/code_agent_traces/.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Trace storage directory (relative to project root, resolved at runtime)
_TRACE_DIR: Optional[str] = None


def _get_trace_dir() -> str:
    """Resolve the trace storage directory.

    Uses the same backend/data/ resolution as engine.py to ensure consistency.
    """
    global _TRACE_DIR
    if _TRACE_DIR is None:
        try:
            from app.db.engine import _DATA_DIR
            backend_data = _DATA_DIR
        except Exception:
            # Fallback: resolve from this file's location
            backend_dir = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            )
            backend_data = os.path.join(backend_dir, "data")
        _TRACE_DIR = os.path.join(backend_data, "code_agent_traces")
    os.makedirs(_TRACE_DIR, exist_ok=True)
    return _TRACE_DIR


@dataclass
class ExecutionEvent:
    """A single event in the execution trace."""

    step: str  # "plan", "setup", "execute", "observe", "repair", "complete", "error"
    status: str  # "started", "succeeded", "failed", "skipped"
    message: str = ""
    details: dict = field(default_factory=dict)
    duration_ms: int = 0
    iteration: int = 0
    sandbox_id: Optional[str] = None
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    def to_dict(self) -> dict:
        return {
            "step": self.step,
            "status": self.status,
            "message": self.message,
            "details": self.details,
            "duration_ms": self.duration_ms,
            "iteration": self.iteration,
            "sandbox_id": self.sandbox_id,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ExecutionEvent":
        return cls(
            step=data.get("step", "unknown"),
            status=data.get("status", "unknown"),
            message=data.get("message", ""),
            details=data.get("details", {}),
            duration_ms=data.get("duration_ms", 0),
            iteration=data.get("iteration", 0),
            sandbox_id=data.get("sandbox_id"),
            timestamp=data.get("timestamp", ""),
        )


class ExecutionTrace:
    """Append-only event sequence for one autonomous agent run.

    Usage:
        trace = ExecutionTrace(trace_id="agent_abc123", project_id="proj_xyz")
        trace.record(ExecutionEvent(step="plan", status="started", ...))
        # ... run agent ...
        trace.record(ExecutionEvent(step="complete", status="succeeded", ...))
        summary = trace.summary()
    """

    def __init__(self, trace_id: str, project_id: str, goal: str = ""):
        self.trace_id = trace_id
        self.project_id = project_id
        self.goal = goal
        self.events: list[ExecutionEvent] = []
        self._start_time = time.time()

    def record(self, event: ExecutionEvent) -> None:
        """Append an event and persist to disk."""
        self.events.append(event)
        self._flush()

    def summary(self) -> dict:
        """Return structured summary of the entire run."""
        if not self.events:
            return {
                "trace_id": self.trace_id,
                "project_id": self.project_id,
                "goal": self.goal,
                "total_events": 0,
                "status": "no_events",
                "total_duration_ms": 0,
                "iterations": 0,
                "repairs_applied": 0,
                "errors": [],
            }

        total_ms = sum(e.duration_ms for e in self.events)
        iterations = max((e.iteration for e in self.events), default=0)

        # Find final status from last event
        last_event = self.events[-1]
        if last_event.step in ("complete", "error"):
            final_status = last_event.status
        else:
            final_status = "incomplete"

        # Count repairs
        repairs = sum(
            1 for e in self.events if e.step == "repair" and e.status == "succeeded"
        )

        # Collect errors
        errors = [
            {"step": e.step, "iteration": e.iteration, "message": e.message}
            for e in self.events
            if e.status in ("failed", "error")
        ]

        return {
            "trace_id": self.trace_id,
            "project_id": self.project_id,
            "goal": self.goal,
            "total_events": len(self.events),
            "status": final_status,
            "total_duration_ms": total_ms,
            "wall_time_sec": round(time.time() - self._start_time, 1),
            "iterations": iterations,
            "repairs_applied": repairs,
            "errors": errors,
        }

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "project_id": self.project_id,
            "goal": self.goal,
            "events": [e.to_dict() for e in self.events],
            "summary": self.summary(),
        }

    # ---- persistence ----

    def _get_file_path(self) -> str:
        return os.path.join(_get_trace_dir(), f"{self.trace_id}.json")

    def _flush(self) -> None:
        """Persist all events to a JSON file."""
        try:
            path = self._get_file_path()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        except Exception as exc:
            logger.warning("Failed to flush trace %s: %s", self.trace_id, exc)

    # ---- static helpers ----

    @staticmethod
    def load(trace_id: str) -> Optional["ExecutionTrace"]:
        """Load a trace from disk by its ID."""
        path = os.path.join(_get_trace_dir(), f"{trace_id}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            trace = ExecutionTrace(
                trace_id=data.get("trace_id", trace_id),
                project_id=data.get("project_id", ""),
                goal=data.get("goal", ""),
            )
            trace.events = [
                ExecutionEvent.from_dict(e) for e in data.get("events", [])
            ]
            return trace
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning("Failed to load trace %s: %s", trace_id, exc)
            return None

    @staticmethod
    def list_traces(project_id: Optional[str] = None) -> list[dict]:
        """List all saved traces, optionally filtered by project_id."""
        trace_dir = _get_trace_dir()
        traces = []
        try:
            for filename in sorted(os.listdir(trace_dir), reverse=True):
                if not filename.endswith(".json"):
                    continue
                trace_id = filename[:-5]  # strip .json
                path = os.path.join(trace_dir, filename)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except (json.JSONDecodeError, OSError):
                    continue

                pid = data.get("project_id", "")
                if project_id and pid != project_id:
                    continue

                summary = data.get("summary", {})
                traces.append({
                    "trace_id": trace_id,
                    "project_id": pid,
                    "goal": data.get("goal", ""),
                    "status": summary.get("status", "unknown"),
                    "iterations": summary.get("iterations", 0),
                    "repairs_applied": summary.get("repairs_applied", 0),
                    "total_duration_ms": summary.get("total_duration_ms", 0),
                    "errors": summary.get("errors", []),
                })
        except FileNotFoundError:
            pass
        return traces
