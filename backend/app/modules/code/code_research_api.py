"""Competition-facing Code APIs for assessment, evidence, feedback, and import."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.db.engine import _DATA_DIR
from app.modules.code.code_bundle_import import (
    BundleImportError,
    import_bundle,
    resolve_bundle_source,
)
from app.modules.code.execution_assessment import (
    ExecutionAssessment,
    ExecutionGateDecision,
    assess_execution,
    execution_gate,
    validate_with_public_contract as validate_assessment_contract,
)
from app.modules.code.experiment_evidence_service import (
    ExperimentEvidence,
    ExperimentFeedback,
    build_experiment_evidence,
    build_experiment_feedback,
    save_evidence,
    validate_with_public_contract as validate_evidence_contract,
)
from app.modules.code.storage import get_session


router = APIRouter(prefix="/code/research", tags=["code_research"])
_CART_ID_RE = re.compile(r"^cart_[A-Za-z0-9_-]+$")
_MAX_UPLOAD_BYTES = 512 * 1024 * 1024
_UPLOAD_CHUNK_BYTES = 1024 * 1024


class AssessmentRequest(BaseModel):
    source: Dict[str, Any]
    runId: Optional[str] = None
    questionId: Optional[str] = None
    baseDir: Optional[str] = None


class GateRequest(BaseModel):
    assessment: ExecutionAssessment


class EvidenceRequest(BaseModel):
    assessment: ExecutionAssessment
    supportedClaims: List[str] = Field(default_factory=list)
    unsupportedClaims: List[str] = Field(default_factory=list)
    persist: bool = True


class FeedbackRequest(BaseModel):
    evidence: ExperimentEvidence
    planSource: Dict[str, Any] = Field(default_factory=dict)
    persistToCart: bool = True


class ImportBundleRequest(BaseModel):
    bundlePath: str = Field(
        min_length=1,
        description="Server-local ZIP/directory under backend/data/code_imports or sample_exports",
    )
    title: Optional[str] = None


class ImportBundleResponse(BaseModel):
    projectId: str
    packageId: str
    cartId: str
    fileCount: int
    totalSizeBytes: int
    projectUrl: str
    blueprintUrl: str
    warnings: List[str]


def _import_response(result: Any) -> ImportBundleResponse:
    return ImportBundleResponse(
        projectId=result.project_id,
        packageId=result.package_id,
        cartId=result.cart_id,
        fileCount=result.file_count,
        totalSizeBytes=result.total_size_bytes,
        projectUrl=f"/code/projects/{result.project_id}",
        blueprintUrl=f"/code/blueprint?projectId={result.project_id}",
        warnings=list(result.warnings),
    )


def _cart_path(cart_id: str) -> Path:
    if not _CART_ID_RE.fullmatch(cart_id):
        raise HTTPException(status_code=422, detail="Invalid cart ID")
    cart_base = (Path(_DATA_DIR) / "cart_artifacts").resolve()
    candidate = (cart_base / cart_id).resolve()
    try:
        candidate.relative_to(cart_base)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid cart path") from exc
    return candidate


@router.post("/assess", response_model=ExecutionAssessment)
async def create_execution_assessment(request: AssessmentRequest) -> ExecutionAssessment:
    try:
        assessment = assess_execution(
            request.source,
            run_id=request.runId,
            question_id=request.questionId,
            base_dir=request.baseDir,
        )
        validate_assessment_contract(assessment)
        return assessment
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/gate", response_model=ExecutionGateDecision)
async def check_execution_gate(request: GateRequest) -> ExecutionGateDecision:
    return execution_gate(request.assessment)


@router.post("/carts/{cart_id}/evidence", response_model=ExperimentEvidence)
async def create_experiment_evidence(cart_id: str, request: EvidenceRequest) -> ExperimentEvidence:
    cart_path = _cart_path(cart_id)
    evidence = build_experiment_evidence(
        cart_path,
        request.assessment,
        code_run_id=cart_id,
        supported_claims=request.supportedClaims,
        unsupported_claims=request.unsupportedClaims,
    )
    validate_evidence_contract(evidence)
    if request.persist and cart_path.is_dir():
        save_evidence(cart_path / "data" / "experiment_evidence.json", evidence)
    return evidence


@router.post("/carts/{cart_id}/feedback", response_model=ExperimentFeedback)
async def create_experiment_feedback(cart_id: str, request: FeedbackRequest) -> ExperimentFeedback:
    cart_path = _cart_path(cart_id)
    feedback = build_experiment_feedback(request.evidence, request.planSource)
    if request.persistToCart and cart_path.is_dir():
        target = cart_path / "data" / "experiment_feedback.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(feedback.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)
    return feedback


@router.post("/imports", response_model=ImportBundleResponse, status_code=201)
async def import_finished_bundle(
    request: ImportBundleRequest,
    db: Session = Depends(get_session),
) -> ImportBundleResponse:
    try:
        source = resolve_bundle_source(request.bundlePath)
        result = import_bundle(source, db, title=request.title)
    except BundleImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return _import_response(result)


@router.post("/imports/upload", response_model=ImportBundleResponse, status_code=201)
async def upload_finished_bundle(
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
) -> ImportBundleResponse:
    """Upload, validate, register, and then discard a portable Code bundle ZIP."""
    filename = Path(file.filename or "bundle.zip").name
    if not filename.lower().endswith(".zip"):
        raise HTTPException(status_code=422, detail="Only ZIP bundles are supported")

    temp_parent = Path(_DATA_DIR) / "code_import_tmp"
    temp_parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="upload_", dir=temp_parent) as temp_name:
            archive_path = Path(temp_name) / "bundle.zip"
            uploaded_bytes = 0
            with archive_path.open("wb") as output:
                while chunk := await file.read(_UPLOAD_CHUNK_BYTES):
                    uploaded_bytes += len(chunk)
                    if uploaded_bytes > _MAX_UPLOAD_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail="ZIP bundle exceeds the 512 MB upload limit",
                        )
                    output.write(chunk)
            if uploaded_bytes == 0:
                raise HTTPException(status_code=422, detail="Uploaded ZIP bundle is empty")

            try:
                result = import_bundle(archive_path, db)
            except BundleImportError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            except FileExistsError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            return _import_response(result)
    finally:
        await file.close()


__all__ = ["router"]
