"""
Repository Context Storage - Persists RepoContext objects to JSON files.

Follows the same pattern as other storage modules in AI-Researcher.
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

# Storage directory
DATA_DIR = str(get_data_dir() / "repo_contexts")


class RepoContextStorage:
    """
    File-based storage for RepoContext objects.
    
    Each context is stored as a JSON file: {id}.json
    """
    
    def __init__(self, data_dir: str = DATA_DIR):
        """Initialize storage with data directory."""
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)
    
    def _get_path(self, context_id: str) -> str:
        """Get file path for a context."""
        return os.path.join(self.data_dir, f"{context_id}.json")
    
    def save(self, context_id: str, data: Dict[str, Any]) -> None:
        """
        Save context data atomically.
        
        Args:
            context_id: Context identifier
            data: Context data dictionary
        """
        file_path = self._get_path(context_id)
        
        # Atomic write using temp file
        fd, temp_path = tempfile.mkstemp(suffix=".json", dir=self.data_dir)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, default=str)
            shutil.move(temp_path, file_path)
            logger.info(f"Saved repo context: {context_id}")
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise e
    
    def get(self, context_id: str) -> Optional[Dict[str, Any]]:
        """
        Get context by ID.
        
        Args:
            context_id: Context identifier
            
        Returns:
            Context data or None if not found
        """
        file_path = self._get_path(context_id)
        
        if not os.path.exists(file_path):
            return None
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load context {context_id}: {e}")
            return None
    
    def delete(self, context_id: str) -> bool:
        """
        Delete a context.
        
        Args:
            context_id: Context identifier
            
        Returns:
            True if deleted, False if not found
        """
        file_path = self._get_path(context_id)
        
        if not os.path.exists(file_path):
            return False
        
        try:
            os.remove(file_path)
            logger.info(f"Deleted repo context: {context_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete context {context_id}: {e}")
            return False
    
    def list_all(self) -> List[Dict[str, Any]]:
        """
        List all contexts (metadata only, not full chunks).
        
        Returns:
            List of context metadata dictionaries
        """
        contexts = []
        
        for filename in os.listdir(self.data_dir):
            if not filename.endswith('.json'):
                continue
            
            context_id = filename[:-5]  # Remove .json
            data = self.get(context_id)
            
            if data:
                # Return metadata only (exclude chunks for listing)
                metadata = {
                    "id": data.get("id", context_id),
                    "repo_path": data.get("repo_path"),
                    "file_count": data.get("file_count", 0),
                    "chunk_count": data.get("chunk_count", 0),
                    "languages": data.get("languages", {}),
                    "created_at": data.get("created_at"),
                }
                contexts.append(metadata)
        
        # Sort by creation time descending
        contexts.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        
        return contexts
    
    def exists(self, context_id: str) -> bool:
        """Check if context exists."""
        return os.path.exists(self._get_path(context_id))


# Global storage instance
_storage: Optional[RepoContextStorage] = None


def get_storage() -> RepoContextStorage:
    """Get global storage instance."""
    global _storage
    if _storage is None:
        _storage = RepoContextStorage()
    return _storage
