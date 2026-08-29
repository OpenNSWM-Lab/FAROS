import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  AlertTriangle,
  BookOpen,
  CheckCircle2,
  ClipboardList,
  ChevronDown,
  FileJson,
  GitBranch,
  Layers3,
  Lightbulb,
  MessageSquareText,
  Network,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  UserCheck,
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  createPlanPackageFromIdeaSession,
  addPlanPackageFeedback,
  approvePlanPackageWithMode,
  getPlanPackage,
  getPlanPackageByIdeaSession,
  getPlanPackagePresentation,
  getPlanPackagePresentationByIdeaSession,
  revisePlanPackage,
  type PlanEvidenceRef,
  type PlanGapItem,
  type PlanHumanFeedback,
  type PlanLiteraturePaperSummary,
  type PlanPackage,
  type PlanPackagePresentation,
  type PlanMetaReview,
  type PlanQualityGate,
  type PlanReadableStage,
  type PlanReviewerReport,
  type PlanStage,
} from '@/components/plans/planPackageApi'
import { useReviewLocale } from '@/lib/reviewLocale'

type GenerationMode = 'hybrid' | 'deterministic'
type ReviewerMode = 'deterministic' | 'hybrid'

const DEFAULT_REVIEWER_MODE: ReviewerMode = 'hybrid'
const PLAN_RECOVERY_INTERVAL_MS = 3000
const PLAN_RECOVERY_TIMEOUT_MS = 8 * 60 * 1000
const PLAN_CREATION_MARKER_TTL_MS = 20 * 60 * 1000
const PLAN_CREATION_MARKER_PREFIX = 'faros:plan-creation:'

const EMPTY_GATE: PlanQualityGate = {
  schemaValid: false,
  evidenceValid: false,
  topicRelevant: false,
  citationFaithful: false,
  planSpecific: false,
  agentApproved: false,
  humanApproved: false,
  downstreamReady: false,
  implementationReady: false,
  overallScore: 0,
  reviewDecision: 'draft',
  warnings: [],
  errors: [],
}

function statusVariant(ok: boolean) {
  return ok ? 'border-emerald-500 bg-white text-emerald-800 shadow-sm' : 'border-amber-500 bg-white text-amber-900 shadow-sm'
}

function compactValue(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (Array.isArray(value)) return value.map(compactValue).filter(Boolean).join(', ')
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function uniqueStrings(values: Array<string | null | undefined>): string[] {
  return Array.from(
    new Set(
      values
        .map((value) => value?.trim())
        .filter((value): value is string => Boolean(value)),
    ),
  )
}

function shortId(id?: string | null) {
  if (!id) return '-'
  return id.length > 18 ? `${id.slice(0, 10)}...${id.slice(-6)}` : id
}

function isNotFoundError(error: unknown) {
  return error instanceof Error && /not found|\b404\b/i.test(error.message)
}

function isNetworkInterruption(error: unknown) {
  return error instanceof TypeError
    || (error instanceof Error && /failed to fetch|network|load failed|connection.*(?:closed|reset)/i.test(error.message))
}

function planCreationMarkerKey(ideaSessionId: string) {
  return `${PLAN_CREATION_MARKER_PREFIX}${ideaSessionId}`
}

function markPlanCreation(ideaSessionId: string) {
  try {
    window.localStorage.setItem(planCreationMarkerKey(ideaSessionId), String(Date.now()))
  } catch {
    // Private browsing or storage policy must not block plan generation.
  }
}

function clearPlanCreationMarker(ideaSessionId: string) {
  try {
    window.localStorage.removeItem(planCreationMarkerKey(ideaSessionId))
  } catch {
    // Ignore unavailable browser storage.
  }
}

function hasRecentPlanCreationMarker(ideaSessionId: string) {
  try {
    const startedAt = Number(window.localStorage.getItem(planCreationMarkerKey(ideaSessionId)) || 0)
    if (!startedAt || Date.now() - startedAt > PLAN_CREATION_MARKER_TTL_MS) {
      clearPlanCreationMarker(ideaSessionId)
      return false
    }
    return true
  } catch {
    return false
  }
}

function waitForRecoveryInterval() {
  return new Promise((resolve) => window.setTimeout(resolve, PLAN_RECOVERY_INTERVAL_MS))
}

function formatDomainContextSignal(value: string) {
  const cleaned = value.replace(/^cluster:\s*/i, '').trim()
  return cleaned
    .split(',')
    .map((part) => part.trim())
    .filter(Boolean)
    .join(' · ')
}

function summarizeRecordText(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'string') return value.trim()
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (Array.isArray(value)) {
    return value.map(summarizeRecordText).filter(Boolean).join(', ')
  }
  if (typeof value !== 'object') return compactValue(value)

  const record = value as Record<string, unknown>
  for (const key of ['description', 'summary', 'title', 'name', 'text', 'label', 'statement']) {
    const text = summarizeRecordText(record[key])
    if (text) return text
  }

  return compactValue(value)
}

function StatCard({
  label,
  value,
  detail,
}: {
  label: string
  value: string
  detail?: string
}) {
  return (
    <div className="rounded-md border border-slate-300 bg-white px-3 py-2 shadow-sm">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 text-sm font-semibold text-slate-950">{value}</p>
      {detail && <p className="mt-0.5 text-xs text-slate-600">{detail}</p>}
    </div>
  )
}

function toReadableStage(stage: PlanStage): PlanReadableStage {
  return {
    id: stage.id,
    order: stage.order,
    title: stage.title,
    goal: stage.goal,
    method: stage.method,
    dependsOn: stage.dependsOn,
    steps: stage.steps.map((step) => ({
      id: step.id,
      order: step.order,
      title: step.title,
      description: step.desc,
      method: step.method,
      inputFrom: step.inputFrom ?? [],
      outputs: step.outputs.map((output) => ({
        type: output.type,
        name: output.name,
        desc: output.desc,
      })),
      expected: step.expected.map((expected) => ({
        metric: expected.metric,
        target: expected.target,
      })),
      evidenceRefs: step.evidenceRefs ?? [],
    })),
  }
}

function QualityGateSummary({ gate }: { gate: PlanQualityGate }) {
  const { text } = useReviewLocale()
  const rows = [
    { label: text('结构', 'Schema'), ok: gate.schemaValid },
    { label: text('证据', 'Evidence'), ok: gate.evidenceValid },
    { label: text('主题', 'Topic'), ok: gate.topicRelevant },
    { label: text('引用', 'Citation'), ok: gate.citationFaithful },
    { label: text('计划', 'Plan'), ok: gate.planSpecific },
    { label: 'Agent', ok: gate.agentApproved },
    { label: text('人工', 'Human'), ok: gate.humanApproved },
    { label: text('就绪', 'Ready'), ok: gate.implementationReady },
  ]

  return (
    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
      {rows.map((row) => (
        <div key={row.label} className={`flex items-center justify-between rounded-md border px-3 py-2 ${statusVariant(row.ok)}`}>
          <span className="text-sm font-medium">{row.label}</span>
          {row.ok ? <CheckCircle2 className="h-4 w-4" /> : <AlertTriangle className="h-4 w-4" />}
        </div>
      ))}
    </div>
  )
}

function EvidenceChips({ refs }: { refs: PlanEvidenceRef[] }) {
  if (!refs.length) return null
  return (
    <div className="flex flex-wrap gap-1.5">
      {refs.map((ref, index) => (
        <Badge key={`${ref.type}-${ref.id}-${index}`} variant="outline" className="max-w-full font-mono text-[11px]">
          {ref.type}:{shortId(ref.id)}
        </Badge>
      ))}
    </div>
  )
}

function TextList({ items, emptyLabel }: { items: string[]; emptyLabel: string }) {
  if (!items.length) {
    return <p className="text-sm text-muted-foreground">{emptyLabel}</p>
  }
  return (
    <ul className="space-y-2 text-sm text-slate-800">
      {items.map((item, index) => (
        <li key={`${item}-${index}`} className="break-words rounded-md border border-slate-300 bg-slate-50 px-3 py-2">
          {item}
        </li>
      ))}
    </ul>
  )
}

function DisclosureBlock({
  title,
  summary,
  children,
  icon,
  defaultOpen = false,
}: {
  title: string
  summary?: string
  children: ReactNode
  icon?: ReactNode
  defaultOpen?: boolean
}) {
  const { text } = useReviewLocale()
  const [open, setOpen] = useState(defaultOpen)

  return (
    <div className="rounded-md border border-slate-300 bg-white px-4 py-3 shadow-sm">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-start justify-between gap-3 text-left"
        aria-expanded={open}
      >
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            {icon}
            <p className="text-sm font-semibold text-slate-900">{title}</p>
          </div>
          {summary && <p className="mt-1 text-xs text-slate-600">{summary}</p>}
        </div>
        <span className="flex shrink-0 items-center gap-1 rounded-full border border-slate-200 bg-slate-50 px-2 py-1 text-xs text-slate-600">
          <ChevronDown className={`h-3.5 w-3.5 text-slate-500 transition-transform ${open ? 'rotate-180' : ''}`} />
          {open ? text('收起', 'Collapse') : text('展开', 'Expand')}
        </span>
      </button>

      {open && <div className="mt-3">{children}</div>}
    </div>
  )
}

function ReadableStageBlock({ stage }: { stage: PlanReadableStage }) {
  const { text } = useReviewLocale()
  const [expanded, setExpanded] = useState(false)
  const [expandedSteps, setExpandedSteps] = useState<Record<string, boolean>>({})

  return (
    <div className="rounded-md border border-slate-300 bg-white px-4 py-3 shadow-sm">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="flex w-full flex-wrap items-start justify-between gap-3 text-left"
        aria-expanded={expanded}
      >
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline" className="font-mono text-[11px]">
              {text('阶段', 'Stage')} {stage.order}
            </Badge>
            <h3 className="text-base font-semibold text-slate-900">{stage.title}</h3>
          </div>
          <p className="mt-2 text-sm text-slate-800">{stage.goal}</p>
          <p className="mt-2 text-xs text-slate-600">{stage.method}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2 text-xs text-slate-600">
          <Badge variant="secondary" className="shrink-0">
            {text(`${stage.steps.length} 个步骤`, `${stage.steps.length} steps`)}
          </Badge>
          <span className="flex items-center gap-1 rounded-full border border-slate-200 bg-slate-50 px-2 py-1">
            <ChevronDown className={`h-3.5 w-3.5 text-slate-500 transition-transform ${expanded ? 'rotate-180' : ''}`} />
            {expanded ? text('收起', 'Collapse') : text('展开', 'Expand')}
          </span>
        </div>
      </button>

      {expanded && (
        <>
          {stage.dependsOn.length > 0 && (
            <div className="mt-4 flex flex-wrap items-center gap-2 text-xs text-slate-700">
              <GitBranch className="h-3.5 w-3.5" />
              {stage.dependsOn.map((id) => (
                <span key={id} className="rounded bg-slate-100 px-2 py-1 font-mono text-slate-900">
                  {id}
                </span>
              ))}
            </div>
          )}

          <div className="mt-4 space-y-3 border-t border-slate-200 pt-4">
            {stage.steps.map((step) => {
              const stepOpen = Boolean(expandedSteps[step.id])
              const inputFrom = step.inputFrom ?? []
              const evidenceRefs = step.evidenceRefs ?? []
              const outputs = step.outputs ?? []
              const expected = step.expected ?? []

              return (
                <div key={step.id} className="rounded-md border border-slate-200 bg-slate-50 px-3 py-3">
                  <button
                    type="button"
                    onClick={() => setExpandedSteps((current) => ({ ...current, [step.id]: !current[step.id] }))}
                    className="flex w-full flex-wrap items-start justify-between gap-3 text-left"
                    aria-expanded={stepOpen}
                  >
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant="outline" className="font-mono text-[11px]">
                          {step.id}
                        </Badge>
                        <p className="text-sm font-semibold text-slate-900">{step.title}</p>
                      </div>
                      <p className="mt-1 text-sm text-slate-700">{step.description}</p>
                      <p className="mt-2 text-xs text-slate-600">{step.method}</p>
                    </div>
                    <div className="flex shrink-0 items-center gap-2 text-xs text-slate-600">
                      <Badge variant="secondary">{text('步骤', 'Step')} {step.order}</Badge>
                      <Badge variant="outline">{text(`${outputs.length} 个输出`, `${outputs.length} outputs`)}</Badge>
                      <Badge variant="outline">{text(`${expected.length} 个指标`, `${expected.length} metrics`)}</Badge>
                      <span className="flex items-center gap-1 rounded-full border border-slate-200 bg-white px-2 py-1">
                        <ChevronDown className={`h-3.5 w-3.5 text-slate-500 transition-transform ${stepOpen ? 'rotate-180' : ''}`} />
                        {stepOpen ? text('收起', 'Collapse') : text('展开', 'Expand')}
                      </span>
                    </div>
                  </button>

                  {stepOpen && (
                    <div className="mt-3 grid gap-3 md:grid-cols-2">
                      <div>
                        <p className="mb-2 text-xs font-semibold uppercase text-slate-500">{text('输出', 'Outputs')}</p>
                        <div className="space-y-2">
                          {outputs.map((output, index) => (
                            <div key={`${output.name}-${index}`} className="rounded-md border border-l-4 border-slate-300 border-l-blue-700 bg-white px-3 py-2 text-xs">
                              <div className="flex flex-wrap items-center gap-2">
                                <Badge variant="outline" className="border-blue-400 bg-blue-50 text-blue-900">
                                  {output.type}
                                </Badge>
                                <span className="font-mono text-slate-800">{output.name}</span>
                              </div>
                              {output.desc && <p className="mt-1 text-slate-600">{output.desc}</p>}
                            </div>
                          ))}
                        </div>
                      </div>
                      <div>
                        <p className="mb-2 text-xs font-semibold uppercase text-slate-500">{text('预期指标', 'Expected')}</p>
                        <div className="space-y-2">
                          {expected.map((expectedItem, index) => (
                            <div key={`${expectedItem.metric}-${index}`} className="rounded-md border border-l-4 border-slate-300 border-l-emerald-700 bg-white px-3 py-2 text-xs">
                              <p className="font-medium text-emerald-900">{expectedItem.metric}</p>
                              <p className="mt-1 text-slate-800">{expectedItem.target}</p>
                              {expectedItem.desc && <p className="mt-1 text-slate-600">{expectedItem.desc}</p>}
                            </div>
                          ))}
                        </div>
                      </div>
                      {inputFrom.length > 0 && (
                        <div className="md:col-span-2 flex flex-wrap items-center gap-2 text-xs text-slate-700">
                          <GitBranch className="h-3.5 w-3.5" />
                          {inputFrom.map((id) => (
                            <span key={id} className="rounded bg-slate-100 px-2 py-1 font-mono text-slate-900">
                              {id}
                            </span>
                          ))}
                        </div>
                      )}
                      <div className="md:col-span-2">
                        <EvidenceChips refs={evidenceRefs} />
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}

function PaperRow({ paper }: { paper: PlanLiteraturePaperSummary }) {
  const { text } = useReviewLocale()
  const methods = paper.methods.map(summarizeRecordText).filter(Boolean).slice(0, 2)
  const findings = paper.findings.map(summarizeRecordText).filter(Boolean).slice(0, 2)

  return (
    <div className="rounded-md border border-slate-300 bg-white px-4 py-3 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline" className="font-mono text-[11px]">
              {shortId(paper.paperId)}
            </Badge>
            <Badge className={paper.source === 'probe' ? 'bg-indigo-700 text-white' : 'bg-blue-700 text-white'}>{paper.source}</Badge>
            <Badge
              variant="outline"
              className={
                paper.relevanceScore >= 0.7
                  ? 'border-emerald-400 bg-emerald-50 text-emerald-900'
                  : paper.relevanceScore >= 0.45
                    ? 'border-amber-400 bg-amber-50 text-amber-900'
                    : 'border-red-300 bg-red-50 text-red-900'
              }
            >
              {text('相关性', 'relevance')} {(paper.relevanceScore * 100).toFixed(0)}
            </Badge>
            {paper.year ? <span className="text-xs text-muted-foreground">{paper.year}</span> : null}
          </div>
          <h4 className="mt-2 text-sm font-semibold text-slate-900">{paper.title}</h4>
          <p className="mt-1 text-xs text-slate-500">{paper.authors.join(', ')}</p>
        </div>
        {paper.role && <Badge variant="secondary">{paper.role}</Badge>}
      </div>
      <p className="mt-3 text-sm text-slate-700">{paper.summary}</p>
      {paper.relevanceReason && <p className="mt-2 text-xs text-slate-600">{paper.relevanceReason}</p>}
      {paper.relevanceSignals.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {paper.relevanceSignals.slice(0, 8).map((signal) => (
            <Badge key={signal} variant="outline" className="font-mono text-[11px]">
              {signal}
            </Badge>
          ))}
        </div>
      )}
      <div className="mt-3 grid gap-3 lg:grid-cols-3">
        <div>
          <p className="text-xs font-semibold uppercase text-slate-500">{text('方法', 'Methods')}</p>
          <TextList items={methods} emptyLabel={text('暂无方法摘要', 'No method summary')} />
        </div>
        <div>
          <p className="text-xs font-semibold uppercase text-slate-500">{text('发现', 'Findings')}</p>
          <TextList items={findings} emptyLabel={text('暂无发现摘要', 'No finding summary')} />
        </div>
        <div>
          <p className="text-xs font-semibold uppercase text-slate-500">{text('局限', 'Limitations')}</p>
          <TextList items={paper.limitations.slice(0, 3)} emptyLabel={text('暂无局限摘要', 'No limitation summary')} />
        </div>
      </div>
    </div>
  )
}

function GapItem({ gap }: { gap: PlanGapItem }) {
  return (
    <div className="rounded-md border border-slate-300 bg-white px-3 py-3 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Badge variant="outline" className="font-mono text-[11px]">
          {gap.id}
        </Badge>
        <Badge variant="secondary">{gap.severity}</Badge>
      </div>
      <p className="mt-2 text-sm text-slate-800">{gap.statement}</p>
      {gap.whyUnsolved && <p className="mt-2 text-xs text-slate-500">{gap.whyUnsolved}</p>}
      <div className="mt-2 flex flex-wrap gap-1.5">
        {gap.supportedByPaperIds.slice(0, 6).map((id) => (
          <Badge key={id} variant="outline" className="font-mono text-[11px]">
            paper:{shortId(id)}
          </Badge>
        ))}
      </div>
    </div>
  )
}

function EvidenceCoverageCard({
  label,
  value,
  detail,
  ok,
}: {
  label: string
  value: string
  detail: string
  ok: boolean
}) {
  return (
    <div className={`rounded-md border border-l-4 bg-white px-4 py-3 shadow-sm ${ok ? 'border-l-emerald-700' : 'border-l-amber-700'}`}>
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm font-semibold text-slate-900">{label}</p>
        {ok ? <CheckCircle2 className="h-4 w-4 text-emerald-700" /> : <AlertTriangle className="h-4 w-4 text-amber-700" />}
      </div>
      <p className="mt-2 text-2xl font-semibold text-slate-950">{value}</p>
      <p className="mt-1 text-xs text-slate-600">{detail}</p>
    </div>
  )
}

function ReviewerReportCard({ report }: { report: PlanReviewerReport }) {
  return (
    <div className={`rounded-md border border-l-4 bg-white px-4 py-3 shadow-sm ${report.passed ? 'border-l-emerald-700' : 'border-l-red-700'}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          {report.passed ? <CheckCircle2 className="h-4 w-4 text-emerald-700" /> : <AlertTriangle className="h-4 w-4 text-red-700" />}
          <p className="text-sm font-semibold text-slate-900">{report.reviewer}</p>
        </div>
        <Badge variant="outline" className="font-mono text-[11px]">
          {(report.score * 100).toFixed(0)}
        </Badge>
      </div>
      {report.blockingIssues.length > 0 && (
        <div className="mt-3 space-y-2">
          {report.blockingIssues.slice(0, 3).map((issue) => (
            <div key={issue.id} className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-900">
              <span className="font-mono">{issue.sectionPath || 'package'}</span>: {issue.message}
            </div>
          ))}
        </div>
      )}
      {report.warnings.length > 0 && (
        <div className="mt-3 space-y-2">
          {report.warnings.slice(0, 2).map((issue) => (
            <div key={issue.id} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
              <span className="font-mono">{issue.sectionPath || 'package'}</span>: {issue.message}
            </div>
          ))}
        </div>
      )}
      {report.repairSuggestions.length > 0 && (
        <p className="mt-3 text-xs text-slate-600">{report.repairSuggestions[0]}</p>
      )}
    </div>
  )
}

function ReviewerCommitteeDisclosure({ reports }: { reports: PlanReviewerReport[] }) {
  const { text } = useReviewLocale()
  const [open, setOpen] = useState(false)

  return (
    <div className="rounded-md border border-slate-300 bg-white px-4 py-3 shadow-sm">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-3 text-left"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <span className="text-sm font-medium text-slate-800">{text('多审稿人委员会详情', 'Reviewer committee details')}</span>
        <span className="flex items-center gap-1 rounded-full border border-slate-200 bg-slate-50 px-2 py-1 text-xs text-slate-600">
          <ChevronDown className={`h-3.5 w-3.5 text-slate-500 transition-transform ${open ? 'rotate-180' : ''}`} />
          {open ? text('收起', 'Collapse') : text('展开', 'Expand')}
        </span>
      </button>
      {open && (
        <div className="mt-3 space-y-3">
          {reports.length === 0 ? (
            <p className="text-sm text-muted-foreground">{text('暂无审查报告；创建或修订计划时会自动生成。', 'No reviewer reports yet. They are generated automatically when a package is created or revised.')}</p>
          ) : (
            <div className="space-y-3">
              {reports.map((report) => (
                <ReviewerReportCard key={report.reviewer} report={report} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function IterationNotesDisclosure({
  gate,
  generationWarnings,
  reviewSummary,
  metaReview,
}: {
  gate: PlanQualityGate
  generationWarnings: string[]
  reviewSummary?: PlanPackagePresentation['reviewSummary'] | null
  metaReview?: PlanMetaReview | null
}) {
  const { text } = useReviewLocale()
  const refinementItems = useMemo(
    () =>
      uniqueStrings([
        ...gate.errors,
        ...(metaReview?.requiredRepairs || []),
        ...(reviewSummary?.requiredFixes || []),
      ]),
    [gate.errors, metaReview?.requiredRepairs, reviewSummary?.requiredFixes],
  )
  const watchItems = useMemo(
    () =>
      uniqueStrings([
        ...gate.warnings,
        ...generationWarnings,
        ...(reviewSummary?.mainConcerns || []),
      ]),
    [gate.warnings, generationWarnings, reviewSummary?.mainConcerns],
  )

  const itemCount = refinementItems.length + watchItems.length
  const summary = itemCount
    ? text(
        `${itemCount} 条待办，已按校验、审查意见和生成警告归并`,
        `${itemCount} internal notes grouped from validation, reviewer feedback, and generation warnings`,
      )
    : text('没有待处理项，当前计划可以进入下一步。', 'No open iteration notes. The current package is clean enough for the next pass.')

  return (
    <DisclosureBlock
      title={text('修订建议', 'Iteration notes')}
      summary={summary}
      icon={<RefreshCw className="h-4 w-4 text-slate-600" />}
      defaultOpen={false}
    >
      <div className={`grid gap-4 ${refinementItems.length > 0 && watchItems.length > 0 ? 'lg:grid-cols-2' : ''}`}>
        <div>
          <p className="mb-2 text-xs font-semibold uppercase text-slate-500">{text('必须处理', 'Needs refinement')}</p>
          <TextList items={refinementItems} emptyLabel={text('没有必须处理的问题', 'No refinement items')} />
        </div>
        <div>
          <p className="mb-2 text-xs font-semibold uppercase text-slate-500">{text('建议关注', 'Watch list')}</p>
          <TextList items={watchItems} emptyLabel={text('没有额外提醒', 'No watch list items')} />
        </div>
      </div>
    </DisclosureBlock>
  )
}

function FeedbackList({ feedback }: { feedback: PlanHumanFeedback[] }) {
  const { text } = useReviewLocale()
  if (!feedback.length) {
    return <p className="text-sm text-muted-foreground">{text('暂无人工反馈。', 'No human feedback yet.')}</p>
  }
  return (
    <div className="space-y-2">
      {feedback.slice(0, 6).map((item) => (
        <div key={item.id} className={`rounded-md border px-3 py-2 text-sm ${item.resolved ? 'border-emerald-200 bg-emerald-50' : 'border-slate-300 bg-white'}`}>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Badge variant={item.resolved ? 'secondary' : 'default'}>
              {item.resolved ? text('已解决', 'Resolved') : text('待处理', 'Pending')}
            </Badge>
            <span className="text-xs text-slate-500">{new Date(item.createdAt).toLocaleString()}</span>
          </div>
          <p className="mt-2 text-slate-800">{item.comment}</p>
          {item.resolvedByRevisionId && (
            <p className="mt-1 text-xs text-emerald-800">Revision {item.resolvedByRevisionId}</p>
          )}
        </div>
      ))}
    </div>
  )
}

export function PlanGenerationPanel({
  ideaSessionId: ideaSessionIdProp,
  ideaCandidateId: ideaCandidateIdProp,
  ideaCandidateTitle: ideaCandidateTitleProp,
  ideaSeedQuery: ideaSeedQueryProp,
}: {
  ideaSessionId?: string
  ideaCandidateId?: string
  ideaCandidateTitle?: string
  ideaSeedQuery?: string
}) {
  const navigate = useNavigate()
  const { text } = useReviewLocale()
  const [searchParams, setSearchParams] = useSearchParams()
  const [activeTab, setActiveTab] = useState('summary')
  const [planPackage, setPlanPackage] = useState<PlanPackage | null>(null)
  const [presentation, setPresentation] = useState<PlanPackagePresentation | null>(null)
  const [packageIdInput, setPackageIdInput] = useState(searchParams.get('packageId')?.trim() || '')
  const [isLoading, setIsLoading] = useState(false)
  const [isCreating, setIsCreating] = useState(false)
  const [isRecoveringCreation, setIsRecoveringCreation] = useState(false)
  const [creationMayContinue, setCreationMayContinue] = useState(false)
  const [isRevising, setIsRevising] = useState(false)
  const [isApproving, setIsApproving] = useState(false)
  const [isSubmittingFeedback, setIsSubmittingFeedback] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [generationMode, setGenerationMode] = useState<GenerationMode>('hybrid')
  const [maxStages, setMaxStages] = useState(3)
  const [maxStepsPerStage, setMaxStepsPerStage] = useState(3)
  const [maxReviewIterations, setMaxReviewIterations] = useState(1)
  const [advancedGenerationOpen, setAdvancedGenerationOpen] = useState(false)
  const [packageLoaderOpen, setPackageLoaderOpen] = useState(false)
  const [userNotes, setUserNotes] = useState('')
  const [feedbackComment, setFeedbackComment] = useState('')
  const recoveryRunRef = useRef(0)

  const packageIdFromUrl = searchParams.get('packageId')?.trim() || ''
  const ideaSessionIdFromUrl = ideaSessionIdProp || searchParams.get('ideaSessionId')?.trim() || ''
  const ideaCandidateIdFromUrl = ideaCandidateIdProp || searchParams.get('ideaCandidateId')?.trim() || ''
  const ideaCandidateTitleFromUrl = ideaCandidateTitleProp || searchParams.get('ideaCandidateTitle')?.trim() || ''
  const ideaSeedQueryFromUrl = ideaSeedQueryProp || searchParams.get('ideaSeedQuery')?.trim() || ''

  const loadPackage = useCallback(async (packageId: string) => {
    if (!packageId) return
    setIsLoading(true)
    setError(null)
    try {
      const [loaded, loadedPresentation] = await Promise.all([
        getPlanPackage(packageId),
        getPlanPackagePresentation(packageId),
      ])
      setPlanPackage(loaded)
      setPresentation(loadedPresentation)
      setPackageIdInput(loaded.packageId)
    } catch (err) {
      setPlanPackage(null)
      setPresentation(null)
      setError(err instanceof Error ? err.message : 'Failed to load PlanPackage')
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    if (packageIdFromUrl) {
      void loadPackage(packageIdFromUrl)
      return
    }

    if (!ideaSessionIdFromUrl) {
      setPlanPackage(null)
      return
    }

    let cancelled = false
    setIsLoading(true)
    setError(null)
    Promise.all([
      getPlanPackageByIdeaSession(ideaSessionIdFromUrl),
      getPlanPackagePresentationByIdeaSession(ideaSessionIdFromUrl),
    ])
      .then(([loaded, loadedPresentation]) => {
        if (cancelled) return
        setPlanPackage(loaded)
        setPresentation(loadedPresentation)
        setPackageIdInput(loaded.packageId)
        setCreationMayContinue(false)
        clearPlanCreationMarker(ideaSessionIdFromUrl)
      })
      .catch((err) => {
        if (cancelled) return
        if (err instanceof Error && err.message.includes('not found')) {
          setPlanPackage(null)
          setPresentation(null)
          const mayStillBeRunning = hasRecentPlanCreationMarker(ideaSessionIdFromUrl)
          setCreationMayContinue(mayStillBeRunning)
          setError(mayStillBeRunning
            ? text(
                '检测到此前的计划生成可能仍在后台运行。请点击“查找生成结果”，不要重复创建。',
                'A previous plan generation may still be running. Use "Find generated plan" instead of creating a duplicate.',
              )
            : null)
        } else {
          setError(err instanceof Error ? err.message : 'Failed to load PlanPackage')
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [ideaSessionIdFromUrl, loadPackage, packageIdFromUrl, text])

  useEffect(() => () => {
    recoveryRunRef.current += 1
  }, [])

  const updatePackageUrl = (packageId: string) => {
    const next = new URLSearchParams(searchParams)
    next.set('packageId', packageId)
    setSearchParams(next, { replace: true })
  }

  const openCodeWorkspace = useCallback(
    (packageId: string) => {
      navigate(`/code/workspace?packageId=${encodeURIComponent(packageId)}`)
    },
    [navigate],
  )

  const recoverCreatedPackage = async () => {
    if (!ideaSessionIdFromUrl) return false
    const runId = ++recoveryRunRef.current
    const deadline = Date.now() + PLAN_RECOVERY_TIMEOUT_MS
    setIsCreating(true)
    setIsRecoveringCreation(true)
    setCreationMayContinue(true)
    setError(null)

    try {
      while (Date.now() < deadline && recoveryRunRef.current === runId) {
        try {
          const [loaded, loadedPresentation] = await Promise.all([
            getPlanPackageByIdeaSession(ideaSessionIdFromUrl),
            getPlanPackagePresentationByIdeaSession(ideaSessionIdFromUrl),
          ])
          if (recoveryRunRef.current !== runId) return false
          setPlanPackage(loaded)
          setPresentation(loadedPresentation)
          setPackageIdInput(loaded.packageId)
          setCreationMayContinue(false)
          clearPlanCreationMarker(ideaSessionIdFromUrl)
          updatePackageUrl(loaded.packageId)
          setActiveTab('summary')
          return true
        } catch (recoveryError) {
          if (!isNotFoundError(recoveryError)) throw recoveryError
        }
        await waitForRecoveryInterval()
      }

      if (recoveryRunRef.current === runId) {
        setError(text(
          '计划仍未返回。后台任务可能还在运行，你可以继续查找；只有确认任务已停止后再重新生成。',
          'The plan has not returned yet. The background task may still be running; keep checking, and only regenerate after confirming it stopped.',
        ))
      }
      return false
    } catch (recoveryError) {
      if (recoveryRunRef.current === runId) {
        setError(text(
          `查找生成结果失败：${recoveryError instanceof Error ? recoveryError.message : 'unknown error'}。请检查网络后重试。`,
          `Could not find the generated plan: ${recoveryError instanceof Error ? recoveryError.message : 'unknown error'}. Check the network and retry.`,
        ))
      }
      return false
    } finally {
      if (recoveryRunRef.current === runId) {
        setIsCreating(false)
        setIsRecoveringCreation(false)
      }
    }
  }

  const abandonPendingCreation = () => {
    recoveryRunRef.current += 1
    clearPlanCreationMarker(ideaSessionIdFromUrl)
    setCreationMayContinue(false)
    setIsCreating(false)
    setIsRecoveringCreation(false)
    setError(null)
  }

  const createPackage = async () => {
    if (!ideaSessionIdFromUrl) {
      setError(text('请先从研究创意中选择一个候选，或加载已有 PlanPackage。', 'Select a research idea first, or load an existing PlanPackage.'))
      return
    }
    setIsCreating(true)
    setIsRecoveringCreation(false)
    setCreationMayContinue(false)
    setError(null)
    markPlanCreation(ideaSessionIdFromUrl)
    try {
      const response = await createPlanPackageFromIdeaSession(ideaSessionIdFromUrl, {
        candidateId: ideaCandidateIdFromUrl || undefined,
        generationMode,
        reviewerMode: DEFAULT_REVIEWER_MODE,
        maxStages,
        maxStepsPerStage,
        maxRepairRounds: maxReviewIterations,
        userNotes: userNotes.trim() || undefined,
      })
      setPlanPackage(response.package)
      setPresentation(await getPlanPackagePresentation(response.packageId))
      setPackageIdInput(response.packageId)
      clearPlanCreationMarker(ideaSessionIdFromUrl)
      updatePackageUrl(response.packageId)
      setActiveTab('summary')
    } catch (err) {
      if (isNetworkInterruption(err)) {
        await recoverCreatedPackage()
      } else {
        clearPlanCreationMarker(ideaSessionIdFromUrl)
        setError(text(
          `计划生成失败：${err instanceof Error ? err.message : 'unknown error'}。请根据提示修改设置或研究创意后重试。`,
          `Plan generation failed: ${err instanceof Error ? err.message : 'unknown error'}. Adjust the settings or research idea, then retry.`,
        ))
      }
    } finally {
      setIsCreating(false)
    }
  }

  const approveCurrentPackage = async () => {
    if (!planPackage) return
    if (planPackage.status === 'approved') {
      openCodeWorkspace(planPackage.packageId)
      return
    }
    setIsApproving(true)
    setError(null)
    try {
      const approved = await approvePlanPackageWithMode(planPackage.packageId, DEFAULT_REVIEWER_MODE)
      setPlanPackage(approved)
      setPresentation(await getPlanPackagePresentation(approved.packageId))
      openCodeWorkspace(approved.packageId)
    } catch (err) {
      setError(text(
        `暂时不能批准：${err instanceof Error ? err.message : 'unknown error'}。请先按右侧审查意见修订，再重新批准。`,
        `Approval is not available yet: ${err instanceof Error ? err.message : 'unknown error'}. Revise the plan from the review findings, then approve again.`,
      ))
    } finally {
      setIsApproving(false)
    }
  }

  const reviseFromReview = async () => {
    if (!planPackage) return
    setIsRevising(true)
    setError(null)
    try {
      const revised = await revisePlanPackage(planPackage.packageId, {
        generationMode: 'hybrid',
        reviewerMode: DEFAULT_REVIEWER_MODE,
        maxStages,
        maxStepsPerStage,
        maxRepairRounds: 1,
      })
      setPlanPackage(revised)
      setPresentation(await getPlanPackagePresentation(revised.packageId))
      setActiveTab('summary')
    } catch (err) {
      setError(text(
        `自动修订失败：${err instanceof Error ? err.message : 'unknown error'}。请展开“修订建议”，补充人工反馈后再试。`,
        `Automatic revision failed: ${err instanceof Error ? err.message : 'unknown error'}. Open Iteration notes, add human feedback, and try again.`,
      ))
    } finally {
      setIsRevising(false)
    }
  }

  const submitFeedbackAndRevise = async () => {
    if (!planPackage || !feedbackComment.trim()) return
    setIsSubmittingFeedback(true)
    setIsRevising(true)
    setError(null)
    try {
      const updated = await addPlanPackageFeedback(planPackage.packageId, {
        sectionPath: 'package',
        feedbackType: 'correction',
        severity: 'medium',
        requestedAction: 'revise',
        comment: feedbackComment.trim(),
      })
      const revised = await revisePlanPackage(updated.packageId, {
        generationMode,
        reviewerMode: DEFAULT_REVIEWER_MODE,
        maxStages,
        maxStepsPerStage,
        maxRepairRounds: maxReviewIterations,
      })
      setPlanPackage(revised)
      setPresentation(await getPlanPackagePresentation(revised.packageId))
      setFeedbackComment('')
      setActiveTab('summary')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to revise from feedback')
    } finally {
      setIsSubmittingFeedback(false)
      setIsRevising(false)
    }
  }

  const loadByInput = () => {
    const packageId = packageIdInput.trim()
    if (!packageId) return
    updatePackageUrl(packageId)
    void loadPackage(packageId)
  }

  const totalSteps = useMemo(
    () => planPackage?.stages.reduce((sum, stage) => sum + stage.steps.length, 0) ?? 0,
    [planPackage]
  )

  const evidencePapers = useMemo(() => {
    if (!planPackage) return []
    const ids = new Set([
      ...planPackage.evidenceTrace.selectedPaperIds,
      ...planPackage.evidenceTrace.structuredPaperIds,
      ...planPackage.evidenceTrace.probePaperIds,
    ])
    return planPackage.literatureSurvey.papers.filter((paper) => {
      return ids.has(paper.paperId) || (paper.structuredPaperId ? ids.has(paper.structuredPaperId) : false)
    })
  }, [planPackage])

  const evidencePaperIdsWithoutSummary = useMemo(() => {
    if (!planPackage) return []
    const summarizedIds = new Set<string>()
    evidencePapers.forEach((paper) => {
      summarizedIds.add(paper.paperId)
      if (paper.structuredPaperId) summarizedIds.add(paper.structuredPaperId)
    })
    return [
      ...planPackage.evidenceTrace.selectedPaperIds,
      ...planPackage.evidenceTrace.structuredPaperIds,
      ...planPackage.evidenceTrace.probePaperIds,
    ].filter((id, index, ids) => id && ids.indexOf(id) === index && !summarizedIds.has(id))
  }, [evidencePapers, planPackage])

  const readableImplementationStages = useMemo(
    () => planPackage?.stages.map(toReadableStage) ?? [],
    [planPackage],
  )

  const planStatusLabel = planPackage
    ? ({
        draft: text('草稿', 'Draft'),
        agent_reviewing: text('Agent 审查中', 'Agent reviewing'),
        needs_revision: text('需要修订', 'Needs revision'),
        needs_human_review: text('等待人工确认', 'Needs human review'),
        approved: text('已批准', 'Approved'),
        rejected: text('已拒绝', 'Rejected'),
      }[planPackage.status] || planPackage.status)
    : ''

  const planStats = useMemo(() => {
    if (!planPackage) return null
    return {
      status: planStatusLabel,
      score: `${(planPackage.qualityGate.overallScore * 100).toFixed(0)} / 100`,
      readiness: planPackage.qualityGate.implementationReady
        ? text('可交付下游模块', 'Ready for handoff')
        : text('内部迭代中', 'Iterating internally'),
      stages: text(`${planPackage.stages.length} 个阶段`, `${planPackage.stages.length} stages`),
      steps: text(`${totalSteps} 个步骤`, `${totalSteps} steps`),
      papers: text(`${planPackage.literatureSurvey.papers.length} 篇论文`, `${planPackage.literatureSurvey.papers.length} papers`),
    }
  }, [planPackage, planStatusLabel, text, totalSteps])

  const gate = planPackage?.qualityGate ?? EMPTY_GATE
  const hasUpstreamRepairBlocker = Boolean(planPackage?.generation.warnings.some((warning) => warning.includes('review_repair_blocked:upstream')))
  const canApprove = Boolean(
    planPackage
      && gate.schemaValid
      && gate.evidenceValid
      && gate.topicRelevant
      && gate.citationFaithful
      && gate.planSpecific
      && gate.downstreamReady
      && gate.agentApproved
      && gate.errors.length === 0,
  )
  const summaryTitle = presentation?.title || planPackage?.idea.title || planPackage?.researchQuestion || 'PlanPackage'
  const summaryQuestion = presentation?.researchQuestion || planPackage?.researchQuestion || ''
  const summaryHypothesis = presentation?.hypothesis || planPackage?.hypothesis || ''
  const summaryExecutive = presentation?.executiveSummary || planPackage?.idea.critiqueSummary || ''
  const summaryReviewDecision = presentation?.reviewSummary.decision || planPackage?.metaReview?.decision || gate.reviewDecision
  const summaryReviewScore = presentation?.reviewSummary.score ?? planPackage?.metaReview?.overallScore ?? gate.overallScore
  const summaryReviewerMode = presentation?.reviewSummary.reviewerMode || planPackage?.generation.reviewerMode || 'unknown'
  const summaryReviewerUsed = presentation?.reviewSummary.llmReviewerUsed ?? planPackage?.generation.llmReviewerUsed

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <CardTitle className="flex items-center gap-2 text-xl">
                <ClipboardList className="h-5 w-5 text-indigo-700" />
                PlanPackage {text('工作区', 'Workspace')}
              </CardTitle>
              <CardDescription className="mt-1">
                {text(
                  '承接研究创意并生成可执行计划；生成、反馈修订和批准时会自动执行质量检查。',
                  'Primary handoff for the idea + plan stage. Quality checks run automatically during generation, feedback revision, and approval.',
                )}
              </CardDescription>
            </div>
            {planPackage && (
              <div className="flex flex-wrap gap-2">
                {planPackage.status === 'approved' ? (
                  <Button onClick={() => openCodeWorkspace(planPackage.packageId)} className="bg-emerald-700 text-white hover:bg-emerald-800">
                    <UserCheck className="mr-2 h-4 w-4" />
                    {text('进入 Code', 'Open Code')}
                  </Button>
                ) : canApprove ? (
                  <Button onClick={approveCurrentPackage} disabled={isApproving} className="bg-emerald-700 text-white hover:bg-emerald-800">
                    {isApproving ? <RefreshCw className="mr-2 h-4 w-4 animate-spin" /> : <UserCheck className="mr-2 h-4 w-4" />}
                    {text('批准并进入 Code', 'Approve & Open Code')}
                  </Button>
                ) : hasUpstreamRepairBlocker ? (
                  <Button
                    variant="outline"
                    onClick={() => document.getElementById('pipeline-phase-1')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
                  >
                    <Lightbulb className="mr-2 h-4 w-4" />
                    {text('返回创意阶段调整', 'Return to Idea stage')}
                  </Button>
                ) : (
                  <Button onClick={() => void reviseFromReview()} disabled={isRevising} className="bg-indigo-700 text-white hover:bg-indigo-800">
                    {isRevising ? <RefreshCw className="mr-2 h-4 w-4 animate-spin" /> : <Sparkles className="mr-2 h-4 w-4" />}
                    {text('让千问按审查意见修订', 'Ask Qwen to revise')}
                  </Button>
                )}
              </div>
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {ideaSessionIdFromUrl && (
            <button
              type="button"
              onClick={() => setPackageLoaderOpen((value) => !value)}
              className="inline-flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground"
              aria-expanded={packageLoaderOpen}
            >
              <ChevronDown className={`h-3.5 w-3.5 transition-transform ${packageLoaderOpen ? 'rotate-180' : ''}`} />
              {text('加载已有 PlanPackage', 'Load an existing PlanPackage')}
            </button>
          )}
          {(!ideaSessionIdFromUrl || packageLoaderOpen) && (
            <div className="grid gap-3 lg:grid-cols-[1fr_auto]">
              <input
                value={packageIdInput}
                onChange={(event) => setPackageIdInput(event.target.value)}
                placeholder={text('输入 PlanPackage ID（ppkg_...）', 'Enter a PlanPackage ID (ppkg_...)')}
                aria-label={text('PlanPackage ID', 'PlanPackage ID')}
                className="h-10 w-full rounded-md border border-slate-400 px-3 text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-indigo-600"
              />
              <Button variant="outline" onClick={loadByInput} disabled={!packageIdInput.trim() || isLoading}>
                {isLoading ? <RefreshCw className="mr-2 h-4 w-4 animate-spin" /> : <FileJson className="mr-2 h-4 w-4" />}
                {text('加载', 'Load')}
              </Button>
            </div>
          )}

          {(ideaSessionIdFromUrl || ideaCandidateIdFromUrl) && (
            <div className="rounded-md border border-l-4 border-slate-300 border-l-indigo-700 bg-white px-4 py-3 shadow-sm">
              {!planPackage && (
                <>
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant="outline" className="border-slate-400 bg-slate-50 font-mono text-[11px] text-slate-800">
                          idea:{shortId(ideaSessionIdFromUrl)}
                        </Badge>
                        {ideaCandidateIdFromUrl && (
                          <Badge variant="outline" className="border-slate-400 bg-slate-50 font-mono text-[11px] text-slate-800">
                            candidate:{shortId(ideaCandidateIdFromUrl)}
                          </Badge>
                        )}
                      </div>
                      {ideaCandidateTitleFromUrl && <p className="mt-2 text-sm font-medium text-slate-900">{ideaCandidateTitleFromUrl}</p>}
                      {ideaSeedQueryFromUrl && <p className="mt-1 text-xs text-slate-600">{ideaSeedQueryFromUrl}</p>}
                    </div>
                    <Button
                      onClick={() => creationMayContinue ? void recoverCreatedPackage() : void createPackage()}
                      disabled={isCreating || !ideaSessionIdFromUrl}
                      className="bg-indigo-700 text-white hover:bg-indigo-800"
                    >
                      {isCreating ? <RefreshCw className="mr-2 h-4 w-4 animate-spin" /> : <Sparkles className="mr-2 h-4 w-4" />}
                      {isRecoveringCreation
                        ? text('正在找回结果', 'Recovering result')
                        : creationMayContinue
                          ? text('查找生成结果', 'Find generated plan')
                          : text('生成 PlanPackage', 'Generate PlanPackage')}
                    </Button>
                  </div>
                  {isCreating && (
                    <p className="mt-3 text-xs text-indigo-800">
                      {isRecoveringCreation
                        ? text(
                            '连接已切换为自动找回模式。后台仍在生成，页面每 3 秒检查一次，你无需重新提交。',
                            'The connection switched to recovery mode. Generation continues in the background and this page checks every 3 seconds; do not resubmit.',
                          )
                        : text(
                            '千问正在把创意整理为可执行步骤并进行质量审查，通常需要 1-3 分钟，请勿重复提交。',
                            'Qwen is turning the idea into executable steps and reviewing plan quality. This usually takes 1-3 minutes; do not resubmit.',
                          )}
                    </p>
                  )}
                </>
              )}
              <div className={`${planPackage ? '' : 'mt-3'} flex flex-wrap items-center justify-between gap-3 text-xs text-slate-600`}>
                <button
                  type="button"
                  onClick={() => setAdvancedGenerationOpen((value) => !value)}
                  className="inline-flex items-center gap-1 font-medium text-slate-700 hover:text-slate-950"
                  aria-expanded={advancedGenerationOpen}
                >
                  <ChevronDown className={`h-3.5 w-3.5 transition-transform ${advancedGenerationOpen ? 'rotate-180' : ''}`} />
                  {advancedGenerationOpen
                    ? text('收起高级生成设置', 'Hide advanced generation settings')
                    : text('展开高级生成设置', 'Show advanced generation settings')}
                </button>
                <span className="text-slate-500">
                  {generationMode === 'hybrid' ? 'Hybrid LLM' : text('确定性生成', 'Deterministic')} · {maxStages} {text('个阶段', 'stages')} · {maxStepsPerStage} {text('步/阶段', 'steps/stage')} · {maxReviewIterations} {text('轮审查', 'review passes')}
                </span>
              </div>
              {advancedGenerationOpen && (
                <div className="mt-3 border-t border-slate-200 pt-3">
                  <div className="grid gap-3 md:grid-cols-4">
                    <label className="space-y-1 text-xs font-medium text-slate-700">
                      {text('生成方式', 'Generation')}
                      <select
                        value={generationMode}
                        onChange={(event) => setGenerationMode(event.target.value as GenerationMode)}
                        className="h-9 w-full rounded-md border border-slate-400 bg-white px-2 text-sm text-slate-900"
                      >
                        <option value="hybrid">Hybrid LLM</option>
                        <option value="deterministic">{text('确定性生成', 'Deterministic')}</option>
                      </select>
                    </label>
                    <label className="space-y-1 text-xs font-medium text-slate-700">
                      {text('阶段数上限', 'Max stages')}: {maxStages}
                      <input
                        type="range"
                        min={1}
                        max={5}
                        value={maxStages}
                        onChange={(event) => setMaxStages(Number(event.target.value))}
                        className="w-full"
                      />
                    </label>
                    <label className="space-y-1 text-xs font-medium text-slate-700">
                      {text('每阶段步骤上限', 'Max steps/stage')}: {maxStepsPerStage}
                      <input
                        type="range"
                        min={1}
                        max={5}
                        value={maxStepsPerStage}
                        onChange={(event) => setMaxStepsPerStage(Number(event.target.value))}
                        className="w-full"
                      />
                    </label>
                    <label className="space-y-1 text-xs font-medium text-slate-700">
                      {text('审查迭代轮数', 'Review iterations')}: {maxReviewIterations}
                      <input
                        type="range"
                        min={0}
                        max={2}
                        value={maxReviewIterations}
                        onChange={(event) => setMaxReviewIterations(Number(event.target.value))}
                        className="w-full"
                      />
                    </label>
                  </div>
                  <textarea
                    value={userNotes}
                    onChange={(event) => setUserNotes(event.target.value)}
                    placeholder={text('可选：补充此 PlanPackage 的规划约束', 'Optional planning constraints for this package')}
                    className="mt-3 min-h-[72px] w-full rounded-md border border-slate-400 bg-white px-3 py-2 text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-indigo-600"
                  />
                </div>
              )}
            </div>
          )}

          {error && (
            <div className="rounded-md border border-l-4 border-red-300 border-l-red-700 bg-white px-4 py-3 text-sm text-red-800 shadow-sm">
              <p>{error}</p>
              {creationMayContinue && (
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button type="button" size="sm" variant="outline" onClick={() => void recoverCreatedPackage()} disabled={isCreating}>
                    <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
                    {text('继续查找结果', 'Keep checking')}
                  </Button>
                  <Button type="button" size="sm" variant="ghost" onClick={abandonPendingCreation} disabled={isCreating}>
                    {text('放弃等待并允许重新生成', 'Stop waiting and allow regeneration')}
                  </Button>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {!planPackage && !isLoading && !isCreating && !ideaSessionIdFromUrl && (
        <Card className="border-slate-200">
          <CardContent className="flex flex-col items-center gap-3 py-10 text-center">
            <FileJson className="h-10 w-10 text-slate-400" />
            <div>
              <p className="font-medium text-slate-900">{text('尚未加载 PlanPackage', 'No PlanPackage loaded')}</p>
              <p className="mt-1 text-sm text-muted-foreground">
                {text('请先选择一个已完成的研究创意，或在上方输入 PlanPackage ID。', 'Start from a completed Idea candidate or paste a package ID above.')}
              </p>
            </div>
            <Button variant="outline" onClick={() => navigate('/research/ideas')}>
              <Lightbulb className="mr-2 h-4 w-4" />
              {text('打开研究创意', 'Open Ideas')}
            </Button>
          </CardContent>
        </Card>
      )}

      {planPackage && (
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_340px] xl:items-start">
          <div className="space-y-6">
          <Card>
            <CardHeader>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="outline" className="font-mono">
                      {planPackage.packageId}
                    </Badge>
                    <Badge className={planPackage.status === 'approved' ? 'bg-emerald-700 text-white' : planPackage.status === 'needs_revision' ? 'bg-red-700 text-white' : 'bg-amber-700 text-white'}>
                      {planStatusLabel}
                    </Badge>
                    <Badge className={planPackage.generation.fallbackUsed ? 'bg-amber-700 text-white' : 'bg-emerald-700 text-white'}>
                      {planPackage.generation.mode}
                    </Badge>
                    {planPackage.reviewReports.length > 0 && (
                      <Badge variant="outline" className="border-emerald-400 bg-emerald-50 text-emerald-900">
                        {text('已完成质量检查', 'Quality checked')}
                      </Badge>
                    )}
                    <Badge variant="secondary">{planPackage.schemaVersion}</Badge>
                    <Badge variant="outline" className="font-mono">
                      {text('评分', 'score')} {(planPackage.qualityGate.overallScore * 100).toFixed(0)}
                    </Badge>
                  </div>
                  <CardTitle className="mt-3 text-xl leading-tight">{presentation?.title || planPackage.researchQuestion}</CardTitle>
                  {(presentation?.researchQuestion || planPackage.researchQuestion) && (
                    <CardDescription className="mt-2 text-sm text-slate-800">
                      {presentation?.researchQuestion || planPackage.researchQuestion}
                    </CardDescription>
                  )}
                  {(presentation?.hypothesis || planPackage.hypothesis) && (
                    <CardDescription className="mt-2 text-sm text-slate-700">
                      {presentation?.hypothesis || planPackage.hypothesis}
                    </CardDescription>
                  )}
                </div>
              </div>
            </CardHeader>
              <CardContent className="space-y-4">
                {planPackage.status === 'approved' ? (
                  <div className="flex items-start gap-2 rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-900">
                    <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
                    <p>{text('计划已通过 Agent 与人工审核，可以进入 Code 阶段。', 'The plan passed agent and human review and can move to Code.')}</p>
                  </div>
                ) : canApprove ? (
                  <div className="flex items-start gap-2 rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-900">
                    <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
                    <p>{text('自动质量检查已通过。确认计划内容后，点击“批准并进入 Code”。', 'Automated quality checks passed. Review the plan, then select Approve & Open Code.')}</p>
                  </div>
                ) : hasUpstreamRepairBlocker ? (
                  <div className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-950">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                    <p>{text(
                      '审查发现问题来自选题或文献证据，计划阶段不能凭空修补。请返回创意阶段，换一个候选，或让千问重新改写主题后再运行。',
                      'The review found an upstream idea or evidence problem that planning cannot repair safely. Return to Idea, select another candidate, or ask Qwen to rewrite the topic and rerun.',
                    )}</p>
                  </div>
                ) : (
                  <div className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-950">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                    <p>{text(
                      '计划尚未通过质量检查。可直接让千问按审查意见修订，也可以在右侧补充人工反馈后修订。',
                      'The plan has not passed quality checks. Ask Qwen to revise from the findings, or add human feedback on the right first.',
                    )}</p>
                  </div>
                )}
                {planStats && (
                  <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
                    <StatCard label={text('状态', 'Status')} value={planStats.status} detail={planStats.readiness} />
                    <StatCard label={text('评分', 'Score')} value={planStats.score} detail={summaryTitle} />
                    <StatCard label={text('结构', 'Structure')} value={planStats.stages} detail={planStats.steps} />
                    <StatCard
                      label={text('证据', 'Evidence')}
                      value={planStats.papers}
                      detail={text(`${planPackage.literatureSurvey.coverage.structuredPaperCount} 篇结构化论文`, `${planPackage.literatureSurvey.coverage.structuredPaperCount} structured`)}
                    />
                    <StatCard label={text('创建时间', 'Created')} value={new Date(planPackage.createdAt).toLocaleDateString()} detail={planPackage.schemaVersion} />
                  </div>
                )}
              </CardContent>
            </Card>

            <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-4">
              <TabsList className="h-auto flex-wrap justify-start gap-1">
                <TabsTrigger value="summary">{text('摘要', 'Summary')}</TabsTrigger>
                <TabsTrigger value="narrative">{text('研究叙事', 'Narrative')}</TabsTrigger>
                <TabsTrigger value="implementation">{text('实施计划', 'Implementation')}</TabsTrigger>
                <TabsTrigger value="evidence">{text('证据', 'Evidence')}</TabsTrigger>
              </TabsList>

              <TabsContent value="summary" className="space-y-4">
                {presentation ? (
                  <div className="grid gap-4 xl:grid-cols-[1.45fr_1fr]">
                    <Card>
                      <CardHeader className="pb-3">
                        <CardTitle className="text-lg">{summaryTitle}</CardTitle>
                        <CardDescription className="leading-relaxed">{summaryExecutive}</CardDescription>
                      </CardHeader>
                      <CardContent className="space-y-4">
                        <div className="grid gap-3 md:grid-cols-2">
                          <div className="rounded-md border border-slate-300 bg-slate-50 px-3 py-3">
                            <p className="text-xs font-semibold uppercase text-slate-500">{text('研究问题', 'Research question')}</p>
                            <p className="mt-2 text-sm text-slate-800">{summaryQuestion}</p>
                          </div>
                          <div className="rounded-md border border-slate-300 bg-slate-50 px-3 py-3">
                            <p className="text-xs font-semibold uppercase text-slate-500">{text('研究假设', 'Hypothesis')}</p>
                            <p className="mt-2 text-sm text-slate-800">{summaryHypothesis}</p>
                          </div>
                        </div>
                        {presentation.nextActions.length > 0 && (
                          <div className="rounded-md border border-slate-300 bg-white px-3 py-3">
                            <p className="text-xs font-semibold uppercase text-slate-500">{text('下一步行动', 'Next actions')}</p>
                            <div className="mt-2">
                              <TextList items={presentation.nextActions.slice(0, 3)} emptyLabel={text('暂无下一步行动', 'No next actions')} />
                            </div>
                          </div>
                        )}
                      </CardContent>
                    </Card>

                    <Card>
                      <CardHeader className="pb-3">
                        <CardTitle className="text-base">{text('计划框架', 'Plan frame')}</CardTitle>
                      </CardHeader>
                      <CardContent className="space-y-3">
                        <div className="grid gap-3 md:grid-cols-2">
                          <StatCard label={text('阶段', 'Stages')} value={`${planPackage.stages.length}`} detail={text(`${totalSteps} 个步骤`, `${totalSteps} steps`)} />
                          <StatCard
                            label={text('论文', 'Papers')}
                            value={`${planPackage.literatureSurvey.papers.length}`}
                            detail={`${planPackage.literatureSurvey.coverage.structuredPaperCount} structured · ${planPackage.literatureSurvey.coverage.probePaperCount} probe`}
                          />
                        </div>
                        <div className="grid gap-2 md:grid-cols-2">
                          {Object.keys(planPackage.constants).length === 0 ? (
                            <p className="text-sm text-muted-foreground">{text('未声明固定参数。', 'No constants declared.')}</p>
                          ) : (
                            Object.entries(planPackage.constants).map(([key, value]) => (
                              <div key={key} className="rounded-md border border-slate-300 bg-slate-50 px-3 py-2">
                                <p className="text-xs font-medium text-slate-500">{key}</p>
                                <p className="mt-1 break-words text-sm text-slate-800">{compactValue(value)}</p>
                              </div>
                            ))
                          )}
                        </div>
                      </CardContent>
                    </Card>
                  </div>
                ) : (
                  <Card>
                    <CardContent className="py-8 text-sm text-muted-foreground">
                      {text('此 PlanPackage 暂无展示视图。', 'Presentation view is not available for this package.')}
                    </CardContent>
                  </Card>
                )}
              </TabsContent>

              <TabsContent value="narrative" className="space-y-4">
                <div className="space-y-4">
                  <Card>
                    <CardHeader className="pb-3">
                      <CardTitle className="flex items-center gap-2 text-base">
                        <Lightbulb className="h-4 w-4 text-amber-600" />
                        {text('创意锚点', 'Idea anchor')}
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4 text-sm text-slate-800">
                      <div className="space-y-2">
                        <p className="font-semibold text-slate-950">{planPackage.idea.title}</p>
                        <p>{planPackage.idea.problem}</p>
                        {planPackage.idea.hypothesisStatement && <p className="text-slate-700">{planPackage.idea.hypothesisStatement}</p>}
                      </div>
                      <DisclosureBlock
                        title={text('创意详情', 'Idea details')}
                        summary={text('方法、预期结果、评分、审查意见与相关工作', 'Method, expected outcome, scores, critique, and prior work')}
                        icon={<Sparkles className="h-4 w-4 text-amber-600" />}
                      >
                        <div className="space-y-4">
                          <div className="grid gap-3 md:grid-cols-2">
                            <div className="rounded-md border border-slate-300 bg-slate-50 px-3 py-3">
                              <p className="text-xs font-semibold uppercase text-slate-500">{text('核心洞察', 'Key insight')}</p>
                              <p className="mt-2 text-sm text-slate-800">{planPackage.idea.keyInsight || text('未记录核心洞察。', 'No key insight recorded.')}</p>
                            </div>
                            <div className="rounded-md border border-slate-300 bg-slate-50 px-3 py-3">
                              <p className="text-xs font-semibold uppercase text-slate-500">{text('方法', 'Method')}</p>
                              <p className="mt-2 text-sm text-slate-800">{planPackage.idea.proposedMethod || text('未记录方法。', 'No method recorded.')}</p>
                            </div>
                            <div className="rounded-md border border-slate-300 bg-slate-50 px-3 py-3">
                              <p className="text-xs font-semibold uppercase text-slate-500">{text('预期结果', 'Expected outcome')}</p>
                              <p className="mt-2 text-sm text-slate-800">{planPackage.idea.expectedOutcome || text('未记录预期结果。', 'No expected outcome recorded.')}</p>
                            </div>
                            <div className="rounded-md border border-slate-300 bg-slate-50 px-3 py-3 md:col-span-2">
                              <p className="text-xs font-semibold uppercase text-slate-500">{text('评分', 'Scores')}</p>
                              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                                {Object.entries(planPackage.idea.scores).slice(0, 4).map(([key, value]) => (
                                  <div key={key} className="rounded-md border border-slate-300 bg-white px-3 py-3">
                                    <p className="text-xs font-semibold uppercase text-slate-500">{key}</p>
                                    <p className="mt-2 text-sm font-semibold text-slate-900">{compactValue(value)}</p>
                                  </div>
                                ))}
                              </div>
                            </div>
                          </div>
                          {planPackage.idea.critiqueSummary && (
                            <div className="rounded-md border border-slate-300 bg-slate-50 px-3 py-3">
                              <p className="text-xs font-semibold uppercase text-slate-500">{text('审查意见', 'Critique')}</p>
                              <p className="mt-2 text-sm text-slate-800">{planPackage.idea.critiqueSummary}</p>
                            </div>
                          )}
                          {planPackage.idea.closestPriorWork.length > 0 && (
                            <div className="rounded-md border border-slate-300 bg-slate-50 px-3 py-3">
                              <p className="text-xs font-semibold uppercase text-slate-500">{text('最相关工作', 'Closest prior work')}</p>
                              <TextList
                                items={planPackage.idea.closestPriorWork.map((item) => summarizeRecordText(item)).filter(Boolean)}
                                emptyLabel={text('未记录相关工作', 'No prior work recorded')}
                              />
                            </div>
                          )}
                        </div>
                      </DisclosureBlock>
                    </CardContent>
                  </Card>

                  <Card>
                    <CardHeader className="pb-3">
                      <CardTitle className="text-base">{text('研究背景与空白', 'Background and gap')}</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4 text-sm text-slate-800">
                      <div className="space-y-2">
                        <p>{planPackage.background.summary}</p>
                        {planPackage.background.motivation && <p>{planPackage.background.motivation}</p>}
                      </div>
                      <div className="space-y-3">
                        <div>
                          <p className="mb-2 text-xs font-semibold uppercase text-slate-500">{text('现有局限', 'Current limitations')}</p>
                          <TextList items={planPackage.background.currentLimitations} emptyLabel={text('未列出局限', 'No limitations listed')} />
                        </div>
                      </div>
                      <EvidenceChips refs={planPackage.background.evidenceRefs} />

                      <DisclosureBlock
                        title={text('领域背景', 'Domain context')}
                        summary={text(`检索文献中提取了 ${planPackage.background.domainContext.length} 个主题聚类信号`, `${planPackage.background.domainContext.length} cluster signals from retrieved literature`)}
                        icon={<BookOpen className="h-4 w-4 text-indigo-600" />}
                      >
                        <div className="space-y-2">
                          <p className="text-xs text-slate-600">{text('以下是检索层产生的原始主题聚类，默认折叠以保持可读性。', 'These are raw topic clusters from the retrieval layer, kept collapsed to preserve readability.')}</p>
                          <TextList
                            items={planPackage.background.domainContext.map((item) => formatDomainContextSignal(item))}
                            emptyLabel={text('未列出领域背景', 'No domain context listed')}
                          />
                        </div>
                      </DisclosureBlock>

                      <div className="rounded-md border border-slate-300 bg-slate-50 px-3 py-3">
                        <p className="text-xs font-semibold uppercase text-slate-500">{text('研究空白摘要', 'Gap summary')}</p>
                        <p className="mt-2 text-sm text-slate-800">{planPackage.gap.summary}</p>
                        {planPackage.gap.selectedGapId && <p className="mt-2 text-xs text-slate-600">{text('选定空白', 'Selected gap')}: {planPackage.gap.selectedGapId}</p>}
                      </div>

                      <div className={`grid gap-3 ${planPackage.gap.items.length > 1 ? 'lg:grid-cols-2' : ''}`}>
                        {planPackage.gap.items.map((gap) => (
                          <GapItem key={gap.id} gap={gap} />
                        ))}
                      </div>
                    </CardContent>
                  </Card>

                  <Card>
                    <CardHeader className="pb-3">
                      <CardTitle className="flex items-center gap-2 text-base">
                        <BookOpen className="h-4 w-4 text-indigo-600" />
                        {text('研究原理与文献', 'Principle and literature')}
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="grid gap-4 lg:grid-cols-[1fr_1.1fr]">
                      <div className="space-y-4">
                        <div className="space-y-3 text-sm text-slate-800">
                          <p>{planPackage.principle.summary}</p>
                          {planPackage.principle.mechanism && <p>{planPackage.principle.mechanism}</p>}
                          {planPackage.principle.noveltyClaim && (
                            <div className="rounded-md border border-indigo-200 bg-indigo-50 px-3 py-2 text-indigo-950">
                              {planPackage.principle.noveltyClaim}
                            </div>
                          )}
                        </div>
                        <div className="grid gap-4 lg:grid-cols-2">
                          <div>
                            <p className="mb-2 text-xs font-semibold uppercase text-slate-500">{text('假设条件', 'Assumptions')}</p>
                            <TextList items={planPackage.principle.assumptions} emptyLabel={text('未列出假设条件', 'No assumptions listed')} />
                          </div>
                          <div>
                            <p className="mb-2 text-xs font-semibold uppercase text-slate-500">{text('风险', 'Risks')}</p>
                            <TextList items={planPackage.principle.risks} emptyLabel={text('未列出风险', 'No risks listed')} />
                          </div>
                        </div>
                      </div>

                      <div className="space-y-3">
                        <div className="rounded-md border border-slate-300 bg-white px-4 py-3 shadow-sm">
                          <div className="flex items-center justify-between gap-3">
                            <div>
                              <p className="text-sm font-semibold text-slate-900">{text('文献信号', 'Literature signal')}</p>
                              <p className="mt-1 text-xs text-slate-600">{planPackage.literatureSurvey.summary}</p>
                            </div>
                            <Badge variant="outline" className="border-slate-400 text-slate-700">
                              {planPackage.literatureSurvey.papers.length}
                            </Badge>
                          </div>
                          <div className="mt-3 space-y-3">
                            {planPackage.literatureSurvey.papers.slice(0, 2).map((paper) => (
                              <PaperRow key={`${paper.source}-${paper.paperId}`} paper={paper} />
                            ))}
                          </div>
                          {planPackage.literatureSurvey.papers.length > 2 && (
                            <details className="mt-3 rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
                              <summary className="cursor-pointer text-sm font-medium text-slate-800">{text('更多文献', 'More literature')}</summary>
                              <div className="mt-3 space-y-3">
                                {planPackage.literatureSurvey.papers.slice(2).map((paper) => (
                                  <PaperRow key={`${paper.source}-${paper.paperId}`} paper={paper} />
                                ))}
                              </div>
                            </details>
                          )}
                        </div>
                      </div>
                    </CardContent>
                  </Card>
                </div>
              </TabsContent>

              <TabsContent value="implementation" className="space-y-4">
                <Card>
                  <CardHeader className="pb-3">
                    <CardTitle className="flex items-center gap-2 text-base">
                      <Layers3 className="h-4 w-4 text-blue-600" />
                      {text('实施时间线', 'Implementation timeline')}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {readableImplementationStages.length > 0 ? (
                      readableImplementationStages.map((stage) => <ReadableStageBlock key={stage.id} stage={stage} />)
                    ) : (
                      <p className="text-sm text-muted-foreground">{text('暂无实施阶段。', 'No stages available.')}</p>
                    )}
                  </CardContent>
                </Card>
              </TabsContent>

              <TabsContent value="evidence" className="space-y-4">
                <Card>
                  <CardHeader className="pb-3">
                    <CardTitle className="flex items-center gap-2 text-base">
                      <ShieldCheck className="h-4 w-4 text-indigo-700" />
                      {text('审查快照', 'Review snapshot')}
                    </CardTitle>
                    <CardDescription>{text('优先展示摘要，需要时再展开审查委员会详情。', 'Summary first, reviewer committee details only on demand.')}</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <div className="grid gap-3 md:grid-cols-3">
                      <StatCard label={text('决策', 'Decision')} value={summaryReviewDecision} detail={`${(summaryReviewScore * 100).toFixed(0)} / 100`} />
                      <StatCard label={text('模式', 'Mode')} value={summaryReviewerMode} detail={summaryReviewerUsed ? text('使用 LLM 审查', 'LLM reviewer used') : text('仅确定性检查', 'Deterministic only')} />
                      <StatCard
                        label={text('下一步行动', 'Next actions')}
                        value={text(`${presentation?.nextActions.length || 0} 项`, `${presentation?.nextActions.length || 0} items`)}
                        detail={presentation?.nextActions[0] || text('暂无下一步行动', 'No next actions')}
                      />
                    </div>
                    <p className="text-sm text-slate-600">
                      {text('详细迭代说明已归入“摘要”页签，使该视图聚焦于审查决策和证据链。', 'Detailed iteration notes are grouped in the Summary tab so this review view stays focused on the decision and evidence chain.')}
                    </p>
                  </CardContent>
                </Card>

                <div className="grid gap-4 xl:grid-cols-[1.4fr_1fr]">
                  <Card>
                    <CardHeader className="pb-3">
                      <CardTitle className="flex items-center gap-2 text-base">
                        <Network className="h-4 w-4 text-indigo-700" />
                        {text('证据图谱', 'Evidence map')}
                      </CardTitle>
                      <CardDescription>
                        {text(
                          `${planPackage.evidenceTrace.structuredPaperIds.length + planPackage.evidenceTrace.selectedPaperIds.length} 条文献引用，${planPackage.evidenceTrace.reasoningKgId ? '已关联推理图谱' : '无推理图谱 ID'}，${planPackage.evidenceTrace.probeResultIds.length} 项 probe 检查。`,
                          `${planPackage.evidenceTrace.structuredPaperIds.length + planPackage.evidenceTrace.selectedPaperIds.length} literature references, ${planPackage.evidenceTrace.reasoningKgId ? 'reasoning graph attached' : 'no reasoning graph id'}, ${planPackage.evidenceTrace.probeResultIds.length} probe checks.`,
                        )}
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                        <EvidenceCoverageCard
                          label={text('研究创意', 'Idea')}
                          value={shortId(planPackage.evidenceTrace.ideaCandidateId)}
                          detail={planPackage.idea.title || text('选定候选', 'Selected candidate')}
                          ok={Boolean(planPackage.evidenceTrace.ideaCandidateId)}
                        />
                        <EvidenceCoverageCard
                          label={text('论文', 'Papers')}
                          value={String(planPackage.evidenceTrace.structuredPaperIds.length + planPackage.evidenceTrace.selectedPaperIds.length)}
                          detail={text(`${evidencePapers.length} 篇已匹配摘要`, `${evidencePapers.length} matched to summaries`)}
                          ok={planPackage.evidenceTrace.structuredPaperIds.length + planPackage.evidenceTrace.selectedPaperIds.length > 0}
                        />
                        <EvidenceCoverageCard
                          label={text('推理图谱', 'Reasoning Graph')}
                          value={planPackage.evidenceTrace.reasoningKgId ? text('已关联', 'Linked') : text('缺失', 'Missing')}
                          detail={planPackage.evidenceTrace.reasoningKgId ? shortId(planPackage.evidenceTrace.reasoningKgId) : text('无 KG artifact ID', 'No KG artifact id')}
                          ok={Boolean(planPackage.evidenceTrace.reasoningKgId)}
                        />
                        <EvidenceCoverageCard
                          label="Probe"
                          value={String(planPackage.evidenceTrace.probeResultIds.length)}
                          detail={text(`${planPackage.evidenceTrace.graphPatchIds.length} 个图谱补丁`, `${planPackage.evidenceTrace.graphPatchIds.length} graph patches`)}
                          ok={planPackage.evidenceTrace.probeResultIds.length > 0 || planPackage.evidenceTrace.graphPatchIds.length > 0}
                        />
                      </div>

                      <div className="rounded-md border border-slate-300 bg-white px-4 py-3 shadow-sm">
                        <p className="text-sm font-semibold text-slate-900">{text('证据路径', 'Evidence path')}</p>
                        <div className="mt-3 grid gap-3 lg:grid-cols-5">
                          {[
                            {
                              label: text('选定创意', 'Selected idea'),
                              value: planPackage.idea.title || shortId(planPackage.evidenceTrace.ideaCandidateId),
                              ok: Boolean(planPackage.evidenceTrace.ideaCandidateId),
                            },
                            {
                              label: text('研究空白', 'Gap'),
                              value: planPackage.gap.selectedGapId || planPackage.gap.summary,
                              ok: Boolean(planPackage.gap.selectedGapId || planPackage.gap.items.length),
                            },
                            {
                              label: text('文献', 'Literature'),
                              value: text(`${planPackage.literatureSurvey.papers.length} 篇论文摘要`, `${planPackage.literatureSurvey.papers.length} paper summaries`),
                              ok: planPackage.literatureSurvey.papers.length > 0,
                            },
                            {
                              label: text('推理', 'Reasoning'),
                              value: planPackage.evidenceTrace.reasoningKgId ? shortId(planPackage.evidenceTrace.reasoningKgId) : text('无图谱 ID', 'No graph id'),
                              ok: Boolean(planPackage.evidenceTrace.reasoningKgId),
                            },
                            {
                              label: text('计划就绪度', 'Plan readiness'),
                              value: planPackage.qualityGate.evidenceValid ? text('证据有效', 'Evidence valid') : text('需要审查', 'Needs review'),
                              ok: planPackage.qualityGate.evidenceValid,
                            },
                          ].map((item, index) => (
                            <div key={item.label} className="relative rounded-md border border-slate-300 bg-slate-50 px-3 py-3">
                              <div className="flex items-center gap-2">
                                <span className={`flex h-6 w-6 items-center justify-center rounded-full text-xs font-semibold text-white ${item.ok ? 'bg-emerald-700' : 'bg-amber-700'}`}>
                                  {index + 1}
                                </span>
                                <p className="text-xs font-semibold uppercase text-slate-600">{item.label}</p>
                              </div>
                              <p className="mt-2 break-words text-sm text-slate-900">{item.value}</p>
                            </div>
                          ))}
                        </div>
                      </div>

                      <div className="grid gap-4 xl:grid-cols-[1.5fr_1fr]">
                        <div className="rounded-md border border-slate-300 bg-white px-4 py-3 shadow-sm">
                          <div className="flex items-center justify-between gap-3">
                            <p className="text-sm font-semibold text-slate-900">{text('支持性论文', 'Supporting papers')}</p>
                            <Badge variant="outline" className="border-slate-400 text-slate-700">
                              {evidencePapers.length || planPackage.literatureSurvey.papers.length}
                            </Badge>
                          </div>
                          <div className="mt-3 space-y-3">
                            {(evidencePapers.length ? evidencePapers : planPackage.literatureSurvey.papers.slice(0, 5)).map((paper) => (
                              <div key={`${paper.source}-${paper.paperId}`} className="rounded-md border border-slate-300 bg-slate-50 px-3 py-3">
                                <div className="flex flex-wrap items-center gap-2">
                                  <Badge className={paper.source === 'probe' ? 'bg-indigo-700 text-white' : 'bg-blue-700 text-white'}>
                                    {paper.source}
                                  </Badge>
                                  <span className="font-mono text-xs text-slate-600">{shortId(paper.paperId)}</span>
                                  {paper.year ? <span className="text-xs text-slate-600">{paper.year}</span> : null}
                                </div>
                                <p className="mt-2 text-sm font-semibold text-slate-950">{paper.title}</p>
                                <p className="mt-1 text-sm text-slate-700">{paper.summary}</p>
                                {paper.limitations.length > 0 && <p className="mt-2 text-xs text-slate-600">{text('局限', 'Limitation')}: {paper.limitations[0]}</p>}
                              </div>
                            ))}
                            {evidencePapers.length === 0 && planPackage.literatureSurvey.papers.length === 0 && (
                              <p className="text-sm text-muted-foreground">{text('未关联论文摘要。', 'No paper summaries are attached.')}</p>
                            )}
                          </div>
                        </div>

                        <div className="space-y-4">
                          <ReviewerCommitteeDisclosure reports={planPackage.reviewReports} />

                          {planPackage.revisions.length > 0 && (
                            <details className="rounded-md border border-slate-300 bg-white px-4 py-3 shadow-sm">
                              <summary className="cursor-pointer text-sm font-medium text-slate-800">{text('修订历史', 'Revision history')}</summary>
                              <div className="mt-3 space-y-2">
                                {planPackage.revisions.slice(0, 6).map((revision) => (
                                  <div key={revision.id} className="rounded-md border border-slate-300 bg-slate-50 px-3 py-2 text-sm">
                                    <div className="flex flex-wrap items-center gap-2">
                                      <Badge variant="outline" className="font-mono text-[11px]">
                                        {revision.id}
                                      </Badge>
                                      <Badge variant="secondary">{revision.generationMode}</Badge>
                                      <span className="text-xs text-slate-500">{new Date(revision.createdAt).toLocaleString()}</span>
                                    </div>
                                    <p className="mt-2 text-slate-800">{revision.summary}</p>
                                    {revision.changedSections.length > 0 && <p className="mt-1 text-xs text-slate-600">{text('已修改', 'Changed')}: {revision.changedSections.join(', ')}</p>}
                                  </div>
                                ))}
                              </div>
                            </details>
                          )}

                          {evidencePaperIdsWithoutSummary.length > 0 && (
                            <div className="rounded-md border border-amber-300 bg-white px-4 py-3 shadow-sm">
                              <p className="text-sm font-semibold text-amber-900">{text('缺少摘要的引用 ID', 'Referenced IDs without summaries')}</p>
                              <div className="mt-3">
                                <TextList items={evidencePaperIdsWithoutSummary.map(shortId)} emptyLabel={text('所有引用 ID 均有摘要', 'All referenced IDs are summarized')} />
                              </div>
                            </div>
                          )}

                          <details className="rounded-md border border-slate-300 bg-white px-4 py-3 shadow-sm">
                            <summary className="cursor-pointer text-sm font-medium text-slate-800">{text('原始 PlanPackage 快照', 'Raw package snapshot')}</summary>
                            <pre className="mt-3 max-h-80 overflow-auto rounded bg-slate-950 p-3 text-xs text-slate-100">
                              {JSON.stringify(
                                {
                                  traceIds: {
                                    ideaCandidateId: planPackage.evidenceTrace.ideaCandidateId,
                                    searchNodeId: planPackage.evidenceTrace.searchNodeId,
                                    pathSeedId: planPackage.evidenceTrace.pathSeedId,
                                    reasoningKgId: planPackage.evidenceTrace.reasoningKgId,
                                    literatureMapId: planPackage.evidenceTrace.literatureMapId,
                                  },
                                  reasoningTrace: planPackage.evidenceTrace.reasoningTrace,
                                  candidateGraphEvidence: planPackage.evidenceTrace.candidateGraphEvidence,
                                  sourceFields: planPackage.sourceFields,
                                  downstreamContract: planPackage.downstreamContract,
                                },
                                null,
                                2
                              )}
                            </pre>
                          </details>
                        </div>
                      </div>
                    </CardContent>
                  </Card>
                </div>
              </TabsContent>
          </Tabs>
          </div>
          <aside className="space-y-6 xl:sticky xl:top-6">
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base">{text('计划快照', 'Plan snapshot')}</CardTitle>
                <CardDescription>{text('快速查看状态、评分和结构。', 'Quick status, score, and structure at a glance.')}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {planStats && (
                  <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-1">
                    <StatCard label={text('状态', 'Status')} value={planStats.status} detail={planStats.readiness} />
                    <StatCard label={text('评分', 'Score')} value={planStats.score} detail={summaryTitle} />
                    <StatCard label={text('结构', 'Structure')} value={planStats.stages} detail={planStats.steps} />
                    <StatCard
                      label={text('证据', 'Evidence')}
                      value={planStats.papers}
                      detail={text(`${planPackage.literatureSurvey.coverage.structuredPaperCount} 篇结构化论文`, `${planPackage.literatureSurvey.coverage.structuredPaperCount} structured`)}
                    />
                    <StatCard label={text('创建时间', 'Created')} value={new Date(planPackage.createdAt).toLocaleDateString()} detail={planPackage.schemaVersion} />
                  </div>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base">{text('质量与迭代', 'Quality and iteration')}</CardTitle>
                <CardDescription>{text('内部检查集中展示在此处，避免干扰主要阅读路径。', 'Internal checks stay here instead of crowding the main reading path.')}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <QualityGateSummary gate={gate} />
                <IterationNotesDisclosure
                  gate={gate}
                  generationWarnings={planPackage.generation.warnings}
                  reviewSummary={presentation?.reviewSummary}
                  metaReview={planPackage.metaReview}
                />
                {planPackage.metaReview && (
                  <div className="rounded-md border border-slate-300 bg-white px-4 py-3 shadow-sm">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <p className="text-sm font-semibold text-slate-900">{text('审查决策', 'Reviewer decision')}</p>
                        <p className="mt-1 text-xs text-slate-600">
                          {text('置信度', 'confidence')} {(planPackage.metaReview.confidence * 100).toFixed(0)} · {planPackage.metaReview.blockingIssues.length} {text('个阻断问题', 'blocking issues')}
                        </p>
                      </div>
                      <Badge className={planPackage.metaReview.decision === 'approve' ? 'bg-emerald-700 text-white' : 'bg-amber-700 text-white'}>
                        {planPackage.metaReview.decision}
                      </Badge>
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base">{text('反馈', 'Feedback')}</CardTitle>
                <CardDescription>{text('当前 PlanPackage 的人工编辑与修订历史。', 'Human edits and revision history for the current plan package.')}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div>
                  <div className="mb-3 flex items-center gap-2">
                    <MessageSquareText className="h-4 w-4 text-indigo-700" />
                    <p className="text-sm font-semibold text-slate-900">{text('人工反馈', 'Human feedback')}</p>
                  </div>
                  <textarea
                    value={feedbackComment}
                    onChange={(event) => setFeedbackComment(event.target.value)}
                    placeholder={text('告诉 FAROS 在交付前需要修改哪些内容。', 'Tell FAROS what to change before handoff.')}
                    className="min-h-[118px] w-full rounded-md border border-slate-400 bg-white px-3 py-2 text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-indigo-600"
                  />
                  <Button
                    className="mt-3 bg-indigo-700 text-white hover:bg-indigo-800"
                    onClick={submitFeedbackAndRevise}
                    disabled={!feedbackComment.trim() || isSubmittingFeedback || isRevising}
                  >
                    {isSubmittingFeedback || isRevising ? <RefreshCw className="mr-2 h-4 w-4 animate-spin" /> : <Sparkles className="mr-2 h-4 w-4" />}
                    {text('根据反馈修订', 'Revise from Feedback')}
                  </Button>
                </div>
                <div>
                  <p className="mb-3 text-sm font-semibold text-slate-900">{text('反馈历史', 'Feedback history')}</p>
                  <FeedbackList feedback={planPackage.humanFeedback} />
                </div>
              </CardContent>
            </Card>
          </aside>
        </div>
      )}
    </div>
  )
}
