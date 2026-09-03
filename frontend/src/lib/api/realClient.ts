// Real API Client - Connects to actual backend
// Uses fetch with baseURL, headers, and error handling

import type { ApiClient } from './client'
import type { Run, PaperDraft, SystemConfig } from '@/lib/types'
import { generateRequestId } from './requestId'
import { createApiError, toApiError, ApiErrorCode } from './errors'
import { logApiRequest } from './telemetry'
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
} from './contracts/models'
import {
  mapRunDTOToRun,
  mapTraceDTOToTrace,
  mapExperimentDTOToExperiment,
  mapArtifactDTOToArtifact,
  mapArtifactPreviewDTOToPreview,
  mapPaperDTOToPaper,
  mapReviewFindingDTOToFinding,
  mapSystemHealthDTOToHealth,
  mapSystemLogDTOToLog,
  mapSystemMetricsDTOToMetrics,
  mapSystemConfigDTOToConfig,
} from './contracts/mappers'

export class RealApiClient implements ApiClient {
  private baseURL: string
  private apiKey?: string

  constructor() {
    this.baseURL = import.meta.env.VITE_API_BASE_URL || ''
    this.apiKey = import.meta.env.VITE_API_KEY
  }

  private async fetchWithError<T>(
    path: string,
    options?: RequestInit
  ): Promise<T> {
    const requestId = generateRequestId('real')
    const url = `${this.baseURL}${path}`
    const startTime = performance.now()
    const method = options?.method || 'GET'

    const headers: HeadersInit = {
      'Content-Type': 'application/json',
      'X-Request-ID': requestId,
      ...(this.apiKey && { 'Authorization': `Bearer ${this.apiKey}` }),
    }

    try {
      const response = await fetch(url, {
        ...options,
        headers: {
          ...headers,
          ...options?.headers,
        },
      })

      const duration = performance.now() - startTime

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        const error = createApiError(
          ApiErrorCode.UNKNOWN,
          errorData.message || `HTTP ${response.status}: ${response.statusText}`,
          requestId,
          errorData.details
        )

        logApiRequest({
          method,
          url: path,
          status: response.status,
          duration,
          requestId,
          error: error.message,
        })

        throw error
      }

      const json = await response.json()

      logApiRequest({
        method,
        url: path,
        status: response.status,
        duration,
        requestId,
      })

      // Support both wrapped { data: T } and raw T responses
      return (json && typeof json === 'object' && 'data' in json) ? json.data : json
    } catch (error) {
      const duration = performance.now() - startTime
      const apiError = toApiError(error, requestId)

      logApiRequest({
        method,
        url: path,
        duration,
        requestId,
        error: apiError.message,
      })

      throw apiError
    }
  }

  // Runs
  async getRuns() {
    const raw = await this.fetchWithError<{ runs: RunDTO[]; total: number } | RunDTO[]>('/api/v1/runs')
    const dtos = Array.isArray(raw) ? raw : (raw.runs ?? [])
    return dtos.map(mapRunDTOToRun)
  }

  async getRun(id: string) {
    const dto = await this.fetchWithError<RunDTO>(`/api/v1/runs/${id}`)
    return mapRunDTOToRun(dto)
  }

  async createRun(config: Run['config']) {
    const dto = await this.fetchWithError<RunDTO>('/api/v1/runs', {
      method: 'POST',
      body: JSON.stringify({ type: 'plan', config }),
    })
    return mapRunDTOToRun(dto)
  }

  async deleteRun(id: string) {
    await this.fetchWithError<void>(`/api/v1/runs/${id}`, {
      method: 'DELETE',
    })
  }

  async cancelRun(id: string) {
    await this.fetchWithError<void>(`/api/v1/runs/${id}/cancel`, {
      method: 'POST',
    })
  }

  // Workflow Traces
  async getTrace(runId: string) {
    const dto = await this.fetchWithError<WorkflowTraceDTO>(`/api/v1/runs/${runId}/trace`)
    return mapTraceDTOToTrace(dto)
  }

  // Experiments
  async getExperiments() {
    const raw = await this.fetchWithError<{ experiments: ExperimentDTO[]; total: number } | ExperimentDTO[]>('/api/v1/experiments')
    const dtos = Array.isArray(raw) ? raw : (raw.experiments ?? [])
    return dtos.map(mapExperimentDTOToExperiment)
  }

  async getExperiment(id: string) {
    const dto = await this.fetchWithError<ExperimentDTO>(`/api/v1/experiments/${id}`)
    return mapExperimentDTOToExperiment(dto)
  }

  async compareExperiments(ids: string[]) {
    const query = ids.map(id => `ids[]=${id}`).join('&')
    const dtos = await this.fetchWithError<ExperimentDTO[]>(`/api/v1/experiments/compare?${query}`)
    return dtos.map(mapExperimentDTOToExperiment)
  }

  // Artifacts
  async getArtifacts(filters?: { type?: string; runId?: string }) {
    const params = new URLSearchParams()
    if (filters?.type) params.append('type', filters.type)
    if (filters?.runId) params.append('runId', filters.runId)
    const query = params.toString() ? `?${params.toString()}` : ''
    const dtos = await this.fetchWithError<ArtifactDTO[]>(`/api/v1/artifacts${query}`)
    return dtos.map(mapArtifactDTOToArtifact)
  }

  async getArtifact(id: string) {
    const dto = await this.fetchWithError<ArtifactDTO>(`/api/v1/artifacts/${id}`)
    return mapArtifactDTOToArtifact(dto)
  }

  async getArtifactPreview(id: string) {
    const dto = await this.fetchWithError<ArtifactPreviewDTO>(`/api/v1/artifacts/${id}/preview`)
    return mapArtifactPreviewDTOToPreview(dto)
  }

  // Papers
  async getPapers() {
    const raw = await this.fetchWithError<{ papers: PaperDTO[]; total: number } | PaperDTO[]>('/api/v1/papers')
    const dtos = Array.isArray(raw) ? raw : (raw.papers ?? [])
    return dtos.map(mapPaperDTOToPaper)
  }

  async getPaper(id: string) {
    const dto = await this.fetchWithError<PaperDTO>(`/api/v1/papers/${id}`)
    return mapPaperDTOToPaper(dto)
  }

  async createPaper(paper: Partial<PaperDraft>) {
    const dto = await this.fetchWithError<PaperDTO>('/api/v1/papers', {
      method: 'POST',
      body: JSON.stringify(paper),
    })
    return mapPaperDTOToPaper(dto)
  }

  async updatePaper(id: string, updates: Partial<PaperDraft>) {
    const dto = await this.fetchWithError<PaperDTO>(`/api/v1/papers/${id}`, {
      method: 'PUT',
      body: JSON.stringify(updates),
    })
    return mapPaperDTOToPaper(dto)
  }

  async exportPaper(id: string, format: 'pdf' | 'latex' | 'docx') {
    const response = await this.fetchWithError<{ url: string }>(
      `/api/v1/papers/${id}/export?format=${format}`
    )
    return response
  }

  // Review
  async getReviewFindings(paperId?: string) {
    const query = paperId ? `?paperId=${paperId}` : ''
    const dtos = await this.fetchWithError<ReviewFindingDTO[]>(`/api/v1/reviews/reviewx/findings${query}`)
    return dtos.map(mapReviewFindingDTOToFinding)
  }

  async runConsistencyCheck(input: string | {
    paperId: string
    budgetMode?: string
    visualAuditEnabled?: boolean
    visualModel?: string
  }) {
    const paperId = typeof input === 'string' ? input : input.paperId
    const budgetMode = typeof input === 'string' ? 'balanced' : input.budgetMode ?? 'balanced'
    const visualAuditEnabled = typeof input === 'string' ? false : input.visualAuditEnabled ?? false
    const visualModel = typeof input === 'string' ? undefined : input.visualModel
    const dtos = await this.fetchWithError<ReviewFindingDTO[]>('/api/v1/reviews/reviewx/run', {
      method: 'POST',
      body: JSON.stringify({ paperId, budgetMode, visualAuditEnabled, visualModel }),
    })
    return dtos.map(mapReviewFindingDTOToFinding)
  }

  // System
  async getSystemHealth() {
    const raw = await this.fetchWithError<SystemHealthDTO | Record<string, unknown>>('/api/system/health')
    // Backend may return simple { status, service, version } or full SystemHealthDTO
    if ('components' in raw) {
      return mapSystemHealthDTOToHealth(raw as SystemHealthDTO)
    }
    // Map simple health response to expected SystemHealth shape
    const r = raw as Record<string, unknown>
    const status = typeof r.status === 'string' ? r.status : ''
    const service = typeof r.service === 'string' ? r.service : 'backend'
    const version = typeof r.version === 'string' || typeof r.version === 'number' ? String(r.version) : 'unknown'
    const isHealthy = status === 'healthy'
    return {
      status: (isHealthy ? 'healthy' : 'degraded') as 'healthy' | 'degraded' | 'down',
      lastChecked: new Date().toISOString(),
      components: [{
        name: service,
        status: (isHealthy ? 'ok' : 'down') as 'ok' | 'degraded' | 'down',
        lastCheck: new Date().toISOString(),
        message: `v${version}`,
      }],
    }
  }

  async getSystemLogs() {
    const dtos = await this.fetchWithError<SystemLogDTO[]>('/api/system/logs')
    return dtos.map(mapSystemLogDTOToLog)
  }

  async getSystemMetrics(timeRange?: string) {
    const query = timeRange ? `?timeRange=${timeRange}` : ''
    const dto = await this.fetchWithError<SystemMetricsDTO>(`/api/system/metrics${query}`)
    return mapSystemMetricsDTOToMetrics(dto)
  }

  async getSystemConfig() {
    const dto = await this.fetchWithError<SystemConfigDTO>('/api/system/config')
    return mapSystemConfigDTOToConfig(dto)
  }

  async updateSystemConfig(config: Partial<SystemConfig>) {
    const dto = await this.fetchWithError<SystemConfigDTO>('/api/system/config', {
      method: 'PUT',
      body: JSON.stringify(config),
    })
    return mapSystemConfigDTOToConfig(dto)
  }

  // Streaming interface (placeholder for future SSE implementation)
  subscribeRunEvents(
    _runId: string,
    _onEvent: (event: unknown) => void
  ): () => void {
    // Placeholder for SSE subscription
    // Future implementation will use EventSource for /api/runs/:id/stream
    console.warn('subscribeRunEvents not yet implemented in realClient')

    throw createApiError(
      ApiErrorCode.NOT_IMPLEMENTED,
      'Real-time streaming not yet implemented. Use polling with refetchInterval instead.',
      generateRequestId('real')
    )
  }
}
