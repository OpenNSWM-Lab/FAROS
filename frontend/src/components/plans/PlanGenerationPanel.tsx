import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowRight,
  BookOpen,
  CheckCircle2,
  ClipboardList,
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
  reviewPlanPackageWithMode,
  revisePlanPackage,
  validatePlanPackage,
  type PlanEvidenceRef,
  type PlanGapItem,
  type PlanHumanFeedback,
  type PlanLiteraturePaperSummary,
  type PlanPackage,
  type PlanQualityGate,
  type PlanReviewerReport,
  type PlanStage,
  type PlanStep,
} from '@/components/plans/planPackageApi'

type GenerationMode = 'hybrid' | 'deterministic'
type ReviewerMode = 'deterministic' | 'hybrid'

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

function shortId(id?: string | null) {
  if (!id) return '-'
  return id.length > 18 ? `${id.slice(0, 10)}...${id.slice(-6)}` : id
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
        <li key={`${item}-${index}`} className="rounded-md border border-slate-300 bg-slate-50 px-3 py-2">
          {item}
        </li>
      ))}
    </ul>
  )
}

function StepBlock({ step }: { step: PlanStep }) {
  return (
    <div className="rounded-md border border-slate-300 bg-white px-4 py-3 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline" className="font-mono text-[11px]">
              {step.id}
            </Badge>
            <h4 className="text-sm font-semibold text-slate-900">{step.title}</h4>
          </div>
          <p className="mt-2 text-sm text-slate-800">{step.desc}</p>
          <p className="mt-2 text-xs text-slate-600">{step.method}</p>
        </div>
        <Badge variant="secondary" className="shrink-0">
          Step {step.order}
        </Badge>
      </div>

      {step.inputFrom.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-700">
          <GitBranch className="h-3.5 w-3.5" />
          {step.inputFrom.map((id) => (
            <span key={id} className="rounded bg-slate-200 px-2 py-1 font-mono text-slate-900">
              {id}
            </span>
          ))}
        </div>
      )}

      <div className="mt-3 grid gap-3 lg:grid-cols-2">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase text-slate-500">Outputs</p>
          <div className="space-y-2">
            {step.outputs.map((output, index) => (
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
            {step.expected.map((expected, index) => (
              <div key={`${expected.metric}-${index}`} className="rounded-md border border-l-4 border-slate-300 border-l-emerald-700 bg-white px-3 py-2 text-xs">
                <p className="font-medium text-emerald-900">{expected.metric}</p>
                <p className="mt-1 text-slate-800">{expected.target}</p>
                {expected.desc && <p className="mt-1 text-slate-600">{expected.desc}</p>}
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="mt-3">
        <EvidenceChips refs={step.evidenceRefs} />
      </div>
    </div>
  )
}

function StageBlock({ stage }: { stage: PlanStage }) {
  return (
    <div className="rounded-md border border-slate-300 bg-white px-4 py-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline" className="font-mono text-[11px]">
              {stage.id}
            </Badge>
            <h3 className="text-base font-semibold text-slate-900">{stage.title}</h3>
          </div>
          <p className="mt-2 text-sm text-slate-800">{stage.goal}</p>
          <p className="mt-2 text-xs text-slate-600">{stage.method}</p>
        </div>
        <Badge className="bg-indigo-700 text-white">Stage {stage.order}</Badge>
      </div>
      {stage.dependsOn.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-700">
          <GitBranch className="h-3.5 w-3.5" />
          {stage.dependsOn.map((id) => (
            <span key={id} className="rounded bg-slate-100 px-2 py-1 font-mono text-slate-900">
              {id}
            </span>
          ))}
        </div>
      )}
      <div className="mt-4 space-y-3">
        {stage.steps.map((step) => (
          <StepBlock key={step.id} step={step} />
        ))}
      </div>
    </div>
  )
}

function PaperRow({ paper }: { paper: PlanLiteraturePaperSummary }) {
  const methods = paper.methods.map(compactValue).filter(Boolean).slice(0, 2)
  const findings = paper.findings.map(compactValue).filter(Boolean).slice(0, 2)

  return (
    <div className="rounded-md border border-slate-300 bg-white px-4 py-3 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline" className="font-mono text-[11px]">
              {shortId(paper.paperId)}
            </Badge>
            <Badge className={paper.source === 'probe' ? 'bg-indigo-700 text-white' : 'bg-blue-700 text-white'}>
              {paper.source}
            </Badge>
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
      {paper.relevanceReason && (
        <p className="mt-2 text-xs text-slate-600">{paper.relevanceReason}</p>
      )}
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

function FeedbackList({ feedback }: { feedback: PlanHumanFeedback[] }) {
  if (!feedback.length) {
    return <p className="text-sm text-muted-foreground">No human feedback yet.</p>
  }
  return (
    <div className="space-y-2">
      {feedback.slice(0, 6).map((item) => (
        <div key={item.id} className={`rounded-md border px-3 py-2 text-sm ${item.resolved ? 'border-emerald-200 bg-emerald-50' : 'border-slate-300 bg-white'}`}>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline" className="font-mono text-[11px]">
              {item.sectionPath}
            </Badge>
            <Badge variant={item.resolved ? 'secondary' : 'default'}>{item.severity}</Badge>
            <span className="text-xs text-slate-500">{item.feedbackType}</span>
          </div>
          <p className="mt-2 text-slate-800">{item.comment}</p>
          {item.resolvedByRevisionId && (
            <p className="mt-1 text-xs text-emerald-800">Resolved by {item.resolvedByRevisionId}</p>
          )}
        </div>
      ))}
    </div>
  )
}

export function PlanGenerationPanel() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [activeTab, setActiveTab] = useState('overview')
  const [planPackage, setPlanPackage] = useState<PlanPackage | null>(null)
  const [packageIdInput, setPackageIdInput] = useState(searchParams.get('packageId')?.trim() || '')
  const [isLoading, setIsLoading] = useState(false)
  const [isCreating, setIsCreating] = useState(false)
  const [isValidating, setIsValidating] = useState(false)
  const [isReviewing, setIsReviewing] = useState(false)
  const [isRevising, setIsRevising] = useState(false)
  const [isApproving, setIsApproving] = useState(false)
  const [isSubmittingFeedback, setIsSubmittingFeedback] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [generationMode, setGenerationMode] = useState<GenerationMode>('hybrid')
  const [reviewerMode, setReviewerMode] = useState<ReviewerMode>('hybrid')
  const [maxStages, setMaxStages] = useState(3)
  const [maxStepsPerStage, setMaxStepsPerStage] = useState(3)
  const [userNotes, setUserNotes] = useState('')
  const [feedbackSection, setFeedbackSection] = useState('stages')
  const [feedbackType, setFeedbackType] = useState('correction')
  const [feedbackSeverity, setFeedbackSeverity] = useState('medium')
  const [feedbackComment, setFeedbackComment] = useState('')
  const [revisionTargets, setRevisionTargets] = useState<string[]>(['stages'])

  const packageIdFromUrl = searchParams.get('packageId')?.trim() || ''
  const ideaSessionIdFromUrl = searchParams.get('ideaSessionId')?.trim() || ''
  const ideaCandidateIdFromUrl = searchParams.get('ideaCandidateId')?.trim() || ''
  const ideaCandidateTitleFromUrl = searchParams.get('ideaCandidateTitle')?.trim() || ''
  const ideaSeedQueryFromUrl = searchParams.get('ideaSeedQuery')?.trim() || ''

  const loadPackage = useCallback(async (packageId: string) => {
    if (!packageId) return
    setIsLoading(true)
    setError(null)
    try {
      const loaded = await getPlanPackage(packageId)
      setPlanPackage(loaded)
      setPackageIdInput(loaded.packageId)
    } catch (err) {
      setPlanPackage(null)
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
    getPlanPackageByIdeaSession(ideaSessionIdFromUrl)
      .then((loaded) => {
        if (cancelled) return
        setPlanPackage(loaded)
        setPackageIdInput(loaded.packageId)
      })
      .catch((err) => {
        if (cancelled) return
        if (err instanceof Error && err.message.includes('not found')) {
          setPlanPackage(null)
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
        reviewerMode,
        maxStages,
        maxStepsPerStage,
        maxRepairRounds: 1,
        userNotes: userNotes.trim() || undefined,
      })
      setPlanPackage(response.package)
      setPackageIdInput(response.packageId)
      updatePackageUrl(response.packageId)
      setActiveTab('overview')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create PlanPackage')
    } finally {
      setIsCreating(false)
    }
  }

  const validateCurrentPackage = async () => {
    if (!planPackage) return
    setIsValidating(true)
    setError(null)
    try {
      const response = await validatePlanPackage(planPackage.packageId)
      setPlanPackage({ ...planPackage, qualityGate: response.qualityGate })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to validate PlanPackage')
    } finally {
      setIsValidating(false)
    }
  }

  const reviewCurrentPackage = async () => {
    if (!planPackage) return
    setIsReviewing(true)
    setError(null)
    try {
      const reviewed = await reviewPlanPackageWithMode(planPackage.packageId, reviewerMode)
      setPlanPackage(reviewed)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to review PlanPackage')
    } finally {
      setIsReviewing(false)
    }
  }

  const reviseCurrentPackage = async () => {
    if (!planPackage) return
    setIsRevising(true)
    setError(null)
    try {
      const revised = await revisePlanPackage(planPackage.packageId, {
        generationMode,
        reviewerMode,
        maxStages,
        maxStepsPerStage,
        maxRepairRounds: 2,
        targetSections: revisionTargets.length ? revisionTargets : ['stages'],
      })
      setPlanPackage(revised)
      setActiveTab('overview')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to revise PlanPackage')
    } finally {
      setIsRevising(false)
    }
  }

  const approveCurrentPackage = async () => {
    if (!planPackage) return
    setIsApproving(true)
    setError(null)
    try {
      const approved = await approvePlanPackageWithMode(planPackage.packageId, reviewerMode)
      setPlanPackage(approved)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to approve PlanPackage')
    } finally {
      setIsApproving(false)
    }
  }

  const submitFeedback = async () => {
    if (!planPackage || !feedbackComment.trim()) return
    setIsSubmittingFeedback(true)
    setError(null)
    try {
      const updated = await addPlanPackageFeedback(planPackage.packageId, {
        sectionPath: feedbackSection,
        feedbackType,
        severity: feedbackSeverity,
        requestedAction: feedbackType === 'approve' ? 'comment' : 'revise',
        comment: feedbackComment.trim(),
      })
      setPlanPackage(updated)
      setFeedbackComment('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to submit feedback')
    } finally {
      setIsSubmittingFeedback(false)
    }
  }

  const loadByInput = () => {
    const packageId = packageIdInput.trim()
    if (!packageId) return
    updatePackageUrl(packageId)
    void loadPackage(packageId)
  }

  const toggleRevisionTarget = (target: string) => {
    setRevisionTargets((current) => {
      if (current.includes(target)) {
        const next = current.filter((item) => item !== target)
        return next.length ? next : ['stages']
      }
      return [...current, target]
    })
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

  const gate = planPackage?.qualityGate ?? EMPTY_GATE

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
                Primary handoff for the idea + plan stage.
              </CardDescription>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" onClick={validateCurrentPackage} disabled={!planPackage || isValidating}>
                {isValidating ? <RefreshCw className="mr-2 h-4 w-4 animate-spin" /> : <ShieldCheck className="mr-2 h-4 w-4" />}
                Validate
              </Button>
              <Button variant="outline" onClick={reviewCurrentPackage} disabled={!planPackage || isReviewing}>
                {isReviewing ? <RefreshCw className="mr-2 h-4 w-4 animate-spin" /> : <ShieldCheck className="mr-2 h-4 w-4" />}
                Review
              </Button>
              <Button variant="outline" onClick={reviseCurrentPackage} disabled={!planPackage || isRevising}>
                {isRevising ? <RefreshCw className="mr-2 h-4 w-4 animate-spin" /> : <Sparkles className="mr-2 h-4 w-4" />}
                Revise
              </Button>
              <Button onClick={approveCurrentPackage} disabled={!planPackage || isApproving} className="bg-emerald-700 text-white hover:bg-emerald-800">
                {isApproving ? <RefreshCw className="mr-2 h-4 w-4 animate-spin" /> : <UserCheck className="mr-2 h-4 w-4" />}
                Approve
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

          <div className="rounded-md border border-slate-300 bg-white px-4 py-3 shadow-sm">
            <p className="text-xs font-semibold uppercase text-slate-500">Revision target sections</p>
            <div className="mt-2 flex flex-wrap gap-3 text-sm text-slate-800">
              {[
                ['researchQuestion', 'Research question'],
                ['hypothesis', 'Hypothesis'],
                ['constants', 'Constants'],
                ['stages', 'Stages'],
                ['expectedMetrics', 'Expected metrics'],
              ].map(([value, label]) => (
                <label key={value} className="flex items-center gap-2 rounded-md border border-slate-300 bg-slate-50 px-3 py-2">
                  <input
                    type="checkbox"
                    checked={revisionTargets.includes(value)}
                    onChange={() => toggleRevisionTarget(value)}
                    className="h-4 w-4"
                  />
                  {label}
                </label>
              ))}
            </div>
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
              <div className="mt-3 grid gap-3 md:grid-cols-4">
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
                  Reviewer
                  <select
                    value={reviewerMode}
                    onChange={(event) => setReviewerMode(event.target.value as ReviewerMode)}
                    className="h-9 w-full rounded-md border border-slate-400 bg-white px-2 text-sm text-slate-900"
                  >
                    <option value="hybrid">Rules + LLM</option>
                    <option value="deterministic">Rules</option>
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
              </div>
              <textarea
                value={userNotes}
                onChange={(event) => setUserNotes(event.target.value)}
                placeholder="Optional planning constraints for this package"
                className="mt-3 min-h-[72px] w-full rounded-md border border-slate-400 bg-white px-3 py-2 text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-indigo-600"
              />
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
                    <Badge className={planPackage.generation.reviewerMode === 'hybrid' ? 'bg-violet-700 text-white' : 'bg-slate-700 text-white'}>
                      review {planPackage.generation.reviewerMode}
                    </Badge>
                    {planPackage.generation.reviewerMode === 'hybrid' && (
                      <Badge variant="outline" className={planPackage.generation.llmReviewerUsed ? 'border-emerald-400 bg-emerald-50 text-emerald-900' : 'border-amber-400 bg-amber-50 text-amber-900'}>
                        LLM reviewer {planPackage.generation.llmReviewerUsed ? 'used' : 'skipped'}
                      </Badge>
                    )}
                    <Badge variant="secondary">{planPackage.schemaVersion}</Badge>
                    <Badge variant="outline" className="font-mono">
                      score {(planPackage.qualityGate.overallScore * 100).toFixed(0)}
                    </Badge>
                  </div>
                  <CardTitle className="mt-3 text-xl leading-tight">{planPackage.researchQuestion}</CardTitle>
                  {planPackage.hypothesis && (
                    <CardDescription className="mt-2 text-sm text-slate-700">
                      {planPackage.hypothesis}
                    </CardDescription>
                  )}
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button variant="outline" onClick={() => navigate(`/code?packageId=${encodeURIComponent(planPackage.packageId)}`)}>
                    <ArrowRight className="mr-2 h-4 w-4" />
                    Code
                  </Button>
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <QualityGateSummary gate={gate} />
              {(gate.errors.length > 0 || gate.warnings.length > 0 || planPackage.generation.warnings.length > 0) && (
                <div className="grid gap-3 lg:grid-cols-2">
                  {gate.errors.length > 0 && (
                    <div className="rounded-md border border-l-4 border-red-300 border-l-red-700 bg-white px-3 py-2 text-sm text-red-800">
                      <p className="font-medium">Errors</p>
                      <TextList items={gate.errors} emptyLabel="No errors" />
                    </div>
                  )}
                  {(gate.warnings.length > 0 || planPackage.generation.warnings.length > 0) && (
                    <div className="rounded-md border border-l-4 border-amber-300 border-l-amber-700 bg-white px-3 py-2 text-sm text-amber-900">
                      <p className="font-medium">Warnings</p>
                      <TextList items={[...gate.warnings, ...planPackage.generation.warnings]} emptyLabel="No warnings" />
                    </div>
                  )}
                </div>
              )}
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
                  {planPackage.metaReview.requiredRepairs.length > 0 && (
                    <div className="mt-3">
                      <TextList items={planPackage.metaReview.requiredRepairs.slice(0, 4)} emptyLabel="No required repairs" />
                    </div>
                  )}
                </div>
              )}

              <div className="grid gap-4 xl:grid-cols-[1fr_1.2fr]">
                <div className="rounded-md border border-slate-300 bg-white px-4 py-3 shadow-sm">
                  <div className="mb-3 flex items-center gap-2">
                    <MessageSquareText className="h-4 w-4 text-indigo-700" />
                    <p className="text-sm font-semibold text-slate-900">Human feedback</p>
                  </div>
                  <div className="grid gap-2 md:grid-cols-3">
                    <label className="space-y-1 text-xs font-medium text-slate-700">
                      Section
                      <select
                        value={feedbackSection}
                        onChange={(event) => setFeedbackSection(event.target.value)}
                        className="h-9 w-full rounded-md border border-slate-400 bg-white px-2 text-sm text-slate-900"
                      >
                        <option value="researchQuestion">Research question</option>
                        <option value="gap">Gap</option>
                        <option value="principle">Principle</option>
                        <option value="literatureSurvey">Literature</option>
                        <option value="stages">Plan stages</option>
                        <option value="qualityGate">Quality gate</option>
                      </select>
                    </label>
                    <label className="space-y-1 text-xs font-medium text-slate-700">
                      Type
                      <select
                        value={feedbackType}
                        onChange={(event) => setFeedbackType(event.target.value)}
                        className="h-9 w-full rounded-md border border-slate-400 bg-white px-2 text-sm text-slate-900"
                      >
                        <option value="correction">Correction</option>
                        <option value="regenerate">Regenerate</option>
                        <option value="comment">Comment</option>
                        <option value="reject">Reject</option>
                        <option value="approve">Approve note</option>
                      </select>
                    </label>
                    <label className="space-y-1 text-xs font-medium text-slate-700">
                      Severity
                      <select
                        value={feedbackSeverity}
                        onChange={(event) => setFeedbackSeverity(event.target.value)}
                        className="h-9 w-full rounded-md border border-slate-400 bg-white px-2 text-sm text-slate-900"
                      >
                        <option value="low">Low</option>
                        <option value="medium">Medium</option>
                        <option value="high">High</option>
                        <option value="blocking">Blocking</option>
                      </select>
                    </label>
                  </div>
                  <textarea
                    value={feedbackComment}
                    onChange={(event) => setFeedbackComment(event.target.value)}
                    placeholder="Point out what should be corrected before this package is handed off."
                    className="mt-3 min-h-[84px] w-full rounded-md border border-slate-400 bg-white px-3 py-2 text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-indigo-600"
                  />
                  <Button
                    className="mt-3 bg-indigo-700 text-white hover:bg-indigo-800"
                    onClick={submitFeedback}
                    disabled={!feedbackComment.trim() || isSubmittingFeedback}
                  >
                    {isSubmittingFeedback ? <RefreshCw className="mr-2 h-4 w-4 animate-spin" /> : <MessageSquareText className="mr-2 h-4 w-4" />}
                    Submit Feedback
                  </Button>
                </div>
                <div className="rounded-md border border-slate-300 bg-white px-4 py-3 shadow-sm">
                  <p className="mb-3 text-sm font-semibold text-slate-900">Feedback history</p>
                  <FeedbackList feedback={planPackage.humanFeedback} />
                </div>
              </div>
            </CardContent>
          </Card>

          <Tabs value={activeTab} onValueChange={setActiveTab}>
            <TabsList className="h-auto flex-wrap justify-start">
              <TabsTrigger value="overview">Overview</TabsTrigger>
              <TabsTrigger value="implementation">Implementation</TabsTrigger>
              <TabsTrigger value="context">Context</TabsTrigger>
              <TabsTrigger value="literature">Literature</TabsTrigger>
              <TabsTrigger value="evidence">Evidence</TabsTrigger>
              <TabsTrigger value="review">Review</TabsTrigger>
              <TabsTrigger value="json">JSON</TabsTrigger>
            </TabsList>

            <TabsContent value="overview" className="space-y-4">
              <div className="grid gap-4 lg:grid-cols-3">
                <Card>
                  <CardHeader className="pb-3">
                    <CardTitle className="flex items-center gap-2 text-base">
                      <Lightbulb className="h-4 w-4 text-amber-600" />
                      Idea
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3 text-sm">
                    <p className="font-semibold text-slate-900">{planPackage.idea.title}</p>
                    <p className="text-slate-700">{planPackage.idea.problem}</p>
                    {planPackage.idea.keyInsight && <p className="text-slate-600">{planPackage.idea.keyInsight}</p>}
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader className="pb-3">
                    <CardTitle className="flex items-center gap-2 text-base">
                      <Layers3 className="h-4 w-4 text-blue-600" />
                      Plan Shape
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="grid grid-cols-2 gap-3 text-sm">
                    <div className="rounded-md border border-slate-300 bg-slate-50 px-3 py-2">
                      <p className="text-xs text-muted-foreground">Stages</p>
                      <p className="text-2xl font-semibold">{planPackage.stages.length}</p>
                    </div>
                    <div className="rounded-md border border-slate-300 bg-slate-50 px-3 py-2">
                      <p className="text-xs text-muted-foreground">Steps</p>
                      <p className="text-2xl font-semibold">{totalSteps}</p>
                    </div>
                    <div className="col-span-2 rounded-md border border-slate-300 bg-slate-50 px-3 py-2">
                      <p className="text-xs text-muted-foreground">Created</p>
                      <p className="text-sm">{new Date(planPackage.createdAt).toLocaleString()}</p>
                    </div>
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader className="pb-3">
                    <CardTitle className="flex items-center gap-2 text-base">
                      <BookOpen className="h-4 w-4 text-indigo-600" />
                      Literature
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="grid grid-cols-2 gap-3 text-sm">
                    <div className="rounded-md border border-slate-300 bg-slate-50 px-3 py-2">
                      <p className="text-xs text-muted-foreground">Structured</p>
                      <p className="text-2xl font-semibold">{planPackage.literatureSurvey.coverage.structuredPaperCount}</p>
                    </div>
                    <div className="rounded-md border border-slate-300 bg-slate-50 px-3 py-2">
                      <p className="text-xs text-muted-foreground">Probe</p>
                      <p className="text-2xl font-semibold">{planPackage.literatureSurvey.coverage.probePaperCount}</p>
                    </div>
                    <div className="col-span-2 rounded-md border border-slate-300 bg-slate-50 px-3 py-2">
                      <p className="text-xs text-muted-foreground">Total summaries</p>
                      <p className="text-2xl font-semibold">{planPackage.literatureSurvey.papers.length}</p>
                    </div>
                  </CardContent>
                </Card>
              </div>

              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-base">Constants</CardTitle>
                </CardHeader>
                <CardContent>
                  {Object.keys(planPackage.constants).length === 0 ? (
                    <p className="text-sm text-muted-foreground">No constants declared.</p>
                  ) : (
                    <div className="grid gap-2 md:grid-cols-2">
                      {Object.entries(planPackage.constants).map(([key, value]) => (
                        <div key={key} className="rounded-md border border-slate-300 bg-slate-50 px-3 py-2">
                          <p className="text-xs font-medium text-slate-500">{key}</p>
                          <p className="mt-1 break-words text-sm text-slate-800">{compactValue(value)}</p>
                        </div>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="implementation" className="space-y-4">
              {planPackage.stages.map((stage) => (
                <StageBlock key={stage.id} stage={stage} />
              ))}
            </TabsContent>

            <TabsContent value="context" className="space-y-4">
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-base">Background</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4 text-sm">
                  <p className="text-slate-800">{planPackage.background.summary}</p>
                  {planPackage.background.motivation && <p className="text-slate-700">{planPackage.background.motivation}</p>}
                  <div className="grid gap-4 lg:grid-cols-2">
                    <div>
                      <p className="mb-2 text-xs font-semibold uppercase text-slate-500">Current limitations</p>
                      <TextList items={planPackage.background.currentLimitations} emptyLabel="No limitations listed" />
                    </div>
                    <div>
                      <p className="mb-2 text-xs font-semibold uppercase text-slate-500">Domain context</p>
                      <TextList items={planPackage.background.domainContext} emptyLabel="No domain context listed" />
                    </div>
                  </div>
                  <EvidenceChips refs={planPackage.background.evidenceRefs} />
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-base">Gap</CardTitle>
                  <CardDescription>{planPackage.gap.summary}</CardDescription>
                </CardHeader>
                <CardContent className="grid gap-3 lg:grid-cols-2">
                  {planPackage.gap.items.map((gap) => (
                    <GapItem key={gap.id} gap={gap} />
                  ))}
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-base">Principle</CardTitle>
                  <CardDescription>{planPackage.principle.summary}</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4 text-sm">
                  {planPackage.principle.mechanism && (
                    <div>
                      <p className="mb-1 text-xs font-semibold uppercase text-slate-500">Mechanism</p>
                      <p className="text-slate-800">{planPackage.principle.mechanism}</p>
                    </div>
                  )}
                  {planPackage.principle.noveltyClaim && (
                    <div>
                      <p className="mb-1 text-xs font-semibold uppercase text-slate-500">Novelty claim</p>
                      <p className="text-slate-800">{planPackage.principle.noveltyClaim}</p>
                    </div>
                  )}
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
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="literature" className="space-y-3">
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center gap-2 text-base">
                    <BookOpen className="h-4 w-4 text-indigo-600" />
                    Literature Survey
                  </CardTitle>
                  <CardDescription>{planPackage.literatureSurvey.summary}</CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  {planPackage.literatureSurvey.papers.map((paper) => (
                    <PaperRow key={`${paper.source}-${paper.paperId}`} paper={paper} />
                  ))}
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="evidence" className="space-y-4">
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center gap-2 text-base">
                    <Network className="h-4 w-4 text-indigo-700" />
                    Evidence Map
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
                            {paper.limitations.length > 0 && (
                              <p className="mt-2 text-xs text-slate-600">
                                Limitation: {paper.limitations[0]}
                              </p>
                            )}
                          </div>
                        ))}
                        {evidencePapers.length === 0 && planPackage.literatureSurvey.papers.length === 0 && (
                          <p className="text-sm text-muted-foreground">No paper summaries are attached.</p>
                        )}
                      </div>
                    </div>

                    <div className="space-y-4">
                      <div className="rounded-md border border-slate-300 bg-white px-4 py-3 shadow-sm">
                        <p className="text-sm font-semibold text-slate-900">Evidence signals</p>
                        <div className="mt-3 space-y-3">
                          <div>
                            <p className="mb-2 text-xs font-semibold uppercase text-slate-500">Structured paper IDs</p>
                            <TextList items={planPackage.evidenceTrace.structuredPaperIds.map(shortId)} emptyLabel="No structured paper IDs" />
                          </div>
                          <div>
                            <p className="mb-2 text-xs font-semibold uppercase text-slate-500">Probe results</p>
                            <TextList items={planPackage.evidenceTrace.probeResultIds.map(shortId)} emptyLabel="No probe results" />
                          </div>
                          <div>
                            <p className="mb-2 text-xs font-semibold uppercase text-slate-500">Graph patches</p>
                            <TextList items={planPackage.evidenceTrace.graphPatchIds.map(shortId)} emptyLabel="No graph patches" />
                          </div>
                        </div>
                      </div>

                      {evidencePaperIdsWithoutSummary.length > 0 && (
                        <div className="rounded-md border border-amber-300 bg-white px-4 py-3 shadow-sm">
                          <p className="text-sm font-semibold text-amber-900">Referenced IDs without summaries</p>
                          <div className="mt-3">
                            <TextList items={evidencePaperIdsWithoutSummary.map(shortId)} emptyLabel="All referenced IDs are summarized" />
                          </div>
                        </div>
                      )}
                    </div>
                  </div>

                  <details className="rounded-md border border-slate-300 bg-white px-4 py-3 shadow-sm">
                    <summary className="cursor-pointer text-sm font-medium text-slate-800">Debug IDs and raw graph evidence</summary>
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
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="review" className="space-y-4">
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center gap-2 text-base">
                    <ShieldCheck className="h-4 w-4 text-indigo-700" />
                    Reviewer Committee
                  </CardTitle>
                  <CardDescription>
                    Independent checks for relevance, evidence, feasibility, metrics, and novelty.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  {planPackage.reviewReports.length === 0 ? (
                    <p className="text-sm text-muted-foreground">No reviewer reports yet. Run Review to generate them.</p>
                  ) : (
                    <div className="grid gap-3 lg:grid-cols-2">
                      {planPackage.reviewReports.map((report) => (
                        <ReviewerReportCard key={report.reviewer} report={report} />
                      ))}
                    </div>
                  )}
                  {planPackage.revisions.length > 0 && (
                    <div className="rounded-md border border-slate-300 bg-white px-4 py-3 shadow-sm">
                      <p className="text-sm font-semibold text-slate-900">Revision history</p>
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
                            {revision.changedSections.length > 0 && (
                              <p className="mt-1 text-xs text-slate-600">Changed: {revision.changedSections.join(', ')}</p>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="json">
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center gap-2 text-base">
                    <FileJson className="h-4 w-4 text-slate-600" />
                    Raw PlanPackage
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <pre className="max-h-[720px] overflow-auto rounded-md bg-slate-950 p-4 text-xs text-slate-100">
                    {JSON.stringify(planPackage, null, 2)}
                  </pre>
                </CardContent>
              </Card>
            </TabsContent>
          </Tabs>
        </div>
      )}
    </div>
  )
}
