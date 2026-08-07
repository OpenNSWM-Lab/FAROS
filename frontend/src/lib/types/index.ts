// TypeScript Data Model Contracts for LLM-Scientist Platform
// Based on spec section 4

// ============================================================================
// Run & Workflow Models
// ============================================================================

export interface Run {
  id: string;
  type: 'plan' | 'idea';
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
  startedAt: string; // ISO 8601
  endedAt?: string;
  duration?: number; // seconds
  config: RunConfig;
  trace?: WorkflowTrace;
  artifacts: Artifact[];
  experiments: ExperimentRecord[];
}

export interface RunConfig {
  instancePath?: string;
  taskLevel: string;
  paperType?: string;
  model: string;
  workplaceName: string;
  cachePath: string;
  port: number;
  maxIterTimes: number;
  // Legacy category field (backward compatibility)
  category?: 'reasoning' | 'vq' | 'gnn' | 'recommendation' | 'diffu_flow';
  // New taxonomy-based category fields
  categoryGroup?: 'post-training' | 'inference';
  categoryDirectionId?: string; // matches taxonomy direction IDs
  templateId?: string; // if created from template
  ideas?: string;
  references?: string;
}

export interface WorkflowTrace {
  runId: string;
  workdir: string;
  startedAt: string;
  endedAt: string;
  totalSteps: number;
  successfulSteps: number;
  failedSteps: number;
  artifacts: string[];
  steps: StepResult[];
}

export interface StepResult {
  name: string;
  status: 'ok' | 'skipped' | 'failed';
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown>;
  artifacts: string[];
  startedAt: string;
  endedAt: string;
  error?: string;
  durationSeconds: number;
}

// ============================================================================
// Experiment Models
// ============================================================================

export interface ExperimentRecord {
  id: string;
  runId: string;
  runIds: string[]; // linked runs for comparison
  name: string;
  description?: string;
  domain: string; // reasoning, vq, gnn, etc.
  task: string; // specific task name
  metrics: MetricValue[];
  parameters: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
  status: 'running' | 'completed' | 'failed';
}

export interface MetricValue {
  name: string;
  value: number;
  mean: number; // aggregate mean
  std?: number; // standard deviation
  unit?: string;
  timestamp: string;
}

export interface ExperimentComparison {
  experimentIds: string[];
  metrics: ComparisonMetric[];
  analysis?: string;
}

export interface ComparisonMetric {
  name: string;
  values: { experimentId: string; value: number }[];
  winner?: string; // experimentId with best value
}

// ============================================================================
// Artifact Models
// ============================================================================

export interface Artifact {
  id: string;
  runId: string;
  type: 'paper' | 'code' | 'repository' | 'log' | 'data' | 'model';
  path: string;
  filename: string;
  size: number; // bytes
  mimeType: string;
  createdAt: string;
  metadata?: Record<string, unknown>;
}

export interface ArtifactPreview {
  artifactId: string;
  content: string;
  language?: string; // for syntax highlighting
  rendered?: string; // for LaTeX/Markdown
}

// ============================================================================
// Paper Models
// ============================================================================

export interface PaperDraft {
  id: string;
  runId?: string;
  title: string;
  authors: string[];
  sections: PaperSection[];
  bibliography?: string;
  createdAt: string;
  updatedAt: string;
  status: 'draft' | 'review' | 'final';
}

export interface PaperSection {
  id: string;
  type: 'abstract' | 'introduction' | 'methodology' | 'experiments' |
  'results' | 'conclusion' | 'related_work' | 'preliminaries';
  title: string;
  content: string; // LaTeX source
  order: number;
}

export interface PaperExport {
  format: 'pdf' | 'latex' | 'docx';
  paperId: string;
  url?: string; // download URL
  status: 'pending' | 'processing' | 'ready' | 'failed';
}

// ============================================================================
// Review Models
// ============================================================================

export interface ReviewFinding {
  id: string;
  paperId: string;
  type: 'consistency' | 'citation' | 'formatting' | 'content';
  severity: 'blocker' | 'major' | 'minor' | 'info';
  title: string;
  description: string;
  evidence?: string;
  suggestedFix?: string;
  riskType?: string;
  claimId?: string;
  evidenceIds?: string[];
  targetModule?: string;
  confidence?: number;
  supportStatus?: string;
  verifierIds?: string[];
  reviewerDecision?: string;
  reviewerAssessment?: string;
  reviewerModel?: string;
  cemCalibration?: Record<string, unknown>;
  revisionRequestIds?: string[];
  revisionStatus?: string;
  relatedRunId?: string;
  relatedArtifactId?: string;
  location?: {
    section: string;
    line?: number;
  };
}

export interface ReviewerSimulation {
  id: string;
  paperId: string;
  reviewerProfile: 'expert' | 'generalist' | 'critical' | 'supportive';
  strengths: string[];
  weaknesses: string[];
  questions: string[];
  recommendation: 'accept' | 'minor_revision' | 'major_revision' | 'reject';
  confidence: number; // 1-5
  createdAt: string;
}

// ============================================================================
// Settings Models
// ============================================================================

export interface SystemConfig {
  llm: LLMConfig;
  workspace: WorkspaceConfig;
  preferences: UserPreferences;
}

export interface LLMConfig {
  defaultModel: string;
  cheapModel: string;
  providers: LLMProvider[];
}

export interface LLMProvider {
  name: string;
  enabled: boolean;
  apiKey?: string;
  baseUrl?: string;
  models: string[];
}

export interface WorkspaceConfig {
  workplaceName: string;
  containerName: string;
  port: number;
  cachePath: string;
  dataPath: string;
}

export interface UserPreferences {
  theme: 'light' | 'dark' | 'system';
  language: string;
  timezone: string;
  notifications: boolean;
  autoSave: boolean;
}

// ============================================================================
// System Health Models
// ============================================================================

export interface SystemHealth {
  status: 'healthy' | 'degraded' | 'down';
  components: ComponentHealth[];
  lastChecked: string;
}

export interface ComponentHealth {
  name: string;
  status: 'ok' | 'degraded' | 'down';
  lastCheck: string;
  latency?: number;
  message?: string;
  uptime?: number; // seconds
  metrics?: Record<string, number>;
}

export interface LogEntry {
  timestamp: string;
  level: 'debug' | 'info' | 'warn' | 'error';
  message: string;
  source?: string;
  metadata?: Record<string, unknown>;
}

export interface SystemMetrics {
  cpu: {
    current: number;
    average: number;
    peak: number;
  };
  memory: {
    used: number; // MB
    total: number; // MB
    usedPercent: number;
  };
  disk: {
    used: number; // MB
    total: number; // MB
    usedPercent: number;
  };
  network: {
    in: number; // bytes/sec
    out: number; // bytes/sec
  };
  timestamp: string;
}

export * from './scientificResearch';
