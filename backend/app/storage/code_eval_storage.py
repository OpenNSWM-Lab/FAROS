"""
Code Evaluation Storage - Persists EvalReport objects to JSON files.
"""

import os
import json
import logging
import tempfile
import shutil
from typing import List, Optional, Dict, Any
from datetime import datetime

from app.core.paths import get_data_dir

logger = logging.getLogger(__name__)

DATA_DIR = str(get_data_dir() / "code_evals")


class CodeEvalStorage:
    """File-based storage for code evaluation reports."""
    
    def __init__(self, data_dir: str = DATA_DIR):
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)
    
    def _get_path(self, eval_id: str) -> str:
        return os.path.join(self.data_dir, f"{eval_id}.json")
    
    def save(self, eval_id: str, data: Dict[str, Any]) -> None:
        file_path = self._get_path(eval_id)
        fd, temp_path = tempfile.mkstemp(suffix=".json", dir=self.data_dir)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, default=str)
            shutil.move(temp_path, file_path)
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise e
    
    def get(self, eval_id: str) -> Optional[Dict[str, Any]]:
        file_path = self._get_path(eval_id)
        if not os.path.exists(file_path):
            return None
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load eval {eval_id}: {e}")
            return None
    
    def list_all(self) -> List[Dict[str, Any]]:
        evals = []
        for filename in os.listdir(self.data_dir):
            if not filename.endswith('.json'):
                continue
            eval_id = filename[:-5]
            data = self.get(eval_id)
            if data:
                evals.append({
                    "id": data.get("id", eval_id),
                    "candidateId": data.get("candidate_id"),
                    "status": data.get("status"),
                    "score": data.get("score", {}).get("overall"),
                    "createdAt": data.get("created_at"),
                })
        evals.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return evals


_storage: Optional[CodeEvalStorage] = None

def get_storage() -> CodeEvalStorage:
    global _storage
    if _storage is None:
        _storage = CodeEvalStorage()
    return _storage
