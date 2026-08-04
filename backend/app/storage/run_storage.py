"""
Run Storage Layer

Scientific Responsibility:
- Persist and retrieve Run objects
- Enforce append-only semantics (no updates to terminal runs)
- Validate state transitions
- Provide audit trail through filesystem timestamps
"""

import json
import os
from pathlib import Path
from typing import List, Optional
from datetime import datetime

from app.models.run import Run, RunStatus


class RunStorage:
    """
    File-based storage for Runs.
    
    Storage guarantees:
    - Append-only: Terminal runs (completed/failed/cancelled) cannot be modified
    - State validation: Only valid transitions allowed
    - Persistence: Runs survive server restarts
    - Traceability: Filesystem timestamps provide audit trail
    """
    
    def __init__(self, storage_dir: str = "data/runs"):
        """
        Initialize storage with specified directory.
        
        Args:
            storage_dir: Directory for storing run JSON files
        """
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_run_path(self, run_id: str) -> Path:
        """Get filesystem path for a run ID."""
        return self.storage_dir / f"{run_id}.json"
    
    def _serialize_run(self, run: Run) -> dict:
        """Serialize Run to JSON-compatible dict."""
        data = run.model_dump(mode='json')
        # Add computed duration
        data['duration'] = run.duration
        return data
    
    def _deserialize_run(self, data: dict) -> Run:
        """Deserialize Run from JSON dict."""
        # Remove computed field before reconstruction
        data.pop('duration', None)
        
        # Reconstruct datetime objects
        for field in ['createdAt', 'startedAt', 'endedAt']:
            if field in data and data[field] and isinstance(data[field], str):
                data[field] = datetime.fromisoformat(data[field].replace('Z', '+00:00'))
        
        return Run(**data)
    
    def create(self, run: Run) -> Run:
        """
        Persist a new Run.
        
        Args:
            run: Run to persist
            
        Returns:
            The persisted run
            
        Raises:
            ValueError: If run with same ID already exists
        """
        run_path = self._get_run_path(run.id)
        
        if run_path.exists():
            raise ValueError(
                f"Run with id '{run.id}' already exists. "
                "Use update() to modify existing runs."
            )
        
        # Serialize to JSON
        run_dict = self._serialize_run(run)
        
        # Write atomically
        temp_path = run_path.with_suffix('.tmp')
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(run_dict, f, indent=2, default=str)
        
        temp_path.replace(run_path)
        
        return run
    
    def get(self, run_id: str) -> Optional[Run]:
        """
        Retrieve a Run by ID.
        
        Args:
            run_id: Unique run identifier
            
        Returns:
            Run if found, None otherwise
        """
        run_path = self._get_run_path(run_id)
        
        if not run_path.exists():
            return None
        
        with open(run_path, 'r', encoding='utf-8') as f:
            run_dict = json.load(f)
        
        return self._deserialize_run(run_dict)
    
    def update(self, run: Run) -> Run:
        """
        Update an existing Run.
        
        Args:
            run: Run with updated fields
            
        Returns:
            Updated run
            
        Raises:
            ValueError: If run doesn't exist or is in terminal state
        """
        existing = self.get(run.id)
        
        if existing is None:
            raise ValueError(f"Run with id '{run.id}' not found")
        
        # Enforce append-only for terminal states
        if existing.is_terminal():
            raise ValueError(
                f"Cannot update run '{run.id}' - it is in terminal state '{existing.status}'. "
                "Terminal runs are immutable for scientific integrity."
            )
        
        # Validate state transition
        if run.status != existing.status:
            if not existing.can_transition_to(run.status):
                raise ValueError(
                    f"Invalid state transition: {existing.status} → {run.status}"
                )
        
        # Write updated run
        run_path = self._get_run_path(run.id)
        run_dict = self._serialize_run(run)
        
        temp_path = run_path.with_suffix('.tmp')
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(run_dict, f, indent=2, default=str)
        
        temp_path.replace(run_path)
        
        return run
    
    def list_all(self, status: Optional[RunStatus] = None) -> List[Run]:
        """
        List all Runs, optionally filtered by status.
        
        Args:
            status: Optional status filter
            
        Returns:
            List of runs, sorted by creation time (newest first)
        """
        runs = []
        
        for run_file in self.storage_dir.glob("*.json"):
            with open(run_file, 'r', encoding='utf-8') as f:
                run_dict = json.load(f)
            
            run = self._deserialize_run(run_dict)
            
            # Apply status filter
            if status is None or run.status == status:
                runs.append(run)
        
        # Sort by creation time (newest first)
        runs.sort(key=lambda r: r.createdAt, reverse=True)
        
        return runs
    
    def list_by_plan(self, plan_id: str) -> List[Run]:
        """
        List all Runs for a specific PlanPackage.
        
        Args:
            plan_id: PlanPackage identifier
            
        Returns:
            List of runs linked to this plan, sorted by creation time
        """
        runs = []
        
        for run_file in self.storage_dir.glob("*.json"):
            with open(run_file, 'r', encoding='utf-8') as f:
                run_dict = json.load(f)
            
            if run_dict.get('planId') == plan_id:
                runs.append(self._deserialize_run(run_dict))
        
        runs.sort(key=lambda r: r.createdAt, reverse=True)
        
        return runs
    
    def exists(self, run_id: str) -> bool:
        """
        Check if a run exists.
        
        Args:
            run_id: Run identifier to check
            
        Returns:
            True if run exists, False otherwise
        """
        return self._get_run_path(run_id).exists()


# Global storage instance
_storage_instance: Optional[RunStorage] = None


def get_storage() -> RunStorage:
    """
    Get global storage instance (singleton pattern).
    
    Returns:
        RunStorage instance
    """
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = RunStorage()
    return _storage_instance
