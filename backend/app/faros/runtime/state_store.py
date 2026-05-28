import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.faros.errors import FarosNotFoundError
from app.faros.models.execution import (
    FarosRunRecord,
    StepState,
    assert_run_status_transition,
    assert_step_status_transition,
)


class FarosStateStore:
    """File-backed state store for FAROS runs."""

    def __init__(self, root: Optional[Path] = None):
        base = Path(__file__).resolve().parents[3] / "data" / "faros" / "runs"
        self.root = root or base
        self.root.mkdir(parents=True, exist_ok=True)

    def _run_dir(self, run_id: str) -> Path:
        path = self.root / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _run_path(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "run.json"

    def _events_path(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "events.json"

    def _artifacts_path(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "artifacts.json"

    def _memory_path(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "memory.json"

    def create_run(
        self,
        blueprint_id: str,
        profile_id: str,
        execution_mode: str,
        inputs: Dict[str, Any],
        steps: List[StepState],
        preflight: Optional[Dict[str, Any]] = None,
        runtime_options: Optional[Dict[str, Any]] = None,
        checkpoint: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        run_id = f"faros_{uuid.uuid4().hex[:12]}"
        record = FarosRunRecord(
            id=run_id,
            blueprint_id=blueprint_id,
            profile_id=profile_id,
            status="planned" if execution_mode == "plan" else "pending",
            execution_mode=execution_mode,
            created_at=datetime.now(timezone.utc).isoformat(),
            inputs=inputs,
            runtime_options=runtime_options or {},
            steps=steps,
            preflight=preflight or {},
            checkpoint=checkpoint or {},
        ).model_dump()
        self._save_json(self._run_path(run_id), record)
        self._save_json(self._events_path(run_id), [])
        self._save_json(self._artifacts_path(run_id), [])
        self._save_json(self._memory_path(run_id), inputs)
        return record

    def list_runs(self) -> List[Dict[str, Any]]:
        runs = []
        for path in sorted(self.root.glob("*/run.json"), reverse=True):
            try:
                runs.append(json.loads(path.read_text()))
            except Exception:
                continue
        return runs

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        path = self._run_path(run_id)
        if not path.is_file():
            return None
        return json.loads(path.read_text())

    def update_run(self, run_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        record = self.get_run(run_id)
        if not record:
            raise FarosNotFoundError(f"FAROS run '{run_id}' not found")
        self._validate_run_updates(record, updates)
        record.update(updates)
        self._save_json(self._run_path(run_id), record)
        return record

    def update_step(self, run_id: str, node_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        record = self.get_run(run_id)
        if not record:
            raise FarosNotFoundError(f"FAROS run '{run_id}' not found")
        for step in record.get("steps", []):
            if step["node_id"] == node_id:
                self._validate_step_updates(step, updates)
                step.update(updates)
                break
        else:
            raise FarosNotFoundError(f"FAROS step '{node_id}' not found in run '{run_id}'")
        self._save_json(self._run_path(run_id), record)
        return record

    def list_events(self, run_id: str) -> List[Dict[str, Any]]:
        path = self._events_path(run_id)
        if not path.is_file():
            return []
        return json.loads(path.read_text())

    def append_event(self, run_id: str, event: Dict[str, Any]) -> None:
        events = self.list_events(run_id)
        events.append(event)
        self._save_json(self._events_path(run_id), events)

    def list_artifacts(self, run_id: str) -> List[Dict[str, Any]]:
        path = self._artifacts_path(run_id)
        if not path.is_file():
            return []
        return json.loads(path.read_text())

    def append_artifacts(self, run_id: str, artifacts: List[Dict[str, Any]]) -> None:
        existing = self.list_artifacts(run_id)
        existing.extend(artifacts)
        self._save_json(self._artifacts_path(run_id), existing)

    def get_memory(self, run_id: str) -> Dict[str, Any]:
        path = self._memory_path(run_id)
        if not path.is_file():
            return {}
        return json.loads(path.read_text())

    def save_memory(self, run_id: str, memory: Dict[str, Any]) -> None:
        self._save_json(self._memory_path(run_id), memory)

    def _validate_run_updates(self, current: Dict[str, Any], updates: Dict[str, Any]) -> None:
        if 'status' in updates:
            assert_run_status_transition(current.get('status', 'pending'), updates['status'])
        if 'steps' in updates:
            current_steps = {step['node_id']: step for step in current.get('steps', [])}
            for new_step in updates['steps']:
                node_id = new_step['node_id']
                if node_id in current_steps:
                    old_status = current_steps[node_id].get('status', 'pending')
                    new_status = new_step.get('status', old_status)
                    assert_step_status_transition(old_status, new_status, node_id)

    def _validate_step_updates(self, current_step: Dict[str, Any], updates: Dict[str, Any]) -> None:
        if 'status' in updates:
            assert_step_status_transition(current_step.get('status', 'pending'), updates['status'], current_step['node_id'])

    def _save_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, default=str))
