"""Platform-owned artifacts API implementation."""

import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse

from app.modules.platform.contracts import ArtifactCreate, ArtifactResponse
from app.schemas.artifact import ArtifactListResponse
from app.services.artifact_service import get_service

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.post("", response_model=ArtifactResponse, status_code=status.HTTP_201_CREATED)
async def create_artifact(artifact_data: ArtifactCreate) -> ArtifactResponse:
    service = get_service()
    try:
        artifact = service.create_artifact(artifact_data)
        return ArtifactResponse.model_validate(artifact)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create artifact: {str(exc)}",
        )


@router.get("/{artifact_id}", response_model=ArtifactResponse)
async def get_artifact(artifact_id: str) -> ArtifactResponse:
    service = get_service()
    artifact = service.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact with id '{artifact_id}' not found",
        )
    return ArtifactResponse.model_validate(artifact)


@router.get("", response_model=ArtifactListResponse)
async def list_artifacts(
    runId: Optional[str] = Query(None, description="Filter by runId"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of artifacts to return"),
    offset: int = Query(0, ge=0, description="Number of artifacts to skip"),
) -> ArtifactListResponse:
    service = get_service()
    artifacts = service.list_artifacts(runId=runId, limit=limit, offset=offset)
    return ArtifactListResponse(
        artifacts=[ArtifactResponse.model_validate(a) for a in artifacts],
        total=len(artifacts),
    )


@router.get("/{artifact_id}/download")
async def download_artifact(artifact_id: str):
    service = get_service()
    artifact = service.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact with id '{artifact_id}' not found",
        )

    storage_path = artifact.storagePath
    if not storage_path or not os.path.isfile(storage_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact file not found on disk: {artifact.filename}",
        )

    return FileResponse(
        storage_path,
        filename=artifact.filename,
        media_type="application/octet-stream",
    )
