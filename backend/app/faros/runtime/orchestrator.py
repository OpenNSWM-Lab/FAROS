from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.faros.errors import (
    FarosBlockedError,
    FarosConfigurationError,
    FarosNotFoundError,
    FarosPreflightError,
    FarosVerificationError,
)
from app.faros.memory.research_memory import ResearchMemory
from app.faros.models.blueprint import Blueprint, WorkflowNode
from app.faros.models.execution import ExecutionContext
from app.faros.models.profile import Profile
from app.faros.models.verification import RunPreflightResult
from app.faros.registry.agent_registry import get_agent_registry
from app.faros.registry.artifact_registry import get_artifact_registry
from app.faros.registry.blueprint_registry import get_blueprint_registry
from app.faros.registry.capability_registry import get_capability_registry
from app.faros.registry.profile_registry import get_profile_registry
from app.faros.registry.provider_registry import get_provider_registry
from app.faros.registry.skill_registry import get_skill_registry
from app.faros.registry.verifier_registry import get_verifier_registry
from app.faros.runtime.agent_executor import AgentExecutionPlan, AgentExecutor
from app.faros.runtime.artifact_store import ArtifactStore
from app.faros.runtime.event_log import EventLog
from app.faros.runtime.graph_builder import GraphBuilder
from app.faros.runtime.state_store import FarosStateStore
from app.faros.verification.preflight_validator import RunPreflightValidator
from app.faros.verification.rules import VerifierDispatcher


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _replay_scope(include_downstream: bool) -> str:
    return 'subgraph' if include_downstream else 'node_only'


class FarosOrchestrator:
    """FAROS orchestrator with dependency-aware runtime execution."""

    def __init__(self):
        self.blueprints = get_blueprint_registry()
        self.profiles = get_profile_registry()
        self.capabilities = get_capability_registry()
        self.providers = get_provider_registry()
        self.agents = get_agent_registry()
        self.artifacts = get_artifact_registry()
        self.skills = get_skill_registry()
        self.state_store = FarosStateStore()
        self.graph_builder = GraphBuilder()
        self.event_log = EventLog(self.state_store)
        self.artifact_store = ArtifactStore(self.state_store)
        self.verifier_registry = get_verifier_registry()
        self.verifier = VerifierDispatcher(registry=self.verifier_registry)
        self.preflight_validator = RunPreflightValidator(self.capabilities, self.providers)
        self.agent_executor = AgentExecutor()

    def preflight_run(self, blueprint_id: str, profile_id: str) -> RunPreflightResult:
        blueprint = self.blueprints.get(blueprint_id)
        profile = self.profiles.get(profile_id)
        return self.preflight_validator.validate(blueprint, profile, self._resolve_node_runtime)

    def create_run(self, blueprint_id: str, profile_id: str, inputs: Dict[str, Any], execution_mode: str = 'execute', runtime_options: Dict[str, Any] | None = None) -> Dict[str, Any]:
        blueprint = self.blueprints.get(blueprint_id)
        profile = self.profiles.get(profile_id)
        preflight = self.preflight_validator.validate(blueprint, profile, self._resolve_node_runtime)
        if preflight.status != 'passed':
            raise FarosPreflightError(preflight.message)

        steps = self.graph_builder.initial_step_states(blueprint)
        for step, node in zip(steps, blueprint.workflow):
            runtime_node = self._resolve_node_runtime(node, profile)
            step.agent_id = runtime_node['agentId']
            step.skill_ids = runtime_node['skillIds']
        runtime_options = self._normalize_runtime_options(runtime_options)
        run = self.state_store.create_run(
            blueprint_id=blueprint_id,
            profile_id=profile_id,
            execution_mode=execution_mode,
            inputs=inputs,
            steps=steps,
            preflight=preflight.model_dump(),
            runtime_options=runtime_options,
            checkpoint=self._build_checkpoint_payload(steps, runtime_options),
        )
        ResearchMemory(self.state_store, run['id'], policy=profile.memory_policy.model_dump())
        self._refresh_run_step_statuses(run['id'], blueprint)
        return self.state_store.get_run(run['id'])

    def execute_run(self, run_id: str) -> Dict[str, Any]:
        run = self.state_store.get_run(run_id)
        if not run:
            raise FarosNotFoundError(f"FAROS run '{run_id}' not found")

        blueprint = self.blueprints.get(run['blueprint_id'])
        profile = self.profiles.get(run['profile_id'])
        memory = ResearchMemory(self.state_store, run_id, policy=profile.memory_policy.model_dump())

        self._refresh_run_step_statuses(run_id, blueprint)
        self.state_store.update_run(
            run_id,
            {
                'status': 'running',
                'started_at': run.get('started_at') or utc_now_iso(),
                'error_message': None,
            },
        )

        try:
            while True:
                run = self.state_store.get_run(run_id)
                step_index = {step['node_id']: step for step in run.get('steps', [])}
                ready_ids = self.graph_builder.ready_node_ids(blueprint, step_index, policy=self._ready_node_policy(run))
                if not ready_ids:
                    if all(step.get('status') in {'completed', 'skipped'} for step in run.get('steps', [])):
                        final_memory_snapshot = memory.snapshot()
                        final_memory_data = final_memory_snapshot['data']
                        final_artifact_check = self._verify_run_output_contract(run_id, blueprint)
                        if final_artifact_check['status'] != 'passed':
                            raise FarosVerificationError(final_artifact_check['message'])
                        return self.state_store.update_run(
                            run_id,
                            {
                                'status': 'completed',
                                'ended_at': utc_now_iso(),
                                'output_summary': {
                                    'paperId': final_memory_data.get('paperId'),
                                    'reviewId': final_memory_data.get('reviewId'),
                                    'selectedCandidateId': final_memory_data.get('selectedCandidateId'),
                                    'memorySummary': final_memory_snapshot['summary'],
                                    'artifactContract': final_artifact_check,
                                },
                            },
                        )
                    raise FarosBlockedError('run is blocked with no ready steps; use skip or resume after resolving dependencies')

                node_id = ready_ids[0]
                node = next(item for item in blueprint.workflow if item.id == node_id)
                capability = self.capabilities.get(node.capability)
                runtime_node = self._resolve_node_runtime(node, profile, capability)
                self.state_store.update_step(
                    run_id,
                    node.id,
                    {
                        'status': 'running',
                        'agent_id': runtime_node['agentId'],
                        'skill_ids': runtime_node['skillIds'],
                        'started_at': utc_now_iso(),
                        'error': None,
                        'checkpoint': {'status': 'running', 'at': utc_now_iso()},
                    },
                )
                self.event_log.info(
                    run_id,
                    node.id,
                    f"Executing capability {node.capability}",
                    agentId=runtime_node['agentId'],
                    skillIds=runtime_node['skillIds'],
                )

                node_inputs: Dict[str, Any] = {}
                node_inputs.update(memory.data)
                node_inputs.update(node.inputs)

                context = ExecutionContext(
                    run_id=run_id,
                    blueprint_id=blueprint.id,
                    profile_id=profile.id,
                    node_id=node.id,
                    capability_id=node.capability,
                    agent_id=runtime_node['agentId'],
                    skill_ids=runtime_node['skillIds'],
                    provider_bindings=profile.capability_bindings,
                    memory=memory.snapshot(),
                    settings={
                        'blueprintName': blueprint.name,
                        'profileName': profile.name,
                        'agentRole': runtime_node['agentRole'],
                    },
                )
                agent_plan = AgentExecutionPlan(
                    agent=runtime_node['agent'],
                    skills=runtime_node['skills'],
                    capability=capability,
                    inputs=node_inputs,
                    context=context,
                )
                result = self.agent_executor.execute(agent_plan)
                expected_artifact_types = blueprint.artifact_schema.get(node.capability, [])
                artifact_schema_map = {artifact_type: self.artifacts.get(artifact_type) for artifact_type in expected_artifact_types}
                verifier_policy = self._verification_policy_for_node(blueprint, profile, node)
                verification = self.verifier.verify(
                    node.capability,
                    result,
                    verifier_ids=verifier_policy['verifier_ids'],
                    pack_ids=verifier_policy['pack_ids'],
                    disabled_verifier_ids=verifier_policy['disabled_verifier_ids'],
                    expected_artifact_types=expected_artifact_types,
                    artifact_schema_map=artifact_schema_map,
                    required_outputs=self._required_outputs_for_node(blueprint, node.capability),
                )
                if verification.status != 'passed':
                    raise FarosVerificationError(verification.message)

                outputs_summary = {key: value for key, value in result.outputs.items() if key not in {'ideaCandidates', 'actionItems'}}
                self.state_store.update_step(
                    run_id,
                    node.id,
                    {
                        'status': 'completed',
                        'agent_id': runtime_node['agentId'],
                        'skill_ids': runtime_node['skillIds'],
                        'ended_at': utc_now_iso(),
                        'outputs_summary': outputs_summary,
                        'verification': verification.model_dump(),
                        'error': None,
                        'checkpoint': {'status': 'completed', 'at': utc_now_iso(), 'outputKeys': sorted(result.outputs.keys())},
                    },
                )
                self.artifact_store.add(run_id, result.artifacts)
                memory.merge(result.outputs, scope=node.id)
                memory.record_step(node.id, result.outputs)
                memory.update('lastNodeId', node.id, scope='run')
                memory.compact()
                for event in result.events:
                    level = event.get('level', 'info')
                    message = event.get('message', f"{node.capability} event")
                    details = {k: v for k, v in event.items() if k not in {'level', 'message'}}
                    if level == 'error':
                        self.event_log.error(run_id, node.id, message, **details)
                    else:
                        self.event_log.info(run_id, node.id, message, **details)
                self.event_log.info(
                    run_id,
                    node.id,
                    f"{node.capability} completed",
                    verification=verification.model_dump(),
                    agentId=runtime_node['agentId'],
                    skillIds=runtime_node['skillIds'],
                )
                self._refresh_run_step_statuses(run_id, blueprint)
                self._update_run_checkpoint(run_id)
        except Exception as exc:
            run = self.state_store.get_run(run_id)
            if run and any(step.get('status') == 'running' for step in run.get('steps', [])):
                for step in run.get('steps', []):
                    if step.get('status') == 'running':
                        self.state_store.update_step(
                            run_id,
                            step['node_id'],
                            {
                                'status': 'failed',
                                'ended_at': utc_now_iso(),
                                'error': str(exc),
                                'checkpoint': {'status': 'failed', 'at': utc_now_iso(), 'error': str(exc)},
                            },
                        )
                        break
            self.event_log.error(run_id, 'runtime', 'FAROS run failed', error=str(exc))
            self._update_run_checkpoint(run_id)
            return self.state_store.update_run(
                run_id,
                {
                    'status': 'failed',
                    'ended_at': utc_now_iso(),
                    'error_message': str(exc),
                },
            )

    def resume_run(self, run_id: str) -> Dict[str, Any]:
        run = self.state_store.get_run(run_id)
        if not run:
            raise FarosNotFoundError(f"FAROS run '{run_id}' not found")
        if run.get('status') == 'completed':
            raise ValueError(f"FAROS run '{run_id}' is already completed")
        blueprint = self.blueprints.get(run['blueprint_id'])
        for step in run.get('steps', []):
            if step.get('status') == 'failed':
                step['status'] = 'ready'
                step['error'] = None
                step['ended_at'] = None
                step['checkpoint'] = {'status': 'retrying', 'at': utc_now_iso(), 'retryCount': step.get('retry_count', 0)}
        self.state_store.update_run(run_id, {'steps': run.get('steps', []), 'status': 'pending', 'error_message': None})
        self._refresh_run_step_statuses(run_id, blueprint)
        self._update_run_checkpoint(run_id, action={'type': 'resume', 'at': utc_now_iso()})
        return self.execute_run(run_id)

    def skip_step(self, run_id: str, node_id: str) -> Dict[str, Any]:
        run = self.state_store.get_run(run_id)
        if not run:
            raise FarosNotFoundError(f"FAROS run '{run_id}' not found")
        blueprint = self.blueprints.get(run['blueprint_id'])
        step_index = {step['node_id']: step for step in run.get('steps', [])}
        if node_id not in step_index:
            raise ValueError(f"Node '{node_id}' not found in FAROS run '{run_id}'")
        step = step_index[node_id]
        if step.get('status') in {'completed', 'running'}:
            raise FarosBlockedError(f"Cannot skip node '{node_id}' while it is {step.get('status')}")
        step.update(
            {
                'status': 'skipped',
                'ended_at': utc_now_iso(),
                'error': None,
                'verification': {'status': 'skipped', 'message': 'step skipped by operator'},
            }
        )
        self.state_store.update_run(run_id, {'steps': run.get('steps', [])})
        self._refresh_run_step_statuses(run_id, blueprint)
        self.event_log.info(run_id, node_id, 'step skipped by operator')
        self._update_run_checkpoint(run_id, action={'type': 'skip', 'nodeId': node_id, 'at': utc_now_iso()})
        return self.state_store.get_run(run_id)

    def retry_step(self, run_id: str, node_id: str) -> Dict[str, Any]:
        run = self.state_store.get_run(run_id)
        if not run:
            raise FarosNotFoundError(f"FAROS run '{run_id}' not found")
        blueprint = self.blueprints.get(run['blueprint_id'])
        step_index = {step['node_id']: step for step in run.get('steps', [])}
        if node_id not in step_index:
            raise ValueError(f"Node '{node_id}' not found in FAROS run '{run_id}'")
        step = step_index[node_id]
        if step.get('status') not in {'failed', 'blocked', 'skipped'}:
            raise FarosBlockedError(f"Cannot retry node '{node_id}' while it is {step.get('status')}")
        step.update({
            'status': 'pending',
            'error': None,
            'started_at': None,
            'ended_at': None,
            'retry_count': int(step.get('retry_count', 0) or 0) + 1,
            'checkpoint': {'status': 'retry-requested', 'at': utc_now_iso(), 'retryCount': int(step.get('retry_count', 0) or 0) + 1},
        })
        self.state_store.update_run(run_id, {'steps': run.get('steps', []), 'status': 'pending', 'error_message': None})
        self._refresh_run_step_statuses(run_id, blueprint)
        self._update_run_checkpoint(run_id, action={'type': 'retry', 'nodeId': node_id, 'retryCount': step.get('retry_count', 0), 'at': utc_now_iso()})
        self.event_log.info(run_id, node_id, 'step retry requested by operator', retryCount=step.get('retry_count', 0))
        return self.state_store.get_run(run_id)

    def replay_step(self, run_id: str, node_id: str, include_downstream: bool = True) -> Dict[str, Any]:
        run = self.state_store.get_run(run_id)
        if not run:
            raise FarosNotFoundError(f"FAROS run '{run_id}' not found")
        if run.get('status') == 'running':
            raise FarosBlockedError(f"Cannot replay node '{node_id}' while FAROS run '{run_id}' is running")
        blueprint = self.blueprints.get(run['blueprint_id'])
        step_index = {step['node_id']: step for step in run.get('steps', [])}
        if node_id not in step_index:
            raise ValueError(f"Node '{node_id}' not found in FAROS run '{run_id}'")
        source_step = step_index[node_id]
        if source_step.get('status') not in {'completed', 'failed', 'skipped'}:
            raise FarosBlockedError(f"Cannot replay node '{node_id}' while it is {source_step.get('status')}")
        replay_targets = {node_id}
        if include_downstream:
            replay_targets.update(self._downstream_node_ids(blueprint, node_id))
        replayed_at = utc_now_iso()
        for target_id in replay_targets:
            step = step_index[target_id]
            step.update({
                'status': 'pending',
                'error': None,
                'started_at': None,
                'ended_at': None,
                'outputs_summary': {},
                'verification': {},
                'checkpoint': {
                    'status': 'replay-requested',
                    'at': replayed_at,
                    'sourceNodeId': node_id,
                    'scope': _replay_scope(include_downstream),
                    'replayTargetIds': sorted(replay_targets),
                },
            })
        self.state_store.update_run(run_id, {'steps': list(step_index.values()), 'status': 'pending', 'error_message': None})
        self._refresh_run_step_statuses(run_id, blueprint)
        self._update_run_checkpoint(run_id, action={
            'type': 'replay',
            'nodeId': node_id,
            'scope': _replay_scope(include_downstream),
            'targetIds': sorted(replay_targets),
            'at': replayed_at,
        })
        self.event_log.info(run_id, node_id, 'step replay requested by operator', replayTargets=sorted(replay_targets), includeDownstream=include_downstream)
        return self.state_store.get_run(run_id)

    def _refresh_run_step_statuses(self, run_id: str, blueprint: Blueprint) -> Dict[str, Any]:
        run = self.state_store.get_run(run_id)
        if not run:
            raise FarosNotFoundError(f"FAROS run '{run_id}' not found")
        step_index = {step['node_id']: step for step in run.get('steps', [])}
        self.graph_builder.refresh_step_statuses(blueprint, step_index)
        return self.state_store.update_run(run_id, {'steps': list(step_index.values())})

    def _resolve_node_runtime(self, node: WorkflowNode, profile: Profile, capability=None) -> Dict[str, Any]:
        capability = capability or self.capabilities.get(node.capability)
        agent_id = node.agent or getattr(capability, 'default_agent_id', '') or None
        if agent_id is None:
            raise FarosConfigurationError(f"Workflow node '{node.id}' has no agent binding")
        agent = self.agents.get(agent_id)

        profile_binding = profile.agent_bindings.get(agent_id)
        skill_ids = list(node.skills)
        if not skill_ids and profile_binding and profile_binding.skill_overrides:
            skill_ids = list(profile_binding.skill_overrides)
        if not skill_ids and agent_id in profile.skill_defaults:
            skill_ids = list(profile.skill_defaults[agent_id])
        if not skill_ids and getattr(capability, 'default_skill_ids', None):
            skill_ids = list(capability.default_skill_ids)
        if not skill_ids:
            skill_ids = list(agent.default_skills)

        skills = [self.skills.get(skill_id) for skill_id in skill_ids]
        return {
            'agentId': agent.id,
            'agentRole': agent.role,
            'skillIds': skill_ids,
            'agent': agent,
            'skills': skills,
        }

    def _verify_run_output_contract(self, run_id: str, blueprint: Blueprint) -> Dict[str, Any]:
        final_required = list((blueprint.output_contract or {}).get('finalArtifacts', []) or [])
        produced = [artifact['type'] for artifact in self.state_store.list_artifacts(run_id)]
        missing = [artifact_type for artifact_type in final_required if artifact_type not in produced]
        if missing:
            return {
                'status': 'failed',
                'message': f"run output contract missing final artifacts: {missing}",
                'required': final_required,
                'produced': produced,
                'missing': missing,
            }
        return {
            'status': 'passed',
            'message': 'run output contract satisfied',
            'required': final_required,
            'produced': produced,
            'missing': [],
        }

    def _verification_policy_for_node(self, blueprint: Blueprint, profile: Profile, node: WorkflowNode) -> dict[str, list[str]]:
        verifier_ids: list[str] = []
        pack_ids: list[str] = []
        disabled_verifier_ids: list[str] = []

        profile_policy = profile.verification_policy or {}
        if isinstance(profile_policy, dict):
            pack_ids.extend(profile_policy.get('packs', []) or [])
            verifier_ids.extend(profile_policy.get('verifiers', []) or [])
            disabled_verifier_ids.extend(profile_policy.get('disable_verifiers', []) or [])

        if node.verifier:
            verifier_ids.append(node.verifier)

        for rule in blueprint.verification_rules:
            if rule.get('capability') != node.capability:
                continue
            pack_ids.extend(rule.get('packs', []) or [])
            verifier_ids.extend(rule.get('verifiers', []) or [])
            disabled_verifier_ids.extend(rule.get('disable_verifiers', []) or [])

        return {
            'pack_ids': pack_ids,
            'verifier_ids': verifier_ids,
            'disabled_verifier_ids': disabled_verifier_ids,
        }

    def _required_outputs_for_node(self, blueprint: Blueprint, capability_id: str) -> list[str]:
        for rule in blueprint.verification_rules:
            if rule.get('capability') == capability_id:
                return list(rule.get('requires', []))
        return []

    def _normalize_runtime_options(self, runtime_options: Dict[str, Any] | None) -> Dict[str, Any]:
        normalized = dict(runtime_options or {})
        policy = normalized.get('readyNodePolicy', 'fifo')
        if policy not in {'fifo', 'reverse', 'wave'}:
            policy = 'fifo'
        normalized['readyNodePolicy'] = policy
        return normalized

    def _ready_node_policy(self, run: Dict[str, Any]) -> str:
        return (run.get('runtime_options') or {}).get('readyNodePolicy', 'fifo')

    def _build_checkpoint_payload(self, steps: list[Dict[str, Any]] | list, runtime_options: Dict[str, Any], action: Dict[str, Any] | None = None) -> Dict[str, Any]:
        step_items = [step.model_dump() if hasattr(step, 'model_dump') else dict(step) for step in steps]
        return {
            'readyNodePolicy': runtime_options.get('readyNodePolicy', 'fifo'),
            'readyNodeIds': [step['node_id'] for step in step_items if step.get('status') == 'ready'],
            'completedNodeIds': [step['node_id'] for step in step_items if step.get('status') == 'completed'],
            'failedNodeIds': [step['node_id'] for step in step_items if step.get('status') == 'failed'],
            'skippedNodeIds': [step['node_id'] for step in step_items if step.get('status') == 'skipped'],
            'replayableNodeIds': [step['node_id'] for step in step_items if step.get('status') in {'completed', 'failed', 'skipped'}],
            'stepCheckpoints': {step['node_id']: dict(step.get('checkpoint', {})) for step in step_items if step.get('checkpoint')},
            'lastOperatorAction': dict(action or {}),
            'updatedAt': utc_now_iso(),
        }

    def _update_run_checkpoint(self, run_id: str, action: Dict[str, Any] | None = None) -> None:
        run = self.state_store.get_run(run_id)
        if not run:
            return
        existing = run.get('checkpoint') or {}
        checkpoint = self._build_checkpoint_payload(
            run.get('steps', []),
            self._normalize_runtime_options(run.get('runtime_options')),
            action=action or existing.get('lastOperatorAction'),
        )
        self.state_store.update_run(run_id, {'checkpoint': checkpoint})

    def _downstream_node_ids(self, blueprint: Blueprint, node_id: str) -> list[str]:
        dependencies = self.graph_builder.dependency_map(blueprint)
        visited: set[str] = set()
        stack = list(dependencies.get(node_id, {}).get('downstream', []))
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            stack.extend(dependencies.get(current, {}).get('downstream', []))
        return sorted(visited)

    def list_runs(self) -> list[dict]:
        return self.state_store.list_runs()

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        return self.state_store.get_run(run_id)

    def get_run_detail(self, run_id: str) -> Optional[Dict[str, Any]]:
        run = self.state_store.get_run(run_id)
        if not run:
            return None

        blueprint = self.blueprints.get(run['blueprint_id'])
        profile = self.profiles.get(run['profile_id'])
        events = self.state_store.list_events(run_id)
        artifacts = self.state_store.list_artifacts(run_id)
        memory_snapshot = ResearchMemory(self.state_store, run_id, policy=profile.memory_policy.model_dump()).snapshot()

        step_index = {step['node_id']: step for step in run.get('steps', [])}
        dependencies = self.graph_builder.dependency_map(blueprint)

        workflow = []
        timeline = []
        verification_counts = {'passed': 0, 'failed': 0, 'skipped': 0, 'unknown': 0}
        status_counts: Dict[str, int] = {}
        for node in blueprint.workflow:
            step = step_index.get(node.id, {})
            verification = step.get('verification', {})
            verification_status = verification.get('status', 'unknown') or 'unknown'
            verification_counts[verification_status] = verification_counts.get(verification_status, 0) + 1
            step_status = step.get('status', 'pending')
            status_counts[step_status] = status_counts.get(step_status, 0) + 1
            node_view = {
                'nodeId': node.id,
                'name': node.name,
                'capability': node.capability,
                'agentId': step.get('agent_id') or node.agent,
                'skillIds': step.get('skill_ids', node.skills),
                'status': step_status,
                'startedAt': step.get('started_at'),
                'endedAt': step.get('ended_at'),
                'outputsSummary': step.get('outputs_summary', {}),
                'verification': verification,
                'verificationStatus': verification_status,
                'dependencies': {
                    'upstream': sorted(dependencies.get(node.id, {}).get('upstream', [])),
                    'downstream': sorted(dependencies.get(node.id, {}).get('downstream', [])),
                },
                'error': step.get('error'),
            }
            workflow.append(node_view)
            timeline.append({
                'nodeId': node.id,
                'status': node_view['status'],
                'startedAt': node_view['startedAt'],
                'endedAt': node_view['endedAt'],
                'verificationStatus': verification_status,
            })

        preflight = run.get('preflight', {})
        dependency_summary = {
            'edgeCount': len(blueprint.edges),
            'rootNodes': sorted([node.id for node in blueprint.workflow if not dependencies.get(node.id, {}).get('upstream')]),
            'leafNodes': sorted([node.id for node in blueprint.workflow if not dependencies.get(node.id, {}).get('downstream')]),
        }

        verification_summary = {
            'status': 'failed' if verification_counts.get('failed', 0) else 'passed',
            'counts': verification_counts,
            'preflightStatus': preflight.get('status', 'unknown'),
        }

        return {
            'run': run,
            'runtime': {
                'blueprintId': blueprint.id,
                'blueprintName': blueprint.name,
                'profileId': profile.id,
                'profileName': profile.name,
                'status': run['status'],
                'executionMode': run['execution_mode'],
                'readyNodePolicy': self._ready_node_policy(run),
                'stepCount': len(run.get('steps', [])),
                'eventCount': len(events),
                'artifactCount': len(artifacts),
                'memoryKeys': sorted(memory_snapshot['data'].keys()),
                'memorySummaryKeys': sorted(memory_snapshot['summary'].keys()),
                'memoryHistoryCount': len(memory_snapshot['history']),
                'memoryArchiveScopes': sorted(memory_snapshot['archives'].keys()),
                'memoryPolicy': memory_snapshot['policy'],
                'stepStatusCounts': status_counts,
            },
            'workflow': workflow,
            'timeline': timeline,
            'dependencySummary': dependency_summary,
            'verificationSummary': verification_summary,
            'checkpoint': run.get('checkpoint', {}),
            'preflight': preflight,
            'events': events,
            'artifacts': artifacts,
            'memory': memory_snapshot,
        }

    def get_run_memory(self, run_id: str) -> Optional[Dict[str, Any]]:
        run = self.state_store.get_run(run_id)
        if not run:
            return None
        profile = self.profiles.get(run['profile_id'])
        return ResearchMemory(self.state_store, run_id, policy=profile.memory_policy.model_dump()).snapshot()

    def query_run_memory(
        self,
        run_id: str,
        *,
        keys: list[str] | None = None,
        scopes: list[str] | None = None,
        include_archives: bool = False,
        include_summary: bool = True,
        include_history: bool = False,
        history_limit: int | None = None,
    ) -> Optional[Dict[str, Any]]:
        run = self.state_store.get_run(run_id)
        if not run:
            return None
        profile = self.profiles.get(run['profile_id'])
        memory = ResearchMemory(self.state_store, run_id, policy=profile.memory_policy.model_dump())
        return memory.query(
            keys=keys,
            scopes=scopes,
            include_archives=include_archives,
            include_summary=include_summary,
            include_history=include_history,
            history_limit=history_limit,
        )

    def recall_run_memory(self, run_id: str, scope: str, *, merge_into_run: bool = False) -> Optional[Dict[str, Any]]:
        run = self.state_store.get_run(run_id)
        if not run:
            return None
        profile = self.profiles.get(run['profile_id'])
        memory = ResearchMemory(self.state_store, run_id, policy=profile.memory_policy.model_dump())
        return memory.recall(scope, merge_into_run=merge_into_run)

    def list_events(self, run_id: str) -> list[dict]:
        return self.state_store.list_events(run_id)

    def list_artifacts(self, run_id: str) -> list[dict]:
        return self.state_store.list_artifacts(run_id)


_orchestrator: FarosOrchestrator | None = None


def get_orchestrator() -> FarosOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = FarosOrchestrator()
    return _orchestrator
