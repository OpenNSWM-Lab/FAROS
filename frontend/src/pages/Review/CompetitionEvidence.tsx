import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  BarChart3,
  Bot,
  Check,
  CheckCircle2,
  Clock3,
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
    scope: string
  }
  humanGovernance: {
    feedbackId?: string
    signoffs: Record<'plan' | 'repair' | 'conclusion', string>
    publicationReady: boolean
    reviewerSeparationRequired: boolean
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
  const [tab, setTab] = useState<DashboardTab>('overview')
  const [dashboard, setDashboard] = useState<DashboardPayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [freshJob, setFreshJob] = useState<CompetitionJob | null>(null)
  const [startingFresh, setStartingFresh] = useState(false)

  const loadDashboard = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/reviews/reviewx/competition/dashboard`)
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) {
        const detail = typeof payload.detail === 'string' ? payload.detail : '比赛证据接口暂不可用。'
        throw new Error(detail)
      }
      setDashboard(payload as DashboardPayload)
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : '比赛证据加载失败。')
    } finally {
      setLoading(false)
    }
  }, [])

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
        const detail = typeof payload.detail === 'string' ? payload.detail : '无法启动新的 Qwen 案例。'
        throw new Error(detail)
      }
      setFreshJob(payload as CompetitionJob)
    } catch (runError) {
      setError(runError instanceof Error ? runError.message : '新的 Qwen 案例启动失败。')
    } finally {
      setStartingFresh(false)
    }
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

  const headline = useMemo(() => {
    if (!dashboard) return null
    const feedbackF1 = dashboard.feedbackMetrics.find((item) => item.name === 'F1-Score')
    const feedbackRecall = dashboard.feedbackMetrics.find((item) => item.name === 'Recall')
    const holdoutF1 = dashboard.holdoutMetrics.find((item) => item.name === 'F1-Score')
    const full = dashboard.reliability.methods.find((item) => item.methodId === 'faros_full')
    return { feedbackF1, feedbackRecall, holdoutF1, full }
  }, [dashboard])

  const pageActions = (
    <div className="flex flex-wrap items-center gap-2">
      <Button variant="outline" size="sm" onClick={() => void loadDashboard()} disabled={loading} title="刷新证据">
        <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
        <span className="ml-2 hidden sm:inline">刷新</span>
      </Button>
      <Button size="sm" onClick={() => void runFreshCase()} disabled={startingFresh || freshJob?.status === 'running'}>
        {startingFresh || freshJob?.status === 'running' ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <PlayCircle className="h-4 w-4" />
        )}
        <span className="ml-2">真实 Qwen 复验</span>
      </Button>
    </div>
  )

  return (
    <AppPageLayout
      title="ReviewX · 方向 1B 证据驾驶舱"
      subtitle="FAROS 科学实验任务规划与反馈迭代的可核验结果"
      icon={Gauge}
      iconColor="blue"
      accentColor="blue"
      actions={pageActions}
      breadcrumb={<Link to="/review/consistency" className="text-sm text-sky-700 hover:underline">进入论文证据审计</Link>}
    >
      {error && (
        <div className="mb-5 flex items-start gap-3 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {freshJob && freshJob.status !== 'completed' && (
        <div className="mb-5 flex items-center justify-between gap-3 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <span className="flex min-w-0 items-center gap-2">
            {freshJob.status === 'failed' ? <X className="h-4 w-4" /> : <Loader2 className="h-4 w-4 animate-spin" />}
            <span className="truncate">新案例 {freshJob.jobId} · {freshJob.status}</span>
          </span>
          {freshJob.error && <span className="truncate text-xs">{freshJob.error}</span>}
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
        <EmptyState message="尚无可核验的方向 1B 代表案例。" />
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
                  {dashboard.case.scientificQuestion}
                </h1>
                <p className="mt-2 max-w-4xl text-sm leading-relaxed text-slate-300">
                  {dashboard.track.officialFocus}
                </p>
              </div>
              <div className="flex flex-col items-start gap-2 lg:items-end">
                <Badge className={dashboard.status.technicalReady
                  ? 'border-emerald-400 bg-emerald-400 text-emerald-950'
                  : 'border-amber-300 bg-amber-300 text-amber-950'}>
                  {dashboard.status.technicalReady ? <Check className="mr-1 h-3.5 w-3.5" /> : <AlertTriangle className="mr-1 h-3.5 w-3.5" />}
                  {dashboard.status.label}
                </Badge>
                <div className="font-mono text-[11px] text-slate-400">{dashboard.case.runId}</div>
              </div>
            </div>
            <div className="grid grid-cols-2 divide-x divide-y divide-slate-200 sm:grid-cols-4 sm:divide-y-0">
              <div className="p-4">
                <div className="text-xs font-medium text-slate-500">反馈集 F1</div>
                <div className="mt-1 text-2xl font-semibold text-slate-950">+{formatNumber(headline.feedbackF1?.delta, 4)}</div>
                <div className="mt-1 text-xs text-emerald-700">{formatNumber(headline.feedbackF1?.roundOne, 4)} → {formatNumber(headline.feedbackF1?.roundTwo, 4)}</div>
              </div>
              <div className="p-4">
                <div className="text-xs font-medium text-slate-500">反馈集 Recall</div>
                <div className="mt-1 text-2xl font-semibold text-slate-950">+{formatNumber(headline.feedbackRecall?.delta, 4)}</div>
                <div className="mt-1 text-xs text-emerald-700">{formatPercent(headline.feedbackRecall?.roundOne)} → {formatPercent(headline.feedbackRecall?.roundTwo)}</div>
              </div>
              <div className="p-4">
                <div className="text-xs font-medium text-slate-500">未见集 F1</div>
                <div className="mt-1 text-2xl font-semibold text-slate-950">+{formatNumber(headline.holdoutF1?.delta, 4)}</div>
                <div className="mt-1 text-xs text-amber-700">非退化；95% CI 跨 0</div>
              </div>
              <div className="p-4">
                <div className="text-xs font-medium text-slate-500">受控故障检出</div>
                <div className="mt-1 text-2xl font-semibold text-slate-950">{formatPercent(headline.full?.faultDetectionRate, 0)}</div>
                <div className="mt-1 text-xs text-sky-700">45/45，正常误拒 0/45</div>
              </div>
            </div>
          </section>

          <div className="flex flex-col gap-3 rounded-md border border-slate-200 bg-white px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex flex-wrap gap-2">
              {dashboard.provenanceLegend.map((item) => (
                <span key={item.id} className={`inline-flex items-center gap-2 rounded-md border px-2.5 py-1 text-xs font-medium ${authorityTone[item.id]}`} title={item.description}>
                  <span className={`h-2 w-2 rounded-full ${authorityDot[item.id]}`} />
                  {item.label}
                </span>
              ))}
            </div>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
              <span className="inline-flex items-center gap-1"><Bot className="h-3.5 w-3.5" />{dashboard.qwen.model}</span>
              <span className="inline-flex items-center gap-1"><Clock3 className="h-3.5 w-3.5" />{(dashboard.qwen.latencyMs / 1000).toFixed(2)} s</span>
              <span className="inline-flex items-center gap-1"><Fingerprint className="h-3.5 w-3.5" />{shortHash(dashboard.qwen.promptHash)}</span>
            </div>
          </div>

          <Tabs className="min-w-0" value={tab} onValueChange={(value) => setTab(value as DashboardTab)}>
            <TabsList className="grid h-auto w-full grid-cols-3 bg-slate-200/70 p-1 sm:w-[520px]">
              <TabsTrigger value="overview">闭环与计划变化</TabsTrigger>
              <TabsTrigger value="evidence">对照与证据</TabsTrigger>
              <TabsTrigger value="governance">人工治理与边界</TabsTrigger>
            </TabsList>

            <TabsContent value="overview" className="min-w-0 space-y-5">
              <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                <SectionHeading icon={Activity} title="可核验闭环" detail="每个节点都绑定来源，模型输出与真实观测不混写" />
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
                          <div className="mt-3 text-sm font-semibold text-slate-950">{stage.label}</div>
                          <p className="mt-2 text-xs leading-relaxed text-slate-700">{stage.detail}</p>
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

              <section className="grid min-w-0 gap-5 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.4fr)]">
                <div className="min-w-0 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                  <SectionHeading icon={GitCompareArrows} title="证据到计划变化合同" detail="变更发生在最终未见集加载之前" />
                  {!dashboard.planDelta.available ? (
                    <EmptyState message="当前运行早于计划变化合同，需重新运行真实 Qwen 案例。" />
                  ) : (
                    <div className="space-y-4">
                      <div className="rounded-md border border-amber-200 bg-amber-50 p-3">
                        <div className="text-xs font-semibold uppercase text-amber-900">触发证据</div>
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
                        <div className="mb-1 flex items-center gap-2 font-semibold text-sky-900"><Bot className="h-4 w-4" />Qwen 的责任边界</div>
                        {dashboard.planDelta.qwenContribution.role}
                      </div>
                      <div className="break-all font-mono text-[10px] text-slate-500">合同 {dashboard.planDelta.contentHash}</div>
                    </div>
                  )}
                </div>

                <div className="min-w-0 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                  <SectionHeading icon={BarChart3} title="反事实候选竞技场" detail="高分候选若违反硬约束仍会被淘汰" />
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
                          <th className="pb-2 font-medium">候选</th>
                          <th className="pb-2 font-medium">F1</th>
                          <th className="pb-2 font-medium">ECE</th>
                          <th className="pb-2 font-medium">约束</th>
                          <th className="pb-2 font-medium">结论</th>
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
                                ? <span className="text-emerald-700">全部通过</span>
                                : <span className="text-red-700">{candidate.failedConstraints.join(', ')}</span>}
                            </td>
                            <td className="py-2">
                              {candidate.selected
                                ? <Badge className="bg-emerald-600">选中</Badge>
                                : <Badge variant={candidate.feasible ? 'outline' : 'secondary'}>{candidate.feasible ? '近优' : '淘汰'}</Badge>}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </section>

              <section className="grid min-w-0 gap-5 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)]">
                <div className="min-w-0 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                  <SectionHeading icon={FlaskConical} title="两轮同口径结果" detail="冻结反馈集；第二轮只改变合同声明的字段" />
                  <div className="h-[250px] w-full">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={feedbackChart} margin={{ top: 10, right: 10, left: 0, bottom: 4 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                        <XAxis dataKey="metric" tick={{ fontSize: 11 }} />
                        <YAxis domain={[0, 1]} tick={{ fontSize: 11 }} />
                        <Tooltip formatter={(value: number) => value.toFixed(4)} />
                        <Legend />
                        <Bar dataKey="roundOne" name="第一轮" fill="#64748b" radius={[3, 3, 0, 0]} />
                        <Bar dataKey="roundTwo" name="第二轮" fill="#0f766e" radius={[3, 3, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </div>
                <div className="min-w-0 overflow-hidden rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                  <SectionHeading icon={ShieldCheck} title="官方关注项验收" detail="结果来自机器可读证据，不由页面自行判断" />
                  <div className="grid min-w-0 gap-2 sm:grid-cols-2 lg:grid-cols-1">
                    {dashboard.evaluationMatrix.map((item) => (
                      <div key={item.id} className="flex min-w-0 items-start gap-3 overflow-hidden rounded-md border border-slate-200 px-3 py-2.5">
                        {item.status === 'passed'
                          ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
                          : <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-red-600" />}
                        <div className="min-w-0">
                          <div className="text-sm font-medium text-slate-900">{item.label}</div>
                          <div className="mt-0.5 truncate text-[11px] text-slate-500" title={item.evidence}>{item.evidence}</div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </section>
            </TabsContent>

            <TabsContent value="evidence" className="min-w-0 space-y-5">
              <section className="grid min-w-0 gap-5 lg:grid-cols-2">
                <div className="min-w-0 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                  <SectionHeading icon={ShieldCheck} title="科研可靠性压力测试" detail={`${dashboard.reliability.caseAudit.total} 个配对案例 · ${dashboard.reliability.datasets.join(' / ')}`} />
                  <div className="h-[270px] w-full">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={dashboard.reliability.methods} margin={{ top: 10, right: 10, left: 0, bottom: 4 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                        <XAxis dataKey="label" tick={{ fontSize: 11 }} />
                        <YAxis domain={[0, 1]} tickFormatter={(value: number) => `${Math.round(value * 100)}%`} tick={{ fontSize: 11 }} />
                        <Tooltip formatter={(value: number) => formatPercent(value)} />
                        <Legend />
                        <Bar dataKey="faultDetectionRate" name="故障检出" fill="#0f766e" radius={[3, 3, 0, 0]} />
                        <Bar dataKey="issueLocalizationRate" name="问题定位" fill="#0284c7" radius={[3, 3, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                  <p className="mt-2 text-xs leading-relaxed text-slate-500">{dashboard.reliability.scope}</p>
                </div>

                <div className="min-w-0 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                  <SectionHeading icon={GitCompareArrows} title="同预算规划决策对照" detail={`${dashboard.planning.runCount} seeds · ${dashboard.planning.seeds.length * 6} decisions`} />
                  <div className="h-[270px] w-full">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={dashboard.planning.methods} margin={{ top: 10, right: 10, left: 0, bottom: 4 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                        <XAxis dataKey="label" tick={{ fontSize: 11 }} />
                        <YAxis domain={[0, 1]} tickFormatter={(value: number) => `${Math.round(value * 100)}%`} tick={{ fontSize: 11 }} />
                        <Tooltip formatter={(value: number) => formatPercent(value)} />
                        <Legend />
                        <Bar dataKey="policyAgreementRate" name="预注册策略一致" fill="#7c3aed" radius={[3, 3, 0, 0]} />
                        <Bar dataKey="constraintSatisfactionRate" name="硬约束满足" fill="#d97706" radius={[3, 3, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-2 text-xs text-slate-600">
                    <Badge variant="outline">{dashboard.planning.qwenCost.totalTokens || 0} tokens</Badge>
                    <Badge variant="outline">均值 {((dashboard.planning.qwenCost.meanLatencyMs || 0) / 1000).toFixed(2)} s</Badge>
                    <Badge variant="outline">估算 ¥{formatNumber(dashboard.planning.qwenCost.estimatedCostCny, 5)}</Badge>
                  </div>
                  <p className="mt-3 text-xs leading-relaxed text-slate-500">{dashboard.planning.scope}</p>
                </div>
              </section>

              <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                <SectionHeading icon={FileCheck2} title="可下载证据清单" detail="公开白名单文件逐项计算 SHA-256；不暴露密钥和原始受限数据" />
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[780px] text-left text-xs">
                    <thead className="border-b border-slate-200 text-slate-500">
                      <tr>
                        <th className="pb-2 font-medium">文件</th>
                        <th className="pb-2 font-medium">来源</th>
                        <th className="pb-2 font-medium">大小</th>
                        <th className="pb-2 font-medium">SHA-256</th>
                        <th className="pb-2 text-right font-medium">下载</th>
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
                            <a href={`${API_BASE_URL}${artifact.url}`} target="_blank" rel="noreferrer" title={`下载 ${artifact.filename}`} className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-slate-200 text-slate-600 hover:bg-slate-50 hover:text-sky-700">
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
              <section className="grid min-w-0 gap-5 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)]">
                <div className="min-w-0 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                  <SectionHeading icon={UserCheck} title="三阶段人工责任门" detail="批准与当前证据哈希绑定，证据变化后自动失效" />
                  <div className="grid gap-3 sm:grid-cols-3">
                    {(['plan', 'repair', 'conclusion'] as const).map((stage, index) => {
                      const status = dashboard.humanGovernance.signoffs[stage]
                      const label = ['方案签核', '风险修复签核', '结论签核'][index]
                      return (
                        <div key={stage} className="rounded-md border border-slate-200 p-4">
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-sm font-semibold text-slate-900">{label}</span>
                            {status === 'approved'
                              ? <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                              : <Clock3 className="h-4 w-4 text-amber-600" />}
                          </div>
                          <div className="mt-3 text-lg font-semibold capitalize text-slate-950">{status}</div>
                          <div className="mt-1 text-xs text-slate-500">Stage {index + 1} / 3</div>
                        </div>
                      )
                    })}
                  </div>
                  <div className="mt-4 grid gap-3 sm:grid-cols-2">
                    <div className="rounded-md border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700">
                      <div className="text-xs font-semibold uppercase text-slate-500">审核人分离</div>
                      <div className="mt-1 font-medium">{dashboard.humanGovernance.reviewerSeparationRequired ? '正式结论强制启用' : '未启用'}</div>
                    </div>
                    <div className="rounded-md border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700">
                      <div className="text-xs font-semibold uppercase text-slate-500">正式证据包</div>
                      <div className="mt-1 font-medium">{dashboard.humanGovernance.publicationReady ? '允许发布' : '服务端阻断'}</div>
                    </div>
                  </div>
                  {dashboard.humanGovernance.feedbackId && (
                    <div className="mt-4 flex flex-wrap items-center gap-3">
                      <Link to="/review/consistency" className="inline-flex items-center gap-2 text-sm font-medium text-sky-700 hover:underline">
                        打开签核工作区 <ExternalLink className="h-4 w-4" />
                      </Link>
                      <span className="font-mono text-[11px] text-slate-500">{dashboard.humanGovernance.feedbackId}</span>
                    </div>
                  )}
                </div>

                <div className="min-w-0 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                  <SectionHeading icon={AlertTriangle} title="诚实边界" detail="有效闭环不等于已经完成真实科学或仪器部署" />
                  <div className="space-y-3">
                    {dashboard.limitations.map((limitation, index) => (
                      <div key={limitation} className="flex items-start gap-3 rounded-md border border-amber-200 bg-amber-50/70 p-3">
                        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-amber-200 text-[11px] font-bold text-amber-900">{index + 1}</span>
                        <p className="text-sm leading-relaxed text-slate-700">{limitation}</p>
                      </div>
                    ))}
                  </div>
                </div>
              </section>

              <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                <SectionHeading icon={Fingerprint} title="发布状态解释" detail="自动化验证和真实人员责任明确分离" />
                <div className="grid gap-4 lg:grid-cols-3">
                  <div className="border-l-4 border-emerald-500 pl-4">
                    <div className="text-sm font-semibold text-slate-900">技术证据</div>
                    <p className="mt-1 text-sm leading-relaxed text-slate-600">合同、时间线、Qwen轨迹、两轮结果和受控压力测试均可机器核验。</p>
                  </div>
                  <div className="border-l-4 border-amber-500 pl-4">
                    <div className="text-sm font-semibold text-slate-900">科学结论</div>
                    <p className="mt-1 text-sm leading-relaxed text-slate-600">未见集增益只支持非退化，置信区间跨 0，因此不宣称显著普遍提升。</p>
                  </div>
                  <div className="border-l-4 border-violet-500 pl-4">
                    <div className="text-sm font-semibold text-slate-900">人工责任</div>
                    <p className="mt-1 text-sm leading-relaxed text-slate-600">{dashboard.humanGovernance.note}</p>
                  </div>
                </div>
              </section>
            </TabsContent>
          </Tabs>

          <div className="flex flex-col gap-2 border-t border-slate-200 pt-4 text-xs text-slate-500 sm:flex-row sm:items-center sm:justify-between">
            <span>Evidence snapshot · {new Date(dashboard.generatedAt).toLocaleString()}</span>
            <span className="font-mono">benchmark {shortHash(dashboard.case.benchmarkFingerprint)}</span>
          </div>
        </div>
      )}
    </AppPageLayout>
  )
}
