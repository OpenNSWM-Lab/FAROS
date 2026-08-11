import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
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

type GenerationMode = 'hybrid' | 'deterministic'
type ReviewerMode = 'deterministic' | 'hybrid'

const DEFAULT_REVIEWER_MODE: ReviewerMode = 'hybrid'

const EMPTY_GATE: PlanQualityGate = {
  schemaValid: false,
  evidenceValid: false,
  topicRelevant: false,
  citationFaithful: false,
  planSpecific: false,
  agentApproved: false,
  humanApproved: false,
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
  const rows = [
    { label: 'Schema', ok: gate.schemaValid },
    { label: 'Evidence', ok: gate.evidenceValid },
    { label: 'Topic', ok: gate.topicRelevant },
    { label: 'Citation', ok: gate.citationFaithful },
    { label: 'Plan', ok: gate.planSpecific },
    { label: 'Agent', ok: gate.agentApproved },
    { label: 'Human', ok: gate.humanApproved },
    { label: 'Ready', ok: gate.implementationReady },
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
          {open ? 'Collapse' : 'Expand'}
        </span>
      </button>

      {open && <div className="mt-3">{children}</div>}
    </div>
  )
}

function ReadableStageBlock({ stage }: { stage: PlanReadableStage }) {
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
              Stage {stage.order}
            </Badge>
            <h3 className="text-base font-semibold text-slate-900">{stage.title}</h3>
          </div>
          <p className="mt-2 text-sm text-slate-800">{stage.goal}</p>
          <p className="mt-2 text-xs text-slate-600">{stage.method}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2 text-xs text-slate-600">
          <Badge variant="secondary" className="shrink-0">
            {stage.steps.length} steps
          </Badge>
          <span className="flex items-center gap-1 rounded-full border border-slate-200 bg-slate-50 px-2 py-1">
            <ChevronDown className={`h-3.5 w-3.5 text-slate-500 transition-transform ${expanded ? 'rotate-180' : ''}`} />
            {expanded ? 'Collapse' : 'Expand'}
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
                      <Badge variant="secondary">Step {step.order}</Badge>
                      <Badge variant="outline">{outputs.length} outputs</Badge>
                      <Badge variant="outline">{expected.length} metrics</Badge>
                      <span className="flex items-center gap-1 rounded-full border border-slate-200 bg-white px-2 py-1">
                        <ChevronDown className={`h-3.5 w-3.5 text-slate-500 transition-transform ${stepOpen ? 'rotate-180' : ''}`} />
                        {stepOpen ? 'Collapse' : 'Expand'}
                      </span>
                    </div>
                  </button>

                  {stepOpen && (
                    <div className="mt-3 grid gap-3 md:grid-cols-2">
                      <div>
                        <p className="mb-2 text-xs font-semibold uppercase text-slate-500">Outputs</p>
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
                        <p className="mb-2 text-xs font-semibold uppercase text-slate-500">Expected</p>
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
              relevance {(paper.relevanceScore * 100).toFixed(0)}
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
          <p className="text-xs font-semibold uppercase text-slate-500">Methods</p>
          <TextList items={methods} emptyLabel="No method summary" />
        </div>
        <div>
          <p className="text-xs font-semibold uppercase text-slate-500">Findings</p>
          <TextList items={findings} emptyLabel="No finding summary" />
        </div>
        <div>
          <p className="text-xs font-semibold uppercase text-slate-500">Limitations</p>
          <TextList items={paper.limitations.slice(0, 3)} emptyLabel="No limitation summary" />
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
  const [open, setOpen] = useState(false)

  return (
    <div className="rounded-md border border-slate-300 bg-white px-4 py-3 shadow-sm">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-3 text-left"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <span className="text-sm font-medium text-slate-800">Reviewer committee details</span>
        <span className="flex items-center gap-1 rounded-full border border-slate-200 bg-slate-50 px-2 py-1 text-xs text-slate-600">
          <ChevronDown className={`h-3.5 w-3.5 text-slate-500 transition-transform ${open ? 'rotate-180' : ''}`} />
          {open ? 'Collapse' : 'Expand'}
        </span>
      </button>
      {open && (
        <div className="mt-3 space-y-3">
          {reports.length === 0 ? (
            <p className="text-sm text-muted-foreground">No reviewer reports yet. They are generated automatically when a package is created or revised.</p>
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
    ? `${itemCount} internal notes grouped from validation, reviewer feedback, and generation warnings`
    : 'No open iteration notes. The current package is clean enough for the next pass.'

  return (
    <DisclosureBlock
      title="Iteration notes"
      summary={summary}
      icon={<RefreshCw className="h-4 w-4 text-slate-600" />}
      defaultOpen={false}
    >
      <div className={`grid gap-4 ${refinementItems.length > 0 && watchItems.length > 0 ? 'lg:grid-cols-2' : ''}`}>
        <div>
          <p className="mb-2 text-xs font-semibold uppercase text-slate-500">Needs refinement</p>
          <TextList items={refinementItems} emptyLabel="No refinement items" />
        </div>
        <div>
          <p className="mb-2 text-xs font-semibold uppercase text-slate-500">Watch list</p>
          <TextList items={watchItems} emptyLabel="No watch list items" />
        </div>
      </div>
    </DisclosureBlock>
  )
}

function FeedbackList({ feedback }: { feedback: PlanHumanFeedback[] }) {
  if (!feedback.length) {
    return <p className="text-sm text-muted-foreground">No human feedback yet.</p>
  }
  return (
    <div className="space-y-2">
      {feedback.slice(0, 6).map((item) => (
        <div key={item.id} className={`rounded-md border px-3 py-2 text-sm ${item.resolved ? 'border-emerald-200 bg-emerald-50' : 'border-slate-300 bg-white'}`}>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Badge variant={item.resolved ? 'secondary' : 'default'}>
              {item.resolved ? 'Resolved' : 'Pending'}
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
  const [searchParams, setSearchParams] = useSearchParams()
  const [activeTab, setActiveTab] = useState('summary')
  const [planPackage, setPlanPackage] = useState<PlanPackage | null>(null)
  const [presentation, setPresentation] = useState<PlanPackagePresentation | null>(null)
  const [packageIdInput, setPackageIdInput] = useState(searchParams.get('packageId')?.trim() || '')
  const [isLoading, setIsLoading] = useState(false)
  const [isCreating, setIsCreating] = useState(false)
  const [isRevising, setIsRevising] = useState(false)
  const [isApproving, setIsApproving] = useState(false)
  const [isSubmittingFeedback, setIsSubmittingFeedback] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [generationMode, setGenerationMode] = useState<GenerationMode>('hybrid')
  const [maxStages, setMaxStages] = useState(3)
  const [maxStepsPerStage, setMaxStepsPerStage] = useState(3)
  const [maxReviewIterations, setMaxReviewIterations] = useState(2)
  const [advancedGenerationOpen, setAdvancedGenerationOpen] = useState(false)
  const [userNotes, setUserNotes] = useState('')
  const [feedbackComment, setFeedbackComment] = useState('')

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
      })
      .catch((err) => {
        if (cancelled) return
        if (err instanceof Error && err.message.includes('not found')) {
          setPlanPackage(null)
          setPresentation(null)
          setError(null)
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
  }, [ideaSessionIdFromUrl, loadPackage, packageIdFromUrl])

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

  const createPackage = async () => {
    if (!ideaSessionIdFromUrl) {
      setError('Open this page from an Idea candidate or paste a PlanPackage ID.')
      return
    }
    setIsCreating(true)
    setError(null)
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
      updatePackageUrl(response.packageId)
      setActiveTab('summary')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create PlanPackage')
    } finally {
      setIsCreating(false)
    }
  }

  const approveCurrentPackage = async () => {
    if (!planPackage) return
    setIsApproving(true)
    setError(null)
    try {
      const approved = await approvePlanPackageWithMode(planPackage.packageId, DEFAULT_REVIEWER_MODE)
      setPlanPackage(approved)
      setPresentation(await getPlanPackagePresentation(approved.packageId))
      openCodeWorkspace(approved.packageId)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to approve PlanPackage')
    } finally {
      setIsApproving(false)
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

  const planStats = useMemo(() => {
    if (!planPackage) return null
    return {
      status: planPackage.status,
      score: `${(planPackage.qualityGate.overallScore * 100).toFixed(0)} / 100`,
      readiness: planPackage.qualityGate.implementationReady ? 'Ready for handoff' : 'Iterating internally',
      stages: `${planPackage.stages.length} stages`,
      steps: `${totalSteps} steps`,
      papers: `${planPackage.literatureSurvey.papers.length} papers`,
    }
  }, [planPackage, totalSteps])

  const gate = planPackage?.qualityGate ?? EMPTY_GATE
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
                PlanPackage Workspace
              </CardTitle>
              <CardDescription className="mt-1">
                Primary handoff for the idea + plan stage. Quality checks run automatically during generation, feedback revision, and approval.
              </CardDescription>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button onClick={approveCurrentPackage} disabled={!planPackage || isApproving} className="bg-emerald-700 text-white hover:bg-emerald-800">
                {isApproving ? <RefreshCw className="mr-2 h-4 w-4 animate-spin" /> : <UserCheck className="mr-2 h-4 w-4" />}
                Approve & Open Code
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 lg:grid-cols-[1fr_auto]">
            <input
              value={packageIdInput}
              onChange={(event) => setPackageIdInput(event.target.value)}
              placeholder="ppkg_..."
              className="h-10 w-full rounded-md border border-slate-400 px-3 text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-indigo-600"
            />
            <Button variant="outline" onClick={loadByInput} disabled={!packageIdInput.trim() || isLoading}>
              {isLoading ? <RefreshCw className="mr-2 h-4 w-4 animate-spin" /> : <FileJson className="mr-2 h-4 w-4" />}
              Load Package
            </Button>
          </div>

          {(ideaSessionIdFromUrl || ideaCandidateIdFromUrl) && (
            <div className="rounded-md border border-l-4 border-slate-300 border-l-indigo-700 bg-white px-4 py-3 shadow-sm">
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
                <Button onClick={createPackage} disabled={isCreating || !ideaSessionIdFromUrl} className="bg-indigo-700 text-white hover:bg-indigo-800">
                  {isCreating ? <RefreshCw className="mr-2 h-4 w-4 animate-spin" /> : <Sparkles className="mr-2 h-4 w-4" />}
                  Generate PlanPackage
                </Button>
              </div>
              <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-xs text-slate-600">
                <button
                  type="button"
                  onClick={() => setAdvancedGenerationOpen((value) => !value)}
                  className="inline-flex items-center gap-1 font-medium text-slate-700 hover:text-slate-950"
                  aria-expanded={advancedGenerationOpen}
                >
                  <ChevronDown className={`h-3.5 w-3.5 transition-transform ${advancedGenerationOpen ? 'rotate-180' : ''}`} />
                  {advancedGenerationOpen ? 'Hide advanced generation settings' : 'Show advanced generation settings'}
                </button>
                <span className="text-slate-500">
                  {generationMode === 'hybrid' ? 'Hybrid LLM' : 'Deterministic'} · {maxStages} stages · {maxStepsPerStage} steps/stage · {maxReviewIterations} review passes
                </span>
              </div>
              {advancedGenerationOpen && (
                <div className="mt-3 border-t border-slate-200 pt-3">
                  <div className="grid gap-3 md:grid-cols-4">
                    <label className="space-y-1 text-xs font-medium text-slate-700">
                      Generation
                      <select
                        value={generationMode}
                        onChange={(event) => setGenerationMode(event.target.value as GenerationMode)}
                        className="h-9 w-full rounded-md border border-slate-400 bg-white px-2 text-sm text-slate-900"
                      >
                        <option value="hybrid">Hybrid LLM</option>
                        <option value="deterministic">Deterministic</option>
                      </select>
                    </label>
                    <label className="space-y-1 text-xs font-medium text-slate-700">
                      Max stages: {maxStages}
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
                      Max steps/stage: {maxStepsPerStage}
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
                      Review iterations: {maxReviewIterations}
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
                    placeholder="Optional planning constraints for this package"
                    className="mt-3 min-h-[72px] w-full rounded-md border border-slate-400 bg-white px-3 py-2 text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-indigo-600"
                  />
                </div>
              )}
            </div>
          )}

          {error && (
            <div className="rounded-md border border-l-4 border-red-300 border-l-red-700 bg-white px-4 py-3 text-sm text-red-800 shadow-sm">
              {error}
            </div>
          )}
        </CardContent>
      </Card>

      {!planPackage && !isLoading && (
        <Card className="border-slate-200">
          <CardContent className="flex flex-col items-center gap-3 py-10 text-center">
            <FileJson className="h-10 w-10 text-slate-400" />
            <div>
              <p className="font-medium text-slate-900">No PlanPackage loaded</p>
              <p className="mt-1 text-sm text-muted-foreground">
                Start from a completed Idea candidate or paste a package ID above.
              </p>
            </div>
            <Button variant="outline" onClick={() => navigate('/research/ideas')}>
              <Lightbulb className="mr-2 h-4 w-4" />
              Open Ideas
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
                      {planPackage.status}
                    </Badge>
                    <Badge className={planPackage.generation.fallbackUsed ? 'bg-amber-700 text-white' : 'bg-emerald-700 text-white'}>
                      {planPackage.generation.mode}
                    </Badge>
                    {planPackage.reviewReports.length > 0 && (
                      <Badge variant="outline" className="border-emerald-400 bg-emerald-50 text-emerald-900">
                        Quality checked
                      </Badge>
                    )}
                    <Badge variant="secondary">{planPackage.schemaVersion}</Badge>
                    <Badge variant="outline" className="font-mono">
                      score {(planPackage.qualityGate.overallScore * 100).toFixed(0)}
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
                {planStats && (
                  <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
                    <StatCard label="Status" value={planStats.status} detail={planStats.readiness} />
                    <StatCard label="Score" value={planStats.score} detail={summaryTitle} />
                    <StatCard label="Structure" value={planStats.stages} detail={planStats.steps} />
                    <StatCard
                      label="Evidence"
                      value={planStats.papers}
                      detail={`${planPackage.literatureSurvey.coverage.structuredPaperCount} structured`}
                    />
                    <StatCard label="Created" value={new Date(planPackage.createdAt).toLocaleDateString()} detail={planPackage.schemaVersion} />
                  </div>
                )}
              </CardContent>
            </Card>

            <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-4">
              <TabsList className="h-auto flex-wrap justify-start gap-1">
                <TabsTrigger value="summary">Summary</TabsTrigger>
                <TabsTrigger value="narrative">Narrative</TabsTrigger>
                <TabsTrigger value="implementation">Implementation</TabsTrigger>
                <TabsTrigger value="evidence">Evidence</TabsTrigger>
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
                            <p className="text-xs font-semibold uppercase text-slate-500">Research question</p>
                            <p className="mt-2 text-sm text-slate-800">{summaryQuestion}</p>
                          </div>
                          <div className="rounded-md border border-slate-300 bg-slate-50 px-3 py-3">
                            <p className="text-xs font-semibold uppercase text-slate-500">Hypothesis</p>
                            <p className="mt-2 text-sm text-slate-800">{summaryHypothesis}</p>
                          </div>
                        </div>
                        {presentation.nextActions.length > 0 && (
                          <div className="rounded-md border border-slate-300 bg-white px-3 py-3">
                            <p className="text-xs font-semibold uppercase text-slate-500">Next actions</p>
                            <div className="mt-2">
                              <TextList items={presentation.nextActions.slice(0, 3)} emptyLabel="No next actions" />
                            </div>
                          </div>
                        )}
                      </CardContent>
                    </Card>

                    <Card>
                      <CardHeader className="pb-3">
                        <CardTitle className="text-base">Plan frame</CardTitle>
                      </CardHeader>
                      <CardContent className="space-y-3">
                        <div className="grid gap-3 md:grid-cols-2">
                          <StatCard label="Stages" value={`${planPackage.stages.length}`} detail={`${totalSteps} steps`} />
                          <StatCard
                            label="Papers"
                            value={`${planPackage.literatureSurvey.papers.length}`}
                            detail={`${planPackage.literatureSurvey.coverage.structuredPaperCount} structured · ${planPackage.literatureSurvey.coverage.probePaperCount} probe`}
                          />
                        </div>
                        <div className="grid gap-2 md:grid-cols-2">
                          {Object.keys(planPackage.constants).length === 0 ? (
                            <p className="text-sm text-muted-foreground">No constants declared.</p>
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
                      Presentation view is not available for this package.
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
                        Idea anchor
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4 text-sm text-slate-800">
                      <div className="space-y-2">
                        <p className="font-semibold text-slate-950">{planPackage.idea.title}</p>
                        <p>{planPackage.idea.problem}</p>
                        {planPackage.idea.hypothesisStatement && <p className="text-slate-700">{planPackage.idea.hypothesisStatement}</p>}
                      </div>
                      <DisclosureBlock
                        title="Idea details"
                        summary="Method, expected outcome, scores, critique, and prior work"
                        icon={<Sparkles className="h-4 w-4 text-amber-600" />}
                      >
                        <div className="space-y-4">
                          <div className="grid gap-3 md:grid-cols-2">
                            <div className="rounded-md border border-slate-300 bg-slate-50 px-3 py-3">
                              <p className="text-xs font-semibold uppercase text-slate-500">Key insight</p>
                              <p className="mt-2 text-sm text-slate-800">{planPackage.idea.keyInsight || 'No key insight recorded.'}</p>
                            </div>
                            <div className="rounded-md border border-slate-300 bg-slate-50 px-3 py-3">
                              <p className="text-xs font-semibold uppercase text-slate-500">Method</p>
                              <p className="mt-2 text-sm text-slate-800">{planPackage.idea.proposedMethod || 'No method recorded.'}</p>
                            </div>
                            <div className="rounded-md border border-slate-300 bg-slate-50 px-3 py-3">
                              <p className="text-xs font-semibold uppercase text-slate-500">Expected outcome</p>
                              <p className="mt-2 text-sm text-slate-800">{planPackage.idea.expectedOutcome || 'No expected outcome recorded.'}</p>
                            </div>
                            <div className="rounded-md border border-slate-300 bg-slate-50 px-3 py-3 md:col-span-2">
                              <p className="text-xs font-semibold uppercase text-slate-500">Scores</p>
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
                              <p className="text-xs font-semibold uppercase text-slate-500">Critique</p>
                              <p className="mt-2 text-sm text-slate-800">{planPackage.idea.critiqueSummary}</p>
                            </div>
                          )}
                          {planPackage.idea.closestPriorWork.length > 0 && (
                            <div className="rounded-md border border-slate-300 bg-slate-50 px-3 py-3">
                              <p className="text-xs font-semibold uppercase text-slate-500">Closest prior work</p>
                              <TextList
                                items={planPackage.idea.closestPriorWork.map((item) => summarizeRecordText(item)).filter(Boolean)}
                                emptyLabel="No prior work recorded"
                              />
                            </div>
                          )}
                        </div>
                      </DisclosureBlock>
                    </CardContent>
                  </Card>

                  <Card>
                    <CardHeader className="pb-3">
                      <CardTitle className="text-base">Background and gap</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4 text-sm text-slate-800">
                      <div className="space-y-2">
                        <p>{planPackage.background.summary}</p>
                        {planPackage.background.motivation && <p>{planPackage.background.motivation}</p>}
                      </div>
                      <div className="space-y-3">
                        <div>
                          <p className="mb-2 text-xs font-semibold uppercase text-slate-500">Current limitations</p>
                          <TextList items={planPackage.background.currentLimitations} emptyLabel="No limitations listed" />
                        </div>
                      </div>
                      <EvidenceChips refs={planPackage.background.evidenceRefs} />

                      <DisclosureBlock
                        title="Domain context"
                        summary={`${planPackage.background.domainContext.length} cluster signals from retrieved literature`}
                        icon={<BookOpen className="h-4 w-4 text-indigo-600" />}
                      >
                        <div className="space-y-2">
                          <p className="text-xs text-slate-600">These are raw topic clusters from the retrieval layer, kept collapsed to preserve readability.</p>
                          <TextList
                            items={planPackage.background.domainContext.map((item) => formatDomainContextSignal(item))}
                            emptyLabel="No domain context listed"
                          />
                        </div>
                      </DisclosureBlock>

                      <div className="rounded-md border border-slate-300 bg-slate-50 px-3 py-3">
                        <p className="text-xs font-semibold uppercase text-slate-500">Gap summary</p>
                        <p className="mt-2 text-sm text-slate-800">{planPackage.gap.summary}</p>
                        {planPackage.gap.selectedGapId && <p className="mt-2 text-xs text-slate-600">Selected gap: {planPackage.gap.selectedGapId}</p>}
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
                        Principle and literature
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
                            <p className="mb-2 text-xs font-semibold uppercase text-slate-500">Assumptions</p>
                            <TextList items={planPackage.principle.assumptions} emptyLabel="No assumptions listed" />
                          </div>
                          <div>
                            <p className="mb-2 text-xs font-semibold uppercase text-slate-500">Risks</p>
                            <TextList items={planPackage.principle.risks} emptyLabel="No risks listed" />
                          </div>
                        </div>
                      </div>

                      <div className="space-y-3">
                        <div className="rounded-md border border-slate-300 bg-white px-4 py-3 shadow-sm">
                          <div className="flex items-center justify-between gap-3">
                            <div>
                              <p className="text-sm font-semibold text-slate-900">Literature signal</p>
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
                              <summary className="cursor-pointer text-sm font-medium text-slate-800">More literature</summary>
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
                      Implementation timeline
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {readableImplementationStages.length > 0 ? (
                      readableImplementationStages.map((stage) => <ReadableStageBlock key={stage.id} stage={stage} />)
                    ) : (
                      <p className="text-sm text-muted-foreground">No stages available.</p>
                    )}
                  </CardContent>
                </Card>
              </TabsContent>

              <TabsContent value="evidence" className="space-y-4">
                <Card>
                  <CardHeader className="pb-3">
                    <CardTitle className="flex items-center gap-2 text-base">
                      <ShieldCheck className="h-4 w-4 text-indigo-700" />
                      Review snapshot
                    </CardTitle>
                    <CardDescription>Summary first, reviewer committee details only on demand.</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <div className="grid gap-3 md:grid-cols-3">
                      <StatCard label="Decision" value={summaryReviewDecision} detail={`${(summaryReviewScore * 100).toFixed(0)} / 100`} />
                      <StatCard label="Mode" value={summaryReviewerMode} detail={summaryReviewerUsed ? 'LLM reviewer used' : 'Deterministic only'} />
                      <StatCard
                        label="Next actions"
                        value={`${presentation?.nextActions.length || 0} items`}
                        detail={presentation?.nextActions[0] || 'No next actions'}
                      />
                    </div>
                    <p className="text-sm text-slate-600">
                      Detailed iteration notes are grouped in the Summary tab so this review view stays focused on the decision and evidence chain.
                    </p>
                  </CardContent>
                </Card>

                <div className="grid gap-4 xl:grid-cols-[1.4fr_1fr]">
                  <Card>
                    <CardHeader className="pb-3">
                      <CardTitle className="flex items-center gap-2 text-base">
                        <Network className="h-4 w-4 text-indigo-700" />
                        Evidence map
                      </CardTitle>
                      <CardDescription>
                        {planPackage.evidenceTrace.structuredPaperIds.length + planPackage.evidenceTrace.selectedPaperIds.length} literature references,
                        {' '}{planPackage.evidenceTrace.reasoningKgId ? 'reasoning graph attached' : 'no reasoning graph id'},
                        {' '}{planPackage.evidenceTrace.probeResultIds.length} probe checks.
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                        <EvidenceCoverageCard
                          label="Idea"
                          value={shortId(planPackage.evidenceTrace.ideaCandidateId)}
                          detail={planPackage.idea.title || 'Selected candidate'}
                          ok={Boolean(planPackage.evidenceTrace.ideaCandidateId)}
                        />
                        <EvidenceCoverageCard
                          label="Papers"
                          value={String(planPackage.evidenceTrace.structuredPaperIds.length + planPackage.evidenceTrace.selectedPaperIds.length)}
                          detail={`${evidencePapers.length} matched to summaries`}
                          ok={planPackage.evidenceTrace.structuredPaperIds.length + planPackage.evidenceTrace.selectedPaperIds.length > 0}
                        />
                        <EvidenceCoverageCard
                          label="Reasoning Graph"
                          value={planPackage.evidenceTrace.reasoningKgId ? 'Linked' : 'Missing'}
                          detail={planPackage.evidenceTrace.reasoningKgId ? shortId(planPackage.evidenceTrace.reasoningKgId) : 'No KG artifact id'}
                          ok={Boolean(planPackage.evidenceTrace.reasoningKgId)}
                        />
                        <EvidenceCoverageCard
                          label="Probe"
                          value={String(planPackage.evidenceTrace.probeResultIds.length)}
                          detail={`${planPackage.evidenceTrace.graphPatchIds.length} graph patches`}
                          ok={planPackage.evidenceTrace.probeResultIds.length > 0 || planPackage.evidenceTrace.graphPatchIds.length > 0}
                        />
                      </div>

                      <div className="rounded-md border border-slate-300 bg-white px-4 py-3 shadow-sm">
                        <p className="text-sm font-semibold text-slate-900">Evidence path</p>
                        <div className="mt-3 grid gap-3 lg:grid-cols-5">
                          {[
                            {
                              label: 'Selected idea',
                              value: planPackage.idea.title || shortId(planPackage.evidenceTrace.ideaCandidateId),
                              ok: Boolean(planPackage.evidenceTrace.ideaCandidateId),
                            },
                            {
                              label: 'Gap',
                              value: planPackage.gap.selectedGapId || planPackage.gap.summary,
                              ok: Boolean(planPackage.gap.selectedGapId || planPackage.gap.items.length),
                            },
                            {
                              label: 'Literature',
                              value: `${planPackage.literatureSurvey.papers.length} paper summaries`,
                              ok: planPackage.literatureSurvey.papers.length > 0,
                            },
                            {
                              label: 'Reasoning',
                              value: planPackage.evidenceTrace.reasoningKgId ? shortId(planPackage.evidenceTrace.reasoningKgId) : 'No graph id',
                              ok: Boolean(planPackage.evidenceTrace.reasoningKgId),
                            },
                            {
                              label: 'Plan readiness',
                              value: planPackage.qualityGate.evidenceValid ? 'Evidence valid' : 'Needs review',
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
                            <p className="text-sm font-semibold text-slate-900">Supporting papers</p>
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
                                {paper.limitations.length > 0 && <p className="mt-2 text-xs text-slate-600">Limitation: {paper.limitations[0]}</p>}
                              </div>
                            ))}
                            {evidencePapers.length === 0 && planPackage.literatureSurvey.papers.length === 0 && (
                              <p className="text-sm text-muted-foreground">No paper summaries are attached.</p>
                            )}
                          </div>
                        </div>

                        <div className="space-y-4">
                          <ReviewerCommitteeDisclosure reports={planPackage.reviewReports} />

                          {planPackage.revisions.length > 0 && (
                            <details className="rounded-md border border-slate-300 bg-white px-4 py-3 shadow-sm">
                              <summary className="cursor-pointer text-sm font-medium text-slate-800">Revision history</summary>
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
                                    {revision.changedSections.length > 0 && <p className="mt-1 text-xs text-slate-600">Changed: {revision.changedSections.join(', ')}</p>}
                                  </div>
                                ))}
                              </div>
                            </details>
                          )}

                          {evidencePaperIdsWithoutSummary.length > 0 && (
                            <div className="rounded-md border border-amber-300 bg-white px-4 py-3 shadow-sm">
                              <p className="text-sm font-semibold text-amber-900">Referenced IDs without summaries</p>
                              <div className="mt-3">
                                <TextList items={evidencePaperIdsWithoutSummary.map(shortId)} emptyLabel="All referenced IDs are summarized" />
                              </div>
                            </div>
                          )}

                          <details className="rounded-md border border-slate-300 bg-white px-4 py-3 shadow-sm">
                            <summary className="cursor-pointer text-sm font-medium text-slate-800">Raw package snapshot</summary>
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
                <CardTitle className="text-base">Plan snapshot</CardTitle>
                <CardDescription>Quick status, score, and structure at a glance.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {planStats && (
                  <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-1">
                    <StatCard label="Status" value={planStats.status} detail={planStats.readiness} />
                    <StatCard label="Score" value={planStats.score} detail={summaryTitle} />
                    <StatCard label="Structure" value={planStats.stages} detail={planStats.steps} />
                    <StatCard
                      label="Evidence"
                      value={planStats.papers}
                      detail={`${planPackage.literatureSurvey.coverage.structuredPaperCount} structured`}
                    />
                    <StatCard label="Created" value={new Date(planPackage.createdAt).toLocaleDateString()} detail={planPackage.schemaVersion} />
                  </div>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base">Quality and iteration</CardTitle>
                <CardDescription>Internal checks stay here instead of crowding the main reading path.</CardDescription>
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
                        <p className="text-sm font-semibold text-slate-900">Reviewer decision</p>
                        <p className="mt-1 text-xs text-slate-600">
                          confidence {(planPackage.metaReview.confidence * 100).toFixed(0)} · {planPackage.metaReview.blockingIssues.length} blocking issues
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
                <CardTitle className="text-base">Feedback</CardTitle>
                <CardDescription>Human edits and revision history for the current plan package.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div>
                  <div className="mb-3 flex items-center gap-2">
                    <MessageSquareText className="h-4 w-4 text-indigo-700" />
                    <p className="text-sm font-semibold text-slate-900">Human feedback</p>
                  </div>
                  <textarea
                    value={feedbackComment}
                    onChange={(event) => setFeedbackComment(event.target.value)}
                    placeholder="Tell FAROS what to change before handoff."
                    className="min-h-[118px] w-full rounded-md border border-slate-400 bg-white px-3 py-2 text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-indigo-600"
                  />
                  <Button
                    className="mt-3 bg-indigo-700 text-white hover:bg-indigo-800"
                    onClick={submitFeedbackAndRevise}
                    disabled={!feedbackComment.trim() || isSubmittingFeedback || isRevising}
                  >
                    {isSubmittingFeedback || isRevising ? <RefreshCw className="mr-2 h-4 w-4 animate-spin" /> : <Sparkles className="mr-2 h-4 w-4" />}
                    Revise from Feedback
                  </Button>
                </div>
                <div>
                  <p className="mb-3 text-sm font-semibold text-slate-900">Feedback history</p>
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
