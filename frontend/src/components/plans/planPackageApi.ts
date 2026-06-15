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
  implementationReady: boolean
  warnings: string[]
  errors: string[]
}

export interface PlanSource {
  ideaSessionId: string
  planSessionId?: string | null
  ideaCandidateId: string
  rankedOutputId?: string | null
  searchTreeId?: string | null
  searchNodeId?: string | null
  pathSeedId?: string | null
  reasoningKgId?: string | null
  literatureMapId?: string | null
  bftsHandoffId?: string | null
  selectedResearchPlanId?: string | null
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
  statement: string
  severity: string
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
  repairRounds: number
  fallbackUsed: boolean
  warnings: string[]
}

export interface PlanPackage {
  schemaVersion: string
  packageId: string
  createdAt: string
  source: PlanSource
  idea: PlanIdeaSummary
  background: PlanBackground
  literatureSurvey: PlanLiteratureSurvey
  gap: PlanGap
  principle: PlanPrinciple
  researchQuestion: string
  hypothesis: string
  constants: Record<string, unknown>
  stages: PlanStage[]
  evidenceTrace: PlanEvidenceTrace
  downstreamContract: Record<string, unknown>
  qualityGate: PlanQualityGate
  generation: PlanGenerationMetadata
  sourceFields: Record<string, string[]>
  rawIdeaOutputs: Record<string, unknown>
}

export interface CreatePlanPackageRequest {
  candidateId?: string
  planSessionId?: string
  maxStages?: number
  maxStepsPerStage?: number
  userNotes?: string
  generationMode?: 'hybrid' | 'deterministic'
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

export interface ConvertPlanPackageResponse {
  packageId: string
  researchPlanId: string
  researchPlan: Record<string, unknown>
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

export function convertPlanPackageToResearchPlan(packageId: string): Promise<ConvertPlanPackageResponse> {
  return requestJson<ConvertPlanPackageResponse>(
    `/api/v1/plans/packages/${encodeURIComponent(packageId)}/to-research-plan`,
    { method: 'POST' }
  )
}
