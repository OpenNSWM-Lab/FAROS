import threading
from pathlib import Path
from typing import Any, Dict, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.faros.errors import FarosError, FarosNotFoundError
from app.faros.registry.agent_registry import get_agent_registry
from app.faros.registry.artifact_registry import get_artifact_registry
from app.faros.registry.blueprint_registry import get_blueprint_registry
from app.faros.registry.capability_registry import get_capability_registry
from app.faros.registry.package_compatibility import build_dependency_report
from app.faros.registry.package_trust import inspect_package_trust
from app.faros.registry.profile_registry import get_profile_registry
from app.faros.registry.provider_registry import get_provider_registry
from app.faros.registry.skill_registry import get_skill_registry
from app.faros.registry.verifier_registry import get_verifier_registry
from app.faros.runtime.orchestrator import get_orchestrator

router = APIRouter(prefix='/faros', tags=['faros'])

def _http_error_from_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, FarosError):
        return HTTPException(
            status_code=exc.status_code,
            detail={
                'message': str(exc),
                'errorCode': exc.error_code,
                'category': exc.category,
            },
        )
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail={'message': str(exc), 'errorCode': 'not_found', 'category': 'not_found'})
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail={'message': str(exc), 'errorCode': 'not_found', 'category': 'not_found'})
    if isinstance(exc, TimeoutError):
        return HTTPException(status_code=504, detail={'message': str(exc), 'errorCode': 'timeout', 'category': 'provider'})
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail={'message': str(exc), 'errorCode': 'validation_error', 'category': 'validation'})
    return HTTPException(status_code=500, detail={'message': str(exc), 'errorCode': 'internal_error', 'category': 'internal'})


def _raise_http_error(exc: Exception) -> None:
    raise _http_error_from_exception(exc) from exc


def _inspect_package_source(package_type: str, source_path: str) -> Dict[str, Any]:
    source_dir = Path(source_path).expanduser().resolve()
    if source_dir.is_file():
        source_dir = source_dir.parent
    return inspect_package_trust(package_type, source_dir.name, source_dir)


def _current_package_graph() -> Dict[str, Any]:
    return {
        'profiles': get_profile_registry().list(),
        'agents': get_agent_registry().list(),
        'skills': get_skill_registry().list(),
        'blueprints': get_blueprint_registry().list(),
        'verifiers': get_verifier_registry().list_packages(),
    }


class FarosBindingRequest(BaseModel):
    blueprintId: str = 'ml_paper'
    profileId: str = 'faros_llm'


class CreateFarosRunRequest(FarosBindingRequest):
    executionMode: Literal['plan', 'execute'] = 'execute'
    asyncExecution: bool = True
    runtimeOptions: Dict[str, Any] = Field(default_factory=dict)
    inputs: Dict[str, Any] = Field(default_factory=dict)


class SkillPackageRequest(BaseModel):
    sourcePath: str
    overwrite: bool = False
    allowDowngrade: bool = False
    allowUntrusted: bool = True


class AgentPackageRequest(BaseModel):
    sourcePath: str
    overwrite: bool = False
    allowDowngrade: bool = False
    allowUntrusted: bool = True


class BlueprintPackageRequest(BaseModel):
    sourcePath: str
    overwrite: bool = False
    allowDowngrade: bool = False
    allowUntrusted: bool = True


class PackageTrustRequest(BaseModel):
    packageType: Literal['skill', 'agent', 'blueprint', 'verifier']
    sourcePath: str


class SkillUninstallRequest(BaseModel):
    skillId: str


class SkillRollbackRequest(BaseModel):
    skillId: str


class AgentUninstallRequest(BaseModel):
    agentId: str


class AgentRollbackRequest(BaseModel):
    agentId: str


class BlueprintUninstallRequest(BaseModel):
    blueprintId: str


class BlueprintRollbackRequest(BaseModel):
    blueprintId: str


class VerifierPackageRequest(BaseModel):
    sourcePath: str
    overwrite: bool = False
    allowDowngrade: bool = False
    allowUntrusted: bool = True


class VerifierUninstallRequest(BaseModel):
    verifierId: str


class VerifierRollbackRequest(BaseModel):
    verifierId: str


class RunMemoryRecallRequest(BaseModel):
    mergeIntoRun: bool = False


class RunStepReplayRequest(BaseModel):
    includeDownstream: bool = True


@router.get('/health')
async def faros_health() -> Dict[str, Any]:
    return {
        'status': 'healthy',
        'runtime': 'faros',
        'blueprints': len(get_blueprint_registry().list()),
        'profiles': len(get_profile_registry().list()),
        'agents': len(get_agent_registry().list()),
        'skills': len(get_skill_registry().list()),
        'capabilities': len(get_capability_registry().list()),
    }


@router.get('/blueprints')
async def list_blueprints() -> Dict[str, Any]:
    registry = get_blueprint_registry()
    return {'blueprints': [bp.model_dump() for bp in registry.list()]}


@router.post('/blueprints/validate')
async def validate_blueprint_package(req: BlueprintPackageRequest) -> Dict[str, Any]:
    try:
        blueprint = get_blueprint_registry().validate_package(req.sourcePath)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        _raise_http_error(exc)
    return {'status': 'valid', 'blueprint': blueprint.model_dump()}


@router.post('/blueprints/install', status_code=status.HTTP_201_CREATED)
async def install_blueprint_package(req: BlueprintPackageRequest) -> Dict[str, Any]:
    try:
        blueprint = get_blueprint_registry().install_package(
            req.sourcePath, overwrite=req.overwrite, allow_downgrade=req.allowDowngrade, allow_untrusted=req.allowUntrusted
        )
    except (FileNotFoundError, KeyError, ValueError) as exc:
        _raise_http_error(exc)
    return {'status': 'installed', 'blueprint': blueprint.model_dump()}


@router.post('/blueprints/refresh')
async def refresh_blueprints() -> Dict[str, Any]:
    blueprints = get_blueprint_registry().refresh()
    return {'status': 'refreshed', 'blueprints': [bp.model_dump() for bp in blueprints], 'total': len(blueprints)}


@router.post('/blueprints/uninstall')
async def uninstall_blueprint_package(req: BlueprintUninstallRequest) -> Dict[str, Any]:
    try:
        result = get_blueprint_registry().uninstall_package(req.blueprintId)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        _raise_http_error(exc)
    return result


@router.post('/blueprints/rollback')
async def rollback_blueprint_package(req: BlueprintRollbackRequest) -> Dict[str, Any]:
    try:
        blueprint = get_blueprint_registry().rollback_package(req.blueprintId)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        _raise_http_error(exc)
    return {'status': 'rolled_back', 'blueprint': blueprint.model_dump()}


@router.get('/profiles')
async def list_profiles() -> Dict[str, Any]:
    registry = get_profile_registry()
    return {'profiles': [profile.model_dump() for profile in registry.list()]}


@router.get('/agents')
async def list_agents() -> Dict[str, Any]:
    return {'agents': [agent.model_dump() for agent in get_agent_registry().list()]}


@router.post('/agents/validate')
async def validate_agent_package(req: AgentPackageRequest) -> Dict[str, Any]:
    try:
        agent = get_agent_registry().validate_package(req.sourcePath)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        _raise_http_error(exc)
    return {'status': 'valid', 'agent': agent.model_dump()}


@router.post('/agents/install', status_code=status.HTTP_201_CREATED)
async def install_agent_package(req: AgentPackageRequest) -> Dict[str, Any]:
    try:
        agent = get_agent_registry().install_package(
            req.sourcePath, overwrite=req.overwrite, allow_downgrade=req.allowDowngrade, allow_untrusted=req.allowUntrusted
        )
    except (FileNotFoundError, KeyError, ValueError) as exc:
        _raise_http_error(exc)
    return {'status': 'installed', 'agent': agent.model_dump()}


@router.post('/agents/refresh')
async def refresh_agents() -> Dict[str, Any]:
    agents = get_agent_registry().refresh()
    return {'status': 'refreshed', 'agents': [agent.model_dump() for agent in agents], 'total': len(agents)}


@router.post('/agents/uninstall')
async def uninstall_agent_package(req: AgentUninstallRequest) -> Dict[str, Any]:
    try:
        result = get_agent_registry().uninstall_package(req.agentId)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        _raise_http_error(exc)
    return result


@router.post('/agents/rollback')
async def rollback_agent_package(req: AgentRollbackRequest) -> Dict[str, Any]:
    try:
        agent = get_agent_registry().rollback_package(req.agentId)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        _raise_http_error(exc)
    return {'status': 'rolled_back', 'agent': agent.model_dump()}


@router.get('/skills')
async def list_skills() -> Dict[str, Any]:
    return {'skills': [skill.model_dump() for skill in get_skill_registry().list()]}


@router.post('/skills/validate')
async def validate_skill_package(req: SkillPackageRequest) -> Dict[str, Any]:
    try:
        skill = get_skill_registry().validate_package(req.sourcePath)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        _raise_http_error(exc)
    return {'status': 'valid', 'skill': skill.model_dump()}


@router.post('/skills/install', status_code=status.HTTP_201_CREATED)
async def install_skill_package(req: SkillPackageRequest) -> Dict[str, Any]:
    try:
        skill = get_skill_registry().install_package(
            req.sourcePath, overwrite=req.overwrite, allow_downgrade=req.allowDowngrade, allow_untrusted=req.allowUntrusted
        )
    except (FileNotFoundError, KeyError, ValueError) as exc:
        _raise_http_error(exc)
    return {'status': 'installed', 'skill': skill.model_dump()}


@router.post('/skills/refresh')
async def refresh_skills() -> Dict[str, Any]:
    skills = get_skill_registry().refresh()
    return {'status': 'refreshed', 'skills': [skill.model_dump() for skill in skills], 'total': len(skills)}


@router.post('/skills/uninstall')
async def uninstall_skill_package(req: SkillUninstallRequest) -> Dict[str, Any]:
    try:
        result = get_skill_registry().uninstall_package(req.skillId)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        _raise_http_error(exc)
    return result


@router.post('/skills/rollback')
async def rollback_skill_package(req: SkillRollbackRequest) -> Dict[str, Any]:
    try:
        skill = get_skill_registry().rollback_package(req.skillId)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        _raise_http_error(exc)
    return {'status': 'rolled_back', 'skill': skill.model_dump()}


@router.post('/packages/trust/validate')
async def validate_package_trust(req: PackageTrustRequest) -> Dict[str, Any]:
    try:
        return {'trust': _inspect_package_source(req.packageType, req.sourcePath)}
    except (FileNotFoundError, ValueError) as exc:
        _raise_http_error(exc)


@router.get('/packages/dependencies')
async def get_package_dependencies() -> Dict[str, Any]:
    return build_dependency_report(_current_package_graph())


@router.get('/packages/dependencies/resolve')
async def get_package_dependency_resolution() -> Dict[str, Any]:
    return build_dependency_report(_current_package_graph())


@router.get('/packages/audit')
async def list_package_audit(
    packageType: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> Dict[str, Any]:
    registries = {
        'skill': get_skill_registry(),
        'agent': get_agent_registry(),
        'blueprint': get_blueprint_registry(),
        'verifier': get_verifier_registry(),
    }
    if packageType:
        if packageType not in registries:
            raise _http_error_from_exception(FarosError(message=f"Unsupported packageType '{packageType}'", status_code=400, error_code='unsupported_package_type', category='validation'))
        events = registries[packageType].audit_log(limit=limit)
        return {'events': events, 'total': len(events)}

    events = []
    for registry in registries.values():
        events.extend(registry.audit_log(limit=limit))
    events = sorted(events, key=lambda item: item.get('timestamp', ''), reverse=True)[:limit]
    return {'events': events, 'total': len(events)}


@router.get('/artifacts/schemas')
async def list_artifact_schemas() -> Dict[str, Any]:
    return {'schemas': [schema.model_dump() for schema in get_artifact_registry().list()]}


@router.get('/providers')
async def list_providers() -> Dict[str, Any]:
    registry = get_provider_registry()
    return {'providers': registry.list(), 'health': registry.health_summary()}


@router.get('/verifiers')
async def list_verifiers() -> Dict[str, Any]:
    registry = get_verifier_registry()
    return {'verifiers': registry.list(), 'packs': registry.packs(), 'packMetadata': registry.pack_descriptors(), 'packages': [item.model_dump() for item in registry.list_packages()], 'defaultPack': registry.default_pack()}


@router.post('/verifiers/validate')
async def validate_verifier_package(req: VerifierPackageRequest) -> Dict[str, Any]:
    try:
        verifier = get_verifier_registry().validate_package(req.sourcePath)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        _raise_http_error(exc)
    return {'status': 'valid', 'verifierPackage': verifier.model_dump()}


@router.post('/verifiers/install', status_code=status.HTTP_201_CREATED)
async def install_verifier_package(req: VerifierPackageRequest) -> Dict[str, Any]:
    try:
        verifier = get_verifier_registry().install_package(
            req.sourcePath, overwrite=req.overwrite, allow_downgrade=req.allowDowngrade, allow_untrusted=req.allowUntrusted
        )
    except (FileNotFoundError, KeyError, ValueError) as exc:
        _raise_http_error(exc)
    return {'status': 'installed', 'verifierPackage': verifier.model_dump()}


@router.post('/verifiers/refresh')
async def refresh_verifier_packages() -> Dict[str, Any]:
    items = get_verifier_registry().refresh()
    return {'status': 'refreshed', 'verifierPackages': [item.model_dump() for item in items], 'total': len(items)}


@router.post('/verifiers/uninstall')
async def uninstall_verifier_package(req: VerifierUninstallRequest) -> Dict[str, Any]:
    try:
        result = get_verifier_registry().uninstall_package(req.verifierId)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        _raise_http_error(exc)
    return result


@router.post('/verifiers/rollback')
async def rollback_verifier_package(req: VerifierRollbackRequest) -> Dict[str, Any]:
    try:
        verifier = get_verifier_registry().rollback_package(req.verifierId)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        _raise_http_error(exc)
    return {'status': 'rolled_back', 'verifierPackage': verifier.model_dump()}


@router.get('/capabilities')
async def list_capabilities() -> Dict[str, Any]:
    return {
        'capabilities': get_capability_registry().list(),
        'providers': get_provider_registry().list(),
    }


@router.get('/runs')
async def list_runs() -> Dict[str, Any]:
    runs = get_orchestrator().list_runs()
    return {'runs': runs, 'total': len(runs)}


@router.post('/preflight')
async def preflight_run(req: FarosBindingRequest) -> Dict[str, Any]:
    try:
        result = get_orchestrator().preflight_run(req.blueprintId, req.profileId)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        _raise_http_error(exc)
    return result.model_dump()


@router.post('/runs', status_code=status.HTTP_201_CREATED)
async def create_run(req: CreateFarosRunRequest) -> Dict[str, Any]:
    orchestrator = get_orchestrator()
    try:
        run = orchestrator.create_run(
            blueprint_id=req.blueprintId,
            profile_id=req.profileId,
            inputs=req.inputs,
            execution_mode=req.executionMode,
            runtime_options=req.runtimeOptions,
        )
    except (FileNotFoundError, KeyError, ValueError) as exc:
        _raise_http_error(exc)
    if req.executionMode == 'plan':
        return run

    if req.asyncExecution:
        thread = threading.Thread(target=orchestrator.execute_run, args=(run['id'],), daemon=True)
        thread.start()
        return run

    return orchestrator.execute_run(run['id'])


@router.get('/runs/{run_id}')
async def get_run(run_id: str) -> Dict[str, Any]:
    run = get_orchestrator().get_run(run_id)
    if not run:
        raise _http_error_from_exception(FarosNotFoundError(f"FAROS run '{run_id}' not found"))
    return run


@router.post('/runs/{run_id}/resume')
async def resume_run(run_id: str) -> Dict[str, Any]:
    try:
        run = get_orchestrator().resume_run(run_id)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        _raise_http_error(exc)
    return run


@router.post('/runs/{run_id}/steps/{node_id}/skip')
async def skip_run_step(run_id: str, node_id: str) -> Dict[str, Any]:
    try:
        run = get_orchestrator().skip_step(run_id, node_id)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        _raise_http_error(exc)
    return run


@router.post('/runs/{run_id}/steps/{node_id}/retry')
async def retry_run_step(run_id: str, node_id: str) -> Dict[str, Any]:
    try:
        run = get_orchestrator().retry_step(run_id, node_id)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        _raise_http_error(exc)
    return run


@router.post('/runs/{run_id}/steps/{node_id}/replay')
async def replay_run_step(run_id: str, node_id: str, req: RunStepReplayRequest) -> Dict[str, Any]:
    try:
        run = get_orchestrator().replay_step(run_id, node_id, include_downstream=req.includeDownstream)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        _raise_http_error(exc)
    return run


@router.get('/runs/{run_id}/detail')
async def get_run_detail(run_id: str) -> Dict[str, Any]:
    detail = get_orchestrator().get_run_detail(run_id)
    if not detail:
        raise _http_error_from_exception(FarosNotFoundError(f"FAROS run '{run_id}' not found"))
    return detail


@router.get('/runs/{run_id}/memory')
async def get_run_memory(run_id: str) -> Dict[str, Any]:
    memory = get_orchestrator().get_run_memory(run_id)
    if memory is None:
        raise _http_error_from_exception(FarosNotFoundError(f"FAROS run '{run_id}' not found"))
    return {'runId': run_id, 'memory': memory}


@router.get('/runs/{run_id}/memory/query')
async def query_run_memory(
    run_id: str,
    keys: str | None = Query(default=None),
    scopes: str | None = Query(default=None),
    includeArchives: bool = Query(default=False),
    includeSummary: bool = Query(default=True),
    includeHistory: bool = Query(default=False),
    historyLimit: int | None = Query(default=None, ge=0),
) -> Dict[str, Any]:
    payload = get_orchestrator().query_run_memory(
        run_id,
        keys=[item for item in (keys or '').split(',') if item] or None,
        scopes=[item for item in (scopes or '').split(',') if item] or None,
        include_archives=includeArchives,
        include_summary=includeSummary,
        include_history=includeHistory,
        history_limit=historyLimit,
    )
    if payload is None:
        raise _http_error_from_exception(FarosNotFoundError(f"FAROS run '{run_id}' not found"))
    return {'runId': run_id, 'query': payload}


@router.post('/runs/{run_id}/memory/recall/{scope}')
async def recall_run_memory(run_id: str, scope: str, req: RunMemoryRecallRequest) -> Dict[str, Any]:
    try:
        payload = get_orchestrator().recall_run_memory(run_id, scope, merge_into_run=req.mergeIntoRun)
    except KeyError as exc:
        _raise_http_error(exc)
    if payload is None:
        raise _http_error_from_exception(FarosNotFoundError(f"FAROS run '{run_id}' not found"))
    return {'runId': run_id, 'recall': payload, 'memory': get_orchestrator().get_run_memory(run_id)}


@router.get('/runs/{run_id}/events')
async def get_run_events(run_id: str) -> Dict[str, Any]:
    run = get_orchestrator().get_run(run_id)
    if not run:
        raise _http_error_from_exception(FarosNotFoundError(f"FAROS run '{run_id}' not found"))
    events = get_orchestrator().list_events(run_id)
    return {'runId': run_id, 'events': events, 'total': len(events)}


@router.get('/runs/{run_id}/artifacts')
async def get_run_artifacts(run_id: str) -> Dict[str, Any]:
    run = get_orchestrator().get_run(run_id)
    if not run:
        raise _http_error_from_exception(FarosNotFoundError(f"FAROS run '{run_id}' not found"))
    artifacts = get_orchestrator().list_artifacts(run_id)
    return {'runId': run_id, 'artifacts': artifacts, 'total': len(artifacts)}
