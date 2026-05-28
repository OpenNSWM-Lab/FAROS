"""
ResearchPlan API Schemas

Scientific Responsibility:
- Define IO contracts for ResearchPlan creation and retrieval
- Enforce validation rules at API boundary
- Separate external representation from internal domain model
"""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field

from app.models.research_plan import (
    ResearchApproach,
    Variables,
    Methodology,
    ExpectedOutcomes
)


class ResearchPlanCreate(BaseModel):
    """
    Schema for creating a new ResearchPlan.
    
    Used by POST /api/v1/research/plans
    """
    research_question: str = Field(
        ...,
        description="Clear research question",
        min_length=10,
        examples=["Does DPO improve helpfulness over SFT?"]
    )
    hypothesis: str = Field(
        ...,
        description="Testable hypothesis",
        min_length=10,
        examples=["DPO will increase helpfulness by >10%"]
    )
    variables: Variables
    methodology: Methodology
    expected_outcomes: ExpectedOutcomes
    tags: List[str] = Field(default_factory=list)
    notes: str = Field(default="")


class ResearchPlanResponse(BaseModel):
    """
    Schema for ResearchPlan responses.
    
    Used by GET /api/v1/research/plans/{id}
    """
    id: str
    created_at: datetime
    research_question: str
    hypothesis: str
    variables: Variables
    methodology: Methodology
    expected_outcomes: ExpectedOutcomes
    tags: List[str]
    notes: str
    
    # Idea Traceability (Phase 1.5)
    source_session_id: Optional[str] = None
    source_candidate_id: Optional[str] = None
    source_candidate_index: Optional[int] = None
    source_title: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)


class ResearchPlanListResponse(BaseModel):
    """
    Schema for listing multiple ResearchPlans.
    """
    plans: List[ResearchPlanResponse]
    total: int
