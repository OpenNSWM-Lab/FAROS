/** Stable v1 boundaries shared by the four scientific-research modules. */

export const SCIENTIFIC_RESEARCH_SCHEMA_VERSION = 'scientific-research/v1' as const;

export type ScientificResearchSchemaVersion = typeof SCIENTIFIC_RESEARCH_SCHEMA_VERSION;
export type RunMode = 'coverage' | 'deep';
export type RunStatus = 'pending' | 'running' | 'completed' | 'completed_with_warnings' | 'failed' | 'cancelled';
export type ArtifactKind = 'idea' | 'plan' | 'code' | 'paper' | 'review' | 'dataset' | 'metric' | 'log' | 'report' | 'other';
export type TargetModule = 'idea' | 'code' | 'paper' | 'review' | 'platform';
export type EvidenceStance = 'support' | 'counter' | 'context';
export type EvidenceTier = 'primary' | 'secondary' | 'tertiary' | 'unknown';
export type ExecutionClass =
  | 'computational_ready'
  | 'simulation_ready'
  | 'data_required'
  | 'instrument_required'
  | 'ethics_review_required'
  | 'proof_required'
  | 'protocol_only'
  | 'not_assessed';
export type ExecutionStatus = 'not_assessed' | 'ready' | 'running' | 'executed' | 'failed' | 'not_applicable';
export type GateStatus = 'pass' | 'warn' | 'fail' | 'not_assessed';
export type Severity = 'blocker' | 'major' | 'minor' | 'info';
export type SupportStatus =
  | 'supported'
  | 'weakly_supported'
  | 'unsupported'
  | 'contradicted'
  | 'needs_human_verification'
  | 'not_assessed';

export interface ArtifactRef {
  id: string;
  kind: ArtifactKind;
  sourceModule: TargetModule;
  uri?: string;
  contentHash?: string;
  version?: string;
  createdAt?: string;
  metadata?: Record<string, unknown>;
}

export interface ScientificQuestion {
  id: string;
  text: string;
  language?: string;
  domainHint?: string;
  constraints?: string[];
  source?: string;
  metadata?: Record<string, unknown>;
}

export interface ScientificQuestionRun {
  schemaVersion: ScientificResearchSchemaVersion;
  runId: string;
  question: ScientificQuestion;
  mode: RunMode;
  status: RunStatus;
  providerName?: string;
  model?: string;
  parentRunId?: string;
  createdAt: string;
  updatedAt: string;
  artifactRefs: ArtifactRef[];
  errorMessage?: string;
}

export interface ProblemFrame {
  originalQuestion: string;
  scopedQuestion: string;
  definitions: Record<string, string>;
  observableVariables: string[];
  assumptions: string[];
  outOfScope: string[];
  subQuestions: string[];
}

export interface EvidenceRecord {
  id: string;
  title: string;
  summary: string;
  stance: EvidenceStance;
  sourceType: string;
  source: string;
  authors: string[];
  year?: number;
  doi?: string;
  url?: string;
  evidenceTier: EvidenceTier;
  relevanceScore: number;
  verified: boolean;
  claimIds: string[];
  metadata: Record<string, unknown>;
}

export interface EvidenceMap {
  consensus: string[];
  disputedClaims: string[];
  supportingEvidence: EvidenceRecord[];
  counterEvidence: EvidenceRecord[];
  contextualEvidence: EvidenceRecord[];
  unresolvedGaps: string[];
}

export interface Hypothesis {
  id: string;
  statement: string;
  rationale: string;
  derivationTrace: string[];
  supportingEvidenceIds: string[];
  counterEvidenceIds: string[];
  falsificationCriteria: string[];
  confounders: string[];
  alternativeExplanations: string[];
  scores: Record<string, number>;
  confidence: number;
}

export interface ResearchPlanStep {
  id: string;
  order: number;
  title: string;
  objective: string;
  inputs: string[];
  tools: string[];
  method: string[];
  outputs: string[];
  metrics: string[];
  stopConditions: string[];
  dependencies: string[];
  risks: string[];
}

export interface ResearchPlan {
  objective: string;
  steps: ResearchPlanStep[];
  requiredData: string[];
  requiredResources: string[];
  expectedOutcomes: string[];
  constraints: string[];
  ethics: string[];
  executionClass: ExecutionClass;
}

export interface GenerationTrace {
  providerName?: string;
  model?: string;
  localRulePasses: string[];
  llmCalls: Array<Record<string, unknown>>;
  warnings: string[];
  cacheHits: number;
  estimatedTokenCost?: number;
  startedAt?: string;
  endedAt?: string;
}

export interface ResearchDossier {
  schemaVersion: ScientificResearchSchemaVersion;
  runId: string;
  questionId: string;
  problemFrame: ProblemFrame;
  evidenceMap: EvidenceMap;
  hypotheses: Hypothesis[];
  researchPlan: ResearchPlan;
  uncertainties: string[];
  generationTrace: GenerationTrace;
  artifactRefs: ArtifactRef[];
}

export interface ExecutionAssessment {
  schemaVersion: ScientificResearchSchemaVersion;
  runId: string;
  questionId: string;
  planPackageId?: string;
  executionClass: ExecutionClass;
  feasibilityScore: number;
  rationale: string;
  availableInputs: string[];
  missingInputs: string[];
  toolsAndEnvironment: string[];
  validationMetrics: string[];
  stopConditions: string[];
  safetyConstraints: string[];
  estimatedRuntimeSeconds?: number;
  estimatedCost?: number;
  status: ExecutionStatus;
  warnings: string[];
  artifactRefs: ArtifactRef[];
}

export interface MetricEvidence {
  name: string;
  value: unknown;
  unit: string;
  definition: string;
  split: string;
  sourcePath: string;
}

export interface ExperimentEvidence {
  schemaVersion: ScientificResearchSchemaVersion;
  runId: string;
  questionId: string;
  codeRunId: string;
  status: ExecutionStatus;
  dataHashes: Record<string, string>;
  environmentHash: string;
  codeHash: string;
  method: string;
  baseline: string;
  metrics: MetricEvidence[];
  logRefs: string[];
  artifactRefs: ArtifactRef[];
  supportedClaims: string[];
  unsupportedClaims: string[];
  failures: string[];
  durationSeconds?: number;
}

export interface ClaimBinding {
  id: string;
  text: string;
  sourcePath: string;
  evidenceIds: string[];
  metricRefs: string[];
  supportStatus: SupportStatus;
}

export interface ResearchNarrative {
  schemaVersion: ScientificResearchSchemaVersion;
  runId: string;
  questionId: string;
  title: string;
  problemAndScope: string;
  currentEvidence: string;
  openGaps: string;
  candidateHypotheses: string;
  falsificationAndResearchPlan: string;
  executionStatusAndResults: string;
  limitationsAndUncertainty: string;
  citations: EvidenceRecord[];
  claimBindings: ClaimBinding[];
  artifactRefs: ArtifactRef[];
}

export interface QualityFinding {
  id: string;
  code: string;
  severity: Severity;
  targetModule: TargetModule;
  fieldPath: string;
  evidenceIds: string[];
  description: string;
  suggestedFix: string;
}

export interface QualityAssessment {
  schemaVersion: ScientificResearchSchemaVersion;
  runId: string;
  questionId: string;
  gateStatus: GateStatus;
  dimensionScores: Record<string, number>;
  findings: QualityFinding[];
  ruleTrace: Array<Record<string, unknown>>;
  llmTrace: Array<Record<string, unknown>>;
  uncertainty: string;
  configVersion: string;
  reviewedAt: string;
}

export interface QuestionBatch {
  schemaVersion: ScientificResearchSchemaVersion;
  batchId: string;
  questionSetId?: string;
  questionIds: string[];
  childRunIds: string[];
  chunkSize: number;
  concurrency: number;
  status: RunStatus;
  progress: number;
  failedQuestionIds: string[];
  configHash: string;
  createdAt: string;
  updatedAt: string;
}

export interface QuestionSetManifest {
  schemaVersion: ScientificResearchSchemaVersion;
  questionSetId: string;
  name: string;
  version: string;
  source: string;
  contentHash: string;
  questions: ScientificQuestion[];
}
