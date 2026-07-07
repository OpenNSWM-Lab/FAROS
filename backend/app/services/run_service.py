"""
Run Service Layer

Scientific Responsibility:
- Orchestrate Run lifecycle
- Enforce state machine transitions
- Coordinate between API and storage layers
- Generate unique identifiers
- Validate PlanPackage linkage
"""

import uuid
from typing import List, Optional
from datetime import datetime

from app.models.run import Run, RunStatus
from app.schemas.run import RunCreate, RunUpdate
from app.schemas.execution_summary import (
    ExecutionSummary,
    StatusStripBlock,
    LifecycleBlock,
    ComputePolicyBlock
)
from app.storage.run_storage import get_storage as get_run_storage
from app.storage.plan_package_storage import get_plan_package_storage


class RunService:
    """
    Service for Run operations.
    
    Enforces:
    - Unique ID generation
    - State machine validation
    - PlanPackage linkage verification
    - Append-only semantics for terminal runs
    """
    
    def __init__(self):
        self.storage = get_run_storage()
        self.plan_storage = get_plan_package_storage()
    
    def create_run(self, run_data: RunCreate) -> Run:
        """
        Create a new Run.
        
        Args:
            run_data: Validated run creation data
            
        Returns:
            Created Run in PENDING state
            
        Raises:
            ValueError: If plan_id is invalid or run creation violates business rules
        """
        # Validate planId if provided
        if run_data.planId:
            plan = self.plan_storage.get(run_data.planId)
            if plan is None:
                raise ValueError(f"PlanPackage '{run_data.planId}' not found")
        
        # Generate unique ID
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        
        # Construct domain model (starts in PENDING state)
        run = Run(
            id=run_id,
            planId=run_data.planId,
            type=run_data.type,
            status=RunStatus.PENDING,
            config=run_data.config,
            createdAt=datetime.utcnow(),
            isMock=run_data.isMock if hasattr(run_data, 'isMock') else False
        )
        
        # Persist
        return self.storage.create(run)
    
    def get_run(self, run_id: str) -> Optional[Run]:
        """
        Retrieve a Run by ID.
        
        Args:
            run_id: Run identifier
            
        Returns:
            Run if found, None otherwise
        """
        return self.storage.get(run_id)
    
    def update_run(self, run_id: str, update_data: RunUpdate) -> Run:
        """
        Update a Run's status and metadata.
        
        Args:
            run_id: Run identifier
            update_data: Fields to update
            
        Returns:
            Updated Run
            
        Raises:
            ValueError: If run not found, invalid state transition, or terminal run modification
        """
        existing = self.storage.get(run_id)
        
        if existing is None:
            raise ValueError(f"Run '{run_id}' not found")
        
        # Prevent modification of terminal runs
        if existing.is_terminal():
            raise ValueError(
                f"Cannot update run '{run_id}' - it is in terminal state '{existing.status}'. "
                "Terminal runs are immutable."
            )
        
        # Build updated run (only modify provided fields)
        update_dict = existing.model_dump()
        
        if update_data.status is not None:
            # Validate state transition
            if not existing.can_transition_to(update_data.status):
                raise ValueError(
                    f"Invalid state transition: {existing.status} → {update_data.status}"
                )
            update_dict['status'] = update_data.status
        
        if update_data.startedAt is not None:
            update_dict['startedAt'] = update_data.startedAt
        
        if update_data.endedAt is not None:
            update_dict['endedAt'] = update_data.endedAt
        
        if update_data.errorMessage is not None:
            update_dict['errorMessage'] = update_data.errorMessage
        
        if update_data.trace is not None:
            update_dict['trace'] = update_data.trace
        
        if update_data.artifactIds is not None:
            update_dict['artifactIds'] = update_data.artifactIds
        
        # Reconstruct and persist
        updated_run = Run(**update_dict)
        return self.storage.update(updated_run)
    
    def list_runs(self, status: Optional[RunStatus] = None, planId: Optional[str] = None) -> List[Run]:
        """
        List Runs with optional filters.
        
        Args:
            status: Optional status filter
            planId: Optional planId filter
            
        Returns:
            List of runs matching filters, sorted by creation time (newest first)
        """
        if planId:
            return self.storage.list_by_plan(planId)
        else:
            return self.storage.list_all(status=status)
    
    def start_run(self, run_id: str) -> Run:
        """
        Transition run from PENDING to RUNNING.
        
        Args:
            run_id: Run identifier
            
        Returns:
            Updated run in RUNNING state
        """
        return self.update_run(
            run_id,
            RunUpdate(
                status=RunStatus.RUNNING,
                startedAt=datetime.utcnow()
            )
        )
    
    def complete_run(self, run_id: str, artifactIds: Optional[List[str]] = None) -> Run:
        """
        Transition run from RUNNING to COMPLETED.
        
        Args:
            run_id: Run identifier
            artifactIds: Optional list of generated artifact IDs
            
        Returns:
            Updated run in COMPLETED state
        """
        update = RunUpdate(
            status=RunStatus.COMPLETED,
            endedAt=datetime.utcnow()
        )
        
        if artifactIds:
            update.artifactIds = artifactIds
        
        return self.update_run(run_id, update)
    
    def fail_run(self, run_id: str, errorMessage: str) -> Run:
        """
        Transition run from RUNNING to FAILED.
        
        Args:
            run_id: Run identifier
            errorMessage: Error description
            
        Returns:
            Updated run in FAILED state
        """
        return self.update_run(
            run_id,
            RunUpdate(
                status=RunStatus.FAILED,
                endedAt=datetime.utcnow(),
                errorMessage=errorMessage
            )
        )
    
    def cancel_run(self, run_id: str) -> Run:
        """
        Transition run from RUNNING to CANCELLED.
        
        Args:
            run_id: Run identifier
            
        Returns:
            Updated run in CANCELLED state
        """
        return self.update_run(
            run_id,
            RunUpdate(
                status=RunStatus.CANCELLED,
                endedAt=datetime.utcnow()
            )
        )
    
    def get_execution_summary(self, run: Run) -> ExecutionSummary:
        """
        Generate semantic dashboard blocks for RunDetail UI.
        
        Backend owns interpretation - frontend just renders blocks.
        
        Args:
            run: Run to summarize
            
        Returns:
            ExecutionSummary with semantic blocks
        """
        # Status Strip Block
        status_strip = StatusStripBlock(
            status=run.status if isinstance(run.status, str) else run.status.value,
            type=run.type if isinstance(run.type, str) else run.type.value,
            started=run.startedAt.isoformat() if run.startedAt else run.createdAt.isoformat(),
            duration=run.duration
        )
        
        # Lifecycle Block
        lifecycle = LifecycleBlock(
            started=run.startedAt.isoformat() if run.startedAt else run.createdAt.isoformat(),
            ended=run.endedAt.isoformat() if run.endedAt else None,
            duration=run.duration
        )
        
        # Compute & Policy Block
        steps_str = None
        if run.trace:
            steps_str = f"{run.trace.successful_steps}/{run.trace.total_steps}"
        
        compute_policy = ComputePolicyBlock(
            model=run.config.model,
            task_level=run.config.taskLevel,
            max_iterations=run.config.maxIterTimes,
            steps=steps_str
        )
        
        return ExecutionSummary(
            status_strip=status_strip,
            lifecycle=lifecycle,
            compute_policy=compute_policy
        )


# Global service instance
_service_instance: Optional[RunService] = None


def get_service() -> RunService:
    """
    Get global service instance (singleton pattern).
    
    Returns:
        RunService instance
    """
    global _service_instance
    if _service_instance is None:
        _service_instance = RunService()
    return _service_instance
