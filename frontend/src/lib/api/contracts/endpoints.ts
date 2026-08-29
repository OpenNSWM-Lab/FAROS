// API Endpoint Contract Map
// Defines all backend endpoints that will be implemented

export const API_ENDPOINTS = {
  // Runs
  RUNS_LIST: { path: '/api/runs', method: 'GET' },
  RUNS_GET: { path: '/api/runs/:id', method: 'GET' },
  RUNS_CREATE: { path: '/api/runs', method: 'POST' },
  RUNS_DELETE: { path: '/api/runs/:id', method: 'DELETE' },
  RUNS_STREAM: { path: '/api/runs/:id/stream', method: 'GET' }, // SSE endpoint

  // Workflow Traces
  TRACE_GET: { path: '/api/runs/:id/trace', method: 'GET' },

  // Experiments
  EXPERIMENTS_LIST: { path: '/api/experiments', method: 'GET' },
  EXPERIMENTS_GET: { path: '/api/experiments/:id', method: 'GET' },
  EXPERIMENTS_CREATE: { path: '/api/experiments', method: 'POST' },

  // Artifacts
  ARTIFACTS_LIST: { path: '/api/artifacts', method: 'GET' },
  ARTIFACTS_GET: { path: '/api/artifacts/:id', method: 'GET' },
  ARTIFACTS_PREVIEW: { path: '/api/artifacts/:id/preview', method: 'GET' },
  ARTIFACTS_DOWNLOAD: { path: '/api/artifacts/:id/download', method: 'GET' },

  // Papers
  PAPERS_LIST: { path: '/api/papers', method: 'GET' },
  PAPERS_GET: { path: '/api/papers/:id', method: 'GET' },
  PAPERS_CREATE: { path: '/api/papers', method: 'POST' },
  PAPERS_UPDATE: { path: '/api/papers/:id', method: 'PUT' },

  // Review
  REVIEW_FINDINGS_LIST: { path: '/api/review/findings', method: 'GET' },

  // System
  SYSTEM_HEALTH: { path: '/api/system/health', method: 'GET' },
  SYSTEM_LOGS: { path: '/api/system/logs', method: 'GET' },
  SYSTEM_METRICS: { path: '/api/system/metrics', method: 'GET' },
  SYSTEM_CONFIG: { path: '/api/system/config', method: 'GET' },
} as const

export type EndpointKey = keyof typeof API_ENDPOINTS
