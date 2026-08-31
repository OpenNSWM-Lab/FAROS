import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  BarChart3,
  BookOpenCheck,
  Bot,
  Check,
  CheckCircle2,
  Clock3,
  Database,
  Download,
  ExternalLink,
  FileCheck2,
  Fingerprint,
  FlaskConical,
  Gauge,
  GitCompareArrows,
  Loader2,
  PlayCircle,
  RefreshCw,
  ShieldCheck,
  UserCheck,
  X,
} from 'lucide-react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { AppPageLayout } from '@/components/layout/AppPageLayout'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { API_BASE_URL } from '@/lib/api'
import { useReviewLocale, type ReviewLocale } from '@/lib/reviewLocale'

type DashboardTab = 'overview' | 'evidence' | 'governance'
type Authority = 'observed' | 'deterministic' | 'qwen' | 'human'

interface MetricRow {
  name: string
  roundOne: number
  roundTwo: number
  delta: number
  direction: 'maximize' | 'minimize'
  improved: boolean
}

interface EvaluationItem {
  id: string
  label: string
  status: 'passed' | 'failed'
  evidence: string
}

interface StageItem {
  id: string
  label: string
  authority: Authority
  status: string
  detail: string
  evidence: string
}

interface PlanChange {
  fieldPath: string
  before: unknown
  after: unknown
  rationale: string
  expectedEffect: string
  evidenceIds: string[]
}

interface CandidateRow {
  candidateId: string
  selected: boolean
  feasible: boolean
  change: string
  metrics: Record<string, number>
  failedConstraints: string[]
}

interface ReliabilityMethod {
  methodId: string
  label: string
  faultDetectionRate: number
  faultDetectionWilson95?: [number, number]
  normalFalseRejectRate: number
  issueLocalizationRate: number
  f1: number
}

interface PairedEffect {
  beforeCorrect: number
  afterCorrect: number
  total: number
  corrected: number
  regressed: number
  exactMcNemarPValue: number
  effectStatus: 'significant_improvement' | 'significant_regression' | 'inconclusive'
}

interface MultidomainEffect {
  dataset: string
  roundOneMacroF1: number
  roundTwoMacroF1: number
  delta: number
  roundOneAccuracy?: number
  roundTwoAccuracy?: number
  roundOneBalancedAccuracy?: number
  roundTwoBalancedAccuracy?: number
  roundOneMcc?: number
  roundTwoMcc?: number
  ci95: [number, number]
  probabilityOfImprovement?: number
  effectStatus: 'significant_improvement' | 'significant_regression' | 'inconclusive'
  resamplingUnit: string
  proposedThreshold?: number
  appliedThreshold?: number
  gateDecision: 'apply_revision' | 'keep_round_one' | 'legacy_apply_revision'
  interventionAudit: {
    wrongToRight?: number
    rightToWrong?: number
    netCorrect?: number
  }
}

interface PlanningMethod {
  methodId: string
  label: string
  executabilityRate: number
  constraintSatisfactionRate: number
  policyAgreementRate: number
  policyAgreementCount: [number, number]
  wilson95?: [number, number]
}

interface EvidenceArtifact {
  filename: string
  sizeBytes: number
  sha256: string
  authority: string
  url: string
}

interface ExternalReviewMethod {
  methodId: string
  label: string
  candidateRate: number
  candidateRatePaperClusterBootstrap95: [number, number]
  meanBestMatchScore: number
  meanTotalTokens: number
  meanLatencyMs: number
  llmEscalationRate: number
  localOnlyRunCount: number
  meanGeneratedFindingCount: number
  failedRunCount: number
  budgetExceededCount: number
}

interface ExternalReviewEffect {
  baselineMethodId: string
  baselineLabel: string
  candidateRateDelta: number
  candidateRateDeltaPaperClusterBootstrap95: [number, number]
  exactMcNemarPValue: number
  meanLatencyDeltaMs: number
  latencyDeltaPaperClusterBootstrap95: [number, number]
  meanTokenDelta: number
  tokenDeltaPaperClusterBootstrap95: [number, number]
}

interface ExternalReviewEvidence {
  available: boolean
  dataset?: string
  split?: string
  paperCount?: number
  questionCount?: number
  sourceCount?: number
  providerName?: string
  model?: string
  temperature?: number
  repetitions?: number
  protocolHash?: string
  protocolVerified?: boolean
  fairTop5?: {
    qualityGate: string
    findingLimit: number
    methods: ExternalReviewMethod[]
    reviewxEffects: ExternalReviewEffect[]
  }
  fullAudit?: {
    available: boolean
    qualityGate: string
    fairMethodComparison: boolean
    methods: ExternalReviewMethod[]
    reviewxEffects: ExternalReviewEffect[]
  }
  reportUrl?: string
  fullAuditReportUrl?: string
  scope?: string
}

interface DashboardPayload {
  generatedAt: string
  track: {
    id: string
    name: string
    officialFocus: string
    reportPageLimit: number
  }
  status: {
    technicalReady: boolean
    publicationReady: boolean
    label: string
    qualityGate: string
    evidenceComplete: boolean
    qwenVerified: boolean
    planDeltaAudit: { status: string; checks: Record<string, boolean>; error?: string }
  }
  case: {
    jobId: string
    runId: string
    dataset: {
      name: string
      fitPairs: number
      feedbackPairs: number
      finalHoldoutPairs: number
    }
    benchmarkFingerprint: string
    scientificQuestion: string
    hypothesis: string
    selectedCandidateId: string
  }
  evaluationMatrix: EvaluationItem[]
  stages: StageItem[]
  feedbackMetrics: MetricRow[]
  holdoutMetrics: MetricRow[]
  holdoutInference: Record<string, {
    improvementMean: number
    ci95Low: number
    ci95High: number
    probabilityOfImprovement: number
  }>
  holdoutEffect: {
    improvementMean: number
    ci95Low: number
    ci95High: number
    probabilityOfImprovement: number
    effectStatus: 'significant_improvement' | 'significant_regression' | 'inconclusive'
    claim: string
  }
  planDelta: {
    available: boolean
    audit: { status: string }
    trigger?: {
      statement: string
      metric: string
      observedValue: number
      targetValue?: number
    }
    selectedCandidateId: string
    changes: PlanChange[]
    candidates: CandidateRow[]
    qwenContribution: {
      model: string
      role: string
      selectedCandidateId: string
      rationale?: string
      expectedTradeoff?: string
      falsificationCriteria?: string[]
      promptHash?: string
      finalHoldoutExposed?: boolean
    }
    contentHash?: string
  }
  qwen: {
    provider: string
    model: string
    latencyMs: number
    usage: Record<string, number>
    promptHash: string
    policyFollowed: boolean
    finalHoldoutExposed: boolean
  }
  reliability: {
    runId: string
    datasets: string[]
    caseAudit: { total: number; faulty: number; clean: number }
    methods: ReliabilityMethod[]
    repairEvaluation: { attempted: number; passed: number; successRate?: number }
    qwenMissCount: number
    pairedEffects: {
      comparison: string
      faultDetection: PairedEffect
      issueLocalization: PairedEffect
      scope: string
    }
    scope: string
  }
  planning: {
    runCount: number
    seeds: number[]
    methods: PlanningMethod[]
    qwenCost: {
      totalTokens?: number
      meanLatencyMs?: number
      estimatedCostCny?: number
      failureRate?: number
    }
    scope: string
  }
  multidomain: {
    datasets: string[]
    effects: MultidomainEffect[]
    headline?: MultidomainEffect
    scope: string
  }
  externalReview: ExternalReviewEvidence
  humanGovernance: {
    feedbackId?: string
    signoffs: Record<'plan' | 'repair' | 'conclusion', string>
    publicationReady: boolean
    reviewerSeparationRequired: boolean
    reviewerPolicy: 'single_accountable_reviewer' | 'separated_reviewers' | string
    responsibleReviewerCount: number
    note: string
  }
  evidenceManifest: EvidenceArtifact[]
  limitations: string[]
  provenanceLegend: Array<{ id: Authority; label: string; description: string }>
}

interface CompetitionJob {
  jobId: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  error?: string
  stage?: 'queued' | 'preparing' | 'executing' | 'registering' | 'completed' | 'failed'
  progressPercent?: number
  createdAt?: string
  updatedAt?: string
  model?: string
  runId?: string
  qualityGate?: string
  feedbackId?: string
}

const stageLabels: Record<string, Record<ReviewLocale, string>> = {
  question: { 'zh-CN': '研究问题与约束', 'en-US': 'Question & constraints' },
  round_one: { 'zh-CN': '第一轮执行', 'en-US': 'Round 1 execution' },
  reviewx: { 'zh-CN': 'ReviewX 证据审计', 'en-US': 'ReviewX evidence audit' },
  qwen: { 'zh-CN': 'Qwen 权衡规划', 'en-US': 'Qwen constrained planning' },
  delta: { 'zh-CN': '计划变化合同', 'en-US': 'Plan delta contract' },
  round_two: { 'zh-CN': '第二轮复验', 'en-US': 'Round 2 verification' },
  human: { 'zh-CN': '人工结论签核', 'en-US': 'Human conclusion signoff' },
}

const evaluationLabels: Record<string, Record<ReviewLocale, string>> = {
  closed_loop: { 'zh-CN': '闭环链条完整', 'en-US': 'Complete closed loop' },
  executable_plan: { 'zh-CN': '计划变化可执行', 'en-US': 'Executable plan change' },
  evidence_grounding: { 'zh-CN': '假设与判断有证据', 'en-US': 'Evidence-grounded decisions' },
  feedback_changes_plan: { 'zh-CN': '真实结果改变下一轮计划', 'en-US': 'Results change the next plan' },
  iteration_visible: { 'zh-CN': '迭代过程清楚可追溯', 'en-US': 'Traceable iteration history' },
  measured_improvement: { 'zh-CN': '第二轮结果完成独立复验', 'en-US': 'Round 2 independently evaluated' },
}

const limitationTranslations: Record<string, string> = {
  'The representative case is a public-data computational experiment, not a wet-lab or instrument deployment.': '代表案例是基于公开数据的计算实验，不是湿实验或仪器部署。',
  'The final-holdout F1 interval crosses zero, so no significant improvement or non-inferiority is claimed.': '最终未见集 F1 区间跨 0，因此不宣称显著提升或非劣效。',
  'The 90-case reliability benchmark uses controlled injected faults and cannot estimate natural-error prevalence.': '90 例可靠性基准使用受控注入故障，不能估计自然错误的发生率。',
  'Public scientific conclusions remain blocked until real, independent human signoffs are current.': '在真实、独立的人工签核生效前，公开科学结论仍被阻断。',
  'PeerQA lexical alignment is a candidate-generation proxy, not expert review recall or correctness.': 'PeerQA 词汇对齐是候选生成代理指标，不代表专家审稿召回率或正确性。',
  'The PeerQA full-audit view exposes more findings than the baselines and is not a fair output-count comparison.': 'PeerQA 完整审计展示的 finding 数量多于 baseline，不是公平的输出数量对照。',
}

const authorityLabels: Record<Authority, Record<ReviewLocale, { label: string; description: string }>> = {
  observed: {
    'zh-CN': { label: '真实观测', description: '来自数据集、实验运行或人工录入的事实' },
    'en-US': { label: 'Observed', description: 'Facts from datasets, experiment runs, or human input' },
  },
  deterministic: {
    'zh-CN': { label: '程序判定', description: '可重放的代码、合同与 Gate 检查' },
    'en-US': { label: 'Deterministic', description: 'Replayable code, contracts, and Gate checks' },
  },
  qwen: {
    'zh-CN': { label: 'Qwen 输出', description: 'Qwen 生成的候选解释或规划建议' },
    'en-US': { label: 'Qwen output', description: 'Candidate explanations or plans generated by Qwen' },
  },
  human: {
    'zh-CN': { label: '人工决策', description: '由真实审核人承担责任的签核结论' },
    'en-US': { label: 'Human decision', description: 'Accountable signoff by an identified reviewer' },
  },
}

const jobStageLabels: Record<NonNullable<CompetitionJob['stage']>, Record<ReviewLocale, string>> = {
  queued: { 'zh-CN': '等待调度', 'en-US': 'Queued' },
  preparing: { 'zh-CN': '准备数据', 'en-US': 'Preparing data' },
  executing: { 'zh-CN': '执行两轮闭环', 'en-US': 'Running two-round loop' },
  registering: { 'zh-CN': '注册审核记录', 'en-US': 'Registering review record' },
  completed: { 'zh-CN': '复验完成', 'en-US': 'Verification completed' },
  failed: { 'zh-CN': '复验失败', 'en-US': 'Verification failed' },
}

const authorityTone: Record<Authority, string> = {
  observed: 'border-emerald-200 bg-emerald-50 text-emerald-800',
  deterministic: 'border-sky-200 bg-sky-50 text-sky-800',
  qwen: 'border-amber-200 bg-amber-50 text-amber-900',
  human: 'border-violet-200 bg-violet-50 text-violet-800',
}

const authorityDot: Record<Authority, string> = {
  observed: 'bg-emerald-500',
  deterministic: 'bg-sky-500',
  qwen: 'bg-amber-500',
  human: 'bg-violet-500',
}

const shortCandidateName = (value: string) => value
  .replace('retain_full_', 'full ')
  .replace('remove_numeric_', '-numeric ')
  .replace('remove_negation_', '-negation ')
  .replace('remove_entity_', '-entity ')

const formatNumber = (value: number | undefined, digits = 3) => (
  typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '--'
)

const formatPercent = (value: number | undefined, digits = 1) => (
  typeof value === 'number' && Number.isFinite(value) ? `${(value * 100).toFixed(digits)}%` : '--'
)

const formatSignedPercent = (value: number | undefined, digits = 1) => (
  typeof value === 'number' && Number.isFinite(value)
    ? `${value >= 0 ? '+' : ''}${(value * 100).toFixed(digits)}%`
    : '--'
)

const numericDelta = (after: number | undefined, before: number | undefined) => (
  typeof after === 'number' && typeof before === 'number' ? after - before : undefined
)

const formatValue = (value: unknown) => {
  if (Array.isArray(value)) return `${value.length} factors`
  if (typeof value === 'number') return String(value)
  if (typeof value === 'boolean') return value ? 'enabled' : 'disabled'
  if (value === null || value === undefined) return '--'
  return String(value)
}

const shortHash = (value?: string) => {
  if (!value) return '--'
  return value.length > 24 ? `${value.slice(0, 15)}...${value.slice(-7)}` : value
}

function SectionHeading({
  icon: Icon,
  title,
  detail,
}: {
  icon: typeof Activity
  title: string
  detail: string
}) {
  return (
    <div className="mb-4 flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-950">
          <Icon className="h-5 w-5 text-sky-700" />
          {title}
        </h2>
        <p className="mt-1 text-sm text-slate-500">{detail}</p>
      </div>
    </div>
  )
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-md border border-dashed border-slate-300 bg-white p-8 text-center text-sm text-slate-500">
      {message}
    </div>
  )
}

export function CompetitionEvidence() {
  const { locale, isChinese, text } = useReviewLocale()
  const [tab, setTab] = useState<DashboardTab>('overview')
  const [dashboard, setDashboard] = useState<DashboardPayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [freshJob, setFreshJob] = useState<CompetitionJob | null>(null)
  const [startingFresh, setStartingFresh] = useState(false)

  const loadDashboard = useCallback(async (): Promise<DashboardPayload | null> => {
    setLoading(true)
    setError('')
    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/reviews/reviewx/competition/dashboard`)
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) {
        const detail = typeof payload.detail === 'string'
          ? payload.detail
          : text('比赛证据接口暂不可用。', 'Competition evidence is temporarily unavailable.')
        throw new Error(detail)
      }
      const nextDashboard = payload as DashboardPayload
      setDashboard(nextDashboard)
      return nextDashboard
    } catch (loadError) {
      setError(loadError instanceof Error
        ? loadError.message
        : text('比赛证据加载失败。', 'Failed to load competition evidence.'))
      return null
    } finally {
      setLoading(false)
    }
  }, [text])

  useEffect(() => {
    void loadDashboard()
  }, [loadDashboard])

  useEffect(() => {
    if (!freshJob || !['queued', 'running'].includes(freshJob.status)) return
    const timer = window.setTimeout(async () => {
      try {
        const response = await fetch(
          `${API_BASE_URL}/api/v1/reviews/reviewx/competition/scifact/jobs/${encodeURIComponent(freshJob.jobId)}`,
        )
        if (!response.ok) return
        const job = await response.json() as CompetitionJob
        setFreshJob(job)
        if (job.status === 'completed') await loadDashboard()
      } catch {
        // The next polling attempt remains available through the refresh command.
      }
    }, 2000)
    return () => window.clearTimeout(timer)
  }, [freshJob, loadDashboard])

  const runFreshCase = async () => {
    setStartingFresh(true)
    setError('')
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/v1/reviews/reviewx/competition/scifact/jobs`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ reuseLatest: false, bootstrapSamples: 2000 }),
        },
      )
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) {
        const detail = typeof payload.detail === 'string'
          ? payload.detail
          : text('无法启动新的 Qwen 案例。', 'Unable to start a new Qwen case.')
        throw new Error(detail)
      }
      setFreshJob(payload as CompetitionJob)
    } catch (runError) {
      setError(runError instanceof Error
        ? runError.message
        : text('新的 Qwen 案例启动失败。', 'Failed to start the new Qwen case.'))
    } finally {
      setStartingFresh(false)
    }
  }

  const openWorkflowSection = (nextTab: DashboardTab, sectionId: string) => {
    setTab(nextTab)
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        document.getElementById(sectionId)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      })
    })
  }

  const feedbackChart = useMemo(() => {
    if (!dashboard) return []
    const selected = ['Precision', 'Recall', 'F1-Score', 'Expected Calibration Error (ECE)']
    return selected.map((name) => {
      const item = dashboard.feedbackMetrics.find((metric) => metric.name === name)
      return {
        metric: name === 'Expected Calibration Error (ECE)' ? 'ECE' : name,
        roundOne: item?.roundOne ?? 0,
        roundTwo: item?.roundTwo ?? 0,
      }
    })
  }, [dashboard])

  const candidateChart = useMemo(() => (dashboard?.planDelta.candidates || []).map((candidate) => ({
    name: shortCandidateName(candidate.candidateId),
    f1: candidate.metrics['F1-Score'] || 0,
    ece: candidate.metrics['Expected Calibration Error (ECE)'] || 0,
    selected: candidate.selected,
    feasible: candidate.feasible,
  })), [dashboard])

  const peerqaEvidence = useMemo(() => {
    const evidence = dashboard?.externalReview
    const fair = evidence?.fairTop5
    if (!evidence?.available || !fair) return null
    const reviewx = fair.methods.find((item) => item.methodId.startsWith('reviewx_'))
    const fullReviewx = evidence.fullAudit?.methods.find((item) => item.methodId.startsWith('reviewx_'))
    const singleEffect = fair.reviewxEffects.find((item) => item.baselineMethodId.includes('single_prompt'))
    const rubricEffect = fair.reviewxEffects.find((item) => item.baselineMethodId.includes('rubric'))
    const fullSingleEffect = evidence.fullAudit?.reviewxEffects.find(
      (item) => item.baselineMethodId.includes('single_prompt'),
    )
    return {
      chart: fair.methods.map((item) => ({
        ...item,
        chartLabel: item.methodId.startsWith('reviewx_')
          ? 'ReviewX'
          : item.methodId.includes('rubric') ? 'Rubric' : 'Single',
      })),
      reviewx,
      fullReviewx,
      singleEffect,
      rubricEffect,
      fullSingleEffect,
    }
  }, [dashboard])

  const headline = useMemo(() => {
    if (!dashboard) return null
    const feedbackF1 = dashboard.feedbackMetrics.find((item) => item.name === 'F1-Score')
    const holdoutF1 = dashboard.holdoutMetrics.find((item) => item.name === 'F1-Score')
    const full = dashboard.reliability.methods.find((item) => item.methodId === 'faros_full')
    return {
      feedbackF1,
      holdoutF1,
      full,
      reliabilityEffects: dashboard.reliability.pairedEffects,
      multidomain: dashboard.multidomain.headline,
    }
  }, [dashboard])

  const jobActive = startingFresh || ['queued', 'running'].includes(freshJob?.status || '')
  const evidenceAuditPath = dashboard?.humanGovernance.feedbackId
    ? `/review/consistency?feedbackId=${encodeURIComponent(dashboard.humanGovernance.feedbackId)}`
    : '/review/consistency'
  const dashboardStatusLabel = dashboard?.status.publicationReady
    ? text('技术证据与人工签核已通过', 'Technical evidence and human signoff passed')
    : dashboard?.status.technicalReady
      ? text('技术证据就绪 · 待人工签核', 'Technical evidence ready · human signoff pending')
      : text('证据存在缺口', 'Evidence gaps detected')
  const activeJobStage = freshJob?.stage
    || (freshJob?.status === 'completed' ? 'completed' : freshJob?.status === 'failed' ? 'failed' : 'queued')
  const activeJobProgress = Math.max(0, Math.min(100, freshJob?.progressPercent
    ?? (freshJob?.status === 'completed' || freshJob?.status === 'failed' ? 100 : 5)))
  const workflowItems = dashboard ? [
    {
      id: 'run',
      label: text('运行复验', 'Run'),
      detail: jobActive
        ? jobStageLabels[activeJobStage][locale]
        : text('Qwen 闭环已可复现', 'Qwen loop is reproducible'),
      state: jobActive ? 'active' : dashboard.status.qwenVerified ? 'complete' : 'attention',
      tab: 'overview' as DashboardTab,
      sectionId: 'reviewx-loop',
    },
    {
      id: 'plan',
      label: text('计划变化', 'Plan delta'),
      detail: dashboard.planDelta.audit.status === 'passed'
        ? text('合同已通过', 'Contract passed')
        : text('需要检查', 'Needs review'),
      state: dashboard.planDelta.audit.status === 'passed' ? 'complete' : 'attention',
      tab: 'overview' as DashboardTab,
      sectionId: 'reviewx-plan',
    },
    {
      id: 'evidence',
      label: text('证据核验', 'Evidence'),
      detail: dashboard.status.evidenceComplete
        ? text('机器可核验', 'Machine-verifiable')
        : text('证据不完整', 'Incomplete'),
      state: dashboard.status.evidenceComplete ? 'complete' : 'attention',
      tab: 'evidence' as DashboardTab,
      sectionId: 'reviewx-reliability',
    },
    {
      id: 'signoff',
      label: text('人工签核', 'Signoff'),
      detail: dashboard.humanGovernance.publicationReady
        ? text('已完成', 'Completed')
        : text('待负责人审核', 'Reviewer action required'),
      state: dashboard.humanGovernance.publicationReady ? 'complete' : 'attention',
      tab: 'governance' as DashboardTab,
      sectionId: 'reviewx-governance',
    },
    {
      id: 'export',
      label: text('证据导出', 'Export'),
      detail: dashboard.humanGovernance.publicationReady
        ? text('正式包可用', 'Official bundle ready')
        : text('仅可导出草稿', 'Draft only'),
      state: dashboard.humanGovernance.publicationReady ? 'complete' : 'blocked',
      tab: 'evidence' as DashboardTab,
      sectionId: 'reviewx-artifacts',
    },
  ] : []
  const localizedStageDetail = (stage: StageItem) => {
    if (stage.id === 'question') {
      return text(
        '一次证据驱动修订能否在不违反校准和排序护栏的情况下，提高无支持科学主张的检测能力？',
        dashboard?.case.scientificQuestion || stage.detail,
      )
    }
    if (stage.id === 'reviewx') {
      return text('复算指标、校验来源、隔离未见集并审计候选约束。', 'Recompute metrics, verify sources, isolate the holdout, and audit candidate constraints.')
    }
    if (stage.id === 'qwen') {
      return `${dashboard?.qwen.model || 'Qwen'}; ${text('选择', 'selected')} ${dashboard?.case.selectedCandidateId || '--'}; ${text('最终未见集未暴露', 'final holdout hidden')}`
    }
    if (stage.id === 'delta') {
      return text(
        `${dashboard?.planDelta.changes.length || 0} 个字段变更并与证据哈希绑定。`,
        `${dashboard?.planDelta.changes.length || 0} fields changed and hash-bound to evidence.`,
      )
    }
    if (stage.id === 'human') {
      return text('自动证据不能代替人工批准公开科学结论。', 'Automated evidence cannot approve a public scientific conclusion.')
    }
    return stage.detail
  }

  const pageActions = (
    <div className="flex flex-wrap items-center gap-2">
      <Button
        variant="outline"
        size="sm"
        onClick={() => void loadDashboard()}
        disabled={loading}
        title={text('刷新证据', 'Refresh evidence')}
      >
        <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
        <span className="ml-2 hidden sm:inline">{text('刷新', 'Refresh')}</span>
      </Button>
      <Button
        size="sm"
        onClick={() => void runFreshCase()}
        disabled={jobActive}
        title={text('调用 Qwen API 并重新执行两轮复验，可能产生 API 费用', 'Call the Qwen API and rerun the two-round case; API charges may apply')}
      >
        {jobActive ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <PlayCircle className="h-4 w-4" />
        )}
        <span className="ml-2">{text('运行真实复验', 'Run verified case')}</span>
      </Button>
    </div>
  )

  return (
    <AppPageLayout
      title={text('赛事证据 · Track 1B', 'Competition Evidence · Track 1B')}
      subtitle={text('主流程完成后的验收视图：汇总实验、ReviewX 反馈闭环与人工签核', 'Acceptance view after the main workflow: experiments, ReviewX feedback loops, and human signoff')}
      icon={Gauge}
      iconColor="blue"
      accentColor="blue"
      actions={pageActions}
      breadcrumb={(
        <Link to={evidenceAuditPath} className="text-sm text-sky-700 hover:underline">
          {text('进入论文证据审计', 'Open evidence audit')}
        </Link>
      )}
    >
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3 border-l-4 border-sky-600 bg-sky-50 px-4 py-3 text-sky-950" role="note">
        <div className="flex min-w-0 items-start gap-3">
          <Database className="mt-0.5 h-4 w-4 shrink-0 text-sky-700" />
          <div>
            <div className="text-sm font-semibold">{text('数据范围：团队共享验证成果', 'Data scope: shared team evidence')}</div>
            <p className="mt-0.5 text-xs leading-relaxed text-sky-900">
              {text(
                '本页是 Idea → Plan → Code → Experiment → ReviewX 主流程之后的证据验收层，不是新的流程步骤。页面会读取团队已完成的 benchmark；只有点击“运行真实复验”才会在内网计算节点创建专项验证任务。',
                'This is an evidence acceptance layer after Idea → Plan → Code → Experiment → ReviewX, not another workflow stage. It reads completed team benchmarks; only “Run verified case” creates a dedicated job on the private compute node.',
              )}
            </p>
          </div>
        </div>
        <Badge variant="outline" className="shrink-0 border-sky-300 bg-white text-sky-800">
          {text('团队预置', 'Team baseline')}
        </Badge>
      </div>

      {error && (
        <div className="mb-5 flex items-start gap-3 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {freshJob && (
        <div className={`mb-5 rounded-md border px-4 py-3 text-sm ${
          freshJob.status === 'completed'
            ? 'border-emerald-200 bg-emerald-50 text-emerald-900'
            : freshJob.status === 'failed'
              ? 'border-red-200 bg-red-50 text-red-900'
              : 'border-amber-200 bg-amber-50 text-amber-900'
        }`}>
          <div className="flex items-start justify-between gap-3">
            <div className="flex min-w-0 items-start gap-2">
              {freshJob.status === 'completed'
                ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
                : freshJob.status === 'failed'
                  ? <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                  : <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin" />}
              <div className="min-w-0">
                <div className="font-semibold">{jobStageLabels[activeJobStage][locale]}</div>
                <div className="mt-0.5 truncate font-mono text-[11px] opacity-75">{freshJob.jobId}</div>
                {freshJob.error && <div className="mt-1 text-xs">{freshJob.error}</div>}
                {freshJob.status === 'completed' && (
                  <div className="mt-1 text-xs">
                    {text('新证据快照已加载', 'The new evidence snapshot is loaded')}
                    {freshJob.qualityGate && ` · Gate ${freshJob.qualityGate}`}
                  </div>
                )}
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              {freshJob.status === 'completed' && (
                <Button size="sm" variant="outline" onClick={() => openWorkflowSection('overview', 'reviewx-results')}>
                  {text('查看结果', 'View results')}
                </Button>
              )}
              {freshJob.status === 'failed' && (
                <Button size="sm" variant="outline" onClick={() => void runFreshCase()}>
                  {text('重试', 'Retry')}
                </Button>
              )}
              <button
                type="button"
                onClick={() => setFreshJob(null)}
                className="inline-flex h-8 w-8 items-center justify-center rounded-md hover:bg-black/5"
                aria-label={text('关闭运行通知', 'Dismiss run notification')}
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>
          <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-black/10" role="progressbar" aria-valuenow={activeJobProgress} aria-valuemin={0} aria-valuemax={100}>
            <div className="h-full bg-current transition-all duration-500" style={{ width: `${activeJobProgress}%` }} />
          </div>
        </div>
      )}

      {loading && !dashboard ? (
        <div className="space-y-4">
          <Skeleton className="h-32 w-full" />
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            {[0, 1, 2, 3].map((item) => <Skeleton key={item} className="h-28 w-full" />)}
          </div>
          <Skeleton className="h-80 w-full" />
        </div>
      ) : !dashboard || !headline ? (
        <EmptyState message={text('尚无可核验的方向 1B 代表案例。', 'No verifiable Track 1B case is available yet.')} />
      ) : (
        <div className="min-w-0 space-y-5">
          <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
            <div className="grid gap-5 border-b border-slate-200 bg-slate-950 px-5 py-5 text-white lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2 text-xs font-semibold uppercase text-sky-200">
                  <span>FAROS / ReviewX</span>
                  <span className="h-1 w-1 rounded-full bg-sky-300" />
                  <span>{dashboard.track.id}</span>
                  <span className="h-1 w-1 rounded-full bg-sky-300" />
                  <span>{dashboard.case.dataset.name}</span>
                </div>
                <h1 className="mt-2 max-w-5xl text-xl font-semibold leading-snug sm:text-2xl">
                  {isChinese
                    ? '一次证据驱动修订能否在不违反校准和排序护栏的情况下，提高无支持科学主张的检测能力？'
                    : dashboard.case.scientificQuestion}
                </h1>
                <p className="mt-2 max-w-4xl text-sm leading-relaxed text-slate-300">
                  {isChinese
                    ? '真实实验结果必须改变下一轮计划，并逐轮重新评估。'
                    : 'Real experimental results must change the next-round plan and be re-evaluated iteration by iteration.'}
                </p>
              </div>
              <div className="flex flex-col items-start gap-2 lg:items-end">
                <Badge className={dashboard.status.technicalReady
                  ? 'border-emerald-400 bg-emerald-400 text-emerald-950'
                  : 'border-amber-300 bg-amber-300 text-amber-950'}>
                  {dashboard.status.technicalReady ? <Check className="mr-1 h-3.5 w-3.5" /> : <AlertTriangle className="mr-1 h-3.5 w-3.5" />}
                  {dashboardStatusLabel}
                </Badge>
                <div className="font-mono text-[11px] text-slate-400">{dashboard.case.runId}</div>
              </div>
            </div>
            <div className="grid grid-cols-2 divide-x divide-y divide-slate-200 sm:grid-cols-4 sm:divide-y-0">
              <div className="p-4">
                <div className="flex flex-wrap items-center gap-2 text-xs font-medium text-slate-500">
                  <span>{text('反馈集 F1', 'Feedback F1')}</span>
                  <span className="rounded-sm bg-sky-100 px-1.5 py-0.5 text-[10px] font-semibold text-sky-800">{text('开发集', 'Development')}</span>
                </div>
                <div className="mt-1 text-2xl font-semibold text-slate-950">+{formatNumber(headline.feedbackF1?.delta, 4)}</div>
                <div className="mt-1 text-xs text-sky-700">{formatNumber(headline.feedbackF1?.roundOne, 4)} → {formatNumber(headline.feedbackF1?.roundTwo, 4)}</div>
              </div>
              <div className="p-4">
                <div className="flex flex-wrap items-center gap-2 text-xs font-medium text-slate-500">
                  <span>{text('受控故障检出', 'Controlled fault detection')}</span>
                  <span className={`rounded-sm px-1.5 py-0.5 text-[10px] font-semibold ${headline.reliabilityEffects.faultDetection.effectStatus === 'significant_improvement' ? 'bg-emerald-100 text-emerald-800' : 'bg-slate-100 text-slate-700'}`}>
                    {headline.reliabilityEffects.faultDetection.effectStatus === 'significant_improvement' ? text('配对 p < .05', 'Paired p < .05') : text('结论未定', 'Inconclusive')}
                  </span>
                </div>
                <div className="mt-1 text-2xl font-semibold text-slate-950">{formatPercent(headline.full?.faultDetectionRate, 0)}</div>
                <div className="mt-1 text-xs text-emerald-700">{headline.reliabilityEffects.faultDetection.beforeCorrect}/{headline.reliabilityEffects.faultDetection.total} → {headline.reliabilityEffects.faultDetection.afterCorrect}/{headline.reliabilityEffects.faultDetection.total} · p={formatNumber(headline.reliabilityEffects.faultDetection.exactMcNemarPValue, 3)}</div>
              </div>
              <div className="p-4">
                <div className="flex flex-wrap items-center gap-2 text-xs font-medium text-slate-500">
                  <span>{text('问题定位', 'Issue localization')}</span>
                  <span className={`rounded-sm px-1.5 py-0.5 text-[10px] font-semibold ${headline.reliabilityEffects.issueLocalization.effectStatus === 'significant_improvement' ? 'bg-emerald-100 text-emerald-800' : 'bg-slate-100 text-slate-700'}`}>
                    {headline.reliabilityEffects.issueLocalization.effectStatus === 'significant_improvement' ? text('配对 p < .05', 'Paired p < .05') : text('结论未定', 'Inconclusive')}
                  </span>
                </div>
                <div className="mt-1 text-2xl font-semibold text-slate-950">{formatPercent(headline.full?.issueLocalizationRate, 0)}</div>
                <div className="mt-1 text-xs text-emerald-700">{headline.reliabilityEffects.issueLocalization.beforeCorrect}/{headline.reliabilityEffects.issueLocalization.total} → {headline.reliabilityEffects.issueLocalization.afterCorrect}/{headline.reliabilityEffects.issueLocalization.total} · p={formatNumber(headline.reliabilityEffects.issueLocalization.exactMcNemarPValue, 3)}</div>
              </div>
              <div className="p-4">
                <div className="flex flex-wrap items-center gap-2 text-xs font-medium text-slate-500">
                  <span>{headline.multidomain?.dataset || text('跨域测试', 'Cross-domain test')} Macro-F1</span>
                  <span className={`rounded-sm px-1.5 py-0.5 text-[10px] font-semibold ${headline.multidomain?.effectStatus === 'significant_improvement' ? 'bg-emerald-100 text-emerald-800' : 'bg-slate-100 text-slate-700'}`}>
                    {headline.multidomain?.effectStatus === 'significant_improvement' ? '95% CI > 0' : text('无显著结果', 'No significant result')}
                  </span>
                </div>
                <div className="mt-1 text-2xl font-semibold text-slate-950">{formatSignedPercent(headline.multidomain?.delta, 1)}</div>
                <div className={`mt-1 text-xs ${headline.multidomain?.effectStatus === 'significant_improvement' ? 'text-emerald-700' : 'text-slate-600'}`}>CI [{formatSignedPercent(headline.multidomain?.ci95[0], 1)}, {formatSignedPercent(headline.multidomain?.ci95[1], 1)}]</div>
              </div>
            </div>
            <div className={`flex flex-col gap-1 border-t border-slate-200 px-4 py-3 text-xs sm:flex-row sm:items-center sm:justify-between ${dashboard.holdoutEffect.effectStatus === 'significant_improvement' ? 'bg-emerald-50/60 text-emerald-950' : dashboard.holdoutEffect.effectStatus === 'significant_regression' ? 'bg-red-50/60 text-red-950' : 'bg-amber-50/60 text-amber-950'}`}>
              <span className="font-semibold">
                {dashboard.holdoutEffect.effectStatus === 'significant_improvement'
                  ? text('SciFact 最终未见集检测到显著 F1 提升', 'Significant SciFact final-holdout F1 improvement detected')
                  : dashboard.holdoutEffect.effectStatus === 'significant_regression'
                    ? text('科学警报：SciFact 最终未见集 F1 显著回归', 'Scientific alert: significant SciFact final-holdout F1 regression')
                    : text('科学边界：SciFact 最终未见集未检测到显著 F1 变化', 'Scientific boundary: no significant SciFact final-holdout F1 change detected')}
              </span>
              <span>Δ {formatSignedPercent(headline.holdoutF1?.delta, 2)} · 95% CI [{formatSignedPercent(dashboard.holdoutEffect.ci95Low, 2)}, {formatSignedPercent(dashboard.holdoutEffect.ci95High, 2)}]</span>
            </div>
          </section>

          <div className="flex flex-col gap-3 rounded-md border border-slate-200 bg-white px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex flex-wrap gap-2">
              {dashboard.provenanceLegend.map((item) => (
                <span key={item.id} className={`inline-flex items-center gap-2 rounded-md border px-2.5 py-1 text-xs font-medium ${authorityTone[item.id]}`} title={authorityLabels[item.id][locale].description}>
                  <span className={`h-2 w-2 rounded-full ${authorityDot[item.id]}`} />
                  {authorityLabels[item.id][locale].label}
                </span>
              ))}
            </div>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
              <span className="inline-flex items-center gap-1"><Bot className="h-3.5 w-3.5" />{dashboard.qwen.model}</span>
              <span className="inline-flex items-center gap-1"><Clock3 className="h-3.5 w-3.5" />{(dashboard.qwen.latencyMs / 1000).toFixed(2)} s</span>
              <span className="inline-flex items-center gap-1"><Fingerprint className="h-3.5 w-3.5" />{shortHash(dashboard.qwen.promptHash)}</span>
            </div>
          </div>

          <nav
            className="sticky top-2 z-20 overflow-x-auto rounded-md border border-slate-200 bg-white/95 p-2 shadow-sm backdrop-blur"
            aria-label={text('ReviewX 任务轨迹', 'ReviewX workflow')}
          >
            <div className="flex min-w-max items-stretch gap-1">
              {workflowItems.map((item, index) => {
                const complete = item.state === 'complete'
                const active = item.state === 'active'
                const blocked = item.state === 'blocked'
                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => openWorkflowSection(item.tab, item.sectionId)}
                    className={`group flex min-h-[58px] w-[172px] items-center gap-3 rounded-md px-3 py-2 text-left transition-colors ${
                      active
                        ? 'bg-sky-700 text-white'
                        : complete
                          ? 'bg-emerald-50 text-emerald-950 hover:bg-emerald-100'
                          : blocked
                            ? 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                            : 'bg-amber-50 text-amber-950 hover:bg-amber-100'
                    }`}
                  >
                    <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs font-bold ${
                      active ? 'border-white/40 bg-white/10' : complete ? 'border-emerald-300 bg-white' : 'border-current/20 bg-white/60'
                    }`}>
                      {complete ? <Check className="h-4 w-4" /> : index + 1}
                    </span>
                    <span className="min-w-0">
                      <span className="block text-xs font-semibold">{item.label}</span>
                      <span className={`mt-0.5 block truncate text-[10px] ${active ? 'text-sky-100' : 'opacity-70'}`}>{item.detail}</span>
                    </span>
                  </button>
                )
              })}
            </div>
          </nav>

          <Tabs className="min-w-0" value={tab} onValueChange={(value) => setTab(value as DashboardTab)}>
            <TabsList className="grid h-auto w-full grid-cols-3 bg-slate-200/70 p-1 sm:w-[520px]">
              <TabsTrigger value="overview">{text('闭环与计划变化', 'Loop & plan delta')}</TabsTrigger>
              <TabsTrigger value="evidence">{text('对照与证据', 'Comparisons & evidence')}</TabsTrigger>
              <TabsTrigger value="governance">{text('人工治理与边界', 'Human governance')}</TabsTrigger>
            </TabsList>

            <TabsContent value="overview" className="min-w-0 space-y-5">
              <section id="reviewx-loop" className="scroll-mt-28 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                <SectionHeading
                  icon={Activity}
                  title={text('可核验闭环', 'Verifiable loop')}
                  detail={text('每个节点都绑定来源，模型输出与真实观测不混写', 'Every stage is source-bound; model output is kept separate from observed evidence')}
                />
                <div className="overflow-x-auto pb-2">
                  <div className="grid min-w-[980px] grid-cols-7 gap-2">
                    {dashboard.stages.map((stage, index) => (
                      <div key={stage.id} className="relative">
                        <div className={`h-full min-h-[150px] rounded-md border p-3 ${authorityTone[stage.authority]}`}>
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-[11px] font-semibold uppercase">{String(index + 1).padStart(2, '0')}</span>
                            {['passed', 'executed', 'called', 'frozen'].includes(stage.status)
                              ? <CheckCircle2 className="h-4 w-4" />
                              : <AlertTriangle className="h-4 w-4" />}
                          </div>
                          <div className="mt-3 text-sm font-semibold text-slate-950">{stageLabels[stage.id]?.[locale] || stage.label}</div>
                          <p className="mt-2 text-xs leading-relaxed text-slate-700">{localizedStageDetail(stage)}</p>
                          <div className="mt-3 break-words text-[10px] text-slate-500">{stage.evidence}</div>
                        </div>
                        {index < dashboard.stages.length - 1 && (
                          <ArrowRight className="absolute -right-3 top-[66px] z-10 h-4 w-4 rounded-full bg-white text-slate-400" />
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              </section>

              <section id="reviewx-plan" className="scroll-mt-28 grid min-w-0 gap-5 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.4fr)]">
                <div className="min-w-0 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                  <SectionHeading
                    icon={GitCompareArrows}
                    title={text('证据到计划变化合同', 'Evidence-to-plan delta contract')}
                    detail={text('变更发生在最终未见集加载之前', 'Changes are frozen before the final holdout is loaded')}
                  />
                  {!dashboard.planDelta.available ? (
                    <EmptyState message={text('当前运行早于计划变化合同，需重新运行真实 Qwen 案例。', 'This run predates plan delta contracts. Run a new Qwen case.')} />
                  ) : (
                    <div className="space-y-4">
                      <div className="rounded-md border border-amber-200 bg-amber-50 p-3">
                        <div className="text-xs font-semibold uppercase text-amber-900">{text('触发证据', 'Trigger evidence')}</div>
                        <p className="mt-1 text-sm leading-relaxed text-slate-800">{dashboard.planDelta.trigger?.statement}</p>
                      </div>
                      <div className="space-y-3">
                        {dashboard.planDelta.changes.map((change) => (
                          <div key={change.fieldPath} className="rounded-md border border-slate-200 p-3">
                            <div className="font-mono text-xs font-semibold text-sky-800">{change.fieldPath}</div>
                            <div className="mt-2 grid grid-cols-[1fr_auto_1fr] items-center gap-2 text-sm">
                              <span className="min-w-0 truncate rounded bg-slate-100 px-2 py-1 text-slate-600" title={formatValue(change.before)}>{formatValue(change.before)}</span>
                              <ArrowRight className="h-4 w-4 text-slate-400" />
                              <span className="min-w-0 truncate rounded bg-emerald-50 px-2 py-1 font-semibold text-emerald-800" title={formatValue(change.after)}>{formatValue(change.after)}</span>
                            </div>
                            <p className="mt-2 text-xs leading-relaxed text-slate-600">{change.rationale}</p>
                          </div>
                        ))}
                      </div>
                      <div className="rounded-md border border-sky-200 bg-sky-50 p-3 text-xs leading-relaxed text-slate-700">
                        <div className="mb-1 flex items-center gap-2 font-semibold text-sky-900"><Bot className="h-4 w-4" />{text('Qwen 的责任边界', 'Qwen responsibility boundary')}</div>
                        {dashboard.planDelta.qwenContribution.role}
                      </div>
                      <div className="break-all font-mono text-[10px] text-slate-500">{text('合同', 'contract')} {dashboard.planDelta.contentHash}</div>
                    </div>
                  )}
                </div>

                <div className="min-w-0 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                  <SectionHeading icon={BarChart3} title={text('反事实候选竞技场', 'Counterfactual candidate arena')} detail={text('高分候选若违反硬约束仍会被淘汰', 'High-scoring candidates are rejected when hard constraints fail')} />
                  <div className="h-[265px] w-full">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={candidateChart} layout="vertical" margin={{ top: 4, right: 18, left: 20, bottom: 4 }}>
                        <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#e2e8f0" />
                        <XAxis type="number" domain={[0, 0.85]} tick={{ fontSize: 11 }} />
                        <YAxis type="category" dataKey="name" width={88} tick={{ fontSize: 11 }} />
                        <Tooltip formatter={(value: number) => value.toFixed(4)} />
                        <Legend />
                        <Bar dataKey="f1" name="F1" radius={[0, 3, 3, 0]}>
                          {candidateChart.map((item) => (
                            <Cell key={item.name} fill={item.selected ? '#059669' : item.feasible ? '#0284c7' : '#cbd5e1'} />
                          ))}
                        </Bar>
                        <Bar dataKey="ece" name="ECE" fill="#f59e0b" radius={[0, 3, 3, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                  <div className="mt-3 overflow-x-auto">
                    <table className="w-full min-w-[640px] text-left text-xs">
                      <thead className="border-b border-slate-200 text-slate-500">
                        <tr>
                          <th className="pb-2 font-medium">{text('候选', 'Candidate')}</th>
                          <th className="pb-2 font-medium">F1</th>
                          <th className="pb-2 font-medium">ECE</th>
                          <th className="pb-2 font-medium">{text('约束', 'Constraints')}</th>
                          <th className="pb-2 font-medium">{text('结论', 'Decision')}</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {dashboard.planDelta.candidates.map((candidate) => (
                          <tr key={candidate.candidateId} className={candidate.selected ? 'bg-emerald-50/70' : ''}>
                            <td className="py-2 pr-3 font-mono text-slate-800">{candidate.candidateId}</td>
                            <td className="py-2 pr-3">{formatNumber(candidate.metrics['F1-Score'], 4)}</td>
                            <td className="py-2 pr-3">{formatNumber(candidate.metrics['Expected Calibration Error (ECE)'], 4)}</td>
                            <td className="py-2 pr-3">
                              {candidate.feasible
                                ? <span className="text-emerald-700">{text('全部通过', 'All passed')}</span>
                                : <span className="text-red-700">{candidate.failedConstraints.join(', ')}</span>}
                            </td>
                            <td className="py-2">
                              {candidate.selected
                                ? <Badge className="bg-emerald-600">{text('选中', 'Selected')}</Badge>
                                : <Badge variant={candidate.feasible ? 'outline' : 'secondary'}>{candidate.feasible ? text('近优', 'Runner-up') : text('淘汰', 'Rejected')}</Badge>}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </section>

              <section id="reviewx-results" className="scroll-mt-28 grid min-w-0 gap-5 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)]">
                <div className="min-w-0 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                  <SectionHeading icon={FlaskConical} title={text('两轮同口径结果', 'Same-protocol two-round results')} detail={text('冻结反馈集；第二轮只改变合同声明的字段', 'The feedback set is frozen; Round 2 changes only contract-declared fields')} />
                  <div className="h-[250px] w-full">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={feedbackChart} margin={{ top: 10, right: 10, left: 0, bottom: 4 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                        <XAxis dataKey="metric" tick={{ fontSize: 11 }} />
                        <YAxis domain={[0, 1]} tick={{ fontSize: 11 }} />
                        <Tooltip formatter={(value: number) => value.toFixed(4)} />
                        <Legend />
                        <Bar dataKey="roundOne" name={text('第一轮', 'Round 1')} fill="#64748b" radius={[3, 3, 0, 0]} />
                        <Bar dataKey="roundTwo" name={text('第二轮', 'Round 2')} fill="#0f766e" radius={[3, 3, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </div>
                <div className="min-w-0 overflow-hidden rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                  <SectionHeading icon={ShieldCheck} title={text('官方关注项验收', 'Official criteria')} detail={text('结果来自机器可读证据，不由页面自行判断', 'Statuses are derived from machine-readable evidence, not UI assumptions')} />
                  <div className="grid min-w-0 gap-2 sm:grid-cols-2 lg:grid-cols-1">
                    {dashboard.evaluationMatrix.map((item) => (
                      <div key={item.id} className="flex min-w-0 items-start gap-3 overflow-hidden rounded-md border border-slate-200 px-3 py-2.5">
                        {item.status === 'passed'
                          ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
                          : <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-red-600" />}
                        <div className="min-w-0">
                          <div className="text-sm font-medium text-slate-900">{evaluationLabels[item.id]?.[locale] || item.label}</div>
                          <div className="mt-0.5 truncate text-[11px] text-slate-500" title={item.evidence}>{item.evidence}</div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </section>
            </TabsContent>

            <TabsContent value="evidence" className="min-w-0 space-y-5">
              <section id="reviewx-reliability" className="scroll-mt-28 grid min-w-0 gap-5 lg:grid-cols-2">
                <div className="min-w-0 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                  <SectionHeading icon={ShieldCheck} title={text('科研可靠性压力测试', 'Scientific reliability stress test')} detail={`${dashboard.reliability.caseAudit.total} ${text('个配对案例', 'paired cases')} · ${dashboard.reliability.datasets.join(' / ')}`} />
                  <div className="h-[270px] w-full">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={dashboard.reliability.methods} margin={{ top: 10, right: 10, left: 0, bottom: 4 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                        <XAxis dataKey="label" tick={{ fontSize: 11 }} />
                        <YAxis domain={[0, 1]} tickFormatter={(value: number) => `${Math.round(value * 100)}%`} tick={{ fontSize: 11 }} />
                        <Tooltip formatter={(value: number) => formatPercent(value)} />
                        <Legend />
                        <Bar dataKey="faultDetectionRate" name={text('故障检出', 'Fault detection')} fill="#0f766e" radius={[3, 3, 0, 0]} />
                        <Bar dataKey="issueLocalizationRate" name={text('问题定位', 'Issue localization')} fill="#0284c7" radius={[3, 3, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                  <div className="grid grid-cols-2 border-y border-slate-200 py-3 text-xs">
                    <div className="border-r border-slate-200 pr-3">
                      <div className="font-semibold text-slate-900">{text('检出净增益', 'Detection gain')}</div>
                      <div className="mt-1 text-emerald-700">+{dashboard.reliability.pairedEffects.faultDetection.corrected} · p={formatNumber(dashboard.reliability.pairedEffects.faultDetection.exactMcNemarPValue, 4)}</div>
                    </div>
                    <div className="pl-3">
                      <div className="font-semibold text-slate-900">{text('定位净增益', 'Localization gain')}</div>
                      <div className="mt-1 text-emerald-700">+{dashboard.reliability.pairedEffects.issueLocalization.corrected} · p={formatNumber(dashboard.reliability.pairedEffects.issueLocalization.exactMcNemarPValue, 4)}</div>
                    </div>
                  </div>
                  <p className="mt-3 text-xs leading-relaxed text-slate-500">{text('基于真实数据集的配对受控故障；p 值来自双侧精确 McNemar 检验，不用于估计自然科研错误发生率。', `${dashboard.reliability.pairedEffects.scope} ${dashboard.reliability.scope}`)}</p>
                </div>

                <div className="min-w-0 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                  <SectionHeading icon={GitCompareArrows} title={text('同预算规划决策对照', 'Same-budget planning comparison')} detail={`${dashboard.planning.runCount} seeds · ${dashboard.planning.seeds.length * 6} decisions`} />
                  <div className="h-[270px] w-full">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={dashboard.planning.methods} margin={{ top: 10, right: 10, left: 0, bottom: 4 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                        <XAxis dataKey="label" tick={{ fontSize: 11 }} />
                        <YAxis domain={[0, 1]} tickFormatter={(value: number) => `${Math.round(value * 100)}%`} tick={{ fontSize: 11 }} />
                        <Tooltip formatter={(value: number) => formatPercent(value)} />
                        <Legend />
                        <Bar dataKey="policyAgreementRate" name={text('预注册策略一致', 'Preregistered policy agreement')} fill="#7c3aed" radius={[3, 3, 0, 0]} />
                        <Bar dataKey="constraintSatisfactionRate" name={text('硬约束满足', 'Hard-constraint satisfaction')} fill="#d97706" radius={[3, 3, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-2 text-xs text-slate-600">
                    <Badge variant="outline">{dashboard.planning.qwenCost.totalTokens || 0} tokens</Badge>
                    <Badge variant="outline">{text('均值', 'mean')} {((dashboard.planning.qwenCost.meanLatencyMs || 0) / 1000).toFixed(2)} s</Badge>
                    <Badge variant="outline">{text('估算', 'estimated')} ¥{formatNumber(dashboard.planning.qwenCost.estimatedCostCny, 5)}</Badge>
                  </div>
                  <p className="mt-3 text-xs leading-relaxed text-slate-500">{text('每个 seed 包含 6 个来自真实 SciFact 候选竞技场的受控决策场景。', dashboard.planning.scope)}</p>
                </div>
              </section>

              {dashboard.multidomain.effects.length > 0 && (
                <section className="min-w-0 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                  <SectionHeading
                    icon={Gauge}
                    title={text('跨域保守门控', 'Conservative cross-domain gate')}
                    detail={text('验证集聚类区间不越过 0 才允许修改进入测试集', 'A revision reaches the test set only when its validation cluster interval clears zero')}
                  />
                  <div className="divide-y divide-slate-200 md:hidden">
                    {dashboard.multidomain.effects.map((effect) => (
                      <div key={effect.dataset} className="py-4 first:pt-1 last:pb-1">
                        <div className="flex items-center justify-between gap-3">
                          <div className="font-semibold text-slate-900">{effect.dataset}</div>
                          <Badge variant={effect.gateDecision === 'keep_round_one' ? 'outline' : 'secondary'}>
                            {effect.gateDecision === 'keep_round_one' ? text('保持第一轮', 'Keep round 1') : text('应用修订', 'Apply revision')}
                          </Badge>
                        </div>
                        <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3 text-xs">
                          <div>
                            <dt className="text-slate-500">Macro-F1</dt>
                            <dd className="mt-0.5 font-mono text-slate-900">{formatNumber(effect.roundOneMacroF1, 4)} → {formatNumber(effect.roundTwoMacroF1, 4)}</dd>
                          </div>
                          <div>
                            <dt className="text-slate-500">Δ Macro-F1</dt>
                            <dd className={`mt-0.5 font-semibold ${effect.delta > 0 ? 'text-emerald-700' : 'text-slate-600'}`}>{formatSignedPercent(effect.delta, 1)}</dd>
                          </div>
                          <div>
                            <dt className="text-slate-500">Δ Bal. Acc.</dt>
                            <dd className="mt-0.5 font-semibold text-emerald-700">{formatSignedPercent(numericDelta(effect.roundTwoBalancedAccuracy, effect.roundOneBalancedAccuracy), 1)}</dd>
                          </div>
                          <div>
                            <dt className="text-slate-500">Δ Accuracy</dt>
                            <dd className={`mt-0.5 font-semibold ${(numericDelta(effect.roundTwoAccuracy, effect.roundOneAccuracy) ?? 0) < 0 ? 'text-amber-700' : 'text-slate-600'}`}>{formatSignedPercent(numericDelta(effect.roundTwoAccuracy, effect.roundOneAccuracy), 1)}</dd>
                          </div>
                          <div className="col-span-2">
                            <dt className="text-slate-500">claim_id cluster 95% CI</dt>
                            <dd className="mt-0.5 font-mono text-slate-900">[{formatSignedPercent(effect.ci95[0], 1)}, {formatSignedPercent(effect.ci95[1], 1)}]</dd>
                          </div>
                        </dl>
                        <div className="mt-3 text-xs font-medium">
                          {effect.effectStatus === 'significant_improvement'
                            ? <span className="inline-flex items-center gap-1 text-emerald-700"><CheckCircle2 className="h-4 w-4" />{text('显著提升', 'Significant gain')}</span>
                            : <span className="inline-flex items-center gap-1 text-slate-600"><ShieldCheck className="h-4 w-4" />{text('未部署不确定修改', 'Uncertain revision withheld')}</span>}
                        </div>
                      </div>
                    ))}
                  </div>
                  <div className="hidden overflow-x-auto md:block">
                    <table className="w-full min-w-[980px] text-left text-sm">
                      <thead className="border-b border-slate-200 text-xs text-slate-500">
                        <tr>
                          <th className="pb-2 font-medium">{text('数据集', 'Dataset')}</th>
                          <th className="pb-2 font-medium">Gate</th>
                          <th className="pb-2 font-medium">{text('第一轮', 'Round 1')}</th>
                          <th className="pb-2 font-medium">{text('第二轮', 'Round 2')}</th>
                          <th className="pb-2 font-medium">Δ Macro-F1</th>
                          <th className="pb-2 font-medium">Δ Bal. Acc.</th>
                          <th className="pb-2 font-medium">Δ Accuracy</th>
                          <th className="pb-2 font-medium">95% CI</th>
                          <th className="pb-2 font-medium">{text('结论', 'Conclusion')}</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {dashboard.multidomain.effects.map((effect) => (
                          <tr key={effect.dataset}>
                            <td className="py-3 font-medium text-slate-900">{effect.dataset}</td>
                            <td className="py-3">
                              <Badge variant={effect.gateDecision === 'keep_round_one' ? 'outline' : 'secondary'}>
                                {effect.gateDecision === 'keep_round_one' ? text('保持第一轮', 'Keep round 1') : text('应用修订', 'Apply revision')}
                              </Badge>
                            </td>
                            <td className="py-3 font-mono text-xs">{formatNumber(effect.roundOneMacroF1, 4)}</td>
                            <td className="py-3 font-mono text-xs">{formatNumber(effect.roundTwoMacroF1, 4)}</td>
                            <td className={`py-3 font-semibold ${effect.delta > 0 ? 'text-emerald-700' : 'text-slate-600'}`}>{formatSignedPercent(effect.delta, 1)}</td>
                            <td className="py-3 font-medium text-emerald-700">{formatSignedPercent(numericDelta(effect.roundTwoBalancedAccuracy, effect.roundOneBalancedAccuracy), 1)}</td>
                            <td className={`py-3 font-medium ${(numericDelta(effect.roundTwoAccuracy, effect.roundOneAccuracy) ?? 0) < 0 ? 'text-amber-700' : 'text-slate-600'}`}>{formatSignedPercent(numericDelta(effect.roundTwoAccuracy, effect.roundOneAccuracy), 1)}</td>
                            <td className="py-3 font-mono text-xs">[{formatSignedPercent(effect.ci95[0], 1)}, {formatSignedPercent(effect.ci95[1], 1)}]</td>
                            <td className="py-3">
                              {effect.effectStatus === 'significant_improvement'
                                ? <span className="inline-flex items-center gap-1 text-emerald-700"><CheckCircle2 className="h-4 w-4" />{text('显著提升', 'Significant gain')}</span>
                                : <span className="inline-flex items-center gap-1 text-slate-600"><ShieldCheck className="h-4 w-4" />{text('未部署不确定修改', 'Uncertain revision withheld')}</span>}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="mt-3 text-xs leading-relaxed text-slate-500">{text('Macro-F1 是预设主指标，用于避免类别不均衡掩盖少数类；因此原始 Accuracy 可能与类别平衡指标反向变化。所有区间按 claim_id 聚类重采样；PubHealth 验证证据不足时自动保持第一轮，测试标签不参与 Gate。', `Macro-F1 is the prespecified primary metric so class imbalance cannot hide minority-class behavior; raw accuracy may therefore move against class-balanced metrics. ${dashboard.multidomain.scope}`)}</p>
                </section>
              )}

              {dashboard.externalReview.available && peerqaEvidence && dashboard.externalReview.fairTop5 && (
                <section className="min-w-0 scroll-mt-28 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <SectionHeading
                      icon={BookOpenCheck}
                      title={text('真实论文外部审稿验证', 'External review on real papers')}
                      detail={`${dashboard.externalReview.paperCount} ${text('篇未参与开发的 PeerQA 论文', 'held-out PeerQA papers')} · ${dashboard.externalReview.questionCount} ${text('个专家问题', 'expert questions')} · ${dashboard.externalReview.sourceCount} ${text('个来源', 'sources')}`}
                    />
                    <div className="flex shrink-0 gap-2">
                      {dashboard.externalReview.reportUrl && (
                        <a
                          href={`${API_BASE_URL}${dashboard.externalReview.reportUrl}`}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex h-9 items-center gap-2 rounded-md border border-slate-200 px-3 text-xs font-medium text-slate-700 hover:bg-slate-50"
                        >
                          <Download className="h-4 w-4" />Top-5 {text('报告', 'report')}
                        </a>
                      )}
                      {dashboard.externalReview.fullAuditReportUrl && (
                        <a
                          href={`${API_BASE_URL}${dashboard.externalReview.fullAuditReportUrl}`}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex h-9 items-center gap-2 rounded-md border border-slate-200 px-3 text-xs font-medium text-slate-700 hover:bg-slate-50"
                        >
                          <Download className="h-4 w-4" />{text('完整审计', 'Full audit')}
                        </a>
                      )}
                    </div>
                  </div>

                  <div className="mb-1 flex flex-wrap gap-2 text-xs text-slate-600">
                    <Badge variant="outline">{dashboard.externalReview.model || 'model unavailable'}</Badge>
                    <Badge variant="outline">{dashboard.externalReview.repetitions || 0} {text('次重复', 'repetitions')}</Badge>
                    <Badge variant={dashboard.externalReview.protocolVerified ? 'secondary' : 'destructive'}>
                      {text('冻结协议', 'Frozen protocol')} {dashboard.externalReview.protocolVerified ? text('已验证', 'verified') : text('失败', 'failed')}
                    </Badge>
                  </div>

                  <div className="grid min-w-0 gap-5 xl:grid-cols-[minmax(0,1.35fr)_minmax(280px,0.65fr)]">
                    <div className="min-w-0">
                      <div className="h-[250px] w-full">
                        <ResponsiveContainer width="100%" height="100%">
                          <BarChart data={peerqaEvidence.chart} margin={{ top: 10, right: 12, left: 0, bottom: 4 }}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                            <XAxis dataKey="chartLabel" tick={{ fontSize: 11 }} />
                            <YAxis domain={[0, 0.7]} tickFormatter={(value: number) => `${Math.round(value * 100)}%`} tick={{ fontSize: 11 }} />
                            <Tooltip formatter={(value: number) => formatPercent(value)} />
                            <Bar dataKey="candidateRate" name={text('冻结 Top-5 对齐代理', 'Frozen Top-5 alignment proxy')} radius={[3, 3, 0, 0]}>
                              {peerqaEvidence.chart.map((item) => (
                                <Cell
                                  key={item.methodId}
                                  fill={item.methodId.startsWith('reviewx_') ? '#059669' : item.methodId.includes('rubric') ? '#d97706' : '#0284c7'}
                                />
                              ))}
                            </Bar>
                          </BarChart>
                        </ResponsiveContainer>
                      </div>
                    </div>

                    <div className="min-w-0 border-t border-slate-200 pt-4 xl:border-l xl:border-t-0 xl:pl-5 xl:pt-0">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-xs font-semibold uppercase text-slate-500">{text('公平 Top-5', 'Fair Top-5')}</span>
                        <Badge variant={dashboard.externalReview.fairTop5.qualityGate === 'passed' ? 'secondary' : 'destructive'}>
                          Gate {dashboard.externalReview.fairTop5.qualityGate}
                        </Badge>
                      </div>
                      <div className="mt-2 text-3xl font-semibold text-slate-950">
                        {formatPercent(peerqaEvidence.reviewx?.candidateRate)}
                      </div>
                      <div className="mt-1 text-xs text-slate-500">
                        {text('论文聚类', 'Paper-cluster')} 95% CI {peerqaEvidence.reviewx
                          ? `[${formatPercent(peerqaEvidence.reviewx.candidateRatePaperClusterBootstrap95[0])}, ${formatPercent(peerqaEvidence.reviewx.candidateRatePaperClusterBootstrap95[1])}]`
                          : '--'}
                      </div>
                      <div className="mt-4 divide-y divide-slate-100 border-y border-slate-200 text-sm">
                        <div className="flex items-center justify-between gap-3 py-2">
                          <span className="text-slate-600">{text('相对单提示', 'vs single prompt')}</span>
                          <span className="font-semibold text-emerald-700">{formatSignedPercent(peerqaEvidence.singleEffect?.candidateRateDelta)}</span>
                        </div>
                        <div className="flex items-center justify-between gap-3 py-2">
                          <span className="text-slate-600">{text('相对 rubric', 'vs rubric')}</span>
                          <span className="font-semibold text-emerald-700">{formatSignedPercent(peerqaEvidence.rubricEffect?.candidateRateDelta)}</span>
                        </div>
                        <div className="flex items-center justify-between gap-3 py-2">
                          <span className="text-slate-600">LLM {text('调用率', 'call rate')}</span>
                          <span className="font-semibold text-slate-900">{formatPercent(peerqaEvidence.reviewx?.llmEscalationRate)}</span>
                        </div>
                        <div className="flex items-center justify-between gap-3 py-2">
                          <span className="text-slate-600">{text('相对单提示延迟', 'latency vs single prompt')}</span>
                          <span className="font-semibold text-emerald-700">{formatNumber((peerqaEvidence.singleEffect?.meanLatencyDeltaMs || 0) / 1000, 2)} s</span>
                        </div>
                      </div>
                      <p className="mt-3 text-xs leading-relaxed text-amber-800">
                        {text(
                          `配对 McNemar：单提示 p=${formatNumber(peerqaEvidence.singleEffect?.exactMcNemarPValue, 3)}；rubric p=${formatNumber(peerqaEvidence.rubricEffect?.exactMcNemarPValue, 3)}。当前为正向趋势，未达显著。`,
                          `Paired McNemar: single prompt p=${formatNumber(peerqaEvidence.singleEffect?.exactMcNemarPValue, 3)}; rubric p=${formatNumber(peerqaEvidence.rubricEffect?.exactMcNemarPValue, 3)}. The current trend is positive but not statistically significant.`,
                        )}
                      </p>
                      <div className="mt-4 border-l-2 border-amber-400 pl-3">
                        <div className="text-xs font-semibold text-amber-900">{text('完整审计上限 · 输出数量不等', 'Full-audit ceiling · unequal output counts')}</div>
                        <div className="mt-1 text-xl font-semibold text-slate-950">
                          {formatPercent(peerqaEvidence.fullReviewx?.candidateRate)}
                        </div>
                        <div className="mt-1 text-xs text-slate-500">
                          {text('相对单提示', 'vs single prompt')} {peerqaEvidence.fullSingleEffect
                            ? formatSignedPercent(peerqaEvidence.fullSingleEffect.candidateRateDelta)
                            : '--'}{text('；不作为公平优越性结论。', '; not used as a fair superiority claim.')}
                        </div>
                      </div>
                    </div>
                  </div>

                  <div className="mt-4 overflow-x-auto border-t border-slate-200 pt-4">
                    <table className="w-full min-w-[760px] text-left text-xs">
                      <thead className="border-b border-slate-200 text-slate-500">
                        <tr>
                          <th className="pb-2 font-medium">{text('方法', 'Method')}</th>
                          <th className="pb-2 font-medium">Top-5 {text('代理', 'proxy')}</th>
                          <th className="pb-2 font-medium">95% CI</th>
                          <th className="pb-2 font-medium">LLM {text('调用', 'calls')}</th>
                          <th className="pb-2 font-medium">tokens/run</th>
                          <th className="pb-2 font-medium">latency/run</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {dashboard.externalReview.fairTop5.methods.map((method) => (
                          <tr key={method.methodId} className={method.methodId.startsWith('reviewx_') ? 'bg-emerald-50/60' : ''}>
                            <td className="py-2.5 pr-4 font-medium text-slate-900">{method.label}</td>
                            <td className="py-2.5 pr-4">{formatPercent(method.candidateRate)}</td>
                            <td className="py-2.5 pr-4 text-slate-600">
                              [{formatPercent(method.candidateRatePaperClusterBootstrap95[0])}, {formatPercent(method.candidateRatePaperClusterBootstrap95[1])}]
                            </td>
                            <td className="py-2.5 pr-4">{formatPercent(method.llmEscalationRate)}</td>
                            <td className="py-2.5 pr-4">{formatNumber(method.meanTotalTokens, 0)}</td>
                            <td className="py-2.5">{formatNumber(method.meanLatencyMs / 1000, 2)} s</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  <div className="mt-4 flex flex-col gap-2 border-t border-slate-200 pt-3 text-xs text-slate-500 sm:flex-row sm:items-center sm:justify-between">
                    <span>{dashboard.externalReview.scope}</span>
                    <span className="shrink-0 font-mono" title={dashboard.externalReview.protocolHash}>
                      protocol {shortHash(dashboard.externalReview.protocolHash)}
                    </span>
                  </div>
                </section>
              )}

              <section id="reviewx-artifacts" className="scroll-mt-28 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                <SectionHeading icon={FileCheck2} title={text('可下载证据清单', 'Downloadable evidence manifest')} detail={text('公开白名单文件逐项计算 SHA-256；不暴露密钥和原始受限数据', 'Each allowlisted public file has a SHA-256 digest; secrets and restricted raw data are excluded')} />
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[780px] text-left text-xs">
                    <thead className="border-b border-slate-200 text-slate-500">
                      <tr>
                        <th className="pb-2 font-medium">{text('文件', 'File')}</th>
                        <th className="pb-2 font-medium">{text('来源', 'Authority')}</th>
                        <th className="pb-2 font-medium">{text('大小', 'Size')}</th>
                        <th className="pb-2 font-medium">SHA-256</th>
                        <th className="pb-2 text-right font-medium">{text('下载', 'Download')}</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {dashboard.evidenceManifest.map((artifact) => (
                        <tr key={artifact.filename}>
                          <td className="py-2.5 pr-4 font-mono text-slate-800">{artifact.filename}</td>
                          <td className="py-2.5 pr-4 text-slate-600">{artifact.authority}</td>
                          <td className="py-2.5 pr-4 text-slate-600">{(artifact.sizeBytes / 1024).toFixed(1)} KiB</td>
                          <td className="py-2.5 pr-4 font-mono text-[10px] text-slate-500" title={artifact.sha256}>{shortHash(artifact.sha256)}</td>
                          <td className="py-2.5 text-right">
                            <a href={`${API_BASE_URL}${artifact.url}`} target="_blank" rel="noreferrer" title={`${text('下载', 'Download')} ${artifact.filename}`} className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-slate-200 text-slate-600 hover:bg-slate-50 hover:text-sky-700">
                              <Download className="h-4 w-4" />
                            </a>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            </TabsContent>

            <TabsContent value="governance" className="min-w-0 space-y-5">
              <section id="reviewx-governance" className="scroll-mt-28 grid min-w-0 gap-5 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)]">
                <div className="min-w-0 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                  <SectionHeading icon={UserCheck} title={text('单负责人分阶段签核', 'Single-reviewer staged signoff')} detail={text('一名负责人可完成全部必需签核；每次批准与当前证据哈希绑定', 'One accountable reviewer can complete all required gates; each approval is hash-bound')} />
                  <div className="grid gap-3 sm:grid-cols-3">
                    {(['plan', 'repair', 'conclusion'] as const).map((stage, index) => {
                      const status = dashboard.humanGovernance.signoffs[stage]
                      const label = isChinese
                        ? ['方案签核', '风险修复签核', '结论签核'][index]
                        : ['Plan signoff', 'Repair signoff', 'Conclusion signoff'][index]
                      return (
                        <div key={stage} className="rounded-md border border-slate-200 p-4">
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-sm font-semibold text-slate-900">{label}</span>
                            {status === 'approved'
                              ? <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                              : <Clock3 className="h-4 w-4 text-amber-600" />}
                          </div>
                          <div className="mt-3 text-lg font-semibold capitalize text-slate-950">{status === 'approved' ? text('已批准', 'Approved') : text('待签核', 'Pending')}</div>
                          <div className="mt-1 text-xs text-slate-500">Stage {index + 1} / 3</div>
                        </div>
                      )
                    })}
                  </div>
                  <div className="mt-4 grid gap-3 sm:grid-cols-2">
                    <div className="rounded-md border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700">
                      <div className="text-xs font-semibold uppercase text-slate-500">{text('责任模式', 'Accountability model')}</div>
                      <div className="mt-1 font-medium">
                        {dashboard.humanGovernance.reviewerSeparationRequired
                          ? text('多人分离审核', 'Separated reviewers')
                          : text(`${dashboard.humanGovernance.responsibleReviewerCount} 名负责人`, `${dashboard.humanGovernance.responsibleReviewerCount} accountable reviewer`)}
                      </div>
                    </div>
                    <div className="rounded-md border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700">
                      <div className="text-xs font-semibold uppercase text-slate-500">{text('正式证据包', 'Official evidence bundle')}</div>
                      <div className="mt-1 font-medium">{dashboard.humanGovernance.publicationReady ? text('允许发布', 'Release allowed') : text('服务端阻断', 'Blocked by server')}</div>
                    </div>
                  </div>
                  {dashboard.humanGovernance.feedbackId && (
                    <div className="mt-4 flex flex-wrap items-center gap-3">
                      <Link to={evidenceAuditPath} className="inline-flex items-center gap-2 text-sm font-medium text-sky-700 hover:underline">
                        {text('打开签核工作区', 'Open signoff workspace')} <ExternalLink className="h-4 w-4" />
                      </Link>
                      <span className="font-mono text-[11px] text-slate-500">{dashboard.humanGovernance.feedbackId}</span>
                    </div>
                  )}
                </div>

                <div className="min-w-0 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                  <SectionHeading icon={AlertTriangle} title={text('诚实边界', 'Evidence boundaries')} detail={text('有效闭环不等于已经完成真实科学或仪器部署', 'A valid loop does not imply wet-lab or instrument deployment')} />
                  <div className="space-y-3">
                    {dashboard.limitations.map((limitation, index) => (
                      <div key={limitation} className="flex items-start gap-3 rounded-md border border-amber-200 bg-amber-50/70 p-3">
                        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-amber-200 text-[11px] font-bold text-amber-900">{index + 1}</span>
                        <p className="text-sm leading-relaxed text-slate-700">{isChinese ? limitationTranslations[limitation] || limitation : limitation}</p>
                      </div>
                    ))}
                  </div>
                </div>
              </section>

              <section id="reviewx-release" className="scroll-mt-28 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                <SectionHeading icon={Fingerprint} title={text('发布状态解释', 'Release status')} detail={text('自动化验证和真实人员责任明确分离', 'Automated verification and human accountability are explicitly separated')} />
                <div className="grid gap-4 lg:grid-cols-3">
                  <div className="border-l-4 border-emerald-500 pl-4">
                    <div className="text-sm font-semibold text-slate-900">{text('技术证据', 'Technical evidence')}</div>
                    <p className="mt-1 text-sm leading-relaxed text-slate-600">{text('合同、时间线、Qwen 轨迹、两轮结果和受控压力测试均可机器核验。', 'Contracts, timelines, Qwen traces, two-round results, and controlled stress tests are machine-verifiable.')}</p>
                  </div>
                  <div className="border-l-4 border-amber-500 pl-4">
                    <div className="text-sm font-semibold text-slate-900">{text('科学结论', 'Scientific conclusion')}</div>
                    <p className="mt-1 text-sm leading-relaxed text-slate-600">
                      {dashboard.holdoutEffect.effectStatus === 'significant_improvement'
                        ? text('SciFact 最终未见集支持显著 F1 提升；结论仍受数据集、任务和预注册协议边界约束。', 'The SciFact final holdout supports a significant F1 improvement, bounded by the dataset, task, and preregistered protocol.')
                        : dashboard.holdoutEffect.effectStatus === 'significant_regression'
                          ? text('SciFact 最终未见集检测到显著 F1 回归，当前修订不得发布。', 'The SciFact final holdout detected a significant F1 regression, so the revision must not be released.')
                          : text('SciFact 最终未见集区间跨 0，因此不宣称显著提升或非劣效；显著性证据来自预先门控的跨域测试与机制压力测试。', 'The SciFact final-holdout interval crosses zero, so neither significant improvement nor non-inferiority is claimed; significance evidence comes from the pre-gated cross-domain and mechanism tests.')}
                    </p>
                  </div>
                  <div className="border-l-4 border-violet-500 pl-4">
                    <div className="text-sm font-semibold text-slate-900">{text('人工责任', 'Human accountability')}</div>
                    <p className="mt-1 text-sm leading-relaxed text-slate-600">{text('待签核表示尚无真实人工批准，自动执行不会推定人工同意。', dashboard.humanGovernance.note)}</p>
                  </div>
                </div>
              </section>
            </TabsContent>
          </Tabs>

          <div className="flex flex-col gap-2 border-t border-slate-200 pt-4 text-xs text-slate-500 sm:flex-row sm:items-center sm:justify-between">
            <span>{text('证据快照', 'Evidence snapshot')} · {new Date(dashboard.generatedAt).toLocaleString(locale)}</span>
            <span className="font-mono">benchmark {shortHash(dashboard.case.benchmarkFingerprint)}</span>
          </div>
        </div>
      )}
    </AppPageLayout>
  )
}
