from typing import Any, Iterable

from app.faros.models.capability import CapabilityResult
from app.faros.models.verification import VerificationResult, VerificationSuiteResult
from app.faros.verification.base import BaseVerifier


class StatusVerifier(BaseVerifier):
    verifier_id = 'status'
    name = 'Status Verifier'
    description = 'Ensures a capability completed successfully.'
    default_enabled = True
    tags = ['runtime', 'status']

    def verify(self, capability_id: str, result: CapabilityResult, **context: Any) -> VerificationResult:
        if result.status != 'completed':
            return VerificationResult(
                rule_id=f'{capability_id}:status',
                status='failed',
                message=f'{capability_id} did not complete successfully',
                details={'resultStatus': result.status},
            )
        return VerificationResult(
            rule_id=f'{capability_id}:status',
            status='passed',
            message=f'{capability_id} reported completed status',
        )


class OutputSchemaVerifier(BaseVerifier):
    verifier_id = 'required_outputs'
    name = 'Required Outputs Verifier'
    description = 'Checks that required node outputs were produced.'
    default_enabled = True
    tags = ['runtime', 'outputs']

    def verify(self, capability_id: str, result: CapabilityResult, **context: Any) -> VerificationResult:
        required = list(context.get('required_outputs', []) or [])
        missing = [key for key in required if key not in result.outputs]
        if missing:
            return VerificationResult(
                rule_id=f'{capability_id}:outputs',
                status='failed',
                message=f'{capability_id} result is missing required outputs',
                details={'missing': missing},
            )
        return VerificationResult(
            rule_id=f'{capability_id}:outputs',
            status='passed',
            message=f'{capability_id} satisfied required outputs',
            details={'required': required},
        )


class ArtifactContractVerifier(BaseVerifier):
    verifier_id = 'artifact_contract'
    name = 'Artifact Contract Verifier'
    description = 'Checks required artifact types for one capability result.'
    default_enabled = True
    tags = ['runtime', 'artifacts']

    def verify(self, capability_id: str, result: CapabilityResult, **context: Any) -> VerificationResult:
        expected = list(context.get('expected_artifact_types', []) or [])
        produced = [artifact.type for artifact in result.artifacts]
        missing = [artifact_type for artifact_type in expected if artifact_type not in produced]
        if missing:
            return VerificationResult(
                rule_id=f'{capability_id}:artifacts',
                status='failed',
                message=f'{capability_id} did not emit expected artifact types',
                details={'expected': expected, 'produced': produced, 'missing': missing},
            )
        return VerificationResult(
            rule_id=f'{capability_id}:artifacts',
            status='passed',
            message=f'{capability_id} emitted expected artifact types',
            details={'expected': expected, 'produced': produced},
        )


class ArtifactSchemaVerifier(BaseVerifier):
    verifier_id = 'artifact_schema'
    name = 'Artifact Schema Verifier'
    description = 'Checks artifact metadata, producer, and URI contracts.'
    default_enabled = True
    tags = ['runtime', 'artifacts', 'schema']

    def verify(self, capability_id: str, result: CapabilityResult, **context: Any) -> VerificationResult:
        schema_map = context.get('artifact_schema_map', {}) or {}
        problems: list[dict[str, Any]] = []
        for artifact in result.artifacts:
            schema = schema_map.get(artifact.type)
            if not schema:
                problems.append({'artifactType': artifact.type, 'reason': 'unregistered artifact type'})
                continue
            missing_metadata = [key for key in schema.required_metadata if key not in artifact.metadata]
            if missing_metadata:
                problems.append({
                    'artifactType': artifact.type,
                    'reason': 'missing metadata',
                    'missingMetadata': missing_metadata,
                })
            if schema.required_producer and artifact.producer != schema.required_producer:
                problems.append({
                    'artifactType': artifact.type,
                    'reason': 'unexpected producer',
                    'expectedProducer': schema.required_producer,
                    'actualProducer': artifact.producer,
                })
            if schema.allowed_uri_prefixes and not any(str(artifact.uri).startswith(prefix) for prefix in schema.allowed_uri_prefixes):
                problems.append({
                    'artifactType': artifact.type,
                    'reason': 'uri prefix mismatch',
                    'allowedPrefixes': list(schema.allowed_uri_prefixes),
                    'uri': artifact.uri,
                })
        if problems:
            return VerificationResult(
                rule_id=f'{capability_id}:artifact-schema',
                status='failed',
                message=f'{capability_id} emitted artifacts that violate schema contracts',
                details={'problems': problems},
            )
        return VerificationResult(
            rule_id=f'{capability_id}:artifact-schema',
            status='passed',
            message=f'{capability_id} artifacts satisfied schema contracts',
        )


class RuntimeMetadataVerifier(BaseVerifier):
    verifier_id = 'runtime_metadata'
    name = 'Runtime Metadata Verifier'
    description = 'Checks that runtime agent/skill/provider metadata was attached.'
    default_enabled = True
    tags = ['runtime', 'metadata']

    def verify(self, capability_id: str, result: CapabilityResult, **context: Any) -> VerificationResult:
        verification = result.verification or {}
        missing = [key for key in ('agentId', 'skillIds') if key not in verification]
        if missing:
            return VerificationResult(
                rule_id=f'{capability_id}:runtime-metadata',
                status='failed',
                message=f'{capability_id} result is missing runtime metadata',
                details={'missing': missing},
            )
        return VerificationResult(
            rule_id=f'{capability_id}:runtime-metadata',
            status='passed',
            message=f'{capability_id} included runtime metadata',
        )


class ReviewActionItemsVerifier(BaseVerifier):
    verifier_id = 'review_action_items'
    name = 'Review Action Items Verifier'
    description = 'Ensures review-style outputs include actionable follow-up items.'
    default_enabled = False
    tags = ['review', 'quality']

    def verify(self, capability_id: str, result: CapabilityResult, **context: Any) -> VerificationResult:
        outputs = result.outputs or {}
        if capability_id != 'reviewer_simulation':
            return VerificationResult(
                rule_id=f'{capability_id}:review-action-items',
                status='passed',
                message=f'{capability_id} does not require review action item checks',
            )
        action_items = outputs.get('actionItems') or []
        if not action_items:
            return VerificationResult(
                rule_id=f'{capability_id}:review-action-items',
                status='failed',
                message='reviewer_simulation did not produce any action items',
            )
        return VerificationResult(
            rule_id=f'{capability_id}:review-action-items',
            status='passed',
            message='reviewer_simulation produced actionable review items',
            details={'actionItemCount': len(action_items)},
        )


class VerifierDispatcher:
    """Run a selected chain of verification rules and aggregate the result."""

    def __init__(self, verifiers: Iterable[BaseVerifier] | None = None, registry=None):
        if registry is None:
            from app.faros.registry.verifier_registry import get_verifier_registry
            registry = get_verifier_registry()
        self.registry = registry
        if verifiers:
            self.verifiers = {verifier.verifier_id: verifier for verifier in verifiers}
        else:
            self.verifiers = {item['id']: self.registry.get(item['id']) for item in self.registry.list()}

    def _resolve_verifier_ids(
        self,
        verifier_ids: list[str] | None = None,
        pack_ids: list[str] | None = None,
        disabled_verifier_ids: list[str] | None = None,
    ) -> list[str]:
        disabled = set(disabled_verifier_ids or [])
        if verifier_ids is None and pack_ids is None:
            ordered = self.registry.default_pack()
        else:
            ordered = self.registry.expand(verifier_ids=verifier_ids or [], pack_ids=pack_ids or [])
        return [verifier_id for verifier_id in ordered if verifier_id not in disabled]

    def verify(
        self,
        capability_id: str,
        result: CapabilityResult,
        verifier_ids: list[str] | None = None,
        pack_ids: list[str] | None = None,
        disabled_verifier_ids: list[str] | None = None,
        **context: Any,
    ) -> VerificationSuiteResult:
        resolved_ids = self._resolve_verifier_ids(verifier_ids, pack_ids, disabled_verifier_ids)
        results = [self.registry.get(verifier_id).verify(capability_id, result, **context) for verifier_id in resolved_ids]
        failed = [item for item in results if item.status != 'passed']
        if failed:
            return VerificationSuiteResult(
                status='failed',
                message=f'{capability_id} failed {len(failed)} verification rule(s)',
                verifier_ids=resolved_ids,
                results=results,
            )
        return VerificationSuiteResult(
            status='passed',
            message=f'{capability_id} passed verification dispatch',
            verifier_ids=resolved_ids,
            results=results,
        )
