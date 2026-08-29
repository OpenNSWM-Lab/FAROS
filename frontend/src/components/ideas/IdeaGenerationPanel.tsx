import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
  Lightbulb,
  CheckCircle2,
  XCircle,
  BookOpen,
  Sparkles,
  RefreshCw,
  Zap,
  History,
  ChevronDown,
  ChevronUp,
  FileText,
  Settings,
  ShieldCheck,
  AlertTriangle,
  SlidersHorizontal,
  Plus,
} from 'lucide-react'
import { PAPER_TYPES, getPaperTypeById } from '@/lib/models/providers'
import { summarizeEvidenceGate, type EvidenceGateSummary } from './evidenceGateSummary'
import { DossierViewer } from './DossierViewer'
import { LiteratureFailureNotice } from './LiteratureFailureNotice'
import { summarizeLiteratureFailure } from './literatureFailureSummary'
import { SeedSuggestionList, type SeedSuggestion } from './SeedSuggestionList'
import { useReviewLocale } from '@/lib/reviewLocale'

interface IdeaSession {
  id: string
  status: string
  createdAt?: string
  startedAt?: string
  config: {
    seedQuery: string
    providerName: string
    model: string
    paperType?: string
    maxCandidates: number
    maxReviewIterations?: number
  }
  candidateIds: string[]
  finalCandidateIds?: string[]
  hiddenCandidateIds?: string[]
  rejectedCandidateIds?: string[]
  qualityLoopSummary?: {
    finalCandidateCount?: number
    hiddenCandidateCount?: number
    rejectedCandidateCount?: number
    warnings?: string[]
    blockingReason?: string
    resumeFrom?: string
    qualityStatus?: string
  }
  selectedCandidateId?: string
  errorMessage?: string
}

interface SessionListItem {
  id: string
  status: string
  createdAt: string
  config: {
    seedQuery: string
    paperType?: string
  }
}

interface StepResult {
  name: string
  status: string
  durationSeconds: number
  error?: string
  inputs?: Record<string, unknown>
  outputs?: Record<string, unknown>
}

interface TraceData {
  steps: StepResult[]
  totalSteps: number
  successfulSteps: number
  failedSteps: number
}

const PIPELINE_STEPS = [
  { name: 'expandQuery', labelZh: '理解研究问题', labelEn: 'Understand question', descZh: '拆解主题并生成检索策略', descEn: 'Refining the topic and search strategy' },
  { name: 'literatureSearch', labelZh: '检索相关文献', labelEn: 'Search literature', descZh: '检索、去重并过滤不相关论文', descEn: 'Retrieving, deduplicating, and filtering papers' },
  { name: 'noveltyCheck', labelZh: '深读关键论文', labelEn: 'Read key papers', descZh: '正在逐篇提取证据，通常需要数分钟', descEn: 'Extracting evidence paper by paper; this usually takes several minutes' },
  { name: 'gapAnalysis', labelZh: '识别研究空白', labelEn: 'Find research gaps', descZh: '比较已有方法、局限与未解决问题', descEn: 'Comparing methods, limitations, and open questions' },
  { name: 'evidenceGate', labelZh: '核验证据质量', labelEn: 'Verify evidence', descZh: '确认文献能否支撑后续创意', descEn: 'Checking whether evidence can support idea generation' },
  { name: 'ideaBrainstorm', labelZh: '生成候选创意', labelEn: 'Generate ideas', descZh: '根据证据生成可检验的候选方案', descEn: 'Generating testable candidates from the evidence' },
  { name: 'rankCandidates', labelZh: '审查并优化创意', labelEn: 'Review ideas', descZh: '多维评分并修正薄弱候选', descEn: 'Scoring and repairing weak candidates' },
  { name: 'finalizeSession', labelZh: '形成候选方案', labelEn: 'Finalize shortlist', descZh: '整理可进入研究计划的最终结果', descEn: 'Preparing the final shortlist for planning' },
] as const

interface ScoreEntry {
  value: number
  rationale: string
}

interface Candidate {
  id: string
  title: string
  problem: string
  hypothesisStatement?: string
  keyInsight: string
  proposedMethod?: string
  expectedOutcome?: string
  novelty: number
  noveltyRationale?: string
  feasibility: number
  feasibilityRationale?: string
  impact: number
  impactRationale?: string
  clarity: number
  clarityRationale?: string
  risk: number
  riskRationale?: string
  alignment: number
  alignmentRationale?: string
  referenceSupport: number
  referenceSupportRationale?: string
  experimentSpecificity: number
  experimentSpecificityRationale?: string
  overallScore: number
  scoreBreakdown?: Record<string, ScoreEntry>
  overallRationale?: string
  scoringConfidence?: number
  scoringMethod?: string
  references?: string[]
}

interface LiteratureItem {
  id: string
  title: string
  authors: string[]
  year?: number
  relevanceScore: number
}

interface SeedCheckResult {
  paperCount: number
  isSufficient: boolean
  threshold: number
  rawPaperCount?: number
  alignedPaperCount?: number
  generalizedQuery?: string | null
  suggestedQuery?: string | null
  suggestedQueries?: SeedSuggestion[]
  suggestionProvider?: string | null
  suggestionModel?: string | null
  diagnosisCode?: string | null
  suggestion?: string | null
  topPaperTitles?: string[]
  checkedSeedQuery: string
}

const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

interface CandidateSelection {
  ideaSessionId: string
  ideaCandidateId: string
  ideaCandidateTitle: string
  ideaSeedQuery: string
}

function EvidenceGateStatus({ summary }: { summary: EvidenceGateSummary }) {
  const { text } = useReviewLocale()
  const toneClass = {
    success: 'border-emerald-300 border-l-emerald-700 bg-emerald-50/80',
    warning: 'border-amber-300 border-l-amber-700 bg-amber-50/80',
    danger: 'border-red-300 border-l-red-700 bg-red-50/80',
    neutral: 'border-slate-300 border-l-slate-700 bg-slate-50',
  }[summary.tone]
  const icon = summary.tone === 'danger'
    ? <AlertTriangle className="h-4 w-4 text-red-700" />
    : <ShieldCheck className="h-4 w-4 text-emerald-700" />
  const coverageClass = (status: string) => {
    const normalized = status.toLowerCase()
    if (normalized === 'strong') return 'border-emerald-300 bg-emerald-50 text-emerald-800'
    if (normalized === 'partial') return 'border-sky-300 bg-sky-50 text-sky-800'
    if (normalized === 'weak') return 'border-amber-300 bg-amber-50 text-amber-800'
    if (normalized === 'missing') return 'border-red-300 bg-red-50 text-red-800'
    return 'border-slate-300 bg-white text-slate-700'
  }

  return (
    <div className={`rounded-md border border-l-4 p-3 ${toneClass}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2">
          <div className="mt-0.5">{icon}</div>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-slate-950">{summary.title}</p>
            <p className="mt-0.5 text-xs text-slate-700">{summary.description}</p>
          </div>
        </div>
        {summary.reviewerScore && (
          <Badge variant="outline" className="border-slate-400 bg-white text-xs text-slate-800">
            {text('审查评分', 'Reviewer')} {summary.reviewerScore}
          </Badge>
        )}
      </div>

      <div className="mt-3 grid gap-2 sm:grid-cols-4">
        {summary.stats.map((item) => (
          <div key={item.label} className="rounded border border-white/70 bg-white px-2 py-1.5">
            <p className="text-[11px] font-medium uppercase text-slate-500">{item.label}</p>
            <p className="mt-0.5 text-sm font-semibold text-slate-950">{item.value}</p>
          </div>
        ))}
      </div>

      {(summary.scientistJudgment || summary.coverageDimensions.length > 0) && (
        <div className="mt-3 rounded border border-white/70 bg-white px-2 py-2">
          <p className="text-xs font-semibold text-slate-800">{text('LLM Scientist 证据审查', 'LLM Scientist Evidence Review')}</p>
          {summary.scientistJudgment && (
            <p className="mt-1 text-xs leading-relaxed text-slate-700">{summary.scientistJudgment}</p>
          )}
          {summary.coverageDimensions.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {summary.coverageDimensions.map((dimension) => (
                <Badge
                  key={dimension.key}
                  variant="outline"
                  className={`max-w-full text-xs ${coverageClass(dimension.status)}`}
                >
                  <span className="truncate">
                    {dimension.label}: {dimension.status}
                    {dimension.score ? ` ${dimension.score}` : ''}
                    {dimension.paperCount ? ` · ${dimension.paperCount} ${text('篇论文', 'papers')}` : ''}
                  </span>
                </Badge>
              ))}
            </div>
          )}
        </div>
      )}

      {summary.issues.length > 0 && (
        <div className="mt-3 space-y-1">
          <p className="text-xs font-semibold text-slate-800">{text('主要问题', 'Main issues')}</p>
          {summary.issues.map((issue) => (
            <p key={issue} className="text-xs text-slate-700">{issue}</p>
          ))}
        </div>
      )}

      {summary.repairQueries.length > 0 && (
        <div className="mt-3 space-y-1">
          <p className="text-xs font-semibold text-slate-800">{text('补充检索', 'Repair searches')}</p>
          <div className="flex flex-wrap gap-1.5">
            {summary.repairQueries.map((query) => (
              <Badge key={query} variant="outline" className="max-w-full border-slate-300 bg-white text-xs text-slate-700">
                <span className="truncate">{query}</span>
              </Badge>
            ))}
          </div>
        </div>
      )}

      {summary.warnings.length > 0 && summary.issues.length === 0 && (
        <div className="mt-3 space-y-1">
          <p className="text-xs font-semibold text-slate-800">{text('说明', 'Notes')}</p>
          {summary.warnings.map((warning) => (
            <p key={warning} className="text-xs text-slate-700">{warning}</p>
          ))}
        </div>
      )}
    </div>
  )
}

function PipelineProgress({
  trace,
  isPolling,
  status,
  startedAt,
  expandedStep,
  showTechnicalDetails,
  onToggleStep,
}: {
  trace: TraceData | null
  isPolling: boolean
  status?: string
  startedAt?: string
  expandedStep: number | null
  showTechnicalDetails: boolean
  onToggleStep: (index: number | null) => void
}) {
  const { text } = useReviewLocale()
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!isPolling) return
    setNow(Date.now())
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [isPolling])
  const startedAtMs = startedAt ? Date.parse(startedAt) : Number.NaN
  const elapsedSeconds = Number.isFinite(startedAtMs) ? Math.max(0, Math.floor((now - startedAtMs) / 1000)) : 0
  const elapsedLabel = elapsedSeconds >= 60
    ? `${Math.floor(elapsedSeconds / 60)}m ${elapsedSeconds % 60}s`
    : `${elapsedSeconds}s`
  const actualByName = new Map((trace?.steps || []).map((step) => [step.name, step]))
  const failedStep = trace?.steps.find((step) => step.status === 'failed')
  const firstMissingIndex = PIPELINE_STEPS.findIndex((step) => !actualByName.has(step.name))
  const activeIndex = failedStep
    ? PIPELINE_STEPS.findIndex((step) => step.name === failedStep.name)
    : isPolling && firstMissingIndex >= 0
      ? firstMissingIndex
      : -1
  const visibleSteps = trace?.steps.length
    ? trace.steps
    : []
  const runningMeta = activeIndex >= 0 ? PIPELINE_STEPS[activeIndex] : null

  const stepIcon = (stepStatus: string, order: number) => {
    if (stepStatus === 'ok') return <CheckCircle2 className="h-4 w-4 text-emerald-600" />
    if (stepStatus === 'failed') return <XCircle className="h-4 w-4 text-red-600" />
    if (stepStatus === 'running') return <RefreshCw className="h-4 w-4 animate-spin text-blue-600" />
    return <span className="flex h-4 w-4 items-center justify-center rounded-full border border-slate-300 text-[10px] text-slate-500">{order}</span>
  }

  return (
    <div className="space-y-2">
      <h4 className="text-sm font-medium mb-3">{text('流程步骤', 'Pipeline Steps')}</h4>
      <div className="space-y-1">
        {visibleSteps.map((actual, index) => {
          const meta = PIPELINE_STEPS.find((item) => item.name === actual.name)
          const isExpandable = showTechnicalDetails && Boolean(actual?.outputs && Object.keys(actual.outputs).length > 0)
          return (
            <div key={`${actual.name}-${index}`}>
              <div
                onClick={() => isExpandable && onToggleStep(expandedStep === index ? null : index)}
                className={`flex items-center gap-3 p-2 rounded border border-slate-300 bg-white ${isExpandable ? 'cursor-pointer hover:bg-slate-50' : ''}`}
              >
                {stepIcon(actual.status, index + 1)}
                <span className="text-sm font-medium flex-1">
                  {meta ? text(meta.labelZh, meta.labelEn) : actual.name}
                </span>
                <span className="text-xs text-muted-foreground">{actual.durationSeconds.toFixed(1)}s</span>
                {actual.error && showTechnicalDetails && <span className="text-xs text-red-500 truncate max-w-[200px]">{actual.error}</span>}
                {isExpandable && (expandedStep === index ? <ChevronUp className="h-3 w-3 text-slate-400" /> : <ChevronDown className="h-3 w-3 text-slate-400" />)}
              </div>
              {expandedStep === index && actual?.outputs && (
                <div className="ml-8 mt-1 mb-2 p-2 rounded bg-white border text-xs space-y-1">
                  {actual.inputs && Object.keys(actual.inputs).length > 0 && (
                    <div><span className="font-medium text-slate-500">{text('输入', 'Inputs')}:</span> {Object.entries(actual.inputs).map(([k, v]) => <span key={k} className="ml-1 text-slate-600">{k}={typeof v === 'string' ? v : JSON.stringify(v)}</span>)}</div>
                  )}
                  {Object.entries(actual.outputs).filter(([k]) => k !== 'llmLatencyMs').map(([key, val]) => (
                    <div key={key}>
                      <span className="font-medium text-amber-700">{key}:</span>{' '}
                      <span className="text-slate-700">
                        {Array.isArray(val) ? (val.length > 3 ? `[${val.slice(0, 3).map(v => typeof v === 'string' ? v : JSON.stringify(v)).join(', ')}... +${val.length - 3} more]` : JSON.stringify(val)) : typeof val === 'object' && val !== null ? JSON.stringify(val).slice(0, 200) : String(val).slice(0, 200)}
                      </span>
                    </div>
                  ))}
                  {Boolean(actual.outputs.llmLatencyMs) && <div className="text-slate-400">LLM {text('延迟', 'latency')}: {String(actual.outputs.llmLatencyMs)}ms</div>}
                </div>
              )}
            </div>
          )
        })}

        {isPolling && runningMeta && !actualByName.has(runningMeta.name) && (
          <div className="flex items-center gap-3 p-2 rounded border border-blue-300 bg-blue-50">
            {stepIcon('running', visibleSteps.length + 1)}
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-slate-950">{text(runningMeta.labelZh, runningMeta.labelEn)}</p>
              <p className="mt-0.5 text-xs text-blue-800">{text(runningMeta.descZh, runningMeta.descEn)}</p>
            </div>
            <span className="shrink-0 font-mono text-xs text-blue-700">{elapsedLabel}</span>
          </div>
        )}

        {!visibleSteps.length && !isPolling && (
          <div className="flex items-center gap-3 p-2 rounded border border-slate-300 bg-white text-sm text-slate-500">
            {stepIcon('pending', 1)}
            <span>{text('等待开始', 'Waiting to start')}</span>
          </div>
        )}
      </div>
      <div className="flex gap-4 mt-3 text-xs text-muted-foreground">
        <span>{text('总计', 'Total')}: {trace?.totalSteps || visibleSteps.length}</span>
        <span className="text-green-600">{text('成功', 'Success')}: {trace?.successfulSteps || 0}</span>
        <span className="text-red-600">{text('失败', 'Failed')}: {trace?.failedSteps || 0}</span>
        {status === 'completed' && <span className="text-emerald-700">{text('已生成审查后的候选列表', 'Reviewed shortlist is ready')}</span>}
      </div>
    </div>
  )
}

export function IdeaGenerationPanel({
  onCandidateSelected,
}: {
  onCandidateSelected?: (data: CandidateSelection) => void
}) {
  const navigate = useNavigate()
  const { locale, text } = useReviewLocale()
  const [seedQuery, setSeedQuery] = useState('')
  const [activeProvider, setActiveProvider] = useState('moonshot')
  const [activeModel, setActiveModel] = useState('moonshot-v1-8k')
  const [paperType, setPaperType] = useState('algorithm')
  const [maxCandidates, setMaxCandidates] = useState(3)
  const [maxIdeaReviewIterations, setMaxIdeaReviewIterations] = useState(1)
  const [session, setSession] = useState<IdeaSession | null>(null)
  const [trace, setTrace] = useState<TraceData | null>(null)
  const [candidates, setCandidates] = useState<Candidate[]>([])
  const [literature, setLiterature] = useState<LiteratureItem[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [isPolling, setIsPolling] = useState(false)
  const [providerTestResult, setProviderTestResult] = useState<{ ok: boolean, latencyMs?: number, error?: string } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isTestingProvider, setIsTestingProvider] = useState(false)
  const [sessionHistory, setSessionHistory] = useState<SessionListItem[]>([])
  const [showHistory, setShowHistory] = useState(false)
  const [isLoadingHistory, setIsLoadingHistory] = useState(false)
  const [expandedCandidate, setExpandedCandidate] = useState<string | null>(null)
  const [expandedStep, setExpandedStep] = useState<number | null>(null)
  const [showDebugDetails, setShowDebugDetails] = useState(false)
  const [showAdvancedSettings, setShowAdvancedSettings] = useState(false)
  const [seedCheckResult, setSeedCheckResult] = useState<SeedCheckResult | null>(null)
  const [isCheckingSeed, setIsCheckingSeed] = useState(false)
  const [seedSuggestions, setSeedSuggestions] = useState<SeedSuggestion[]>([])
  const [seedSuggestionModel, setSeedSuggestionModel] = useState<string | undefined>()
  const [seedSuggestionError, setSeedSuggestionError] = useState<string | null>(null)
  const [isSuggestingSeed, setIsSuggestingSeed] = useState(false)
  const [isLoadingCandidates, setIsLoadingCandidates] = useState(false)
  const [candidateLoadFailed, setCandidateLoadFailed] = useState(false)
  const [pollingInterrupted, setPollingInterrupted] = useState(false)
  const seedInputRef = useRef<HTMLTextAreaElement>(null)
  const seedSuggestionRunRef = useRef(0)
  const pollingInFlightRef = useRef(false)
  const literatureLoadedForSessionRef = useRef<string | null>(null)
  const evidenceSummary = useMemo(() => summarizeEvidenceGate(trace?.steps), [trace])
  const literatureFailure = useMemo(() => summarizeLiteratureFailure(trace?.steps), [trace])

  useEffect(() => {
    loadSessionHistory()
    loadActiveLlmFromSettings()
    // Only explicit deep links restore a session. A normal page visit starts
    // with a clean workspace so previous results are not mistaken for new data.
    const linkedSessionId = new URLSearchParams(window.location.search).get('ideaSessionId')
    if (linkedSessionId) {
      loadSession(linkedSessionId)
    }
  }, [])

  const loadActiveLlmFromSettings = async () => {
    try {
      const r = await fetch(`${API_BASE}/api/v1/providers`)
      if (!r.ok) return
      const data = await r.json()
      const provider = data.activeProvider || 'moonshot'
      const providerInfo = (data.providers || []).find((p: { providerName: string; model: string }) => p.providerName === provider)
      setActiveProvider(provider)
      setActiveModel(providerInfo?.model || 'moonshot-v1-8k')
    } catch (err) {
      console.error('Failed to load active LLM from settings:', err)
    }
  }

  const loadSessionHistory = async () => {
    setIsLoadingHistory(true)
    try {
      const response = await fetch(`${API_BASE}/api/v1/ideas/sessions`)
      if (response.ok) {
        const data = await response.json()
        setSessionHistory(data.sessions || [])
      }
    } catch (err) {
      console.error('Failed to load session history:', err)
    } finally {
      setIsLoadingHistory(false)
    }
  }

  const loadSession = async (sessionId: string) => {
    setIsLoadingCandidates(true)
    setCandidateLoadFailed(false)
    try {
      const [sessionResponse, traceResponse, litResponse, candResponse] = await Promise.all([
        fetch(`${API_BASE}/api/v1/ideas/sessions/${sessionId}`),
        fetch(`${API_BASE}/api/v1/ideas/sessions/${sessionId}/trace`),
        fetch(`${API_BASE}/api/v1/ideas/sessions/${sessionId}/literature`),
        fetch(`${API_BASE}/api/v1/ideas/sessions/${sessionId}/candidates`),
      ])
      if (!sessionResponse.ok) throw new Error('Session not found')
      const sessionData = await sessionResponse.json()
      const [traceData, literatureData, candidateData] = await Promise.all([
        traceResponse.ok ? traceResponse.json() : Promise.resolve(null),
        litResponse.ok ? litResponse.json() : Promise.resolve(null),
        candResponse.ok ? candResponse.json() : Promise.resolve(null),
      ])

      setSession(sessionData)
      setShowDebugDetails(false)
      setSeedQuery(sessionData.config.seedQuery)
      setPaperType(sessionData.config.paperType || 'algorithm')
      setMaxCandidates(sessionData.config.maxCandidates)
      setMaxIdeaReviewIterations(sessionData.config.maxReviewIterations || 2)
      setSeedCheckResult(null)
      setSeedSuggestions([])
      setSeedSuggestionError(null)
      setTrace(traceData)
      setLiterature(literatureData?.items || [])
      setCandidates(candidateData?.candidates || [])
      setCandidateLoadFailed(!candResponse.ok)
      literatureLoadedForSessionRef.current = litResponse.ok ? sessionId : null
      const isTerminal = ['completed', 'failed', 'awaiting_evidence', 'awaiting_ideas'].includes(sessionData.status)
      setIsPolling(!isTerminal)
      setPollingInterrupted(false)
      setShowHistory(false)
      setError(null)
      localStorage.setItem('idea_active_session_id', sessionId)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load session')
      setCandidateLoadFailed(true)
    } finally {
      setIsLoadingCandidates(false)
    }
  }

  const clearLoadedResults = () => {
    setSession(null)
    setTrace(null)
    setCandidates([])
    setLiterature([])
    setExpandedCandidate(null)
    setExpandedStep(null)
    setShowDebugDetails(false)
    setIsLoadingCandidates(false)
    setCandidateLoadFailed(false)
    setPollingInterrupted(false)
    literatureLoadedForSessionRef.current = null
    localStorage.removeItem('idea_active_session_id')
  }

  const startNewResearch = (nextSeed = '') => {
    seedSuggestionRunRef.current += 1
    clearLoadedResults()
    setSeedQuery(nextSeed)
    setSeedCheckResult(null)
    setSeedSuggestions([])
    setSeedSuggestionError(null)
    setIsSuggestingSeed(false)
    setError(null)
    window.setTimeout(() => seedInputRef.current?.focus(), 0)
  }

  const applySeedSuggestion = (query: string) => {
    seedSuggestionRunRef.current += 1
    clearLoadedResults()
    setSeedQuery(query)
    setSeedCheckResult(null)
    setSeedSuggestions([])
    setSeedSuggestionError(null)
    setIsSuggestingSeed(false)
    setError(null)
    window.setTimeout(() => {
      seedInputRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
      seedInputRef.current?.focus({ preventScroll: true })
    }, 0)
  }

  const requestSeedSuggestions = async (
    userIdea = seedQuery,
    diagnosisCode?: string,
  ) => {
    const runId = seedSuggestionRunRef.current + 1
    seedSuggestionRunRef.current = runId
    setIsSuggestingSeed(true)
    setSeedSuggestionError(null)
    try {
      const response = await fetch(`${API_BASE}/api/v1/ideas/seed-suggestion-jobs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          userIdea: userIdea.trim(),
          paperType,
          count: 3,
          diagnosisCode: diagnosisCode || null,
        }),
      })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) {
        const detail = String(data.detail || '')
        throw new Error(detail || `HTTP ${response.status}`)
      }
      const jobId = String(data.jobId || '')
      if (!jobId) throw new Error(text('未能创建主题推荐任务，请重试。', 'Could not start topic recommendation. Please retry.'))

      let result: { model?: string; suggestions?: SeedSuggestion[] } | null = null
      for (let attempt = 0; attempt < 120; attempt += 1) {
        if (seedSuggestionRunRef.current !== runId) return
        let jobResponse: Response
        try {
          jobResponse = await fetch(`${API_BASE}/api/v1/ideas/seed-suggestion-jobs/${encodeURIComponent(jobId)}`)
        } catch {
          await new Promise((resolve) => window.setTimeout(resolve, 2000))
          continue
        }
        const job = await jobResponse.json().catch(() => ({}))
        if (!jobResponse.ok) throw new Error(String(job.detail || `HTTP ${jobResponse.status}`))
        if (job.status === 'completed') {
          result = job.result || null
          break
        }
        if (job.status === 'failed') throw new Error(String(job.error || 'Qwen topic recommendation failed'))
        await new Promise((resolve) => window.setTimeout(resolve, 2000))
      }
      if (!result) throw new Error(text('千问生成主题超时，请稍后重试。', 'Qwen topic generation timed out. Please retry later.'))
      if (seedSuggestionRunRef.current !== runId) return

      const suggestions = Array.isArray(result.suggestions) ? result.suggestions : []
      if (!suggestions.length) {
        throw new Error(text('千问没有返回可用主题，请重试。', 'Qwen returned no usable topics. Please retry.'))
      }
      setSeedSuggestions(suggestions)
      setSeedSuggestionModel(result.model || undefined)
      window.setTimeout(() => seedInputRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' }), 0)
    } catch (err) {
      if (seedSuggestionRunRef.current !== runId) return
      const detail = err instanceof Error ? err.message : ''
      setSeedSuggestionError(
        detail.includes('API key')
          ? text(
              '当前账户还没有配置千问 API Key，请先前往“设置 > LLM Provider”完成配置。',
              'This account has no Qwen API key. Configure it in Settings > LLM Providers first.',
            )
          : text(
              `千问主题推荐失败：${detail || 'unknown error'}。请检查网络和模型设置后重试。`,
              `Qwen topic recommendation failed: ${detail || 'unknown error'}. Check the network and model settings, then retry.`,
            ),
      )
    } finally {
      if (seedSuggestionRunRef.current === runId) setIsSuggestingSeed(false)
    }
  }

  const testProvider = async () => {
    setProviderTestResult(null)
    setIsTestingProvider(true)
    try {
      const response = await fetch(`${API_BASE}/api/v1/providers/test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: 'Say OK', maxTokens: 10 })
      })
      if (!response.ok) {
        if (response.status === 404) { setProviderTestResult({ ok: false, error: 'Endpoint not found' }) }
        else if (response.status === 400) { const d = await response.json(); setProviderTestResult({ ok: false, error: `Config error: ${d.detail || 'Missing API key'}` }) }
        else { setProviderTestResult({ ok: false, error: `Server error: ${response.status}` }) }
        return
      }
      setProviderTestResult(await response.json())
    } catch (err) {
      setProviderTestResult({ ok: false, error: 'Backend unreachable' })
    } finally {
      setIsTestingProvider(false)
    }
  }

  const checkSeed = async (): Promise<SeedCheckResult | null> => {
    const checkedSeedQuery = seedQuery.trim()
    if (!checkedSeedQuery) return null
    setIsCheckingSeed(true)
    setSeedCheckResult(null)
    setSeedSuggestionError(null)
    setError(null)
    try {
      const response = await fetch(`${API_BASE}/api/v1/ideas/seed-check`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ seedQuery: checkedSeedQuery, paperType }),
      })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) {
        throw new Error(text(
          `主题预检失败（HTTP ${response.status}），请检查网络或稍后重试。`,
          `Topic pre-check failed (HTTP ${response.status}). Check the network or retry later.`,
        ))
      }
      const result: SeedCheckResult = { ...data, checkedSeedQuery }
      setSeedCheckResult(result)
      if (Array.isArray(result.suggestedQueries) && result.suggestedQueries.length > 0) {
        setSeedSuggestions(result.suggestedQueries)
        setSeedSuggestionModel(result.suggestionModel || undefined)
      } else if (!result.isSufficient) {
        void requestSeedSuggestions(checkedSeedQuery, result.diagnosisCode || undefined)
      }
      return result
    } catch (err) {
      setSeedCheckResult(null)
      setError(err instanceof Error ? err.message : text('主题预检失败', 'Topic pre-check failed'))
      return null
    } finally {
      setIsCheckingSeed(false)
    }
  }

  const generateIdeas = async () => {
    if (!seedQuery.trim()) { setError(text('请输入研究主题', 'Please enter a research topic')); return }
    setIsLoading(true); setError(null); setSession(null); setTrace(null); setCandidates([]); setLiterature([]); setShowDebugDetails(false)
    setCandidateLoadFailed(false); setPollingInterrupted(false); literatureLoadedForSessionRef.current = null
    try {
      await loadActiveLlmFromSettings()
      const createResponse = await fetch(`${API_BASE}/api/v1/ideas/sessions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ seedQuery, paperType, maxCandidates, maxReviewIterations: maxIdeaReviewIterations })
      })
      if (!createResponse.ok) { const d = await createResponse.json().catch(() => ({})); throw new Error(d.detail || `Failed: ${createResponse.status}`) }
      const sessionData = await createResponse.json()
      setSession(sessionData)
      localStorage.setItem('idea_active_session_id', sessionData.id)
      const startResponse = await fetch(`${API_BASE}/api/v1/ideas/sessions/${sessionData.id}/start`, { method: 'POST' })
      if (!startResponse.ok) { throw new Error(`Failed to start: ${startResponse.status}`) }
      setSession(await startResponse.json())
      setIsPolling(true)
      loadSessionHistory()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setIsLoading(false)
    }
  }

  const checkAndGenerate = async () => {
    const normalizedSeed = seedQuery.trim()
    if (!normalizedSeed) {
      setError(text('可以先写一个大概方向，或者让千问直接推荐主题。', 'Enter a rough interest or ask Qwen to recommend a topic.'))
      seedInputRef.current?.focus()
      return
    }

    const currentCheck = seedCheckResult?.checkedSeedQuery === normalizedSeed
      ? seedCheckResult
      : await checkSeed()
    if (!currentCheck?.isSufficient) {
      window.setTimeout(() => seedInputRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'center' }), 0)
      return
    }
    await generateIdeas()
  }

  const pollSession = useCallback(async () => {
    if (!session?.id || !isPolling || pollingInFlightRef.current) return
    pollingInFlightRef.current = true
    try {
      const sessionId = session.id
      const [sessionResponse, traceResponse] = await Promise.all([
        fetch(`${API_BASE}/api/v1/ideas/sessions/${sessionId}`),
        fetch(`${API_BASE}/api/v1/ideas/sessions/${sessionId}/trace`),
      ])
      if (!sessionResponse.ok || !traceResponse.ok) throw new Error('Polling request failed')
      const [sessionData, traceData] = await Promise.all([
        sessionResponse.json(),
        traceResponse.json(),
      ])
      const isTerminal = ['completed', 'failed', 'awaiting_evidence', 'awaiting_ideas'].includes(sessionData.status)
      const literatureReady = traceData.steps?.some((step: StepResult) => step.name === 'literatureSearch' && step.status === 'ok')

      let literatureData: { items?: LiteratureItem[] } | null = null
      let candidateData: { candidates?: Candidate[] } | null = null
      if (literatureReady && literatureLoadedForSessionRef.current !== sessionId) {
        const litResponse = await fetch(`${API_BASE}/api/v1/ideas/sessions/${sessionId}/literature`)
        if (litResponse.ok) {
          literatureData = await litResponse.json()
          literatureLoadedForSessionRef.current = sessionId
        }
      }
      if (isTerminal && sessionData.status === 'completed') {
        setIsLoadingCandidates(true)
        const candResponse = await fetch(`${API_BASE}/api/v1/ideas/sessions/${sessionId}/candidates`)
        if (candResponse.ok) {
          candidateData = await candResponse.json()
          setCandidateLoadFailed(false)
        } else {
          setCandidateLoadFailed(true)
        }
      }

      setTrace(traceData)
      if (literatureData) setLiterature(literatureData.items || [])
      if (candidateData) setCandidates(candidateData.candidates || [])
      setSession(sessionData)
      setPollingInterrupted(false)
      if (isTerminal) {
        setIsPolling(false)
        loadSessionHistory()
      }
    } catch (err) {
      console.error('Polling error:', err)
      setPollingInterrupted(true)
    } finally {
      setIsLoadingCandidates(false)
      pollingInFlightRef.current = false
    }
  }, [session?.id, isPolling])

  const resumeSession = async () => {
    if (!session?.id) return
    setError(null)
    setIsLoading(true)
    try {
      const response = await fetch(`${API_BASE}/api/v1/ideas/sessions/${session.id}/resume`, {
        method: 'POST',
      })
      if (!response.ok) {
        const data = await response.json().catch(() => ({}))
        throw new Error(data.detail || `Failed to resume: ${response.status}`)
      }
      setSession(await response.json())
      setIsPolling(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to resume session')
    } finally {
      setIsLoading(false)
    }
  }

  const focusSeedEditor = () => {
    seedInputRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
    seedInputRef.current?.focus({ preventScroll: true })
  }

  useEffect(() => {
    if (!isPolling) return
    void pollSession()
    const interval = window.setInterval(() => void pollSession(), 4000)
    return () => window.clearInterval(interval)
  }, [isPolling, pollSession])

  const openPlanningForCandidate = (candidate: Candidate) => {
    if (!session?.id) return
    const q = session.config.seedQuery || seedQuery
    const data: CandidateSelection = {
      ideaSessionId: session.id,
      ideaCandidateId: candidate.id,
      ideaCandidateTitle: candidate.title,
      ideaSeedQuery: q,
    }
    if (onCandidateSelected) {
      onCandidateSelected(data)
    } else {
      const params = new URLSearchParams({
        ideaSessionId: data.ideaSessionId,
        ideaCandidateId: data.ideaCandidateId,
        ideaCandidateTitle: data.ideaCandidateTitle,
      })
      if (data.ideaSeedQuery) params.set('ideaSeedQuery', data.ideaSeedQuery)
      navigate(`/research/planning?${params.toString()}`)
    }
  }

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'completed': return 'bg-emerald-700 text-white'
      case 'running': return 'bg-blue-700 text-white'
      case 'failed': return 'bg-red-700 text-white'
      case 'awaiting_evidence': return 'bg-amber-700 text-white'
      case 'awaiting_ideas': return 'bg-amber-700 text-white'
      case 'pending': return 'bg-amber-600 text-white'
      default: return 'bg-slate-600 text-white'
    }
  }

  const getStatusLabel = (status: string) => {
    const labels: Record<string, [string, string]> = {
      completed: ['已完成', 'Completed'],
      running: ['运行中', 'Running'],
      failed: ['失败', 'Failed'],
      awaiting_evidence: ['需修改主题', 'Needs a topic change'],
      awaiting_ideas: ['需补充创意', 'Needs more ideas'],
      pending: ['等待开始', 'Pending'],
    }
    const label = labels[status]
    return label ? text(label[0], label[1]) : status
  }

  const getScoreColor = (score: number) => {
    if (score >= 8) return 'bg-emerald-700 text-white'
    if (score >= 6) return 'bg-amber-600 text-white'
    return 'bg-red-700 text-white'
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={() => setShowHistory(!showHistory)}>
          <History className="mr-1.5 h-4 w-4" />
          {text('历史研究', 'Research history')} ({sessionHistory.length})
          {showHistory ? <ChevronUp className="ml-1 h-4 w-4" /> : <ChevronDown className="ml-1 h-4 w-4" />}
        </Button>
        {session && (
          <Button variant="outline" size="sm" onClick={() => startNewResearch()} disabled={isPolling}>
            <Plus className="mr-1.5 h-4 w-4" />
            {text('新建研究', 'New research')}
          </Button>
        )}
      </div>

      {showHistory && (
        <div className="rounded-md border border-border bg-background px-3 py-3">
          {isLoadingHistory ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground"><RefreshCw className="h-4 w-4 animate-spin" /> {text('正在加载...', 'Loading...')}</div>
          ) : sessionHistory.length === 0 ? (
            <p className="text-sm text-muted-foreground">{text('暂无历史研究', 'No previous research')}</p>
          ) : (
            <div className="max-h-56 divide-y divide-border overflow-y-auto">
              {sessionHistory.map((item) => (
                <button
                  type="button"
                  key={item.id}
                  className="flex w-full items-center gap-3 px-1 py-2.5 text-left hover:bg-muted/60"
                  onClick={() => loadSession(item.id)}
                >
                  <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">{item.config.seedQuery}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">{new Date(item.createdAt).toLocaleDateString(locale)}</span>
                  <Badge className={getStatusColor(item.status)} variant="outline">{getStatusLabel(item.status)}</Badge>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Lightbulb className="h-5 w-5 text-amber-500" />{text('从一个研究方向开始', 'Start with a research direction')}</CardTitle>
          <CardDescription>{text('可以用自然语言描述大概想法，也可以让千问直接推荐可运行的主题。', 'Describe a rough interest, or let Qwen recommend a topic that is ready to run.')}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <label className="text-sm font-medium">{text('你想研究什么？', 'What do you want to study?')}</label>
            <textarea
              ref={seedInputRef}
              value={seedQuery}
              onChange={(event) => {
                seedSuggestionRunRef.current += 1
                setIsSuggestingSeed(false)
                if (session) clearLoadedResults()
                setSeedQuery(event.target.value)
                setSeedCheckResult(null)
                setSeedSuggestions([])
                setSeedSuggestionError(null)
                setError(null)
              }}
              placeholder={text(
                '例如：我想研究如何让 AI Scientist 生成的科研创意更可信',
                'e.g., I want to make research ideas generated by AI Scientists more trustworthy',
              )}
              className="min-h-[96px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground focus:ring-2 focus:ring-amber-500"
              disabled={isPolling}
            />
            <div className="flex flex-wrap items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="border-cyan-500 text-cyan-800 hover:bg-cyan-50 dark:text-cyan-200 dark:hover:bg-cyan-950"
                onClick={() => requestSeedSuggestions(seedQuery, seedCheckResult?.diagnosisCode || undefined)}
                disabled={isPolling || isSuggestingSeed}
              >
                {isSuggestingSeed ? <RefreshCw className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Sparkles className="mr-1.5 h-3.5 w-3.5" />}
                {isSuggestingSeed
                  ? text('千问正在准备主题', 'Qwen is preparing topics')
                  : seedQuery.trim()
                    ? text('让千问完善这个主题', 'Ask Qwen to refine this topic')
                    : text('让千问推荐 3 个主题', 'Ask Qwen for 3 topics')}
              </Button>
            </div>

            {isSuggestingSeed && (
              <p role="status" aria-live="polite" className="text-xs leading-5 text-muted-foreground">
                {text(
                  '正在补全具体任务、研究方法和评估指标，通常需要 10-60 秒；可以留在本页等待。',
                  'Adding a concrete task, method, and evaluation target. This usually takes 10-60 seconds; keep this page open.',
                )}
              </p>
            )}

            {seedSuggestionError && (
              <div className="flex flex-wrap items-center justify-between gap-2 border-l-4 border-red-600 bg-red-50 px-3 py-2 dark:bg-red-950/30">
                <p className="text-sm text-red-800 dark:text-red-200">{seedSuggestionError}</p>
                <Button type="button" size="sm" variant="outline" onClick={() => navigate('/settings/providers')}>
                  <Settings className="mr-1.5 h-3.5 w-3.5" />
                  {text('打开模型设置', 'Open model settings')}
                </Button>
              </div>
            )}

            <SeedSuggestionList
              suggestions={seedSuggestions}
              model={seedSuggestionModel}
              onSelect={applySeedSuggestion}
            />

            {seedCheckResult && (
              <div className={`rounded-md border border-l-4 p-3 ${
                seedCheckResult.isSufficient
                  ? 'border-emerald-300 border-l-emerald-700 bg-emerald-50/80'
                  : 'border-amber-300 border-l-amber-700 bg-amber-50/80'
              }`}>
                <div className="flex items-start gap-2">
                  {seedCheckResult.isSufficient
                    ? <CheckCircle2 className="h-4 w-4 mt-0.5 shrink-0 text-emerald-700" />
                    : <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0 text-amber-700" />}
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold text-slate-950">
                      {seedCheckResult.isSufficient
                        ? text('这个主题具备足够的相关文献，可以开始研究。', 'This topic has enough relevant literature to begin.')
                        : text('这个主题暂时不适合直接开始，请选择一条千问建议或修改后重试。', 'This topic is not ready yet. Choose a Qwen suggestion or revise it before retrying.')}
                    </p>
                    {!seedCheckResult.isSufficient && (
                      <p className="mt-1 text-xs leading-5 text-slate-700">
                        {seedCheckResult.diagnosisCode === 'seed_too_broad'
                          ? text(
                              '当前主题过短或过宽。请同时写明具体任务、方法和评估目标，然后使用建议改写创建新会话。',
                              'The topic is too short or broad. Name a task, method, and evaluation target, then create a new session with the rewrite.',
                            )
                          : seedCheckResult.diagnosisCode === 'no_search_results'
                            ? text(
                                '文献源暂未返回结果。请使用英文论文术语，等待接口限流恢复后重试。',
                                'No source returned results. Use English academic terms and retry after API cooldown.',
                              )
                            : seedCheckResult.suggestion}
                      </p>
                    )}
                    <details className="mt-2 text-xs text-slate-600">
                      <summary className="cursor-pointer font-medium">{text('查看预检详情', 'View pre-check details')}</summary>
                      <p className="mt-2">
                        {text('原始结果', 'Raw')} {seedCheckResult.rawPaperCount ?? seedCheckResult.paperCount}
                        {' · '}{text('有效论文', 'Eligible')} {seedCheckResult.paperCount}/{seedCheckResult.threshold}
                        {' · '}{text('主题对齐', 'Aligned')} {seedCheckResult.alignedPaperCount ?? seedCheckResult.paperCount}
                      </p>
                      {seedCheckResult.topPaperTitles && seedCheckResult.topPaperTitles.length > 0 && (
                        <div className="mt-1 space-y-0.5">
                          {seedCheckResult.topPaperTitles.slice(0, 3).map((title, index) => <p key={title}>{index + 1}. {title}</p>)}
                        </div>
                      )}
                    </details>
                  </div>
                </div>
              </div>
            )}
          </div>

          <div className="rounded-md border border-border bg-muted/30">
            <button
              type="button"
              className="flex w-full items-center justify-between gap-3 px-3 py-2.5 text-left"
              onClick={() => setShowAdvancedSettings(!showAdvancedSettings)}
              aria-expanded={showAdvancedSettings}
            >
              <span className="flex min-w-0 items-center gap-2 text-sm font-medium text-foreground">
                <SlidersHorizontal className="h-4 w-4 text-muted-foreground" />
                <span className="shrink-0">{text('高级设置', 'Advanced settings')}</span>
                <span className="hidden min-w-0 truncate text-xs font-normal text-muted-foreground sm:inline">{activeProvider} / {activeModel}</span>
              </span>
              {showAdvancedSettings ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
            </button>
            {showAdvancedSettings && (
              <div className="space-y-4 border-t border-border px-3 py-3">
                <div className="grid gap-4 md:grid-cols-2">
                  <div className="space-y-2">
                    <div className="flex items-center justify-between gap-2">
                      <label className="text-sm font-medium">LLM Provider</label>
                      <Button type="button" variant="outline" size="sm" onClick={() => navigate('/settings/providers')} disabled={isPolling}>
                        <Settings className="mr-2 h-4 w-4" />{text('配置 LLM', 'Configure LLM')}
                      </Button>
                    </div>
                    <p className="rounded-md border border-border bg-background px-3 py-2 font-mono text-sm text-foreground">{activeProvider} / {activeModel}</p>
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-medium">{text('论文类型', 'Paper Type')}</label>
                    <select
                      value={paperType}
                      onChange={(event) => {
                        setPaperType(event.target.value)
                        setSeedCheckResult(null)
                        setSeedSuggestions([])
                      }}
                      className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                      disabled={isPolling}
                    >
                      {PAPER_TYPES.map((paper) => <option key={paper.id} value={paper.id}>{paper.name}</option>)}
                    </select>
                    <p className="text-xs text-muted-foreground">{getPaperTypeById(paperType)?.description}</p>
                  </div>
                </div>
                <div className="grid gap-4 md:grid-cols-2">
                  <div className="space-y-2">
                    <label className="text-sm font-medium">{text('候选创意数', 'Idea candidates')}: {maxCandidates}</label>
                    <input type="range" min={1} max={10} value={maxCandidates} onChange={(event) => setMaxCandidates(parseInt(event.target.value))} className="w-full" disabled={isPolling} />
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-medium">{text('创意审查轮数', 'Review iterations')}: {maxIdeaReviewIterations}</label>
                    <input type="range" min={1} max={5} value={maxIdeaReviewIterations} onChange={(event) => setMaxIdeaReviewIterations(parseInt(event.target.value))} className="w-full" disabled={isPolling} />
                  </div>
                </div>
                <p className="text-xs text-muted-foreground">
                  {text('增加候选数或审查轮数会提升探索广度，也会增加千问调用时间与费用。默认值适合首次体验。', 'More candidates or review rounds broaden exploration but increase Qwen time and cost. The defaults are suitable for a first run.')}
                </p>
                <Button variant="outline" size="sm" onClick={testProvider} disabled={isPolling || isTestingProvider}>
                  {isTestingProvider ? <RefreshCw className="mr-2 h-4 w-4 animate-spin" /> : <Zap className="mr-2 h-4 w-4" />}{text('测试模型连接', 'Test model connection')}
                </Button>
              </div>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-3 pt-1">
            <Button onClick={checkAndGenerate} disabled={isLoading || isPolling || isCheckingSeed || !seedQuery.trim()} className="bg-amber-700 text-white hover:bg-amber-800">
              {isLoading || isCheckingSeed ? <RefreshCw className="mr-2 h-4 w-4 animate-spin" /> : <Sparkles className="mr-2 h-4 w-4" />}
              {isCheckingSeed
                ? text('正在检查相关文献...', 'Checking related literature...')
                : isLoading
                  ? text('正在启动研究...', 'Starting research...')
                  : text('检查并开始研究', 'Check and start research')}
            </Button>
            <p className="text-xs text-muted-foreground">
              {text('完整研究通常需要几分钟；切换页面不会中断服务器任务，可稍后从研究历史继续查看。', 'A full run usually takes several minutes. Leaving this page will not stop the server task; reopen it later from research history.')}
            </p>
          </div>

          {providerTestResult && (
            <div className={`rounded-md border border-l-4 bg-background p-3 ${providerTestResult.ok ? 'border-emerald-300 border-l-emerald-700' : 'border-red-300 border-l-red-700'}`}>
              <div className="flex items-center gap-2">
                {providerTestResult.ok ? <CheckCircle2 className="h-4 w-4 text-green-600" /> : <XCircle className="h-4 w-4 text-red-600" />}
                <span className={`text-sm font-medium ${providerTestResult.ok ? 'text-green-700' : 'text-red-700'}`}>
                  {providerTestResult.ok
                    ? text(`Provider 可用（${providerTestResult.latencyMs}ms）`, `Provider OK (${providerTestResult.latencyMs}ms)`)
                    : text(`错误：${providerTestResult.error}`, `Error: ${providerTestResult.error}`)}
                </span>
              </div>
            </div>
          )}
          {error && (<div className="rounded-md border border-l-4 border-red-300 border-l-red-700 bg-background p-3"><p className="text-sm font-medium text-red-800 dark:text-red-200">{error}</p></div>)}
        </CardContent>
      </Card>

      {session && (
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="text-lg">{text('本次研究进度', 'Research progress')}</CardTitle>
                {showDebugDetails && <p className="mt-1 font-mono text-xs text-muted-foreground">{session.id}</p>}
              </div>
              <Badge className={getStatusColor(session.status)}>{getStatusLabel(session.status)}</Badge>
            </div>
          </CardHeader>
          <CardContent>
            {session.status === 'completed' && (
              <div className="flex flex-wrap items-center gap-2 text-sm text-slate-700">
                <Badge variant="outline" className="border-emerald-300 bg-emerald-50 text-emerald-800">
                  {isLoadingCandidates
                    ? text('正在加载最终候选...', 'Loading final shortlist...')
                    : `${text('可选研究创意', 'Research ideas ready')}: ${candidates.length}`}
                </Badge>
              </div>
            )}

            <div className="mt-4">
              <PipelineProgress
                trace={trace}
                isPolling={isPolling}
                status={session.status}
                startedAt={session.startedAt}
                expandedStep={expandedStep}
                showTechnicalDetails={showDebugDetails}
                onToggleStep={setExpandedStep}
              />
            </div>

            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setShowDebugDetails(!showDebugDetails)
                if (showDebugDetails) setExpandedStep(null)
              }}
              className="mt-2 px-0 text-xs text-muted-foreground hover:bg-transparent hover:text-foreground"
            >
              {showDebugDetails ? <ChevronUp className="mr-1 h-3 w-3" /> : <ChevronDown className="mr-1 h-3 w-3" />}
              {text('技术详情', 'Technical details')}
            </Button>

            {literatureFailure && (
              <LiteratureFailureNotice
                summary={literatureFailure}
                isBusy={isLoading || isPolling}
                isSuggesting={isSuggestingSeed}
                onEditSeed={focusSeedEditor}
                onAskQwen={() => requestSeedSuggestions(literatureFailure.seedQuery, literatureFailure.code)}
                onResume={resumeSession}
              />
            )}

            {evidenceSummary && showDebugDetails && (
              <div className="mt-4">
                <EvidenceGateStatus summary={evidenceSummary} />
              </div>
            )}
            {pollingInterrupted && isPolling && (
              <div className="mt-4 flex items-start gap-2 border-l-4 border-amber-600 bg-amber-50 px-3 py-2 text-sm text-amber-950 dark:bg-amber-950/30 dark:text-amber-100">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <p>{text('网络连接刚才中断，任务仍在服务器运行；页面会自动重试，不要重复提交。', 'The connection was interrupted, but the task is still running on the server. This page will retry automatically; do not submit again.')}</p>
              </div>
            )}
            {session.status === 'completed' && !isLoadingCandidates && (candidateLoadFailed || candidates.length === 0) && (
              <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-l-4 border-amber-600 bg-amber-50 px-3 py-3 dark:bg-amber-950/30">
                <p className="text-sm font-medium text-amber-950 dark:text-amber-100">
                  {text('研究已经完成，但最终候选没有成功显示。可以重新加载结果，不会再次调用模型。', 'The research run completed, but the final shortlist did not load. Reloading will not call the model again.')}
                </p>
                <Button variant="outline" size="sm" onClick={() => loadSession(session.id)}>
                  <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
                  {text('重新加载结果', 'Reload results')}
                </Button>
              </div>
            )}
            {(session.status === 'awaiting_evidence' || session.status === 'awaiting_ideas') && !literatureFailure && (
              <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-l-4 border-amber-600 bg-amber-50 px-3 py-3">
                <div>
                  <p className="text-sm font-semibold text-amber-950">
                    {session.status === 'awaiting_evidence'
                      ? text('需要更多相关证据', 'More relevant evidence is required')
                      : text('至少需要两个通过审查的创意', 'Two approved ideas are required')}
                  </p>
                  <p className="mt-0.5 text-xs text-amber-900">
                    {session.qualityLoopSummary?.blockingReason || session.errorMessage}
                  </p>
                </div>
                <Button variant="outline" onClick={resumeSession} disabled={isLoading || isPolling}>
                  <RefreshCw className={`mr-2 h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
                  {text('继续', 'Resume')}
                </Button>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {literature.length > 0 && (
        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2 text-lg"><BookOpen className="h-5 w-5 text-blue-500" />{text('文献', 'Literature')} ({literature.length})</CardTitle></CardHeader>
          <CardContent>
            <div className="space-y-2 max-h-64 overflow-y-auto">
              {literature.map((item) => (
                <div key={item.id} className="p-3 rounded-md bg-white border border-slate-300 shadow-sm">
                  <div className="flex items-start justify-between">
                    <div className="flex-1">
                      <p className="text-sm font-medium">{item.title}</p>
                      <p className="text-xs text-muted-foreground">{item.authors.join(', ')} {item.year && `(${item.year})`}</p>
                    </div>
                    <Badge variant="outline" className="ml-2">{(item.relevanceScore * 100).toFixed(0)}%</Badge>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {candidates.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg">
              <Sparkles className="h-5 w-5 text-amber-500" />
              {text('已审查的创意候选', 'Reviewed Idea Shortlist')} ({candidates.length})
            </CardTitle>
            <CardDescription>{text('选择一个已审查的创意，继续生成研究计划。', 'Select one reviewed idea to continue into planning.')}</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {candidates.map((candidate, index) => (
                <div key={candidate.id} className="p-4 rounded-md border border-l-4 border-slate-300 border-l-amber-700 bg-white shadow-sm">
                  <div className="flex flex-wrap items-start justify-between gap-3 mb-3">
                    <div className="flex min-w-0 items-start gap-2">
                      <span className="text-lg font-bold text-amber-800">#{index + 1}</span>
                      <div className="min-w-0">
                        <h4 className="font-semibold text-slate-950">{candidate.title}</h4>
                        {candidate.problem && <p className="mt-1 text-sm leading-relaxed text-slate-700">{candidate.problem}</p>}
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <Badge className={getScoreColor(candidate.overallScore)}>{text('评分', 'Score')}: {candidate.overallScore.toFixed(1)}</Badge>
                      <Badge variant="outline" className="border-slate-300 bg-white text-xs text-slate-700">
                        {text('证据', 'Evidence')}: {(candidate.referenceSupport ?? 0).toFixed(1)}
                      </Badge>
                    </div>
                  </div>

                  <div className="grid gap-3 md:grid-cols-3">
                    {candidate.hypothesisStatement && (
                      <div className="rounded border border-slate-200 bg-slate-50 p-3">
                        <p className="text-xs font-semibold uppercase text-slate-500">{text('研究假设', 'Hypothesis')}</p>
                        <p className="mt-1 text-sm leading-relaxed text-slate-800">{candidate.hypothesisStatement}</p>
                      </div>
                    )}
                    {(candidate.proposedMethod || candidate.keyInsight) && (
                      <div className="rounded border border-slate-200 bg-slate-50 p-3">
                        <p className="text-xs font-semibold uppercase text-slate-500">{text('方法', 'Method')}</p>
                        <p className="mt-1 text-sm leading-relaxed text-slate-800">{candidate.proposedMethod || candidate.keyInsight}</p>
                      </div>
                    )}
                    {candidate.expectedOutcome && (
                      <div className="rounded border border-slate-200 bg-slate-50 p-3">
                        <p className="text-xs font-semibold uppercase text-slate-500">{text('预期结果', 'Expected Outcome')}</p>
                        <p className="mt-1 text-sm leading-relaxed text-slate-800">{candidate.expectedOutcome}</p>
                      </div>
                    )}
                  </div>

                  <div className="mt-3 flex flex-wrap gap-2">
                    <Badge variant="outline" className="border-purple-200 bg-purple-50 text-purple-800">{text('创新性', 'Novelty')} {candidate.novelty.toFixed(1)}</Badge>
                    <Badge variant="outline" className="border-blue-200 bg-blue-50 text-blue-800">{text('可行性', 'Feasibility')} {candidate.feasibility.toFixed(1)}</Badge>
                    <Badge variant="outline" className="border-emerald-200 bg-emerald-50 text-emerald-800">{text('影响力', 'Impact')} {candidate.impact.toFixed(1)}</Badge>
                    <Badge variant="outline" className="border-cyan-200 bg-cyan-50 text-cyan-800">{text('可验证性', 'Validation')} {candidate.experimentSpecificity.toFixed(1)}</Badge>
                  </div>

                  <Button variant="ghost" size="sm" onClick={() => setExpandedCandidate(expandedCandidate === candidate.id ? null : candidate.id)} className="mt-2 px-0 text-xs text-slate-600 hover:bg-transparent hover:text-slate-950">
                    {expandedCandidate === candidate.id
                      ? <><ChevronUp className="h-3 w-3 mr-1" /> {text('收起审查说明', 'Hide review notes')}</>
                      : <><ChevronDown className="h-3 w-3 mr-1" /> {text('查看审查说明', 'Review notes')}</>}
                  </Button>
                  {expandedCandidate === candidate.id && (
                    <div className="mb-3 p-3 bg-slate-50 rounded text-xs space-y-2 border border-slate-300">
                      {candidate.keyInsight && <p><span className="font-medium">{text('核心洞察', 'Core insight')}:</span> {candidate.keyInsight}</p>}
                      {candidate.overallRationale && <p className="font-medium text-slate-950 mb-1">{candidate.overallRationale}</p>}
                      {candidate.scoreBreakdown && Object.entries(candidate.scoreBreakdown).map(([k, entry]) => (
                        entry.rationale && entry.rationale !== 'Pending ranking' ? (
                          <p key={k}><span className="font-medium capitalize">{k}:</span> {entry.rationale}</p>
                        ) : null
                      ))}
                      {candidate.scoringMethod && candidate.scoringMethod !== 'pending' && (
                        <p className="text-muted-foreground">{text('评分方式', 'Scoring')}: {candidate.scoringMethod}</p>
                      )}
                      {(candidate.references?.length ?? 0) > 0 && (
                        <p className="text-muted-foreground">{text('证据引用', 'Evidence refs')}: {candidate.references?.slice(0, 6).join(', ')}</p>
                      )}
                      {candidate.scoringConfidence != null && (
                        <p className="text-muted-foreground">{text('置信度', 'Confidence')}: {(candidate.scoringConfidence * 100).toFixed(0)}%</p>
                      )}
                    </div>
                  )}
                  <div className="flex flex-wrap gap-2">
                    {session?.status === 'completed' && (
                      <Button
                        size="sm"
                        onClick={() => openPlanningForCandidate(candidate)}
                      >
                        <FileText className="h-4 w-4 mr-2" />
                        {text('用于计划生成', 'Use In Planning')}
                      </Button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Research Dossier Viewer */}
      {session?.status === 'completed' && candidates.length > 0 && (
        <DossierViewer sessionId={session.id} />
      )}
    </div>
  )
}
