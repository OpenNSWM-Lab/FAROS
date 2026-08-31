const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

export interface ComputeStatus {
  nodeName: string
  role: string
  location: string
  acceptingJobs: boolean
  isolationRequired: boolean
  runtime: {
    dockerAvailable: boolean
    nvidiaContainerRuntime: boolean
    defaultBackend: string
    maxConcurrent: number
  }
  cpu: { logicalCores: number }
  memory: { totalGiB: number; availableGiB: number }
  storage: { totalGiB: number; freeGiB: number; freePercent: number }
  gpus: Array<{
    index: number
    name: string
    memoryTotalMiB: number
    memoryFreeMiB: number
    utilizationPercent: number
  }>
  warnings: string[]
  scheduler?: {
    active_count?: number
    max_active?: number
    default_backend?: string
  }
}

export async function getComputeStatus(): Promise<ComputeStatus> {
  const response = await fetch(`${API_BASE}/api/system/compute`)
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    throw new Error(payload.detail || `Compute status failed: HTTP ${response.status}`)
  }
  return response.json()
}
