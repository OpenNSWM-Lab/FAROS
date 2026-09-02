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
import { useSystemSession } from '@/lib/hooks/useApi'
import { useReviewLocale, type ReviewLocale } from '@/lib/reviewLocale'
import type { QualityAssessment } from '@/lib/types/scientificResearch'
import { ReviewIterationLoop, type ReviewLoopTrace } from './ReviewIterationLoop'
import { SignoffDossier } from './SignoffDossier'

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
  reviewerName?: string | null
  actorAccountId?: string | null
  actorRole?: string | null
  authAssurance?: string | null
  acknowledgements?: string[]
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
  sourceArtifactUrls?: Record<string, string>
  closedLoop?: ReviewLoopTrace
  publicationReady: boolean
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

const closedLoopArtifacts = [
  { filename: 'preregistration.json', label: 'Preregistration', required: true },
  { filename: 'execution_timing.json', label: 'Execution evidence', required: true },
  { filename: 'experiment_series.json', label: 'Iteration series', required: true },
]

const artifactChineseLabels: Record<string, string> = {
  'research_dossier.json': '研究档案',
  'execution_assessment.json': '执行评估',
  'experiment_evidence.json': '实验证据',
  'preregistration.json': '预注册方案',
  'execution_timing.json': '执行证据',
  'experiment_series.json': '迭代序列',
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

const signoffAcknowledgements: Record<HumanSignoffStage, Array<{
  id: string
  label: Record<ReviewLocale, string>
}>> = {
  plan: [
    { id: 'reviewed_scientific_question_and_hypothesis', label: { 'zh-CN': '已核对研究问题与假设', 'en-US': 'Reviewed the scientific question and hypothesis' } },
    { id: 'reviewed_data_split_and_holdout', label: { 'zh-CN': '已核对数据划分及最终留出隔离', 'en-US': 'Reviewed data splits and final-holdout isolation' } },
    { id: 'reviewed_metrics_budget_and_stop_conditions', label: { 'zh-CN': '已核对主指标、guardrail、预算与停止条件', 'en-US': 'Reviewed primary metric, guardrails, budget, and stop conditions' } },
  ],
  repair: [
    { id: 'reviewed_reviewx_findings', label: { 'zh-CN': '已核对 ReviewX finding', 'en-US': 'Reviewed ReviewX findings' } },
    { id: 'confirmed_repairs_applied', label: { 'zh-CN': '已确认修复实际应用到目标节点', 'en-US': 'Confirmed repairs were applied to target nodes' } },
    { id: 'reviewed_rerun_scope_and_residual_risk', label: { 'zh-CN': '已核对重跑范围和剩余风险', 'en-US': 'Reviewed rerun scope and residual risk' } },
  ],
  conclusion: [
    { id: 'reviewed_baseline_current_and_interval', label: { 'zh-CN': '已核对基线、当前值和统计区间', 'en-US': 'Reviewed baseline, current value, and interval' } },
    { id: 'reviewed_side_effects_and_limitations', label: { 'zh-CN': '已核对副作用与限制', 'en-US': 'Reviewed side effects and limitations' } },
    { id: 'accepted_claim_scope', label: { 'zh-CN': '同意只在档案定义的 claim scope 内发布', 'en-US': 'Accepted publication only within the dossier claim scope' } },
  ],
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

export function ExperimentFeedbackPanel({
  initialFeedbackId,
  initialFocus = 'loop',
}: {
  initialFeedbackId?: string
  initialFocus?: 'loop' | 'signoff'
}) {
  const { locale, text } = useReviewLocale()
  const session = useSystemSession()
  const statusText = (status?: string) => status
    ? uiStatusLabels[status]?.[locale] || status
    : '--'
  const messageText = (message?: string) => {
    if (!message || locale === 'en-US') return message || ''
    const knownMessages: Array<[string, string]> = [
      [
        'The experiment is reproducible, aligned with the plan, and ready for scientific interpretation.',
        '本轮实验可复现、与计划一致，可以进入科学解释与结论审核。',
      ],
      [
        'Record the result and decide whether the research stop conditions have been met.',
        '记录本轮结果，并依据停止条件决定结束或继续迭代。',
      ],
      [
        'Competition case uses its preregistered round-two plan; no PlanPackage write is required.',
        '该代表案例使用已预注册的第二轮计划，无需再次写入 PlanPackage。',
      ],
    ]
    return knownMessages.find(([source]) => source === message)?.[1] || message
  }
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
  const [reviewerId, setReviewerId] = useState(() => localStorage.getItem('faros-reviewer-id') || '')
  const [signoffRationale, setSignoffRationale] = useState('')
  const [signoffConditions, setSignoffConditions] = useState('')
  const [signoffTargetSections, setSignoffTargetSections] = useState('')
  const [signoffLoading, setSignoffLoading] = useState(false)
  const [dossierRefreshKey, setDossierRefreshKey] = useState(0)
  const [acknowledgements, setAcknowledgements] = useState<Record<HumanSignoffStage, string[]>>({
    plan: [],
    repair: [],
    conclusion: [],
  })
  const [feedbackApplying, setFeedbackApplying] = useState(false)
  const [selectedConditionId, setSelectedConditionId] = useState('')
  const [conditionRationale, setConditionRationale] = useState('')
  const [conditionEvidenceId, setConditionEvidenceId] = useState('')
  const [conditionLoading, setConditionLoading] = useState(false)
  const [reviewAuthToken, setReviewAuthToken] = useState('')

  useEffect(() => {
    const normalized = reviewerId.trim()
    if (normalized) localStorage.setItem('faros-reviewer-id', normalized)
    else localStorage.removeItem('faros-reviewer-id')
  }, [reviewerId])

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
        setRuns((current) => current.some((run) => run.id === feedback.runId) ? current : [
          {
            id: feedback.runId,
            status: 'completed',
            runKind: feedback.runKind,
            createdAt: feedback.createdAt,
            parentRunId: feedback.parentRunId,
            researchSeriesId: feedback.researchSeriesId,
            iterationNumber: feedback.iterationNumber,
            artifacts: Object.entries(feedback.sourceArtifacts || {}).map(([filename, id]) => ({ id, filename })),
            config: {
              workplaceName: text(`ReviewX 受控闭环 · V${feedback.iterationNumber || 1}`, `ReviewX controlled loop · V${feedback.iterationNumber || 1}`),
              model: 'ReviewX',
            },
          },
          ...current,
        ])
        setPrimaryMetric(controls.primaryMetric)
        setMetricDirection(controls.direction)
        setMaxIterations(controls.maxIterations)
        setLoopPatience(controls.patience)
        setMetricGuardrails(controls.guardrails)
        setLoopProgress(controls.progress)
        setPlanRevised(false)
        setNextRunId('')
        setActionMessage(text('已打开指定证据记录。', 'Requested evidence record loaded.'))
        void loadHistory(feedback.runId, feedback.researchSeriesId)
        window.setTimeout(() => {
          const targetId = initialFocus === 'signoff' ? 'reviewx-human-oversight' : 'reviewx-closed-loop'
          document.getElementById(targetId)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
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
  }, [initialFeedbackId, initialFocus, text])

  const selectedRun = useMemo(
    () => runs.find((run) => run.id === selectedRunId),
    [runs, selectedRunId],
  )
  const availableFilenames = useMemo(
    () => new Set([
      ...(selectedRun?.artifacts || []).map((artifact) => artifactBasename(artifact.filename)),
      ...(selectedRunId === result?.runId ? Object.keys(result.sourceArtifacts || {}).map(artifactBasename) : []),
    ]),
    [result, selectedRun, selectedRunId],
  )
  const isClosedLoopEvidenceRecord = Boolean(
    selectedRunId === result?.runId && result?.sourceArtifactUrls?.['plan_delta_contract.json'],
  )
  const displayedArtifacts = isClosedLoopEvidenceRecord ? closedLoopArtifacts : requiredArtifacts
  const requiredReady = displayedArtifacts
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

  const runAudit = async (requestedRunId = selectedRunId) => {
    if (!requestedRunId) return
    const requestedRun = runs.find((run) => run.id === requestedRunId)
    setSelectedRunId(requestedRunId)
    setAuditing(true)
    setError('')
    setResult(null)
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/v1/reviews/reviewx/runs/${requestedRunId}/experiment-feedback`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(reviewAuthToken ? { Authorization: `Bearer ${reviewAuthToken}` } : {}),
          },
          body: JSON.stringify({
            planPackageId: requestedRun?.runKind === 'platform' ? planPackageId || undefined : undefined,
            applyToPlanPackage: requestedRun?.runKind === 'platform' ? applyToPlan : false,
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
      void loadHistory(requestedRunId, data.researchSeriesId)
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
      sourceArtifactUrls: record.sourceArtifactUrls,
      closedLoop: record.closedLoop,
      publicationReady: record.publicationReady,
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
          publicationReady: signoffData.publicationReady,
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
            reviewerName: reviewerId.trim(),
            acknowledgements: acknowledgements[selectedSignoffStage],
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
        publicationReady: data.publicationReady,
      } : current)
      setSignoffRationale('')
      setSignoffConditions('')
      setSignoffTargetSections('')
      setAcknowledgements((current) => ({ ...current, [selectedSignoffStage]: [] }))
      setDossierRefreshKey((current) => current + 1)
      setActionMessage(`${selectedSignoffStage} signoff: ${status.replace('_', ' ')}`)
      void loadHistory(result.runId, result.researchSeriesId)
    } catch (signoffError) {
      setActionMessage(signoffError instanceof Error ? signoffError.message : 'Human signoff failed.')
    } finally {
      setSignoffLoading(false)
    }
  }

  const approveRequiredSignoffs = async () => {
    if (!result?.feedbackId || !reviewerId.trim() || !signoffRationale.trim()) return
    setSignoffLoading(true)
    setActionMessage('')
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/v1/reviews/reviewx/experiment-feedback/${result.feedbackId}/signoffs`,
        {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
            ...(reviewAuthToken ? { Authorization: `Bearer ${reviewAuthToken}` } : {}),
          },
          body: JSON.stringify({
            reviewerRole,
            reviewerId: reviewerId.trim(),
            reviewerName: reviewerId.trim(),
            rationale: signoffRationale.trim(),
            acknowledgementsByStage: acknowledgements,
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
        publicationReady: data.publicationReady,
      } : current)
      setSignoffRationale('')
      setAcknowledgements({ plan: [], repair: [], conclusion: [] })
      setDossierRefreshKey((current) => current + 1)
      setActionMessage(text(
        data.publicationReady ? '负责人签核完成，正式证据包已解锁。' : '负责人已批准当前所有必需阶段。',
        data.publicationReady ? 'Reviewer signoff complete; the official bundle is unlocked.' : 'The reviewer approved every currently required stage.',
      ))
      void loadHistory(result.runId, result.researchSeriesId)
    } catch (signoffError) {
      setActionMessage(signoffError instanceof Error ? signoffError.message : text('负责人签核失败。', 'Reviewer signoff failed.'))
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
        publicationReady: data.publicationReady ?? false,
      } : current)
      if (data.planRevision) setPlanRevised(true)
      setDossierRefreshKey((current) => current + 1)
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
        publicationReady: data.publicationReady,
      } : current)
      setConditionRationale('')
      setConditionEvidenceId('')
      setDossierRefreshKey((current) => current + 1)
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
  const iterationHumanReady = Boolean(
    (!result?.humanFeedback?.requiresApplication || result.humanFeedback.applied)
    && planSignoff?.status === 'approved'
    && (!repairSignoff?.required || repairSignoff.status === 'approved'),
  )
  const publicationReady = Boolean(result?.publicationReady)
  const currentAcknowledgementsComplete = signoffAcknowledgements[selectedSignoffStage]
    .every((item) => acknowledgements[selectedSignoffStage].includes(item.id))
  const signoffFormReady = Boolean(
    reviewerId.trim() && signoffRationale.trim() && currentAcknowledgementsComplete,
  )
  const allRequiredAcknowledgementsComplete = (Object.keys(signoffAcknowledgements) as HumanSignoffStage[])
    .filter((stage) => result?.humanSignoffs[stage]?.required)
    .every((stage) => signoffAcknowledgements[stage]
      .every((item) => acknowledgements[stage].includes(item.id)))
  const isReadOnlyJudge = session.data?.role === 'judge'
  const nextRun = useMemo(
    () => runs.find((run) => run.id === nextRunId),
    [nextRunId, runs],
  )
  const reviewLoopTrace: ReviewLoopTrace | null = result ? result.closedLoop || {
    status: nextRunId ? 'iteration_created' : result.iterationDecision.decision === 'accept_results' ? 'accepted' : 'needs_iteration',
    fromRunId: result.runId,
    toRunId: nextRunId || null,
    researchSeriesId: result.researchSeriesId,
    fromIteration: result.iterationNumber || 1,
    toIteration: nextRunId ? (result.iterationNumber || 1) + 1 : null,
    scientificDecision: result.iterationDecision.decision,
    targetModules: Array.from(new Set(result.qualityAssessment.findings.map((finding) => finding.targetModule).filter(Boolean))) as string[],
    targetSections: result.iterationDecision.targetSections,
    changes: [],
    rounds: [],
    primaryMetric: result.loopProgress?.primaryMetric,
  } : null

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
        {result && reviewLoopTrace && (
          <div className="mb-5">
            <ReviewIterationLoop
              trace={reviewLoopTrace}
              gateStatus={result.qualityAssessment.gateStatus}
              findingCount={result.qualityAssessment.findings.length}
              metricDeltas={result.iterationDecision.metricDeltas}
              sourceArtifactUrls={result.sourceArtifactUrls}
              nextRunId={nextRunId}
              nextRunStatus={nextRun?.status}
              iterationHumanReady={iterationHumanReady}
              actionLoading={actionLoading}
              showActions={result.runKind === 'faros'}
              onCreateIteration={() => {
                if (result.runKind === 'faros') void advanceControlledLoop()
                else void createNextRun()
              }}
              onStartIteration={() => void startNextFarosRun()}
              onAuditIteration={() => nextRunId && void runAudit(nextRunId)}
              onOpenSignoff={() => document.getElementById('reviewx-human-oversight')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
            />
          </div>
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
              {displayedArtifacts.map((artifact) => {
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
                    <div className="mt-1 text-sm leading-5 text-slate-600">{messageText(result.iterationDecision.rationale)}</div>
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
                  <details className="rounded-md border border-slate-200">
                    <summary className="cursor-pointer px-3 py-2 text-sm font-semibold text-slate-800 hover:bg-slate-50">
                      {text('完整指标变化', 'Full metric deltas')} ({result.iterationDecision.metricDeltas.length})
                    </summary>
                    <div className="overflow-x-auto border-t border-slate-200">
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
                  </details>
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
                    <div className="mb-2 text-xs font-semibold uppercase text-slate-600 dark:text-slate-300">{text('下一步行动', 'Next actions')}</div>
                    <div className="space-y-2">
                      {result.iterationDecision.nextActions.slice(0, 4).map((action, index) => (
                        <div key={`${index}-${action}`} className="flex gap-2 text-sm text-slate-700 dark:text-slate-200">
                          <span className="font-semibold text-emerald-700 dark:text-emerald-400">{index + 1}.</span>
                          <span>{messageText(action)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                  <div>
                    <div className="mb-2 text-xs font-semibold uppercase text-slate-600 dark:text-slate-300">{text('证据与反馈', 'Evidence & feedback')}</div>
                    <div className="space-y-2 text-sm text-slate-700 dark:text-slate-200">
                      <div>{Object.keys(result.sourceArtifacts).length} {text('个合同 artifact 已验证', 'contract artifacts verified')}</div>
                      <div>{result.qualityAssessment.findings.length} {text('个质量 finding', 'quality findings')}</div>
                      <div className={result.planFeedback.applied ? 'text-emerald-700 dark:text-emerald-400' : 'text-slate-500 dark:text-slate-400'}>
                        {result.planFeedback.applied
                          ? text('PlanPackage 修正已附加', 'PlanPackage correction attached')
                          : messageText(result.planFeedback.reason) || text('未请求写入 PlanPackage', 'No PlanPackage write requested')}
                      </div>
                      {result.humanFeedback?.requiresApplication && (
                        <div className={result.humanFeedback.applied ? 'text-emerald-700 dark:text-emerald-400' : 'font-medium text-amber-700 dark:text-amber-300'}>
                          {text('人工反馈', 'Human feedback')} {result.humanFeedback.applied ? text('已应用', 'applied') : text('待应用', 'awaiting application')}
                        </div>
                      )}
                    </div>
                  </div>
                </div>

                <section id="reviewx-human-oversight" className="scroll-mt-24 border-t border-slate-200 pt-4 dark:border-slate-700" aria-label={text('人工审核', 'Human oversight')}>
                  <SignoffDossier feedbackId={result.feedbackId} refreshKey={dossierRefreshKey} />
                  <div className="mb-3 mt-5 flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2 text-xs font-semibold uppercase text-slate-600 dark:text-slate-300">
                      <ShieldCheck className="h-4 w-4" />
                      {text('逐阶段责任确认', 'Stage-by-stage responsibility confirmation')}
                    </div>
                    <Badge variant={isReadOnlyJudge ? 'outline' : 'secondary'}>
                      {isReadOnlyJudge ? text('评委账号 · 只读', 'Judge account · read only') : text('受信账号签核', 'Trusted-account signoff')}
                    </Badge>
                  </div>

                  {!publicationReady && (
                    <div className="mb-3 text-xs text-amber-700 dark:text-amber-300">
                      {text('正式档案仍被服务端门禁锁定；请按摘要中的阻断项逐项处理。', 'The official dossier remains server-locked; resolve each blocker listed in the summary.')}
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
                                ? 'border-emerald-700 bg-emerald-50 dark:border-emerald-500 dark:bg-emerald-950/30'
                                : 'border-slate-200 bg-white hover:border-slate-300 dark:border-slate-700 dark:bg-slate-950 dark:hover:border-slate-600'
                            }`}
                          >
                            <div className="flex items-center justify-between gap-2">
                              <span className="truncate text-xs font-semibold text-slate-900 dark:text-slate-100">{signoffStageLabel[stage][locale]}</span>
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
                            <div className="mt-2 truncate font-mono text-[10px] text-slate-500 dark:text-slate-400" title={signoff.artifactHash}>
                              SHA-256 {signoff.artifactHash.slice(0, 12)}
                            </div>
                            {signoff.status === 'approved' && (
                              <div className="mt-1 truncate text-[10px] text-slate-600 dark:text-slate-300" title={`${signoff.actorAccountId || ''} · ${signoff.authAssurance || ''}`}>
                                {signoff.reviewerName || signoff.reviewerId} · {signoff.actorAccountId || text('旧记录未绑定账号', 'legacy unbound account')} · {signoff.authAssurance || 'self_reported'}
                              </div>
                            )}
                          </button>
                        )
                      })}
                    </div>

                    {result.humanFeedback?.requiresApplication && (
                      <div className={`flex flex-wrap items-center justify-between gap-3 rounded-md border px-3 py-3 ${
                        result.humanFeedback.applied
                          ? 'border-emerald-200 bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-950/30'
                          : 'border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/30'
                      }`}>
                        <div className="min-w-0">
                          <div className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                            {result.humanFeedback.requiredActions.length} {text('项人工行动', `human action${result.humanFeedback.requiredActions.length === 1 ? '' : 's'}`)}
                            {' · '}{result.humanFeedback.applied ? text('已应用', 'Applied') : text('需要应用', 'Application required')}
                          </div>
                          <div className="mt-1 truncate font-mono text-[10px] text-slate-600 dark:text-slate-300" title={result.humanFeedback.feedbackHash}>
                            SHA-256 {result.humanFeedback.feedbackHash.slice(0, 24)}
                          </div>
                        </div>
                        {!result.humanFeedback.applied && !isReadOnlyJudge && (
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
                      <div className="grid gap-3 rounded-md border border-slate-200 bg-white p-3 text-slate-900 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div>
                            <div className="text-sm font-semibold text-slate-900 dark:text-slate-100">{text('继承的验收条件', 'Inherited acceptance conditions')}</div>
                            <div className="mt-1 text-xs text-slate-600 dark:text-slate-300">
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
                                  ? 'border-emerald-700 bg-emerald-50 dark:border-emerald-500 dark:bg-emerald-950/30'
                                  : 'border-slate-200 hover:border-slate-300 dark:border-slate-700 dark:hover:border-slate-600'
                              }`}
                            >
                              <div className="flex flex-wrap items-start justify-between gap-2">
                                <span className="min-w-0 flex-1 text-sm text-slate-800 dark:text-slate-200">{condition.condition}</span>
                                <span className={`text-xs font-semibold ${
                                  condition.status === 'passed' || condition.status === 'waived'
                                      ? 'text-emerald-700 dark:text-emerald-400'
                                    : condition.status === 'failed'
                                      ? 'text-red-700 dark:text-red-400'
                                      : 'text-amber-700 dark:text-amber-300'
                                }`}>
                                  {condition.stale
                                    ? `${statusText(condition.storedStatus)} · ${text('已过期', 'stale')}`
                                    : statusText(condition.status)}
                                </span>
                              </div>
                              <div className="mt-1 truncate font-mono text-[10px] text-slate-500 dark:text-slate-400" title={condition.subjectHash}>
                                {text('证据状态', 'Evidence state')} {condition.subjectHash.slice(0, 19)}
                              </div>
                            </button>
                          ))}
                        </div>

                        {selectedConditionId && !isReadOnlyJudge && (
                          <div className="grid gap-2 border-t border-slate-200 pt-3">
                            <select
                              value={conditionEvidenceId}
                              onChange={(event) => setConditionEvidenceId(event.target.value)}
                              className="min-w-0 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-950"
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
                              className="min-w-0 resize-y rounded-md border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-950"
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

                    {isReadOnlyJudge ? (
                      <div className="border-l-4 border-slate-400 bg-slate-50 px-4 py-3 text-sm text-slate-700 dark:bg-slate-900 dark:text-slate-200">
                        {text('评委账号为只读证据观察者，不显示签核或共享反馈修改控件。', 'Judge accounts are read-only evidence observers; signoff and shared-feedback controls are hidden.')}
                      </div>
                    ) : (
                    <div className="grid gap-3 rounded-md border border-slate-200 bg-slate-50 p-3 text-slate-900 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="text-sm font-semibold text-slate-900 dark:text-slate-100">
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
                          className="min-w-0 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-950"
                          aria-label={text('审核人角色', 'Reviewer role')}
                        >
                          <option value="team_lead">{text('团队负责人', 'Team lead')}</option>
                          <option value="domain_expert">{text('领域专家', 'Domain expert')}</option>
                          <option value="safety_reviewer">{text('安全审核人', 'Safety reviewer')}</option>
                        </select>
                        <input
                          value={reviewerId}
                          onChange={(event) => setReviewerId(event.target.value)}
                          placeholder={text('签核人真实姓名', 'Reviewer name')}
                          aria-label={text('签核人真实姓名', 'Reviewer name')}
                          className="min-w-0 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-950"
                        />
                      </div>
                      <textarea
                        value={signoffRationale}
                        onChange={(event) => setSignoffRationale(event.target.value)}
                        placeholder={text('决策理由', 'Decision rationale')}
                        rows={2}
                        aria-label={text('决策理由', 'Decision rationale')}
                        className="min-w-0 resize-y rounded-md border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-950"
                      />
                      <input
                        value={signoffTargetSections}
                        onChange={(event) => setSignoffTargetSections(event.target.value)}
                        placeholder={text('目标章节，用逗号分隔', 'Target sections, comma separated')}
                        className="min-w-0 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-950"
                      />
                      <textarea
                        value={signoffConditions}
                        onChange={(event) => setSignoffConditions(event.target.value)}
                        placeholder={text('验收条件，每行一条', 'Acceptance conditions, one per line')}
                        rows={2}
                        className="min-w-0 resize-y rounded-md border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-950"
                      />
                      <fieldset className="grid gap-2 border-y border-slate-200 py-3 text-slate-900 dark:border-slate-700 dark:text-slate-100">
                        <legend className="px-1 text-sm font-semibold">{text('本阶段责任确认（不预勾选）', 'Stage acknowledgements (never preselected)')}</legend>
                        {signoffAcknowledgements[selectedSignoffStage].map((item) => (
                          <label key={item.id} className="flex items-start gap-2 text-sm">
                            <input
                              type="checkbox"
                              className="mt-0.5 h-4 w-4"
                              checked={acknowledgements[selectedSignoffStage].includes(item.id)}
                              onChange={(event) => setAcknowledgements((current) => ({
                                ...current,
                                [selectedSignoffStage]: event.target.checked
                                  ? [...current[selectedSignoffStage], item.id]
                                  : current[selectedSignoffStage].filter((value) => value !== item.id),
                              }))}
                            />
                            <span>{item.label[locale]}</span>
                          </label>
                        ))}
                      </fieldset>
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
                        <div className="text-xs text-amber-700 dark:text-amber-300">
                          {text('填写真实姓名和决策理由，并逐项完成本阶段责任确认后方可提交。', 'Enter a real name and rationale, then complete every stage acknowledgement.')}
                        </div>
                      )}
                      <details className="border-t border-slate-200 pt-3 text-sm dark:border-slate-700">
                        <summary className="cursor-pointer text-slate-600 dark:text-slate-300">{text('高级 API 客户端与批量操作', 'Advanced API client and batch actions')}</summary>
                        <div className="mt-3 grid gap-2">
                          <input
                            type="password"
                            value={reviewAuthToken}
                            onChange={(event) => setReviewAuthToken(event.target.value)}
                            placeholder={text('Bearer Token（仅 API 兼容模式）', 'Bearer token (API compatibility mode only)')}
                            autoComplete="off"
                            aria-label={text('ReviewX API Bearer Token', 'ReviewX API bearer token')}
                            className="min-w-0 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-950"
                          />
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => void approveRequiredSignoffs()}
                            disabled={signoffLoading || feedbackApplying || !reviewerId.trim() || !signoffRationale.trim() || !allRequiredAcknowledgementsComplete}
                            className="w-full sm:w-fit"
                          >
                            {signoffLoading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <ShieldCheck className="mr-2 h-4 w-4" />}
                            {text('批准全部必需阶段', 'Approve all required stages')}
                          </Button>
                        </div>
                      </details>
                    </div>
                    )}
                  </div>
                </section>

                {result.runKind === 'platform' && result.planFeedback.packageId && (
                  <div className="flex flex-wrap items-center gap-2 border-t border-slate-200 pt-4">
                    {result.iterationDecision.decision !== 'accept_results' && (
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
                    <Button
                      onClick={() => void createNextRun()}
                      disabled={
                        Boolean(actionLoading)
                        || (result.iterationDecision.decision === 'revise_plan' && !planRevised)
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
                    {nextRunId && (
                      <Link to={`/runs/${nextRunId}`} className={buttonVariants({ variant: 'ghost' })}>
                        {text('查看', 'View')} {nextRunId}
                      </Link>
                    )}
                    {actionMessage && <div className="w-full text-xs text-slate-600">{actionMessage}</div>}
                    {!iterationHumanReady && (
                      <div className="w-full text-xs font-medium text-amber-700">
                        {repairSignoff?.required
                          ? text('下一轮前需完成方案和修复签核。', 'Plan and repair approval are required before the next iteration.')
                          : text('下一轮前需完成方案签核。', 'Plan approval is required before the next iteration.')}
                      </div>
                    )}
                    {!result.planFeedback.applied && result.iterationDecision.decision !== 'accept_results' && (
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
