import json
import subprocess
import time
from pathlib import Path
from typing import Any, Dict

from app.faros.errors import (
    FarosConfigurationError,
    FarosProviderError,
    FarosProviderTimeoutError,
)
from app.faros.models.provider import ProviderResult, ProviderTask


def _normalize_result(task: ProviderTask, payload: Dict[str, Any]) -> ProviderResult:
    return ProviderResult(
        ok=payload.get('ok', True),
        provider=payload.get('provider', task.provider),
        model=payload.get('model', task.model or 'external-backend'),
        text=payload.get('text', ''),
        usage=payload.get('usage', {}),
        payload=payload.get('payload', {}),
        latency_ms=payload.get('latency_ms', 0),
        error=payload.get('error'),
    )


def _resolve_path(base: Path | None, raw_path: str | None) -> Path:
    if not raw_path:
        raise FarosConfigurationError('external backend path is required')
    path = Path(raw_path).expanduser()
    if base is not None and not path.is_absolute():
        path = base / path
    return path.resolve()


def _file_result(task: ProviderTask, response_path: Path) -> ProviderResult:
    data = json.loads(response_path.read_text())
    return _normalize_result(task, data)


def _approval_result(task: ProviderTask, decision_path: Path) -> ProviderResult:
    data = json.loads(decision_path.read_text())
    status = str(data.get('status', 'approved')).lower()
    approved = bool(data.get('approved', status == 'approved'))
    payload = dict(data.get('payload', {}))
    payload.setdefault('approvalStatus', status)
    payload.setdefault('approved', approved)
    return ProviderResult(
        ok=approved,
        provider=data.get('provider', task.provider),
        model=data.get('model', task.model or 'external-approval-file'),
        text=data.get('text', '' if approved else 'approval rejected'),
        usage=data.get('usage', {}),
        payload=payload,
        latency_ms=data.get('latency_ms', 0),
        error=data.get('error') or (None if approved else f'approval backend returned status {status}'),
    )


def _poll_for_path(path: Path, timeout_sec: int, poll_ms: int) -> Path:
    deadline = time.time() + timeout_sec
    while time.time() <= deadline:
        if path.is_file():
            return path
        time.sleep(max(poll_ms, 1) / 1000.0)
    raise FarosProviderTimeoutError(f'external backend timed out waiting for {path}')


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


def _queue_runtime_info(task: ProviderTask) -> Dict[str, Any]:
    return dict(task.options.get('_faros', {}))


def _queue_exchange_result(task: ProviderTask, queue_root: Path, token: str, *, approval: bool = False) -> ProviderResult:
    timeout = int((task.options.get('external') or {}).get('timeoutSec', 30))
    poll_ms = int((task.options.get('external') or {}).get('pollMs', 100))
    external = task.options.get('external') or {}
    runtime_info = _queue_runtime_info(task)

    request_dir_name = 'approval_requests' if approval else 'requests'
    response_dir_name = 'approval_decisions' if approval else 'responses'
    worker_dir_name = 'approval_workers' if approval else 'workers'
    claim_dir_name = 'approval_claims' if approval else 'claims'
    ack_dir_name = 'approval_acks' if approval else 'acks'

    requests_dir = queue_root / request_dir_name
    responses_dir = queue_root / response_dir_name
    workers_dir = queue_root / worker_dir_name
    claims_dir = queue_root / claim_dir_name
    acks_dir = queue_root / ack_dir_name
    for directory in (requests_dir, responses_dir, workers_dir, claims_dir, acks_dir):
        directory.mkdir(parents=True, exist_ok=True)

    request_path = requests_dir / f'{token}.json'
    response_path = responses_dir / f'{token}.json'
    claim_path = claims_dir / f'{token}.json'
    ack_path = acks_dir / f'{token}.json'

    worker_id = external.get('workerId')
    if worker_id and external.get('requireRegistration', False):
        worker_path = workers_dir / f'{worker_id}.json'
        _poll_for_path(worker_path, timeout, poll_ms)

    request_payload = task.model_dump()
    request_payload['_faros_queue'] = {
        'requestId': token,
        'submittedAt': time.time(),
        'approval': approval,
        'workerId': worker_id,
        'runId': runtime_info.get('runId'),
        'nodeId': runtime_info.get('nodeId'),
        'capabilityId': runtime_info.get('capabilityId'),
    }
    _write_json(request_path, request_payload)

    if external.get('requireClaim', False):
        _poll_for_path(claim_path, timeout, poll_ms)

    _poll_for_path(response_path, timeout, poll_ms)
    result = _approval_result(task, response_path) if approval else _file_result(task, response_path)

    ack_payload = {
        'requestId': token,
        'acknowledgedAt': time.time(),
        'approved': result.ok if approval else True,
        'provider': result.provider,
        'model': result.model,
        'runId': runtime_info.get('runId'),
        'nodeId': runtime_info.get('nodeId'),
        'capabilityId': runtime_info.get('capabilityId'),
    }
    _write_json(ack_path, ack_payload)
    return result


def maybe_invoke_external_backend(task: ProviderTask) -> ProviderResult | None:
    external = task.options.get('external') or {}
    if not isinstance(external, dict):
        raise FarosConfigurationError('provider external backend config must be a mapping')
    mode = external.get('mode')
    if not mode:
        return None

    if mode == 'file':
        response_path = external.get('responsePath')
        if not response_path:
            raise FarosConfigurationError('file backend requires external.responsePath')
        return _file_result(task, Path(response_path))

    if mode == 'workspace_file':
        workspace_root_raw = external.get('workspaceRoot')
        if not workspace_root_raw:
            raise FarosConfigurationError('workspace_file backend requires external.workspaceRoot')
        workspace_root = Path(workspace_root_raw).expanduser().resolve()
        workspace_root.mkdir(parents=True, exist_ok=True)
        request_path_raw = external.get('requestPath')
        if request_path_raw:
            request_path = _resolve_path(workspace_root, request_path_raw)
            request_path.parent.mkdir(parents=True, exist_ok=True)
            request_path.write_text(json.dumps(task.model_dump(), indent=2))
        response_path = _resolve_path(workspace_root, external.get('responsePath'))
        return _file_result(task, response_path)

    if mode == 'queue_file':
        queue_root_raw = external.get('queueRoot')
        if not queue_root_raw:
            raise FarosConfigurationError('queue_file backend requires external.queueRoot')
        queue_root = Path(queue_root_raw).expanduser().resolve()
        token = external.get('requestId') or task.options.get('_faros', {}).get('runId') or 'faros-request'
        return _queue_exchange_result(task, queue_root, token, approval=False)

    if mode == 'approval_file':
        decision_path_raw = external.get('decisionPath')
        if not decision_path_raw:
            raise FarosConfigurationError('approval_file backend requires external.decisionPath')
        decision_path = Path(decision_path_raw).expanduser().resolve()
        return _approval_result(task, decision_path)

    if mode == 'approval_queue':
        queue_root_raw = external.get('queueRoot')
        if not queue_root_raw:
            raise FarosConfigurationError('approval_queue backend requires external.queueRoot')
        queue_root = Path(queue_root_raw).expanduser().resolve()
        token = external.get('requestId') or task.options.get('_faros', {}).get('runId') or 'faros-approval'
        return _queue_exchange_result(task, queue_root, token, approval=True)

    if mode == 'command':
        command = external.get('command')
        if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
            raise FarosConfigurationError('command backend requires external.command as a non-empty string list')
        timeout = int(external.get('timeoutSec', 30))
        cwd = external.get('cwd')
        proc = subprocess.run(
            command,
            input=json.dumps(task.model_dump()),
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            check=False,
        )
        if proc.returncode != 0:
            return ProviderResult(
                ok=False,
                provider=task.provider,
                model=task.model or 'external-command',
                text=proc.stdout or '',
                error=(proc.stderr or f'command backend failed with exit code {proc.returncode}').strip(),
            )
        output_path = external.get('outputPath')
        raw = proc.stdout.strip()
        if not raw and output_path:
            raw = Path(output_path).read_text().strip()
        if not raw:
            raise FarosProviderError('command backend produced no JSON output', error_code='provider_protocol_error')
        data = json.loads(raw)
        return _normalize_result(task, data)

    raise FarosConfigurationError(f"unsupported external backend mode '{mode}'")
