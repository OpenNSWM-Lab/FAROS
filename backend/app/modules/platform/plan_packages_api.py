"""PlanPackage API endpoints."""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.models.plan_package import PlanPackage, PlanQualityGate
from app.services.plan_package_service import (
    PlanPackageConflictError,
    PlanPackageNotFoundError,
    get_plan_package_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["plan_packages"])


class CreatePlanPackageRequest(BaseModel):
    candidateId: Optional[str] = None
    maxStages: int = Field(default=3, ge=1, le=5)
    maxStepsPerStage: int = Field(default=3, ge=1, le=5)
    userNotes: Optional[str] = None
    generationMode: str = Field(default="hybrid", description="hybrid | deterministic")
    reviewerMode: str = Field(default="hybrid", description="deterministic | hybrid")
    maxRepairRounds: int = Field(default=1, ge=0, le=2)


class CreatePlanPackageResponse(BaseModel):
    packageId: str
    schemaVersion: str
    qualityGate: PlanQualityGate
    package: PlanPackage


class ValidatePlanPackageResponse(BaseModel):
    packageId: str
    qualityGate: PlanQualityGate


class PlanPackageFeedbackRequest(BaseModel):
    sectionPath: str = Field(default="package")
    feedbackType: str = Field(default="comment", description="comment | correction | reject | regenerate | approve")
    comment: str
    severity: str = Field(default="medium", description="low | medium | high | blocking")
    requestedAction: str = Field(default="revise")


class RevisePlanPackageRequest(BaseModel):
    generationMode: str = Field(default="hybrid", description="hybrid | deterministic")
    reviewerMode: str = Field(default="hybrid", description="deterministic | hybrid")
    maxStages: Optional[int] = Field(default=None, ge=1, le=5)
    maxStepsPerStage: Optional[int] = Field(default=None, ge=1, le=5)
    maxRepairRounds: int = Field(default=1, ge=0, le=3)
    targetSections: Optional[list[str]] = Field(
        default=None,
        description="Writable sections to revise: researchQuestion, hypothesis, constants, stages, expectedMetrics",
    )


class ReviewPlanPackageRequest(BaseModel):
    reviewerMode: str = Field(default="hybrid", description="deterministic | hybrid")


class ApprovePlanPackageRequest(BaseModel):
    reviewerMode: Optional[str] = Field(default=None, description="deterministic | hybrid")


@router.post(
    "/plans/packages/from-idea-session/{idea_session_id}",
    response_model=CreatePlanPackageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create PlanPackage from Idea Session",
)
async def create_plan_package_from_idea_session(
    idea_session_id: str,
    request: CreatePlanPackageRequest,
) -> CreatePlanPackageResponse:
    service = get_plan_package_service()
    try:
        package = service.create_from_idea_session(
            idea_session_id,
            candidate_id=request.candidateId,
            max_stages=request.maxStages,
            max_steps_per_stage=request.maxStepsPerStage,
            user_notes=request.userNotes,
            generation_mode=request.generationMode,
            reviewer_mode=request.reviewerMode,
            max_repair_rounds=request.maxRepairRounds,
        )
        return CreatePlanPackageResponse(
            packageId=package.packageId,
            schemaVersion=package.schemaVersion,
            qualityGate=package.qualityGate,
            package=package,
        )
    except PlanPackageNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except PlanPackageConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception as exc:
        logger.error("PlanPackage creation failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get(
    "/plans/packages/{package_id}",
    response_model=PlanPackage,
    summary="Get PlanPackage",
)
async def get_plan_package(package_id: str) -> PlanPackage:
    service = get_plan_package_service()
    package = service.get(package_id)
    if not package:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"PlanPackage {package_id} not found",
        )
    return package


@router.get(
    "/ideas/sessions/{idea_session_id}/plan-package",
    response_model=PlanPackage,
    summary="Get PlanPackage by Idea Session",
)
async def get_plan_package_by_idea_session(idea_session_id: str) -> PlanPackage:
    service = get_plan_package_service()
    package = service.get_by_idea_session(idea_session_id)
    if not package:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"PlanPackage for idea session {idea_session_id} not found",
        )
    return package


@router.post(
    "/plans/packages/{package_id}/validate",
    response_model=ValidatePlanPackageResponse,
    summary="Validate PlanPackage",
)
async def validate_plan_package(package_id: str) -> ValidatePlanPackageResponse:
    service = get_plan_package_service()
    try:
        package = service.validate(package_id)
        return ValidatePlanPackageResponse(
            packageId=package.packageId,
            qualityGate=package.qualityGate,
        )
    except PlanPackageNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post(
    "/plans/packages/{package_id}/feedback",
    response_model=PlanPackage,
    summary="Add human feedback to PlanPackage",
)
async def add_plan_package_feedback(
    package_id: str,
    request: PlanPackageFeedbackRequest,
) -> PlanPackage:
    service = get_plan_package_service()
    try:
        return service.add_feedback(
            package_id,
            section_path=request.sectionPath,
            feedback_type=request.feedbackType,
            comment=request.comment,
            severity=request.severity,
            requested_action=request.requestedAction,
        )
    except PlanPackageNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.post(
    "/plans/packages/{package_id}/review",
    response_model=PlanPackage,
    summary="Run PlanPackage reviewer committee",
)
async def review_plan_package(
    package_id: str,
    request: Optional[ReviewPlanPackageRequest] = None,
) -> PlanPackage:
    service = get_plan_package_service()
    try:
        reviewer_mode = request.reviewerMode if request else "hybrid"
        return service.run_review(package_id, reviewer_mode=reviewer_mode)
    except PlanPackageNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post(
    "/plans/packages/{package_id}/revise",
    response_model=PlanPackage,
    summary="Revise PlanPackage from human feedback and reviewer findings",
)
async def revise_plan_package(
    package_id: str,
    request: RevisePlanPackageRequest,
) -> PlanPackage:
    service = get_plan_package_service()
    try:
        return service.revise(
            package_id,
            generation_mode=request.generationMode,
            max_stages=request.maxStages,
            max_steps_per_stage=request.maxStepsPerStage,
            max_repair_rounds=request.maxRepairRounds,
            target_sections=request.targetSections,
            reviewer_mode=request.reviewerMode,
        )
    except PlanPackageNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except PlanPackageConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception as exc:
        logger.error("PlanPackage revision failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post(
    "/plans/packages/{package_id}/approve",
    response_model=PlanPackage,
    summary="Approve PlanPackage for downstream handoff",
)
async def approve_plan_package(
    package_id: str,
    request: Optional[ApprovePlanPackageRequest] = None,
) -> PlanPackage:
    service = get_plan_package_service()
    try:
        return service.approve(package_id, reviewer_mode=request.reviewerMode if request else None)
    except PlanPackageNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except PlanPackageConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
