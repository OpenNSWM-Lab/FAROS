import type {
  Run,
  WorkflowTrace,
  ExperimentRecord,
  Artifact,
  PaperDraft,
  ReviewFinding,
  SystemHealth,
  LogEntry,
  SystemMetrics,
  SystemConfig,
} from '@/lib/types'

export interface ApiClient {
  // Runs
  getRuns(): Promise<Run[]>
  getRun(id: string): Promise<Run>
  createRun(config: Run['config']): Promise<Run>
  cancelRun(id: string): Promise<void>

  // Workflow Traces
  getTrace(runId: string): Promise<WorkflowTrace>

  // Experiments
  getExperiments(): Promise<ExperimentRecord[]>
  getExperiment(id: string): Promise<ExperimentRecord>
  compareExperiments(ids: string[]): Promise<ExperimentRecord[]>

  // Artifacts
  getArtifacts(filters?: { type?: string; runId?: string }): Promise<Artifact[]>
  getArtifact(id: string): Promise<Artifact>
  getArtifactPreview(id: string): Promise<{ content: string; language?: string }>

  // Papers
  getPapers(): Promise<PaperDraft[]>
  getPaper(id: string): Promise<PaperDraft>
  updatePaper(id: string, paper: Partial<PaperDraft>): Promise<PaperDraft>
  exportPaper(id: string, format: 'pdf' | 'latex' | 'docx'): Promise<{ url: string }>

  // Review
  getReviewFindings(paperId: string): Promise<ReviewFinding[]>
  runConsistencyCheck(input: string | { paperId: string; budgetMode?: string }): Promise<ReviewFinding[]>

  // System
  getSystemHealth(): Promise<SystemHealth>
  getSystemLogs(filters?: { level?: string; limit?: number }): Promise<LogEntry[]>
  getSystemMetrics(timeRange?: string): Promise<SystemMetrics>

  // Settings
  getSystemConfig(): Promise<SystemConfig>
  updateSystemConfig(config: Partial<SystemConfig>): Promise<SystemConfig>
}
