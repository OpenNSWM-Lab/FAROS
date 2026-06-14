"""PlanPackage API endpoints."""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.models.plan_package import PlanPackage, PlanQualityGate
from app.services.blueprint_converter import convert_plan_package_to_blueprint
from app.services.plan_package_service import (
    PlanPackageConflictError,
    PlanPackageNotFoundError,
    get_plan_package_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["plan_packages"])


class CreatePlanPackageRequest(BaseModel):
    candidateId: Optional[str] = None
    planSessionId: Optional[str] = None
    maxStages: int = Field(default=4, ge=1, le=8)
    maxStepsPerStage: int = Field(default=5, ge=1, le=10)
    userNotes: Optional[str] = None
    generationMode: str = Field(default="hybrid", description="hybrid | deterministic")
    useLLM: Optional[bool] = Field(default=None, description="Deprecated compatibility flag; overrides generationMode when set")
    maxRepairRounds: int = Field(default=0, ge=0, le=2)


class CreatePlanPackageResponse(BaseModel):
    packageId: str
    schemaVersion: str
    qualityGate: PlanQualityGate
    package: PlanPackage


class ValidatePlanPackageResponse(BaseModel):
    packageId: str
    qualityGate: PlanQualityGate


class PlanPackageResearchPlanResponse(BaseModel):
    packageId: str
    researchPlanId: str
    researchPlan: dict


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
            plan_session_id=request.planSessionId,
            max_stages=request.maxStages,
            max_steps_per_stage=request.maxStepsPerStage,
            user_notes=request.userNotes,
            use_llm=request.useLLM,
            generation_mode=request.generationMode,
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
    "/plans/sessions/{plan_session_id}/package",
    response_model=PlanPackage,
    summary="Get PlanPackage by Plan Session",
)
async def get_plan_package_by_plan_session(plan_session_id: str) -> PlanPackage:
    service = get_plan_package_service()
    package = service.get_by_plan_session(plan_session_id)
    if not package:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"PlanPackage for plan session {plan_session_id} not found",
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


@router.get(
    "/plans/packages/{package_id}/blueprint",
    summary="Get PlanPackage as experiment blueprint DAG",
)
async def get_plan_package_blueprint(package_id: str):
    service = get_plan_package_service()
    package = service.get(package_id)
    if not package:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"PlanPackage {package_id} not found",
        )
    return convert_plan_package_to_blueprint(package)


@router.post(
    "/plans/packages/{package_id}/to-research-plan",
    response_model=PlanPackageResearchPlanResponse,
    summary="Convert PlanPackage to legacy ResearchPlan",
)
async def convert_plan_package_to_research_plan(package_id: str) -> PlanPackageResearchPlanResponse:
    service = get_plan_package_service()
    try:
        plan = service.to_research_plan(package_id)
        return PlanPackageResearchPlanResponse(
            packageId=package_id,
            researchPlanId=plan.id,
            researchPlan=plan.model_dump(mode="json"),
        )
    except PlanPackageNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
