"""
Artifact Service Layer

Scientific Responsibility:
- Orchestrate Artifact operations
- Generate unique identifiers
- Validate Run linkage
- Enforce immutability guarantees
"""

import uuid
from typing import List, Optional

from app.modules.platform.contracts import Artifact
from app.modules.platform.contracts import ArtifactCreate
from app.modules.platform.storage import get_artifact_storage
from app.modules.platform.storage import get_run_storage


class ArtifactService:
    """
    Service for Artifact operations.
    
    Enforces:
    - Unique ID generation
    - Run linkage verification
    - Immutability (no updates/deletes)
    """
    
    def __init__(self):
        self.storage = get_artifact_storage()
        self.run_storage = get_run_storage()
    
    def create_artifact(self, artifact_data: ArtifactCreate) -> Artifact:
        """
        Register a new Artifact.
        
        Args:
            artifact_data: Validated artifact creation data
            
        Returns:
            Created Artifact
            
        Raises:
            ValueError: If runId is invalid or artifact creation violates business rules
        """
        # Validate runId
        run = self.run_storage.get(artifact_data.runId)
        if run is None:
            raise ValueError(f"Run '{artifact_data.runId}' not found")
        
        # Generate unique ID
        artifact_id = f"artifact_{uuid.uuid4().hex[:12]}"
        
        # Construct domain model
        artifact = Artifact(
            id=artifact_id,
            runId=artifact_data.runId,
            type=artifact_data.type,
            filename=artifact_data.filename,
            size=artifact_data.size,
            storagePath=artifact_data.storagePath,
            checksum=artifact_data.checksum
        )
        
        # Persist
        return self.storage.create(artifact)
    
    def get_artifact(self, artifact_id: str) -> Optional[Artifact]:
        """
        Retrieve an Artifact by ID.
        
        Args:
            artifact_id: Artifact identifier
            
        Returns:
            Artifact if found, None otherwise
        """
        return self.storage.get(artifact_id)
    
    def list_artifacts(self, runId: Optional[str] = None, limit: int = 100, offset: int = 0) -> List[Artifact]:
        """
        List Artifacts with optional filters and pagination.
        
        Args:
            runId: Optional runId filter
            limit: Maximum number of artifacts to return (default 100)
            offset: Number of artifacts to skip (default 0)
            
        Returns:
            List of artifacts matching filters, sorted by creation time (newest first)
        """
        if runId:
            return self.storage.list_by_run(runId, limit=limit, offset=offset)
        else:
            return self.storage.list_all(limit=limit, offset=offset)


# Global service instance
_service_instance: Optional[ArtifactService] = None


def get_service() -> ArtifactService:
    """
    Get global service instance (singleton pattern).
    
    Returns:
        ArtifactService instance
    """
    global _service_instance
    if _service_instance is None:
        _service_instance = ArtifactService()
    return _service_instance
