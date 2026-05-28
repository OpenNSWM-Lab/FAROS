import hashlib
import hmac
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

pytest.importorskip('sqlmodel')

from fastapi.testclient import TestClient

from app.main import app
from app.faros.loaders.blueprint_loader import BlueprintLoader
from app.faros.registry.blueprint_registry import BlueprintRegistry
from app.faros.loaders.profile_loader import ProfileLoader
from app.faros.loaders.skill_loader import SkillLoader
from app.faros.registry.skill_registry import SkillRegistry, get_skill_registry
from app.faros.runtime.agent_executor import AgentExecutor, AgentExecutionPlan
from app.faros.registry.agent_registry import AgentRegistry, get_agent_registry
from app.faros.registry.package_compatibility import build_dependency_report, validate_dependency_graph
from app.faros.registry.capability_registry import get_capability_registry
from app.faros.registry.profile_registry import get_profile_registry
from app.faros.models.artifact import ArtifactRecord
from app.faros.models.agent import AgentSpec
from app.faros.models.blueprint import Blueprint
from app.faros.models.capability import CapabilityResult
from app.faros.models.execution import ExecutionContext
from app.faros.models.skill import SkillSpec
from app.faros.memory.research_memory import ResearchMemory
from app.faros.runtime.package_audit import PackageAuditStore
from app.faros.runtime.state_store import FarosStateStore
from app.faros.models.execution import StepState
from app.faros.runtime.orchestrator import get_orchestrator
from app.faros.verification.rules import VerifierDispatcher

client = TestClient(app)


def test_faros_routes_are_mounted():
    paths = {route.path for route in app.routes}
    expected_prefixes = [
        '/api/faros/health',
        '/api/faros/blueprints',
        '/api/faros/blueprints/validate',
        '/api/faros/blueprints/install',
        '/api/faros/blueprints/refresh',
        '/api/faros/blueprints/uninstall',
        '/api/faros/blueprints/rollback',
        '/api/faros/profiles',
        '/api/faros/agents',
        '/api/faros/agents/validate',
        '/api/faros/agents/install',
        '/api/faros/agents/refresh',
        '/api/faros/agents/uninstall',
        '/api/faros/agents/rollback',
        '/api/faros/skills',
        '/api/faros/skills/validate',
        '/api/faros/skills/install',
        '/api/faros/skills/refresh',
        '/api/faros/skills/uninstall',
        '/api/faros/skills/rollback',
        '/api/faros/providers',
        '/api/faros/verifiers',
        '/api/faros/verifiers/validate',
        '/api/faros/verifiers/install',
        '/api/faros/verifiers/refresh',
        '/api/faros/verifiers/uninstall',
        '/api/faros/verifiers/rollback',
        '/api/faros/packages/trust/validate',
        '/api/faros/packages/dependencies',
        '/api/faros/packages/dependencies/resolve',
        '/api/faros/packages/audit',
        '/api/faros/artifacts/schemas',
        '/api/faros/capabilities',
        '/api/faros/preflight',
        '/api/faros/runs',
        '/api/faros/runs/{run_id}/resume',
        '/api/faros/runs/{run_id}/steps/{node_id}/skip',
        '/api/faros/runs/{run_id}/steps/{node_id}/retry',
        '/api/faros/runs/{run_id}/steps/{node_id}/replay',
        '/api/faros/runs/{run_id}/detail',
        '/api/faros/runs/{run_id}/memory',
        '/api/faros/runs/{run_id}/memory/query',
        '/api/faros/runs/{run_id}/memory/recall/{scope}',
    ]
    for prefix in expected_prefixes:
        assert any(path == prefix or path.startswith(prefix + '/') for path in paths), prefix


def test_faros_metadata_endpoints_return_assets():
    health = client.get('/api/faros/health')
    assert health.status_code == 200
    assert health.json()['runtime'] == 'faros'
    assert health.json()['agents'] >= 4
    assert health.json()['skills'] >= 8

    blueprints = client.get('/api/faros/blueprints')
    assert blueprints.status_code == 200
    blueprint = next(item for item in blueprints.json()['blueprints'] if item['id'] == 'ml_paper')
    assert len(blueprint['workflow']) == 4
    assert blueprint['workflow'][0]['agent'] == 'researcher'
    assert blueprint['workflow'][0]['skills']

    profiles = client.get('/api/faros/profiles')
    assert profiles.status_code == 200
    profile_ids = {item['id'] for item in profiles.json()['profiles']}
    assert 'faros_llm' in profile_ids
    assert 'faros_hybrid' in profile_ids

    agents = client.get('/api/faros/agents')
    assert agents.status_code == 200
    assert any(item['id'] == 'writer' for item in agents.json()['agents'])

    skills = client.get('/api/faros/skills')
    assert skills.status_code == 200
    assert any(item['id'] == 'latex-assembly' for item in skills.json()['skills'])
    assert all(item['manifest_path'] for item in skills.json()['skills'])

    verifiers = client.get('/api/faros/verifiers')
    assert verifiers.status_code == 200
    verifier_ids = {item['id'] for item in verifiers.json()['verifiers']}
    assert 'status' in verifier_ids
    assert 'review_action_items' in verifier_ids
    assert 'runtime_baseline' in verifiers.json()['packs']
    pack_metadata = {item['id']: item for item in verifiers.json()['packMetadata']}
    assert pack_metadata['runtime_baseline']['provider_types'] == ['llm', 'tool', 'execution', 'human']
    assert pack_metadata['review_quality']['capability_ids'] == ['reviewer_simulation']
    assert verifiers.json()['packages'] == []






def test_faros_package_trust_validation_endpoint_reports_untrusted_package(tmp_path: Path):
    package_dir = tmp_path / 'untrusted-skill'
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / 'skill.json').write_text(json.dumps({
        'id': 'untrusted-skill',
        'name': 'Untrusted Skill',
        'version': '0.1.0',
        'manifest_version': '1.0',
        'kind': 'runtime',
        'description': 'trust endpoint test',
        'agent_roles': ['researcher'],
        'tags': ['test'],
        'artifact_types': ['run_manifest'],
        'verification_hooks': ['schema-check'],
        'provider_requirements': {'storage': 'local'},
    }, indent=2))
    (package_dir / 'README.md').write_text('# Untrusted Skill\n')
    response = client.post('/api/faros/packages/trust/validate', json={'packageType': 'skill', 'sourcePath': str(package_dir)})
    assert response.status_code == 200
    payload = response.json()['trust']
    assert payload['trusted'] is False
    assert payload['policyStatus'] == 'untrusted'
    assert 'missing TRUST.json' in payload['reasons']



def test_faros_package_trust_validation_endpoint_verifies_integrity(tmp_path: Path):
    package_dir = tmp_path / 'trusted-skill'
    package_dir.mkdir(parents=True, exist_ok=True)
    skill_payload = {
        'id': 'trusted-skill',
        'name': 'Trusted Skill',
        'version': '0.1.0',
        'manifest_version': '1.0',
        'kind': 'runtime',
        'description': 'trust integrity test',
        'agent_roles': ['researcher'],
        'tags': ['test'],
        'artifact_types': ['run_manifest'],
        'verification_hooks': ['schema-check'],
        'provider_requirements': {'storage': 'local'},
    }
    readme = '# Trusted Skill\n'
    (package_dir / 'skill.json').write_text(json.dumps(skill_payload, indent=2))
    (package_dir / 'README.md').write_text(readme)
    trust_payload = {
        'publisher': 'faros-test',
        'source': 'local-fixture',
        'trustLevel': 'verified',
        'signedBy': 'test-suite',
        'integrity': {
            'algorithm': 'sha256',
            'files': {
                'skill.json': hashlib.sha256((package_dir / 'skill.json').read_bytes()).hexdigest(),
                'README.md': hashlib.sha256((package_dir / 'README.md').read_bytes()).hexdigest(),
            },
        },
    }
    (package_dir / 'TRUST.json').write_text(json.dumps(trust_payload, indent=2))
    response = client.post('/api/faros/packages/trust/validate', json={'packageType': 'skill', 'sourcePath': str(package_dir)})
    assert response.status_code == 200
    payload = response.json()['trust']
    assert payload['trusted'] is True
    assert payload['policyStatus'] == 'trusted'
    assert payload['integrityStatus'] == 'verified'
    assert payload['integrityCheckedFiles'] == ['README.md', 'skill.json']



def test_faros_package_trust_validation_endpoint_verifies_signature(tmp_path: Path):
    package_dir = tmp_path / 'signed-skill'
    package_dir.mkdir(parents=True, exist_ok=True)
    skill_payload = {
        'id': 'signed-skill',
        'name': 'Signed Skill',
        'version': '0.1.0',
        'manifest_version': '1.0',
        'kind': 'runtime',
        'description': 'signature trust test',
        'agent_roles': ['researcher'],
        'tags': ['test'],
        'artifact_types': ['run_manifest'],
        'verification_hooks': ['schema-check'],
        'provider_requirements': {'storage': 'local'},
    }
    (package_dir / 'skill.json').write_text(json.dumps(skill_payload, indent=2))
    (package_dir / 'README.md').write_text('# Signed Skill\n')

    keyring_path = Path(__file__).resolve().parents[1] / 'data' / 'faros' / 'packages' / 'trust_keyring.json'
    keyring_path.parent.mkdir(parents=True, exist_ok=True)
    keyring_path.write_text(json.dumps({'faros-test-signer': {'secret': 'faros-test-secret'}}, indent=2))

    trust_payload = {
        'publisher': 'faros-test',
        'source': 'local-fixture',
        'trustLevel': 'verified',
        'signedBy': 'faros-test-signer',
        'integrity': {
            'algorithm': 'sha256',
            'files': {
                'skill.json': hashlib.sha256((package_dir / 'skill.json').read_bytes()).hexdigest(),
                'README.md': hashlib.sha256((package_dir / 'README.md').read_bytes()).hexdigest(),
            },
        },
    }
    canonical = json.dumps(trust_payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    signature = hmac.new(b'faros-test-secret', canonical, hashlib.sha256).hexdigest()
    trust_payload['signature'] = {'algorithm': 'hmac-sha256', 'signedBy': 'faros-test-signer', 'value': signature}
    (package_dir / 'TRUST.json').write_text(json.dumps(trust_payload, indent=2))

    response = client.post('/api/faros/packages/trust/validate', json={'packageType': 'skill', 'sourcePath': str(package_dir)})
    assert response.status_code == 200
    payload = response.json()['trust']
    assert payload['trusted'] is True
    assert payload['signatureStatus'] == 'verified'
    assert payload['signatureSigner'] == 'faros-test-signer'
    assert payload['keyringMatched'] is True


def test_skill_registry_rejects_integrity_mismatch_when_trust_is_enforced(tmp_path: Path):
    skills_root = tmp_path / 'installed_skills'
    registry = SkillRegistry(skills_root)

    package_dir = tmp_path / 'tampered-skill'
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / 'skill.json').write_text(json.dumps({
        'id': 'tampered-skill',
        'name': 'Tampered Skill',
        'version': '0.1.0',
        'manifest_version': '1.0',
        'kind': 'runtime',
        'description': 'tampered trust test',
        'agent_roles': ['researcher'],
        'tags': ['test'],
        'artifact_types': ['run_manifest'],
        'verification_hooks': ['schema-check'],
        'provider_requirements': {'storage': 'local'},
    }, indent=2))
    (package_dir / 'README.md').write_text('# Tampered Skill\n')
    (package_dir / 'TRUST.json').write_text(json.dumps({
        'publisher': 'faros-test',
        'source': 'local-fixture',
        'trustLevel': 'verified',
        'signedBy': 'test-suite',
        'integrity': {
            'algorithm': 'sha256',
            'files': {
                'skill.json': '0' * 64,
                'README.md': hashlib.sha256((package_dir / 'README.md').read_bytes()).hexdigest(),
            },
        },
    }, indent=2))

    with pytest.raises(ValueError, match='not trusted'):
        registry.install_package(package_dir, allow_untrusted=False)



def test_faros_install_skill_endpoint_rejects_untrusted_package_with_structured_error(tmp_path: Path):
    package_dir = tmp_path / 'untrusted-endpoint-skill'
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / 'skill.json').write_text(json.dumps({
        'id': 'untrusted-endpoint-skill',
        'name': 'Untrusted Endpoint Skill',
        'version': '0.1.0',
        'manifest_version': '1.0',
        'kind': 'runtime',
        'description': 'endpoint trust test',
        'agent_roles': ['researcher'],
        'tags': ['test'],
        'artifact_types': ['run_manifest'],
        'verification_hooks': ['schema-check'],
        'provider_requirements': {'storage': 'local'},
    }, indent=2))
    (package_dir / 'README.md').write_text('# Untrusted Endpoint Skill\n')

    response = client.post('/api/faros/skills/install', json={
        'sourcePath': str(package_dir),
        'allowUntrusted': False,
    })
    assert response.status_code == 403
    detail = response.json()['detail']
    assert detail['errorCode'] == 'trust_error'
    assert detail['category'] == 'trust'
    assert 'not trusted' in detail['message']


def test_faros_package_dependencies_endpoint_returns_graph_summary():
    response = client.get('/api/faros/packages/dependencies')
    assert response.status_code == 200
    payload = response.json()
    assert payload['status'] == 'passed'
    assert payload['counts']['packages'] >= 4
    assert 'skills' in payload['packages']
    assert 'edges' in payload
    assert 'issues' in payload
    assert 'resolution' in payload
    assert 'solution' in payload
    assert 'targets' in payload['resolution']
    assert 'conflicts' in payload['resolution']
    assert payload['solution']['status'] in {'solved', 'unsatisfied'}



def test_faros_package_dependency_resolution_endpoint_returns_resolution_summary():
    response = client.get('/api/faros/packages/dependencies/resolve')
    assert response.status_code == 200
    payload = response.json()
    assert 'resolution' in payload
    assert 'solution' in payload
    assert 'counts' in payload['resolution']
    assert 'targets' in payload['resolution']
    assert 'selection' in payload['solution']


def test_faros_artifact_schema_endpoint_returns_contracts():
    response = client.get('/api/faros/artifacts/schemas')
    assert response.status_code == 200
    payload = response.json()
    paper_record = next(item for item in payload['schemas'] if item['type'] == 'paper_record')
    assert 'paperId' in paper_record['required_metadata']
    assert paper_record['required_producer'] == 'paper_drafting'

def test_faros_providers_endpoint_returns_metadata():
    response = client.get('/api/faros/providers')
    assert response.status_code == 200
    payload = response.json()
    provider_types = {item['type'] for item in payload['providers']}
    assert {'llm', 'tool', 'execution', 'human'}.issubset(provider_types)

    llm = next(item for item in payload['providers'] if item['type'] == 'llm')
    tool = next(item for item in payload['providers'] if item['type'] == 'tool')
    execution = next(item for item in payload['providers'] if item['type'] == 'execution')
    human = next(item for item in payload['providers'] if item['type'] == 'human')

    assert 'reasoning' in llm['supported_capabilities']
    assert llm['supported_provider_ids']
    assert 'local-toolbox' in tool['supported_provider_ids']
    assert 'local-executor' in execution['supported_provider_ids']
    assert 'manual-review' in human['supported_provider_ids']

    health_types = {item['type'] for item in payload['health']}
    assert {'llm', 'tool', 'execution', 'human'}.issubset(health_types)

def test_skill_registry_loads_disk_manifests():
    registry = get_skill_registry()
    skill = registry.get('literature-grounding')
    assert skill.kind == 'retrieval'
    assert skill.manifest_version == '1.0'
    assert skill.manifest_path.endswith('literature-grounding/skill.json')


def test_agent_executor_uses_tool_provider_owned_execution_for_paper():
    capability = get_capability_registry().get('paper_drafting')
    agent = get_agent_registry().get('writer')
    skills = [get_skill_registry().get('paper-outline'), get_skill_registry().get('section-drafting'), get_skill_registry().get('latex-assembly')]
    executor = AgentExecutor()
    plan = AgentExecutionPlan(
        agent=agent,
        skills=skills,
        capability=capability,
        inputs={'seedQuery': 'provider owned paper', 'paperType': 'system', 'targetVenue': 'generic'},
        context=ExecutionContext(
            run_id='tool-run',
            blueprint_id='ml_paper',
            profile_id='faros_hybrid',
            node_id='paper',
            capability_id='paper_drafting',
            agent_id='writer',
            skill_ids=['paper-outline', 'section-drafting', 'latex-assembly'],
            provider_bindings={'paper_drafting': {'provider_type': 'tool', 'provider': 'local-toolbox', 'options': {}}},
            memory={},
            settings={},
        ),
    )
    result = executor.execute(plan)
    assert result.outputs['paperStatus'] == 'prepared'
    artifact_types = {artifact.type for artifact in result.artifacts}
    assert {'paper_record', 'latex_project', 'latex_zip', 'paper_pdf'}.issubset(artifact_types)
    assert result.verification['providerType'] == 'tool'


def test_agent_executor_uses_execution_file_backend_for_experiment(tmp_path: Path):
    response_path = tmp_path / 'execution_backend.json'
    response_path.write_text(json.dumps({
        'ok': True,
        'provider': 'local-executor',
        'model': 'external-execution-file',
        'text': 'external execution backend completed',
        'payload': {
            'projectTitle': 'External Experiment [python]',
            'files': [
                {'path': 'README.md', 'content': '# External Experiment\n'},
                {'path': 'scripts/run.sh', 'content': '#!/usr/bin/env bash\necho external\n'},
            ],
            'experimentStatus': 'designed',
            'executionSpec': {'entrypoint': 'scripts/run.sh', 'framework': 'FastAPI', 'language': 'python'},
        },
    }))
    capability = get_capability_registry().get('experiment')
    agent = get_agent_registry().get('experimenter')
    skills = [get_skill_registry().get('experiment-scaffold'), get_skill_registry().get('artifact-packaging')]
    executor = AgentExecutor()
    plan = AgentExecutionPlan(
        agent=agent,
        skills=skills,
        capability=capability,
        inputs={'seedQuery': 'external execution experiment', 'paperType': 'system', 'framework': 'FastAPI', 'language': 'python'},
        context=ExecutionContext(
            run_id='execution-external-run',
            blueprint_id='ml_paper',
            profile_id='faros_hybrid',
            node_id='experiment',
            capability_id='experiment',
            agent_id='experimenter',
            skill_ids=['experiment-scaffold', 'artifact-packaging'],
            provider_bindings={'experiment': {'provider_type': 'execution', 'provider': 'local-executor', 'options': {'external': {'mode': 'file', 'responsePath': str(response_path)}}}},
            memory={},
            settings={},
        ),
    )
    result = executor.execute(plan)
    assert result.outputs['experimentStatus'] == 'designed'
    assert result.outputs['projectTitle'] == 'External Experiment [python]'
    assert result.events[0]['message'] == 'external execution backend completed'
    assert result.verification['providerType'] == 'execution'


def test_agent_executor_uses_tool_command_backend_for_paper(tmp_path: Path):
    script = tmp_path / 'tool_backend.py'
    latex_literal = repr("\\documentclass{article}\n\\begin{document}\nExternal tool backend\n\\end{document}\n")
    pdf_literal = repr("%PDF-1.4\n% external tool backend\n")
    script.write_text(f"""import json
import sys

task = json.loads(sys.stdin.read())
runtime = task['options'].get('_faros', {{}})
title = task['options'].get('title', 'External Tool Draft')
venue = task['options'].get('targetVenue', 'generic')
latex = {latex_literal}
payload = {{
    'ok': True,
    'provider': task['provider'],
    'model': 'external-tool-command',
    'text': f"external tool backend for {{runtime.get('runId', 'unknown')}}",
    'payload': {{
        'paperStatus': 'prepared',
        'title': title,
        'targetVenue': venue,
        'latexFiles': {{
            'main.tex': latex
        }},
        'pdfPlaceholder': {pdf_literal}
    }}
}}
print(json.dumps(payload))
""")
    capability = get_capability_registry().get('paper_drafting')
    agent = get_agent_registry().get('writer')
    skills = [get_skill_registry().get('paper-outline'), get_skill_registry().get('section-drafting'), get_skill_registry().get('latex-assembly')]
    executor = AgentExecutor()
    plan = AgentExecutionPlan(
        agent=agent,
        skills=skills,
        capability=capability,
        inputs={'seedQuery': 'external tool paper', 'paperType': 'system', 'targetVenue': 'iclr'},
        context=ExecutionContext(
            run_id='tool-external-run',
            blueprint_id='ml_paper',
            profile_id='faros_hybrid',
            node_id='paper',
            capability_id='paper_drafting',
            agent_id='writer',
            skill_ids=['paper-outline', 'section-drafting', 'latex-assembly'],
            provider_bindings={'paper_drafting': {'provider_type': 'tool', 'provider': 'local-toolbox', 'options': {'external': {'mode': 'command', 'command': [sys.executable, str(script)]}}}},
            memory={},
            settings={},
        ),
    )
    result = executor.execute(plan)
    assert result.outputs['paperStatus'] == 'prepared'
    assert result.outputs['paperVenue'] == 'iclr'
    assert result.events[0]['message'] == 'external tool backend for tool-external-run'
    assert result.verification['providerType'] == 'tool'


def test_agent_executor_uses_human_file_backend_for_review(tmp_path: Path):
    response_path = tmp_path / 'review_backend.json'
    response_path.write_text(json.dumps({
        'ok': True,
        'provider': 'manual-review',
        'model': 'external-human-file',
        'text': 'external human review completed',
        'payload': {
            'reviewStatus': 'completed',
            'scoreSuggestion': 8,
            'markdownReport': '# External Review\n\nLooks good.\n',
            'jsonReport': {'summary': 'External human review'},
            'actionItems': [{'title': 'Tighten evidence section', 'severity': 'major'}],
        },
    }))
    capability = get_capability_registry().get('reviewer_simulation')
    agent = get_agent_registry().get('reviewer')
    skills = [get_skill_registry().get('review-critique'), get_skill_registry().get('consistency-audit')]
    executor = AgentExecutor()
    plan = AgentExecutionPlan(
        agent=agent,
        skills=skills,
        capability=capability,
        inputs={'paperId': 'paper_file_backend'},
        context=ExecutionContext(
            run_id='human-external-run',
            blueprint_id='ml_paper',
            profile_id='faros_hybrid',
            node_id='review',
            capability_id='reviewer_simulation',
            agent_id='reviewer',
            skill_ids=['review-critique', 'consistency-audit'],
            provider_bindings={'reviewer_simulation': {'provider_type': 'human', 'provider': 'manual-review', 'options': {'external': {'mode': 'file', 'responsePath': str(response_path)}}}},
            memory={},
            settings={},
        ),
    )
    result = executor.execute(plan)
    assert result.outputs['reviewStatus'] == 'completed'
    assert result.outputs['scoreSuggestion'] == 8
    assert result.outputs['actionItemCount'] == 1
    assert result.events[0]['message'] == 'external human review completed'


def test_agent_executor_uses_execution_workspace_file_backend_for_experiment(tmp_path: Path):
    workspace = tmp_path / 'worker_workspace'
    workspace.mkdir(parents=True, exist_ok=True)
    response_path = workspace / 'response.json'
    response_path.write_text(json.dumps({
        'ok': True,
        'provider': 'local-executor',
        'model': 'workspace-execution-file',
        'text': 'workspace execution backend completed',
        'payload': {
            'projectTitle': 'Workspace Experiment [python]',
            'files': [{'path': 'README.md', 'content': '# Workspace Experiment\n'}],
            'experimentStatus': 'designed',
            'executionSpec': {'entrypoint': 'scripts/run.sh', 'framework': 'FastAPI', 'language': 'python'},
        },
    }))
    capability = get_capability_registry().get('experiment')
    agent = get_agent_registry().get('experimenter')
    skills = [get_skill_registry().get('experiment-scaffold'), get_skill_registry().get('artifact-packaging')]
    executor = AgentExecutor()
    plan = AgentExecutionPlan(
        agent=agent,
        skills=skills,
        capability=capability,
        inputs={'seedQuery': 'workspace execution experiment', 'paperType': 'system', 'framework': 'FastAPI', 'language': 'python'},
        context=ExecutionContext(
            run_id='execution-workspace-run',
            blueprint_id='ml_paper',
            profile_id='faros_hybrid',
            node_id='experiment',
            capability_id='experiment',
            agent_id='experimenter',
            skill_ids=['experiment-scaffold', 'artifact-packaging'],
            provider_bindings={'experiment': {'provider_type': 'execution', 'provider': 'local-executor', 'options': {'external': {'mode': 'workspace_file', 'workspaceRoot': str(workspace), 'requestPath': 'request.json', 'responsePath': 'response.json'}}}},
            memory={},
            settings={},
        ),
    )
    result = executor.execute(plan)
    assert result.outputs['projectTitle'] == 'Workspace Experiment [python]'
    assert result.events[0]['message'] == 'workspace execution backend completed'
    request_payload = json.loads((workspace / 'request.json').read_text())
    assert request_payload['capability_id'] == 'experiment'


def test_agent_executor_uses_human_approval_file_backend_for_review(tmp_path: Path):
    decision_path = tmp_path / 'approval.json'
    decision_path.write_text(json.dumps({
        'status': 'approved',
        'provider': 'manual-review',
        'model': 'approval-file-review',
        'text': 'approval granted by reviewer',
        'payload': {
            'reviewStatus': 'completed',
            'scoreSuggestion': 7,
            'markdownReport': '# Approved Review\n',
            'jsonReport': {'summary': 'approved externally'},
            'actionItems': [{'title': 'Recheck citations', 'severity': 'minor'}],
        },
    }))
    capability = get_capability_registry().get('reviewer_simulation')
    agent = get_agent_registry().get('reviewer')
    skills = [get_skill_registry().get('review-critique'), get_skill_registry().get('consistency-audit')]
    executor = AgentExecutor()
    plan = AgentExecutionPlan(
        agent=agent,
        skills=skills,
        capability=capability,
        inputs={'paperId': 'paper_approval_backend'},
        context=ExecutionContext(
            run_id='human-approval-run',
            blueprint_id='ml_paper',
            profile_id='faros_hybrid',
            node_id='review',
            capability_id='reviewer_simulation',
            agent_id='reviewer',
            skill_ids=['review-critique', 'consistency-audit'],
            provider_bindings={'reviewer_simulation': {'provider_type': 'human', 'provider': 'manual-review', 'options': {'external': {'mode': 'approval_file', 'decisionPath': str(decision_path)}}}},
            memory={},
            settings={},
        ),
    )
    result = executor.execute(plan)
    assert result.outputs['reviewStatus'] == 'completed'
    assert result.outputs['scoreSuggestion'] == 7
    assert result.events[0]['message'] == 'approval granted by reviewer'


def test_agent_executor_uses_execution_queue_file_backend_for_experiment(tmp_path: Path):
    queue_root = tmp_path / 'execution_queue'
    requests_dir = queue_root / 'requests'
    responses_dir = queue_root / 'responses'
    workers_dir = queue_root / 'workers'
    claims_dir = queue_root / 'claims'
    acks_dir = queue_root / 'acks'
    requests_dir.mkdir(parents=True, exist_ok=True)
    responses_dir.mkdir(parents=True, exist_ok=True)
    workers_dir.mkdir(parents=True, exist_ok=True)
    claims_dir.mkdir(parents=True, exist_ok=True)
    acks_dir.mkdir(parents=True, exist_ok=True)
    request_id = 'queued-experiment'
    worker_id = 'worker-a'
    (workers_dir / f'{worker_id}.json').write_text(json.dumps({'workerId': worker_id, 'status': 'ready'}))
    (claims_dir / f'{request_id}.json').write_text(json.dumps({'workerId': worker_id, 'claimed': True}))
    (responses_dir / f'{request_id}.json').write_text(json.dumps({
        'ok': True,
        'provider': 'local-executor',
        'model': 'queued-execution-worker',
        'text': 'queued execution worker completed',
        'payload': {
            'projectTitle': 'Queued Experiment [python]',
            'files': [{'path': 'README.md', 'content': '# Queued Experiment\n'}],
            'experimentStatus': 'designed',
            'executionSpec': {'entrypoint': 'scripts/run.sh', 'framework': 'FastAPI', 'language': 'python'},
        },
    }))
    capability = get_capability_registry().get('experiment')
    agent = get_agent_registry().get('experimenter')
    skills = [get_skill_registry().get('experiment-scaffold'), get_skill_registry().get('artifact-packaging')]
    executor = AgentExecutor()
    plan = AgentExecutionPlan(
        agent=agent,
        skills=skills,
        capability=capability,
        inputs={'seedQuery': 'queued execution experiment', 'paperType': 'system', 'framework': 'FastAPI', 'language': 'python'},
        context=ExecutionContext(
            run_id='execution-queue-run',
            blueprint_id='ml_paper',
            profile_id='faros_hybrid',
            node_id='experiment',
            capability_id='experiment',
            agent_id='experimenter',
            skill_ids=['experiment-scaffold', 'artifact-packaging'],
            provider_bindings={'experiment': {'provider_type': 'execution', 'provider': 'local-executor', 'options': {'external': {'mode': 'queue_file', 'queueRoot': str(queue_root), 'requestId': request_id, 'workerId': worker_id, 'requireRegistration': True, 'requireClaim': True, 'pollMs': 1, 'timeoutSec': 5}}}},
            memory={},
            settings={},
        ),
    )
    result = executor.execute(plan)
    assert result.outputs['projectTitle'] == 'Queued Experiment [python]'
    assert result.events[0]['message'] == 'queued execution worker completed'
    request_payload = json.loads((requests_dir / f'{request_id}.json').read_text())
    assert request_payload['capability_id'] == 'experiment'
    assert request_payload['_faros_queue']['workerId'] == worker_id
    ack_payload = json.loads((acks_dir / f'{request_id}.json').read_text())
    assert ack_payload['requestId'] == request_id
    assert ack_payload['capabilityId'] == 'experiment'


def test_agent_executor_uses_human_approval_queue_backend_for_review(tmp_path: Path):
    queue_root = tmp_path / 'approval_queue'
    requests_dir = queue_root / 'approval_requests'
    decisions_dir = queue_root / 'approval_decisions'
    workers_dir = queue_root / 'approval_workers'
    claims_dir = queue_root / 'approval_claims'
    acks_dir = queue_root / 'approval_acks'
    requests_dir.mkdir(parents=True, exist_ok=True)
    decisions_dir.mkdir(parents=True, exist_ok=True)
    workers_dir.mkdir(parents=True, exist_ok=True)
    claims_dir.mkdir(parents=True, exist_ok=True)
    acks_dir.mkdir(parents=True, exist_ok=True)
    request_id = 'queued-review'
    worker_id = 'reviewer-a'
    (workers_dir / f'{worker_id}.json').write_text(json.dumps({'workerId': worker_id, 'status': 'ready'}))
    (claims_dir / f'{request_id}.json').write_text(json.dumps({'workerId': worker_id, 'claimed': True}))
    (decisions_dir / f'{request_id}.json').write_text(json.dumps({
        'status': 'approved',
        'provider': 'manual-review',
        'model': 'queued-approval-review',
        'text': 'queued approval granted',
        'payload': {
            'reviewStatus': 'completed',
            'scoreSuggestion': 9,
            'markdownReport': '# Queued Approval\n',
            'jsonReport': {'summary': 'queued approval'},
            'actionItems': [{'title': 'Double-check appendix', 'severity': 'minor'}],
        },
    }))
    capability = get_capability_registry().get('reviewer_simulation')
    agent = get_agent_registry().get('reviewer')
    skills = [get_skill_registry().get('review-critique'), get_skill_registry().get('consistency-audit')]
    executor = AgentExecutor()
    plan = AgentExecutionPlan(
        agent=agent,
        skills=skills,
        capability=capability,
        inputs={'paperId': 'paper_approval_queue'},
        context=ExecutionContext(
            run_id='human-approval-queue-run',
            blueprint_id='ml_paper',
            profile_id='faros_hybrid',
            node_id='review',
            capability_id='reviewer_simulation',
            agent_id='reviewer',
            skill_ids=['review-critique', 'consistency-audit'],
            provider_bindings={'reviewer_simulation': {'provider_type': 'human', 'provider': 'manual-review', 'options': {'external': {'mode': 'approval_queue', 'queueRoot': str(queue_root), 'requestId': request_id, 'workerId': worker_id, 'requireRegistration': True, 'requireClaim': True, 'pollMs': 1, 'timeoutSec': 5}}}},
            memory={},
            settings={},
        ),
    )
    result = executor.execute(plan)
    assert result.outputs['reviewStatus'] == 'completed'
    assert result.outputs['scoreSuggestion'] == 9
    assert result.events[0]['message'] == 'queued approval granted'
    request_payload = json.loads((requests_dir / f'{request_id}.json').read_text())
    assert request_payload['capability_id'] == 'reviewer_simulation'
    assert request_payload['_faros_queue']['workerId'] == worker_id
    ack_payload = json.loads((acks_dir / f'{request_id}.json').read_text())
    assert ack_payload['requestId'] == request_id
    assert ack_payload['approved'] is True


def test_agent_executor_uses_human_provider_owned_execution_for_review():
    capability = get_capability_registry().get('reviewer_simulation')
    agent = get_agent_registry().get('reviewer')
    skills = [get_skill_registry().get('review-critique'), get_skill_registry().get('consistency-audit')]
    executor = AgentExecutor()
    plan = AgentExecutionPlan(
        agent=agent,
        skills=skills,
        capability=capability,
        inputs={'paperId': 'paper_provider_test'},
        context=ExecutionContext(
            run_id='human-run',
            blueprint_id='ml_paper',
            profile_id='faros_hybrid',
            node_id='review',
            capability_id='reviewer_simulation',
            agent_id='reviewer',
            skill_ids=['review-critique', 'consistency-audit'],
            provider_bindings={'reviewer_simulation': {'provider_type': 'human', 'provider': 'manual-review', 'options': {}}},
            memory={},
            settings={},
        ),
    )
    result = executor.execute(plan)
    assert result.outputs['reviewStatus'] == 'completed'
    assert result.outputs['actionItemCount'] == 2
    assert result.verification['providerType'] == 'human'


def test_agent_executor_wraps_capability_execution_metadata():
    capability = get_capability_registry().get('idea_refinement')
    agent = get_agent_registry().get('researcher')
    skills = [get_skill_registry().get('literature-grounding'), get_skill_registry().get('idea-analysis')]
    executor = AgentExecutor()
    plan = AgentExecutionPlan(
        agent=agent,
        skills=skills,
        capability=capability,
        inputs={'seedQuery': 'Agent executor smoke test', 'paperType': 'algorithm'},
        context=ExecutionContext(
            run_id='test-run',
            blueprint_id='ml_paper',
            profile_id='faros_llm',
            node_id='idea',
            capability_id='idea_refinement',
            agent_id='researcher',
            skill_ids=['literature-grounding', 'idea-analysis'],
            provider_bindings={},
            memory={},
            settings={},
        ),
    )
    result = executor.execute(plan)
    assert result.verification['agentId'] == 'researcher'
    assert result.verification['skillIds'] == ['literature-grounding', 'idea-analysis']






def test_skill_loader_rejects_invalid_manifest(tmp_path: Path):
    skill_dir = tmp_path / 'broken-skill'
    skill_dir.mkdir(parents=True, exist_ok=True)
    invalid_skill = {
        'id': 'broken-skill',
        'name': 'Broken Skill',
        'version': '0.1',
        'manifest_version': '1.0',
        'kind': 'unknown',
        'description': 'broken',
        'agent_roles': ['researcher'],
        'tags': ['x'],
        'artifact_types': ['artifact'],
        'verification_hooks': ['schema-check'],
        'provider_requirements': {'reasoning': 'llm'}
    }
    (skill_dir / 'skill.json').write_text(json.dumps(invalid_skill, indent=2))
    (skill_dir / 'README.md').write_text('# Broken Skill\n')
    loader = SkillLoader(tmp_path)
    with pytest.raises(ValueError):
        loader.load('broken-skill')

def test_profile_loader_rejects_invalid_profile(tmp_path: Path):
    profile_dir = tmp_path / 'broken_profile'
    profile_dir.mkdir(parents=True, exist_ok=True)
    invalid_profile = {
        'id': 'broken_profile',
        'name': 'Broken Profile',
        'version': '0.0.1',
        'capability_bindings': {
            'missing_capability': {
                'provider_type': 'unknown',
                'provider': 'minimax'
            }
        },
        'agent_bindings': {
            'researcher': {
                'agent_id': 'researcher',
                'provider_type': 'llm',
                'preferred_provider': 'nonexistent-provider',
                'skill_overrides': ['paper-outline'],
                'runtime_policy': {}
            }
        },
        'skill_defaults': {}
    }
    (profile_dir / 'profile.json').write_text(json.dumps(invalid_profile, indent=2))
    loader = ProfileLoader(tmp_path)
    with pytest.raises(ValueError):
        loader.load('broken_profile')

def test_blueprint_loader_rejects_invalid_blueprint(tmp_path: Path):
    blueprint_dir = tmp_path / 'broken'
    blueprint_dir.mkdir(parents=True, exist_ok=True)
    invalid_blueprint = {
        'id': 'broken',
        'name': 'Broken Blueprint',
        'version': '0.0.1',
        'workflow': [
            {
                'id': 'n1',
                'capability': 'idea_refinement',
                'agent': 'researcher',
                'skills': ['missing-skill'],
            }
        ],
        'edges': [],
    }
    (blueprint_dir / 'blueprint.json').write_text(json.dumps(invalid_blueprint, indent=2))
    loader = BlueprintLoader(tmp_path)
    with pytest.raises(ValueError):
        loader.load('broken')






def test_faros_preflight_returns_passed_result():
    response = client.post('/api/faros/preflight', json={'blueprintId': 'ml_paper', 'profileId': 'faros_llm'})
    assert response.status_code == 200
    payload = response.json()
    assert payload['status'] == 'passed'
    assert len(payload['nodes']) == 4
    assert payload['nodes'][0]['agent_id'] == 'researcher'
    assert 'llm' in payload['nodes'][0]['provider_types']



def test_faros_run_detail_returns_structured_runtime_view():
    create = client.post(
        '/api/faros/runs',
        json={
            'blueprintId': 'ml_paper',
            'profileId': 'faros_llm',
            'executionMode': 'plan',
            'inputs': {'seedQuery': 'detail view', 'paperType': 'system', 'targetVenue': 'generic'}
        },
    )
    assert create.status_code == 201
    run_id = create.json()['id']

    detail = client.get(f'/api/faros/runs/{run_id}/detail')
    assert detail.status_code == 200
    payload = detail.json()
    assert payload['runtime']['blueprintId'] == 'ml_paper'
    assert payload['runtime']['profileId'] == 'faros_llm'
    assert payload['runtime']['stepCount'] == 4
    assert payload['runtime']['readyNodePolicy'] == 'fifo'
    assert payload['runtime']['memoryPolicy']['mode'] == 'persistent'
    assert payload['runtime']['memoryHistoryCount'] == 0
    assert payload['runtime']['memoryArchiveScopes'] == []
    assert payload['runtime']['stepStatusCounts']['ready'] == 1
    assert payload['runtime']['stepStatusCounts']['blocked'] == 3
    assert payload['timeline'][0]['nodeId'] == 'idea'
    assert payload['dependencySummary']['edgeCount'] == 3
    assert payload['dependencySummary']['rootNodes'] == ['idea']
    assert payload['dependencySummary']['leafNodes'] == ['review']
    assert payload['verificationSummary']['status'] == 'passed'
    assert payload['verificationSummary']['preflightStatus'] == 'passed'
    assert payload['workflow'][0]['nodeId'] == 'idea'
    assert payload['workflow'][0]['agentId'] == 'researcher'
    assert payload['workflow'][0]['dependencies']['upstream'] == []
    assert payload['workflow'][0]['dependencies']['downstream'] == ['experiment']
    assert payload['preflight']['status'] == 'passed'
    assert payload['checkpoint']['readyNodePolicy'] == 'fifo'
    assert payload['checkpoint']['readyNodeIds'] == ['idea']
    assert 'idea' in payload['checkpoint']['replayableNodeIds'] or payload['checkpoint']['replayableNodeIds'] == []






def test_state_store_rejects_invalid_step_transition(tmp_path: Path):
    store = FarosStateStore(root=tmp_path / 'runs')
    run = store.create_run(
        blueprint_id='ml_paper',
        profile_id='faros_llm',
        execution_mode='execute',
        inputs={},
        steps=[StepState(node_id='idea', capability='idea_refinement', status='completed')],
    )
    with pytest.raises(ValueError, match='Invalid FAROS step status transition'):
        store.update_step(run['id'], 'idea', {'status': 'running'})


def test_state_store_rejects_invalid_run_transition(tmp_path: Path):
    store = FarosStateStore(root=tmp_path / 'runs')
    run = store.create_run(
        blueprint_id='ml_paper',
        profile_id='faros_llm',
        execution_mode='execute',
        inputs={},
        steps=[StepState(node_id='idea', capability='idea_refinement', status='ready')],
    )
    store.update_run(run['id'], {'status': 'completed'})
    with pytest.raises(ValueError, match='Invalid FAROS run status transition'):
        store.update_run(run['id'], {'status': 'running'})


def test_faros_retry_endpoint_requeues_failed_step_and_preserves_policy():
    create = client.post(
        '/api/faros/runs',
        json={
            'blueprintId': 'ml_paper',
            'profileId': 'faros_llm',
            'executionMode': 'plan',
            'runtimeOptions': {'readyNodePolicy': 'reverse'},
            'inputs': {'seedQuery': 'retry flow', 'paperType': 'system', 'targetVenue': 'generic'}
        },
    )
    assert create.status_code == 201
    run_id = create.json()['id']

    orchestrator = get_orchestrator()
    orchestrator.state_store.update_step(run_id, 'idea', {'status': 'running'})
    orchestrator.state_store.update_step(run_id, 'idea', {'status': 'failed', 'error': 'forced failure'})

    retry = client.post(f'/api/faros/runs/{run_id}/steps/idea/retry')
    assert retry.status_code == 200
    retry_payload = retry.json()
    idea_step = next(step for step in retry_payload['steps'] if step['node_id'] == 'idea')
    assert idea_step['status'] == 'ready'
    assert idea_step['retry_count'] == 1
    assert retry_payload['runtime_options']['readyNodePolicy'] == 'reverse'
    assert retry_payload['checkpoint']['readyNodePolicy'] == 'reverse'
    assert 'idea' in retry_payload['checkpoint']['readyNodeIds']


def test_faros_replay_endpoint_resets_completed_subgraph_from_checkpoint():
    create = client.post(
        '/api/faros/runs',
        json={
            'blueprintId': 'ml_paper',
            'profileId': 'faros_hybrid',
            'executionMode': 'plan',
            'inputs': {'seedQuery': 'replay flow', 'paperType': 'system', 'targetVenue': 'generic'},
        },
    )
    assert create.status_code == 201
    run_id = create.json()['id']

    resumed = client.post(f'/api/faros/runs/{run_id}/resume')
    assert resumed.status_code == 200
    assert resumed.json()['status'] == 'completed'

    replay = client.post(
        f'/api/faros/runs/{run_id}/steps/experiment/replay',
        json={'includeDownstream': True},
    )
    assert replay.status_code == 200
    replay_payload = replay.json()
    steps = {step['node_id']: step for step in replay_payload['steps']}
    assert steps['idea']['status'] == 'completed'
    assert steps['experiment']['status'] == 'ready'
    assert steps['paper']['status'] == 'blocked'
    assert steps['review']['status'] == 'blocked'
    assert replay_payload['checkpoint']['readyNodeIds'] == ['experiment']
    assert replay_payload['checkpoint']['completedNodeIds'] == ['idea']
    assert replay_payload['checkpoint']['stepCheckpoints']['experiment']['status'] == 'replay-requested'
    assert replay_payload['checkpoint']['lastOperatorAction']['type'] == 'replay'
    assert replay_payload['checkpoint']['lastOperatorAction']['scope'] == 'subgraph'


def test_faros_replay_endpoint_rejects_non_terminal_or_running_nodes():
    create = client.post(
        '/api/faros/runs',
        json={
            'blueprintId': 'ml_paper',
            'profileId': 'faros_llm',
            'executionMode': 'plan',
            'inputs': {'seedQuery': 'replay guard', 'paperType': 'system', 'targetVenue': 'generic'},
        },
    )
    assert create.status_code == 201
    run_id = create.json()['id']

    pending_replay = client.post(f'/api/faros/runs/{run_id}/steps/idea/replay', json={'includeDownstream': False})
    assert pending_replay.status_code == 409
    assert pending_replay.json()['detail']['errorCode'] == 'run_blocked'
    assert 'Cannot replay node' in pending_replay.json()['detail']['message']

    orchestrator = get_orchestrator()
    orchestrator.state_store.update_run(run_id, {'status': 'running'})
    running_replay = client.post(f'/api/faros/runs/{run_id}/steps/idea/replay', json={'includeDownstream': False})
    assert running_replay.status_code == 409
    assert running_replay.json()['detail']['errorCode'] == 'run_blocked'
    assert 'is running' in running_replay.json()['detail']['message']


def test_faros_dependency_aware_resume_and_skip_runtime_flow():
    create = client.post(
        '/api/faros/runs',
        json={
            'blueprintId': 'ml_paper',
            'profileId': 'faros_hybrid',
            'executionMode': 'plan',
            'inputs': {'seedQuery': 'hybrid runtime flow', 'paperType': 'system', 'targetVenue': 'generic'},
        },
    )
    assert create.status_code == 201
    run = create.json()
    run_id = run['id']
    steps = {step['node_id']: step for step in run['steps']}
    assert steps['idea']['status'] == 'ready'
    assert steps['experiment']['status'] == 'blocked'
    assert steps['paper']['status'] == 'blocked'
    assert steps['review']['status'] == 'blocked'

    skipped = client.post(f'/api/faros/runs/{run_id}/steps/idea/skip')
    assert skipped.status_code == 200
    skipped_payload = skipped.json()
    skipped_steps = {step['node_id']: step for step in skipped_payload['steps']}
    assert skipped_steps['idea']['status'] == 'skipped'
    assert skipped_steps['experiment']['status'] == 'ready'

    resumed = client.post(f'/api/faros/runs/{run_id}/resume')
    assert resumed.status_code == 200
    resumed_payload = resumed.json()
    assert resumed_payload['status'] == 'completed'
    resumed_steps = {step['node_id']: step for step in resumed_payload['steps']}
    assert resumed_steps['experiment']['status'] == 'completed'
    assert resumed_steps['paper']['status'] == 'completed'
    assert resumed_steps['review']['status'] == 'completed'
    assert resumed_payload['output_summary']['paperId']
    assert resumed_payload['output_summary']['reviewId']

    detail = client.get(f'/api/faros/runs/{run_id}/detail')
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload['runtime']['status'] == 'completed'
    assert detail_payload['runtime']['stepStatusCounts']['skipped'] == 1
    assert detail_payload['runtime']['stepStatusCounts']['completed'] == 3
    assert detail_payload['verificationSummary']['status'] == 'passed'
    artifact_types = {artifact['type'] for artifact in detail_payload['artifacts']}
    assert {'code_project', 'experiment_record', 'paper_record', 'review_report'}.issubset(artifact_types)


def test_faros_run_memory_query_endpoint_filters_payload():
    create = client.post(
        '/api/faros/runs',
        json={
            'blueprintId': 'ml_paper',
            'profileId': 'faros_llm',
            'executionMode': 'plan',
            'inputs': {'seedQuery': 'memory query', 'paperType': 'system', 'targetVenue': 'generic'}
        },
    )
    assert create.status_code == 201
    run_id = create.json()['id']

    response = client.get(
        f'/api/faros/runs/{run_id}/memory/query',
        params={
            'keys': 'seedQuery,lastNodeId',
            'scopes': 'run',
            'includeSummary': 'false',
            'includeHistory': 'true',
            'historyLimit': '0',
        },
    )
    assert response.status_code == 200
    payload = response.json()['query']
    assert payload['data'] == {'seedQuery': 'memory query'}
    assert payload['summary'] == {}
    assert 'run' in payload['scopes']
    assert payload['history'] == []
    assert payload['archives'] == {}


def test_faros_run_memory_recall_endpoint_restores_archived_scope():
    create = client.post(
        '/api/faros/runs',
        json={
            'blueprintId': 'ml_paper',
            'profileId': 'faros_llm',
            'executionMode': 'plan',
            'inputs': {'seedQuery': 'memory recall', 'paperType': 'system', 'targetVenue': 'generic'}
        },
    )
    assert create.status_code == 201
    run_id = create.json()['id']

    orchestrator = get_orchestrator()
    memory = ResearchMemory(
        orchestrator.state_store,
        run_id,
        policy={
            'mode': 'persistent',
            'summary_keys': ['selectedCandidateId', 'paperId'],
            'scope_strategy': 'node',
            'max_history_entries': 32,
            'compaction_mode': 'archive_nonretained',
            'volatile_prefixes': [],
            'retained_scopes': ['run'],
            'remove_archived_keys': True,
        },
    )
    memory.merge({'paperId': 'paper_archived', 'projectId': 'proj_archived'}, scope='paper')

    query_before = client.get(
        f'/api/faros/runs/{run_id}/memory/query',
        params={'scopes': 'paper', 'includeArchives': 'true'},
    )
    assert query_before.status_code == 200
    before_payload = query_before.json()['query']
    assert before_payload['data'] == {'paperId': 'paper_archived'}
    assert before_payload['archives']['paper']['data']['paperId'] == 'paper_archived'

    recall = client.post(
        f'/api/faros/runs/{run_id}/memory/recall/paper',
        json={'mergeIntoRun': True},
    )
    assert recall.status_code == 200
    recall_payload = recall.json()
    assert recall_payload['recall']['scope'] == 'paper'
    assert recall_payload['recall']['restoredKeys'] == ['paperId', 'projectId']
    assert recall_payload['memory']['data']['paperId'] == 'paper_archived'
    assert recall_payload['memory']['data']['projectId'] == 'proj_archived'
    assert 'paperId' in recall_payload['memory']['scopes']['paper']
    assert 'projectId' in recall_payload['memory']['scopes']['paper']


def test_faros_run_memory_endpoint_returns_envelope():
    create = client.post(
        '/api/faros/runs',
        json={
            'blueprintId': 'ml_paper',
            'profileId': 'faros_llm',
            'executionMode': 'plan',
            'inputs': {'seedQuery': 'memory view', 'paperType': 'system', 'targetVenue': 'generic'}
        },
    )
    assert create.status_code == 201
    run_id = create.json()['id']

    response = client.get(f'/api/faros/runs/{run_id}/memory')
    assert response.status_code == 200
    payload = response.json()['memory']
    assert payload['data']['seedQuery'] == 'memory view'
    assert payload['summary'] == {}
    assert 'run' in payload['scopes']
    assert payload['history'] == []
    assert payload['policy']['mode'] == 'persistent'
    assert payload['policy']['compaction_mode'] == 'summary_only'

def test_verifier_registry_can_validate_install_and_refresh_package(tmp_path: Path):
    package_dir = tmp_path / 'custom-review-verifiers'
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / 'verifier.json').write_text(json.dumps({
        'id': 'custom-review-verifiers',
        'name': 'Custom Review Verifiers',
        'version': '0.1.0',
        'manifest_version': '1.0',
        'description': 'package lifecycle test',
        'verifier_ids': ['review_action_items'],
        'packs': {'strict-review': {'name': 'Strict Review', 'description': 'Use review checks for human and simulated reviewers.', 'verifier_ids': ['review_action_items', 'status'], 'tags': ['review', 'quality'], 'capability_ids': ['reviewer_simulation'], 'provider_types': ['human', 'llm'], 'recommended_node_ids': ['review']}},
        'compatibility': {'profiles': {'faros_llm': '>=0.1.0'}},
    }, indent=2))
    (package_dir / 'README.md').write_text('# Custom Review Verifiers\n')

    validate = client.post('/api/faros/verifiers/validate', json={'sourcePath': str(package_dir)})
    assert validate.status_code == 200
    assert validate.json()['verifierPackage']['id'] == 'custom-review-verifiers'

    install = client.post('/api/faros/verifiers/install', json={'sourcePath': str(package_dir), 'allowUntrusted': True})
    assert install.status_code == 201
    assert install.json()['verifierPackage']['id'] == 'custom-review-verifiers'

    verifiers = client.get('/api/faros/verifiers')
    assert verifiers.status_code == 200
    assert any(item['id'] == 'custom-review-verifiers' for item in verifiers.json()['packages'])
    assert 'strict-review' in verifiers.json()['packs']
    strict_review = next(item for item in verifiers.json()['packMetadata'] if item['id'] == 'strict-review')
    assert strict_review['provider_types'] == ['human', 'llm']
    assert strict_review['recommended_node_ids'] == ['review']

    refresh = client.post('/api/faros/verifiers/refresh')
    assert refresh.status_code == 200
    assert any(item['id'] == 'custom-review-verifiers' for item in refresh.json()['verifierPackages'])

    rollback = client.post('/api/faros/verifiers/rollback', json={'verifierId': 'custom-review-verifiers'})
    assert rollback.status_code == 400

    uninstall = client.post('/api/faros/verifiers/uninstall', json={'verifierId': 'custom-review-verifiers'})
    assert uninstall.status_code == 200
    assert uninstall.json()['status'] == 'uninstalled'


def test_verifier_dispatch_fails_invalid_artifact_schema():
    dispatcher = VerifierDispatcher()
    result = CapabilityResult(
        status='completed',
        outputs={'paperId': 'paper_1', 'paperStatus': 'completed'},
        artifacts=[
            ArtifactRecord(
                id='a1',
                type='paper_record',
                uri='paper://paper_1',
                producer='paper_drafting',
                metadata={'paperId': 'paper_1'}
            )
        ],
        verification={'agentId': 'writer', 'skillIds': ['paper-outline']},
    )
    verification = dispatcher.verify(
        'paper_drafting',
        result,
        expected_artifact_types=['paper_record'],
        artifact_schema_map={'paper_record': type('Schema', (), {'required_metadata': ['paperId', 'status'], 'required_producer': 'paper_drafting', 'allowed_uri_prefixes': ['paper://']})()},
        required_outputs=['paperId', 'paperStatus'],
    )
    assert verification.status == 'failed'
    assert any(item.rule_id.endswith(':artifact-schema') and item.status == 'failed' for item in verification.results)

def test_verifier_dispatch_fails_missing_artifact_contract():
    dispatcher = VerifierDispatcher()
    result = CapabilityResult(
        status='completed',
        outputs={'paperId': 'paper_1', 'paperStatus': 'completed'},
        artifacts=[],
        verification={'agentId': 'writer', 'skillIds': ['paper-outline']},
    )
    verification = dispatcher.verify(
        'paper_drafting',
        result,
        expected_artifact_types=['paper_record'],
        required_outputs=['paperId', 'paperStatus'],
    )
    assert verification.status == 'failed'
    assert any(item.rule_id.endswith(':artifacts') and item.status == 'failed' for item in verification.results)

def test_verifier_dispatch_supports_policy_packs_and_review_plugin():
    dispatcher = VerifierDispatcher()
    result = CapabilityResult(
        status='completed',
        outputs={'reviewId': 'r1', 'reviewStatus': 'completed', 'actionItems': [{'title': 'Fix scope', 'severity': 'major'}]},
        artifacts=[
            ArtifactRecord(
                id='r1',
                type='review_report',
                uri='review://r1',
                producer='reviewer_simulation',
                metadata={'reviewId': 'r1', 'paperId': 'p1'},
            )
        ],
        verification={'agentId': 'reviewer', 'skillIds': ['review-critique'], 'providerType': 'human'},
    )
    verification = dispatcher.verify(
        'reviewer_simulation',
        result,
        pack_ids=['runtime_baseline', 'review_quality'],
        disabled_verifier_ids=['artifact_contract'],
        expected_artifact_types=['review_report'],
        artifact_schema_map={'review_report': type('Schema', (), {'required_metadata': ['reviewId', 'paperId'], 'required_producer': 'reviewer_simulation', 'allowed_uri_prefixes': ['review://']})()},
        required_outputs=['reviewId', 'reviewStatus'],
    )
    assert verification.status == 'passed'
    assert 'review_action_items' in verification.verifier_ids
    assert 'artifact_contract' not in verification.verifier_ids
    assert any(item.rule_id.endswith(':review-action-items') and item.status == 'passed' for item in verification.results)

def test_faros_plan_run_can_be_created_without_provider_execution():
    response = client.post(
        '/api/faros/runs',
        json={
            'blueprintId': 'ml_paper',
            'profileId': 'faros_llm',
            'executionMode': 'plan',
            'inputs': {
                'seedQuery': 'Optimize CPU usage in LLM workflows',
                'paperType': 'system',
                'targetVenue': 'generic'
            }
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload['status'] == 'planned'
    assert payload['blueprint_id'] == 'ml_paper'
    assert payload['profile_id'] == 'faros_llm'
    assert payload['preflight']['status'] == 'passed'
    assert len(payload['steps']) == 4
    assert payload['steps'][0]['agent_id'] == 'researcher'
    assert payload['steps'][0]['skill_ids'] == ['literature-grounding', 'idea-analysis']


def test_research_memory_can_archive_nonretained_scopes(tmp_path: Path):
    store = FarosStateStore(root=tmp_path)
    run = store.create_run(
        blueprint_id='ml_paper',
        profile_id='faros_llm',
        execution_mode='plan',
        inputs={'seedQuery': 'archive', 'tmp_seed': 'drop-me'},
        steps=[],
        preflight={},
    )
    memory = ResearchMemory(
        store,
        run['id'],
        policy={
            'mode': 'persistent',
            'summary_keys': ['selectedCandidateId'],
            'scope_strategy': 'compact',
            'max_history_entries': 2,
            'compaction_mode': 'archive_nonretained',
            'volatile_prefixes': ['tmp_'],
            'retained_scopes': ['run', 'idea'],
            'remove_archived_keys': True,
        },
    )
    memory.merge({'selectedCandidateId': 'cand_1', 'ideaNotes': 'keep'}, scope='idea')
    memory.merge({'paperDraft': 'archive-me'}, scope='paper')
    memory.record_step('paper', {'paperDraft': 'archive-me'})

    snapshot = memory.snapshot()
    assert snapshot['summary']['selectedCandidateId'] == 'cand_1'
    assert snapshot['archives']['paper']['data']['paperDraft'] == 'archive-me'
    assert 'paperDraft' not in snapshot['data']
    assert 'tmp_seed' not in snapshot['data']
    assert 'paper' not in snapshot['scopes']


def test_skill_registry_can_install_and_refresh_local_package(tmp_path: Path):
    skills_root = tmp_path / 'installed_skills'
    registry = SkillRegistry(skills_root)

    package_dir = tmp_path / 'candidate-skill'
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / 'skill.json').write_text(json.dumps({
        'id': 'candidate-skill',
        'name': 'Candidate Skill',
        'version': '0.1.0',
        'manifest_version': '1.0',
        'kind': 'runtime',
        'description': 'A local install test skill.',
        'agent_roles': ['researcher'],
        'tags': ['test'],
        'artifact_types': ['run_manifest'],
        'verification_hooks': ['schema-check'],
        'provider_requirements': {'storage': 'local'},
    }, indent=2))
    (package_dir / 'README.md').write_text('# Candidate Skill\n')

    validated = registry.validate_package(package_dir)
    assert validated.id == 'candidate-skill'

    installed = registry.install_package(package_dir)
    assert installed.id == 'candidate-skill'
    assert (skills_root / 'candidate-skill' / 'skill.json').is_file()

    refreshed = registry.refresh()
    assert any(skill.id == 'candidate-skill' for skill in refreshed)


def test_skill_registry_rejects_signature_mismatch_when_trust_is_enforced(tmp_path: Path):
    skills_root = tmp_path / 'installed_skills_sig'
    registry = SkillRegistry(skills_root)

    package_dir = tmp_path / 'bad-signature-skill'
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / 'skill.json').write_text(json.dumps({
        'id': 'bad-signature-skill',
        'name': 'Bad Signature Skill',
        'version': '0.1.0',
        'manifest_version': '1.0',
        'kind': 'runtime',
        'description': 'bad signature trust test',
        'agent_roles': ['researcher'],
        'tags': ['test'],
        'artifact_types': ['run_manifest'],
        'verification_hooks': ['schema-check'],
        'provider_requirements': {'storage': 'local'},
    }, indent=2))
    (package_dir / 'README.md').write_text('# Bad Signature Skill\n')

    keyring_path = Path(__file__).resolve().parents[1] / 'data' / 'faros' / 'packages' / 'trust_keyring.json'
    keyring_path.parent.mkdir(parents=True, exist_ok=True)
    keyring_path.write_text(json.dumps({'faros-test-signer': {'secret': 'faros-test-secret'}}, indent=2))

    trust_payload = {
        'publisher': 'faros-test',
        'source': 'local-fixture',
        'trustLevel': 'verified',
        'signedBy': 'faros-test-signer',
        'signature': {'algorithm': 'hmac-sha256', 'signedBy': 'faros-test-signer', 'value': '0' * 64},
    }
    (package_dir / 'TRUST.json').write_text(json.dumps(trust_payload, indent=2))

    with pytest.raises(ValueError, match='not trusted'):
        registry.install_package(package_dir, allow_untrusted=False)


def test_skill_registry_rejects_untrusted_package_when_policy_is_enforced(tmp_path: Path):
    skills_root = tmp_path / 'installed_skills'
    registry = SkillRegistry(skills_root)

    package_dir = tmp_path / 'strict-skill'
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / 'skill.json').write_text(json.dumps({
        'id': 'strict-skill',
        'name': 'Strict Skill',
        'version': '0.1.0',
        'manifest_version': '1.0',
        'kind': 'runtime',
        'description': 'strict trust test',
        'agent_roles': ['researcher'],
        'tags': ['test'],
        'artifact_types': ['run_manifest'],
        'verification_hooks': ['schema-check'],
        'provider_requirements': {'storage': 'local'},
    }, indent=2))
    (package_dir / 'README.md').write_text('# Strict Skill\n')

    with pytest.raises(ValueError, match='not trusted'):
        registry.install_package(package_dir, allow_untrusted=False)

    (package_dir / 'TRUST.json').write_text(json.dumps({
        'publisher': 'faros-test',
        'source': 'local-fixture',
        'trustLevel': 'verified',
        'signedBy': 'test-suite',
    }, indent=2))
    installed = registry.install_package(package_dir, allow_untrusted=False)
    assert installed.id == 'strict-skill'
    assert registry.audit_log()[0]['trustStatus'] == 'trusted'


def test_skill_registry_install_rejects_duplicate_without_overwrite(tmp_path: Path):
    skills_root = tmp_path / 'installed_skills'
    registry = SkillRegistry(skills_root)

    package_dir = tmp_path / 'duplicate-skill'
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / 'skill.json').write_text(json.dumps({
        'id': 'duplicate-skill',
        'name': 'Duplicate Skill',
        'version': '0.1.0',
        'manifest_version': '1.0',
        'kind': 'runtime',
        'description': 'A duplicate install test skill.',
        'agent_roles': ['researcher'],
        'tags': ['test'],
        'artifact_types': ['run_manifest'],
        'verification_hooks': ['schema-check'],
        'provider_requirements': {'storage': 'local'},
    }, indent=2))
    (package_dir / 'README.md').write_text('# Duplicate Skill\n')

    registry.install_package(package_dir)
    with pytest.raises(ValueError):
        registry.install_package(package_dir)


def test_agent_registry_can_install_and_refresh_local_package(tmp_path: Path):
    agents_root = tmp_path / 'installed_agents'
    registry = AgentRegistry(agents_root)

    package_dir = tmp_path / 'assistant-agent'
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / 'agent.json').write_text(json.dumps({
        'id': 'assistant-agent',
        'name': 'Assistant Agent',
        'role': 'researcher',
        'description': 'A local install test agent.',
        'default_skills': ['literature-grounding'],
        'provider_preferences': {'reasoning': 'llm'},
        'handoff_policy': {'mode': 'direct'},
    }, indent=2))
    (package_dir / 'README.md').write_text('# Assistant Agent\n')

    validated = registry.validate_package(package_dir)
    assert validated.id == 'assistant-agent'

    installed = registry.install_package(package_dir)
    assert installed.id == 'assistant-agent'
    assert (agents_root / 'assistant-agent' / 'agent.json').is_file()

    refreshed = registry.refresh()
    assert any(agent.id == 'assistant-agent' for agent in refreshed)


def test_agent_registry_install_rejects_duplicate_without_overwrite(tmp_path: Path):
    agents_root = tmp_path / 'installed_agents'
    registry = AgentRegistry(agents_root)

    package_dir = tmp_path / 'duplicate-agent'
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / 'agent.json').write_text(json.dumps({
        'id': 'duplicate-agent',
        'name': 'Duplicate Agent',
        'role': 'researcher',
        'description': 'A duplicate install test agent.',
        'default_skills': ['literature-grounding'],
        'provider_preferences': {'reasoning': 'llm'},
        'handoff_policy': {'mode': 'direct'},
    }, indent=2))
    (package_dir / 'README.md').write_text('# Duplicate Agent\n')

    registry.install_package(package_dir)
    with pytest.raises(ValueError):
        registry.install_package(package_dir)


def test_blueprint_registry_can_install_and_refresh_local_package(tmp_path: Path):
    blueprints_root = tmp_path / 'installed_blueprints'
    registry = BlueprintRegistry(blueprints_root)

    package_dir = tmp_path / 'mini_flow'
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / 'blueprint.json').write_text(json.dumps({
        'id': 'mini_flow',
        'name': 'Mini Flow',
        'version': '0.1.0',
        'domain': 'test',
        'description': 'A local install test blueprint.',
        'workflow': [
            {
                'id': 'idea',
                'capability': 'idea_refinement',
                'agent': 'researcher',
                'skills': ['literature-grounding', 'idea-analysis'],
            }
        ],
        'edges': [],
        'artifact_schema': {},
        'verification_rules': [],
        'output_contract': {},
    }, indent=2))
    (package_dir / 'README.md').write_text('# Mini Flow\n')

    validated = registry.validate_package(package_dir)
    assert validated.id == 'mini_flow'

    installed = registry.install_package(package_dir)
    assert installed.id == 'mini_flow'
    assert (blueprints_root / 'mini_flow' / 'blueprint.json').is_file()

    refreshed = registry.refresh()
    assert any(blueprint.id == 'mini_flow' for blueprint in refreshed)


def test_blueprint_registry_install_rejects_duplicate_without_overwrite(tmp_path: Path):
    blueprints_root = tmp_path / 'installed_blueprints'
    registry = BlueprintRegistry(blueprints_root)

    package_dir = tmp_path / 'duplicate_flow'
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / 'blueprint.json').write_text(json.dumps({
        'id': 'duplicate_flow',
        'name': 'Duplicate Flow',
        'version': '0.1.0',
        'domain': 'test',
        'description': 'A duplicate install test blueprint.',
        'workflow': [
            {
                'id': 'idea',
                'capability': 'idea_refinement',
                'agent': 'researcher',
                'skills': ['literature-grounding', 'idea-analysis'],
            }
        ],
        'edges': [],
        'artifact_schema': {},
        'verification_rules': [],
        'output_contract': {},
    }, indent=2))
    (package_dir / 'README.md').write_text('# Duplicate Flow\n')

    registry.install_package(package_dir)
    with pytest.raises(ValueError):
        registry.install_package(package_dir)

def test_faros_hybrid_profile_preflight_returns_passed_result():
    response = client.post('/api/faros/preflight', json={'blueprintId': 'ml_paper', 'profileId': 'faros_hybrid'})
    assert response.status_code == 200
    payload = response.json()
    assert payload['status'] == 'passed'
    provider_types = {ptype for node in payload['nodes'] for ptype in node['provider_types']}
    assert {'llm', 'tool', 'execution', 'human'}.issubset(provider_types)

def test_skill_registry_downgrade_guard_and_audit_log(tmp_path: Path):
    skills_root = tmp_path / 'installed_skills'
    audit = PackageAuditStore(tmp_path / 'audit')
    registry = SkillRegistry(skills_root, audit_store=audit)

    package_dir = tmp_path / 'versioned-skill'
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / 'README.md').write_text('# Versioned Skill\n')
    (package_dir / 'skill.json').write_text(json.dumps({
        'id': 'versioned-skill',
        'name': 'Versioned Skill',
        'version': '0.2.0',
        'manifest_version': '1.0',
        'kind': 'runtime',
        'description': 'test',
        'agent_roles': ['researcher'],
        'tags': ['test'],
        'artifact_types': ['run_manifest'],
        'verification_hooks': ['schema-check'],
        'provider_requirements': {'storage': 'local'},
    }, indent=2))

    installed = registry.install_package(package_dir)
    assert installed.version == '0.2.0'

    (package_dir / 'skill.json').write_text(json.dumps({
        'id': 'versioned-skill',
        'name': 'Versioned Skill',
        'version': '0.1.0',
        'manifest_version': '1.0',
        'kind': 'runtime',
        'description': 'test',
        'agent_roles': ['researcher'],
        'tags': ['test'],
        'artifact_types': ['run_manifest'],
        'verification_hooks': ['schema-check'],
        'provider_requirements': {'storage': 'local'},
    }, indent=2))

    with pytest.raises(ValueError):
        registry.install_package(package_dir, overwrite=True)

    downgraded = registry.install_package(package_dir, overwrite=True, allow_downgrade=True)
    assert downgraded.version == '0.1.0'
    audit_events = registry.audit_log()
    assert audit_events[0]['action'] == 'upgrade'
    assert audit_events[0]['version'] == '0.1.0'
    assert audit_events[0]['previousVersion'] == '0.2.0'


def test_agent_registry_uninstall_and_audit_log(tmp_path: Path):
    agents_root = tmp_path / 'installed_agents'
    audit = PackageAuditStore(tmp_path / 'audit')
    registry = AgentRegistry(agents_root, audit_store=audit)

    package_dir = tmp_path / 'assistant-agent'
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / 'agent.json').write_text(json.dumps({
        'id': 'assistant-agent',
        'name': 'Assistant Agent',
        'version': '0.1.0',
        'role': 'researcher',
        'description': 'A local install test agent.',
        'default_skills': ['literature-grounding'],
        'provider_preferences': {'reasoning': 'llm'},
        'handoff_policy': {'mode': 'direct'},
    }, indent=2))
    (package_dir / 'README.md').write_text('# Assistant Agent\n')

    registry.install_package(package_dir)
    result = registry.uninstall_package('assistant-agent')
    assert result['status'] == 'uninstalled'
    assert not (agents_root / 'assistant-agent').exists()
    audit_events = registry.audit_log()
    assert audit_events[0]['action'] == 'uninstall'
    assert audit_events[0]['packageId'] == 'assistant-agent'


def test_blueprint_registry_upgrade_and_audit_log(tmp_path: Path):
    blueprints_root = tmp_path / 'installed_blueprints'
    audit = PackageAuditStore(tmp_path / 'audit')
    registry = BlueprintRegistry(blueprints_root, audit_store=audit)

    package_dir = tmp_path / 'mini_flow'
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / 'README.md').write_text('# Mini Flow\n')
    (package_dir / 'blueprint.json').write_text(json.dumps({
        'id': 'mini_flow',
        'name': 'Mini Flow',
        'version': '0.1.0',
        'domain': 'test',
        'description': 'A local install test blueprint.',
        'workflow': [{'id': 'idea', 'capability': 'idea_refinement', 'agent': 'researcher', 'skills': ['literature-grounding', 'idea-analysis']}],
        'edges': [],
        'artifact_schema': {},
        'verification_rules': [],
        'output_contract': {},
    }, indent=2))

    registry.install_package(package_dir)

    (package_dir / 'blueprint.json').write_text(json.dumps({
        'id': 'mini_flow',
        'name': 'Mini Flow',
        'version': '0.2.0',
        'domain': 'test',
        'description': 'A local install test blueprint.',
        'workflow': [{'id': 'idea', 'capability': 'idea_refinement', 'agent': 'researcher', 'skills': ['literature-grounding', 'idea-analysis']}],
        'edges': [],
        'artifact_schema': {},
        'verification_rules': [],
        'output_contract': {},
    }, indent=2))

    upgraded = registry.install_package(package_dir, overwrite=True)
    assert upgraded.version == '0.2.0'
    audit_events = registry.audit_log()
    assert audit_events[0]['action'] == 'upgrade'
    assert audit_events[0]['previousVersion'] == '0.1.0'


def test_package_audit_api_returns_events():
    response = client.get('/api/faros/packages/audit')
    assert response.status_code == 200
    payload = response.json()
    assert 'events' in payload
    assert 'total' in payload

def test_skill_registry_rejects_incompatible_profile_version(tmp_path: Path):
    skills_root = tmp_path / 'installed_skills'
    registry = SkillRegistry(skills_root)
    current_profile_version = get_profile_registry().get('faros_llm').version
    major = int(current_profile_version.split('.')[0]) + 10

    package_dir = tmp_path / 'compat-skill'
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / 'README.md').write_text('# Compat Skill\n')
    (package_dir / 'skill.json').write_text(json.dumps({
        'id': 'compat-skill',
        'name': 'Compat Skill',
        'version': '0.1.0',
        'manifest_version': '1.0',
        'kind': 'runtime',
        'description': 'compatibility test',
        'agent_roles': ['researcher'],
        'tags': ['test'],
        'artifact_types': ['run_manifest'],
        'verification_hooks': ['schema-check'],
        'provider_requirements': {'storage': 'local'},
        'compatibility': {'profiles': {'faros_llm': f'>={major}.0.0'}},
    }, indent=2))

    with pytest.raises(ValueError):
        registry.install_package(package_dir)


def test_agent_registry_accepts_compatible_profile_and_skill_versions(tmp_path: Path):
    agents_root = tmp_path / 'installed_agents'
    registry = AgentRegistry(agents_root)
    current_profile_version = get_profile_registry().get('faros_llm').version
    current_skill_version = get_skill_registry().get('literature-grounding').version

    package_dir = tmp_path / 'compat-agent'
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / 'README.md').write_text('# Compat Agent\n')
    (package_dir / 'agent.json').write_text(json.dumps({
        'id': 'compat-agent',
        'name': 'Compat Agent',
        'version': '0.1.0',
        'role': 'researcher',
        'description': 'compatibility test agent',
        'default_skills': ['literature-grounding'],
        'provider_preferences': {'reasoning': 'llm'},
        'compatibility': {
            'profiles': {'faros_llm': f'>={current_profile_version}'},
            'skills': {'literature-grounding': f'=={current_skill_version}'},
        },
        'handoff_policy': {'mode': 'direct'},
    }, indent=2))

    installed = registry.install_package(package_dir)
    assert installed.id == 'compat-agent'


def test_blueprint_registry_rejects_incompatible_profile_version(tmp_path: Path):
    blueprints_root = tmp_path / 'installed_blueprints'
    registry = BlueprintRegistry(blueprints_root)
    current_profile_version = get_profile_registry().get('faros_llm').version
    major = int(current_profile_version.split('.')[0]) + 10

    package_dir = tmp_path / 'compat_flow'
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / 'README.md').write_text('# Compat Flow\n')
    (package_dir / 'blueprint.json').write_text(json.dumps({
        'id': 'compat_flow',
        'name': 'Compat Flow',
        'version': '0.1.0',
        'domain': 'test',
        'description': 'compatibility test blueprint.',
        'workflow': [{'id': 'idea', 'capability': 'idea_refinement', 'agent': 'researcher', 'skills': ['literature-grounding', 'idea-analysis']}],
        'edges': [],
        'artifact_schema': {},
        'verification_rules': [],
        'output_contract': {},
        'compatibility': {'profiles': {'faros_llm': f'>={major}.0.0'}},
    }, indent=2))

    with pytest.raises(ValueError):
        registry.install_package(package_dir)



def test_dependency_report_returns_structured_issue_details():
    current_profile = get_profile_registry().get('faros_llm')
    report = build_dependency_report({
        'profiles': [current_profile],
        'agents': [AgentSpec(
            id='dependent-agent',
            name='Dependent Agent',
            version='0.1.0',
            role='researcher',
            description='depends on base skill',
            default_skills=['base-skill'],
            provider_preferences={'reasoning': 'llm'},
            compatibility={'skills': {'base-skill': '==0.1.0'}},
        )],
        'skills': [
            SkillSpec(
                id='base-skill',
                name='Base Skill',
                version='0.1.0',
                manifest_version='1.0',
                kind='runtime',
                description='base dependency',
                agent_roles=['researcher'],
                provider_requirements={'storage': 'local'},
            ),
            SkillSpec(
                id='base-skill',
                name='Base Skill',
                version='0.2.0',
                manifest_version='1.0',
                kind='runtime',
                description='base dependency',
                agent_roles=['researcher'],
                provider_requirements={'storage': 'local'},
            ),
        ],
        'blueprints': [Blueprint(
            id='compatible-blueprint',
            name='Compatible Blueprint',
            version='0.1.0',
            workflow=[],
            compatibility={'profiles': {'faros_llm': f'>={current_profile.version}'}},
        )],
    })
    assert report['status'] == 'failed'
    assert report['counts']['issues'] == 1
    assert report['issues'][0]['subjectId'] == 'dependent-agent'
    assert report['issues'][0]['targetId'] == 'base-skill'
    assert report['edges'][0]['status'] in {'satisfied', 'version_mismatch', 'missing'}
    assert report['resolution']['counts']['targets'] >= 1
    assert report['resolution']['counts']['conflicts'] == 1
    assert report['resolution']['counts']['unsatisfiable'] == 0
    assert report['resolution']['conflicts'][0]['targetId'] == 'base-skill'
    assert report['resolution']['conflicts'][0]['blockingSubjects'][0]['subjectId'] == 'dependent-agent'
    assert report['resolution']['conflicts'][0]['candidateVersions'] == ['0.1.0']
    assert report['resolution']['conflicts'][0]['selectedVersion'] == '0.1.0'
    assert report['resolution']['conflicts'][0]['solver']['satisfiable'] is True
    assert report['resolution']['conflicts'][0]['solver']['recommendedConstraint'] == '==0.1.0'
    assert report['solution']['status'] == 'solved'
    assert {'packageType': 'skills', 'packageId': 'base-skill', 'fromVersion': '0.2.0', 'toVersion': '0.1.0'} in report['solution']['changes']
    assert {'id': 'base-skill', 'version': '0.1.0'} in report['solution']['selection']['skills']
    assert report['solution']['stats']['visitedStates'] >= 1
    assert report['solution']['stats']['decisions'] >= 1
    assert any(item['kind'] in {'decision', 'domain_reduction', 'auto_select'} for item in report['solution']['trace'])



def test_dependency_report_exposes_unsatisfiable_constraint_set():
    report = build_dependency_report({
        'profiles': [],
        'agents': [
            AgentSpec(
                id='agent-low',
                name='Agent Low',
                version='0.1.0',
                role='researcher',
                description='requires lower range',
                default_skills=['shared-skill'],
                provider_preferences={'reasoning': 'llm'},
                compatibility={'skills': {'shared-skill': '<=0.1.0'}},
            ),
            AgentSpec(
                id='agent-high',
                name='Agent High',
                version='0.1.0',
                role='researcher',
                description='requires upper range',
                default_skills=['shared-skill'],
                provider_preferences={'reasoning': 'llm'},
                compatibility={'skills': {'shared-skill': '>=0.3.0'}},
            ),
        ],
        'skills': [SkillSpec(
            id='shared-skill',
            name='Shared Skill',
            version='0.2.0',
            manifest_version='1.0',
            kind='runtime',
            description='shared dependency',
            agent_roles=['researcher'],
            provider_requirements={'storage': 'local'},
        )],
        'blueprints': [],
    })
    assert report['resolution']['counts']['unsatisfiable'] == 1
    conflict = report['resolution']['unsatisfiable'][0]
    assert conflict['targetId'] == 'shared-skill'
    assert conflict['status'] == 'unsatisfiable'
    assert conflict['solver']['satisfiable'] is False
    assert conflict['candidateVersions'] == []
    assert conflict['selectedVersion'] is None
    assert conflict['solver']['recommendedConstraint'] in {'<=0.1.0,>=0.3.0', '>=0.3.0,<=0.1.0'}
    assert conflict['solver']['reasons']
    assert {item['subjectId'] for item in conflict['constraints']} == {'agent-low', 'agent-high'}
    assert report['solution']['status'] == 'unsatisfied'
    assert report['solution']['selected'] == []
    assert report['solution']['reasons']
    assert report['solution']['stats']['backtracks'] >= 1
    assert any(item['kind'] == 'contradiction' for item in report['solution']['trace'])


def test_dependency_graph_validation_rejects_transitive_breakage():
    current_profile = get_profile_registry().get('faros_llm')
    base_skill = SkillSpec(
        id='base-skill',
        name='Base Skill',
        version='0.2.0',
        manifest_version='1.0',
        kind='runtime',
        description='base dependency',
        agent_roles=['researcher'],
        provider_requirements={'storage': 'local'},
    )
    dependent_agent = AgentSpec(
        id='dependent-agent',
        name='Dependent Agent',
        version='0.1.0',
        role='researcher',
        description='depends on base skill',
        default_skills=['base-skill'],
        provider_preferences={'reasoning': 'llm'},
        compatibility={'skills': {'base-skill': '==0.1.0'}},
    )
    compatible_blueprint = Blueprint(
        id='compatible-blueprint',
        name='Compatible Blueprint',
        version='0.1.0',
        workflow=[],
        compatibility={'profiles': {'faros_llm': f'>={current_profile.version}'}},
    )
    with pytest.raises(ValueError, match='dependent-agent'):
        validate_dependency_graph({
            'profiles': [current_profile],
            'agents': [dependent_agent],
            'skills': [base_skill],
            'blueprints': [compatible_blueprint],
        })


def test_skill_registry_upgrade_rolls_back_when_dependency_graph_breaks(tmp_path: Path):
    skills_root = tmp_path / 'installed_skills'
    audit = PackageAuditStore(tmp_path / 'audit')
    registry = SkillRegistry(skills_root, audit_store=audit)

    base_dir = tmp_path / 'base-skill'
    base_dir.mkdir(parents=True, exist_ok=True)
    (base_dir / 'README.md').write_text('# Base Skill\n')
    (base_dir / 'skill.json').write_text(json.dumps({
        'id': 'base-skill',
        'name': 'Base Skill',
        'version': '0.1.0',
        'manifest_version': '1.0',
        'kind': 'runtime',
        'description': 'base dependency',
        'agent_roles': ['researcher'],
        'tags': ['test'],
        'artifact_types': ['run_manifest'],
        'verification_hooks': ['schema-check'],
        'provider_requirements': {'storage': 'local'},
    }, indent=2))
    registry.install_package(base_dir)

    dependent_dir = tmp_path / 'dependent-skill'
    dependent_dir.mkdir(parents=True, exist_ok=True)
    (dependent_dir / 'README.md').write_text('# Dependent Skill\n')
    (dependent_dir / 'skill.json').write_text(json.dumps({
        'id': 'dependent-skill',
        'name': 'Dependent Skill',
        'version': '0.1.0',
        'manifest_version': '1.0',
        'kind': 'runtime',
        'description': 'depends on base skill',
        'agent_roles': ['researcher'],
        'tags': ['test'],
        'artifact_types': ['run_manifest'],
        'verification_hooks': ['schema-check'],
        'provider_requirements': {'storage': 'local'},
        'compatibility': {'skills': {'base-skill': '==0.1.0'}},
    }, indent=2))
    registry.install_package(dependent_dir)

    (base_dir / 'skill.json').write_text(json.dumps({
        'id': 'base-skill',
        'name': 'Base Skill',
        'version': '0.2.0',
        'manifest_version': '1.0',
        'kind': 'runtime',
        'description': 'base dependency',
        'agent_roles': ['researcher'],
        'tags': ['test'],
        'artifact_types': ['run_manifest'],
        'verification_hooks': ['schema-check'],
        'provider_requirements': {'storage': 'local'},
    }, indent=2))

    with pytest.raises(ValueError, match='dependent-skill'):
        registry.install_package(base_dir, overwrite=True)

    restored = registry.get('base-skill')
    assert restored.version == '0.1.0'
    events = registry.audit_log(limit=10)
    actions = [event['action'] for event in events]
    assert 'backup' in actions
    assert 'upgrade' not in actions

def test_skill_registry_can_rollback_to_previous_backup(tmp_path: Path):
    skills_root = tmp_path / 'installed_skills'
    audit = PackageAuditStore(tmp_path / 'audit')
    registry = SkillRegistry(skills_root, audit_store=audit)

    package_dir = tmp_path / 'rollback-skill'
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / 'README.md').write_text('# Rollback Skill\n')
    (package_dir / 'skill.json').write_text(json.dumps({
        'id': 'rollback-skill',
        'name': 'Rollback Skill',
        'version': '0.1.0',
        'manifest_version': '1.0',
        'kind': 'runtime',
        'description': 'rollback test',
        'agent_roles': ['researcher'],
        'tags': ['test'],
        'artifact_types': ['run_manifest'],
        'verification_hooks': ['schema-check'],
        'provider_requirements': {'storage': 'local'},
    }, indent=2))

    registry.install_package(package_dir)
    (package_dir / 'skill.json').write_text(json.dumps({
        'id': 'rollback-skill',
        'name': 'Rollback Skill',
        'version': '0.2.0',
        'manifest_version': '1.0',
        'kind': 'runtime',
        'description': 'rollback test',
        'agent_roles': ['researcher'],
        'tags': ['test'],
        'artifact_types': ['run_manifest'],
        'verification_hooks': ['schema-check'],
        'provider_requirements': {'storage': 'local'},
    }, indent=2))

    upgraded = registry.install_package(package_dir, overwrite=True)
    assert upgraded.version == '0.2.0'

    rolled_back = registry.rollback_package('rollback-skill')
    assert rolled_back.version == '0.1.0'
    events = registry.audit_log()
    actions = [event['action'] for event in events[:3]]
    assert 'rollback' in actions
    assert 'backup' in actions


def test_skill_rollback_api_returns_rolled_back_status(tmp_path: Path):
    from app.faros.registry.skill_registry import SkillRegistry
    registry = get_skill_registry()
    package_dir = tmp_path / 'api-rollback-skill'
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / 'README.md').write_text('# API Rollback Skill\n')
    (package_dir / 'skill.json').write_text(json.dumps({
        'id': 'api-rollback-skill',
        'name': 'API Rollback Skill',
        'version': '0.1.0',
        'manifest_version': '1.0',
        'kind': 'runtime',
        'description': 'rollback api test',
        'agent_roles': ['researcher'],
        'tags': ['test'],
        'artifact_types': ['run_manifest'],
        'verification_hooks': ['schema-check'],
        'provider_requirements': {'storage': 'local'},
    }, indent=2))

    registry.install_package(package_dir, overwrite=False)
    (package_dir / 'skill.json').write_text(json.dumps({
        'id': 'api-rollback-skill',
        'name': 'API Rollback Skill',
        'version': '0.2.0',
        'manifest_version': '1.0',
        'kind': 'runtime',
        'description': 'rollback api test',
        'agent_roles': ['researcher'],
        'tags': ['test'],
        'artifact_types': ['run_manifest'],
        'verification_hooks': ['schema-check'],
        'provider_requirements': {'storage': 'local'},
    }, indent=2))
    registry.install_package(package_dir, overwrite=True)

    response = client.post('/api/faros/skills/rollback', json={'skillId': 'api-rollback-skill'})
    assert response.status_code == 200
    payload = response.json()
    assert payload['status'] == 'rolled_back'
    assert payload['skill']['version'] == '0.1.0'
    registry.uninstall_package('api-rollback-skill')

