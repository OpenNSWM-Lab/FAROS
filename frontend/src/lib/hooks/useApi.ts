import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { Run, PaperDraft, SystemConfig } from '@/lib/types'

// Query keys
export const queryKeys = {
  runs: ['runs'] as const,
  run: (id: string) => ['runs', id] as const,
  trace: (runId: string) => ['traces', runId] as const,
  experiments: ['experiments'] as const,
  experiment: (id: string) => ['experiments', id] as const,
  artifacts: (filters?: { type?: string; runId?: string }) => ['artifacts', filters] as const,
  artifact: (id: string) => ['artifacts', id] as const,
  artifactPreview: (id: string) => ['artifacts', id, 'preview'] as const,
  papers: ['papers'] as const,
  paper: (id: string) => ['papers', id] as const,
  reviewFindings: (paperId: string) => ['review', 'findings', paperId] as const,
  reviewerSimulations: (paperId: string) => ['review', 'simulations', paperId] as const,
  systemHealth: ['system', 'health'] as const,
  systemLogs: (filters?: { level?: string; limit?: number }) => ['system', 'logs', filters] as const,
  systemMetrics: (timeRange?: string) => ['system', 'metrics', timeRange] as const,
  systemConfig: ['system', 'config'] as const,
}

// Runs
export function useRuns(options?: { refetchInterval?: number | false }) {
  return useQuery({
    queryKey: queryKeys.runs,
    queryFn: () => api.getRuns(),
    refetchInterval: options?.refetchInterval,
  })
}

export function useRun(id: string, refetchInterval?: number) {
  return useQuery({
    queryKey: queryKeys.run(id),
    queryFn: () => api.getRun(id),
    enabled: !!id,
    refetchInterval,
  })
}

export function useTrace(runId: string) {
  return useQuery({
    queryKey: queryKeys.trace(runId),
    queryFn: () => api.getTrace(runId),
    enabled: !!runId,
  })
}

export function useCreateRun() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (config: Run['config']) => api.createRun(config),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.runs })
    },
  })
}

export function useCancelRun() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (id: string) => api.cancelRun(id),
    onSuccess: (_data, id) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.run(id) })
      queryClient.invalidateQueries({ queryKey: queryKeys.runs })
    },
  })
}

// Experiments
export function useExperiments() {
  return useQuery({
    queryKey: queryKeys.experiments,
    queryFn: () => api.getExperiments(),
  })
}

export function useExperiment(id: string) {
  return useQuery({
    queryKey: queryKeys.experiment(id),
    queryFn: () => api.getExperiment(id),
    enabled: !!id,
  })
}

export function useCompareExperiments(ids: string[]) {
  return useQuery({
    queryKey: ['experiments', 'compare', ...ids],
    queryFn: () => api.compareExperiments(ids),
    enabled: ids.length > 0,
  })
}

// Artifacts
export function useArtifacts(filters?: { type?: string; runId?: string }) {
  return useQuery({
    queryKey: queryKeys.artifacts(filters),
    queryFn: () => api.getArtifacts(filters),
  })
}

export function useArtifact(id: string) {
  return useQuery({
    queryKey: queryKeys.artifact(id),
    queryFn: () => api.getArtifact(id),
    enabled: !!id,
  })
}

export function useArtifactPreview(id: string) {
  return useQuery({
    queryKey: queryKeys.artifactPreview(id),
    queryFn: () => api.getArtifactPreview(id),
    enabled: !!id,
  })
}

// Papers
export function usePapers() {
  return useQuery({
    queryKey: queryKeys.papers,
    queryFn: () => api.getPapers(),
  })
}

export function usePaper(id: string) {
  return useQuery({
    queryKey: queryKeys.paper(id),
    queryFn: () => api.getPaper(id),
    enabled: !!id,
  })
}

export function useUpdatePaper() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ id, updates }: { id: string; updates: Partial<PaperDraft> }) =>
      api.updatePaper(id, updates),
    onSuccess: (_data, { id }) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.paper(id) })
      queryClient.invalidateQueries({ queryKey: queryKeys.papers })
    },
  })
}

export function useExportPaper() {
  return useMutation({
    mutationFn: ({ id, format }: { id: string; format: 'pdf' | 'latex' | 'docx' }) =>
      api.exportPaper(id, format),
  })
}

// Review
export function useReviewFindings(paperId: string) {
  return useQuery({
    queryKey: queryKeys.reviewFindings(paperId),
    queryFn: () => api.getReviewFindings(paperId),
    enabled: !!paperId,
  })
}

export function useReviewerSimulations(paperId: string) {
  return useQuery({
    queryKey: queryKeys.reviewerSimulations(paperId),
    queryFn: () => api.getReviewerSimulations(paperId),
    enabled: !!paperId,
  })
}

export function useRunConsistencyCheck() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (input: string | { paperId: string; budgetMode?: string }) => api.runConsistencyCheck(input),
    onSuccess: (_data, input) => {
      const paperId = typeof input === 'string' ? input : input.paperId
      queryClient.invalidateQueries({ queryKey: queryKeys.reviewFindings(paperId) })
    },
  })
}

export function useRunReviewerSimulation() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ paperId, profile }: { paperId: string; profile: string }) =>
      api.runReviewerSimulation(paperId, profile),
    onSuccess: (_data, { paperId }) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.reviewerSimulations(paperId) })
    },
  })
}

// System
export function useSystemHealth() {
  return useQuery({
    queryKey: queryKeys.systemHealth,
    queryFn: () => api.getSystemHealth(),
    refetchInterval: 30000, // Refetch every 30 seconds
  })
}

export function useSystemLogs(filters?: { level?: string; limit?: number }) {
  return useQuery({
    queryKey: queryKeys.systemLogs(filters),
    queryFn: () => api.getSystemLogs(filters),
  })
}

export function useSystemMetrics(timeRange?: string) {
  return useQuery({
    queryKey: queryKeys.systemMetrics(timeRange),
    queryFn: () => api.getSystemMetrics(timeRange),
    refetchInterval: 10000, // Refetch every 10 seconds
  })
}

// Settings
export function useSystemConfig() {
  return useQuery({
    queryKey: queryKeys.systemConfig,
    queryFn: () => api.getSystemConfig(),
  })
}

export function useUpdateSystemConfig() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (updates: Partial<SystemConfig>) => api.updateSystemConfig(updates),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.systemConfig })
    },
  })
}
