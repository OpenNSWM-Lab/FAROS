"""
ResearchPlan Storage Layer

Scientific Responsibility:
- Persist and retrieve ResearchPlan objects
- Enforce immutability (no updates allowed)
- Provide audit trail through filesystem timestamps
- Abstract storage mechanism from domain logic

Phase 1: Simple file-based storage
Future: Database with full audit logging
"""

import json
import os
from pathlib import Path
from typing import List, Optional
from datetime import datetime

from app.models.research_plan import ResearchPlan


def _get_data_dir() -> str:
    """Return the backend-level data directory regardless of current cwd."""
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "data")


class ResearchPlanStorage:
    """
    File-based storage for ResearchPlans.
    
    Storage guarantees:
    - Immutability: Plans cannot be modified after creation
    - Persistence: Plans survive server restarts
    - Traceability: Filesystem timestamps provide audit trail
    """
    
    def __init__(self, storage_dir: str = "data/research_plans"):
        """
        Initialize storage with specified directory.
        
        Args:
            storage_dir: Directory for storing plan JSON files
        """
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_plan_path(self, plan_id: str) -> Path:
        """Get filesystem path for a plan ID."""
        return self.storage_dir / f"{plan_id}.json"
    
    def create(self, plan: ResearchPlan) -> ResearchPlan:
        """
        Persist a new ResearchPlan.
        
        Args:
            plan: ResearchPlan to persist
            
        Returns:
            The persisted plan
            
        Raises:
            ValueError: If plan with same ID already exists (immutability violation)
        """
        plan_path = self._get_plan_path(plan.id)
        
        if plan_path.exists():
            raise ValueError(
                f"ResearchPlan with id '{plan.id}' already exists. "
                "Plans are immutable and cannot be overwritten."
            )
        
        # Serialize to JSON
        plan_dict = plan.model_dump(mode='json')
        
        # Write atomically (write to temp, then rename)
        temp_path = plan_path.with_suffix('.tmp')
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(plan_dict, f, indent=2, default=str)
        
        temp_path.replace(plan_path)
        
        return plan
    
    def get(self, plan_id: str) -> Optional[ResearchPlan]:
        """
        Retrieve a ResearchPlan by ID.
        
        Args:
            plan_id: Unique plan identifier
            
        Returns:
            ResearchPlan if found, None otherwise
        """
        plan_path = self._get_plan_path(plan_id)
        
        if not plan_path.exists():
            return None
        
        with open(plan_path, 'r', encoding='utf-8') as f:
            plan_dict = json.load(f)
        
        # Reconstruct datetime objects
        if 'created_at' in plan_dict and isinstance(plan_dict['created_at'], str):
            plan_dict['created_at'] = datetime.fromisoformat(plan_dict['created_at'].replace('Z', '+00:00'))
        
        return ResearchPlan(**plan_dict)
    
    def list_all(self) -> List[ResearchPlan]:
        """
        List all ResearchPlans.
        
        Returns:
            List of all persisted plans, sorted by creation time (newest first)
        """
        plans = []
        
        for plan_file in self.storage_dir.glob("*.json"):
            with open(plan_file, 'r', encoding='utf-8') as f:
                plan_dict = json.load(f)
            
            # Reconstruct datetime
            if 'created_at' in plan_dict and isinstance(plan_dict['created_at'], str):
                plan_dict['created_at'] = datetime.fromisoformat(plan_dict['created_at'].replace('Z', '+00:00'))
            
            plans.append(ResearchPlan(**plan_dict))
        
        # Sort by creation time (newest first)
        plans.sort(key=lambda p: p.created_at, reverse=True)
        
        return plans
    
    def exists(self, plan_id: str) -> bool:
        """
        Check if a plan exists.
        
        Args:
            plan_id: Plan identifier to check
            
        Returns:
            True if plan exists, False otherwise
        """
        return self._get_plan_path(plan_id).exists()


# Global storage instance
_storage_instance: Optional[ResearchPlanStorage] = None


def get_storage() -> ResearchPlanStorage:
    """
    Get global storage instance (singleton pattern).
    
    Returns:
        ResearchPlanStorage instance
    """
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = ResearchPlanStorage(os.path.join(_get_data_dir(), "research_plans"))
    return _storage_instance
