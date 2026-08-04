"""
Artifact Storage Layer

Scientific Responsibility:
- Persist and retrieve Artifact metadata
- Enforce immutability (no updates/deletes)
- Maintain traceability via runId linkage
- Phase 1: Metadata only, no file content storage
"""

import json
import os
from pathlib import Path
from typing import List, Optional
from datetime import datetime

from app.models.artifact import Artifact


class ArtifactStorage:
    """
    File-based storage for Artifact metadata.
    
    Phase 1 guarantees:
    - Append-only: Artifacts cannot be modified or deleted
    - Metadata persistence: Artifact records survive server restarts
    - Traceability: Can query artifacts by runId
    """
    
    def __init__(self, storage_dir: str = "data/artifacts"):
        """
        Initialize storage with specified directory.
        
        Args:
            storage_dir: Directory for storing artifact metadata JSON files
        """
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_artifact_path(self, artifact_id: str) -> Path:
        """Get filesystem path for an artifact ID."""
        return self.storage_dir / f"{artifact_id}.json"
    
    def _serialize_artifact(self, artifact: Artifact) -> dict:
        """Serialize Artifact to JSON-compatible dict."""
        return artifact.model_dump(mode='json')
    
    def _deserialize_artifact(self, data: dict) -> Artifact:
        """Deserialize Artifact from JSON dict."""
        # Reconstruct datetime objects
        if 'createdAt' in data and isinstance(data['createdAt'], str):
            data['createdAt'] = datetime.fromisoformat(data['createdAt'].replace('Z', '+00:00'))
        
        return Artifact(**data)
    
    def create(self, artifact: Artifact) -> Artifact:
        """
        Persist a new Artifact.
        
        Args:
            artifact: Artifact to persist
            
        Returns:
            The persisted artifact
            
        Raises:
            ValueError: If artifact with same ID already exists
        """
        artifact_path = self._get_artifact_path(artifact.id)
        
        if artifact_path.exists():
            raise ValueError(
                f"Artifact with id '{artifact.id}' already exists. "
                "Artifacts are immutable and cannot be overwritten."
            )
        
        # Serialize to JSON
        artifact_dict = self._serialize_artifact(artifact)
        
        # Write atomically
        temp_path = artifact_path.with_suffix('.tmp')
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(artifact_dict, f, indent=2, default=str)
        
        temp_path.replace(artifact_path)
        
        return artifact
    
    def get(self, artifact_id: str) -> Optional[Artifact]:
        """
        Retrieve an Artifact by ID.
        
        Args:
            artifact_id: Unique artifact identifier
            
        Returns:
            Artifact if found, None otherwise
        """
        artifact_path = self._get_artifact_path(artifact_id)
        
        if not artifact_path.exists():
            return None
        
        with open(artifact_path, 'r', encoding='utf-8') as f:
            artifact_dict = json.load(f)
        
        return self._deserialize_artifact(artifact_dict)
    
    def list_all(self) -> List[Artifact]:
        """
        List all Artifacts.
        
        Returns:
            List of artifacts, sorted by creation time (newest first)
        """
        artifacts = []
        
        for artifact_file in self.storage_dir.glob("*.json"):
            with open(artifact_file, 'r', encoding='utf-8') as f:
                artifact_dict = json.load(f)
            
            artifacts.append(self._deserialize_artifact(artifact_dict))
        
        # Sort by creation time (newest first)
        artifacts.sort(key=lambda a: a.createdAt, reverse=True)
        
        return artifacts
    
    def list_by_run(self, runId: str) -> List[Artifact]:
        """
        List all Artifacts for a specific Run.
        
        Args:
            runId: Run identifier
            
        Returns:
            List of artifacts linked to this run, sorted by creation time
        """
        artifacts = []
        
        for artifact_file in self.storage_dir.glob("*.json"):
            with open(artifact_file, 'r', encoding='utf-8') as f:
                artifact_dict = json.load(f)
            
            if artifact_dict.get('runId') == runId:
                artifacts.append(self._deserialize_artifact(artifact_dict))
        
        artifacts.sort(key=lambda a: a.createdAt, reverse=True)
        
        return artifacts
    
    def exists(self, artifact_id: str) -> bool:
        """
        Check if an artifact exists.
        
        Args:
            artifact_id: Artifact identifier to check
            
        Returns:
            True if artifact exists, False otherwise
        """
        return self._get_artifact_path(artifact_id).exists()


# Global storage instance
_storage_instance: Optional[ArtifactStorage] = None


def get_storage() -> ArtifactStorage:
    """
    Get global storage instance (singleton pattern).
    
    Returns:
        ArtifactStorage instance
    """
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = ArtifactStorage()
    return _storage_instance
