/**
 * Code Agent API client — Autonomous code execution agent endpoints.
 *
 * Endpoints:
 *   POST   /code/agent/run        — Start autonomous agent run
 *   GET    /code/agent/runs/:id   — Get run status with trace events
 *   GET    /code/agent/runs       — List agent runs
 *   DELETE /code/agent/runs/:id   — Delete a run record
 *   GET    /code/agent/status     — Agent system health
 */

const API_BASE = import.meta.env.VITE_API_BASE_URL || '';

// ---- Types ----

export interface AgentRunRequest {
  projectId: string;
  goal?: string;
  language?: string;
  command?: string;
  backend?: 'docker' | 'subprocess';
  maxIterations?: number;
  executionTimeout?: number;
  providerName?: string;
  model?: string;
}

export interface AgentRunResponse {
  runId: string;
  traceId: string;
  status: string;
  message: string;
}

export interface ExecutionEvent {
  step: string;
  status: string;
  message: string;
  details: Record<string, unknown>;
  duration_ms: number;
  iteration: number;
  sandbox_id: string | null;
  timestamp: string;
}

export interface AgentRun {
  id: string;
  projectId: string;
  goal?: string;
  language: string;
  status: string;
  iterations: number;
  repairsApplied: number;
  traceId?: string;
  summary?: string;
  error?: string;
  createdAt?: string;
  completedAt?: string;
  events: ExecutionEvent[];
}

export interface AgentRunListResponse {
  runs: AgentRun[];
  total: number;
}

export interface AgentStatus {
  available: boolean;
  defaultBackend: string;
  availableBackends: string[];
  pool: {
    active_count: number;
    max_active: number;
    default_backend: string;
    available_backends: string[];
    active_sandboxes: Array<{
      id: string;
      backend: string;
      age_sec: number;
      idle_sec: number;
    }>;
  };
}

// ---- API functions ----

async function fetchJSON<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text().catch(() => 'Unknown error');
    throw new Error(`API error ${response.status}: ${text}`);
  }
  return response.json();
}

/** Start an autonomous agent run. Returns immediately with runId/traceId. */
export async function startAgentRun(
  request: AgentRunRequest,
): Promise<AgentRunResponse> {
  return fetchJSON<AgentRunResponse>(`${API_BASE}/api/v1/code/agent/run`, {
    method: 'POST',
    body: JSON.stringify(request),
  });
}

/** Get agent run status with trace events. */
export async function getAgentRun(runId: string): Promise<AgentRun> {
  return fetchJSON<AgentRun>(`${API_BASE}/api/v1/code/agent/runs/${runId}`);
}

/** List agent runs, optionally filtered. */
export async function listAgentRuns(params?: {
  projectId?: string;
  status?: string;
  limit?: number;
  offset?: number;
}): Promise<AgentRunListResponse> {
  const searchParams = new URLSearchParams();
  if (params?.projectId) searchParams.set('projectId', params.projectId);
  if (params?.status) searchParams.set('status', params.status);
  if (params?.limit) searchParams.set('limit', String(params.limit));
  if (params?.offset) searchParams.set('offset', String(params.offset));
  const qs = searchParams.toString();
  return fetchJSON<AgentRunListResponse>(
    `${API_BASE}/api/v1/code/agent/runs${qs ? '?' + qs : ''}`,
  );
}

/** Delete an agent run record. */
export async function deleteAgentRun(
  runId: string,
): Promise<{ deleted: boolean; runId: string }> {
  return fetchJSON(`${API_BASE}/api/v1/code/agent/runs/${runId}`, {
    method: 'DELETE',
  });
}

/** Get agent system health status. */
export async function getAgentStatus(): Promise<AgentStatus> {
  return fetchJSON<AgentStatus>(`${API_BASE}/api/v1/code/agent/status`);
}

/** Poll an agent run until it completes, calling onUpdate with each status. */
export async function pollAgentRun(
  runId: string,
  onUpdate: (run: AgentRun) => void,
  intervalMs: number = 2000,
  timeoutMs: number = 600000,
): Promise<AgentRun> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const run = await getAgentRun(runId);
    onUpdate(run);
    if (
      run.status === 'succeeded' ||
      run.status === 'failed' ||
      run.status === 'max_iterations' ||
      run.status === 'error'
    ) {
      return run;
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error(`Agent run ${runId} polling timed out after ${timeoutMs}ms`);
}

// ---- Claude Code Agent (streaming) ----

export interface ClaudeStreamRequest {
  projectId: string;
  goal: string;
  systemPrompt?: string;
  template?: 'run_experiment' | 'fix_and_verify' | 'analyze_and_plot' | 'custom';
  model?: string;
  maxBudget?: number;
  timeout?: number;
  sessionId?: string;
}

export interface ClaudeStreamEvent {
  event_type: string;  // "thinking" | "tool_use" | "tool_result" | "error" | "done"
  content: string;
  tool_name: string;
  tool_input: string;
  tool_output: string;
  step: string;        // "planning" | "executing" | "analyzing" | "complete"
  timestamp: string;
}

// ---- Cart Pipeline Runner ----

export interface CartRunRequest {
  projectId: string;
  packageId?: string;
  timeout?: number;
}

export interface CartProgressEvent {
  event_type: string;  // "cart_start" | "node_start" | "node_complete" | "cart_complete"
  node_id: string;
  status: string;      // "running" | "succeeded" | "failed" | "skipped" | "partial"
  message: string;
  result?: Record<string, unknown>;
  timestamp: string;
}

/** Stream Cart pipeline execution via SSE. Returns AbortController for cancellation. */
export function streamCartRun(
  request: CartRunRequest,
  onEvent: (event: CartProgressEvent) => void,
  onDone: (error?: string) => void,
): AbortController {
  const controller = new AbortController();
  fetch(`${API_BASE}/api/v1/code/agent/cart/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        const text = await response.text().catch(() => 'Unknown');
        onDone(`API error ${response.status}: ${text}`);
        return;
      }
      const reader = response.body?.getReader();
      if (!reader) { onDone('No response body'); return; }
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const event: CartProgressEvent = JSON.parse(line.slice(6));
              onEvent(event);
              if (event.event_type === 'cart_complete') { onDone(); return; }
            } catch { /* skip */ }
          }
        }
      }
      onDone();
    })
    .catch((err) => {
      onDone(err.name === 'AbortError' ? 'Cancelled' : err.message);
    });
  return controller;
}

/** Stream Claude Code execution via SSE. Returns an AbortController for cancellation. */
export function streamClaudeAgent(
  request: ClaudeStreamRequest,
  onEvent: (event: ClaudeStreamEvent) => void,
  onDone: (error?: string) => void,
): AbortController {
  const controller = new AbortController();

  fetch(`${API_BASE}/api/v1/code/agent/claude-stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        const text = await response.text().catch(() => 'Unknown error');
        onDone(`API error ${response.status}: ${text}`);
        return;
      }
      const reader = response.body?.getReader();
      if (!reader) {
        onDone('No response body');
        return;
      }
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        // Parse SSE lines
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const event: ClaudeStreamEvent = JSON.parse(line.slice(6));
              onEvent(event);
              if (event.event_type === 'done' || event.event_type === 'error') {
                onDone(event.event_type === 'error' ? event.content : undefined);
                return;
              }
            } catch {
              // skip malformed JSON
            }
          }
        }
      }
      onDone();
    })
    .catch((err) => {
      if (err.name === 'AbortError') {
        onDone('Cancelled');
      } else {
        onDone(err.message);
      }
    });

  return controller;
}
