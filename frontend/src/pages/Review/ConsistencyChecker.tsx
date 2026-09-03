import { useEffect, useState, useMemo } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { AppPageLayout } from '@/components/layout/AppPageLayout'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import {
  AlertCircle,
  AlertTriangle,
  Info,
  CheckCircle,
  ExternalLink,
  Shield,
  History,
  Route,
  GitBranch,
  Target,
  ArrowRight,
  Check,
  Loader2,
  Database,
  ScanSearch,
} from 'lucide-react'
import { usePapers, useReviewFindings, useRunConsistencyCheck } from '@/lib/hooks/useApi'
import { API_BASE_URL } from '@/lib/api'
import { useReviewLocale } from '@/lib/reviewLocale'
import type { ReviewFinding } from '@/lib/types'
import { ExperimentFeedbackPanel } from '@/components/review/ExperimentFeedbackPanel'

const severityIcons = {
  blocker: <AlertCircle className="h-4 w-4 text-destructive" />,
  major: <AlertTriangle className="h-4 w-4 text-orange-500" />,
  minor: <Info className="h-4 w-4 text-blue-500" />,
  info: <CheckCircle className="h-4 w-4 text-muted-foreground" />,
}

const severityVariants = {
  blocker: 'destructive' as const,
  major: 'default' as const,
  minor: 'secondary' as const,
  info: 'outline' as const,
}

interface ReviewXHistoryItem {
  id: string
  paperId: string
  status: string
  budgetMode: string
  providerName?: string
  model?: string
  scoreSuggestion?: number
  createdAt?: string
  updatedAt?: string
  claimCount?: number
  evidenceCount?: number
  verificationCount?: number
  riskQuestionCount?: number
  findingCount?: number
  severityCounts?: Record<string, number>
  supportCounts?: Record<string, number>
  llmCallCount?: number
  llmSkipped?: boolean
  llmSkipReason?: string
  visualAuditEnabled?: boolean
  visualModel?: string
  visualAuditStatus?: string
}

interface ReviewXRunDetail {
  id: string
  paperId: string
  budgetMode?: string
  providerName?: string
  model?: string
  scoreSuggestion?: number
  actionItems?: ReviewXActionItem[]
  riskTree?: ReviewXRiskNode[]
  mismatchReport?: ReviewXMismatchReport
  evidenceGraph?: ReviewXEvidenceGraph
  modelTrace?: {
    routingMode?: string
    estimatedTokenCost?: number
    llmCalls?: Array<{
      task?: string
      model?: string
      provider?: string
      latencyMs?: number
      usage?: Record<string, number>
      selectedFindingIds?: string[]
      finishReason?: string
    }>
    llmRouting?: {
      routingMode?: string
      providerName?: string
      requestedModel?: string
      budgetPolicy?: string
      budgetFormula?: string
      budgetThresholds?: Record<string, number>
      budgetAllocations?: Array<{
        findingId: string
        claimId?: string
        priority?: number
        mismatchScore?: number
        severity?: string
        supportStatus?: string
        recommendedModel?: string
        selected?: boolean
        drivers?: string[]
      }>
      selectedFindingIds?: string[]
      skipped?: boolean
      skipReason?: string
      estimatedTokenCost?: number
      llmCalls?: Array<{
        task?: string
        model?: string
        provider?: string
        latencyMs?: number
        usage?: Record<string, number>
        selectedFindingIds?: string[]
        finishReason?: string
      }>
    }
    visualEvidenceAudit?: {
      enabled?: boolean
      status?: string
      providerName?: string
      model?: string
      selectedFigureCount?: number
      auditedFigureCount?: number
      captionCheckCount?: number
      checkCount?: number
      verificationCount?: number
      anomalyCount?: number
      estimatedTokenCost?: number
      skipped?: boolean
      skipReason?: string
      calls?: Array<{
        sourcePath?: string
        status?: string
        latencyMs?: number
        anomalyCount?: number
        error?: string
      }>
    }
    localRulePasses?: string[]
    note?: string
  }
}

interface ReviewXMismatchReport {
  method?: {
    name?: string
    metric?: string
    formula?: string
    thresholds?: Record<string, number>
  }
  aggregate?: {
    meanMismatch?: number
    maxMismatch?: number
    highMismatchClaimCount?: number
    claimCount?: number
    supportCounts?: Record<string, number>
    dimensionMax?: Record<string, number>
  }
  claimScores?: Array<{
    claimId: string
    claimType?: string
    importance?: string
    mismatchScore?: number
    rawMismatchScore?: number
    supportStatus?: string
    linkedEvidenceCount?: number
    findingIds?: string[]
    verificationIds?: string[]
    dimensions?: Record<string, number>
    calibration?: {
      llmDecision?: string
      llmFactor?: number
      revisionAdjustment?: number
      revisionStatuses?: string[]
    }
    reasons?: string[]
    text?: string
  }>
}

interface ReviewXEvidenceGraph {
  nodeCount?: number
  edgeCount?: number
  nodes?: Array<{
    id: string
    nodeType?: string
    label?: string
    claimType?: string
    evidenceType?: string
    sourceModule?: string
    mismatchScore?: number
    supportStatus?: string
  }>
  edges?: Array<{
    id: string
    source: string
    target: string
    edgeType?: string
  }>
}

interface ReviewXComparisonMetrics {
  reviewId: string
  updatedAt?: string
  score?: number
  findingCount?: number
  blockerCount?: number
  majorCount?: number
  unsupportedCount?: number
  contradictedCount?: number
  artifactAbsentCount?: number
  needsHumanVerificationCount?: number
  weaklySupportedCount?: number
  supportedCount?: number
  coverage?: number
  requestCount?: number
  resolvedRequestCount?: number
  meanMismatch?: number
  maxMismatch?: number
  highMismatchClaimCount?: number
}

interface ReviewXComparisonFinding {
  id?: string
  title?: string
  severity?: string
  riskType?: string
  claimId?: string
  targetModule?: string
  supportStatus?: string
  confidence?: number
}

interface ReviewXComparison {
  paperId: string
  before: ReviewXComparisonMetrics
  after: ReviewXComparisonMetrics
  delta: Record<string, number | null>
  resolvedFindings: ReviewXComparisonFinding[]
  newFindings: ReviewXComparisonFinding[]
  persistentFindings: ReviewXComparisonFinding[]
}

interface ReviewXActionItem {
  description: string
  section?: string
  severity?: string
  targetModule?: string
  suggestedEdit?: string
  sourceFindingId?: string
  claimId?: string
  evidenceIds?: string[]
  riskType?: string
  confidence?: number
  supportStatus?: string
  verifierIds?: string[]
  acceptanceCriteria?: string[]
}

interface ImprovementRequest {
  id: string
  reviewId: string
  paperId: string
  targetModule: string
  description: string
  severity: string
  sectionPointer?: string
  suggestedEdit?: string
  status: string
  sourceFindingId?: string
  supportStatus?: string
  acceptanceCriteria?: string[]
  createdAt?: string
}

interface ReviewXRiskNode {
  id: string
  question: string
  claimIds?: string[]
  riskScore?: number
  status?: string
  assignedModel?: string
  children?: string[]
  parentId?: string | null
  level?: number
  category?: string
  findingIds?: string[]
  evidenceIds?: string[]
  supportCounts?: Record<string, number>
  mismatchScore?: number
  expansionPolicy?: string
  mismatchDrivers?: string[]
}

const formatDateTime = (value?: string) => {
  if (!value) return 'Unknown time'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

const severityText = (counts?: Record<string, number>) => {
  const c = counts || {}
  return `B ${c.blocker || 0} · M ${c.major || 0} · m ${c.minor || 0} · I ${c.info || 0}`
}

const supportText = (counts?: Record<string, number>) => {
  const c = counts || {}
  return `U ${c.unsupported || 0} · A ${c.artifact_absent || 0} · H ${c.needs_human_verification || 0} · W ${c.weakly_supported || 0} · S ${c.supported || 0} · C ${c.contradicted || 0}`
}

const supportStatusText = (status?: string) => {
  if (status === 'artifact_absent') return 'Artifact absent'
  if (status === 'needs_human_verification') return 'Needs human verification'
  return status || ''
}

const moduleBadgeClass = (targetModule?: string) => {
  if (targetModule === 'experiments') return 'bg-emerald-50 text-emerald-800 border-emerald-200'
  if (targetModule === 'code') return 'bg-violet-50 text-violet-800 border-violet-200'
  return 'bg-indigo-50 text-indigo-800 border-indigo-200'
}

const formatMetricValue = (value?: number, percent = false) => {
  if (value === undefined || value === null) return '-'
  if (percent) return `${Math.round(value * 100)}%`
  return Number.isInteger(value) ? String(value) : value.toFixed(2)
}

const formatDelta = (value?: number | null, percent = false) => {
  if (value === undefined || value === null) return 'n/a'
  const prefix = value > 0 ? '+' : ''
  if (percent) return `${prefix}${Math.round(value * 100)}%`
  return `${prefix}${Number.isInteger(value) ? value : value.toFixed(2)}`
}

const deltaTone = (value?: number | null, lowerIsBetter = true) => {
  if (value === undefined || value === null || value === 0) return 'outline' as const
  const improved = lowerIsBetter ? value < 0 : value > 0
  return improved ? 'secondary' as const : 'destructive' as const
}

const findingHasLlmRefinement = (finding: ReviewFinding) =>
  finding.description.includes('LLM deep review')

export function ConsistencyChecker() {
  const { text } = useReviewLocale()
  const [searchParams] = useSearchParams()
  const requestedFeedbackId = searchParams.get('feedbackId') || undefined
  const requestedFeedbackFocus = searchParams.get('focus') === 'signoff' ? 'signoff' : 'loop'
  const { data: papers, isLoading: papersLoading } = usePapers()
  const [selectedPaperId, setSelectedPaperId] = useState<string>('')
  const [budgetMode, setBudgetMode] = useState<string>('balanced')
  const [visualAuditEnabled, setVisualAuditEnabled] = useState(false)
  const [severityFilter, setSeverityFilter] = useState<string>('all')
  const [searchQuery, setSearchQuery] = useState('')
  const [history, setHistory] = useState<ReviewXHistoryItem[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [selectedHistoryId, setSelectedHistoryId] = useState<string>('')
  const [historyFindings, setHistoryFindings] = useState<ReviewFinding[] | null>(null)
  const [historyFindingsLoading, setHistoryFindingsLoading] = useState(false)
  const [runDetail, setRunDetail] = useState<ReviewXRunDetail | null>(null)
  const [runDetailLoading, setRunDetailLoading] = useState(false)
  const [revisionRequests, setRevisionRequests] = useState<ImprovementRequest[]>([])
  const [revisionRequestsLoading, setRevisionRequestsLoading] = useState(false)
  const [selectedActionIndexes, setSelectedActionIndexes] = useState<Set<number>>(new Set())
  const [applyingActions, setApplyingActions] = useState(false)
  const [applyMessage, setApplyMessage] = useState('')
  const [comparison, setComparison] = useState<ReviewXComparison | null>(null)
  const [comparisonLoading, setComparisonLoading] = useState(false)
  const [latestResultsEnabled, setLatestResultsEnabled] = useState(false)

  const latestFindingsPaperId = latestResultsEnabled && !selectedHistoryId ? selectedPaperId : ''
  const { data: latestFindings, isLoading: latestFindingsLoading } = useReviewFindings(latestFindingsPaperId)
  const runConsistencyCheck = useRunConsistencyCheck()
  const findings = selectedHistoryId ? historyFindings : latestResultsEnabled ? latestFindings : null
  const findingsLoading = selectedHistoryId ? historyFindingsLoading : latestResultsEnabled ? latestFindingsLoading : false
  const hasLlmCalls = (runDetail?.modelTrace?.llmCalls || []).length > 0
  const visualTrace = runDetail?.modelTrace?.visualEvidenceAudit
  const runSourceLabel = selectedHistoryId
    ? text('已保存历史', 'Stored History')
    : latestResultsEnabled
      ? text('已保存最新结果', 'Stored Latest')
      : text('未加载', 'Not Loaded')

  const refreshHistory = async (paperId: string) => {
    if (!paperId) {
      setHistory([])
      return
    }
    setHistoryLoading(true)
    try {
      const resp = await fetch(`${API_BASE_URL}/api/v1/reviews/reviewx/history?paperId=${paperId}`)
      const data = await resp.json()
      setHistory(data.reviews || [])
    } finally {
      setHistoryLoading(false)
    }
  }

  const loadHistoryFindings = async (reviewId: string) => {
    setSelectedHistoryId(reviewId)
    setLatestResultsEnabled(false)
    setHistoryFindingsLoading(true)
    setRunDetailLoading(true)
    try {
      const [findingsResp, detailResp] = await Promise.all([
        fetch(`${API_BASE_URL}/api/v1/reviews/reviewx/${reviewId}/findings`),
        fetch(`${API_BASE_URL}/api/v1/reviews/reviewx/${reviewId}`),
      ])
      const findingsData = await findingsResp.json()
      const detailData = await detailResp.json()
      setHistoryFindings(findingsData || [])
      setRunDetail(detailData)
      setSelectedActionIndexes(new Set())
      setApplyMessage('')
      void loadRevisionRequests(detailData.id)
      void loadComparison(detailData.paperId, detailData.id)
    } finally {
      setHistoryFindingsLoading(false)
      setRunDetailLoading(false)
    }
  }

  const loadLatestRunDetail = async (paperId: string) => {
    if (!paperId) {
      setRunDetail(null)
      return
    }
    setRunDetailLoading(true)
    try {
      const resp = await fetch(`${API_BASE_URL}/api/v1/reviews/reviewx/latest?paperId=${paperId}`)
      if (resp.ok) {
        const detail = await resp.json()
        setRunDetail(detail)
        setSelectedActionIndexes(new Set())
        setApplyMessage('')
        void loadRevisionRequests(detail.id)
        void loadComparison(paperId, detail.id)
      } else {
        setRunDetail(null)
        setRevisionRequests([])
        setComparison(null)
      }
    } finally {
      setRunDetailLoading(false)
    }
  }

  const loadLatestReviewX = async (paperId: string) => {
    if (!paperId) return
    setSelectedHistoryId('')
    setHistoryFindings(null)
    setLatestResultsEnabled(true)
    await loadLatestRunDetail(paperId)
  }

  const loadComparison = async (paperId: string, targetReviewId?: string) => {
    if (!paperId) {
      setComparison(null)
      return
    }
    setComparisonLoading(true)
    try {
      const params = new URLSearchParams({ paperId })
      if (targetReviewId) params.set('targetReviewId', targetReviewId)
      const resp = await fetch(`${API_BASE_URL}/api/v1/reviews/reviewx/compare?${params.toString()}`)
      if (resp.ok) {
        setComparison(await resp.json())
      } else {
        setComparison(null)
      }
    } finally {
      setComparisonLoading(false)
    }
  }

  const loadRevisionRequests = async (reviewId: string) => {
    if (!reviewId) {
      setRevisionRequests([])
      return
    }
    setRevisionRequestsLoading(true)
    try {
      const resp = await fetch(`${API_BASE_URL}/api/v1/reviews/requests?reviewId=${reviewId}`)
      if (resp.ok) {
        const data = await resp.json()
        setRevisionRequests(data.requests || [])
      }
    } finally {
      setRevisionRequestsLoading(false)
    }
  }

  const toggleActionIndex = (index: number) => {
    setSelectedActionIndexes((prev) => {
      const next = new Set(prev)
      if (next.has(index)) next.delete(index)
      else next.add(index)
      return next
    })
  }

  const toggleAllActions = () => {
    const items = runDetail?.actionItems || []
    if (selectedActionIndexes.size === items.length) {
      setSelectedActionIndexes(new Set())
    } else {
      setSelectedActionIndexes(new Set(items.map((_, index) => index)))
    }
  }

  const applySelectedActions = async () => {
    if (!runDetail || selectedActionIndexes.size === 0) return
    setApplyingActions(true)
    setApplyMessage('')
    try {
      const resp = await fetch(`${API_BASE_URL}/api/v1/reviews/${runDetail.id}/apply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ actionItemIndices: [...selectedActionIndexes] }),
      })
      if (resp.ok) {
        const data = await resp.json()
        setSelectedActionIndexes(new Set())
        const skipped = data.skippedCount ? ` Skipped ${data.skippedCount} duplicate item(s).` : ''
        setApplyMessage(`Created ${data.appliedCount || 0} improvement request(s).${skipped}`)
        void loadRevisionRequests(runDetail.id)
      } else {
        const data = await resp.json().catch(() => ({}))
        setApplyMessage(data.detail || 'Failed to apply selected action items.')
      }
    } finally {
      setApplyingActions(false)
    }
  }

  const updateRevisionRequestStatus = async (requestId: string, status: string) => {
    const resp = await fetch(`${API_BASE_URL}/api/v1/reviews/requests/${requestId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    })
    if (resp.ok && runDetail) {
      void loadRevisionRequests(runDetail.id)
      void loadComparison(runDetail.paperId, runDetail.id)
    }
  }

  useEffect(() => {
    setSelectedHistoryId('')
    setHistoryFindings(null)
    setRunDetail(null)
    setRevisionRequests([])
    setSelectedActionIndexes(new Set())
    setApplyMessage('')
    setComparison(null)
    setLatestResultsEnabled(false)
    setSearchQuery('')
    setSeverityFilter('all')
    void refreshHistory(selectedPaperId)
  }, [selectedPaperId])

  const filteredFindings = useMemo(() => {
    if (!findings) return []

    return findings.filter((finding) => {
      if (severityFilter !== 'all' && finding.severity !== severityFilter) return false
      if (searchQuery && !finding.title.toLowerCase().includes(searchQuery.toLowerCase()) &&
        !finding.description.toLowerCase().includes(searchQuery.toLowerCase())) return false
      return true
    })
  }, [findings, severityFilter, searchQuery])

  const groupedFindings = useMemo(() => {
    const groups: Record<string, typeof filteredFindings> = {
      blocker: [],
      major: [],
      minor: [],
      info: [],
    }

    filteredFindings.forEach((finding) => {
      groups[finding.severity].push(finding)
    })

    return groups
  }, [filteredFindings])

  const severityCounts = useMemo(() => {
    if (!findings) return { blocker: 0, major: 0, minor: 0, info: 0 }
    return findings.reduce((acc, f) => {
      acc[f.severity] = (acc[f.severity] || 0) + 1
      return acc
    }, {} as Record<string, number>)
  }, [findings])

  const riskTreeNodes = useMemo(() => runDetail?.riskTree || [], [runDetail?.riskTree])
  const riskNodeById = useMemo(() => {
    const map: Record<string, ReviewXRiskNode> = {}
    riskTreeNodes.forEach((node) => {
      map[node.id] = node
    })
    return map
  }, [riskTreeNodes])

  const rootRiskNodes = useMemo(() => {
    const explicitRoots = riskTreeNodes.filter((node) => node.id === 'risk_root')
    if (explicitRoots.length > 0) return explicitRoots
    return riskTreeNodes.filter((node) => !node.parentId)
  }, [riskTreeNodes])

  const actionItems = runDetail?.actionItems || []
  const mismatchAggregate = runDetail?.mismatchReport?.aggregate
  const topMismatchClaims = useMemo(() => {
    return [...(runDetail?.mismatchReport?.claimScores || [])]
      .sort((a, b) => (b.mismatchScore || 0) - (a.mismatchScore || 0))
      .slice(0, 5)
  }, [runDetail])
  const graphPreviewEdges = useMemo(() => {
    const nodesById: Record<string, string> = {}
    ;(runDetail?.evidenceGraph?.nodes || []).forEach((node) => {
      nodesById[node.id] = node.label || node.id
    })
    return (runDetail?.evidenceGraph?.edges || [])
      .filter((edge) => edge.edgeType === 'linked_to')
      .slice(0, 12)
      .map((edge) => ({
        ...edge,
        sourceLabel: nodesById[edge.source] || edge.source,
        targetLabel: nodesById[edge.target] || edge.target,
      }))
  }, [runDetail])

  const comparisonRows = [
    { key: 'score', label: 'Score', lowerIsBetter: false },
    { key: 'blockerCount', label: 'Blockers', lowerIsBetter: true },
    { key: 'unsupportedCount', label: 'Unsupported', lowerIsBetter: true },
    { key: 'contradictedCount', label: 'Contradicted', lowerIsBetter: true },
    { key: 'artifactAbsentCount', label: 'Artifact absent', lowerIsBetter: true },
    { key: 'needsHumanVerificationCount', label: 'Needs human review', lowerIsBetter: true },
    { key: 'findingCount', label: 'Findings', lowerIsBetter: true },
    { key: 'coverage', label: 'Coverage', lowerIsBetter: false, percent: true },
    { key: 'meanMismatch', label: 'Mean Mismatch', lowerIsBetter: true },
    { key: 'highMismatchClaimCount', label: 'High Mismatch', lowerIsBetter: true },
  ]

  const renderRiskNode = (node: ReviewXRiskNode, depth = 0) => {
    const score = Math.round((node.riskScore || 0) * 100)
    const mismatch = node.mismatchScore !== undefined ? Math.round(node.mismatchScore * 100) : null
    const childNodes = (node.children || [])
      .map((childId) => riskNodeById[childId])
      .filter(Boolean)
    return (
      <div key={node.id} className={depth === 0 ? 'space-y-2' : 'ml-4 border-l border-slate-200 pl-3 pt-2'}>
        <div className="rounded-md border border-slate-200 bg-white p-3">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="text-sm font-semibold text-slate-900 leading-snug">{node.question}</div>
              <div className="mt-1 text-xs text-muted-foreground">
                {node.id}
                {node.category && ` · ${node.category}`}
              </div>
            </div>
            <div className="flex flex-col items-end gap-1">
              <Badge variant={score >= 88 ? 'destructive' : score >= 62 ? 'default' : 'outline'}>{score}% risk</Badge>
              {mismatch !== null && (
                <Badge variant={mismatch >= 72 ? 'destructive' : mismatch >= 30 ? 'default' : 'outline'}>
                  {mismatch}% CEM
                </Badge>
              )}
            </div>
          </div>
          <div className="mt-2 flex flex-wrap gap-2 text-xs">
            {node.status && <Badge variant="outline">{node.status}</Badge>}
            {node.assignedModel && <Badge variant="outline">{node.assignedModel}</Badge>}
            {node.expansionPolicy && <Badge variant="secondary">{node.expansionPolicy}</Badge>}
            {node.claimIds && node.claimIds.length > 0 && <Badge variant="secondary">{node.claimIds.length} {text('主张', 'claims')}</Badge>}
            {node.findingIds && node.findingIds.length > 0 && <Badge variant="secondary">{node.findingIds.length} findings</Badge>}
          </div>
          {node.mismatchDrivers && node.mismatchDrivers.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1 text-xs">
              {node.mismatchDrivers.map((driver) => (
                <Badge key={driver} variant="outline">{text('驱动因素', 'Driver')}: {driver}</Badge>
              ))}
            </div>
          )}
          {node.supportCounts && Object.keys(node.supportCounts).length > 0 && (
            <div className="mt-2 text-xs text-muted-foreground">
              Support: {supportText(node.supportCounts)}
            </div>
          )}
        </div>
        {childNodes.length > 0 && (
          <div className="space-y-2">
            {childNodes.map((child) => renderRiskNode(child, depth + 1))}
          </div>
        )}
      </div>
    )
  }

  return (
    <AppPageLayout
      title={text('ReviewX 证据审计', 'ReviewX Evidence Auditor')}
      subtitle={text('审计科学主张，并将实验证据路由到下一轮科研迭代', 'Audit scientific claims and route experiment-driven research iterations')}
      icon={Shield}
      iconColor="orange"
      accentColor="orange"
    >
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3 border-l-4 border-amber-600 bg-amber-50 px-4 py-3 text-amber-950" role="note">
        <div className="flex min-w-0 items-start gap-3">
          <Database className="mt-0.5 h-4 w-4 shrink-0 text-amber-700" />
          <div>
            <div className="text-sm font-semibold">{text('数据范围：共享科研工作区', 'Data scope: shared research workspace')}</div>
            <p className="mt-0.5 text-xs leading-relaxed text-amber-900">
              {text(
                '论文、已完成运行和 ReviewX 历史当前由团队与评委账号共享，只有 API Key 按账号隔离。未点击“审计”或“运行”按钮不会创建新结果。',
                'Papers, completed runs, and ReviewX history are shared by team and judge accounts; only API keys are isolated per account. No new result is created until an audit or run command is clicked.',
              )}
            </p>
          </div>
        </div>
        <Badge variant="outline" className="shrink-0 border-amber-300 bg-white text-amber-900">
          {text('共享数据', 'Shared data')}
        </Badge>
      </div>

      <ExperimentFeedbackPanel initialFeedbackId={requestedFeedbackId} initialFocus={requestedFeedbackFocus} />
      <div className="grid grid-cols-1 xl:grid-cols-[420px_minmax(0,1fr)] gap-6 items-start">
        <div className="space-y-6">
          <Card className="shadow-md">
            <CardHeader className="bg-gradient-to-r from-slate-50 to-white border-b">
              <CardTitle className="text-xl">{text('运行 ReviewX 审计', 'Run ReviewX Audit')}</CardTitle>
              <CardDescription>{text('对照引用和生成的 artifact 审计科学主张', 'Audit claims against citations and generated artifacts')}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4 pt-6">
              <div className="space-y-2">
                <label className="text-sm font-semibold text-slate-700">{text('选择论文', 'Select Paper')}</label>
                {papersLoading ? (
                  <Skeleton className="h-10 w-full" />
                ) : (
                  <select
                    className="w-full rounded-md border-2 border-slate-200 bg-white px-4 py-2.5 text-sm font-medium hover:border-teal-500 focus:border-teal-500 focus:ring-2 focus:ring-teal-500/20 transition-colors"
                    value={selectedPaperId}
                    onChange={(e) => setSelectedPaperId(e.target.value)}
                  >
                    <option value="">{text('选择一篇论文...', 'Select a paper...')}</option>
                    {papers?.map((paper) => (
                      <option key={paper.id} value={paper.id}>
                        {paper.title}
                      </option>
                    ))}
                  </select>
                )}
              </div>
              <div className="space-y-2">
                <label className="text-sm font-semibold text-slate-700">{text('审计模式', 'Review Mode')}</label>
                <select
                  className="w-full rounded-md border-2 border-slate-200 bg-white px-4 py-2.5 text-sm font-medium hover:border-teal-500 focus:border-teal-500 focus:ring-2 focus:ring-teal-500/20 transition-colors"
                  value={budgetMode}
                  onChange={(e) => setBudgetMode(e.target.value)}
                >
                  <option value="local_only">Local Only</option>
                  <option value="balanced">Balanced</option>
                  <option value="deep">Deep</option>
                </select>
                <div className="text-xs text-muted-foreground">
                  {budgetMode === 'local_only'
                    ? text('Local Only 使用已保存的 artifact 和规则检查，不调用模型 API。', 'Local Only uses saved artifacts and rule-based checks. No model API call is made.')
                    : text('Balanced 和 Deep 先执行本地检查，再可能为高风险 finding 调用已配置模型。', 'Balanced and Deep run local checks first, then may call the configured model for high-risk findings.')}
                </div>
              </div>
              <label
                className={`flex items-start gap-3 rounded-md border px-3 py-3 ${
                  budgetMode === 'local_only'
                    ? 'cursor-not-allowed border-slate-200 bg-slate-50 opacity-60'
                    : visualAuditEnabled
                      ? 'cursor-pointer border-amber-300 bg-amber-50'
                      : 'cursor-pointer border-slate-200 bg-white hover:border-amber-300'
                }`}
              >
                <input
                  type="checkbox"
                  className="mt-0.5 h-4 w-4 accent-amber-600"
                  checked={visualAuditEnabled && budgetMode !== 'local_only'}
                  disabled={budgetMode === 'local_only'}
                  onChange={(event) => setVisualAuditEnabled(event.target.checked)}
                />
                <ScanSearch className="mt-0.5 h-4 w-4 shrink-0 text-amber-700" />
                <span className="min-w-0">
                  <span className="block text-sm font-semibold text-slate-800">
                    {text('图表视觉审计', 'Visual Figure Audit')}
                    <Badge variant="outline" className="ml-2 border-amber-300 bg-white text-[10px]">Qwen3-VL</Badge>
                  </span>
                  <span className="mt-1 block text-xs leading-relaxed text-muted-foreground">
                    {text(
                      '核对图中数值、趋势、坐标轴、图例与论文主张；仅发送本次选中的论文图表。',
                      'Check visible values, trends, axes, and legends against paper claims; only selected paper figures are sent.',
                    )}
                  </span>
                </span>
              </label>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                <Button
                  disabled={!selectedPaperId || runDetailLoading}
                  onClick={() => selectedPaperId && void loadLatestReviewX(selectedPaperId)}
                  variant="outline"
                  size="lg"
                >
                  <History className="h-4 w-4 mr-2" />
                  {text('加载最新结果', 'Load Latest')}
                </Button>
                <Button
                  disabled={!selectedPaperId || runConsistencyCheck.isPending}
                  onClick={() => {
                    if (selectedPaperId) {
                      runConsistencyCheck.mutate(
                        {
                          paperId: selectedPaperId,
                          budgetMode,
                          visualAuditEnabled: visualAuditEnabled && budgetMode !== 'local_only',
                          visualModel: visualAuditEnabled && budgetMode !== 'local_only' ? 'qwen3-vl-plus' : undefined,
                        },
                        {
                          onSuccess: () => {
                            setSelectedHistoryId('')
                            setHistoryFindings(null)
                            setLatestResultsEnabled(true)
                            void refreshHistory(selectedPaperId)
                            void loadLatestRunDetail(selectedPaperId)
                          },
                        }
                      )
                    }
                  }}
                  className="bg-teal-600 hover:bg-teal-700"
                  size="lg"
                >
                  {runConsistencyCheck.isPending ? text('ReviewX 正在运行...', 'Running ReviewX...') : text('运行新 ReviewX', 'Run New ReviewX')}
                </Button>
              </div>
              {runConsistencyCheck.isError && (
                <p className="text-sm text-destructive">
                  {text('ReviewX 运行失败，请检查后端日志。', 'ReviewX failed to run. Check backend logs for details.')}
                </p>
              )}
            </CardContent>
          </Card>

          {selectedPaperId && (
            <Card className="shadow-md">
              <CardHeader className="bg-gradient-to-r from-slate-50 to-white border-b">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <CardTitle className="text-xl flex items-center gap-2">
                      <History className="h-5 w-5 text-teal-600" />
                      ReviewX {text('历史', 'History')}
                    </CardTitle>
                    <CardDescription>{text('该论文已保存的 ReviewX 运行', 'Saved ReviewX runs for this paper')}</CardDescription>
                  </div>
                  <Button variant="outline" size="sm" onClick={() => void refreshHistory(selectedPaperId)}>
                    {text('刷新', 'Refresh')}
                  </Button>
                </div>
              </CardHeader>
              <CardContent className="pt-6">
                {selectedHistoryId && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="mb-3 w-full"
                    onClick={() => {
                      setSelectedHistoryId('')
                      setHistoryFindings(null)
                      void loadLatestReviewX(selectedPaperId)
                    }}
                  >
                    {text('显示最新结果', 'Show Latest')}
                  </Button>
                )}
                {historyLoading ? (
                  <div className="space-y-2">
                    {[...Array(3)].map((_, i) => (
                      <Skeleton key={i} className="h-16 w-full" />
                    ))}
                  </div>
                ) : history.length === 0 ? (
                  <div className="text-sm text-muted-foreground">
                    {text('暂无 ReviewX 历史，运行一次一致性检查后将在此生成。', 'No ReviewX history yet. Run a consistency check to create one.')}
                  </div>
                ) : (
                  <div className="space-y-3 max-h-[720px] overflow-y-auto pr-1">
                    {history.map((item) => (
                      <button
                        key={item.id}
                        type="button"
                        onClick={() => void loadHistoryFindings(item.id)}
                        className={`w-full text-left rounded-md border p-4 transition-colors hover:border-teal-500 hover:bg-teal-50/40 ${
                          selectedHistoryId === item.id ? 'border-teal-600 bg-teal-50' : 'border-slate-200 bg-white'
                        }`}
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <div className="text-sm font-semibold text-slate-900 truncate">
                              {formatDateTime(item.updatedAt || item.createdAt)}
                            </div>
                            <div className="mt-1 text-xs text-muted-foreground truncate">
                              {item.id}
                            </div>
                          </div>
                          <Badge variant={item.status === 'completed' ? 'secondary' : 'outline'}>
                            {item.budgetMode}
                          </Badge>
                        </div>
                        <div className="mt-3 flex flex-wrap gap-2 text-xs">
                          <Badge variant="outline">{item.findingCount || 0} findings</Badge>
                          <Badge variant="outline">{severityText(item.severityCounts)}</Badge>
                          <Badge variant="outline">{item.claimCount || 0} {text('主张', 'claims')}</Badge>
                          <Badge variant="outline">{item.evidenceCount || 0} {text('证据', 'evidence')}</Badge>
                          <Badge variant="outline">{item.verificationCount || 0} {text('检查', 'checks')}</Badge>
                          <Badge variant="outline">{item.riskQuestionCount || 0} {text('问题', 'questions')}</Badge>
                          <Badge variant="outline">{supportText(item.supportCounts)}</Badge>
                          {item.llmCallCount !== undefined && (
                            <Badge variant="outline">LLM {text('调用', 'calls')}: {item.llmCallCount}</Badge>
                          )}
                        </div>
                        {item.llmSkipped && item.llmSkipReason && (
                          <div className="mt-2 text-xs text-muted-foreground truncate">
                            {item.llmSkipReason}
                          </div>
                        )}
                      </button>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          {selectedPaperId && (
            <Card className="shadow-md">
              <CardHeader className="bg-gradient-to-r from-slate-50 to-white border-b">
                <CardTitle className="text-xl flex items-center gap-2">
                  <GitBranch className="h-5 w-5 text-teal-600" />
                  Mismatch Graph
                  <Badge variant="outline" className="ml-auto text-xs">{text('实验 Metric', 'Experiment Metrics')}</Badge>
                </CardTitle>
                <CardDescription>{text('用于 ReviewX 评估的主张-证据 mismatch 分数与图连接', 'Claim-evidence mismatch scores and graph links for ReviewX evaluation')}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4 pt-6">
                {runDetailLoading ? (
                  <div className="space-y-2">
                    <Skeleton className="h-16 w-full" />
                    <Skeleton className="h-24 w-full" />
                  </div>
                ) : !runDetail ? (
                  <div className="text-sm text-muted-foreground">
                    {text('加载 ReviewX 运行以检查 mismatch 分数和主张-证据连接。', 'Load a ReviewX run to inspect mismatch scores and claim-evidence links.')}
                  </div>
                ) : !mismatchAggregate ? (
                  <div className="text-sm text-muted-foreground">
                    {text('此运行早于 mismatch 评分功能，请重新运行 ReviewX 以生成图 metric。', 'This run was created before mismatch scoring was enabled. Run ReviewX again to generate graph metrics.')}
                  </div>
                ) : (
                  <>
                    <div className="grid grid-cols-3 gap-2">
                      <div className="rounded-md border border-slate-200 bg-white p-3">
                        <div className="text-lg font-bold text-slate-900">
                          {formatMetricValue(mismatchAggregate.meanMismatch)}
                        </div>
                        <div className="text-xs text-muted-foreground">{text('平均 mismatch', 'Mean mismatch')}</div>
                      </div>
                      <div className="rounded-md border border-slate-200 bg-white p-3">
                        <div className="text-lg font-bold text-slate-900">
                          {formatMetricValue(mismatchAggregate.maxMismatch)}
                        </div>
                        <div className="text-xs text-muted-foreground">{text('最大 mismatch', 'Max mismatch')}</div>
                      </div>
                      <div className="rounded-md border border-slate-200 bg-white p-3">
                        <div className="text-lg font-bold text-slate-900">
                          {mismatchAggregate.highMismatchClaimCount || 0}
                        </div>
                        <div className="text-xs text-muted-foreground">{text('高风险主张', 'High-risk claims')}</div>
                      </div>
                    </div>

                    <div className="flex flex-wrap gap-2 text-xs">
                      {runDetail.mismatchReport?.method?.name && (
                        <Badge variant="secondary">{runDetail.mismatchReport.method.name}</Badge>
                      )}
                      <Badge variant="outline">{runDetail.evidenceGraph?.nodeCount || 0} {text('图节点', 'graph nodes')}</Badge>
                      <Badge variant="outline">{runDetail.evidenceGraph?.edgeCount || 0} {text('图边', 'graph edges')}</Badge>
                      {Object.entries(mismatchAggregate.dimensionMax || {}).map(([name, value]) => (
                        <Badge key={name} variant="outline">{name}: {formatMetricValue(value)}</Badge>
                      ))}
                    </div>

                    {runDetail.mismatchReport?.method?.formula && (
                      <div className="rounded-md border border-slate-200 bg-slate-50 p-3 text-xs leading-relaxed text-slate-700">
                        {runDetail.mismatchReport.method.formula}
                      </div>
                    )}

                    {topMismatchClaims.length > 0 && (
                      <div className="rounded-md border border-slate-200 bg-white p-3">
                        <div className="text-xs font-semibold uppercase text-slate-600">Top Mismatch {text('主张', 'Claims')}</div>
                        <div className="mt-2 space-y-2">
                          {topMismatchClaims.map((claim) => (
                            <div key={claim.claimId} className="rounded border border-slate-100 bg-slate-50 p-2">
                              <div className="flex items-center justify-between gap-2">
                                <div className="text-xs font-semibold text-slate-900">{claim.claimId}</div>
                                <Badge variant={(claim.mismatchScore || 0) >= 0.72 ? 'destructive' : 'outline'}>
                                  {formatMetricValue(claim.mismatchScore)}
                                </Badge>
                              </div>
                              <div className="mt-1 line-clamp-2 text-xs text-slate-700">{claim.text}</div>
                              <div className="mt-1 flex flex-wrap gap-1">
                                {claim.supportStatus && <Badge variant="outline">Support: {supportStatusText(claim.supportStatus)}</Badge>}
                                {claim.rawMismatchScore !== undefined && claim.rawMismatchScore !== claim.mismatchScore && (
                                  <Badge variant="outline">Raw: {formatMetricValue(claim.rawMismatchScore)}</Badge>
                                )}
                                {claim.calibration?.llmDecision && (
                                  <Badge variant="outline">LLM: {claim.calibration.llmDecision}</Badge>
                                )}
                                {(claim.calibration?.revisionAdjustment || 0) > 0 && (
                                  <Badge variant="secondary">Revision -{formatMetricValue(claim.calibration?.revisionAdjustment)}</Badge>
                                )}
                                <Badge variant="outline">{claim.linkedEvidenceCount || 0} evidence links</Badge>
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {graphPreviewEdges.length > 0 && (
                      <div className="rounded-md border border-slate-200 bg-white p-3">
                        <div className="text-xs font-semibold uppercase text-slate-600">{text('主张-证据连接', 'Claim-Evidence Links')}</div>
                        <div className="mt-2 space-y-2 max-h-[320px] overflow-y-auto pr-1">
                          {graphPreviewEdges.map((edge) => (
                            <div key={edge.id} className="text-xs text-slate-700">
                              <span className="font-semibold">{edge.source}</span>
                              <span className="px-2 text-muted-foreground">{'->'}</span>
                              <span>{edge.target}</span>
                              <div className="mt-0.5 line-clamp-1 text-muted-foreground">
                                {edge.sourceLabel} / {edge.targetLabel}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </>
                )}
              </CardContent>
            </Card>
          )}

          {selectedPaperId && (
            <Card className="shadow-md">
              <CardHeader className="bg-gradient-to-r from-slate-50 to-white border-b">
                <CardTitle className="text-xl flex items-center gap-2">
                  <CheckCircle className="h-5 w-5 text-teal-600" />
                  {text('修订前 / 修订后', 'Before / After')}
                </CardTitle>
                <CardDescription>{text('当前 ReviewX 运行与上一次完成运行的存档对照', 'Stored comparison of this ReviewX run with the previous completed run')}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4 pt-6">
                {comparisonLoading ? (
                  <div className="space-y-2">
                    <Skeleton className="h-12 w-full" />
                    <Skeleton className="h-24 w-full" />
                  </div>
                ) : !comparison ? (
                  <div className="text-sm text-muted-foreground">
                    {text('该论文至少运行两次 ReviewX 才能对比修订进度。', 'Run ReviewX at least twice for this paper to compare revision progress.')}
                  </div>
                ) : (
                  <>
                    <div className="rounded-md border border-slate-200 bg-white p-3">
                      <div className="grid grid-cols-[1fr_64px_64px_64px] gap-2 text-xs font-semibold text-slate-600">
                        <span>Metric</span>
                        <span className="text-right">{text('修订前', 'Before')}</span>
                        <span className="text-right">{text('修订后', 'After')}</span>
                        <span className="text-right">Δ</span>
                      </div>
                      <div className="mt-2 space-y-2">
                        {comparisonRows.map((row) => (
                          <div key={row.key} className="grid grid-cols-[1fr_64px_64px_64px] items-center gap-2 text-sm">
                            <span className="text-slate-700">{row.label}</span>
                            <span className="text-right text-muted-foreground">
                              {formatMetricValue(comparison.before[row.key as keyof ReviewXComparisonMetrics] as number | undefined, row.percent)}
                            </span>
                            <span className="text-right font-medium text-slate-900">
                              {formatMetricValue(comparison.after[row.key as keyof ReviewXComparisonMetrics] as number | undefined, row.percent)}
                            </span>
                            <span className="text-right">
                              <Badge variant={deltaTone(comparison.delta[row.key], row.lowerIsBetter)}>
                                {formatDelta(comparison.delta[row.key], row.percent)}
                              </Badge>
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>

                    <div className="grid grid-cols-3 gap-2">
                      <div className="rounded-md border border-emerald-200 bg-emerald-50 p-3">
                        <div className="text-lg font-bold text-emerald-700">{comparison.resolvedFindings.length}</div>
                        <div className="text-xs text-emerald-900">{text('已解决', 'Resolved')}</div>
                      </div>
                      <div className="rounded-md border border-amber-200 bg-amber-50 p-3">
                        <div className="text-lg font-bold text-amber-700">{comparison.persistentFindings.length}</div>
                        <div className="text-xs text-amber-900">{text('持续存在', 'Persistent')}</div>
                      </div>
                      <div className="rounded-md border border-red-200 bg-red-50 p-3">
                        <div className="text-lg font-bold text-red-700">{comparison.newFindings.length}</div>
                        <div className="text-xs text-red-900">{text('新增', 'New')}</div>
                      </div>
                    </div>

                    {comparison.resolvedFindings.length > 0 && (
                      <div className="rounded-md border border-slate-200 bg-white p-3">
                        <div className="text-xs font-semibold uppercase text-slate-600">{text('最近解决', 'Recently Resolved')}</div>
                        <div className="mt-2 space-y-2">
                          {comparison.resolvedFindings.slice(0, 3).map((finding) => (
                            <div key={`${finding.claimId || finding.id}-${finding.title}`} className="text-xs text-slate-700">
                              {finding.title}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </>
                )}
              </CardContent>
            </Card>
          )}

          {selectedPaperId && (
            <Card className="shadow-md">
              <CardHeader className="bg-gradient-to-r from-slate-50 to-white border-b">
                <CardTitle className="text-xl flex items-center gap-2">
                  <GitBranch className="h-5 w-5 text-teal-600" />
                  {text('风险问题树', 'Risk Question Tree')}
                  <Badge variant="outline" className="ml-auto text-xs">{text('本地规则', 'Local Rules')}</Badge>
                </CardTitle>
                <CardDescription>{text('分层 ReviewX 审计问题与路由决策', 'Hierarchical ReviewX audit questions and routing decisions')}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3 pt-6">
                {runDetailLoading ? (
                  <div className="space-y-2">
                    <Skeleton className="h-16 w-full" />
                    <Skeleton className="h-16 w-full" />
                  </div>
                ) : rootRiskNodes.length === 0 ? (
                  <div className="text-sm text-muted-foreground">
                    {text('暂无风险问题树，运行 ReviewX 后将在此生成。', 'No risk question tree yet. Run ReviewX to create one.')}
                  </div>
                ) : (
                  <div className="max-h-[720px] overflow-y-auto pr-1">
                    {rootRiskNodes.map((node) => renderRiskNode(node))}
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          {selectedPaperId && (
            <Card className="shadow-md">
              <CardHeader className="bg-gradient-to-r from-slate-50 to-white border-b">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <CardTitle className="text-xl flex items-center gap-2">
                      <Target className="h-5 w-5 text-teal-600" />
                      {text('修订计划', 'Revision Plan')}
                      <Badge variant="outline" className="text-xs">{text('本地规则', 'Local Rules')}</Badge>
                    </CardTitle>
                    <CardDescription>{text('将 ReviewX 行动项转化为 FAROS 改进请求', 'Apply ReviewX action items into FAROS improvement requests')}</CardDescription>
                  </div>
                  {actionItems.length > 0 && (
                    <Badge variant="outline">{actionItems.length} {text('项行动', 'actions')}</Badge>
                  )}
                </div>
              </CardHeader>
              <CardContent className="space-y-4 pt-6">
                {runDetailLoading ? (
                  <div className="space-y-2">
                    <Skeleton className="h-16 w-full" />
                    <Skeleton className="h-16 w-full" />
                  </div>
                ) : !runDetail ? (
                  <div className="text-sm text-muted-foreground">
                    {text('尚未选择 ReviewX 运行。', 'No ReviewX run selected.')}
                  </div>
                ) : actionItems.length === 0 ? (
                  <div className="text-sm text-muted-foreground">
                    {text('此运行未生成修订行动。', 'No revision actions were generated for this run.')}
                  </div>
                ) : (
                  <>
                    <div className="flex items-center justify-between gap-3">
                      <Button variant="outline" size="sm" onClick={toggleAllActions}>
                        <Check className="h-3 w-3 mr-2" />
                        {selectedActionIndexes.size === actionItems.length ? text('全部取消', 'Deselect All') : text('全选', 'Select All')}
                      </Button>
                      <Button
                        size="sm"
                        onClick={() => void applySelectedActions()}
                        disabled={applyingActions || selectedActionIndexes.size === 0}
                        className="bg-teal-600 hover:bg-teal-700"
                      >
                        {applyingActions ? (
                          <Loader2 className="h-3 w-3 mr-2 animate-spin" />
                        ) : (
                          <ArrowRight className="h-3 w-3 mr-2" />
                        )}
                        {text('应用', 'Apply')} {selectedActionIndexes.size || ''}
                      </Button>
                    </div>

                    {applyMessage && (
                      <div className="rounded-md border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700">
                        {applyMessage}
                      </div>
                    )}

                    <div className="space-y-2 max-h-[520px] overflow-y-auto pr-1">
                      {actionItems.map((item, index) => (
                        <button
                          key={`${item.sourceFindingId || item.description}-${index}`}
                          type="button"
                          onClick={() => toggleActionIndex(index)}
                          className={`w-full rounded-md border p-3 text-left transition-colors ${
                            selectedActionIndexes.has(index)
                              ? 'border-teal-500 bg-teal-50'
                              : 'border-slate-200 bg-white hover:border-teal-400 hover:bg-teal-50/40'
                          }`}
                        >
                          <div className="flex items-start gap-3">
                            <input
                              type="checkbox"
                              checked={selectedActionIndexes.has(index)}
                              readOnly
                              className="mt-1 accent-teal-600"
                            />
                            <div className="min-w-0 flex-1">
                              <div className="flex flex-wrap items-center gap-2">
                                <Badge variant={item.severity === 'BLOCKER' ? 'destructive' : 'outline'}>
                                  {item.severity || 'MAJOR'}
                                </Badge>
                                <span className={`rounded border px-2 py-0.5 text-xs font-medium ${moduleBadgeClass(item.targetModule)}`}>
                                  {item.targetModule || 'papers'}
                                </span>
                                {item.supportStatus && (
                                  <Badge variant="outline">Support: {supportStatusText(item.supportStatus)}</Badge>
                                )}
                              </div>
                              <div className="mt-2 text-sm font-medium text-slate-900">
                                {item.description}
                              </div>
                              {item.suggestedEdit && (
                                <div className="mt-1 text-xs leading-relaxed text-muted-foreground">
                                  {item.suggestedEdit}
                                </div>
                              )}
                              {item.acceptanceCriteria && item.acceptanceCriteria.length > 0 && (
                                <div className="mt-2 space-y-1">
                                  {item.acceptanceCriteria.slice(0, 2).map((criterion, criterionIndex) => (
                                    <div key={criterionIndex} className="text-xs text-slate-600">
                                      {criterionIndex + 1}. {criterion}
                                    </div>
                                  ))}
                                </div>
                              )}
                            </div>
                          </div>
                        </button>
                      ))}
                    </div>
                  </>
                )}

                <div className="border-t pt-4">
                  <div className="mb-2 flex items-center justify-between">
                    <div className="text-sm font-semibold text-slate-900">{text('改进请求', 'Improvement Requests')}</div>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => runDetail && void loadRevisionRequests(runDetail.id)}
                      disabled={!runDetail || revisionRequestsLoading}
                    >
                      {text('刷新', 'Refresh')}
                    </Button>
                  </div>
                  {revisionRequestsLoading ? (
                    <div className="space-y-2">
                      <Skeleton className="h-12 w-full" />
                      <Skeleton className="h-12 w-full" />
                    </div>
                  ) : revisionRequests.length === 0 ? (
                    <div className="text-xs text-muted-foreground">
                      {text('此 ReviewX 运行尚未创建改进请求。', 'No improvement requests created from this ReviewX run yet.')}
                    </div>
                  ) : (
                    <div className="space-y-2 max-h-[320px] overflow-y-auto pr-1">
                      {revisionRequests.map((request) => (
                        <div key={request.id} className="rounded-md border border-slate-200 bg-white p-3">
                          <div className="flex flex-wrap items-center gap-2">
                            <Badge variant="outline">{request.status}</Badge>
                            <Badge variant={request.severity === 'BLOCKER' ? 'destructive' : 'outline'}>
                              {request.severity}
                            </Badge>
                            <span className={`rounded border px-2 py-0.5 text-xs font-medium ${moduleBadgeClass(request.targetModule)}`}>
                              {request.targetModule}
                            </span>
                          </div>
                          <div className="mt-2 text-sm font-medium text-slate-900">
                            {request.description}
                          </div>
                          {request.suggestedEdit && (
                            <div className="mt-1 text-xs leading-relaxed text-muted-foreground">
                              {request.suggestedEdit}
                            </div>
                          )}
                          <div className="mt-3 flex flex-wrap gap-2">
                            {request.status !== 'in_progress' && (
                              <Button
                                variant="outline"
                                size="sm"
                                className="h-7 text-xs"
                                onClick={() => void updateRevisionRequestStatus(request.id, 'in_progress')}
                              >
                                {text('开始', 'Start')}
                              </Button>
                            )}
                            {request.status !== 'resolved' && (
                              <Button
                                variant="outline"
                                size="sm"
                                className="h-7 text-xs"
                                onClick={() => void updateRevisionRequestStatus(request.id, 'resolved')}
                              >
                                {text('解决', 'Resolve')}
                              </Button>
                            )}
                            {request.status !== 'verified' && (
                              <Button
                                variant="outline"
                                size="sm"
                                className="h-7 text-xs"
                                onClick={() => void updateRevisionRequestStatus(request.id, 'verified')}
                              >
                                {text('验证', 'Verify')}
                              </Button>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>
          )}

          {selectedPaperId && (
            <Card className="shadow-md">
              <CardHeader className="bg-gradient-to-r from-slate-50 to-white border-b">
                <CardTitle className="text-xl flex items-center gap-2">
                  <Route className="h-5 w-5 text-teal-600" />
                  {text('模型轨迹', 'Model Trace')}
                  <Badge variant={hasLlmCalls ? 'secondary' : 'outline'} className="ml-auto text-xs">
                    {hasLlmCalls ? text('LLM 增强', 'LLM Refined') : text('未调用 LLM', 'No LLM Call')}
                  </Badge>
                </CardTitle>
                <CardDescription>{text('预算路由与 Qwen/provider 升级轨迹', 'Budget routing and Qwen/provider escalation trace')}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4 pt-6">
                {runDetailLoading ? (
                  <div className="space-y-2">
                    <Skeleton className="h-12 w-full" />
                    <Skeleton className="h-20 w-full" />
                  </div>
                ) : !runDetail?.modelTrace ? (
                  <div className="text-sm text-muted-foreground">
                    {text('暂无模型轨迹，运行 ReviewX 后将在此生成。', 'No model trace yet. Run ReviewX to create one.')}
                  </div>
                ) : (
                  <>
                    <div className="grid grid-cols-2 gap-2 text-xs">
                      <Badge variant="outline">Mode: {runDetail.modelTrace.routingMode || runDetail.budgetMode || 'unknown'}</Badge>
                      <Badge variant="outline">Provider: {runDetail.modelTrace.llmRouting?.providerName || runDetail.providerName || 'unknown'}</Badge>
                      <Badge variant="outline">Model: {runDetail.modelTrace.llmRouting?.requestedModel || runDetail.model || 'unknown'}</Badge>
                      <Badge variant="outline">Tokens: {runDetail.modelTrace.estimatedTokenCost || 0}</Badge>
                      {runDetail.modelTrace.llmRouting?.budgetPolicy && (
                        <Badge variant="secondary">Policy: {runDetail.modelTrace.llmRouting.budgetPolicy}</Badge>
                      )}
                    </div>

                    {visualTrace?.enabled && (
                      <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-950">
                        <div className="flex flex-wrap items-center gap-2">
                          <ScanSearch className="h-4 w-4 text-amber-700" />
                          <span className="font-semibold">{text('图表视觉审计', 'Visual Figure Audit')}</span>
                          <Badge variant={visualTrace.status === 'completed' ? 'secondary' : 'outline'}>
                            {visualTrace.status || 'unknown'}
                          </Badge>
                          <Badge variant="outline">{visualTrace.model || 'qwen3-vl-plus'}</Badge>
                        </div>
                        <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
                          <span>{text('候选图', 'Selected')}: {visualTrace.selectedFigureCount || 0}</span>
                          <span>{text('已审计', 'Audited')}: {visualTrace.auditedFigureCount || 0}</span>
                          <span>{text('核验', 'Checks')}: {visualTrace.checkCount || 0}</span>
                          <span>{text('异常', 'Anomalies')}: {visualTrace.anomalyCount || 0}</span>
                        </div>
                        {visualTrace.skipReason && (
                          <div className="mt-2 leading-relaxed text-amber-900">{visualTrace.skipReason}</div>
                        )}
                      </div>
                    )}

                    {runDetail.modelTrace.llmRouting?.budgetFormula && (
                      <div className="rounded-md border border-slate-200 bg-slate-50 p-3 text-xs leading-relaxed text-slate-700">
                        {runDetail.modelTrace.llmRouting.budgetFormula}
                      </div>
                    )}

                    {runDetail.modelTrace.llmRouting?.selectedFindingIds && runDetail.modelTrace.llmRouting.selectedFindingIds.length > 0 && (
                      <div className="rounded-md border border-slate-200 bg-white p-3">
                        <div className="text-xs font-semibold uppercase text-slate-600">Selected Findings</div>
                        <div className="mt-2 flex flex-wrap gap-2">
                          {runDetail.modelTrace.llmRouting.selectedFindingIds.map((id) => (
                            <Badge key={id} variant="secondary">{id}</Badge>
                          ))}
                        </div>
                      </div>
                    )}

                    {runDetail.modelTrace.llmRouting?.budgetAllocations && runDetail.modelTrace.llmRouting.budgetAllocations.length > 0 && (
                      <div className="rounded-md border border-slate-200 bg-white p-3">
                        <div className="flex items-center justify-between gap-2">
                          <div className="text-xs font-semibold uppercase text-slate-600">CEM Budget Allocation</div>
                          <Badge variant="outline">{runDetail.modelTrace.llmRouting.budgetAllocations.length} findings</Badge>
                        </div>
                        <div className="mt-2 space-y-2 max-h-[280px] overflow-y-auto pr-1">
                          {runDetail.modelTrace.llmRouting.budgetAllocations.slice(0, 10).map((item) => (
                            <div key={item.findingId} className="rounded border border-slate-100 bg-slate-50 p-2 text-xs">
                              <div className="flex flex-wrap items-center gap-2">
                                <Badge variant={item.selected ? 'secondary' : 'outline'}>{item.findingId}</Badge>
                                <Badge variant="outline">Priority: {formatMetricValue(item.priority)}</Badge>
                                <Badge variant={(item.mismatchScore || 0) >= 0.72 ? 'destructive' : 'outline'}>
                                  CEM: {formatMetricValue(item.mismatchScore)}
                                </Badge>
                                <Badge variant="outline">{item.recommendedModel || 'rules'}</Badge>
                              </div>
                              <div className="mt-1 flex flex-wrap gap-1">
                                {item.claimId && <Badge variant="outline">Claim: {item.claimId}</Badge>}
                                {item.severity && <Badge variant="outline">{item.severity}</Badge>}
                                {item.supportStatus && <Badge variant="outline">Support: {supportStatusText(item.supportStatus)}</Badge>}
                                {(item.drivers || []).map((driver) => (
                                  <Badge key={`${item.findingId}-${driver}`} variant="outline">{driver}</Badge>
                                ))}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {runDetail.modelTrace.llmRouting?.skipped && (
                      <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                        {runDetail.modelTrace.llmRouting.skipReason || 'LLM escalation skipped'}
                      </div>
                    )}

                    {(runDetail.modelTrace.llmCalls || []).length > 0 ? (
                      <div className="space-y-2">
                        {(runDetail.modelTrace.llmCalls || []).map((call, index) => (
                          <div key={`${call.task || 'call'}-${index}`} className="rounded-md border border-slate-200 bg-white p-3">
                            <div className="flex items-center justify-between gap-2">
                              <div className="text-sm font-semibold text-slate-900">{call.task || `LLM call ${index + 1}`}</div>
                              <Badge variant="outline">{call.model || 'model'}</Badge>
                            </div>
                            <div className="mt-2 grid grid-cols-2 gap-2 text-xs text-muted-foreground">
                              <span>Provider: {call.provider || 'unknown'}</span>
                              <span>Latency: {call.latencyMs ?? 0} ms</span>
                              <span>Total tokens: {call.usage?.total_tokens ?? 0}</span>
                              <span>Finish: {call.finishReason || 'unknown'}</span>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="text-xs text-muted-foreground">
                        No LLM calls were made for this run.
                      </div>
                    )}
                  </>
                )}
              </CardContent>
            </Card>
          )}
        </div>

        <div className="space-y-6 min-w-0">
          {selectedPaperId && findings && (
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
              <Card className="border-l-4 border-l-red-500 bg-gradient-to-br from-red-50/50 to-white">
                <CardContent className="pt-6">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-sm font-medium text-slate-600">{text('阻断项 (Blocker)', 'Blockers')}</p>
                      <p className="text-3xl font-bold text-red-600">{severityCounts.blocker || 0}</p>
                    </div>
                    <AlertCircle className="h-8 w-8 text-red-500" />
                  </div>
                </CardContent>
              </Card>
              <Card className="border-l-4 border-l-orange-500 bg-gradient-to-br from-orange-50/50 to-white">
                <CardContent className="pt-6">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-sm font-medium text-slate-600">{text('主要问题', 'Major Issues')}</p>
                      <p className="text-3xl font-bold text-orange-600">{severityCounts.major || 0}</p>
                    </div>
                    <AlertTriangle className="h-8 w-8 text-orange-500" />
                  </div>
                </CardContent>
              </Card>
              <Card className="border-l-4 border-l-blue-500 bg-gradient-to-br from-blue-50/50 to-white">
                <CardContent className="pt-6">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-sm font-medium text-slate-600">{text('次要问题', 'Minor Issues')}</p>
                      <p className="text-3xl font-bold text-blue-600">{severityCounts.minor || 0}</p>
                    </div>
                    <Info className="h-8 w-8 text-blue-500" />
                  </div>
                </CardContent>
              </Card>
              <Card className="border-l-4 border-l-teal-500 bg-gradient-to-br from-teal-50/50 to-white">
                <CardContent className="pt-6">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-sm font-medium text-slate-600">{text('提示', 'Info')}</p>
                      <p className="text-3xl font-bold text-teal-600">{severityCounts.info || 0}</p>
                    </div>
                    <CheckCircle className="h-8 w-8 text-teal-500" />
                  </div>
                </CardContent>
              </Card>
            </div>
          )}

          {selectedPaperId ? (
            <Card className="shadow-md">
          <CardHeader className="bg-gradient-to-r from-slate-50 to-white border-b">
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="text-xl">{text('审计结果', 'Audit Results')} ({findings ? filteredFindings.length : 0})</CardTitle>
                <CardDescription>
                  {findings
                    ? text('ReviewX finding 按严重程度分组', 'ReviewX findings grouped by severity')
                    : text('尚未加载 ReviewX 运行', 'No ReviewX run is loaded yet')}
                </CardDescription>
              </div>
              <div className="flex flex-wrap justify-end gap-2">
                <Badge variant="outline" className="text-sm px-3 py-1">{runSourceLabel}</Badge>
                {runDetail && (
                  <Badge variant={hasLlmCalls ? 'secondary' : 'outline'} className="text-sm px-3 py-1">
                    {hasLlmCalls ? text('LLM 增强', 'LLM Refined') : text('本地规则', 'Local Rules')}
                  </Badge>
                )}
                <Badge variant="outline" className="text-sm px-3 py-1">
                  {findings ? filteredFindings.length : 0} findings
                </Badge>
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-4 pt-6">
            {!findings && !findingsLoading ? (
              <div className="rounded-md border border-slate-200 bg-slate-50 p-6 text-center text-sm text-muted-foreground">
                {text('选择论文后，点击“加载最新结果”查看存档，或运行新 ReviewX 审计。', 'Select a paper, then click Load Latest to inspect saved results or Run New ReviewX to create a new audit.')}
              </div>
            ) : (
              <>
            <div className="flex gap-3">
              <select
                className="rounded-md border-2 border-slate-200 bg-white px-4 py-2 text-sm font-medium hover:border-teal-500 focus:border-teal-500 focus:ring-2 focus:ring-teal-500/20 transition-colors"
                value={severityFilter}
                onChange={(e) => setSeverityFilter(e.target.value)}
              >
                <option value="all">{text('全部级别', 'All Severities')}</option>
                <option value="blocker">Blocker</option>
                <option value="major">Major</option>
                <option value="minor">Minor</option>
                <option value="info">Info</option>
              </select>
              <input
                type="search"
                placeholder={text('搜索 finding...', 'Search findings...')}
                className="flex-1 rounded-md border-2 border-slate-200 bg-white px-4 py-2 text-sm hover:border-teal-500 focus:border-teal-500 focus:ring-2 focus:ring-teal-500/20 transition-colors"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
            </div>

            {findingsLoading ? (
              <div className="space-y-2">
                {[...Array(5)].map((_, i) => (
                  <Skeleton key={i} className="h-24 w-full" />
                ))}
              </div>
            ) : filteredFindings.length === 0 ? (
              <div className="text-center py-8 text-sm text-muted-foreground">
                {text('未发现 finding。', 'No findings. Paper looks good.')}
              </div>
            ) : (
              <div className="space-y-8">
                {Object.entries(groupedFindings).map(([severity, items]) =>
                  items.length > 0 && (
                    <div key={severity} className="space-y-3">
                      <div className="flex items-center gap-3 pb-2 border-b-2" style={{
                        borderColor: severity === 'blocker' ? '#ef4444' :
                          severity === 'major' ? '#f97316' :
                            severity === 'minor' ? '#3b82f6' : '#14b8a6'
                      }}>
                        {severityIcons[severity as keyof typeof severityIcons]}
                        <h3 className="text-lg font-bold capitalize text-slate-900">{severity}</h3>
                        <Badge variant="outline" className="ml-auto">{items.length} {text('个问题', 'issues')}</Badge>
                      </div>
                      <div className="space-y-3">
                        {items.map((finding) => (
                          <Card key={finding.id} className="border-l-4 shadow-sm hover:shadow-md transition-shadow" style={{
                            borderLeftColor: severity === 'blocker' ? '#ef4444' :
                              severity === 'major' ? '#f97316' :
                                severity === 'minor' ? '#3b82f6' : '#14b8a6'
                          }}>
                            <CardHeader className="pb-3">
                              <div className="flex items-start justify-between">
                                <div className="flex-1">
                                  <div className="flex items-center gap-2 mb-2">
                                    <CardTitle className="text-base font-semibold text-slate-900">{finding.title}</CardTitle>
                                    <Badge variant={severityVariants[severity as keyof typeof severityVariants]} className="capitalize text-xs">
                                      {severity}
                                    </Badge>
                                    {finding.riskType && (
                                      <Badge variant="outline" className="text-xs">
                                        {finding.riskType}
                                      </Badge>
                                    )}
                                    <Badge
                                      variant={findingHasLlmRefinement(finding) ? 'secondary' : 'outline'}
                                      className="text-xs"
                                    >
                                    {findingHasLlmRefinement(finding) ? text('LLM 增强', 'LLM Refined') : text('本地规则', 'Local Rule')}
                                  </Badge>
                                  {finding.reviewerDecision && (
                                    <Badge variant="outline" className="text-xs">
                                      Decision: {finding.reviewerDecision}
                                    </Badge>
                                  )}
                                  {finding.revisionStatus && (
                                    <Badge variant="secondary" className="text-xs">
                                      Revision: {finding.revisionStatus}
                                    </Badge>
                                  )}
                                </div>
                                  <CardDescription className="text-sm leading-relaxed">
                                    {finding.description}
                                  </CardDescription>
                                </div>
                              </div>
                            </CardHeader>
                            <CardContent className="space-y-4">
                              <div className="flex flex-wrap gap-2 text-xs">
                                {finding.targetModule && (
                                  <Badge variant="secondary">Target: {finding.targetModule}</Badge>
                                )}
                                {finding.confidence !== undefined && (
                                  <Badge variant="outline">Confidence: {Math.round(finding.confidence * 100)}%</Badge>
                                )}
                                {finding.supportStatus && (
                                  <Badge variant="outline">Support: {supportStatusText(finding.supportStatus)}</Badge>
                                )}
                                {finding.claimId && (
                                  <Badge variant="outline">Claim: {finding.claimId}</Badge>
                                )}
                                {finding.verifierIds && finding.verifierIds.length > 0 && (
                                  <Badge variant="outline">Verifier: {finding.verifierIds.join(', ')}</Badge>
                                )}
                                {finding.reviewerModel && (
                                  <Badge variant="outline">Reviewer: {finding.reviewerModel}</Badge>
                                )}
                                {finding.revisionRequestIds && finding.revisionRequestIds.length > 0 && (
                                  <Badge variant="outline">Requests: {finding.revisionRequestIds.length}</Badge>
                                )}
                                {finding.evidenceIds && finding.evidenceIds.length > 0 && (
                                  <Badge variant="outline">Evidence: {finding.evidenceIds.join(', ')}</Badge>
                                )}
                              </div>

                              {finding.evidence && (
                                <div className="bg-slate-50 border-l-2 border-slate-300 rounded-r-md">
                                  <div className="px-4 py-2 bg-slate-100 border-b border-slate-200">
                                    <span className="text-xs font-semibold text-slate-700 uppercase tracking-wide">{text('证据', 'Evidence')}</span>
                                  </div>
                                  <div className="p-4">
                                    <pre className="text-xs font-mono text-slate-700 whitespace-pre-wrap leading-relaxed">
                                      {finding.evidence}
                                    </pre>
                                  </div>
                                </div>
                              )}

                              {finding.suggestedFix && (
                                <div className="bg-teal-50 border-l-2 border-teal-400 rounded-r-md">
                                  <div className="px-4 py-2 bg-teal-100 border-b border-teal-200">
                                    <span className="text-xs font-semibold text-teal-800 uppercase tracking-wide">{text('建议修复', 'Suggested Fix')}</span>
                                  </div>
                                  <div className="p-4">
                                    <p className="text-sm text-teal-900 leading-relaxed">{finding.suggestedFix}</p>
                                  </div>
                                </div>
                              )}

                              <div className="flex items-center gap-2 pt-3 border-t">
                                {finding.relatedRunId && (
                                  <Link to={`/runs/${finding.relatedRunId}`}>
                                    <Button variant="outline" size="sm" className="hover:bg-teal-50 hover:border-teal-500">
                                      <ExternalLink className="h-3 w-3 mr-2" />
                                      {text('查看运行', 'View Run')}
                                    </Button>
                                  </Link>
                                )}
                              </div>
                            </CardContent>
                          </Card>
                        ))}
                      </div>
                    </div>
                  )
                )}
              </div>
            )}
              </>
            )}
              </CardContent>
            </Card>
          ) : (
            <Card className="shadow-md">
              <CardContent className="py-16 text-center text-sm text-muted-foreground">
                {text('选择一篇论文以查看 ReviewX 结果和历史。', 'Select a paper to view ReviewX results and history.')}
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </AppPageLayout>
  )
}
