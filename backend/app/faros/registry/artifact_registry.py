from typing import Dict, List

from app.faros.models.artifact import ArtifactSchemaSpec


class ArtifactRegistry:
    """Registry of artifact schema contracts available to FAROS."""

    def __init__(self):
        self._schemas: Dict[str, ArtifactSchemaSpec] = {}

    def register(self, schema: ArtifactSchemaSpec) -> None:
        self._schemas[schema.type] = schema

    def get(self, artifact_type: str) -> ArtifactSchemaSpec:
        if artifact_type not in self._schemas:
            raise KeyError(f"Artifact type '{artifact_type}' is not registered")
        return self._schemas[artifact_type]

    def list(self) -> List[ArtifactSchemaSpec]:
        return list(self._schemas.values())


_registry: ArtifactRegistry | None = None


def get_artifact_registry() -> ArtifactRegistry:
    global _registry
    if _registry is None:
        _registry = ArtifactRegistry()
        _registry.register(ArtifactSchemaSpec(
            type='idea_session',
            description='Stored idea session output from the idea module.',
            required_metadata=['sessionId', 'selectedCandidateId'],
            allowed_uri_prefixes=['idea://'],
            required_producer='idea_refinement',
        ))
        _registry.register(ArtifactSchemaSpec(
            type='code_project',
            description='Provisioned project workspace for experiment execution.',
            required_metadata=['projectId', 'language', 'framework'],
            allowed_uri_prefixes=['project://'],
            required_producer='experiment',
        ))
        _registry.register(ArtifactSchemaSpec(
            type='experiment_record',
            description='Experiment record linked to a project workspace.',
            required_metadata=['experimentId', 'projectId'],
            allowed_uri_prefixes=['experiment://'],
            required_producer='experiment',
        ))
        _registry.register(ArtifactSchemaSpec(
            type='paper_record',
            description='Persistent paper record emitted by paper drafting.',
            required_metadata=['paperId', 'status'],
            allowed_uri_prefixes=['paper://'],
            required_producer='paper_drafting',
        ))
        _registry.register(ArtifactSchemaSpec(
            type='latex_project',
            description='Local LaTeX project directory.',
            required_metadata=['paperId', 'fileCount'],
            allowed_uri_prefixes=['/', '.'],
            required_producer='paper_drafting',
        ))
        _registry.register(ArtifactSchemaSpec(
            type='latex_zip',
            description='Bundled LaTeX ZIP archive.',
            required_metadata=['paperId'],
            allowed_uri_prefixes=['/', '.'],
            required_producer='paper_drafting',
        ))
        _registry.register(ArtifactSchemaSpec(
            type='paper_pdf',
            description='Compiled paper PDF output.',
            required_metadata=['paperId'],
            allowed_uri_prefixes=['/', '.'],
            required_producer='paper_drafting',
        ))
        _registry.register(ArtifactSchemaSpec(
            type='review_report',
            description='Structured reviewer simulation output.',
            required_metadata=['reviewId', 'paperId'],
            allowed_uri_prefixes=['review://'],
            required_producer='reviewer_simulation',
        ))
        _registry.register(ArtifactSchemaSpec(
            type='literature_notes',
            description='Intermediate literature evidence notes.',
            required_metadata=[],
            allowed_uri_prefixes=[],
        ))
        _registry.register(ArtifactSchemaSpec(
            type='paper_outline',
            description='Structured paper outline artifact.',
            required_metadata=[],
            allowed_uri_prefixes=[],
        ))
        _registry.register(ArtifactSchemaSpec(
            type='consistency_report',
            description='Consistency verification report.',
            required_metadata=[],
            allowed_uri_prefixes=[],
        ))
        _registry.register(ArtifactSchemaSpec(
            type='run_manifest',
            description='Runtime-level artifact packaging manifest.',
            required_metadata=[],
            allowed_uri_prefixes=[],
        ))
    return _registry
