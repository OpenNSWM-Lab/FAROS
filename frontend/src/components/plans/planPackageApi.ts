const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

export type PlanOutputType = 'metrics' | 'chart' | 'table' | 'checkpoint' | 'code' | 'report' | 'log'

export interface PlanEvidenceRef {
  type: string
  id: string
  source?: string
  note?: string
}

export interface PlanQualityGate {
  schemaValid: boolean
  evidenceValid: boolean
  topicRelevant: boolean
  citationFaithful: boolean
  planSpecific: boolean
  agentApproved: boolean
  humanApproved: boolean
  implementationReady: boolean
  overallScore: number
  reviewDecision: string
  warnings: string[]
  errors: string[]
}

export interface PlanSource {
  ideaSessionId: string
  ideaCandidateId: string
  rankedOutputId?: string | null
  searchTreeId?: string | null
  searchNodeId?: string | null
  pathSeedId?: string | null
  reasoningKgId?: string | null
  literatureMapId?: string | null
  bftsHandoffId?: string | null
}

export interface PlanIdeaSummary {
  id: string
  title: string
  problem: string
  hypothesisStatement: string
  keyInsight: string
  proposedMethod: string
  expectedOutcome: string
  scores: Record<string, unknown>
  critiqueSummary: string
  closestPriorWork: Array<Record<string, unknown>>
}

export interface PlanBackground {
  summary: string
  motivation: string
  currentLimitations: string[]
  domainContext: string[]
  evidenceRefs: PlanEvidenceRef[]
}

export interface PlanLiteratureCoverage {
  rawPaperCount: number
  selectedPaperCount: number
  structuredPaperCount: number
  probePaperCount: number
  clusterCount: number
}

export interface PlanLiteraturePaperSummary {
  paperId: string
  structuredPaperId?: string | null
  source: 'structured' | 'probe' | string
  title: string
  authors: string[]
  year?: number | null
  venue: string
  url: string
  role: string
  relevanceScore: number
  relevanceSignals: string[]
  relevanceReason: string
  summary: string
  methods: Array<Record<string, unknown>>
  findings: Array<Record<string, unknown>>
  limitations: string[]
  claims: Array<Record<string, unknown>>
  usedByStageIds: string[]
  usedByStepIds: string[]
  evidenceRefs: PlanEvidenceRef[]
}

export interface PlanLiteratureSurvey {
  summary: string
  coverage: PlanLiteratureCoverage
  clusters: Array<Record<string, unknown>>
  papers: PlanLiteraturePaperSummary[]
}

export interface PlanGapItem {
  id: string
  kind: 'selected' | 'supporting_signal' | 'literature_limitation'
  statement: string
  severity: string
  existingCoverage: string
  unresolvedIssue: string
  proposedEntry: string
  boundary: string
  validationNeeds: string[]
  whyUnsolved: string
  supportedByPaperIds: string[]
  supportedByClaimIds: string[]
  linkedGraphSignalIds: string[]
}

export interface PlanGap {
  summary: string
  items: PlanGapItem[]
  selectedGapId: string
}

export interface PlanPrinciple {
  summary: string
  mechanism: string
  noveltyClaim: string
  assumptions: string[]
  risks: string[]
  reasoningPath: Array<Record<string, unknown>>
  graphGrounding: {
    entityIds: string[]
    relationIds: string[]
    pathSeedIds: string[]
    searchNodeIds: string[]
  }
  probeGrounding: {
    probeResultIds: string[]
    graphPatchIds: string[]
    probePaperIds: string[]
  }
}

export interface PlanContributionStatement {
  id: string
  type: 'method' | 'system' | 'evaluation' | 'analysis' | 'application'
  statement: string
  noveltyBasis: string
  validationStageIds: string[]
  validationStepIds: string[]
  evidenceRefs: PlanEvidenceRef[]
}

export interface PlanOutput {
  type: PlanOutputType
  name: string
  desc: string
  requiredFor: string[]
}

export interface PlanExpectedMetric {
  metric: string
  target: string
  desc: string
}

export interface PlanStep {
  id: string
  order: number
  title: string
  desc: string
  method: string
  inputFrom: string[]
  outputs: PlanOutput[]
  expected: PlanExpectedMetric[]
  evidenceRefs: PlanEvidenceRef[]
  codeHints: Record<string, unknown>
}

export interface PlanStage {
  id: string
  order: number
  title: string
  goal: string
  method: string
  dependsOn: string[]
  steps: PlanStep[]
}

export interface PlanEvidenceTrace {
  ideaCandidateId: string
  searchNodeId?: string | null
  pathSeedId?: string | null
  reasoningKgId?: string | null
  literatureMapId?: string | null
  selectedPaperIds: string[]
  structuredPaperIds: string[]
  probeResultIds: string[]
  graphPatchIds: string[]
  probePaperIds: string[]
  candidateGraphEvidence: Record<string, unknown>
  reasoningTrace: Array<Record<string, unknown>>
}

export interface PlanGenerationMetadata {
  mode: string
  providerName?: string | null
  model?: string | null
  promptVersion: string
  llmUsedSections: string[]
  reviewerMode: 'deterministic' | 'hybrid' | string
  llmReviewerUsed: boolean
  repairRounds: number
  fallbackUsed: boolean
  warnings: string[]
}

export type PlanPackageStatus =
  | 'draft'
  | 'agent_reviewing'
  | 'needs_revision'
  | 'needs_human_review'
  | 'approved'
  | 'rejected'

export interface PlanHumanFeedback {
  id: string
  sectionPath: string
  feedbackType: string
  comment: string
  severity: string
  requestedAction: string
  createdAt: string
  resolved: boolean
  resolvedByRevisionId?: string | null
}

export interface PlanRevision {
  id: string
  parentPackageId: string
  createdAt: string
  changedSections: string[]
  feedbackIds: string[]
  summary: string
  generationMode: string
  repairRounds: number
}

export interface PlanReviewerIssue {
  id: string
  severity: string
  sectionPath: string
  message: string
  evidenceRefs: PlanEvidenceRef[]
}

export interface PlanReviewerReport {
  reviewer: string
  score: number
  passed: boolean
  blockingIssues: PlanReviewerIssue[]
  warnings: PlanReviewerIssue[]
  repairSuggestions: string[]
  evidenceRefs: PlanEvidenceRef[]
  createdAt: string
}

export interface PlanMetaReview {
  overallScore: number
  decision: string
  confidence: number
  blockingIssues: PlanReviewerIssue[]
  warnings: PlanReviewerIssue[]
  requiredRepairs: string[]
  reviewerScores: Record<string, number>
  createdAt: string
}

export interface PlanPackage {
  schemaVersion: string
  packageId: string
  createdAt: string
  status: PlanPackageStatus
  source: PlanSource
  idea: PlanIdeaSummary
  background: PlanBackground
  literatureSurvey: PlanLiteratureSurvey
  gap: PlanGap
  principle: PlanPrinciple
  contributionStatement: PlanContributionStatement[]
  researchQuestion: string
  hypothesis: string
  constants: Record<string, unknown>
  stages: PlanStage[]
  evidenceTrace: PlanEvidenceTrace
  downstreamContract: Record<string, unknown>
  qualityGate: PlanQualityGate
  generation: PlanGenerationMetadata
  humanFeedback: PlanHumanFeedback[]
  revisions: PlanRevision[]
  reviewReports: PlanReviewerReport[]
  metaReview?: PlanMetaReview | null
  sourceFields: Record<string, string[]>
  rawIdeaOutputs: Record<string, unknown>
}

export interface CreatePlanPackageRequest {
  candidateId?: string
  maxStages?: number
  maxStepsPerStage?: number
  userNotes?: string
  generationMode?: 'hybrid' | 'deterministic'
  reviewerMode?: 'deterministic' | 'hybrid'
  maxRepairRounds?: number
}

export interface CreatePlanPackageResponse {
  packageId: string
  schemaVersion: string
  qualityGate: PlanQualityGate
  package: PlanPackage
}

export interface ValidatePlanPackageResponse {
  packageId: string
  qualityGate: PlanQualityGate
}

export interface PlanPackageFeedbackRequest {
  sectionPath?: string
  feedbackType?: string
  comment: string
  severity?: string
  requestedAction?: string
}

export interface RevisePlanPackageRequest {
  generationMode?: 'hybrid' | 'deterministic'
  reviewerMode?: 'deterministic' | 'hybrid'
  maxStages?: number
  maxStepsPerStage?: number
  maxRepairRounds?: number
  targetSections?: string[]
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${url}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = typeof data.detail === 'string' ? data.detail : `Request failed (${response.status})`
    throw new Error(detail)
  }
  return data as T
}

export function createPlanPackageFromIdeaSession(
  ideaSessionId: string,
  body: CreatePlanPackageRequest
): Promise<CreatePlanPackageResponse> {
  return requestJson<CreatePlanPackageResponse>(
    `/api/v1/plans/packages/from-idea-session/${encodeURIComponent(ideaSessionId)}`,
    {
      method: 'POST',
      body: JSON.stringify(body),
    }
  )
}

export function getPlanPackage(packageId: string): Promise<PlanPackage> {
  return requestJson<PlanPackage>(`/api/v1/plans/packages/${encodeURIComponent(packageId)}`)
}

export function getPlanPackageByIdeaSession(ideaSessionId: string): Promise<PlanPackage> {
  return requestJson<PlanPackage>(`/api/v1/ideas/sessions/${encodeURIComponent(ideaSessionId)}/plan-package`)
}

export function validatePlanPackage(packageId: string): Promise<ValidatePlanPackageResponse> {
  return requestJson<ValidatePlanPackageResponse>(
    `/api/v1/plans/packages/${encodeURIComponent(packageId)}/validate`,
    { method: 'POST' }
  )
}

export function addPlanPackageFeedback(
  packageId: string,
  body: PlanPackageFeedbackRequest
): Promise<PlanPackage> {
  return requestJson<PlanPackage>(
    `/api/v1/plans/packages/${encodeURIComponent(packageId)}/feedback`,
    {
      method: 'POST',
      body: JSON.stringify(body),
    }
  )
}

export function reviewPlanPackage(packageId: string): Promise<PlanPackage> {
  return requestJson<PlanPackage>(
    `/api/v1/plans/packages/${encodeURIComponent(packageId)}/review`,
    {
      method: 'POST',
      body: JSON.stringify({ reviewerMode: 'hybrid' }),
    }
  )
}

export function reviewPlanPackageWithMode(
  packageId: string,
  reviewerMode: 'deterministic' | 'hybrid'
): Promise<PlanPackage> {
  return requestJson<PlanPackage>(
    `/api/v1/plans/packages/${encodeURIComponent(packageId)}/review`,
    {
      method: 'POST',
      body: JSON.stringify({ reviewerMode }),
    }
  )
}

export function revisePlanPackage(
  packageId: string,
  body: RevisePlanPackageRequest
): Promise<PlanPackage> {
  return requestJson<PlanPackage>(
    `/api/v1/plans/packages/${encodeURIComponent(packageId)}/revise`,
    {
      method: 'POST',
      body: JSON.stringify(body),
    }
  )
}

export function approvePlanPackage(packageId: string): Promise<PlanPackage> {
  return requestJson<PlanPackage>(
    `/api/v1/plans/packages/${encodeURIComponent(packageId)}/approve`,
    {
      method: 'POST',
      body: JSON.stringify({ reviewerMode: 'hybrid' }),
    }
  )
}

export function approvePlanPackageWithMode(
  packageId: string,
  reviewerMode: 'deterministic' | 'hybrid'
): Promise<PlanPackage> {
  return requestJson<PlanPackage>(
    `/api/v1/plans/packages/${encodeURIComponent(packageId)}/approve`,
    {
      method: 'POST',
      body: JSON.stringify({ reviewerMode }),
    }
  )
}
