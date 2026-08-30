import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  FileCheck2,
  FlaskConical,
  History,
  Loader2,
  Plus,
  PlayCircle,
  RefreshCw,
  RotateCcw,
  Trash2,
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
  planId?: string
  startedAt?: string
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

type MetricDirection = 'maximize' | 'minimize'

interface MetricGuardrail {
  name: string
  direction: MetricDirection
  threshold: number
}

interface OptimizationPolicy {
  primaryMetric: string
  direction: MetricDirection
  minimumImprovement: number
  guardrails: MetricGuardrail[]
}

interface PrimaryObjectiveEvaluation {
  name: string
  direction: MetricDirection
  previous?: number
  current?: number
  improvement?: number
  minimumImprovement: number
  comparable: boolean
  satisfied?: boolean
}

interface GuardrailEvaluation extends MetricGuardrail {
  current?: number
  satisfied: boolean
}

interface ExperimentFeedbackResponse {
  feedbackId: string
  createdAt: string
  runId: string
  sourceArtifacts: Record<string, string>
  qualityAssessment: QualityAssessment
  iterationDecision: {
    decision: 'accept_results' | 'revise_plan' | 'rerun_experiment' | 'needs_human'
    rationale: string
    targetSections: string[]
    metricDeltas: MetricDelta[]
    nextActions: string[]
    optimizationPolicy?: OptimizationPolicy
    primaryObjective?: PrimaryObjectiveEvaluation
    guardrailEvaluations?: GuardrailEvaluation[]
    guardrailViolations?: string[]
    benchmarkComparable?: boolean
  }
  planFeedback: {
    requested: boolean
    applied: boolean
    packageId?: string
    targetSections: string[]
    reason: string
  }
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

const decisionLabel: Record<ExperimentFeedbackResponse['iterationDecision']['decision'], string> = {
  accept_results: 'Accept results',
  revise_plan: 'Revise plan',
  rerun_experiment: 'Rerun experiment',
  needs_human: 'Human decision',
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

export function ExperimentFeedbackPanel() {
  const [runs, setRuns] = useState<CompletedRun[]>([])
  const [runsLoading, setRunsLoading] = useState(true)
  const [selectedRunId, setSelectedRunId] = useState('')
  const [planPackageId, setPlanPackageId] = useState('')
  const [applyToPlan, setApplyToPlan] = useState(false)
  const [auditing, setAuditing] = useState(false)
  const [actionLoading, setActionLoading] = useState<'revise' | 'next' | ''>('')
  const [actionMessage, setActionMessage] = useState('')
  const [error, setError] = useState('')
  const [result, setResult] = useState<ExperimentFeedbackResponse | null>(null)
  const [history, setHistory] = useState<ExperimentFeedbackHistory[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [planRevised, setPlanRevised] = useState(false)
  const [nextRunId, setNextRunId] = useState('')
  const [optimizationEnabled, setOptimizationEnabled] = useState(false)
  const [primaryMetric, setPrimaryMetric] = useState('')
  const [primaryDirection, setPrimaryDirection] = useState<MetricDirection>('minimize')
  const [minimumImprovement, setMinimumImprovement] = useState('0')
  const [guardrails, setGuardrails] = useState<Array<{
    id: number
    name: string
    direction: MetricDirection
    threshold: string
  }>>([])

  const loadRuns = async () => {
    setRunsLoading(true)
    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/reviews/reviewx/experiment-runs`)
      if (!response.ok) throw new Error('Failed to load completed runs.')
      const data = await response.json()
      setRuns(data.runs || [])
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Failed to load completed runs.')
    } finally {
      setRunsLoading(false)
    }
  }

  useEffect(() => {
    void loadRuns()
  }, [])

  const loadHistory = async (runId: string) => {
    if (!runId) {
      setHistory([])
      return
    }
    setHistoryLoading(true)
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/v1/reviews/reviewx/experiment-feedback/history?runId=${encodeURIComponent(runId)}&limit=8`,
      )
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
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            planPackageId: planPackageId || undefined,
            applyToPlanPackage: applyToPlan,
            optimizationPolicy: optimizationEnabled && primaryMetric.trim()
              ? {
                  primaryMetric: primaryMetric.trim(),
                  direction: primaryDirection,
                  minimumImprovement: Number(minimumImprovement) || 0,
                  guardrails: guardrails
                    .filter((guardrail) => guardrail.name.trim() && guardrail.threshold !== '')
                    .map((guardrail) => ({
                      name: guardrail.name.trim(),
                      direction: guardrail.direction,
                      threshold: Number(guardrail.threshold),
                    })),
                }
              : undefined,
          }),
        },
      )
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(formatError(data.detail))
      setResult(data)
      setPlanRevised(false)
      setNextRunId('')
      setActionMessage('')
      void loadHistory(selectedRunId)
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
      sourceArtifacts: record.sourceArtifacts,
      qualityAssessment: record.qualityAssessment,
      iterationDecision: record.iterationDecision,
      planFeedback: record.planFeedback,
    })
    setPlanRevised(Boolean(record.planRevision))
    setNextRunId(record.nextRunId || '')
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
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ generationMode: 'deterministic', reviewerMode: 'deterministic' }),
        },
      )
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(formatError(data.detail))
      setPlanRevised(true)
      setActionMessage(`Plan revised: ${data.revisionId || data.status}`)
      void loadHistory(result.runId)
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
      void loadHistory(result.runId)
      void loadRuns()
    } catch (actionError) {
      setActionMessage(actionError instanceof Error ? actionError.message : 'Failed to create next run.')
    } finally {
      setActionLoading('')
    }
  }

  const gate = result?.qualityAssessment.gateStatus
  const gateTone = gate === 'pass' ? 'secondary' : gate === 'fail' ? 'destructive' : 'default'

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
        <div className="grid gap-5 xl:grid-cols-[minmax(280px,0.9fr)_minmax(0,1.4fr)]">
          <div className="space-y-4">
            <div className="flex gap-2">
              <select
                value={selectedRunId}
                onChange={(event) => {
                  setSelectedRunId(event.target.value)
                  setResult(null)
                  setPlanRevised(false)
                  setNextRunId('')
                  setActionMessage('')
                  setError('')
                  void loadHistory(event.target.value)
                }}
                className="min-w-0 flex-1 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
                disabled={runsLoading}
              >
                <option value="">Select completed run...</option>
                {runs.map((run) => (
                  <option key={run.id} value={run.id}>
                    {run.config?.workplaceName || run.id} · {run.config?.model || 'unknown model'}
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

            <div className="border-t border-slate-200 pt-3">
              <label className="flex min-h-9 items-center gap-2 text-sm font-medium text-slate-800">
                <input
                  type="checkbox"
                  checked={optimizationEnabled}
                  onChange={(event) => setOptimizationEnabled(event.target.checked)}
                  className="h-4 w-4"
                />
                Controlled metric optimization
              </label>
              {optimizationEnabled && (
                <div className="mt-3 space-y-3">
                  <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_110px_110px] xl:grid-cols-1 2xl:grid-cols-[minmax(0,1fr)_110px_110px]">
                    <input
                      value={primaryMetric}
                      onChange={(event) => setPrimaryMetric(event.target.value)}
                      placeholder="Primary metric"
                      className="min-w-0 rounded-md border border-slate-300 px-3 py-2 text-sm"
                    />
                    <select
                      value={primaryDirection}
                      onChange={(event) => setPrimaryDirection(event.target.value as MetricDirection)}
                      className="rounded-md border border-slate-300 bg-white px-2 py-2 text-sm"
                      aria-label="Primary metric direction"
                    >
                      <option value="minimize">Minimize</option>
                      <option value="maximize">Maximize</option>
                    </select>
                    <input
                      type="number"
                      min="0"
                      step="any"
                      value={minimumImprovement}
                      onChange={(event) => setMinimumImprovement(event.target.value)}
                      placeholder="Min gain"
                      className="min-w-0 rounded-md border border-slate-300 px-3 py-2 text-sm"
                      aria-label="Minimum improvement"
                    />
                  </div>

                  <div className="flex items-center justify-between">
                    <div className="text-xs font-semibold uppercase text-slate-600">Hard guardrails</div>
                    <Button
                      type="button"
                      variant="outline"
                      size="icon"
                      className="h-8 w-8"
                      title="Add metric guardrail"
                      onClick={() => setGuardrails((items) => [
                        ...items,
                        { id: Date.now(), name: '', direction: 'maximize', threshold: '' },
                      ])}
                    >
                      <Plus className="h-4 w-4" />
                    </Button>
                  </div>
                  {guardrails.length === 0 ? (
                    <div className="text-xs text-slate-500">No guardrail configured.</div>
                  ) : (
                    <div className="space-y-2">
                      {guardrails.map((guardrail) => (
                        <div key={guardrail.id} className="grid grid-cols-[minmax(0,1fr)_100px_90px_32px] gap-2">
                          <input
                            value={guardrail.name}
                            onChange={(event) => setGuardrails((items) => items.map((item) => (
                              item.id === guardrail.id ? { ...item, name: event.target.value } : item
                            )))}
                            placeholder="Metric"
                            className="min-w-0 rounded-md border border-slate-300 px-2 py-1.5 text-xs"
                          />
                          <select
                            value={guardrail.direction}
                            onChange={(event) => setGuardrails((items) => items.map((item) => (
                              item.id === guardrail.id
                                ? { ...item, direction: event.target.value as MetricDirection }
                                : item
                            )))}
                            className="rounded-md border border-slate-300 bg-white px-1.5 py-1.5 text-xs"
                            aria-label={`${guardrail.name || 'Guardrail'} direction`}
                          >
                            <option value="maximize">At least</option>
                            <option value="minimize">At most</option>
                          </select>
                          <input
                            type="number"
                            step="any"
                            value={guardrail.threshold}
                            onChange={(event) => setGuardrails((items) => items.map((item) => (
                              item.id === guardrail.id ? { ...item, threshold: event.target.value } : item
                            )))}
                            placeholder="Limit"
                            className="min-w-0 rounded-md border border-slate-300 px-2 py-1.5 text-xs"
                            aria-label={`${guardrail.name || 'Guardrail'} threshold`}
                          />
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            className="h-8 w-8"
                            title="Remove metric guardrail"
                            onClick={() => setGuardrails((items) => items.filter((item) => item.id !== guardrail.id))}
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
            <Button
              className="w-full"
              onClick={() => void runAudit()}
              disabled={!selectedRunId || auditing || (optimizationEnabled && !primaryMetric.trim())}
            >
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
                            {decisionLabel[record.iterationDecision.decision]}
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

                {result.iterationDecision.primaryObjective && (
                  <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
                    <div className="rounded-md border border-slate-200 px-3 py-3">
                      <div className="text-xs font-semibold uppercase text-slate-600">Primary objective</div>
                      <div className="mt-2 flex items-center justify-between gap-3">
                        <div className="min-w-0 truncate text-sm font-medium text-slate-900">
                          {result.iterationDecision.primaryObjective.name}
                        </div>
                        <Badge variant={result.iterationDecision.primaryObjective.satisfied === false ? 'destructive' : 'secondary'}>
                          {result.iterationDecision.primaryObjective.comparable
                            ? result.iterationDecision.primaryObjective.satisfied ? 'Met' : 'Missed'
                            : 'Baseline only'}
                        </Badge>
                      </div>
                      <div className="mt-2 text-xs text-slate-600">
                        {result.iterationDecision.primaryObjective.previous ?? 'n/a'} →{' '}
                        {result.iterationDecision.primaryObjective.current ?? 'n/a'}
                        {result.iterationDecision.primaryObjective.improvement !== undefined
                          ? ` · gain ${result.iterationDecision.primaryObjective.improvement}`
                          : ''}
                      </div>
                    </div>
                    <div className="rounded-md border border-slate-200 px-3 py-3">
                      <div className="flex items-center justify-between gap-3">
                        <div className="text-xs font-semibold uppercase text-slate-600">Guardrails</div>
                        <Badge variant={result.iterationDecision.guardrailViolations?.length ? 'destructive' : 'secondary'}>
                          {result.iterationDecision.guardrailViolations?.length ? 'Violation' : 'All satisfied'}
                        </Badge>
                      </div>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {(result.iterationDecision.guardrailEvaluations || []).length === 0 ? (
                          <span className="text-xs text-slate-500">No guardrails evaluated.</span>
                        ) : result.iterationDecision.guardrailEvaluations?.map((guardrail) => (
                          <Badge key={guardrail.name} variant={guardrail.satisfied ? 'outline' : 'destructive'}>
                            {guardrail.name}: {guardrail.current ?? 'missing'}{' '}
                            {guardrail.direction === 'maximize' ? '≥' : '≤'} {guardrail.threshold}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  </div>
                )}

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
                    </div>
                  </div>
                </div>

                {result.planFeedback.packageId && result.iterationDecision.decision !== 'needs_human' && (
                  <div className="flex flex-wrap items-center gap-2 border-t border-slate-200 pt-4">
                    {result.iterationDecision.decision !== 'accept_results' && (
                      <Button
                        variant="outline"
                        onClick={() => void revisePlan()}
                        disabled={!result.planFeedback.applied || planRevised || Boolean(actionLoading)}
                      >
                        {actionLoading === 'revise' ? (
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        ) : (
                          <RotateCcw className="mr-2 h-4 w-4" />
                        )}
                        {planRevised ? 'Plan revised' : 'Revise Plan'}
                      </Button>
                    )}
                    <Button
                      onClick={() => void createNextRun()}
                      disabled={
                        Boolean(actionLoading)
                        || (result.iterationDecision.decision === 'revise_plan' && !planRevised)
                      }
                    >
                      {actionLoading === 'next' ? (
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      ) : (
                        <PlayCircle className="mr-2 h-4 w-4" />
                      )}
                      {nextRunId ? 'Open Next Run' : 'Create Next Run'}
                    </Button>
                    {nextRunId && (
                      <Button asChild variant="ghost">
                        <Link to={`/runs/${nextRunId}`}>View {nextRunId}</Link>
                      </Button>
                    )}
                    {actionMessage && <div className="w-full text-xs text-slate-600">{actionMessage}</div>}
                    {!result.planFeedback.applied && result.iterationDecision.decision !== 'accept_results' && (
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
