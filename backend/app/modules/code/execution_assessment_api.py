"""HTTP API for deterministic scientific execution assessment."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.contracts import ExecutionAssessment
from app.modules.code.execution_assessment import (
    assess_candidate_execution,
    validate_with_public_contract,
)


router = APIRouter(prefix="/code/execution-assessments", tags=["code-execution-assessment"])


class ExecutionAssessmentRequest(BaseModel):
    runId: str = Field(min_length=1)
    questionId: str = Field(min_length=1)
    researchQuestion: str = Field(min_length=5)
    planPackageId: str | None = None
    candidate: dict[str, Any] = Field(default_factory=dict)
    inputs: dict[str, Any] = Field(default_factory=dict)


class BatchExecutionAssessmentRequest(BaseModel):
    items: list[ExecutionAssessmentRequest] = Field(min_length=1, max_length=200)


@router.post("", response_model=ExecutionAssessment)
async def create_execution_assessment(req: ExecutionAssessmentRequest) -> ExecutionAssessment:
    return validate_with_public_contract(assess_candidate_execution(
        run_id=req.runId,
        question_id=req.questionId,
        research_question=req.researchQuestion,
        candidate=req.candidate,
        inputs=req.inputs,
        plan_package_id=req.planPackageId,
    ))


@router.post("/batch", response_model=list[ExecutionAssessment])
async def create_execution_assessments(req: BatchExecutionAssessmentRequest) -> list[ExecutionAssessment]:
    return [
        validate_with_public_contract(assess_candidate_execution(
            run_id=item.runId,
            question_id=item.questionId,
            research_question=item.researchQuestion,
            candidate=item.candidate,
            inputs=item.inputs,
            plan_package_id=item.planPackageId,
        ))
        for item in req.items
    ]
