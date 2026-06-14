"""
Plan Session Storage

Provides file-based storage for plan sessions and candidate plans.
Follows the same pattern as idea_storage.py.
"""

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

from app.models.plan_session import (
    PlanSession,
    PlanSessionStatus,
    CandidatePlan,
    SelectedPlan,
)


def generate_plan_session_id() -> str:
    return f"psess_{uuid.uuid4().hex[:12]}"


def generate_candidate_plan_id() -> str:
    return f"cplan_{uuid.uuid4().hex[:12]}"


def generate_selected_plan_id() -> str:
    return f"splan_{uuid.uuid4().hex[:12]}"


def _dt_to_iso(val):
    if isinstance(val, datetime):
        return val.isoformat()
    return val


def _iso_to_dt(val):
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val)
        except (ValueError, TypeError):
            return val
    return val


class PlanSessionStorage:
    """Storage for plan generation sessions."""

    def __init__(self, data_dir: str = "data"):
        self.base_path = Path(data_dir) / "plan_sessions" / "sessions"
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self.base_path / f"{session_id}.json"

    def _serialize(self, session: PlanSession) -> Dict[str, Any]:
        data = session.model_dump()
        for key in ['createdAt', 'startedAt', 'endedAt']:
            if data.get(key):
                data[key] = _dt_to_iso(data[key])
        if data.get('trace'):
            for tk in ['startedAt', 'endedAt']:
                if data['trace'].get(tk):
                    data['trace'][tk] = _dt_to_iso(data['trace'][tk])
            for step in data['trace'].get('steps', []):
                for sk in ['startedAt', 'endedAt']:
                    if step.get(sk):
                        step[sk] = _dt_to_iso(step[sk])
        return data

    def _deserialize(self, data: Dict[str, Any]) -> PlanSession:
        for key in ['createdAt', 'startedAt', 'endedAt']:
            if data.get(key):
                data[key] = _iso_to_dt(data[key])
        if data.get('trace'):
            for tk in ['startedAt', 'endedAt']:
                if data['trace'].get(tk):
                    data['trace'][tk] = _iso_to_dt(data['trace'][tk])
            for step in data['trace'].get('steps', []):
                for sk in ['startedAt', 'endedAt']:
                    if step.get(sk):
                        step[sk] = _iso_to_dt(step[sk])
        return PlanSession(**data)

    def create(self, session: PlanSession) -> PlanSession:
        path = self._path(session.id)
        with open(path, 'w') as f:
            json.dump(self._serialize(session), f, indent=2, default=str)
        return session

    def get(self, session_id: str) -> Optional[PlanSession]:
        path = self._path(session_id)
        if not path.exists():
            return None
        with open(path, 'r') as f:
            return self._deserialize(json.load(f))

    def update(self, session: PlanSession) -> PlanSession:
        return self.create(session)

    def list_all(self, status: Optional[PlanSessionStatus] = None) -> List[PlanSession]:
        sessions = []
        for p in sorted(self.base_path.glob("psess_*.json"), reverse=True):
            try:
                with open(p, 'r') as f:
                    s = self._deserialize(json.load(f))
                if status is None or s.status == status:
                    sessions.append(s)
            except Exception:
                continue
        # Newest first (createdAt descending)
        sessions.sort(key=lambda s: s.createdAt or datetime.min, reverse=True)
        return sessions


class CandidatePlanStorage:
    """Storage for candidate plans."""

    def __init__(self, data_dir: str = "data"):
        self.base_path = Path(data_dir) / "plan_sessions" / "candidates"
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _path(self, candidate_id: str) -> Path:
        return self.base_path / f"{candidate_id}.json"

    def _serialize(self, c: CandidatePlan) -> Dict[str, Any]:
        data = c.model_dump()
        if data.get('createdAt'):
            data['createdAt'] = _dt_to_iso(data['createdAt'])
        return data

    def _deserialize(self, data: Dict[str, Any]) -> CandidatePlan:
        if data.get('createdAt'):
            data['createdAt'] = _iso_to_dt(data['createdAt'])
        return CandidatePlan(**data)

    def create(self, candidate: CandidatePlan) -> CandidatePlan:
        path = self._path(candidate.id)
        with open(path, 'w') as f:
            json.dump(self._serialize(candidate), f, indent=2, default=str)
        return candidate

    def get(self, candidate_id: str) -> Optional[CandidatePlan]:
        path = self._path(candidate_id)
        if not path.exists():
            return None
        with open(path, 'r') as f:
            return self._deserialize(json.load(f))

    def list_by_session(self, session_id: str) -> List[CandidatePlan]:
        candidates = []
        for p in sorted(self.base_path.glob("cplan_*.json")):
            try:
                with open(p, 'r') as f:
                    c = self._deserialize(json.load(f))
                if c.sessionId == session_id:
                    candidates.append(c)
            except Exception:
                continue
        candidates.sort(key=lambda c: c.indexNumber)
        return candidates


class SelectedPlanStorage:
    """Storage for selected plan records."""

    def __init__(self, data_dir: str = "data"):
        self.base_path = Path(data_dir) / "plan_sessions" / "selections"
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _path(self, sel_id: str) -> Path:
        return self.base_path / f"{sel_id}.json"

    def create(self, sel: SelectedPlan) -> SelectedPlan:
        path = self._path(sel.id)
        data = sel.model_dump()
        if data.get('createdAt'):
            data['createdAt'] = _dt_to_iso(data['createdAt'])
        with open(path, 'w') as f:
            json.dump(data, f, indent=2, default=str)
        return sel

    def get(self, sel_id: str) -> Optional[SelectedPlan]:
        path = self._path(sel_id)
        if not path.exists():
            return None
        with open(path, 'r') as f:
            data = json.load(f)
        if data.get('createdAt'):
            data['createdAt'] = _iso_to_dt(data['createdAt'])
        return SelectedPlan(**data)


# Singleton instances
_session_storage: Optional[PlanSessionStorage] = None
_candidate_storage: Optional[CandidatePlanStorage] = None
_selected_storage: Optional[SelectedPlanStorage] = None


def _get_data_dir() -> str:
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "data")


def get_session_storage() -> PlanSessionStorage:
    global _session_storage
    if _session_storage is None:
        _session_storage = PlanSessionStorage(_get_data_dir())
    return _session_storage


def get_candidate_storage() -> CandidatePlanStorage:
    global _candidate_storage
    if _candidate_storage is None:
        _candidate_storage = CandidatePlanStorage(_get_data_dir())
    return _candidate_storage


def get_selected_storage() -> SelectedPlanStorage:
    global _selected_storage
    if _selected_storage is None:
        _selected_storage = SelectedPlanStorage(_get_data_dir())
    return _selected_storage
