"""
Idea Generation Storage

Provides file-based storage for idea sessions, literature items, and candidates.
Follows append-only pattern for scientific integrity.
"""

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

from app.models.idea import (
    IdeaSession,
    IdeaSessionStatus,
    LiteratureItem,
    IdeaCandidate,
)


def generate_session_id() -> str:
    """Generate unique session ID."""
    return f"idea_{uuid.uuid4().hex[:12]}"


def generate_literature_id() -> str:
    """Generate unique literature item ID."""
    return f"lit_{uuid.uuid4().hex[:12]}"


def generate_candidate_id() -> str:
    """Generate unique candidate ID."""
    return f"cand_{uuid.uuid4().hex[:12]}"


class IdeaSessionStorage:
    """Storage for idea generation sessions."""
    
    def __init__(self, data_dir: str = "backend/data"):
        self.base_path = Path(data_dir) / "ideas" / "sessions"
        self.base_path.mkdir(parents=True, exist_ok=True)
    
    def _get_session_path(self, session_id: str) -> Path:
        return self.base_path / f"{session_id}.json"
    
    def _serialize_session(self, session: IdeaSession) -> Dict[str, Any]:
        data = session.model_dump()
        # Convert datetime to ISO format
        for key in ['createdAt', 'startedAt', 'endedAt']:
            if data.get(key):
                data[key] = data[key].isoformat() if isinstance(data[key], datetime) else data[key]
        if data.get('trace'):
            if data['trace'].get('startedAt'):
                data['trace']['startedAt'] = data['trace']['startedAt'].isoformat() if isinstance(data['trace']['startedAt'], datetime) else data['trace']['startedAt']
            if data['trace'].get('endedAt'):
                data['trace']['endedAt'] = data['trace']['endedAt'].isoformat() if isinstance(data['trace']['endedAt'], datetime) else data['trace']['endedAt']
            for step in data['trace'].get('steps', []):
                if step.get('startedAt'):
                    step['startedAt'] = step['startedAt'].isoformat() if isinstance(step['startedAt'], datetime) else step['startedAt']
                if step.get('endedAt'):
                    step['endedAt'] = step['endedAt'].isoformat() if isinstance(step['endedAt'], datetime) else step['endedAt']
        return data
    
    def _deserialize_session(self, data: Dict[str, Any]) -> IdeaSession:
        # Convert ISO strings back to datetime
        for key in ['createdAt', 'startedAt', 'endedAt']:
            if data.get(key) and isinstance(data[key], str):
                data[key] = datetime.fromisoformat(data[key])
        if data.get('trace'):
            if data['trace'].get('startedAt') and isinstance(data['trace']['startedAt'], str):
                data['trace']['startedAt'] = datetime.fromisoformat(data['trace']['startedAt'])
            if data['trace'].get('endedAt') and isinstance(data['trace']['endedAt'], str):
                data['trace']['endedAt'] = datetime.fromisoformat(data['trace']['endedAt'])
            for step in data['trace'].get('steps', []):
                if step.get('startedAt') and isinstance(step['startedAt'], str):
                    step['startedAt'] = datetime.fromisoformat(step['startedAt'])
                if step.get('endedAt') and isinstance(step['endedAt'], str):
                    step['endedAt'] = datetime.fromisoformat(step['endedAt'])
        return IdeaSession(**data)
    
    def create(self, session: IdeaSession) -> IdeaSession:
        """Create a new session."""
        path = self._get_session_path(session.id)
        if path.exists():
            raise ValueError(f"Session {session.id} already exists")
        
        data = self._serialize_session(session)
        temp_path = path.with_suffix('.tmp')
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, default=str)
        temp_path.replace(path)
        
        return session
    
    def get(self, session_id: str) -> Optional[IdeaSession]:
        """Get session by ID."""
        path = self._get_session_path(session_id)
        if not path.exists():
            return None
        
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return self._deserialize_session(data)
    
    def update(self, session: IdeaSession) -> IdeaSession:
        """Update an existing session."""
        path = self._get_session_path(session.id)
        if not path.exists():
            raise ValueError(f"Session {session.id} not found")
        
        data = self._serialize_session(session)
        temp_path = path.with_suffix('.tmp')
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, default=str)
        temp_path.replace(path)
        
        return session
    
    def list_all(self, status: Optional[IdeaSessionStatus] = None) -> List[IdeaSession]:
        """List all sessions, optionally filtered by status."""
        sessions = []
        for path in self.base_path.glob("idea_*.json"):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                session = self._deserialize_session(data)
                if status is None or session.status == status:
                    sessions.append(session)
            except Exception:
                continue
        return sorted(sessions, key=lambda s: s.createdAt, reverse=True)


class LiteratureStorage:
    """Storage for literature items."""
    
    def __init__(self, data_dir: str = "backend/data"):
        self.base_path = Path(data_dir) / "ideas" / "literature"
        self.base_path.mkdir(parents=True, exist_ok=True)
    
    def _get_item_path(self, item_id: str) -> Path:
        return self.base_path / f"{item_id}.json"
    
    def create(self, item: LiteratureItem) -> LiteratureItem:
        """Create a new literature item."""
        path = self._get_item_path(item.id)
        data = item.model_dump()
        data['createdAt'] = data['createdAt'].isoformat() if isinstance(data['createdAt'], datetime) else data['createdAt']
        
        temp_path = path.with_suffix('.tmp')
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        temp_path.replace(path)
        
        return item
    
    def get(self, item_id: str) -> Optional[LiteratureItem]:
        """Get item by ID."""
        path = self._get_item_path(item_id)
        if not path.exists():
            return None
        
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if data.get('createdAt') and isinstance(data['createdAt'], str):
            data['createdAt'] = datetime.fromisoformat(data['createdAt'])
        return LiteratureItem(**data)
    
    def list_by_session(self, session_id: str) -> List[LiteratureItem]:
        """List all items for a session."""
        items = []
        for path in self.base_path.glob("lit_*.json"):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if data.get('sessionId') == session_id:
                    if data.get('createdAt') and isinstance(data['createdAt'], str):
                        data['createdAt'] = datetime.fromisoformat(data['createdAt'])
                    items.append(LiteratureItem(**data))
            except Exception:
                continue
        return sorted(items, key=lambda i: i.relevanceScore, reverse=True)


class CandidateStorage:
    """Storage for idea candidates."""
    
    def __init__(self, data_dir: str = "backend/data"):
        self.base_path = Path(data_dir) / "ideas" / "candidates"
        self.base_path.mkdir(parents=True, exist_ok=True)
    
    def _get_candidate_path(self, candidate_id: str) -> Path:
        return self.base_path / f"{candidate_id}.json"
    
    def create(self, candidate: IdeaCandidate) -> IdeaCandidate:
        """Create a new candidate."""
        path = self._get_candidate_path(candidate.id)
        data = candidate.model_dump()
        data['createdAt'] = data['createdAt'].isoformat() if isinstance(data['createdAt'], datetime) else data['createdAt']
        
        temp_path = path.with_suffix('.tmp')
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        temp_path.replace(path)
        
        return candidate
    
    def get(self, candidate_id: str) -> Optional[IdeaCandidate]:
        """Get candidate by ID."""
        path = self._get_candidate_path(candidate_id)
        if not path.exists():
            return None
        
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if data.get('createdAt') and isinstance(data['createdAt'], str):
            data['createdAt'] = datetime.fromisoformat(data['createdAt'])
        return IdeaCandidate(**data)
    
    def list_by_session(self, session_id: str) -> List[IdeaCandidate]:
        """List all candidates for a session."""
        candidates = []
        for path in self.base_path.glob("cand_*.json"):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if data.get('sessionId') == session_id:
                    if data.get('createdAt') and isinstance(data['createdAt'], str):
                        data['createdAt'] = datetime.fromisoformat(data['createdAt'])
                    candidates.append(IdeaCandidate(**data))
            except Exception:
                continue
        # Sort by overall score
        return sorted(candidates, key=lambda c: c.overallScore, reverse=True)


# Global storage instances
_session_storage: Optional[IdeaSessionStorage] = None
_literature_storage: Optional[LiteratureStorage] = None
_candidate_storage: Optional[CandidateStorage] = None


def get_session_storage() -> IdeaSessionStorage:
    global _session_storage
    if _session_storage is None:
        _session_storage = IdeaSessionStorage()
    return _session_storage


def get_literature_storage() -> LiteratureStorage:
    global _literature_storage
    if _literature_storage is None:
        _literature_storage = LiteratureStorage()
    return _literature_storage


def get_candidate_storage() -> CandidateStorage:
    global _candidate_storage
    if _candidate_storage is None:
        _candidate_storage = CandidateStorage()
    return _candidate_storage
