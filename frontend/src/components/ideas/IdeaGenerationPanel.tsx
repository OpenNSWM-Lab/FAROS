import { useState, useEffect, useCallback, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
  Lightbulb,
  Play,
  CheckCircle2,
  XCircle,
  Clock,
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
  AlertTriangle
} from 'lucide-react'
import { PAPER_TYPES, getPaperTypeById } from '@/lib/models/providers'
import { summarizeEvidenceGate, type EvidenceGateSummary } from './evidenceGateSummary'

interface IdeaSession {
  id: string
  status: string
  createdAt?: string
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

const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

interface CandidateSelection {
  ideaSessionId: string
  ideaCandidateId: string
  ideaCandidateTitle: string
  ideaSeedQuery: string
}

function EvidenceGateStatus({ summary }: { summary: EvidenceGateSummary }) {
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
            Reviewer {summary.reviewerScore}
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
          <p className="text-xs font-semibold text-slate-800">LLM Scientist Evidence Review</p>
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
                    {dimension.paperCount ? ` · ${dimension.paperCount} papers` : ''}
                  </span>
                </Badge>
              ))}
            </div>
          )}
        </div>
      )}

      {summary.issues.length > 0 && (
        <div className="mt-3 space-y-1">
          <p className="text-xs font-semibold text-slate-800">Main issues</p>
          {summary.issues.map((issue) => (
            <p key={issue} className="text-xs text-slate-700">{issue}</p>
          ))}
        </div>
      )}

      {summary.repairQueries.length > 0 && (
        <div className="mt-3 space-y-1">
          <p className="text-xs font-semibold text-slate-800">Repair searches</p>
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
          <p className="text-xs font-semibold text-slate-800">Notes</p>
          {summary.warnings.map((warning) => (
            <p key={warning} className="text-xs text-slate-700">{warning}</p>
          ))}
        </div>
      )}
    </div>
  )
}

export function IdeaGenerationPanel({
  onCandidateSelected,
}: {
  onCandidateSelected?: (data: CandidateSelection) => void
}) {
  const navigate = useNavigate()
  const [seedQuery, setSeedQuery] = useState('')
  const [activeProvider, setActiveProvider] = useState('moonshot')
  const [activeModel, setActiveModel] = useState('moonshot-v1-8k')
  const [paperType, setPaperType] = useState('algorithm')
  const [maxCandidates, setMaxCandidates] = useState(5)
  const [maxIdeaReviewIterations, setMaxIdeaReviewIterations] = useState(2)
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
  const evidenceSummary = useMemo(() => summarizeEvidenceGate(trace?.steps), [trace])

  useEffect(() => {
    loadSessionHistory()
    loadActiveLlmFromSettings()
    // Restore last active session from localStorage
    const lastSessionId = localStorage.getItem('idea_active_session_id')
    if (lastSessionId) {
      loadSession(lastSessionId)
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
    try {
      const sessionResponse = await fetch(`${API_BASE}/api/v1/ideas/sessions/${sessionId}`)
      if (!sessionResponse.ok) throw new Error('Session not found')
      const sessionData = await sessionResponse.json()
      setSession(sessionData)
      setShowDebugDetails(false)
      setSeedQuery(sessionData.config.seedQuery)
      setPaperType(sessionData.config.paperType || 'algorithm')
      setMaxCandidates(sessionData.config.maxCandidates)
      setMaxIdeaReviewIterations(sessionData.config.maxReviewIterations || 2)
      const traceResponse = await fetch(`${API_BASE}/api/v1/ideas/sessions/${sessionId}/trace`)
      if (traceResponse.ok) { setTrace(await traceResponse.json()) }
      const litResponse = await fetch(`${API_BASE}/api/v1/ideas/sessions/${sessionId}/literature`)
      if (litResponse.ok) { const d = await litResponse.json(); setLiterature(d.items || []) }
      const candResponse = await fetch(`${API_BASE}/api/v1/ideas/sessions/${sessionId}/candidates`)
      if (candResponse.ok) { const d = await candResponse.json(); setCandidates(d.candidates || []) }
      setShowHistory(false)
      setError(null)
      localStorage.setItem('idea_active_session_id', sessionId)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load session')
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

  const generateIdeas = async () => {
    if (!seedQuery.trim()) { setError('Please enter a research topic'); return }
    setIsLoading(true); setError(null); setSession(null); setTrace(null); setCandidates([]); setLiterature([]); setShowDebugDetails(false)
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

  const pollSession = useCallback(async () => {
    if (!session?.id || !isPolling) return
    try {
      const sessionResponse = await fetch(`${API_BASE}/api/v1/ideas/sessions/${session.id}`)
      const sessionData = await sessionResponse.json()
      setSession(sessionData)
      const traceResponse = await fetch(`${API_BASE}/api/v1/ideas/sessions/${session.id}/trace`)
      setTrace(await traceResponse.json())
      const litResponse = await fetch(`${API_BASE}/api/v1/ideas/sessions/${session.id}/literature`)
      const litData = await litResponse.json()
      setLiterature(litData.items || [])
      if (sessionData.status === 'completed' || sessionData.status === 'failed') {
        setIsPolling(false)
        if (sessionData.status === 'completed') {
          const candResponse = await fetch(`${API_BASE}/api/v1/ideas/sessions/${session.id}/candidates`)
          const candData = await candResponse.json()
          setCandidates(candData.candidates || [])
        }
        loadSessionHistory()
      }
    } catch (err) { console.error('Polling error:', err) }
  }, [session?.id, isPolling])

  useEffect(() => {
    if (!isPolling) return
    const interval = setInterval(pollSession, 2000)
    return () => clearInterval(interval)
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
      case 'pending': return 'bg-amber-600 text-white'
      default: return 'bg-slate-600 text-white'
    }
  }

  const getStepIcon = (status: string) => {
    switch (status) {
      case 'ok': return <CheckCircle2 className="h-4 w-4 text-green-500" />
      case 'failed': return <XCircle className="h-4 w-4 text-red-500" />
      default: return <Clock className="h-4 w-4 text-gray-400" />
    }
  }

  const getScoreColor = (score: number) => {
    if (score >= 8) return 'bg-emerald-700 text-white'
    if (score >= 6) return 'bg-amber-600 text-white'
    return 'bg-red-700 text-white'
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2 text-base">
              <History className="h-4 w-4 text-slate-500" />
              Session History
            </CardTitle>
            <Button variant="ghost" size="sm" onClick={() => setShowHistory(!showHistory)}>
              {showHistory ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
              <span className="ml-1">{sessionHistory.length} sessions</span>
            </Button>
          </div>
        </CardHeader>
        {showHistory && (
          <CardContent>
            {isLoadingHistory ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground"><RefreshCw className="h-4 w-4 animate-spin" /> Loading...</div>
            ) : sessionHistory.length === 0 ? (
              <p className="text-sm text-muted-foreground">No previous sessions</p>
            ) : (
              <div className="space-y-2 max-h-48 overflow-y-auto">
                {sessionHistory.map((s) => (
                  <div key={s.id} className={`p-2 rounded border cursor-pointer hover:bg-slate-50 ${session?.id === s.id ? 'border-amber-600 bg-white shadow-sm ring-1 ring-amber-600' : 'border-slate-300 bg-white'}`} onClick={() => loadSession(s.id)}>
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium truncate flex-1">{s.config.seedQuery.slice(0, 50)}{s.config.seedQuery.length > 50 ? '...' : ''}</span>
                      <Badge className={getStatusColor(s.status)} variant="outline">{s.status}</Badge>
                    </div>
                    <div className="flex items-center gap-2 mt-1 text-xs text-muted-foreground">
                      <span>{s.id}</span><span>•</span><span>{new Date(s.createdAt).toLocaleDateString()}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
            <Button variant="outline" size="sm" onClick={loadSessionHistory} className="mt-2"><RefreshCw className="h-3 w-3 mr-1" /> Refresh</Button>
          </CardContent>
        )}
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Lightbulb className="h-5 w-5 text-amber-500" />Idea Generation</CardTitle>
          <CardDescription>Generate novel research ideas using AI-powered literature analysis</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <label className="text-sm font-medium">Research Topic / Seed Query</label>
            <textarea value={seedQuery} onChange={(e) => setSeedQuery(e.target.value)} placeholder="e.g., graph neural networks for recommendation systems" className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm min-h-[80px] focus:ring-2 focus:ring-amber-500" disabled={isPolling} />
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <div className="flex items-center justify-between gap-2">
                <label className="text-sm font-medium">LLM Provider</label>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => navigate('/settings/providers')}
                  disabled={isPolling}
                >
                  <Settings className="h-4 w-4 mr-2" />
                  Configure LLM
                </Button>
              </div>
              <div className="rounded-md border border-slate-400 bg-white px-3 py-2 text-sm">
                <p className="text-xs font-medium text-slate-500">Active provider / model</p>
                <p className="mt-1 font-mono text-slate-950">{activeProvider} / {activeModel}</p>
              </div>
              <p className="text-xs text-slate-600">API key, Base URL, active provider, and model are configured in Settings.</p>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Paper Type</label>
              <select value={paperType} onChange={(e) => setPaperType(e.target.value)} className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm" disabled={isPolling}>
                {PAPER_TYPES.map((pt) => (<option key={pt.id} value={pt.id}>{pt.name}</option>))}
              </select>
              <p className="text-xs text-muted-foreground">{getPaperTypeById(paperType)?.description}</p>
            </div>
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">Max Candidates: {maxCandidates}</label>
            <input type="range" min={1} max={10} value={maxCandidates} onChange={(e) => setMaxCandidates(parseInt(e.target.value))} className="w-full" disabled={isPolling} />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">Idea Review Iterations: {maxIdeaReviewIterations}</label>
            <input type="range" min={1} max={5} value={maxIdeaReviewIterations} onChange={(e) => setMaxIdeaReviewIterations(parseInt(e.target.value))} className="w-full" disabled={isPolling} />
          </div>
          <div className="flex gap-3 pt-2">
            <Button variant="outline" onClick={testProvider} disabled={isPolling || isTestingProvider}>
              {isTestingProvider ? <RefreshCw className="h-4 w-4 animate-spin mr-2" /> : <Zap className="h-4 w-4 mr-2" />}Test Provider
            </Button>
            <Button onClick={generateIdeas} disabled={isLoading || isPolling || !seedQuery.trim()} className="bg-amber-700 text-white hover:bg-amber-800">
              {isLoading ? <RefreshCw className="h-4 w-4 animate-spin mr-2" /> : <Sparkles className="h-4 w-4 mr-2" />}Generate Ideas
            </Button>
          </div>
          {providerTestResult && (
            <div className={`p-3 rounded-md bg-white ${providerTestResult.ok ? 'border border-l-4 border-emerald-300 border-l-emerald-700' : 'border border-l-4 border-red-300 border-l-red-700'}`}>
              <div className="flex items-center gap-2">
                {providerTestResult.ok ? <CheckCircle2 className="h-4 w-4 text-green-600" /> : <XCircle className="h-4 w-4 text-red-600" />}
                <span className={`text-sm font-medium ${providerTestResult.ok ? 'text-green-700' : 'text-red-700'}`}>
                  {providerTestResult.ok ? `Provider OK (${providerTestResult.latencyMs}ms)` : `Error: ${providerTestResult.error}`}
                </span>
              </div>
            </div>
          )}
          {error && (<div className="p-3 rounded-md bg-white border border-l-4 border-red-300 border-l-red-700"><p className="text-sm font-medium text-red-800">{error}</p></div>)}
        </CardContent>
      </Card>

      {session && (
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle className="text-lg">Session: {session.id}</CardTitle>
              <Badge className={getStatusColor(session.status)}>{session.status}</Badge>
            </div>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap items-center gap-2 text-sm text-slate-700">
              <Badge variant="outline" className="border-emerald-300 bg-emerald-50 text-emerald-800">
                Final ideas: {session.finalCandidateIds?.length ?? candidates.length}
              </Badge>
              {(session.hiddenCandidateIds?.length ?? 0) > 0 && (
                <Badge variant="outline" className="border-slate-300 bg-white text-slate-700">
                  Internally filtered: {session.hiddenCandidateIds?.length}
                </Badge>
              )}
              {(session.rejectedCandidateIds?.length ?? 0) > 0 && (
                <Badge variant="outline" className="border-amber-300 bg-amber-50 text-amber-800">
                  Repaired/rejected: {session.rejectedCandidateIds?.length}
                </Badge>
              )}
            </div>

            {(evidenceSummary || (trace && trace.steps.length > 0)) && (
              <div className="mt-4">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setShowDebugDetails(!showDebugDetails)}
                  className="px-0 text-xs text-slate-600 hover:bg-transparent hover:text-slate-950"
                >
                  {showDebugDetails ? <ChevronUp className="h-3 w-3 mr-1" /> : <ChevronDown className="h-3 w-3 mr-1" />}
                  Developer diagnostics
                </Button>
                {showDebugDetails && (
                  <div className="mt-3 space-y-4">
                    {evidenceSummary && <EvidenceGateStatus summary={evidenceSummary} />}
                    {trace && trace.steps.length > 0 && (
                      <div className="space-y-2">
                        <h4 className="text-sm font-medium mb-3">Pipeline Steps</h4>
                        <div className="space-y-1">
                          {trace.steps.map((step, i) => (
                            <div key={i}>
                              <div className="flex items-center gap-3 p-2 rounded border border-slate-300 bg-white cursor-pointer hover:bg-slate-50" onClick={() => setExpandedStep(expandedStep === i ? null : i)}>
                                {getStepIcon(step.status)}
                                <span className="text-sm font-medium flex-1">{step.name}</span>
                                <span className="text-xs text-muted-foreground">{step.durationSeconds.toFixed(1)}s</span>
                                {step.error && <span className="text-xs text-red-500 truncate max-w-[200px]">{step.error}</span>}
                                {step.outputs && Object.keys(step.outputs).length > 0 && (
                                  expandedStep === i ? <ChevronUp className="h-3 w-3 text-slate-400" /> : <ChevronDown className="h-3 w-3 text-slate-400" />
                                )}
                              </div>
                              {expandedStep === i && step.outputs && (
                                <div className="ml-8 mt-1 mb-2 p-2 rounded bg-white border text-xs space-y-1">
                                  {step.inputs && Object.keys(step.inputs).length > 0 && (
                                    <div><span className="font-medium text-slate-500">Inputs:</span> {Object.entries(step.inputs).map(([k, v]) => <span key={k} className="ml-1 text-slate-600">{k}={typeof v === 'string' ? v : JSON.stringify(v)}</span>)}</div>
                                  )}
                                  {Object.entries(step.outputs).filter(([k]) => k !== 'llmLatencyMs').map(([key, val]) => (
                                    <div key={key}>
                                      <span className="font-medium text-amber-700">{key}:</span>{' '}
                                      <span className="text-slate-700">
                                        {Array.isArray(val) ? (val.length > 3 ? `[${val.slice(0, 3).map(v => typeof v === 'string' ? v : JSON.stringify(v)).join(', ')}... +${val.length - 3} more]` : JSON.stringify(val)) : typeof val === 'object' && val !== null ? JSON.stringify(val).slice(0, 200) : String(val).slice(0, 200)}
                                      </span>
                                    </div>
                                  ))}
                                  {Boolean(step.outputs.llmLatencyMs) && <div className="text-slate-400">LLM latency: {String(step.outputs.llmLatencyMs)}ms</div>}
                                </div>
                              )}
                            </div>
                          ))}
                        </div>
                        <div className="flex gap-4 mt-3 text-xs text-muted-foreground">
                          <span>Total: {trace.totalSteps}</span>
                          <span className="text-green-600">Success: {trace.successfulSteps}</span>
                          <span className="text-red-600">Failed: {trace.failedSteps}</span>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
            {isPolling && (<div className="flex items-center gap-2 mt-4 text-sm text-muted-foreground"><RefreshCw className="h-4 w-4 animate-spin" /> Processing...</div>)}
          </CardContent>
        </Card>
      )}

      {literature.length > 0 && (
        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2 text-lg"><BookOpen className="h-5 w-5 text-blue-500" />Literature ({literature.length})</CardTitle></CardHeader>
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
              Reviewed Idea Shortlist ({candidates.length})
            </CardTitle>
            <CardDescription>Select one reviewed idea to continue into planning.</CardDescription>
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
                      <Badge className={getScoreColor(candidate.overallScore)}>Score: {candidate.overallScore.toFixed(1)}</Badge>
                      <Badge variant="outline" className="border-slate-300 bg-white text-xs text-slate-700">
                        Evidence: {(candidate.referenceSupport ?? 0).toFixed(1)}
                      </Badge>
                    </div>
                  </div>

                  <div className="grid gap-3 md:grid-cols-3">
                    {candidate.hypothesisStatement && (
                      <div className="rounded border border-slate-200 bg-slate-50 p-3">
                        <p className="text-xs font-semibold uppercase text-slate-500">Hypothesis</p>
                        <p className="mt-1 text-sm leading-relaxed text-slate-800">{candidate.hypothesisStatement}</p>
                      </div>
                    )}
                    {(candidate.proposedMethod || candidate.keyInsight) && (
                      <div className="rounded border border-slate-200 bg-slate-50 p-3">
                        <p className="text-xs font-semibold uppercase text-slate-500">Method</p>
                        <p className="mt-1 text-sm leading-relaxed text-slate-800">{candidate.proposedMethod || candidate.keyInsight}</p>
                      </div>
                    )}
                    {candidate.expectedOutcome && (
                      <div className="rounded border border-slate-200 bg-slate-50 p-3">
                        <p className="text-xs font-semibold uppercase text-slate-500">Expected Outcome</p>
                        <p className="mt-1 text-sm leading-relaxed text-slate-800">{candidate.expectedOutcome}</p>
                      </div>
                    )}
                  </div>

                  <div className="mt-3 flex flex-wrap gap-2">
                    <Badge variant="outline" className="border-purple-200 bg-purple-50 text-purple-800">Novelty {candidate.novelty.toFixed(1)}</Badge>
                    <Badge variant="outline" className="border-blue-200 bg-blue-50 text-blue-800">Feasibility {candidate.feasibility.toFixed(1)}</Badge>
                    <Badge variant="outline" className="border-emerald-200 bg-emerald-50 text-emerald-800">Impact {candidate.impact.toFixed(1)}</Badge>
                    <Badge variant="outline" className="border-cyan-200 bg-cyan-50 text-cyan-800">Validation {candidate.experimentSpecificity.toFixed(1)}</Badge>
                  </div>

                  <Button variant="ghost" size="sm" onClick={() => setExpandedCandidate(expandedCandidate === candidate.id ? null : candidate.id)} className="mt-2 px-0 text-xs text-slate-600 hover:bg-transparent hover:text-slate-950">
                    {expandedCandidate === candidate.id ? <><ChevronUp className="h-3 w-3 mr-1" /> Hide review notes</> : <><ChevronDown className="h-3 w-3 mr-1" /> Review notes</>}
                  </Button>
                  {expandedCandidate === candidate.id && (
                    <div className="mb-3 p-3 bg-slate-50 rounded text-xs space-y-2 border border-slate-300">
                      {candidate.keyInsight && <p><span className="font-medium">Core insight:</span> {candidate.keyInsight}</p>}
                      {candidate.overallRationale && <p className="font-medium text-slate-950 mb-1">{candidate.overallRationale}</p>}
                      {candidate.scoreBreakdown && Object.entries(candidate.scoreBreakdown).map(([k, entry]) => (
                        entry.rationale && entry.rationale !== 'Pending ranking' ? (
                          <p key={k}><span className="font-medium capitalize">{k}:</span> {entry.rationale}</p>
                        ) : null
                      ))}
                      {candidate.scoringMethod && candidate.scoringMethod !== 'pending' && (
                        <p className="text-muted-foreground">Scoring: {candidate.scoringMethod}</p>
                      )}
                      {(candidate.references?.length ?? 0) > 0 && (
                        <p className="text-muted-foreground">Evidence refs: {candidate.references?.slice(0, 6).join(', ')}</p>
                      )}
                      {candidate.scoringConfidence != null && (
                        <p className="text-muted-foreground">Confidence: {(candidate.scoringConfidence * 100).toFixed(0)}%</p>
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
                        Use In Planning
                      </Button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Module Navigation Links */}
      <div className="flex justify-center gap-4 pt-2">
        <Button variant="ghost" size="sm" onClick={() => window.location.href = '/research/planning'} className="text-muted-foreground">
          <FileText className="h-4 w-4 mr-1" /> Planning Module
        </Button>
        <Button variant="ghost" size="sm" onClick={() => window.location.href = '/runs'} className="text-muted-foreground">
          <Play className="h-4 w-4 mr-1" /> Runs Module
        </Button>
        <Button variant="ghost" size="sm" onClick={() => window.location.href = '/research/workflows'} className="text-muted-foreground">
          <Sparkles className="h-4 w-4 mr-1" /> Workflows
        </Button>
      </div>
    </div>
  )
}
