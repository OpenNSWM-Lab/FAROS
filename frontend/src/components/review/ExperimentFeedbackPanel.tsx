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
import { Button, buttonVariants } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { API_BASE_URL } from '@/lib/api'
import { useReviewLocale, type ReviewLocale } from '@/lib/reviewLocale'
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

const requiredArtifacts = [
  { filename: 'research_dossier.json', label: 'Research dossier', required: true },
  { filename: 'execution_assessment.json', label: 'Execution assessment', required: false },
  { filename: 'experiment_evidence.json', label: 'Experiment evidence', required: true },
]

const artifactChineseLabels: Record<string, string> = {
  'research_dossier.json': '研究档案',
  'execution_assessment.json': '执行评估',
  'experiment_evidence.json': '实验证据',
}

const decisionLabel: Record<ExperimentFeedbackResponse['iterationDecision']['decision'], Record<ReviewLocale, string>> = {
  accept_results: { 'zh-CN': '接受结果', 'en-US': 'Accept results' },
  revise_plan: { 'zh-CN': '修订计划', 'en-US': 'Revise plan' },
  rerun_experiment: { 'zh-CN': '重跑实验', 'en-US': 'Rerun experiment' },
  needs_human: { 'zh-CN': '需人工决策', 'en-US': 'Human decision' },
}

const signoffStageLabel: Record<HumanSignoffStage, Record<ReviewLocale, string>> = {
  plan: { 'zh-CN': '方案签核', 'en-US': 'Plan approval' },
  repair: { 'zh-CN': '修复签核', 'en-US': 'Repair approval' },
  conclusion: { 'zh-CN': '结论发布', 'en-US': 'Conclusion release' },
}

const signoffStatusLabel: Record<HumanSignoffStatus, Record<ReviewLocale, string>> = {
  pending: { 'zh-CN': '待签核', 'en-US': 'Pending' },
  approved: { 'zh-CN': '已批准', 'en-US': 'Approved' },
  rejected: { 'zh-CN': '已拒绝', 'en-US': 'Rejected' },
  changes_requested: { 'zh-CN': '要求修改', 'en-US': 'Changes requested' },
}

const uiStatusLabels: Record<string, Record<ReviewLocale, string>> = {
  queued: { 'zh-CN': '排队中', 'en-US': 'queued' },
  running: { 'zh-CN': '运行中', 'en-US': 'running' },
  completed: { 'zh-CN': '已完成', 'en-US': 'completed' },
  failed: { 'zh-CN': '失败', 'en-US': 'failed' },
  pass: { 'zh-CN': '通过', 'en-US': 'pass' },
  passed: { 'zh-CN': '通过', 'en-US': 'passed' },
  fail: { 'zh-CN': '不通过', 'en-US': 'fail' },
  pending: { 'zh-CN': '待签核', 'en-US': 'pending' },
  continue: { 'zh-CN': '继续', 'en-US': 'continue' },
  blocked: { 'zh-CN': '已阻断', 'en-US': 'blocked' },
  in_progress: { 'zh-CN': '处理中', 'en-US': 'in progress' },
  resolved: { 'zh-CN': '已解决', 'en-US': 'resolved' },
  verified: { 'zh-CN': '已验证', 'en-US': 'verified' },
  rejected: { 'zh-CN': '已拒绝', 'en-US': 'rejected' },
  changes_requested: { 'zh-CN': '要求修改', 'en-US': 'changes requested' },
  waived: { 'zh-CN': '已豁免', 'en-US': 'waived' },
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

export function ExperimentFeedbackPanel({ initialFeedbackId }: { initialFeedbackId?: string }) {
  const { locale, text } = useReviewLocale()
  const statusText = (status?: string) => status
    ? uiStatusLabels[status]?.[locale] || status
    : '--'
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
  }, [loadRuns])

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

  useEffect(() => {
    if (!initialFeedbackId) return
    let cancelled = false
    const loadRequestedFeedback = async () => {
      setAuditing(true)
      setError('')
      try {
        const response = await fetch(
          `${API_BASE_URL}/api/v1/reviews/reviewx/experiment-feedback/${encodeURIComponent(initialFeedbackId)}`,
        )
        const data = await response.json().catch(() => ({}))
        if (!response.ok) throw new Error(formatError(data.detail))
        if (cancelled) return
        const feedback = data as ExperimentFeedbackResponse
        const controls = restoreLoopControls(feedback)
        setResult(feedback)
        setSelectedRunId(feedback.runId)
        setPrimaryMetric(controls.primaryMetric)
        setMetricDirection(controls.direction)
        setMaxIterations(controls.maxIterations)
        setLoopPatience(controls.patience)
        setMetricGuardrails(controls.guardrails)
        setLoopProgress(controls.progress)
        setPlanRevised(false)
        setNextRunId('')
        setActionMessage(text('已打开指定证据记录，可直接进行人工签核。', 'Requested evidence record loaded for human signoff.'))
        void loadHistory(feedback.runId, feedback.researchSeriesId)
        window.setTimeout(() => {
          document.getElementById('reviewx-human-oversight')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
        }, 80)
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error
            ? loadError.message
            : text('指定的审核记录加载失败。', 'Failed to load the requested review record.'))
        }
      } finally {
        if (!cancelled) setAuditing(false)
      }
    }
    void loadRequestedFeedback()
    return () => {
      cancelled = true
    }
  }, [initialFeedbackId, text])

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
  const signoffFormReady = Boolean(reviewerId.trim() && signoffRationale.trim())

  return (
    <Card className="mb-6 border-emerald-200 shadow-md">
      <CardHeader className="border-b bg-emerald-50/60 pb-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-md bg-emerald-700 text-white">
              <FlaskConical className="h-5 w-5" />
            </div>
            <div>
              <CardTitle className="text-lg">{text('实验反馈 Gate', 'Experiment Feedback Gate')}</CardTitle>
              <div className="mt-1 text-xs text-slate-600">{text('方向 B · 证据驱动迭代', 'Direction B · evidence-driven iteration')}</div>
            </div>
          </div>
          {result && (
            <div className="flex items-center gap-2">
              <Badge variant={gateTone}>Gate: {statusText(gate)}</Badge>
              <Badge variant="outline">{decisionLabel[result.iterationDecision.decision][locale]}</Badge>
            </div>
          )}
        </div>
      </CardHeader>
      <CardContent className="pt-5">
        <section className="mb-5 flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 pb-5">
          <div className="flex items-start gap-3">
            <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-emerald-700" />
            <div>
              <div className="text-sm font-semibold text-slate-900">
                {text('比赛 benchmark 与复验证据', 'Competition benchmarks and replication evidence')}
              </div>
              <p className="mt-1 text-xs leading-5 text-slate-600">
                {text('集中在 Track 1B 展示；本页只处理所选 Run 的审核、反馈和人工签核。', 'Centralized in Track 1B. This page only audits the selected run and records feedback and human sign-off.')}
              </p>
            </div>
          </div>
          <Link
            to="/review/competition"
            className={`${buttonVariants({ variant: 'outline', size: 'sm' })} whitespace-nowrap`}
          >
            {text('查看 Track 1B 证据', 'Open Track 1B evidence')}
            <ArrowRight className="ml-2 h-4 w-4" />
          </Link>
        </section>
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
                <option value="">{text('选择已完成运行...', 'Select completed run...')}</option>
                {runs.map((run) => (
                  <option key={run.id} value={run.id}>
                    {run.config?.workplaceName || run.id} · {run.config?.model || text('未知模型', 'unknown model')} · {run.runKind}
                  </option>
                ))}
              </select>
              <Button variant="outline" size="icon" onClick={() => void loadRuns()} disabled={runsLoading} title={text('刷新运行记录', 'Refresh runs')}>
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
                      <div className="truncate text-xs font-medium text-slate-800">{text(artifactChineseLabels[artifact.filename] || artifact.label, artifact.label)}</div>
                      <div className="text-[11px] text-slate-500">{present ? text('就绪', 'Ready') : artifact.required ? text('必需', 'Required') : text('可选', 'Optional')}</div>
                    </div>
                  </div>
                )
              })}
            </div>

            {selectedRun?.runKind === 'faros' ? (
              <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-900">
                {text('ReviewX 反馈将自动带入下一轮 FAROS 迭代。', 'ReviewX feedback will be carried into the next FAROS iteration automatically.')}
              </div>
            ) : (
              <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] xl:grid-cols-1 2xl:grid-cols-[minmax(0,1fr)_auto]">
                <input
                  value={planPackageId}
                  onChange={(event) => setPlanPackageId(event.target.value)}
                  placeholder={text('PlanPackage ID（可选）', 'PlanPackage ID (optional)')}
                  className="min-w-0 rounded-md border border-slate-300 px-3 py-2 text-sm"
                />
                <label className="flex min-h-10 items-center gap-2 text-sm text-slate-700">
                  <input
                    type="checkbox"
                    checked={applyToPlan}
                    onChange={(event) => setApplyToPlan(event.target.checked)}
                    className="h-4 w-4"
                  />
                  {text('写入修正', 'Write correction')}
                </label>
              </div>
            )}
            <Button className="w-full" onClick={() => void runAudit()} disabled={!selectedRunId || auditing}>
              {auditing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <FlaskConical className="mr-2 h-4 w-4" />}
              {text('审计本轮实验', 'Audit Experiment Iteration')}
            </Button>
            {selectedRunId && !requiredReady && !error && (
              <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                {text('此运行尚未注册必需的合同 artifact。', 'Required contract artifacts are not registered for this run.')}
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
                    {text('实验历史', 'Experiment history')}
                  </div>
                  <Badge variant="outline">{history.length}</Badge>
                </div>
                {historyLoading ? (
                  <div className="flex items-center gap-2 py-3 text-xs text-slate-500">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" /> {text('正在加载历史', 'Loading history')}
                  </div>
                ) : history.length === 0 ? (
                  <div className="py-2 text-xs text-slate-500">{text('暂无实验反馈。', 'No experiment feedback yet.')}</div>
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
                            V{record.iterationNumber || 1} · {decisionLabel[record.iterationDecision.decision][locale]}
                          </div>
                          <div className="mt-0.5 text-[11px] text-slate-500">
                            {new Date(record.createdAt).toLocaleString(locale)}
                          </div>
                        </div>
                        <div className="flex shrink-0 gap-1">
                          {record.planRevision && <Badge variant="secondary">{text('已修订', 'Revised')}</Badge>}
                          {record.nextRunId && <Badge variant="outline">{text('下一轮', 'Next run')}</Badge>}
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
                {selectedRun
                  ? text('运行证据 Gate 以决定下一轮路由。', 'Run the evidence Gate to route the next iteration.')
                  : text('请选择一个已完成的实验运行。', 'Select a completed experiment run.')}
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
                    <div className="text-sm font-semibold text-slate-900">{decisionLabel[result.iterationDecision.decision][locale]}</div>
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
                          <th className="px-3 py-2 text-right">{text('上一轮', 'Previous')}</th>
                          <th className="px-3 py-2 text-right">{text('当前', 'Current')}</th>
                          <th className="px-3 py-2 text-right">Δ</th>
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
                          {direction === 'maximize' ? text('最大化', 'Maximize') : text('最小化', 'Minimize')}
                        </button>
                      ))}
                    </div>
                    <label className="min-w-0 text-[11px] font-medium uppercase text-slate-500">
                      {text('最大轮数', 'Max rounds')}
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
                      {text('耐心轮数', 'Patience')}
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
                      <div className="text-[11px] uppercase text-slate-500">{text('序列', 'Series')}</div>
                      <div className="mt-1 text-sm font-semibold text-slate-900">{statusText(loopProgress.status)}</div>
                    </div>
                    <div>
                      <div className="text-[11px] uppercase text-slate-500">{text('迭代', 'Iteration')}</div>
                      <div className="mt-1 text-sm font-semibold text-slate-900">V{loopProgress.currentIteration}</div>
                    </div>
                    <div>
                      <div className="text-[11px] uppercase text-slate-500">{text('最优可行值', 'Feasible best')}</div>
                      <div className="mt-1 text-sm font-semibold text-slate-900">
                        {loopProgress.bestFeasibleValue === undefined
                          ? 'N/A'
                          : `${loopProgress.bestFeasibleValue.toFixed(4)} · V${loopProgress.bestFeasibleIteration}`}
                      </div>
                    </div>
                    <div>
                      <div className="text-[11px] uppercase text-slate-500">{text('停止条件', 'Stop condition')}</div>
                      <div className="mt-1 break-words text-xs font-medium text-slate-700">
                        {loopProgress.stopReason.replace(/_/g, ' ')}
                      </div>
                    </div>
                    <div>
                      <div className="text-[11px] uppercase text-slate-500">Guardrails</div>
                      <div className={`mt-1 text-xs font-semibold ${loopProgress.guardrailsSatisfied ? 'text-emerald-700' : 'text-amber-700'}`}>
                        {loopProgress.guardrailsSatisfied
                          ? text('全部满足', 'All satisfied')
                          : loopProgress.guardrailViolations.map((item) => item.metric.replace(/^method:/i, '')).join(', ')}
                      </div>
                    </div>
                  </div>
                )}

                <div className="grid gap-4 md:grid-cols-2">
                  <div>
                    <div className="mb-2 text-xs font-semibold uppercase text-slate-600">{text('下一步行动', 'Next actions')}</div>
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
                    <div className="mb-2 text-xs font-semibold uppercase text-slate-600">{text('证据与反馈', 'Evidence & feedback')}</div>
                    <div className="space-y-2 text-sm text-slate-700">
                      <div>{Object.keys(result.sourceArtifacts).length} {text('个合同 artifact 已验证', 'contract artifacts verified')}</div>
                      <div>{result.qualityAssessment.findings.length} {text('个质量 finding', 'quality findings')}</div>
                      <div className={result.planFeedback.applied ? 'text-emerald-700' : 'text-slate-500'}>
                        {result.planFeedback.applied
                          ? text('PlanPackage 修正已附加', 'PlanPackage correction attached')
                          : result.planFeedback.reason || text('未请求写入 PlanPackage', 'No PlanPackage write requested')}
                      </div>
                      {result.humanFeedback?.requiresApplication && (
                        <div className={result.humanFeedback.applied ? 'text-emerald-700' : 'font-medium text-amber-700'}>
                          {text('人工反馈', 'Human feedback')} {result.humanFeedback.applied ? text('已应用', 'applied') : text('待应用', 'awaiting application')}
                        </div>
                      )}
                    </div>
                  </div>
                </div>

                <section id="reviewx-human-oversight" className="scroll-mt-24 border-t border-slate-200 pt-4" aria-label={text('人工审核', 'Human oversight')}>
                  <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2 text-xs font-semibold uppercase text-slate-600">
                      <ShieldCheck className="h-4 w-4" />
                      {text('人工审核', 'Human oversight')}
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <a
                        href={`${API_BASE_URL}/api/v1/reviews/reviewx/experiment-feedback/${encodeURIComponent(result.feedbackId)}/evidence-bundle?release=draft`}
                        target="_blank"
                        rel="noreferrer"
                        className={buttonVariants({ variant: 'outline', size: 'sm' })}
                      >
                        <FileCheck2 className="mr-2 h-4 w-4" />
                        {text('草稿证据包', 'Draft bundle')}
                      </a>
                      {publicationReady ? (
                        <a
                          href={`${API_BASE_URL}/api/v1/reviews/reviewx/experiment-feedback/${encodeURIComponent(result.feedbackId)}/evidence-bundle?release=official`}
                          target="_blank"
                          rel="noreferrer"
                          className={buttonVariants({ size: 'sm' })}
                        >
                          <FileCheck2 className="mr-2 h-4 w-4" />
                          {text('正式证据包', 'Official bundle')}
                        </a>
                      ) : (
                        <Button size="sm" disabled title={text('需方案、必要修复和结论签核全部通过', 'Plan, required repair, and conclusion signoffs must all pass')}>
                          <FileCheck2 className="mr-2 h-4 w-4" />
                          {text('正式证据包', 'Official bundle')}
                        </Button>
                      )}
                    </div>
                  </div>

                  {!publicationReady && (
                    <div className="mb-3 text-xs text-amber-700">
                      {text('正式证据包尚被阻断：需方案、必要修复和结论签核全部通过，且所有验收条件已解决。', 'Official bundle blocked: plan, required repair, and conclusion signoffs must pass, and all acceptance conditions must be resolved.')}
                    </div>
                  )}

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
                              <span className="truncate text-xs font-semibold text-slate-900">{signoffStageLabel[stage][locale]}</span>
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
                                {signoffStatusLabel[signoff.status][locale]}
                              </Badge>
                              <Badge variant="outline">{signoff.required ? text('必需', 'Required') : text('可选', 'Optional')}</Badge>
                              {signoff.stale && <Badge variant="destructive">{text('已过期', 'Stale')}</Badge>}
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
                            {result.humanFeedback.requiredActions.length} {text('项人工行动', `human action${result.humanFeedback.requiredActions.length === 1 ? '' : 's'}`)}
                            {' · '}{result.humanFeedback.applied ? text('已应用', 'Applied') : text('需要应用', 'Application required')}
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
                            {text('应用人工反馈', 'Apply human feedback')}
                          </Button>
                        )}
                      </div>
                    )}

                    {result.humanConditionVerifications?.required && (
                      <div className="grid gap-3 rounded-md border border-slate-200 bg-white p-3">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div>
                            <div className="text-sm font-semibold text-slate-900">{text('继承的验收条件', 'Inherited acceptance conditions')}</div>
                            <div className="mt-1 text-xs text-slate-600">
                              {result.humanConditionVerifications.passed} {text('已通过', 'passed')}
                              {' · '}{result.humanConditionVerifications.waived} {text('已豁免', 'waived')}
                              {' · '}{result.humanConditionVerifications.unresolved} {text('未解决', 'unresolved')}
                            </div>
                          </div>
                          <Badge variant={result.humanConditionVerifications.allResolved ? 'secondary' : 'outline'}>
                            {result.humanConditionVerifications.allResolved ? text('已解决', 'Resolved') : text('阻断结论', 'Blocks conclusion')}
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
                                  {condition.stale
                                    ? `${statusText(condition.storedStatus)} · ${text('已过期', 'stale')}`
                                    : statusText(condition.status)}
                                </span>
                              </div>
                              <div className="mt-1 truncate font-mono text-[10px] text-slate-500" title={condition.subjectHash}>
                                {text('证据状态', 'Evidence state')} {condition.subjectHash.slice(0, 19)}
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
                              aria-label={text('验收条件的证据 artifact', 'Condition evidence artifact')}
                            >
                              <option value="">{text('选择当前证据 artifact', 'Select current evidence artifact')}</option>
                              {Object.entries(result.sourceArtifacts).map(([label, artifactId]) => (
                                <option key={`${label}-${artifactId}`} value={artifactId}>{label}</option>
                              ))}
                            </select>
                            <textarea
                              value={conditionRationale}
                              onChange={(event) => setConditionRationale(event.target.value)}
                              placeholder={text('验证理由', 'Verification rationale')}
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
                                {text('凭证据通过', 'Pass with evidence')}
                              </Button>
                              <Button
                                size="sm"
                                variant="destructive"
                                onClick={() => void verifyHumanCondition('failed')}
                                disabled={conditionLoading || !reviewerId.trim() || !conditionRationale.trim()}
                              >
                                <AlertTriangle className="mr-2 h-4 w-4" />
                                {text('不通过', 'Fail')}
                              </Button>
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => void verifyHumanCondition('waived')}
                                disabled={conditionLoading || !reviewerId.trim() || !conditionRationale.trim()}
                              >
                                {text('豁免', 'Waive')}
                              </Button>
                            </div>
                          </div>
                        )}
                      </div>
                    )}

                    <div className="grid gap-2 rounded-md border border-slate-200 bg-slate-50 p-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="text-sm font-semibold text-slate-900">
                          {signoffStageLabel[selectedSignoffStage][locale]}
                        </div>
                        {result.humanSignoffs[selectedSignoffStage].stale && (
                          <span className="text-xs font-medium text-red-700">{text('证据已变化，请重新审核', 'Evidence changed; review again')}</span>
                        )}
                      </div>
                      <div className="grid gap-2 sm:grid-cols-2">
                        <select
                          value={reviewerRole}
                          onChange={(event) => setReviewerRole(event.target.value)}
                          className="min-w-0 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
                          aria-label={text('审核人角色', 'Reviewer role')}
                        >
                          <option value="team_lead">{text('团队负责人', 'Team lead')}</option>
                          <option value="domain_expert">{text('领域专家', 'Domain expert')}</option>
                          <option value="safety_reviewer">{text('安全审核人', 'Safety reviewer')}</option>
                        </select>
                        <input
                          value={reviewerId}
                          onChange={(event) => setReviewerId(event.target.value)}
                          placeholder={text('审核人身份', 'Reviewer identity')}
                          className="min-w-0 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
                        />
                        <input
                          type="password"
                          value={reviewAuthToken}
                          onChange={(event) => setReviewAuthToken(event.target.value)}
                          placeholder={text('Review bearer token（如服务端要求）', 'Review bearer token (if required)')}
                          autoComplete="off"
                          className="min-w-0 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm sm:col-span-2"
                        />
                      </div>
                      <textarea
                        value={signoffRationale}
                        onChange={(event) => setSignoffRationale(event.target.value)}
                        placeholder={text('决策理由', 'Decision rationale')}
                        rows={2}
                        className="min-w-0 resize-y rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
                      />
                      <input
                        value={signoffTargetSections}
                        onChange={(event) => setSignoffTargetSections(event.target.value)}
                        placeholder={text('目标章节，用逗号分隔', 'Target sections, comma separated')}
                        className="min-w-0 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
                      />
                      <textarea
                        value={signoffConditions}
                        onChange={(event) => setSignoffConditions(event.target.value)}
                        placeholder={text('验收条件，每行一条', 'Acceptance conditions, one per line')}
                        rows={2}
                        className="min-w-0 resize-y rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
                      />
                      <div className="flex flex-wrap gap-2">
                        <Button
                          size="sm"
                          onClick={() => void decideSignoff('approved')}
                          disabled={signoffLoading || feedbackApplying || !signoffFormReady}
                        >
                          {signoffLoading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <CheckCircle2 className="mr-2 h-4 w-4" />}
                          {text('批准', 'Approve')}
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => void decideSignoff('changes_requested')}
                          disabled={signoffLoading || feedbackApplying || !signoffFormReady}
                        >
                          <RotateCcw className="mr-2 h-4 w-4" />
                          {text('要求修改', 'Request changes')}
                        </Button>
                        <Button
                          size="sm"
                          variant="destructive"
                          onClick={() => void decideSignoff('rejected')}
                          disabled={signoffLoading || feedbackApplying || !signoffFormReady}
                        >
                          <AlertTriangle className="mr-2 h-4 w-4" />
                          {text('拒绝', 'Reject')}
                        </Button>
                      </div>
                      {!signoffFormReady && (
                        <div className="text-xs text-amber-700">
                          {text('填写审核人身份和决策理由后方可提交。', 'Enter reviewer identity and decision rationale to enable signoff.')}
                        </div>
                      )}
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
                        {planRevised ? text('计划已修订', 'Plan revised') : text('修订计划', 'Revise Plan')}
                      </Button>
                    )}
                    {result.runKind === 'faros' && nextRunId ? (
                      <Button onClick={() => void startNextFarosRun()} disabled={Boolean(actionLoading)}>
                        {actionLoading === 'start' ? (
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        ) : (
                          <PlayCircle className="mr-2 h-4 w-4" />
                        )}
                        {text('启动下一轮', 'Start Next Run')}
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
                        {text('推进受控闭环', 'Advance Controlled Loop')}
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
                        {nextRunId ? text('打开下一轮', 'Open Next Run') : text('创建下一轮', 'Create Next Run')}
                      </Button>
                    )}
                    {nextRunId && (
                      result.runKind === 'faros' ? (
                        <a
                          href={`${API_BASE_URL}/api/faros/runs/${encodeURIComponent(nextRunId)}/detail`}
                          target="_blank"
                          rel="noreferrer"
                          className={buttonVariants({ variant: 'ghost' })}
                        >
                          {text('检查', 'Inspect')} {nextRunId}
                        </a>
                      ) : (
                        <Link to={`/runs/${nextRunId}`} className={buttonVariants({ variant: 'ghost' })}>
                          {text('查看', 'View')} {nextRunId}
                        </Link>
                      )
                    )}
                    {actionMessage && <div className="w-full text-xs text-slate-600">{actionMessage}</div>}
                    {!iterationHumanReady && (
                      <div className="w-full text-xs font-medium text-amber-700">
                        {repairSignoff?.required
                          ? text('下一轮前需完成方案和修复签核。', 'Plan and repair approval are required before the next iteration.')
                          : text('下一轮前需完成方案签核。', 'Plan approval is required before the next iteration.')}
                      </div>
                    )}
                    {result.runKind === 'platform' && !result.planFeedback.applied && result.iterationDecision.decision !== 'accept_results' && (
                      <div className="w-full text-xs text-amber-700">
                        {text('修订计划前，请启用“写入修正”后重新运行审计。', 'Run the audit with Write correction enabled before revising the plan.')}
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
