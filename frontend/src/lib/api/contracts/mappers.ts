// API Mappers - Convert between DTOs and UI domain types

import type {
  RunDTO,
  WorkflowTraceDTO,
  ExperimentDTO,
  ArtifactDTO,
  ArtifactPreviewDTO,
  PaperDTO,
  ReviewFindingDTO,
  SystemHealthDTO,
  SystemLogDTO,
  SystemMetricsDTO,
  SystemConfigDTO,
} from './models'

import type {
  Run,
  WorkflowTrace,
  ExperimentRecord,
  Artifact,
  ArtifactPreview,
  PaperDraft,
  ReviewFinding,
  SystemHealth,
  LogEntry,
  SystemMetrics,
  SystemConfig,
} from '@/lib/types'

// Run mappers
export function mapRunDTOToRun(dto: RunDTO): Run {
  // Defensive check for null/undefined dto
  if (!dto) {
    throw new Error('Cannot map null/undefined RunDTO to Run')
  }

  // Defensive check for config
  const config = dto.config || {}

  return {
    id: dto.id,
    type: dto.type,
    status: dto.status,
    startedAt: dto.startedAt || '',
    endedAt: dto.endedAt,
    duration: dto.duration,
    config: {
      instancePath: config.instancePath || undefined,
      taskLevel: config.taskLevel || 'task1',
      paperType: config.paperType || undefined,
      model: config.model || '',
      workplaceName: config.workplaceName || '',
      cachePath: config.cachePath || 'cache',
      port: config.port || 8000,
      maxIterTimes: config.maxIterTimes || 10,
      category: config.category as 'reasoning' | 'vq' | 'gnn' | 'recommendation' | 'diffu_flow' | undefined,
      categoryGroup: config.categoryGroup,
      categoryDirectionId: config.categoryDirectionId,
      templateId: config.templateId,
      ideas: config.ideas,
      references: config.references,
    },
    artifacts: [],
    experiments: [],
  }
}

export function mapRunToRunDTO(run: Run): RunDTO {
  return {
    id: run.id,
    type: run.type,
    status: run.status,
    startedAt: run.startedAt,
    endedAt: run.endedAt,
    duration: run.duration,
    config: {
      instancePath: run.config.instancePath,
      taskLevel: run.config.taskLevel,
      paperType: run.config.paperType,
      model: run.config.model,
      workplaceName: run.config.workplaceName,
      cachePath: run.config.cachePath,
      port: run.config.port,
      maxIterTimes: run.config.maxIterTimes,
      category: run.config.category,
      categoryGroup: run.config.categoryGroup,
      categoryDirectionId: run.config.categoryDirectionId,
      templateId: run.config.templateId,
      ideas: run.config.ideas,
      references: run.config.references,
    },
  }
}

// Trace mappers
export function mapTraceDTOToTrace(dto: WorkflowTraceDTO): WorkflowTrace {
  return {
    runId: dto.runId,
    workdir: dto.workdir,
    startedAt: dto.startedAt,
    endedAt: dto.endedAt,
    totalSteps: dto.totalSteps,
    successfulSteps: dto.successfulSteps,
    failedSteps: dto.failedSteps,
    artifacts: dto.artifacts,
    steps: dto.steps.map(step => ({
      name: step.name,
      status: step.status,
      inputs: step.inputs,
      outputs: step.outputs,
      artifacts: step.artifacts,
      startedAt: step.startedAt,
      endedAt: step.endedAt,
      error: step.error,
      durationSeconds: step.durationSeconds,
    })),
  }
}

// Experiment mappers
export function mapExperimentDTOToExperiment(dto: ExperimentDTO): ExperimentRecord {
  return {
    id: dto.id,
    runId: dto.runId,
    runIds: dto.runIds,
    name: dto.name,
    description: dto.description,
    domain: dto.domain,
    task: dto.task,
    metrics: dto.metrics.map(m => ({
      name: m.name,
      value: m.value,
      mean: m.mean,
      std: m.std,
      unit: m.unit,
      timestamp: m.timestamp,
    })),
    parameters: dto.parameters,
    createdAt: dto.createdAt,
    updatedAt: dto.updatedAt,
    status: dto.status,
  }
}

// Artifact mappers
export function mapArtifactDTOToArtifact(dto: ArtifactDTO): Artifact {
  return {
    id: dto.id,
    runId: dto.runId,
    type: dto.type,
    path: dto.path,
    filename: dto.filename,
    size: dto.size,
    mimeType: dto.mimeType,
    createdAt: dto.createdAt,
    metadata: dto.metadata,
  }
}

export function mapArtifactPreviewDTOToPreview(dto: ArtifactPreviewDTO): ArtifactPreview {
  return {
    artifactId: dto.artifactId,
    content: dto.content,
    language: dto.language,
    rendered: dto.rendered,
  }
}

// Paper mappers
export function mapPaperDTOToPaper(dto: PaperDTO): PaperDraft {
  const sections = Array.isArray(dto.sections)
    ? dto.sections
    : Array.isArray(dto.outlineJson?.sections)
      ? dto.outlineJson.sections
      : []

  const status = dto.status === 'completed'
    ? 'final'
    : dto.status === 'created' || dto.status === 'generating'
      ? 'draft'
      : dto.status === 'failed'
        ? 'draft'
        : dto.status ?? 'draft'

  return {
    id: dto.id,
    runId: dto.runId,
    title: dto.title,
    authors: dto.authors ?? dto.outlineJson?.authors ?? [],
    sections: sections.map(s => ({
      id: s.id,
      type: s.type,
      title: s.title,
      content: s.content,
      order: s.order,
    })),
    bibliography: dto.bibliography,
    createdAt: dto.createdAt ?? new Date(0).toISOString(),
    updatedAt: dto.updatedAt ?? dto.createdAt ?? new Date(0).toISOString(),
    status,
  }
}

// Review mappers
export function mapReviewFindingDTOToFinding(dto: ReviewFindingDTO): ReviewFinding {
  return {
    id: dto.id,
    paperId: dto.paperId,
    relatedRunId: dto.relatedRunId,
    relatedArtifactId: dto.relatedArtifactId,
    type: dto.type,
    severity: dto.severity,
    title: dto.title,
    description: dto.description,
    evidence: dto.evidence,
    riskType: dto.riskType,
    claimId: dto.claimId,
    evidenceIds: dto.evidenceIds,
    targetModule: dto.targetModule,
    confidence: dto.confidence,
    supportStatus: dto.supportStatus,
    verifierIds: dto.verifierIds,
    location: dto.location,
    suggestedFix: dto.suggestedFix,
  }
}

// System mappers
export function mapSystemHealthDTOToHealth(dto: SystemHealthDTO): SystemHealth {
  return {
    status: dto.status,
    components: dto.components.map(c => ({
      name: c.name,
      status: c.status,
      lastCheck: c.lastCheck,
      latency: c.latency,
      message: c.message,
      uptime: c.uptime,
      metrics: c.metrics,
    })),
    lastChecked: dto.lastChecked,
  }
}

export function mapSystemLogDTOToLog(dto: SystemLogDTO): LogEntry {
  return {
    timestamp: dto.timestamp,
    level: dto.level,
    message: dto.message,
    source: dto.source,
    metadata: dto.metadata,
  }
}

export function mapSystemMetricsDTOToMetrics(dto: SystemMetricsDTO): SystemMetrics {
  return {
    cpu: dto.cpu,
    memory: dto.memory,
    disk: dto.disk,
    network: dto.network,
    timestamp: dto.timestamp,
  }
}

export function mapSystemConfigDTOToConfig(dto: SystemConfigDTO): SystemConfig {
  return {
    llm: {
      defaultModel: dto.llm.defaultModel,
      cheapModel: dto.llm.cheapModel,
      providers: dto.llm.providers.map(p => ({
        name: p.name,
        enabled: p.enabled,
        apiKey: p.apiKey,
        baseUrl: p.baseUrl,
        models: p.models,
      })),
    },
    workspace: dto.workspace,
    preferences: dto.preferences,
  }
}
