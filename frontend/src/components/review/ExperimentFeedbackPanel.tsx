import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  FileCheck2,
  FlaskConical,
  History,
  Loader2,
  PlayCircle,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
} from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { API_BASE_URL } from '@/lib/api'
import type { QualityAssessment } from '@/lib/types/scientificResearch'

interface RunArtifact {
  id: string
  filename: string
}

interface CompletedRun {
  id: string
  status: string
  runKind: 'platform' | 'faros'
  planId?: string
  createdAt?: string
  startedAt?: string
  parentRunId?: string
  researchSeriesId?: string
  iterationNumber?: number
  artifacts?: RunArtifact[]
  config?: {
    workplaceName?: string
    model?: string
  }
}

interface MetricDelta {
  name: string
  previous: number
  current: number
  delta: number
  relativeChange?: number
}

interface MetricSnapshot {
  name: string
  value: number
  unit?: string
  split?: string
}

interface MetricGuardrail {
  metric: string
  direction: 'maximize' | 'minimize'
  threshold: number
  enabled: boolean
}

interface GuardrailEvaluation extends Omit<MetricGuardrail, 'enabled'> {
  value?: number
  satisfied: boolean
}

interface ExperimentSeriesProgress {
  researchSeriesId: string
  status: 'continue' | 'completed' | 'blocked'
  stopReason: string
  primaryMetric: string
  direction: 'maximize' | 'minimize'
  roundsObserved: number
  currentIteration: number
  bestIteration?: number
  bestValue?: number
  bestFeasibleIteration?: number
  bestFeasibleValue?: number
  consecutiveNoImprovement: number
  guardrailsSatisfied: boolean
  guardrailViolations: GuardrailEvaluation[]
}

interface ExperimentLoopPolicy {
  primaryMetric: string
  direction: 'maximize' | 'minimize'
  maxIterations: number
  patience: number
  guardrails: Array<Omit<MetricGuardrail, 'enabled'>>
}

type HumanSignoffStage = 'plan' | 'repair' | 'conclusion'
type HumanSignoffStatus = 'pending' | 'approved' | 'rejected' | 'changes_requested'

interface HumanSignoff {
  stage: HumanSignoffStage
  status: HumanSignoffStatus
  storedStatus?: HumanSignoffStatus
  required: boolean
  artifactHash: string
  reviewerRole?: string | null
  reviewerId?: string | null
  rationale?: string
  conditions?: string[]
  decidedAt?: string | null
  stale: boolean
  history: Array<Record<string, unknown>>
}

interface HumanFeedbackState {
  feedbackHash: string
  items: Array<{
    decisionId: string
    stage: HumanSignoffStage
    status: 'changes_requested' | 'rejected'
    reviewerRole: string
    rationale: string
    conditions: string[]
    targetSections: string[]
    decidedAt?: string | null
  }>
  targetSections: string[]
  requiredActions: string[]
  requiresApplication: boolean
  applied: boolean
  staleApplication: boolean
  application?: {
    status: 'applied_to_plan' | 'queued_for_iteration'
    feedbackHash: string
    appliedAt: string
  } | null
}

type HumanConditionVerificationStatus = 'pending' | 'passed' | 'failed' | 'waived'

interface HumanConditionVerificationState {
  required: boolean
  allResolved: boolean
  total: number
  passed: number
  waived: number
  unresolved: number
  conditions: Array<{
    conditionId: string
    stage: HumanSignoffStage
    condition: string
    targetSections: string[]
    status: HumanConditionVerificationStatus
    storedStatus: HumanConditionVerificationStatus
    stale: boolean
    subjectHash: string
    verifierRole?: string
    verifierId?: string
    rationale?: string
    evidenceArtifactIds: string[]
    decidedAt?: string
  }>
}

interface ExperimentFeedbackResponse {
  feedbackId: string
  createdAt: string
  runId: string
  runKind: 'platform' | 'faros'
  parentRunId?: string
  researchSeriesId?: string
  iterationNumber: number
  sourceArtifacts: Record<string, string>
  metricSnapshot: MetricSnapshot[]
  loopPolicy?: ExperimentLoopPolicy
  loopProgress?: ExperimentSeriesProgress
  qualityAssessment: QualityAssessment
  iterationDecision: {
    decision: 'accept_results' | 'revise_plan' | 'rerun_experiment' | 'needs_human'
    rationale: string
    targetSections: string[]
    metricDeltas: MetricDelta[]
    nextActions: string[]
  }
  planFeedback: {
    requested: boolean
    applied: boolean
    packageId?: string
    targetSections: string[]
    reason: string
  }
  humanSignoffs: Record<HumanSignoffStage, HumanSignoff>
  humanFeedback?: HumanFeedbackState
  humanConditionVerifications?: HumanConditionVerificationState
}

interface ExperimentFeedbackHistory extends Omit<ExperimentFeedbackResponse, 'feedbackId'> {
  id: string
  planPackageId?: string
  planRevision?: {
    revisionId?: string
    changedSections?: string[]
  } | null
  nextRunId?: string | null
}

interface SciFactCaseJob {
  jobId: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  createdAt: string
  updatedAt: string
  model: string
  bootstrapSamples: number
  reused: boolean
  runId?: string
  qualityGate?: string
  summaryUrl?: string
  reportUrl?: string
  feedbackId?: string
  error?: string
}

interface SciFactMetricSet {
  'Precision': number
  'Recall': number
  'F1-Score': number
  'Brier Score': number
  'Expected Calibration Error (ECE)': number
  'AUROC': number
}

interface SciFactCaseSummary {
  runId: string
  feedbackResults: { roundOne: SciFactMetricSet; roundTwo: SciFactMetricSet }
  finalHoldout: { roundOne: SciFactMetricSet; roundTwo: SciFactMetricSet }
  qwenTrace: { model: string; usage: { total_tokens: number } }
  qualityGate: { status: string }
  preregistration: { contentHash: string }
  humanSignoff: { status: string }
}

interface ReliabilityMethodScore {
  faultDetectionRate: number
  normalFalseRejectRate: number
  f1: number
  issueLocalizationRate: number
}

interface ReliabilityBenchmarkSummary {
  runId: string
  qualityGate: string
  datasets: string[]
  totalCases: number
  faultyCases: number
  cleanCases: number
  scores: Record<'qwen_only' | 'rules_only' | 'faros_full', ReliabilityMethodScore>
  repairEvaluation: { attempted: number; passed: number; successRate: number }
  qwenModel?: string
  qwenUsage: { total_tokens?: number }
  qwenMisses: Array<{ dataset: string; faultType: string; rationale: string }>
  reportUrl: string
}

const requiredArtifacts = [
  { filename: 'research_dossier.json', label: 'Research dossier', required: true },
  { filename: 'execution_assessment.json', label: 'Execution assessment', required: false },
  { filename: 'experiment_evidence.json', label: 'Experiment evidence', required: true },
]

const decisionLabel: Record<ExperimentFeedbackResponse['iterationDecision']['decision'], string> = {
  accept_results: 'Accept results',
  revise_plan: 'Revise plan',
  rerun_experiment: 'Rerun experiment',
  needs_human: 'Human decision',
}

const signoffStageLabel: Record<HumanSignoffStage, string> = {
  plan: 'Plan approval',
  repair: 'Repair approval',
  conclusion: 'Conclusion release',
}

const signoffStatusLabel: Record<HumanSignoffStatus, string> = {
  pending: 'Pending',
  approved: 'Approved',
  rejected: 'Rejected',
  changes_requested: 'Changes requested',
}

function formatError(detail: unknown) {
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object' && 'message' in detail) {
    return String((detail as { message: unknown }).message)
  }
  return 'Experiment feedback audit failed.'
}

function artifactBasename(filename: string) {
  return filename.replace(/\\/g, '/').split('/').pop()?.toLowerCase() || ''
}

function metricIdentity(name: string) {
  return name.replace(/^[^:]+:/, '').toLowerCase().replace(/[^a-z0-9]+/g, '')
}

function defaultMetricGuardrails(metrics: MetricSnapshot[]): MetricGuardrail[] {
  const baselineMetrics = metrics.filter((metric) => metric.name.toLowerCase().startsWith('baseline:'))
  return metrics
    .filter((metric) => metric.name.toLowerCase().startsWith('method:'))
    .flatMap<MetricGuardrail>((metric) => {
      const identity = metricIdentity(metric.name)
      const baseline = baselineMetrics.find((item) => metricIdentity(item.name) === identity)
      if (!baseline) return []
      if (/f1/.test(identity)) {
        return [{ metric: metric.name, direction: 'maximize' as const, threshold: baseline.value * 0.9, enabled: true }]
      }
      if (/brier/.test(identity)) {
        return [{ metric: metric.name, direction: 'minimize' as const, threshold: baseline.value * 1.2, enabled: true }]
      }
      if (/auroc|rocauc/.test(identity)) {
        return [{ metric: metric.name, direction: 'maximize' as const, threshold: Math.max(0, baseline.value - 0.02), enabled: true }]
      }
      return []
    })
}

function restoreLoopControls(
  data: Pick<ExperimentFeedbackResponse, 'metricSnapshot' | 'loopPolicy' | 'loopProgress'>,
) {
  if (data.loopPolicy) {
    return {
      primaryMetric: data.loopPolicy.primaryMetric,
      direction: data.loopPolicy.direction,
      maxIterations: data.loopPolicy.maxIterations,
      patience: data.loopPolicy.patience,
      guardrails: data.loopPolicy.guardrails.map((guardrail) => ({ ...guardrail, enabled: true })),
      progress: data.loopProgress || null,
    }
  }
  const firstMethodMetric = data.metricSnapshot.find((metric) =>
    metric.name.toLowerCase().startsWith('method:'),
  ) || data.metricSnapshot[0]
  return {
    primaryMetric: firstMethodMetric?.name || '',
    direction: firstMethodMetric && /error|loss|brier/i.test(firstMethodMetric.name)
      ? 'minimize' as const
      : 'maximize' as const,
    maxIterations: 5,
    patience: 3,
    guardrails: defaultMetricGuardrails(data.metricSnapshot),
    progress: null,
  }
}

export function ExperimentFeedbackPanel() {
  const [runs, setRuns] = useState<CompletedRun[]>([])
  const [runsLoading, setRunsLoading] = useState(true)
  const [selectedRunId, setSelectedRunId] = useState('')
  const [planPackageId, setPlanPackageId] = useState('')
  const [applyToPlan, setApplyToPlan] = useState(false)
  const [auditing, setAuditing] = useState(false)
  const [actionLoading, setActionLoading] = useState<'revise' | 'next' | 'start' | ''>('')
  const [actionMessage, setActionMessage] = useState('')
  const [error, setError] = useState('')
  const [result, setResult] = useState<ExperimentFeedbackResponse | null>(null)
  const [history, setHistory] = useState<ExperimentFeedbackHistory[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [planRevised, setPlanRevised] = useState(false)
  const [nextRunId, setNextRunId] = useState('')
  const [primaryMetric, setPrimaryMetric] = useState('')
  const [metricDirection, setMetricDirection] = useState<'maximize' | 'minimize'>('maximize')
  const [maxIterations, setMaxIterations] = useState(5)
  const [loopPatience, setLoopPatience] = useState(3)
  const [loopProgress, setLoopProgress] = useState<ExperimentSeriesProgress | null>(null)
  const [metricGuardrails, setMetricGuardrails] = useState<MetricGuardrail[]>([])
  const [scifactJob, setScifactJob] = useState<SciFactCaseJob | null>(null)
  const [scifactSummary, setScifactSummary] = useState<SciFactCaseSummary | null>(null)
  const [scifactLoading, setScifactLoading] = useState(false)
  const [scifactError, setScifactError] = useState('')
  const [reliabilitySummary, setReliabilitySummary] = useState<ReliabilityBenchmarkSummary | null>(null)
  const [selectedSignoffStage, setSelectedSignoffStage] = useState<HumanSignoffStage>('plan')
  const [reviewerRole, setReviewerRole] = useState('team_lead')
  const [reviewerId, setReviewerId] = useState('')
  const [signoffRationale, setSignoffRationale] = useState('')
  const [signoffConditions, setSignoffConditions] = useState('')
  const [signoffTargetSections, setSignoffTargetSections] = useState('')
  const [signoffLoading, setSignoffLoading] = useState(false)
  const [feedbackApplying, setFeedbackApplying] = useState(false)
  const [selectedConditionId, setSelectedConditionId] = useState('')
  const [conditionRationale, setConditionRationale] = useState('')
  const [conditionEvidenceId, setConditionEvidenceId] = useState('')
  const [conditionLoading, setConditionLoading] = useState(false)
  const [reviewAuthToken, setReviewAuthToken] = useState('')

  const loadScifactSummary = useCallback(async (job: SciFactCaseJob) => {
    if (!job.summaryUrl) return
    const response = await fetch(`${API_BASE_URL}${job.summaryUrl}`)
    if (!response.ok) throw new Error('SciFact summary is unavailable.')
    setScifactSummary(await response.json())
  }, [])

  const loadScifactJob = useCallback(async (jobId?: string) => {
    const suffix = jobId ? encodeURIComponent(jobId) : 'latest'
    const response = await fetch(
      `${API_BASE_URL}/api/v1/reviews/reviewx/competition/scifact/jobs/${suffix}`,
    )
    if (response.status === 404 && !jobId) return null
    if (!response.ok) throw new Error('Failed to load the SciFact competition case.')
    const job = await response.json() as SciFactCaseJob
    setScifactJob(job)
    if (job.status === 'completed') await loadScifactSummary(job)
    return job
  }, [loadScifactSummary])

  const startScifactCase = async () => {
    setScifactLoading(true)
    setScifactError('')
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/v1/reviews/reviewx/competition/scifact/jobs`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ reuseLatest: true, bootstrapSamples: 2000 }),
        },
      )
      if (!response.ok) throw new Error('Failed to start the SciFact competition case.')
      const job = await response.json() as SciFactCaseJob
      setScifactJob(job)
      if (job.status === 'completed') await loadScifactSummary(job)
    } catch (caseError) {
      setScifactError(caseError instanceof Error ? caseError.message : 'SciFact case failed.')
    } finally {
      setScifactLoading(false)
    }
  }

  const openScifactHumanReview = async () => {
    if (!scifactJob?.feedbackId) return
    setAuditing(true)
    setError('')
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/v1/reviews/reviewx/experiment-feedback/${scifactJob.feedbackId}`,
      )
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(formatError(data.detail))
      const feedback = data as ExperimentFeedbackResponse
      setResult(feedback)
      const controls = restoreLoopControls({
        metricSnapshot: feedback.metricSnapshot,
        loopPolicy: feedback.loopPolicy,
        loopProgress: feedback.loopProgress,
      })
      setPrimaryMetric(controls.primaryMetric)
      setMetricDirection(controls.direction)
      setMaxIterations(controls.maxIterations)
      setLoopPatience(controls.patience)
      setMetricGuardrails(controls.guardrails)
      setLoopProgress(controls.progress)
      setPlanRevised(false)
      setNextRunId('')
      setActionMessage('SciFact evidence loaded for human review.')
      void loadHistory(feedback.runId, feedback.researchSeriesId)
    } catch (reviewError) {
      setError(reviewError instanceof Error ? reviewError.message : 'Failed to open SciFact human review.')
    } finally {
      setAuditing(false)
    }
  }

  const loadRuns = useCallback(async () => {
    setRunsLoading(true)
    try {
      const [platformResult, farosResult] = await Promise.allSettled([
        fetch(`${API_BASE_URL}/api/v1/runs?status=completed`).then(async (response) => {
          if (!response.ok) throw new Error('Platform runs unavailable.')
          const data = await response.json()
          return (data.runs || []).map((run: CompletedRun) => ({ ...run, runKind: 'platform' as const }))
        }),
        fetch(`${API_BASE_URL}/api/faros/runs`).then(async (response) => {
          if (!response.ok) throw new Error('FAROS runs unavailable.')
          const data = await response.json()
          return (data.runs || [])
            .filter((run: Record<string, unknown>) => run.status === 'completed')
            .map((run: Record<string, unknown>) => ({
              id: String(run.id),
              status: String(run.status),
              runKind: 'faros' as const,
              createdAt: String(run.created_at || ''),
              startedAt: String(run.started_at || ''),
              parentRunId: run.parent_run_id ? String(run.parent_run_id) : undefined,
              researchSeriesId: String(run.research_series_id || run.id),
              iterationNumber: Number(run.iteration_number || 1),
              artifacts: [],
              config: {
                workplaceName: `FAROS · ${String(run.blueprint_id || 'workflow')} · V${Number(run.iteration_number || 1)}`,
                model: String(run.profile_id || 'default profile'),
              },
            }))
        }),
      ])
      const platformRuns = platformResult.status === 'fulfilled' ? platformResult.value : []
      const farosRuns = farosResult.status === 'fulfilled' ? farosResult.value : []
      if (platformResult.status === 'rejected' && farosResult.status === 'rejected') {
        throw new Error('Failed to load completed runs.')
      }
      setRuns([...farosRuns, ...platformRuns].sort((left, right) =>
        String(right.startedAt || right.createdAt || '').localeCompare(String(left.startedAt || left.createdAt || '')),
      ))
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Failed to load completed runs.')
    } finally {
      setRunsLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadRuns()
    void loadScifactJob().catch(() => undefined)
    void fetch(`${API_BASE_URL}/api/v1/reviews/reviewx/competition/reliability/latest`)
      .then(async (response) => {
        if (!response.ok) return null
        return response.json() as Promise<ReliabilityBenchmarkSummary>
      })
      .then((summary) => setReliabilitySummary(summary))
      .catch(() => undefined)
  }, [loadRuns, loadScifactJob])

  useEffect(() => {
    if (!scifactJob || !['queued', 'running'].includes(scifactJob.status)) return
    const timer = window.setTimeout(() => {
      void loadScifactJob(scifactJob.jobId).catch((caseError) => {
        setScifactError(caseError instanceof Error ? caseError.message : 'SciFact case failed.')
      })
    }, 2000)
    return () => window.clearTimeout(timer)
  }, [scifactJob, loadScifactJob])

  const loadHistory = async (runId: string, researchSeriesId?: string) => {
    if (!runId) {
      setHistory([])
      return
    }
    setHistoryLoading(true)
    try {
      const query = researchSeriesId
        ? `researchSeriesId=${encodeURIComponent(researchSeriesId)}`
        : `runId=${encodeURIComponent(runId)}`
      const response = await fetch(`${API_BASE_URL}/api/v1/reviews/reviewx/experiment-feedback/history?${query}&limit=8`)
      if (!response.ok) throw new Error('Failed to load experiment feedback history.')
      const data = await response.json()
      setHistory(data.records || [])
    } catch (historyError) {
      setError(historyError instanceof Error ? historyError.message : 'Failed to load experiment feedback history.')
    } finally {
      setHistoryLoading(false)
    }
  }

  const selectedRun = useMemo(
    () => runs.find((run) => run.id === selectedRunId),
    [runs, selectedRunId],
  )
  const availableFilenames = useMemo(
    () => new Set((selectedRun?.artifacts || []).map((artifact) => artifactBasename(artifact.filename))),
    [selectedRun],
  )
  const requiredReady = requiredArtifacts
    .filter((artifact) => artifact.required)
    .every((artifact) => availableFilenames.has(artifact.filename))

  const selectRun = async (runId: string) => {
    setSelectedRunId(runId)
    setResult(null)
    setPlanRevised(false)
    setNextRunId('')
    setPrimaryMetric('')
    setMetricGuardrails([])
    setLoopProgress(null)
    setActionMessage('')
    setError('')
    const run = runs.find((item) => item.id === runId)
    if (!run) {
      setHistory([])
      return
    }
    void loadHistory(run.id, run.runKind === 'faros' ? run.researchSeriesId : undefined)
    if (run.runKind !== 'faros') return
    try {
      const response = await fetch(`${API_BASE_URL}/api/faros/runs/${encodeURIComponent(run.id)}/artifacts`)
      if (!response.ok) throw new Error('Failed to load FAROS artifacts.')
      const data = await response.json()
      const artifacts = (data.artifacts || []).map((artifact: Record<string, unknown>) => ({
        id: String(artifact.id),
        filename: String(artifact.filename || artifact.uri || artifact.type || ''),
      }))
      setRuns((current) => current.map((item) => item.id === run.id ? { ...item, artifacts } : item))
    } catch (artifactError) {
      setError(artifactError instanceof Error ? artifactError.message : 'Failed to load FAROS artifacts.')
    }
  }

  const runAudit = async () => {
    if (!selectedRunId) return
    setAuditing(true)
    setError('')
    setResult(null)
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/v1/reviews/reviewx/runs/${selectedRunId}/experiment-feedback`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(reviewAuthToken ? { Authorization: `Bearer ${reviewAuthToken}` } : {}),
          },
          body: JSON.stringify({
            planPackageId: selectedRun?.runKind === 'platform' ? planPackageId || undefined : undefined,
            applyToPlanPackage: selectedRun?.runKind === 'platform' ? applyToPlan : false,
          }),
        },
      )
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(formatError(data.detail))
      setResult(data)
      const controls = restoreLoopControls({
        metricSnapshot: data.metricSnapshot || [],
        loopPolicy: data.loopPolicy,
        loopProgress: data.loopProgress,
      })
      setPrimaryMetric(controls.primaryMetric)
      setMetricDirection(controls.direction)
      setMaxIterations(controls.maxIterations)
      setLoopPatience(controls.patience)
      setMetricGuardrails(controls.guardrails)
      setLoopProgress(controls.progress)
      setPlanRevised(false)
      setNextRunId('')
      setActionMessage('')
      void loadHistory(selectedRunId, data.researchSeriesId)
    } catch (auditError) {
      setError(auditError instanceof Error ? auditError.message : 'Experiment feedback audit failed.')
    } finally {
      setAuditing(false)
    }
  }

  const openHistoryRecord = (record: ExperimentFeedbackHistory) => {
    setResult({
      feedbackId: record.id,
      createdAt: record.createdAt,
      runId: record.runId,
      runKind: record.runKind || 'platform',
      parentRunId: record.parentRunId,
      researchSeriesId: record.researchSeriesId,
      iterationNumber: record.iterationNumber || 1,
      sourceArtifacts: record.sourceArtifacts,
      metricSnapshot: record.metricSnapshot || [],
      loopPolicy: record.loopPolicy,
      loopProgress: record.loopProgress,
      qualityAssessment: record.qualityAssessment,
      iterationDecision: record.iterationDecision,
      planFeedback: record.planFeedback,
      humanSignoffs: record.humanSignoffs,
      humanFeedback: record.humanFeedback,
      humanConditionVerifications: record.humanConditionVerifications,
    })
    setPlanRevised(Boolean(record.planRevision))
    setNextRunId(record.nextRunId || '')
    const controls = restoreLoopControls({
      metricSnapshot: record.metricSnapshot || [],
      loopPolicy: record.loopPolicy,
      loopProgress: record.loopProgress,
    })
    setPrimaryMetric(controls.primaryMetric)
    setMetricDirection(controls.direction)
    setMaxIterations(controls.maxIterations)
    setLoopPatience(controls.patience)
    setMetricGuardrails(controls.guardrails)
    setLoopProgress(controls.progress)
    setActionMessage('')
    setError('')
  }

  const revisePlan = async () => {
    if (!result?.feedbackId) return
    setActionLoading('revise')
    setActionMessage('')
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/v1/reviews/reviewx/experiment-feedback/${result.feedbackId}/revise-plan`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(reviewAuthToken ? { Authorization: `Bearer ${reviewAuthToken}` } : {}),
          },
          body: JSON.stringify({ generationMode: 'deterministic', reviewerMode: 'deterministic' }),
        },
      )
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(formatError(data.detail))
      setPlanRevised(true)
      setActionMessage(`Plan revised: ${data.revisionId || data.status}`)
      const signoffResponse = await fetch(
        `${API_BASE_URL}/api/v1/reviews/reviewx/experiment-feedback/${result.feedbackId}/signoffs`,
      )
      if (signoffResponse.ok) {
        const signoffData = await signoffResponse.json()
        setResult((current) => current ? {
          ...current,
          humanSignoffs: signoffData.humanSignoffs,
          humanFeedback: signoffData.humanFeedback,
          humanConditionVerifications: signoffData.humanConditionVerifications,
        } : current)
      }
      void loadHistory(result.runId, result.researchSeriesId)
    } catch (actionError) {
      setActionMessage(actionError instanceof Error ? actionError.message : 'Plan revision failed.')
    } finally {
      setActionLoading('')
    }
  }

  const createNextRun = async () => {
    if (!result?.feedbackId) return
    setActionLoading('next')
    setActionMessage('')
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/v1/reviews/reviewx/experiment-feedback/${result.feedbackId}/next-run`,
        { method: 'POST' },
      )
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(formatError(data.detail))
      setNextRunId(data.runId)
      setActionMessage(data.reused ? 'Existing next run reopened.' : 'Next experiment run created.')
      void loadHistory(result.runId, result.researchSeriesId)
      void loadRuns()
    } catch (actionError) {
      setActionMessage(actionError instanceof Error ? actionError.message : 'Failed to create next run.')
    } finally {
      setActionLoading('')
    }
  }

  const advanceControlledLoop = async () => {
    if (!result?.runId || !primaryMetric) return
    setActionLoading('next')
    setActionMessage('')
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/v1/reviews/reviewx/runs/${encodeURIComponent(result.runId)}/experiment-loop/advance`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            policy: {
              primaryMetric,
              direction: metricDirection,
              minIterations: 3,
              maxIterations,
              minAbsoluteImprovement: 0.001,
              patience: loopPatience,
              guardrails: metricGuardrails
                .filter((guardrail) => guardrail.enabled && guardrail.metric !== primaryMetric)
                .map(({ metric, direction, threshold }) => ({ metric, direction, threshold })),
            },
          }),
        },
      )
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(formatError(data.detail))
      setLoopProgress(data.progress)
      setNextRunId(data.nextRunId || '')
      setActionMessage(data.nextRunId ? 'Controlled iteration created.' : `Series ${data.progress.status}.`)
      void loadHistory(result.runId, result.researchSeriesId)
      void loadRuns()
    } catch (actionError) {
      setActionMessage(actionError instanceof Error ? actionError.message : 'Failed to advance experiment loop.')
    } finally {
      setActionLoading('')
    }
  }

  const startNextFarosRun = async () => {
    if (!nextRunId) return
    setActionLoading('start')
    setActionMessage('')
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/faros/runs/${encodeURIComponent(nextRunId)}/execute`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ asyncExecution: true }),
        },
      )
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(formatError(data.detail))
      setActionMessage(`Iteration V${data.iteration_number || (result?.iterationNumber || 1) + 1} scheduled.`)
      void loadRuns()
    } catch (actionError) {
      setActionMessage(actionError instanceof Error ? actionError.message : 'Failed to start the FAROS iteration.')
    } finally {
      setActionLoading('')
    }
  }

  const decideSignoff = async (status: Exclude<HumanSignoffStatus, 'pending'>) => {
    if (!result?.feedbackId || !reviewerId.trim() || !signoffRationale.trim()) return
    setSignoffLoading(true)
    setActionMessage('')
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/v1/reviews/reviewx/experiment-feedback/${result.feedbackId}/signoffs/${selectedSignoffStage}`,
        {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
            ...(reviewAuthToken ? { Authorization: `Bearer ${reviewAuthToken}` } : {}),
          },
          body: JSON.stringify({
            status,
            reviewerRole,
            reviewerId: reviewerId.trim(),
            rationale: signoffRationale.trim(),
            conditions: signoffConditions
              .split('\n')
              .map((item) => item.trim())
              .filter(Boolean),
            targetSections: signoffTargetSections
              .split(',')
              .map((item) => item.trim())
              .filter(Boolean),
          }),
        },
      )
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(formatError(data.detail))
      setResult((current) => current ? {
        ...current,
        humanSignoffs: data.humanSignoffs,
        humanFeedback: data.humanFeedback,
        humanConditionVerifications: data.humanConditionVerifications,
      } : current)
      setSignoffRationale('')
      setSignoffConditions('')
      setSignoffTargetSections('')
      setActionMessage(`${selectedSignoffStage} signoff: ${status.replace('_', ' ')}`)
      void loadHistory(result.runId, result.researchSeriesId)
    } catch (signoffError) {
      setActionMessage(signoffError instanceof Error ? signoffError.message : 'Human signoff failed.')
    } finally {
      setSignoffLoading(false)
    }
  }

  const applyHumanFeedback = async () => {
    if (!result?.feedbackId) return
    setFeedbackApplying(true)
    setActionMessage('')
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/v1/reviews/reviewx/experiment-feedback/${result.feedbackId}/human-feedback/apply`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ generationMode: 'deterministic', reviewerMode: 'deterministic' }),
        },
      )
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(formatError(data.detail))
      setResult((current) => current ? {
        ...current,
        humanSignoffs: data.humanSignoffs,
        humanFeedback: data.humanFeedback,
        humanConditionVerifications: data.humanConditionVerifications,
      } : current)
      if (data.planRevision) setPlanRevised(true)
      setActionMessage(
        data.status === 'applied_to_plan'
          ? 'Human feedback applied to a new PlanPackage revision.'
          : 'Human feedback queued in the next FAROS iteration contract.',
      )
      void loadHistory(result.runId, result.researchSeriesId)
    } catch (feedbackError) {
      setActionMessage(feedbackError instanceof Error ? feedbackError.message : 'Failed to apply human feedback.')
    } finally {
      setFeedbackApplying(false)
    }
  }

  const verifyHumanCondition = async (status: Exclude<HumanConditionVerificationStatus, 'pending'>) => {
    if (!result?.feedbackId || !selectedConditionId || !reviewerId.trim() || !conditionRationale.trim()) return
    if (status === 'passed' && !conditionEvidenceId) return
    setConditionLoading(true)
    setActionMessage('')
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/v1/reviews/reviewx/experiment-feedback/${result.feedbackId}/human-feedback/conditions/${selectedConditionId}`,
        {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
            ...(reviewAuthToken ? { Authorization: `Bearer ${reviewAuthToken}` } : {}),
          },
          body: JSON.stringify({
            status,
            verifierRole: reviewerRole,
            verifierId: reviewerId.trim(),
            rationale: conditionRationale.trim(),
            evidenceArtifactIds: conditionEvidenceId ? [conditionEvidenceId] : [],
          }),
        },
      )
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(formatError(data.detail))
      setResult((current) => current ? {
        ...current,
        humanSignoffs: data.humanSignoffs,
        humanConditionVerifications: data.humanConditionVerifications,
      } : current)
      setConditionRationale('')
      setConditionEvidenceId('')
      setActionMessage(`Acceptance condition marked ${status}.`)
      void loadHistory(result.runId, result.researchSeriesId)
    } catch (conditionError) {
      setActionMessage(conditionError instanceof Error ? conditionError.message : 'Condition verification failed.')
    } finally {
      setConditionLoading(false)
    }
  }

  const gate = result?.qualityAssessment.gateStatus
  const gateTone = gate === 'pass' ? 'secondary' : gate === 'fail' ? 'destructive' : 'default'
  const planSignoff = result?.humanSignoffs?.plan
  const repairSignoff = result?.humanSignoffs?.repair
  const conclusionSignoff = result?.humanSignoffs?.conclusion
  const iterationHumanReady = Boolean(
    (!result?.humanFeedback?.requiresApplication || result.humanFeedback.applied)
    && planSignoff?.status === 'approved'
    && (!repairSignoff?.required || repairSignoff.status === 'approved'),
  )
  const publicationReady = Boolean(
    (!result?.humanFeedback?.requiresApplication || result.humanFeedback.applied)
    && planSignoff?.status === 'approved'
    && (!repairSignoff?.required || repairSignoff.status === 'approved')
    && conclusionSignoff?.status === 'approved'
    && (!result?.humanConditionVerifications?.required || result.humanConditionVerifications.allResolved)
    && gate !== 'fail'
    && !result?.qualityAssessment.findings.some((finding) => finding.severity === 'blocker'),
  )

  return (
    <Card className="mb-6 border-emerald-200 shadow-md">
      <CardHeader className="border-b bg-emerald-50/60 pb-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-md bg-emerald-700 text-white">
              <FlaskConical className="h-5 w-5" />
            </div>
            <div>
              <CardTitle className="text-lg">Experiment Feedback Gate</CardTitle>
              <div className="mt-1 text-xs text-slate-600">Direction B · evidence-driven iteration</div>
            </div>
          </div>
          {result && (
            <div className="flex items-center gap-2">
              <Badge variant={gateTone}>Gate: {gate}</Badge>
              <Badge variant="outline">{decisionLabel[result.iterationDecision.decision]}</Badge>
            </div>
          )}
        </div>
      </CardHeader>
      <CardContent className="pt-5">
        <section className="mb-5 border-b border-slate-200 pb-5" aria-label="SciFact competition case">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                <FileCheck2 className="h-4 w-4 text-emerald-700" />
                Official SciFact two-round case
              </div>
              <div className="mt-1 text-xs text-slate-600">
                Preregistered plan · frozen benchmark · ReviewX feedback · real Qwen planning · untouched dev holdout
              </div>
            </div>
            <div className="flex items-center gap-2">
              {scifactJob && <Badge variant={scifactJob.qualityGate === 'passed' ? 'secondary' : 'outline'}>{scifactJob.status}</Badge>}
              <Button
                variant="outline"
                onClick={() => void startScifactCase()}
                disabled={scifactLoading || scifactJob?.status === 'queued' || scifactJob?.status === 'running'}
              >
                {scifactLoading || scifactJob?.status === 'queued' || scifactJob?.status === 'running'
                  ? <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  : <PlayCircle className="mr-2 h-4 w-4" />}
                {scifactSummary ? 'Load verified case' : 'Run official case'}
              </Button>
            </div>
          </div>
          {scifactError && (
            <div className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{scifactError}</div>
          )}
          {scifactSummary && scifactJob && (
            <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <div className="min-w-0 border-l-2 border-emerald-600 pl-3">
                <div className="text-[11px] uppercase text-slate-500">Feedback F1</div>
                <div className="mt-1 font-mono text-sm font-semibold text-slate-900">
                  {scifactSummary.feedbackResults.roundOne['F1-Score'].toFixed(4)} → {scifactSummary.feedbackResults.roundTwo['F1-Score'].toFixed(4)}
                </div>
              </div>
              <div className="min-w-0 border-l-2 border-amber-500 pl-3">
                <div className="text-[11px] uppercase text-slate-500">Untouched dev F1</div>
                <div className="mt-1 font-mono text-sm font-semibold text-slate-900">
                  {scifactSummary.finalHoldout.roundOne['F1-Score'].toFixed(4)} → {scifactSummary.finalHoldout.roundTwo['F1-Score'].toFixed(4)}
                </div>
              </div>
              <div className="min-w-0 border-l-2 border-slate-400 pl-3">
                <div className="text-[11px] uppercase text-slate-500">Qwen evidence</div>
                <div className="mt-1 truncate text-sm font-semibold text-slate-900">
                  {scifactSummary.qwenTrace.usage.total_tokens} tokens
                </div>
              </div>
              <div className="min-w-0 border-l-2 border-slate-400 pl-3">
                <div className="text-[11px] uppercase text-slate-500">Evidence state</div>
                <div className="mt-1 flex flex-wrap gap-2 text-sm font-semibold text-slate-900">
                  <span>{scifactSummary.qualityGate.status}</span>
                  <span className="font-normal text-slate-500">Human: {scifactSummary.humanSignoff.status}</span>
                </div>
              </div>
              <div className="flex flex-wrap gap-3 text-xs sm:col-span-2 lg:col-span-4">
                {scifactJob.reportUrl && (
                  <a className="font-medium text-emerald-700 hover:underline" href={`${API_BASE_URL}${scifactJob.reportUrl}`} target="_blank" rel="noreferrer">
                    Experiment report
                  </a>
                )}
                {scifactJob.summaryUrl && (
                  <a className="font-medium text-emerald-700 hover:underline" href={`${API_BASE_URL}${scifactJob.summaryUrl}`} target="_blank" rel="noreferrer">
                    Evidence summary
                  </a>
                )}
                {scifactJob.feedbackId && (
                  <button
                    type="button"
                    className="font-medium text-emerald-700 hover:underline"
                    onClick={() => void openScifactHumanReview()}
                    disabled={auditing}
                  >
                    Open human review
                  </button>
                )}
                <span className="truncate text-slate-500">Plan hash: {scifactSummary.preregistration.contentHash}</span>
              </div>
            </div>
          )}
        </section>
        {reliabilitySummary && (
          <section className="mb-5 border-b border-slate-200 pb-5" aria-label="Scientific reliability benchmark">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                  <ShieldCheck className="h-4 w-4 text-emerald-700" />
                  Scientific reliability benchmark
                </div>
                <div className="mt-1 text-xs text-slate-600">
                  {reliabilitySummary.datasets.join(' · ')} · {reliabilitySummary.faultyCases} controlled faults · {reliabilitySummary.cleanCases} paired controls
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Badge variant={reliabilitySummary.qualityGate === 'passed' ? 'secondary' : 'outline'}>
                  Gate: {reliabilitySummary.qualityGate}
                </Badge>
                <a
                  className="text-xs font-medium text-emerald-700 hover:underline"
                  href={`${API_BASE_URL}${reliabilitySummary.reportUrl}`}
                  target="_blank"
                  rel="noreferrer"
                >
                  Full report
                </a>
              </div>
            </div>
            <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {([
                ['Qwen-only', reliabilitySummary.scores.qwen_only],
                ['Structure rules', reliabilitySummary.scores.rules_only],
                ['FAROS + ReviewX', reliabilitySummary.scores.faros_full],
              ] as const).map(([label, score], index) => (
                <div key={label} className={`min-w-0 border-l-2 pl-3 ${index === 2 ? 'border-emerald-600' : 'border-slate-400'}`}>
                  <div className="text-[11px] uppercase text-slate-500">{label}</div>
                  <div className="mt-1 font-mono text-lg font-semibold text-slate-900">
                    {(score.faultDetectionRate * 100).toFixed(1)}%
                  </div>
                  <div className="text-[11px] text-slate-500">
                    false reject {(score.normalFalseRejectRate * 100).toFixed(1)}%
                  </div>
                </div>
              ))}
              <div className="min-w-0 border-l-2 border-amber-500 pl-3">
                <div className="text-[11px] uppercase text-slate-500">Repair replay</div>
                <div className="mt-1 font-mono text-lg font-semibold text-slate-900">
                  {reliabilitySummary.repairEvaluation.passed}/{reliabilitySummary.repairEvaluation.attempted}
                </div>
                <div className="text-[11px] text-slate-500">
                  {reliabilitySummary.qwenUsage.total_tokens || 0} Qwen tokens
                </div>
              </div>
            </div>
            {reliabilitySummary.qwenMisses.length > 0 && (
              <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-950">
                Qwen-only missed {reliabilitySummary.qwenMisses.length} faults; FAROS rejected all through record recomputation, split isolation, and evidence hashing.
              </div>
            )}
          </section>
        )}
        <div className="grid gap-5 xl:grid-cols-[minmax(280px,0.9fr)_minmax(0,1.4fr)]">
          <div className="space-y-4">
            <div className="flex gap-2">
              <select
                value={selectedRunId}
                onChange={(event) => {
                  void selectRun(event.target.value)
                }}
                className="min-w-0 flex-1 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
                disabled={runsLoading}
              >
                <option value="">Select completed run...</option>
                {runs.map((run) => (
                  <option key={run.id} value={run.id}>
                    {run.config?.workplaceName || run.id} · {run.config?.model || 'unknown model'} · {run.runKind}
                  </option>
                ))}
              </select>
              <Button variant="outline" size="icon" onClick={() => void loadRuns()} disabled={runsLoading} title="Refresh runs">
                <RefreshCw className={`h-4 w-4 ${runsLoading ? 'animate-spin' : ''}`} />
              </Button>
            </div>

            <div className="grid gap-2 sm:grid-cols-3 xl:grid-cols-1 2xl:grid-cols-3">
              {requiredArtifacts.map((artifact) => {
                const present = availableFilenames.has(artifact.filename)
                return (
                  <div key={artifact.filename} className="flex min-h-12 items-center gap-2 rounded-md border border-slate-200 px-3 py-2">
                    {present ? (
                      <FileCheck2 className="h-4 w-4 shrink-0 text-emerald-700" />
                    ) : (
                      <AlertTriangle className={`h-4 w-4 shrink-0 ${artifact.required ? 'text-amber-600' : 'text-slate-400'}`} />
                    )}
                    <div className="min-w-0">
                      <div className="truncate text-xs font-medium text-slate-800">{artifact.label}</div>
                      <div className="text-[11px] text-slate-500">{present ? 'Ready' : artifact.required ? 'Required' : 'Optional'}</div>
                    </div>
                  </div>
                )
              })}
            </div>

            {selectedRun?.runKind === 'faros' ? (
              <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-900">
                ReviewX feedback will be carried into the next FAROS iteration automatically.
              </div>
            ) : (
              <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] xl:grid-cols-1 2xl:grid-cols-[minmax(0,1fr)_auto]">
                <input
                  value={planPackageId}
                  onChange={(event) => setPlanPackageId(event.target.value)}
                  placeholder="PlanPackage ID (optional)"
                  className="min-w-0 rounded-md border border-slate-300 px-3 py-2 text-sm"
                />
                <label className="flex min-h-10 items-center gap-2 text-sm text-slate-700">
                  <input
                    type="checkbox"
                    checked={applyToPlan}
                    onChange={(event) => setApplyToPlan(event.target.checked)}
                    className="h-4 w-4"
                  />
                  Write correction
                </label>
              </div>
            )}
            <Button className="w-full" onClick={() => void runAudit()} disabled={!selectedRunId || auditing}>
              {auditing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <FlaskConical className="mr-2 h-4 w-4" />}
              Audit Experiment Iteration
            </Button>
            {selectedRunId && !requiredReady && !error && (
              <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                Required contract artifacts are not registered for this run.
              </div>
            )}
            {error && (
              <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{error}</div>
            )}
            {selectedRunId && (
              <div className="border-t border-slate-200 pt-3">
                <div className="mb-2 flex items-center justify-between">
                  <div className="flex items-center gap-2 text-xs font-semibold uppercase text-slate-600">
                    <History className="h-4 w-4" />
                    Experiment history
                  </div>
                  <Badge variant="outline">{history.length}</Badge>
                </div>
                {historyLoading ? (
                  <div className="flex items-center gap-2 py-3 text-xs text-slate-500">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading history
                  </div>
                ) : history.length === 0 ? (
                  <div className="py-2 text-xs text-slate-500">No experiment feedback yet.</div>
                ) : (
                  <div className="space-y-2">
                    {history.slice(0, 4).map((record) => (
                      <button
                        key={record.id}
                        type="button"
                        onClick={() => openHistoryRecord(record)}
                        className="flex w-full items-center justify-between gap-3 rounded-md border border-slate-200 px-3 py-2 text-left hover:bg-slate-50"
                      >
                        <div className="min-w-0">
                          <div className="truncate text-xs font-medium text-slate-800">
                            V{record.iterationNumber || 1} · {decisionLabel[record.iterationDecision.decision]}
                          </div>
                          <div className="mt-0.5 text-[11px] text-slate-500">
                            {new Date(record.createdAt).toLocaleString()}
                          </div>
                        </div>
                        <div className="flex shrink-0 gap-1">
                          {record.planRevision && <Badge variant="secondary">Revised</Badge>}
                          {record.nextRunId && <Badge variant="outline">Next run</Badge>}
                        </div>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="min-h-48 border-t border-slate-200 pt-4 xl:border-l xl:border-t-0 xl:pl-5 xl:pt-0">
            {!result ? (
              <div className="flex min-h-44 items-center justify-center text-sm text-slate-500">
                {selectedRun ? 'Run the evidence gate to route the next iteration.' : 'Select a completed experiment run.'}
              </div>
            ) : (
              <div className="space-y-4">
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                  {Object.entries(result.qualityAssessment.dimensionScores).map(([name, score]) => (
                    <div key={name} className="rounded-md border border-slate-200 px-3 py-2">
                      <div className="truncate text-[11px] uppercase text-slate-500">{name.replace(/([A-Z])/g, ' $1')}</div>
                      <div className="mt-1 text-lg font-semibold text-slate-900">{Math.round(score * 100)}%</div>
                    </div>
                  ))}
                </div>

                <div className="flex items-start gap-3 rounded-md border border-slate-200 p-3">
                  {gate === 'pass' ? (
                    <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-700" />
                  ) : (
                    <ArrowRight className="mt-0.5 h-5 w-5 shrink-0 text-amber-700" />
                  )}
                  <div className="min-w-0">
                    <div className="text-sm font-semibold text-slate-900">{decisionLabel[result.iterationDecision.decision]}</div>
                    <div className="mt-1 text-sm leading-5 text-slate-600">{result.iterationDecision.rationale}</div>
                    {result.iterationDecision.targetSections.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {result.iterationDecision.targetSections.map((section) => (
                          <Badge key={section} variant="outline">{section}</Badge>
                        ))}
                      </div>
                    )}
                  </div>
                </div>

                {result.iterationDecision.metricDeltas.length > 0 && (
                  <div className="overflow-x-auto rounded-md border border-slate-200">
                    <table className="w-full text-sm">
                      <thead className="bg-slate-50 text-left text-xs text-slate-600">
                        <tr>
                          <th className="px-3 py-2">Metric</th>
                          <th className="px-3 py-2 text-right">Previous</th>
                          <th className="px-3 py-2 text-right">Current</th>
                          <th className="px-3 py-2 text-right">Delta</th>
                        </tr>
                      </thead>
                      <tbody>
                        {result.iterationDecision.metricDeltas.map((metric) => (
                          <tr key={metric.name} className="border-t border-slate-200">
                            <td className="px-3 py-2 font-medium">{metric.name}</td>
                            <td className="px-3 py-2 text-right font-mono">{metric.previous}</td>
                            <td className="px-3 py-2 text-right font-mono">{metric.current}</td>
                            <td className="px-3 py-2 text-right font-mono">{metric.delta > 0 ? '+' : ''}{metric.delta}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {result.runKind === 'faros' && result.metricSnapshot.length > 0 && (
                  <div className="grid gap-3 border-t border-slate-200 pt-4">
                    <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto_5rem_5rem]">
                    <select
                      value={primaryMetric}
                      onChange={(event) => {
                        const metric = event.target.value
                        setPrimaryMetric(metric)
                        setMetricDirection(/error|loss|brier/i.test(metric) ? 'minimize' : 'maximize')
                      }}
                      className="min-w-0 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
                    >
                      {result.metricSnapshot.map((metric) => (
                        <option key={`${metric.name}-${metric.split || ''}`} value={metric.name}>
                          {metric.name} · {metric.value.toFixed(4)}
                        </option>
                      ))}
                    </select>
                    <div className="grid grid-cols-2 rounded-md border border-slate-300 p-0.5">
                      {(['maximize', 'minimize'] as const).map((direction) => (
                        <button
                          key={direction}
                          type="button"
                          onClick={() => setMetricDirection(direction)}
                          className={`min-h-8 px-3 text-xs font-medium ${
                            metricDirection === direction
                              ? 'rounded bg-slate-900 text-white'
                              : 'text-slate-600 hover:text-slate-900'
                          }`}
                        >
                          {direction === 'maximize' ? 'Maximize' : 'Minimize'}
                        </button>
                      ))}
                    </div>
                    <label className="min-w-0 text-[11px] font-medium uppercase text-slate-500">
                      Max rounds
                      <input
                        type="number"
                        min={3}
                        max={20}
                        value={maxIterations}
                        onChange={(event) => setMaxIterations(Math.max(3, Math.min(20, Number(event.target.value) || 3)))}
                        className="mt-1 h-8 w-full rounded-md border border-slate-300 px-2 text-sm font-medium text-slate-900"
                      />
                    </label>
                    <label className="min-w-0 text-[11px] font-medium uppercase text-slate-500">
                      Patience
                      <input
                        type="number"
                        min={1}
                        max={10}
                        value={loopPatience}
                        onChange={(event) => setLoopPatience(Math.max(1, Math.min(10, Number(event.target.value) || 1)))}
                        className="mt-1 h-8 w-full rounded-md border border-slate-300 px-2 text-sm font-medium text-slate-900"
                      />
                    </label>
                    </div>
                    {metricGuardrails.length > 0 && (
                      <div className="grid gap-2 border-t border-slate-200 pt-3 sm:grid-cols-3">
                        {metricGuardrails.map((guardrail, index) => (
                          <label
                            key={guardrail.metric}
                            className={`grid min-w-0 grid-cols-[auto_minmax(0,1fr)_5rem] items-center gap-2 rounded-md border px-2 py-2 text-xs ${
                              guardrail.metric === primaryMetric ? 'border-slate-200 bg-slate-50 text-slate-400' : 'border-slate-300 bg-white text-slate-700'
                            }`}
                          >
                            <input
                              type="checkbox"
                              checked={guardrail.enabled && guardrail.metric !== primaryMetric}
                              disabled={guardrail.metric === primaryMetric}
                              onChange={(event) => setMetricGuardrails((current) => current.map((item, itemIndex) =>
                                itemIndex === index ? { ...item, enabled: event.target.checked } : item,
                              ))}
                            />
                            <span className="truncate" title={guardrail.metric}>
                              {guardrail.metric.replace(/^method:/i, '')} {guardrail.direction === 'maximize' ? '≥' : '≤'}
                            </span>
                            <input
                              type="number"
                              step="0.001"
                              value={Number(guardrail.threshold.toFixed(4))}
                              disabled={!guardrail.enabled || guardrail.metric === primaryMetric}
                              onChange={(event) => setMetricGuardrails((current) => current.map((item, itemIndex) =>
                                itemIndex === index ? { ...item, threshold: Number(event.target.value) || 0 } : item,
                              ))}
                              className="h-7 w-full rounded border border-slate-300 px-1.5 font-mono text-xs text-slate-900 disabled:bg-slate-100"
                            />
                          </label>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {loopProgress && (
                  <div className="grid gap-2 rounded-md border border-slate-200 bg-slate-50 px-3 py-3 sm:grid-cols-5">
                    <div>
                      <div className="text-[11px] uppercase text-slate-500">Series</div>
                      <div className="mt-1 text-sm font-semibold text-slate-900">{loopProgress.status}</div>
                    </div>
                    <div>
                      <div className="text-[11px] uppercase text-slate-500">Iteration</div>
                      <div className="mt-1 text-sm font-semibold text-slate-900">V{loopProgress.currentIteration}</div>
                    </div>
                    <div>
                      <div className="text-[11px] uppercase text-slate-500">Feasible best</div>
                      <div className="mt-1 text-sm font-semibold text-slate-900">
                        {loopProgress.bestFeasibleValue === undefined
                          ? 'N/A'
                          : `${loopProgress.bestFeasibleValue.toFixed(4)} · V${loopProgress.bestFeasibleIteration}`}
                      </div>
                    </div>
                    <div>
                      <div className="text-[11px] uppercase text-slate-500">Stop condition</div>
                      <div className="mt-1 break-words text-xs font-medium text-slate-700">
                        {loopProgress.stopReason.replace(/_/g, ' ')}
                      </div>
                    </div>
                    <div>
                      <div className="text-[11px] uppercase text-slate-500">Guardrails</div>
                      <div className={`mt-1 text-xs font-semibold ${loopProgress.guardrailsSatisfied ? 'text-emerald-700' : 'text-amber-700'}`}>
                        {loopProgress.guardrailsSatisfied
                          ? 'All satisfied'
                          : loopProgress.guardrailViolations.map((item) => item.metric.replace(/^method:/i, '')).join(', ')}
                      </div>
                    </div>
                  </div>
                )}

                <div className="grid gap-4 md:grid-cols-2">
                  <div>
                    <div className="mb-2 text-xs font-semibold uppercase text-slate-600">Next actions</div>
                    <div className="space-y-2">
                      {result.iterationDecision.nextActions.slice(0, 4).map((action, index) => (
                        <div key={`${index}-${action}`} className="flex gap-2 text-sm text-slate-700">
                          <span className="font-semibold text-emerald-700">{index + 1}.</span>
                          <span>{action}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                  <div>
                    <div className="mb-2 text-xs font-semibold uppercase text-slate-600">Evidence & feedback</div>
                    <div className="space-y-2 text-sm text-slate-700">
                      <div>{Object.keys(result.sourceArtifacts).length} contract artifacts verified</div>
                      <div>{result.qualityAssessment.findings.length} quality findings</div>
                      <div className={result.planFeedback.applied ? 'text-emerald-700' : 'text-slate-500'}>
                        {result.planFeedback.applied ? 'PlanPackage correction attached' : result.planFeedback.reason || 'No PlanPackage write requested'}
                      </div>
                      {result.humanFeedback?.requiresApplication && (
                        <div className={result.humanFeedback.applied ? 'text-emerald-700' : 'font-medium text-amber-700'}>
                          Human feedback {result.humanFeedback.applied ? 'applied' : 'awaiting application'}
                        </div>
                      )}
                    </div>
                  </div>
                </div>

                <section className="border-t border-slate-200 pt-4" aria-label="Human oversight">
                  <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2 text-xs font-semibold uppercase text-slate-600">
                      <ShieldCheck className="h-4 w-4" />
                      Human oversight
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button asChild size="sm" variant="outline">
                        <a
                          href={`${API_BASE_URL}/api/v1/reviews/reviewx/experiment-feedback/${encodeURIComponent(result.feedbackId)}/evidence-bundle?release=draft`}
                          target="_blank"
                          rel="noreferrer"
                        >
                          <FileCheck2 className="mr-2 h-4 w-4" />
                          Draft bundle
                        </a>
                      </Button>
                      {publicationReady ? (
                        <Button asChild size="sm">
                          <a
                            href={`${API_BASE_URL}/api/v1/reviews/reviewx/experiment-feedback/${encodeURIComponent(result.feedbackId)}/evidence-bundle?release=official`}
                            target="_blank"
                            rel="noreferrer"
                          >
                            <FileCheck2 className="mr-2 h-4 w-4" />
                            Official bundle
                          </a>
                        </Button>
                      ) : (
                        <Button size="sm" disabled>
                          <FileCheck2 className="mr-2 h-4 w-4" />
                          Official bundle
                        </Button>
                      )}
                    </div>
                  </div>

                  <div className="grid gap-3">
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                      {(Object.keys(signoffStageLabel) as HumanSignoffStage[]).map((stage) => {
                        const signoff = result.humanSignoffs[stage]
                        const active = selectedSignoffStage === stage
                        return (
                          <button
                            key={stage}
                            type="button"
                            onClick={() => setSelectedSignoffStage(stage)}
                            className={`min-w-0 rounded-md border px-3 py-3 text-left ${
                              active
                                ? 'border-emerald-700 bg-emerald-50'
                                : 'border-slate-200 bg-white hover:border-slate-300'
                            }`}
                          >
                            <div className="flex items-center justify-between gap-2">
                              <span className="truncate text-xs font-semibold text-slate-900">{signoffStageLabel[stage]}</span>
                              <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${
                                signoff.status === 'approved'
                                  ? 'bg-emerald-600'
                                  : signoff.status === 'pending'
                                    ? 'bg-amber-500'
                                    : 'bg-red-600'
                              }`} />
                            </div>
                            <div className="mt-2 flex flex-wrap items-center gap-1.5">
                              <Badge variant={signoff.status === 'approved' ? 'secondary' : 'outline'}>
                                {signoffStatusLabel[signoff.status]}
                              </Badge>
                              <Badge variant="outline">{signoff.required ? 'Required' : 'Optional'}</Badge>
                              {signoff.stale && <Badge variant="destructive">Stale</Badge>}
                            </div>
                            <div className="mt-2 truncate font-mono text-[10px] text-slate-500" title={signoff.artifactHash}>
                              SHA-256 {signoff.artifactHash.slice(0, 12)}
                            </div>
                          </button>
                        )
                      })}
                    </div>

                    {result.humanFeedback?.requiresApplication && (
                      <div className={`flex flex-wrap items-center justify-between gap-3 rounded-md border px-3 py-3 ${
                        result.humanFeedback.applied
                          ? 'border-emerald-200 bg-emerald-50'
                          : 'border-amber-200 bg-amber-50'
                      }`}>
                        <div className="min-w-0">
                          <div className="text-sm font-semibold text-slate-900">
                            {result.humanFeedback.requiredActions.length} human action{result.humanFeedback.requiredActions.length === 1 ? '' : 's'}
                            {' · '}{result.humanFeedback.applied ? 'Applied' : 'Application required'}
                          </div>
                          <div className="mt-1 truncate font-mono text-[10px] text-slate-600" title={result.humanFeedback.feedbackHash}>
                            SHA-256 {result.humanFeedback.feedbackHash.slice(0, 24)}
                          </div>
                        </div>
                        {!result.humanFeedback.applied && (
                          <Button
                            size="sm"
                            onClick={() => void applyHumanFeedback()}
                            disabled={feedbackApplying}
                          >
                            {feedbackApplying ? (
                              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            ) : (
                              <RotateCcw className="mr-2 h-4 w-4" />
                            )}
                            Apply human feedback
                          </Button>
                        )}
                      </div>
                    )}

                    {result.humanConditionVerifications?.required && (
                      <div className="grid gap-3 rounded-md border border-slate-200 bg-white p-3">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div>
                            <div className="text-sm font-semibold text-slate-900">Inherited acceptance conditions</div>
                            <div className="mt-1 text-xs text-slate-600">
                              {result.humanConditionVerifications.passed} passed
                              {' · '}{result.humanConditionVerifications.waived} waived
                              {' · '}{result.humanConditionVerifications.unresolved} unresolved
                            </div>
                          </div>
                          <Badge variant={result.humanConditionVerifications.allResolved ? 'secondary' : 'outline'}>
                            {result.humanConditionVerifications.allResolved ? 'Resolved' : 'Blocks conclusion'}
                          </Badge>
                        </div>

                        <div className="grid gap-2">
                          {result.humanConditionVerifications.conditions.map((condition) => (
                            <button
                              key={condition.conditionId}
                              type="button"
                              onClick={() => {
                                setSelectedConditionId(condition.conditionId)
                                setConditionEvidenceId(condition.evidenceArtifactIds[0] || '')
                              }}
                              className={`min-w-0 rounded-md border px-3 py-2 text-left ${
                                selectedConditionId === condition.conditionId
                                  ? 'border-emerald-700 bg-emerald-50'
                                  : 'border-slate-200 hover:border-slate-300'
                              }`}
                            >
                              <div className="flex flex-wrap items-start justify-between gap-2">
                                <span className="min-w-0 flex-1 text-sm text-slate-800">{condition.condition}</span>
                                <span className={`text-xs font-semibold ${
                                  condition.status === 'passed' || condition.status === 'waived'
                                    ? 'text-emerald-700'
                                    : condition.status === 'failed'
                                      ? 'text-red-700'
                                      : 'text-amber-700'
                                }`}>
                                  {condition.stale ? `${condition.storedStatus} · stale` : condition.status}
                                </span>
                              </div>
                              <div className="mt-1 truncate font-mono text-[10px] text-slate-500" title={condition.subjectHash}>
                                Evidence state {condition.subjectHash.slice(0, 19)}
                              </div>
                            </button>
                          ))}
                        </div>

                        {selectedConditionId && (
                          <div className="grid gap-2 border-t border-slate-200 pt-3">
                            <select
                              value={conditionEvidenceId}
                              onChange={(event) => setConditionEvidenceId(event.target.value)}
                              className="min-w-0 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
                              aria-label="Condition evidence artifact"
                            >
                              <option value="">Select current evidence artifact</option>
                              {Object.entries(result.sourceArtifacts).map(([label, artifactId]) => (
                                <option key={`${label}-${artifactId}`} value={artifactId}>{label}</option>
                              ))}
                            </select>
                            <textarea
                              value={conditionRationale}
                              onChange={(event) => setConditionRationale(event.target.value)}
                              placeholder="Verification rationale"
                              rows={2}
                              className="min-w-0 resize-y rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
                            />
                            <div className="flex flex-wrap gap-2">
                              <Button
                                size="sm"
                                onClick={() => void verifyHumanCondition('passed')}
                                disabled={conditionLoading || !reviewerId.trim() || !conditionRationale.trim() || !conditionEvidenceId}
                              >
                                {conditionLoading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <CheckCircle2 className="mr-2 h-4 w-4" />}
                                Pass with evidence
                              </Button>
                              <Button
                                size="sm"
                                variant="destructive"
                                onClick={() => void verifyHumanCondition('failed')}
                                disabled={conditionLoading || !reviewerId.trim() || !conditionRationale.trim()}
                              >
                                <AlertTriangle className="mr-2 h-4 w-4" />
                                Fail
                              </Button>
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => void verifyHumanCondition('waived')}
                                disabled={conditionLoading || !reviewerId.trim() || !conditionRationale.trim()}
                              >
                                Waive
                              </Button>
                            </div>
                          </div>
                        )}
                      </div>
                    )}

                    <div className="grid gap-2 rounded-md border border-slate-200 bg-slate-50 p-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="text-sm font-semibold text-slate-900">
                          {signoffStageLabel[selectedSignoffStage]}
                        </div>
                        {result.humanSignoffs[selectedSignoffStage].stale && (
                          <span className="text-xs font-medium text-red-700">Evidence changed; review again</span>
                        )}
                      </div>
                      <div className="grid gap-2 sm:grid-cols-2">
                        <select
                          value={reviewerRole}
                          onChange={(event) => setReviewerRole(event.target.value)}
                          className="min-w-0 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
                          aria-label="Reviewer role"
                        >
                          <option value="team_lead">Team lead</option>
                          <option value="domain_expert">Domain expert</option>
                          <option value="safety_reviewer">Safety reviewer</option>
                        </select>
                        <input
                          value={reviewerId}
                          onChange={(event) => setReviewerId(event.target.value)}
                          placeholder="Reviewer identity"
                          className="min-w-0 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
                        />
                        <input
                          type="password"
                          value={reviewAuthToken}
                          onChange={(event) => setReviewAuthToken(event.target.value)}
                          placeholder="Review bearer token"
                          autoComplete="off"
                          className="min-w-0 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm sm:col-span-2"
                        />
                      </div>
                      <textarea
                        value={signoffRationale}
                        onChange={(event) => setSignoffRationale(event.target.value)}
                        placeholder="Decision rationale"
                        rows={2}
                        className="min-w-0 resize-y rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
                      />
                      <input
                        value={signoffTargetSections}
                        onChange={(event) => setSignoffTargetSections(event.target.value)}
                        placeholder="Target sections, comma separated"
                        className="min-w-0 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
                      />
                      <textarea
                        value={signoffConditions}
                        onChange={(event) => setSignoffConditions(event.target.value)}
                        placeholder="Acceptance conditions, one per line"
                        rows={2}
                        className="min-w-0 resize-y rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
                      />
                      <div className="flex flex-wrap gap-2">
                        <Button
                          size="sm"
                          onClick={() => void decideSignoff('approved')}
                          disabled={signoffLoading || feedbackApplying || !reviewerId.trim() || !signoffRationale.trim()}
                        >
                          {signoffLoading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <CheckCircle2 className="mr-2 h-4 w-4" />}
                          Approve
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => void decideSignoff('changes_requested')}
                          disabled={signoffLoading || feedbackApplying || !reviewerId.trim() || !signoffRationale.trim()}
                        >
                          <RotateCcw className="mr-2 h-4 w-4" />
                          Request changes
                        </Button>
                        <Button
                          size="sm"
                          variant="destructive"
                          onClick={() => void decideSignoff('rejected')}
                          disabled={signoffLoading || feedbackApplying || !reviewerId.trim() || !signoffRationale.trim()}
                        >
                          <AlertTriangle className="mr-2 h-4 w-4" />
                          Reject
                        </Button>
                      </div>
                    </div>
                  </div>
                </section>

                {(result.planFeedback.packageId || result.runKind === 'faros') && (
                  <div className="flex flex-wrap items-center gap-2 border-t border-slate-200 pt-4">
                    {result.runKind === 'platform' && result.iterationDecision.decision !== 'accept_results' && (
                      <Button
                        variant="outline"
                        onClick={() => void revisePlan()}
                        disabled={
                          !result.planFeedback.applied
                          || planRevised
                          || Boolean(actionLoading)
                          || Boolean(result.humanFeedback?.requiresApplication && !result.humanFeedback.applied)
                        }
                      >
                        {actionLoading === 'revise' ? (
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        ) : (
                          <RotateCcw className="mr-2 h-4 w-4" />
                        )}
                        {planRevised ? 'Plan revised' : 'Revise Plan'}
                      </Button>
                    )}
                    {result.runKind === 'faros' && nextRunId ? (
                      <Button onClick={() => void startNextFarosRun()} disabled={Boolean(actionLoading)}>
                        {actionLoading === 'start' ? (
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        ) : (
                          <PlayCircle className="mr-2 h-4 w-4" />
                        )}
                        Start Next Run
                      </Button>
                    ) : result.runKind === 'faros' ? (
                      <Button
                        onClick={() => void advanceControlledLoop()}
                        disabled={Boolean(actionLoading) || !primaryMetric || !iterationHumanReady}
                      >
                        {actionLoading === 'next' ? (
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        ) : (
                          <RotateCcw className="mr-2 h-4 w-4" />
                        )}
                        Advance Controlled Loop
                      </Button>
                    ) : (
                      <Button
                        onClick={() => void createNextRun()}
                        disabled={
                          Boolean(actionLoading)
                          || (
                            result.runKind === 'platform'
                            && result.iterationDecision.decision === 'revise_plan'
                            && !planRevised
                          )
                          || !iterationHumanReady
                        }
                      >
                        {actionLoading === 'next' ? (
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        ) : (
                          <PlayCircle className="mr-2 h-4 w-4" />
                        )}
                        {nextRunId ? 'Open Next Run' : 'Create Next Run'}
                      </Button>
                    )}
                    {nextRunId && (
                      result.runKind === 'faros' ? (
                        <Button asChild variant="ghost">
                          <a
                            href={`${API_BASE_URL}/api/faros/runs/${encodeURIComponent(nextRunId)}/detail`}
                            target="_blank"
                            rel="noreferrer"
                          >
                            Inspect {nextRunId}
                          </a>
                        </Button>
                      ) : (
                        <Button asChild variant="ghost">
                          <Link to={`/runs/${nextRunId}`}>View {nextRunId}</Link>
                        </Button>
                      )
                    )}
                    {actionMessage && <div className="w-full text-xs text-slate-600">{actionMessage}</div>}
                    {!iterationHumanReady && (
                      <div className="w-full text-xs font-medium text-amber-700">
                        Plan approval{repairSignoff?.required ? ' and repair approval are' : ' is'} required before the next iteration.
                      </div>
                    )}
                    {result.runKind === 'platform' && !result.planFeedback.applied && result.iterationDecision.decision !== 'accept_results' && (
                      <div className="w-full text-xs text-amber-700">
                        Run the audit with Write correction enabled before revising the plan.
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
