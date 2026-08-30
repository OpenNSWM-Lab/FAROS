"""Platform-owned experiments API implementation."""

import csv
import hashlib
import io
import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.settings import get_settings
from app.core.paths import get_data_dir
from app.modules.platform.storage import (
    create_experiment,
    get_dataset,
    get_dataset_preview,
    get_experiment,
    get_execution_evidence,
    get_figure,
    get_metrics,
    ingest_metrics,
    list_datasets,
    list_experiments,
    list_figures,
    save_dataset,
    save_execution_evidence,
    update_experiment,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/experiments", tags=["experiments"])

ALL_FIGURE_TYPES = [
    "line", "bar", "groupedBar", "stackedBar",
    "scatter", "bubble", "histogram", "boxplot", "violin",
    "heatmap", "radar", "roc", "pr",
]


class CreateExperimentRequest(BaseModel):
    name: str = "Untitled Experiment"
    projectId: Optional[str] = None
    planSessionId: Optional[str] = None
    planLinkId: Optional[str] = None
    description: str = ""
    tags: List[str] = Field(default_factory=list)
    status: str = "created"


class MetricEntry(BaseModel):
    key: str
    value: float
    step: Optional[int] = None
    timestamp: Optional[str] = None


class IngestMetricsRequest(BaseModel):
    metrics: List[MetricEntry]


class ImportProjectEvidenceRequest(BaseModel):
    projectId: str = Field(..., pattern=r"^cproj_[A-Za-z0-9_-]+$")
    artifactPath: str = Field(..., min_length=1, max_length=500)
    metricsFile: str = "metrics.json"
    manifestFile: str = "run_manifest.json"
    predictionsFile: str = "predictions.jsonl"


class GenerateFigureRequest(BaseModel):
    providerName: Optional[str] = None
    model: Optional[str] = None
    preferredFigureType: Optional[str] = None
    datasetId: Optional[str] = None


class RenderFigureRequest(BaseModel):
    figureType: str
    title: str = ""
    xLabel: str = ""
    yLabel: str = ""
    caption: str = ""
    series: List[Dict[str, Any]] = Field(default_factory=list)
    heatmapData: Optional[Dict[str, Any]] = None
    datasetId: Optional[str] = None


class RecommendFiguresRequest(BaseModel):
    providerName: Optional[str] = None
    model: Optional[str] = None
    datasetId: Optional[str] = None


@router.get('/figure-types')
async def get_figure_types():
    return {"types": ALL_FIGURE_TYPES}


@router.get('')
async def list_experiments_endpoint(projectId: Optional[str] = None):
    experiments = list_experiments(project_id=projectId)
    return {"experiments": experiments, "total": len(experiments)}


@router.post('', status_code=status.HTTP_201_CREATED)
async def create_experiment_endpoint(req: CreateExperimentRequest):
    return create_experiment(req.model_dump())


@router.get('/{experiment_id}')
async def get_experiment_endpoint(experiment_id: str):
    record = get_experiment(experiment_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Experiment '{experiment_id}' not found")
    metrics = get_metrics(experiment_id)
    figures = list_figures(experiment_id)
    datasets = list_datasets(experiment_id)
    record['metricsCount'] = len(metrics)
    record['figuresCount'] = len(figures)
    record['datasetsCount'] = len(datasets)
    record['executionEvidence'] = get_execution_evidence(experiment_id)
    return record


def _parse_uploaded_dataset(filename: str, raw_bytes: bytes) -> tuple[List[Dict[str, Any]], str]:
    text = raw_bytes.decode('utf-8', errors='replace')
    if filename.endswith('.csv'):
        reader = csv.DictReader(io.StringIO(text))
        parsed = [dict(row) for row in reader]
        for row in parsed:
            for key, value in row.items():
                try:
                    row[key] = float(value)
                except (ValueError, TypeError):
                    pass
        return parsed, 'csv'
    if filename.endswith('.jsonl'):
        parsed = []
        for line_number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f'Invalid JSONL at line {line_number}: {exc.msg}') from exc
            if not isinstance(value, dict):
                raise ValueError(f'Invalid JSONL at line {line_number}: each row must be an object')
            parsed.append(value)
        return parsed, 'jsonl'
    if filename.endswith('.json'):
        data = json.loads(text)
        if isinstance(data, list):
            if not all(isinstance(row, dict) for row in data):
                raise ValueError('JSON arrays must contain objects')
            return data, 'json'
        if isinstance(data, dict):
            return [data], 'json'
        raise ValueError('JSON content must be an object or an array of objects')
    raise ValueError('Unsupported format. Use CSV, JSON, or JSONL.')


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _resolve_project_artifact(project_id: str, artifact_path: str) -> Path:
    repo = (get_data_dir() / 'code_projects' / project_id / 'repo').resolve()
    target = (repo / artifact_path).resolve()
    if repo not in target.parents or not target.is_dir():
        raise HTTPException(status_code=400, detail='Artifact path must be an existing directory inside the linked project')
    return target


def _flatten_numeric_metrics(payload: Dict[str, Any], prefix: str = '') -> List[Dict[str, Any]]:
    entries = []
    for key, value in payload.items():
        metric_key = f'{prefix}.{key}' if prefix else key
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            entries.append({'key': metric_key, 'value': float(value)})
        elif isinstance(value, dict):
            entries.extend(_flatten_numeric_metrics(value, metric_key))
        elif isinstance(value, list) and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value):
            entries.extend(
                {'key': f'{metric_key}.{index}', 'value': float(item), 'step': index}
                for index, item in enumerate(value)
            )
    return entries


@router.get('/{experiment_id}/evidence')
async def get_experiment_evidence_endpoint(experiment_id: str):
    if not get_experiment(experiment_id):
        raise HTTPException(status_code=404, detail=f"Experiment '{experiment_id}' not found")
    evidence = get_execution_evidence(experiment_id)
    if not evidence:
        raise HTTPException(status_code=404, detail='No verified execution evidence has been imported')
    return evidence


@router.post('/{experiment_id}/evidence/import', status_code=status.HTTP_201_CREATED)
async def import_project_evidence(experiment_id: str, req: ImportProjectEvidenceRequest):
    experiment = get_experiment(experiment_id)
    if not experiment:
        raise HTTPException(status_code=404, detail=f"Experiment '{experiment_id}' not found")
    linked_project = experiment.get('projectId')
    if linked_project and linked_project != req.projectId:
        raise HTTPException(status_code=409, detail='Evidence project does not match the experiment project')

    artifact_dir = _resolve_project_artifact(req.projectId, req.artifactPath)
    files = {
        'metrics': artifact_dir / req.metricsFile,
        'manifest': artifact_dir / req.manifestFile,
        'predictions': artifact_dir / req.predictionsFile,
    }
    if any(not path.is_file() for path in files.values()):
        missing = [name for name, path in files.items() if not path.is_file()]
        raise HTTPException(status_code=400, detail=f"Evidence bundle is incomplete: missing {', '.join(missing)}")
    if files['predictions'].stat().st_size > 50_000_000:
        raise HTTPException(status_code=400, detail='Predictions artifact exceeds 50MB')

    try:
        metrics = json.loads(files['metrics'].read_text(encoding='utf-8'))
        manifest = json.loads(files['manifest'].read_text(encoding='utf-8'))
        predictions, prediction_format = _parse_uploaded_dataset(
            req.predictionsFile.lower(),
            files['predictions'].read_bytes(),
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f'Invalid execution evidence: {exc}') from exc
    if not isinstance(metrics, dict) or metrics.get('evidence_status') != 'executed':
        raise HTTPException(status_code=400, detail="metrics.json must declare evidence_status='executed'")
    inputs = manifest.get('inputs') if isinstance(manifest, dict) else None
    if not isinstance(inputs, dict) or not inputs:
        raise HTTPException(status_code=400, detail='run_manifest.json must contain input hashes')
    if any(not re.fullmatch(r'[0-9a-f]{64}', str(value)) for value in inputs.values()):
        raise HTTPException(status_code=400, detail='Every manifest input hash must be a lowercase SHA-256 digest')
    expected_rows = int(metrics.get('holdout_records') or 0)
    if expected_rows <= 0 or len(predictions) != expected_rows:
        raise HTTPException(
            status_code=400,
            detail=f'Prediction row count mismatch: metrics={expected_rows}, predictions={len(predictions)}',
        )
    required_prediction_fields = {'claim_id', 'gold_mismatch', 'mismatch_score', 'prediction'}
    if any(not required_prediction_fields.issubset(row) for row in predictions):
        raise HTTPException(status_code=400, detail='Prediction rows are missing required audit fields')

    hashes = {name: _sha256_file(path) for name, path in files.items()}
    bundle_sha256 = hashlib.sha256(
        ''.join(f'{name}:{hashes[name]}\n' for name in sorted(hashes)).encode('utf-8')
    ).hexdigest()
    previous = get_execution_evidence(experiment_id)
    if previous and previous.get('bundleSha256') == bundle_sha256:
        return {**previous, 'duplicate': True}

    metric_entries = _flatten_numeric_metrics(metrics)
    ingested = ingest_metrics(experiment_id, metric_entries)
    dataset = save_dataset(
        experiment_id,
        'SciFact holdout predictions',
        prediction_format,
        files['predictions'].read_bytes(),
        predictions,
    )
    evidence = {
        'schemaVersion': 'faros-execution-evidence/v1',
        'experimentId': experiment_id,
        'projectId': req.projectId,
        'status': 'verified',
        'artifactPath': req.artifactPath,
        'importedAt': datetime.now(UTC).isoformat(),
        'bundleSha256': bundle_sha256,
        'artifactSha256': hashes,
        'inputSha256': inputs,
        'predictionRows': len(predictions),
        'ingestedMetrics': ingested,
        'datasetId': dataset['id'],
        'checks': {
            'projectBoundary': True,
            'executedStatus': True,
            'inputHashesPresent': True,
            'predictionRowCount': True,
            'auditFieldsPresent': True,
        },
        'limitations': list(metrics.get('limitations') or []),
    }
    save_execution_evidence(experiment_id, evidence)
    update_experiment(experiment_id, {
        'projectId': req.projectId,
        'status': 'completed',
        'evidenceStatus': 'verified',
        'evidenceBundleSha256': bundle_sha256,
    })
    return evidence


@router.post('/{experiment_id}/metrics', status_code=status.HTTP_201_CREATED)
async def ingest_metrics_endpoint(experiment_id: str, req: IngestMetricsRequest):
    record = get_experiment(experiment_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Experiment '{experiment_id}' not found")
    count = ingest_metrics(experiment_id, [metric.model_dump() for metric in req.metrics])
    return {"ingested": count, "experimentId": experiment_id}


@router.get('/{experiment_id}/metrics')
async def get_metrics_endpoint(experiment_id: str):
    record = get_experiment(experiment_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Experiment '{experiment_id}' not found")
    metrics = get_metrics(experiment_id)
    return {"metrics": metrics, "total": len(metrics)}


@router.post('/{experiment_id}/datasets/upload', status_code=status.HTTP_201_CREATED)
async def upload_dataset(
    experiment_id: str,
    file: UploadFile = File(...),
    name: str = Form('uploaded_data'),
):
    record = get_experiment(experiment_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Experiment '{experiment_id}' not found")

    raw_bytes = await file.read()
    if len(raw_bytes) > 50_000_000:
        raise HTTPException(status_code=400, detail='File too large (max 50MB)')

    filename = (file.filename or 'data').lower()
    try:
        parsed, fmt = _parse_uploaded_dataset(filename, raw_bytes)
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:300])
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to parse file: {str(exc)[:200]}")

    if not parsed:
        raise HTTPException(status_code=400, detail='File contains no data rows')

    meta = save_dataset(experiment_id, name, fmt, raw_bytes, parsed)
    return meta


@router.get('/{experiment_id}/datasets')
async def list_datasets_endpoint(experiment_id: str):
    datasets = list_datasets(experiment_id)
    return {"datasets": datasets, "total": len(datasets)}


@router.get('/datasets/{dataset_id}')
async def get_dataset_endpoint(dataset_id: str):
    dataset = get_dataset(dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found")
    return dataset


@router.get('/datasets/{dataset_id}/preview')
async def get_dataset_preview_endpoint(dataset_id: str):
    dataset = get_dataset(dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found")
    preview = get_dataset_preview(dataset_id)
    return {"datasetId": dataset_id, "rows": preview, "total": len(preview), "columns": dataset.get('columns', [])}


@router.post('/{experiment_id}/figures/generate', status_code=status.HTTP_201_CREATED)
async def generate_figure_endpoint(experiment_id: str, req: GenerateFigureRequest):
    record = get_experiment(experiment_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Experiment '{experiment_id}' not found")
    try:
        from app.services.figure_service import generate_figure
        data_override = None
        if req.datasetId:
            preview = get_dataset_preview(req.datasetId)
            if preview:
                data_override = preview
        settings = get_settings()
        provider_name = req.providerName or settings.get_active_provider()
        model = req.model or settings.get_active_model(provider_name)
        artifact = generate_figure(
            experiment_id=experiment_id,
            provider_name=provider_name,
            model=model,
            preferred_figure_type=req.preferredFigureType,
            data_override=data_override,
        )
        return artifact
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error('Figure generation failed: %s', exc, exc_info=True)
        error_msg = str(exc)
        code = 400 if 'API key' in error_msg or 'not configured' in error_msg else 500
        raise HTTPException(status_code=code, detail=f"Figure generation failed: {error_msg}")


@router.post('/{experiment_id}/figures/render', status_code=status.HTTP_201_CREATED)
async def render_figure_endpoint(experiment_id: str, req: RenderFigureRequest):
    record = get_experiment(experiment_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Experiment '{experiment_id}' not found")
    if req.figureType not in ALL_FIGURE_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported figure type. Use one of: {ALL_FIGURE_TYPES}")
    try:
        from app.services.figure_service import generate_figure
        user_spec = {
            'figureType': req.figureType,
            'title': req.title,
            'xLabel': req.xLabel,
            'yLabel': req.yLabel,
            'caption': req.caption,
            'series': req.series,
            'heatmapData': req.heatmapData,
        }
        data_override = None
        if req.datasetId:
            preview = get_dataset_preview(req.datasetId)
            if preview:
                data_override = preview
        artifact = generate_figure(
            experiment_id=experiment_id,
            user_spec=user_spec,
            data_override=data_override or [{'_': 1}],
        )
        return artifact
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error('Figure rendering failed: %s', exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Figure rendering failed: {str(exc)}")


@router.get('/{experiment_id}/figures')
async def list_figures_endpoint(experiment_id: str):
    figures = list_figures(experiment_id)
    return {"figures": figures, "total": len(figures)}


@router.get('/figures/{figure_id}/png')
async def get_figure_png(figure_id: str):
    figure = get_figure(figure_id)
    if not figure:
        raise HTTPException(status_code=404, detail=f"Figure '{figure_id}' not found")
    png_path = figure.get('pathPng')
    if not png_path or not os.path.isfile(png_path):
        raise HTTPException(status_code=404, detail='PNG file not found')
    return FileResponse(png_path, media_type='image/png', filename=f'{figure_id}.png')


@router.get('/figures/{figure_id}/pdf')
async def get_figure_pdf(figure_id: str):
    figure = get_figure(figure_id)
    if not figure:
        raise HTTPException(status_code=404, detail=f"Figure '{figure_id}' not found")
    pdf_path = figure.get('pathPdf')
    if not pdf_path or not os.path.isfile(pdf_path):
        raise HTTPException(status_code=404, detail='PDF file not found')
    return FileResponse(pdf_path, media_type='application/pdf', filename=f'{figure_id}.pdf')


@router.get('/figures/{figure_id}/code')
async def get_figure_code(figure_id: str):
    figure = get_figure(figure_id)
    if not figure:
        raise HTTPException(status_code=404, detail=f"Figure '{figure_id}' not found")
    code_path = figure.get('pathCode')
    if not code_path or not os.path.isfile(code_path):
        raise HTTPException(status_code=404, detail='Code file not found for this figure')
    with open(code_path, 'r') as handle:
        code = handle.read()
    return {'figureId': figure_id, 'code': code, 'language': 'python'}


@router.get('/figures/{figure_id}/download/code.py')
async def download_figure_code(figure_id: str):
    figure = get_figure(figure_id)
    if not figure:
        raise HTTPException(status_code=404, detail=f"Figure '{figure_id}' not found")
    code_path = figure.get('pathCode')
    if not code_path or not os.path.isfile(code_path):
        raise HTTPException(status_code=404, detail='Code file not found')
    return FileResponse(code_path, media_type='text/x-python', filename=f'{figure_id}_plot.py')


@router.post('/{experiment_id}/figures/recommend')
async def recommend_figures_endpoint(experiment_id: str, req: RecommendFiguresRequest):
    record = get_experiment(experiment_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Experiment '{experiment_id}' not found")
    try:
        from app.services.figure_service import recommend_figures
        data_override = None
        if req.datasetId:
            preview = get_dataset_preview(req.datasetId)
            if preview:
                data_override = preview
        recommendations = recommend_figures(
            experiment_id=experiment_id,
            provider_name=req.providerName,
            model=req.model,
            data_override=data_override,
        )
        return {'experimentId': experiment_id, 'recommendations': recommendations}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error('Figure recommendation failed: %s', exc, exc_info=True)
        error_msg = str(exc)
        code = 400 if 'API key' in error_msg or 'not configured' in error_msg else 500
        raise HTTPException(status_code=code, detail=f'Recommendation failed: {error_msg}')
