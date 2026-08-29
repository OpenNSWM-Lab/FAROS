import { useState, useCallback } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
  FileText,
  Loader2,
  ChevronDown,
  ChevronUp,
  ShieldCheck,
  AlertTriangle,
  FlaskConical,
  Target,
  BookOpen,
  Lightbulb,
} from 'lucide-react'
import { useReviewLocale } from '@/lib/reviewLocale'

const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

interface DossierViewerProps {
  sessionId: string
}

interface DossierEvidence {
  id?: string
  title?: string
  evidenceTier?: string
  summary?: string
  authors?: string[]
  year?: number
  source?: string
  relevanceScore?: number
  verified?: boolean
}

interface DossierHypothesis {
  id?: string
  statement?: string
  scores?: Record<string, number>
  confidence?: number
  rationale?: string
  derivationTrace?: string[]
  falsificationCriteria?: string[]
  confounders?: string[]
  alternativeExplanations?: string[]
  supportingEvidenceIds?: string[]
  counterEvidenceIds?: string[]
}

interface DossierPlanStep {
  id?: string
  order?: number
  title?: string
  objective?: string
  inputs?: string[]
  tools?: string[]
  method?: string[]
  outputs?: string[]
  metrics?: string[]
  stopConditions?: string[]
  dependencies?: string[]
  risks?: string[]
}

interface ResearchDossier {
  runId?: string
  degradationState?: string
  uncertainties?: string[]
  problemFrame?: {
    originalQuestion?: string
    scopedQuestion?: string
    definitions?: Record<string, string>
    observableVariables?: string[]
    assumptions?: string[]
    outOfScope?: string[]
    subQuestions?: string[]
  }
  evidenceMap?: {
    supportingEvidence?: DossierEvidence[]
    counterEvidence?: DossierEvidence[]
    contextualEvidence?: DossierEvidence[]
    unresolvedGaps?: string[]
  }
  hypotheses?: DossierHypothesis[]
  researchPlan?: {
    objective?: string
    steps?: DossierPlanStep[]
    expectedOutcomes?: string[]
  }
  generationTrace?: {
    providerName?: string
    model?: string
    startedAt?: string
  }
}

export function DossierViewer({ sessionId }: DossierViewerProps) {
  const { locale, text } = useReviewLocale()
  const [dossier, setDossier] = useState<ResearchDossier | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState(true)
  const [activeEvidenceTab, setActiveEvidenceTab] = useState<'supporting' | 'counter' | 'context'>('supporting')
  const [expandedHypothesis, setExpandedHypothesis] = useState<string | null>(null)

  const buildDossier = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${API_BASE}/api/v1/ideas/dossier`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sessionId, mode: 'deep' }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || `HTTP ${res.status}`)
      }
      const data = await res.json()
      setDossier(data.dossier as ResearchDossier)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : text('研究档案生成失败', 'Failed to build research dossier'))
    } finally {
      setLoading(false)
    }
  }, [sessionId, text])

  if (!dossier && !loading && !error) {
    return (
      <Card>
        <CardContent className="pt-6">
          <Button onClick={buildDossier} disabled={loading}>
            <FileText className="h-4 w-4 mr-2" />
            {text('生成研究档案', 'Build Research Dossier')}
          </Button>
        </CardContent>
      </Card>
    )
  }

  if (loading) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
          <span className="ml-3 text-slate-500">{text('正在生成研究档案...', 'Building Research Dossier...')}</span>
        </CardContent>
      </Card>
    )
  }

  if (error) {
    return (
      <Card className="border-red-200">
        <CardContent className="pt-6">
          <div className="flex items-center gap-2 text-red-700">
            <AlertTriangle className="h-5 w-5" />
            <span className="font-medium">{text('生成失败', 'Error')}</span>
          </div>
          <p className="mt-2 text-sm text-red-600">{error}</p>
          <Button size="sm" variant="outline" className="mt-3" onClick={buildDossier}>
            {text('重试', 'Retry')}
          </Button>
        </CardContent>
      </Card>
    )
  }

  if (!dossier) return null

  const pf = dossier.problemFrame || {}
  const em = dossier.evidenceMap || {}
  const hyps = dossier.hypotheses || []
  const plan = dossier.researchPlan || {}
  const trace = dossier.generationTrace || {}

  const evidenceTabs = [
    { key: 'supporting' as const, label: text('支持证据', 'Supporting'), items: em.supportingEvidence || [], color: 'text-emerald-700 bg-emerald-50' },
    { key: 'counter' as const, label: text('反向证据', 'Counter'), items: em.counterEvidence || [], color: 'text-red-700 bg-red-50' },
    { key: 'context' as const, label: text('背景证据', 'Context'), items: em.contextualEvidence || [], color: 'text-blue-700 bg-blue-50' },
  ]

  return (
    <Card>
      <CardHeader
        className="cursor-pointer select-none"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <FileText className="h-5 w-5 text-indigo-600" />
            <CardTitle className="text-base">{text('研究档案', 'Research Dossier')}</CardTitle>
            <Badge variant="secondary" className="text-xs">
              {trace.providerName || 'unknown'} / {trace.model || 'N/A'}
            </Badge>
            {dossier.degradationState && (
              <Badge variant="outline" className="text-xs text-amber-700 border-amber-300">
                {text('降级运行', 'Degraded')}: {dossier.degradationState}
              </Badge>
            )}
          </div>
          {expanded ? <ChevronUp className="h-4 w-4 text-slate-400" /> : <ChevronDown className="h-4 w-4 text-slate-400" />}
        </div>
        {dossier.runId && (
          <CardDescription className="text-xs">Run ID: {dossier.runId}</CardDescription>
        )}
      </CardHeader>

      {expanded && (
        <CardContent className="space-y-6">
          {/* ProblemFrame */}
          <section>
            <div className="mb-3 flex items-center gap-2">
              <Target className="h-4 w-4 text-indigo-600" />
              <h3 className="text-sm font-semibold text-slate-900">{text('问题定义', 'Problem Frame')}</h3>
            </div>
            <div className="space-y-3 rounded-lg border border-slate-200 bg-slate-50 p-4">
              <div>
                <p className="text-xs font-semibold uppercase text-slate-500">{text('原始问题', 'Original Question')}</p>
                <p className="mt-1 text-sm text-slate-800">{pf.originalQuestion || 'N/A'}</p>
              </div>
              <div>
                <p className="text-xs font-semibold uppercase text-slate-500">{text('聚焦后的问题', 'Scoped Question')}</p>
                <p className="mt-1 text-sm font-medium text-indigo-900">{pf.scopedQuestion || 'N/A'}</p>
              </div>
              {pf.definitions && Object.keys(pf.definitions).length > 0 && (
                <div>
                  <p className="text-xs font-semibold uppercase text-slate-500">{text('关键定义', 'Definitions')}</p>
                  <dl className="mt-1 space-y-1">
                    {Object.entries(pf.definitions).map(([term, def]) => (
                      <div key={term} className="flex gap-2 text-sm">
                        <dt className="font-medium text-slate-700">{term}:</dt>
                        <dd className="text-slate-600">{def as string}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
              )}
              {pf.observableVariables && pf.observableVariables.length > 0 && (
                <div>
                  <p className="text-xs font-semibold uppercase text-slate-500">{text('可观测变量', 'Observable Variables')}</p>
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    {pf.observableVariables.map((v: string, i: number) => (
                      <Badge key={i} variant="outline" className="text-xs">{v}</Badge>
                    ))}
                  </div>
                </div>
              )}
              {pf.assumptions && pf.assumptions.length > 0 && (
                <div>
                  <p className="text-xs font-semibold uppercase text-slate-500">{text('研究假设', 'Assumptions')}</p>
                  <ul className="mt-1 space-y-1">
                    {pf.assumptions.map((a: string, i: number) => (
                      <li key={i} className="text-sm text-slate-600">- {a}</li>
                    ))}
                  </ul>
                </div>
              )}
              {pf.outOfScope && pf.outOfScope.length > 0 && (
                <div>
                  <p className="text-xs font-semibold uppercase text-slate-500">{text('研究范围外', 'Out of Scope')}</p>
                  <ul className="mt-1 space-y-1">
                    {pf.outOfScope.map((s: string, i: number) => (
                      <li key={i} className="text-sm text-slate-500">- {s}</li>
                    ))}
                  </ul>
                </div>
              )}
              {pf.subQuestions && pf.subQuestions.length > 0 && (
                <div>
                  <p className="text-xs font-semibold uppercase text-slate-500">{text('子问题', 'Sub-Questions')}</p>
                  <ol className="mt-1 space-y-1">
                    {pf.subQuestions.map((q: string, i: number) => (
                      <li key={i} className="text-sm text-slate-600">{i + 1}. {q}</li>
                    ))}
                  </ol>
                </div>
              )}
            </div>
          </section>

          {/* Evidence Map */}
          <section>
            <div className="mb-3 flex items-center gap-2">
              <BookOpen className="h-4 w-4 text-indigo-600" />
              <h3 className="text-sm font-semibold text-slate-900">{text('证据图谱', 'Evidence Map')}</h3>
              <Badge variant="secondary" className="text-xs">
                {(em.supportingEvidence || []).length} + {(em.counterEvidence || []).length} + {(em.contextualEvidence || []).length}
              </Badge>
            </div>
            {/* Evidence tabs */}
            <div className="mb-3 flex gap-1.5">
              {evidenceTabs.map((tab) => (
                <button
                  key={tab.key}
                  onClick={() => setActiveEvidenceTab(tab.key)}
                  className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${
                    activeEvidenceTab === tab.key
                      ? tab.color
                      : 'text-slate-500 hover:bg-slate-100'
                  }`}
                >
                  {tab.label} ({tab.items.length})
                </button>
              ))}
            </div>
            {/* Active evidence list */}
            <div className="max-h-64 space-y-2 overflow-y-auto rounded-lg border border-slate-200 p-3">
              {evidenceTabs.find((t) => t.key === activeEvidenceTab)?.items.length === 0 ? (
                <p className="py-4 text-center text-sm text-slate-400">{text('此分类暂无证据', 'No evidence in this category')}</p>
              ) : (
                evidenceTabs
                  .find((t) => t.key === activeEvidenceTab)!
                  .items.map((ev: DossierEvidence, index: number) => (
                    <div key={ev.id || `${ev.title}-${index}`} className="rounded border border-slate-200 bg-white p-2.5">
                      <div className="flex items-start justify-between gap-2">
                        <p className="text-sm font-medium text-slate-800">{ev.title || text('无标题', 'Untitled')}</p>
                        <Badge variant="outline" className="text-xs shrink-0">
                          {ev.evidenceTier || 'N/A'}
                        </Badge>
                      </div>
                      <p className="mt-1 text-xs text-slate-500">{ev.summary || ''}</p>
                      <div className="mt-1.5 flex flex-wrap gap-2 text-xs text-slate-400">
                        {ev.authors && ev.authors.length > 0 && <span>{ev.authors.slice(0, 2).join(', ')}{ev.authors.length > 2 ? ' et al.' : ''}</span>}
                        {ev.year && <span>({ev.year})</span>}
                        {ev.source && <span>{ev.source}</span>}
                        {ev.relevanceScore != null && <span>{text('相关性', 'Relevance')}: {(ev.relevanceScore * 100).toFixed(0)}%</span>}
                        {ev.verified && <ShieldCheck className="h-3 w-3 text-emerald-600" />}
                      </div>
                    </div>
                  ))
              )}
            </div>
            {em.unresolvedGaps && em.unresolvedGaps.length > 0 && (
              <div className="mt-2">
                <p className="text-xs font-semibold uppercase text-amber-600">{text('待解决空白', 'Unresolved Gaps')}</p>
                <ul className="mt-1 space-y-1">
                  {em.unresolvedGaps.map((g: string, i: number) => (
                    <li key={i} className="text-sm text-amber-700">- {g}</li>
                  ))}
                </ul>
              </div>
            )}
          </section>

          {/* Hypotheses */}
          <section>
            <div className="mb-3 flex items-center gap-2">
              <Lightbulb className="h-4 w-4 text-indigo-600" />
              <h3 className="text-sm font-semibold text-slate-900">{text('研究假设', 'Hypotheses')}</h3>
              <Badge variant="secondary" className="text-xs">{hyps.length}</Badge>
            </div>
            <div className="space-y-3">
              {hyps.map((hyp: DossierHypothesis, idx: number) => (
                <div key={hyp.id || `hypothesis-${idx}`} className="rounded-lg border border-slate-200 p-4">
                  <div
                    className="flex cursor-pointer items-start justify-between gap-2"
                    onClick={() => {
                      const hypothesisId = hyp.id || `hypothesis-${idx}`
                      setExpandedHypothesis(expandedHypothesis === hypothesisId ? null : hypothesisId)
                    }}
                  >
                    <div className="flex-1">
                      <p className="text-sm font-medium text-slate-900">
                        {idx + 1}. {hyp.statement || hyp.id}
                      </p>
                      <div className="mt-1 flex flex-wrap gap-1.5">
                        {hyp.scores &&
                          Object.entries(hyp.scores).map(([dim, score]) => (
                            <Badge key={dim} variant="outline" className="text-xs">
                              {dim}: {(score as number).toFixed(2)}
                            </Badge>
                          ))}
                        {hyp.confidence != null && (
                          <Badge variant="outline" className="text-xs text-indigo-700">
                            {text('置信度', 'Confidence')}: {hyp.confidence.toFixed(2)}
                          </Badge>
                        )}
                      </div>
                    </div>
                    {expandedHypothesis === (hyp.id || `hypothesis-${idx}`) ? (
                      <ChevronUp className="h-4 w-4 shrink-0 text-slate-400" />
                    ) : (
                      <ChevronDown className="h-4 w-4 shrink-0 text-slate-400" />
                    )}
                  </div>

                  {expandedHypothesis === (hyp.id || `hypothesis-${idx}`) && (
                    <div className="mt-3 space-y-3 border-t border-slate-100 pt-3">
                      {hyp.rationale && (
                        <div>
                          <p className="text-xs font-semibold uppercase text-slate-500">{text('论证依据', 'Rationale')}</p>
                          <p className="mt-1 text-sm text-slate-600">{hyp.rationale}</p>
                        </div>
                      )}
                      {hyp.derivationTrace && hyp.derivationTrace.length > 0 && (
                        <div>
                          <p className="text-xs font-semibold uppercase text-slate-500">{text('推导过程', 'Derivation Trace')}</p>
                          <ol className="mt-1 space-y-1">
                            {hyp.derivationTrace.map((step: string, i: number) => (
                              <li key={i} className="text-sm text-slate-600">{i + 1}. {step}</li>
                            ))}
                          </ol>
                        </div>
                      )}
                      {hyp.falsificationCriteria && hyp.falsificationCriteria.length > 0 && (
                        <div>
                          <p className="text-xs font-semibold uppercase text-red-600">{text('证伪标准', 'Falsification Criteria')}</p>
                          <ul className="mt-1 space-y-1">
                            {hyp.falsificationCriteria.map((f: string, i: number) => (
                              <li key={i} className="text-sm text-red-700">- {f}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {hyp.confounders && hyp.confounders.length > 0 && (
                        <div>
                          <p className="text-xs font-semibold uppercase text-amber-600">{text('混杂因素', 'Confounders')}</p>
                          <ul className="mt-1 space-y-1">
                            {hyp.confounders.map((c: string, i: number) => (
                              <li key={i} className="text-sm text-amber-700">- {c}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {hyp.alternativeExplanations && hyp.alternativeExplanations.length > 0 && (
                        <div>
                          <p className="text-xs font-semibold uppercase text-slate-500">{text('替代解释', 'Alternative Explanations')}</p>
                          <ul className="mt-1 space-y-1">
                            {hyp.alternativeExplanations.map((a: string, i: number) => (
                              <li key={i} className="text-sm text-slate-600">- {a}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                      <div className="flex gap-4 text-xs text-slate-400">
                        <span>{text('支持证据', 'Supporting')}: {hyp.supportingEvidenceIds?.length || 0}</span>
                        <span>{text('反向证据', 'Counter')}: {hyp.counterEvidenceIds?.length || 0}</span>
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </section>

          {/* Research Plan */}
          <section>
            <div className="mb-3 flex items-center gap-2">
              <FlaskConical className="h-4 w-4 text-indigo-600" />
              <h3 className="text-sm font-semibold text-slate-900">{text('研究计划', 'Research Plan')}</h3>
              <Badge variant="secondary" className="text-xs">{plan.steps?.length || 0} {text('步', 'steps')}</Badge>
            </div>
            {plan.objective && (
              <p className="mb-3 text-sm text-slate-600">{plan.objective}</p>
            )}
            <div className="space-y-3">
              {(plan.steps || []).map((step: DossierPlanStep, idx: number) => (
                <div key={step.id || idx} className="rounded-lg border border-slate-200 p-4">
                  <div className="flex items-center gap-2">
                    <span className="flex h-6 w-6 items-center justify-center rounded-full bg-indigo-700 text-xs font-bold text-white">
                      {step.order || idx + 1}
                    </span>
                    <p className="text-sm font-semibold text-slate-900">{step.title}</p>
                  </div>
                  {step.objective && (
                    <p className="mt-2 text-sm text-slate-600">{step.objective}</p>
                  )}
                  <div className="mt-3 grid grid-cols-2 gap-3 text-xs">
                    {step.inputs && step.inputs.length > 0 && (
                      <div>
                        <p className="font-semibold text-slate-500">{text('输入', 'Inputs')}</p>
                        <ul className="mt-0.5 space-y-0.5">
                          {step.inputs.map((s: string, i: number) => (
                            <li key={i} className="text-slate-600">- {s}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {step.tools && step.tools.length > 0 && (
                      <div>
                        <p className="font-semibold text-slate-500">{text('工具', 'Tools')}</p>
                        <div className="mt-0.5 flex flex-wrap gap-1">
                          {step.tools.map((t: string, i: number) => (
                            <Badge key={i} variant="outline" className="text-xs">{t}</Badge>
                          ))}
                        </div>
                      </div>
                    )}
                    {step.method && step.method.length > 0 && (
                      <div>
                        <p className="font-semibold text-slate-500">{text('方法', 'Method')}</p>
                        <ul className="mt-0.5 space-y-0.5">
                          {step.method.map((s: string, i: number) => (
                            <li key={i} className="text-slate-600">- {s}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {step.outputs && step.outputs.length > 0 && (
                      <div>
                        <p className="font-semibold text-slate-500">{text('输出', 'Outputs')}</p>
                        <ul className="mt-0.5 space-y-0.5">
                          {step.outputs.map((s: string, i: number) => (
                            <li key={i} className="text-slate-600">- {s}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {step.metrics && step.metrics.length > 0 && (
                      <div>
                        <p className="font-semibold text-slate-500">{text('指标', 'Metrics')}</p>
                        <div className="mt-0.5 flex flex-wrap gap-1">
                          {step.metrics.map((m: string, i: number) => (
                            <Badge key={i} variant="outline" className="text-xs">{m}</Badge>
                          ))}
                        </div>
                      </div>
                    )}
                    {step.stopConditions && step.stopConditions.length > 0 && (
                      <div>
                        <p className="font-semibold text-amber-600">{text('停止条件', 'Stop Conditions')}</p>
                        <ul className="mt-0.5 space-y-0.5">
                          {step.stopConditions.map((s: string, i: number) => (
                            <li key={i} className="text-amber-700">- {s}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {step.dependencies && step.dependencies.length > 0 && (
                      <div>
                        <p className="font-semibold text-slate-500">{text('依赖', 'Dependencies')}</p>
                        <ul className="mt-0.5 space-y-0.5">
                          {step.dependencies.map((s: string, i: number) => (
                            <li key={i} className="text-slate-600">- {s}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {step.risks && step.risks.length > 0 && (
                      <div>
                        <p className="font-semibold text-red-600">{text('风险', 'Risks')}</p>
                        <ul className="mt-0.5 space-y-0.5">
                          {step.risks.map((r: string, i: number) => (
                            <li key={i} className="text-red-700">- {r}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
            {(plan.expectedOutcomes && plan.expectedOutcomes.length > 0) && (
              <div className="mt-3 rounded-lg bg-slate-50 p-3">
                <p className="text-xs font-semibold uppercase text-slate-500">{text('预期产出', 'Expected Outcomes')}</p>
                <ul className="mt-1 space-y-1">
                  {plan.expectedOutcomes.map((o: string, i: number) => (
                    <li key={i} className="text-sm text-slate-600">- {o}</li>
                  ))}
                </ul>
              </div>
            )}
          </section>

          {/* Uncertainties */}
          {dossier.uncertainties && dossier.uncertainties.length > 0 && (
            <section>
              <div className="mb-2 flex items-center gap-2">
                <AlertTriangle className="h-4 w-4 text-amber-600" />
                <h3 className="text-sm font-semibold text-slate-900">{text('不确定性', 'Uncertainties')}</h3>
              </div>
              <ul className="space-y-1 rounded-lg border border-amber-200 bg-amber-50 p-3">
                {dossier.uncertainties.map((u: string, i: number) => (
                  <li key={i} className="text-sm text-amber-800">- {u}</li>
                ))}
              </ul>
            </section>
          )}

          {/* Generation Trace */}
          {trace.providerName && (
            <section className="border-t border-slate-100 pt-3">
              <p className="text-xs text-slate-400">
                {text('生成模型', 'Generated by')} <span className="font-medium text-slate-600">{trace.providerName}</span>
                {trace.model && <span> / {trace.model}</span>}
                {trace.startedAt && <span> · {new Date(trace.startedAt).toLocaleString(locale)}</span>}
              </p>
            </section>
          )}
        </CardContent>
      )}
    </Card>
  )
}
