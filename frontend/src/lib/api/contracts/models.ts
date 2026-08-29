// API Data Transfer Objects (DTOs)
// Backend-facing types that will be returned by the real API

export interface RunDTO {
  id: string
  type: 'plan' | 'idea'
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
  startedAt: string
  endedAt?: string
  duration?: number
  config: RunConfigDTO
  traceId?: string
}

export interface RunConfigDTO {
  instancePath?: string | null
  taskLevel: string
  paperType?: string | null
  model: string
  workplaceName: string
  cachePath: string
  port: number
  maxIterTimes: number
  category?: string
  categoryGroup?: 'post-training' | 'inference'
  categoryDirectionId?: string
  templateId?: string
  ideas?: string
  references?: string
}

export interface WorkflowTraceDTO {
  runId: string
  workdir: string
  startedAt: string
  endedAt: string
  totalSteps: number
  successfulSteps: number
  failedSteps: number
  artifacts: string[]
  steps: StepResultDTO[]
}

export interface StepResultDTO {
  name: string
  status: 'ok' | 'skipped' | 'failed'
  inputs: Record<string, unknown>
  outputs: Record<string, unknown>
  artifacts: string[]
  startedAt: string
  endedAt: string
  error?: string
  durationSeconds: number
}

export interface ExperimentDTO {
  id: string
  runId: string
  runIds: string[]
  name: string
  description?: string
  domain: string
  task: string
  metrics: MetricValueDTO[]
  parameters: Record<string, unknown>
  createdAt: string
  updatedAt: string
  status: 'running' | 'completed' | 'failed'
}

export interface MetricValueDTO {
  name: string
  value: number
  mean: number
  std?: number
  unit?: string
  timestamp: string
}

export interface ArtifactDTO {
  id: string
  runId: string
  type: 'paper' | 'code' | 'repository' | 'log' | 'data' | 'model'
  path: string
  filename: string
  size: number
  mimeType: string
  createdAt: string
  metadata?: Record<string, unknown>
}

export interface ArtifactPreviewDTO {
  artifactId: string
  content: string
  language?: string
  rendered?: string
}

export interface PaperDTO {
  id: string
  runId?: string
  title: string
  authors?: string[]
  sections?: PaperSectionDTO[]
  bibliography?: string
  createdAt?: string
  updatedAt?: string
  status?: 'created' | 'generating' | 'completed' | 'failed' | 'draft' | 'review' | 'final'
  outlineJson?: {
    authors?: string[]
    sections?: PaperSectionDTO[]
  } | null
}

export interface PaperSectionDTO {
  id: string
  type: 'abstract' | 'introduction' | 'methodology' | 'experiments' | 'results' | 'conclusion' | 'related_work' | 'preliminaries'
  title: string
  content: string
  order: number
}

export interface ReviewFindingDTO {
  id: string
  paperId: string
  relatedRunId?: string
  relatedArtifactId?: string
  type: 'consistency' | 'citation' | 'formatting' | 'content'
  severity: 'blocker' | 'major' | 'minor' | 'info'
  title: string
  description: string
  evidence?: string
  riskType?: string
  claimId?: string
  evidenceIds?: string[]
  targetModule?: string
  confidence?: number
  supportStatus?: string
  verifierIds?: string[]
  reviewerDecision?: string
  reviewerAssessment?: string
  reviewerModel?: string
  cemCalibration?: Record<string, unknown>
  revisionRequestIds?: string[]
  revisionStatus?: string
  location?: {
    section: string
    line?: number
  }
  suggestedFix?: string
}

export interface ComponentHealthDTO {
  name: string
  status: 'ok' | 'degraded' | 'down'
  lastCheck: string
  latency?: number
  message?: string
  uptime?: number
  metrics?: Record<string, number>
}

export interface SystemHealthDTO {
  status: 'healthy' | 'degraded' | 'down'
  components: ComponentHealthDTO[]
  lastChecked: string
}

export interface SystemLogDTO {
  timestamp: string
  level: 'debug' | 'info' | 'warn' | 'error'
  message: string
  source?: string
  metadata?: Record<string, unknown>
}

export interface SystemMetricsDTO {
  cpu: {
    current: number
    average: number
    peak: number
  }
  memory: {
    used: number
    total: number
    usedPercent: number
  }
  disk: {
    used: number
    total: number
    usedPercent: number
  }
  network: {
    in: number
    out: number
  }
  timestamp: string
}

export interface LLMConfigDTO {
  defaultModel: string
  cheapModel: string
  providers: {
    name: string
    enabled: boolean
    apiKey?: string
    baseUrl?: string
    models: string[]
  }[]
}

export interface WorkspaceConfigDTO {
  workplaceName: string
  containerName: string
  port: number
  cachePath: string
  dataPath: string
}

export interface UserPreferencesDTO {
  theme: 'light' | 'dark' | 'system'
  language: string
  timezone: string
  notifications: boolean
  autoSave: boolean
}

export interface SystemConfigDTO {
  llm: LLMConfigDTO
  workspace: WorkspaceConfigDTO
  preferences: UserPreferencesDTO
}

// Request/Response wrappers
export interface ApiResponse<T> {
  data: T
  requestId: string
  timestamp: string
}

export interface ApiErrorResponse {
  error: {
    code: string
    message: string
    details?: Record<string, unknown>
  }
  requestId: string
  timestamp: string
}

export interface CreateRunRequest {
  type: 'plan' | 'idea'
  config: Omit<RunConfigDTO, 'cachePath' | 'port'>
}

export interface CreateRunResponse {
  runId: string
  status: string
}
