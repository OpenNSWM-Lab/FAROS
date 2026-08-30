"""
Code Session Storage - Persists CodeSession and CodeCandidate objects.
"""

import os
import json
import logging
import tempfile
import shutil
from typing import List, Optional, Dict, Any

from app.core.paths import get_data_dir

logger = logging.getLogger(__name__)

DATA_DIR = str(get_data_dir() / "code_sessions")


class CodeSessionStorage:
    """File-based storage for code sessions and candidates."""
    
    def __init__(self, data_dir: str = DATA_DIR):
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)
    
    def _get_session_path(self, session_id: str) -> str:
        return os.path.join(self.data_dir, f"{session_id}.json")
    
    def _get_candidate_dir(self, session_id: str) -> str:
        return os.path.join(self.data_dir, session_id)
    
    def _get_candidate_path(self, candidate_id: str) -> str:
        # Candidates are stored in a shared candidates directory
        candidates_dir = os.path.join(self.data_dir, "candidates")
        os.makedirs(candidates_dir, exist_ok=True)
        return os.path.join(candidates_dir, f"{candidate_id}.json")
    
    def save(self, session_id: str, data: Dict[str, Any]) -> None:
        """Save session data."""
        file_path = self._get_session_path(session_id)
        fd, temp_path = tempfile.mkstemp(suffix=".json", dir=self.data_dir)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, default=str)
            shutil.move(temp_path, file_path)
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise e
    
    def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session by ID."""
        file_path = self._get_session_path(session_id)
        if not os.path.exists(file_path):
            return None
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load session {session_id}: {e}")
            return None
    
    def delete(self, session_id: str) -> bool:
        """Delete a session."""
        file_path = self._get_session_path(session_id)
        if not os.path.exists(file_path):
            return False
        try:
            os.remove(file_path)
            # Also remove candidate directory if exists
            cand_dir = self._get_candidate_dir(session_id)
            if os.path.exists(cand_dir):
                shutil.rmtree(cand_dir)
            return True
        except Exception as e:
            logger.error(f"Failed to delete session {session_id}: {e}")
            return False
    
    def list_all(self) -> List[Dict[str, Any]]:
        """List all sessions (metadata only)."""
        sessions = []
        for filename in os.listdir(self.data_dir):
            if not filename.endswith('.json') or filename.startswith('candidates'):
                continue
            session_id = filename[:-5]
            if session_id == "candidates":
                continue
            data = self.get(session_id)
            if data:
                sessions.append({
                    "id": data.get("id", session_id),
                    "status": data.get("status"),
                    "config": data.get("config", {}),
                    "createdAt": data.get("createdAt"),
                    "startedAt": data.get("startedAt"),
                    "endedAt": data.get("endedAt"),
                    "duration": data.get("duration"),
                    "candidateCount": len(data.get("candidateIds", [])),
                    "selectedCandidateId": data.get("selectedCandidateId"),
                    "summary": data.get("summary"),
                })
        sessions.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return sessions
    
    def save_candidate(self, candidate_id: str, data: Dict[str, Any]) -> None:
        """Save candidate data."""
        file_path = self._get_candidate_path(candidate_id)
        fd, temp_path = tempfile.mkstemp(suffix=".json", dir=os.path.dirname(file_path))
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, default=str)
            shutil.move(temp_path, file_path)
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise e
    
    def get_candidate(self, candidate_id: str) -> Optional[Dict[str, Any]]:
        """Get candidate by ID."""
        file_path = self._get_candidate_path(candidate_id)
        if not os.path.exists(file_path):
            return None
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load candidate {candidate_id}: {e}")
            return None
    
    def get_candidates_for_session(self, session_id: str) -> List[Dict[str, Any]]:
        """Get all candidates for a session."""
        session = self.get(session_id)
        if not session:
            return []
        
        candidates = []
        for cand_id in session.get("candidateIds", []):
            cand = self.get_candidate(cand_id)
            if cand:
                candidates.append(cand)
        
        # Sort by rank
        candidates.sort(key=lambda c: c.get("rank", 999))
        return candidates


_storage: Optional[CodeSessionStorage] = None

def get_storage() -> CodeSessionStorage:
    global _storage
    if _storage is None:
        _storage = CodeSessionStorage()
    return _storage
