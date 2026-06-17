/**
 * Code Projects API Client
 * 
 * Typed API client for code project browsing, search, export, VSCode link.
 */

const API_BASE = import.meta.env.VITE_API_BASE_URL || '';

// Types

export interface CodeProjectV2 {
  id: string;
  title: string;
  description?: string;
  language?: string;
  framework?: string;
  license?: string;
  sourceIdeaSessionId?: string;
  sourceCandidateId?: string;
  rootStoragePath?: string;
  repoSchemaVersion: number;
  fileCount: number;
  totalSizeBytes: number;
  createdAt: string;
  updatedAt: string;
}

export interface ProjectListResponse {
  projects: CodeProjectV2[];
  total: number;
}

export interface TreeEntry {
  name: string;
  path: string;
  isDir: boolean;
  size: number;
}

export interface TreeResponse {
  projectId: string;
  path: string;
  entries: TreeEntry[];
}

export interface FileContentResponse {
  projectId: string;
  path: string;
  content: string;
  size: number;
  language?: string;
}

export interface SearchResult {
  path: string;
  line?: number;
  content?: string;
  isDir?: boolean;
}

export interface SearchResponse {
  projectId: string;
  query: string;
  mode: string;
  results: SearchResult[];
  total: number;
}

export interface ExportResponse {
  id: string;
  projectId: string;
  kind: string;
  size: number;
  sha256?: string;
  createdAt: string;
}

export interface VSCodeLinkResponse {
  uri: string;
  path: string;
  exists: boolean;
  instructions: string;
}

export interface CreateProjectRequest {
  title: string;
  description?: string;
  language?: string;
  framework?: string;
  license?: string;
  sourceIdeaSessionId?: string;
  sourceCandidateId?: string;
  files?: Array<{ path: string; content: string }>;
}

// Helper

async function fetchJSON<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `HTTP ${response.status}: ${response.statusText}`);
  }
  return response.json();
}

// API Functions

export async function createProject(request: CreateProjectRequest): Promise<CodeProjectV2> {
  return fetchJSON(`${API_BASE}/api/v1/code/projects`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  });
}

export async function listProjects(params?: {
  search?: string;
  language?: string;
  limit?: number;
  offset?: number;
}): Promise<ProjectListResponse> {
  const sp = new URLSearchParams();
  if (params?.search) sp.set('search', params.search);
  if (params?.language) sp.set('language', params.language);
  if (params?.limit) sp.set('limit', params.limit.toString());
  if (params?.offset) sp.set('offset', params.offset.toString());
  const qs = sp.toString() ? `?${sp}` : '';
  return fetchJSON(`${API_BASE}/api/v1/code/projects${qs}`);
}

export async function getProject(projectId: string): Promise<CodeProjectV2> {
  return fetchJSON(`${API_BASE}/api/v1/code/projects/${projectId}`);
}

export async function deleteProject(projectId: string): Promise<void> {
  const response = await fetch(`${API_BASE}/api/v1/code/projects/${projectId}`, { method: 'DELETE' });
  if (!response.ok && response.status !== 204) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
}

export async function getTree(projectId: string, path: string = ''): Promise<TreeResponse> {
  const sp = new URLSearchParams();
  if (path) sp.set('path', path);
  const qs = sp.toString() ? `?${sp}` : '';
  return fetchJSON(`${API_BASE}/api/v1/code/projects/${projectId}/tree${qs}`);
}

export async function getFileContent(projectId: string, path: string): Promise<FileContentResponse> {
  return fetchJSON(`${API_BASE}/api/v1/code/projects/${projectId}/file?path=${encodeURIComponent(path)}`);
}

export function getFileDownloadUrl(projectId: string, path: string): string {
  return `${API_BASE}/api/v1/code/projects/${projectId}/file/download?path=${encodeURIComponent(path)}`;
}

export async function searchProject(projectId: string, query: string, mode: 'path' | 'content' = 'path'): Promise<SearchResponse> {
  return fetchJSON(`${API_BASE}/api/v1/code/projects/${projectId}/search?q=${encodeURIComponent(query)}&mode=${mode}`);
}

export async function exportProject(projectId: string): Promise<ExportResponse> {
  return fetchJSON(`${API_BASE}/api/v1/code/projects/${projectId}/export`, { method: 'POST' });
}

export function getExportDownloadUrl(exportId: string): string {
  return `${API_BASE}/api/v1/code/projects/exports/${exportId}/download`;
}

export async function getVSCodeLink(projectId: string): Promise<VSCodeLinkResponse> {
  return fetchJSON(`${API_BASE}/api/v1/code/projects/${projectId}/vscode-link`);
}

export async function generateSampleProject(title: string, language: string = 'python', description?: string): Promise<CodeProjectV2> {
  return fetchJSON(`${API_BASE}/api/v1/code/projects/generate-sample`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, language, description }),
  });
}

// ---- Run / Job types and API ----

export interface RunProjectRequest {
  command?: string;
  mode?: string;
  envVars?: Record<string, string>;
  timeoutSec?: number;
}

export interface RunProjectResponse {
  jobId: string;
  projectId: string;
  status: string;
  command: string;
  workspacePath: string;
  createdAt: string;
}

export interface JobInfo {
  id: string;
  sessionId?: string;
  projectId?: string;
  candidateId?: string;
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled';
  mode: string;
  command: string;
  envVars?: Record<string, string>;
  cwdRel?: string;
  timeoutSec: number;
  workspacePath?: string;
  pid?: number;
  exitCode?: number;
  stdoutPath?: string;
  stderrPath?: string;
  createdAt: string;
  startedAt?: string;
  endedAt?: string;
  durationSec?: number;
}

export interface JobLogResponse {
  jobId: string;
  logType: string;
  lines: string[];
  totalLines: number;
}

export interface ProjectJobsResponse {
  projectId: string;
  jobs: JobInfo[];
  total: number;
}

export async function runProject(projectId: string, request: RunProjectRequest = {}): Promise<RunProjectResponse> {
  return fetchJSON(`${API_BASE}/api/v1/code/projects/${projectId}/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  });
}

// ---- Pipeline Run (step-by-step) ----

export interface PipelineStepResult {
  name: string;
  purpose: string;
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'skipped';
  durationMs: number;
  stdout: string;
  stderr: string;
  exitCode?: number;
  error?: string;
}

export interface PipelineRunResponse {
  jobId: string;
  projectId: string;
  status: string;  // running | succeeded | failed | partial
  steps: PipelineStepResult[];
  totalDurationMs: number;
  summary: string;
}

export async function runProjectPipeline(projectId: string): Promise<PipelineRunResponse> {
  return fetchJSON(`${API_BASE}/api/v1/code/projects/${projectId}/pipeline-run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
}

export async function getPipelineResults(projectId: string, jobId?: string): Promise<PipelineRunResponse> {
  const qs = jobId ? `?jobId=${jobId}` : '';
  return fetchJSON(`${API_BASE}/api/v1/code/projects/${projectId}/pipeline-results${qs}`);
}

export async function getProjectJobs(projectId: string): Promise<ProjectJobsResponse> {
  return fetchJSON(`${API_BASE}/api/v1/code/projects/${projectId}/jobs`);
}

export async function getJob(jobId: string): Promise<JobInfo> {
  return fetchJSON(`${API_BASE}/api/v1/code/jobs/${jobId}`);
}

export async function getJobLogs(jobId: string, logType: 'stdout' | 'stderr' = 'stdout', lines: number = 100): Promise<JobLogResponse> {
  return fetchJSON(`${API_BASE}/api/v1/code/jobs/${jobId}/logs?logType=${logType}&lines=${lines}`);
}

export async function deleteJob(jobId: string): Promise<void> {
  await fetch(`${API_BASE}/api/v1/code/jobs/${jobId}`, { method: 'DELETE' });
}

export interface FixApplied {
  stepName: string;
  filePath: string;
  description: string;
  applied: boolean;
  method: 'deterministic' | 'llm' | 'none';
  diffLines: string[];
  originalContent: string;
  newContent: string;
}

export interface AutoFixResponse {
  jobId: string;
  projectId: string;
  status: string;
  iterations: number;
  fixesApplied: FixApplied[];
  summary: string;
  pipeline?: PipelineRunResponse;
}

export async function autoFixProject(projectId: string): Promise<AutoFixResponse> {
  return fetchJSON(`${API_BASE}/api/v1/code/projects/${projectId}/auto-fix`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
}

// ---- Blueprint ----

export interface BlueprintNode {
  id: string; label: string; stage: string; status: string;
  description: string; method: string;
  inputs: string[]; outputs: string[];
  result: Record<string, unknown> | null;
  startedAt: string | null; finishedAt: string | null; duration: number | null;
}

export interface BlueprintEdge { id: string; source: string; target: string }

export interface BlueprintResponse {
  projectId: string; projectTitle: string; source: string;
  id: string; title: string; description: string;
  nodes: BlueprintNode[]; edges: BlueprintEdge[];
}

export interface BlueprintSummary {
  id: string; title: string; source: string;
  nodeCount: number; createdAt?: string;
}

/** Get the experiment blueprint DAG for a project. */
export async function getProjectBlueprint(projectId: string): Promise<BlueprintResponse> {
  return fetchJSON(`${API_BASE}/api/v1/code/blueprints/${projectId}`);
}

/** List all blueprint sessions for a project. */
export async function listProjectBlueprints(projectId: string): Promise<BlueprintSummary[]> {
  return fetchJSON(`${API_BASE}/api/v1/code/blueprints/${projectId}/list`);
}
