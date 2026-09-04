import { useEffect, useState, useMemo, useRef } from 'react'
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
  ChevronDown,
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

const severityLabels = {
  blocker: ['阻断问题', 'Blocker'],
  major: ['主要问题', 'Major'],
  minor: ['次要问题', 'Minor'],
  info: ['提示', 'Info'],
} as const

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
  claims?: Array<{ id: string }>
  jsonReport?: {
    summary?: {
      claimCount?: number
      evidenceCount?: number
      verificationCount?: number
      findingCount?: number
      riskQuestionCount?: number
      coverage?: number
    }
  }
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

const formatDateTime = (value: string | undefined, locale: 'zh-CN' | 'en-US') => {
  if (!value) return locale === 'zh-CN' ? '时间未知' : 'Unknown time'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(locale, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}

const severityText = (counts: Record<string, number> | undefined, locale: 'zh-CN' | 'en-US') => {
  const c = counts || {}
  return locale === 'zh-CN'
    ? `阻断 ${c.blocker || 0} · 主要 ${c.major || 0}`
    : `Blocker ${c.blocker || 0} · Major ${c.major || 0}`
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

const mismatchDimensionLabels: Record<string, [string, string]> = {
  baseline: ['基线完整性', 'Baseline'],
  citation: ['引用完整性', 'Citation'],
  citation_semantic: ['引用语义', 'Citation semantics'],
  coverage: ['证据覆盖', 'Evidence coverage'],
  general: ['综合风险', 'General risk'],
  guardrail: ['护栏', 'Guardrail'],
  importance: ['主张重要性', 'Claim importance'],
  numeric: ['数值一致性', 'Numeric consistency'],
  review_risk: ['评审风险', 'Review risk'],
  visual_claim_consistency: ['图文一致性', 'Visual consistency'],
}

export function ConsistencyChecker() {
  const { text, locale } = useReviewLocale()
  const [searchParams] = useSearchParams()
  const requestedPaperId = searchParams.get('paperId')?.trim() || ''
  const requestedReviewId = searchParams.get('reviewId')?.trim() || ''
  const openedDeepLinkRef = useRef('')
  const historyLoaderRef = useRef<(reviewId: string) => Promise<void>>(async () => undefined)
  const requestedFeedbackId = searchParams.get('feedbackId') || undefined
  const requestedFeedbackFocus = searchParams.get('focus') === 'signoff' ? 'signoff' : 'loop'
  const { data: papers, isLoading: papersLoading } = usePapers()
  const [selectedPaperId, setSelectedPaperId] = useState<string>(requestedPaperId)
  const [budgetMode, setBudgetMode] = useState<string>('balanced')
  const [visualAuditEnabled, setVisualAuditEnabled] = useState(false)
  const [severityFilter, setSeverityFilter] = useState<string>('all')
  const [searchQuery, setSearchQuery] = useState('')
  const [history, setHistory] = useState<ReviewXHistoryItem[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [selectedHistoryId, setSelectedHistoryId] = useState<string>('')
  const [historyFindings, setHistoryFindings] = useState<ReviewFinding[] | null>(null)
  const [historyFindingsLoading, setHistoryFindingsLoading] = useState(false)
  const [historyLoadError, setHistoryLoadError] = useState(false)
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
  const [feedbackPanelOpen, setFeedbackPanelOpen] = useState(Boolean(requestedFeedbackId))

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
    setHistoryLoadError(false)
    setHistoryFindingsLoading(true)
    setRunDetailLoading(true)
    try {
      const [findingsResp, detailResp] = await Promise.all([
        fetch(`${API_BASE_URL}/api/v1/reviews/reviewx/${reviewId}/findings`),
        fetch(`${API_BASE_URL}/api/v1/reviews/reviewx/${reviewId}`),
      ])
      if (!findingsResp.ok || !detailResp.ok) {
        throw new Error('Saved ReviewX run is unavailable')
      }
      const findingsData = await findingsResp.json()
      const detailData = await detailResp.json()
      if (!Array.isArray(findingsData) || !detailData?.id || !detailData?.paperId) {
        throw new Error('Saved ReviewX run returned an invalid response')
      }
      setHistoryFindings(findingsData)
      setRunDetail(detailData)
      setSelectedActionIndexes(new Set())
      setApplyMessage('')
      void loadRevisionRequests(detailData.id)
      void loadComparison(detailData.paperId, detailData.id)
    } catch {
      setSelectedHistoryId('')
      setHistoryFindings(null)
      setRunDetail(null)
      setRevisionRequests([])
      setComparison(null)
      setHistoryLoadError(true)
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
    setHistoryLoadError(false)
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
  historyLoaderRef.current = loadHistoryFindings

  useEffect(() => {
    openedDeepLinkRef.current = ''
    setSelectedHistoryId('')
    setHistoryFindings(null)
    setRunDetail(null)
    setRevisionRequests([])
    setSelectedActionIndexes(new Set())
    setApplyMessage('')
    setComparison(null)
    setHistoryLoadError(false)
    setLatestResultsEnabled(false)
    setSearchQuery('')
    setSeverityFilter('all')
    void refreshHistory(selectedPaperId)
  }, [selectedPaperId])

  useEffect(() => {
    if (requestedPaperId) setSelectedPaperId(requestedPaperId)
  }, [requestedPaperId])

  useEffect(() => {
    if (requestedFeedbackId) setFeedbackPanelOpen(true)
  }, [requestedFeedbackId])

  useEffect(() => {
    if (!requestedPaperId || selectedPaperId !== requestedPaperId || !requestedReviewId) return
    const deepLinkKey = `${requestedPaperId}:${requestedReviewId}`
    if (openedDeepLinkRef.current === deepLinkKey) return
    openedDeepLinkRef.current = deepLinkKey
    void historyLoaderRef.current(requestedReviewId)
  }, [requestedPaperId, requestedReviewId, selectedPaperId])

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
  const mismatchDimensions = useMemo(
    () => Object.entries(mismatchAggregate?.dimensionMax || {})
      .sort((left, right) => right[1] - left[1]),
    [mismatchAggregate?.dimensionMax],
  )
  const activeHistory = useMemo(
    () => history.find((item) => item.id === runDetail?.id),
    [history, runDetail?.id],
  )
  const auditSummary = runDetail?.jsonReport?.summary
  const auditScore = runDetail?.scoreSuggestion ?? activeHistory?.scoreSuggestion
  const auditClaimCount = auditSummary?.claimCount ?? activeHistory?.claimCount ?? runDetail?.claims?.length ?? 0
  const auditEvidenceCount = auditSummary?.evidenceCount ?? activeHistory?.evidenceCount ?? 0
  const auditVerificationCount = auditSummary?.verificationCount ?? activeHistory?.verificationCount ?? 0
  const auditTraceAvailable = Boolean(runDetail?.mismatchReport && runDetail?.evidenceGraph && runDetail?.modelTrace)
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
        <div className="min-w-0 overflow-hidden rounded-md border border-slate-200 bg-white p-3">
          <div className="flex min-w-0 flex-col items-start gap-2 sm:flex-row sm:justify-between">
            <div className="min-w-0">
              <div className="text-sm font-semibold text-slate-900 leading-snug">{node.question}</div>
              <div className="mt-1 break-words text-xs text-muted-foreground [overflow-wrap:anywhere]">
                {node.id}
                {node.category && ` · ${node.category}`}
              </div>
            </div>
            <div className="flex shrink-0 flex-row flex-wrap gap-1 sm:flex-col sm:items-end">
              <Badge variant={score >= 88 ? 'destructive' : score >= 62 ? 'default' : 'outline'}>{score}% risk</Badge>
              {mismatch !== null && (
                <Badge variant={mismatch >= 72 ? 'destructive' : mismatch >= 30 ? 'default' : 'outline'}>
                  {mismatch}% CEM
                </Badge>
              )}
            </div>
          </div>
          <div className="mt-2 flex min-w-0 flex-wrap gap-2 text-xs">
            {node.status && <Badge variant="outline" className="max-w-full whitespace-normal break-all">{node.status}</Badge>}
            {node.assignedModel && <Badge variant="outline" className="max-w-full whitespace-normal break-all">{node.assignedModel}</Badge>}
            {node.expansionPolicy && <Badge variant="secondary" className="max-w-full whitespace-normal break-all">{node.expansionPolicy}</Badge>}
            {node.claimIds && node.claimIds.length > 0 && <Badge variant="secondary">{node.claimIds.length} {text('主张', 'claims')}</Badge>}
            {node.findingIds && node.findingIds.length > 0 && <Badge variant="secondary">{node.findingIds.length} findings</Badge>}
          </div>
          {node.mismatchDrivers && node.mismatchDrivers.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1 text-xs">
              {node.mismatchDrivers.map((driver) => (
                <Badge key={driver} variant="outline" className="max-w-full whitespace-normal break-all">{text('驱动因素', 'Driver')}: {driver}</Badge>
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

      <section className="mb-6 overflow-hidden border border-emerald-200 bg-white">
        <button
          type="button"
          className="flex w-full items-center justify-between gap-4 px-4 py-3 text-left transition-colors hover:bg-emerald-50/60 sm:px-5"
          aria-expanded={feedbackPanelOpen}
          onClick={() => setFeedbackPanelOpen((open) => !open)}
        >
          <span className="flex min-w-0 items-center gap-3">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-emerald-700 text-white">
              <Route className="h-4 w-4" />
            </span>
            <span className="min-w-0">
              <span className="block text-sm font-bold text-slate-950">
                {text('实验反馈闭环与人工签核', 'Experiment feedback loop and human signoff')}
              </span>
              <span className="mt-0.5 block text-xs leading-5 text-slate-600">
                {text('审计完成后，在这里决定退回计划、创建下一轮并确认可发布结论。', 'After an audit, return findings to the plan, create the next iteration, and sign off conclusions here.')}
              </span>
            </span>
          </span>
          <span className="flex shrink-0 items-center gap-2 text-xs font-semibold text-emerald-800">
            {feedbackPanelOpen ? text('收起', 'Collapse') : text('展开闭环', 'Open loop')}
            <ChevronDown className={`h-4 w-4 transition-transform ${feedbackPanelOpen ? 'rotate-180' : ''}`} />
          </span>
        </button>
        {feedbackPanelOpen && (
          <div className="border-t border-emerald-200 px-3 pt-4 sm:px-4">
            <ExperimentFeedbackPanel initialFeedbackId={requestedFeedbackId} initialFocus={requestedFeedbackFocus} />
          </div>
        )}
      </section>
      <div className="grid min-w-0 grid-cols-1 items-start gap-6 xl:grid-cols-[420px_minmax(0,1fr)]">
        <div className="contents xl:block xl:min-w-0 xl:space-y-6">
          <Card className="order-1 min-w-0 overflow-hidden shadow-md xl:order-none">
            <CardHeader className="bg-gradient-to-r from-slate-50 to-white border-b">
              <CardTitle className="text-xl">{text('运行 ReviewX 审计', 'Run ReviewX Audit')}</CardTitle>
              <CardDescription>{text('对照引用和生成的 artifact 审计科学主张', 'Audit claims against citations and generated artifacts')}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4 pt-6">
              <div className="space-y-2">
                <label className="flex items-center gap-2 text-sm font-semibold text-slate-700">
                  <span className="flex h-5 w-5 items-center justify-center rounded-full bg-slate-900 text-[11px] text-white">1</span>
                  {text('选择论文', 'Select Paper')}
                </label>
                {papersLoading ? (
                  <Skeleton className="h-10 w-full" />
                ) : (
                  <select
                    className="w-full min-w-0 max-w-full overflow-hidden text-ellipsis whitespace-nowrap rounded-md border-2 border-slate-200 bg-white px-4 py-2.5 text-sm font-medium hover:border-teal-500 focus:border-teal-500 focus:ring-2 focus:ring-teal-500/20 transition-colors"
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
                <label className="flex items-center gap-2 text-sm font-semibold text-slate-700">
                  <span className="flex h-5 w-5 items-center justify-center rounded-full bg-slate-900 text-[11px] text-white">2</span>
                  {text('审计模式', 'Review Mode')}
                </label>
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
              <div className="flex items-center gap-2 text-sm font-semibold text-slate-700">
                <span className="flex h-5 w-5 items-center justify-center rounded-full bg-slate-900 text-[11px] text-white">3</span>
                {text('加载存档或开始新审计', 'Load a saved result or start a new audit')}
              </div>
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
                  {!runConsistencyCheck.isPending && <ScanSearch className="mr-2 h-4 w-4" />}
                  {runConsistencyCheck.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  {runConsistencyCheck.isPending ? text('ReviewX 正在运行...', 'Running ReviewX...') : text('运行新 ReviewX', 'Run New ReviewX')}
                </Button>
              </div>
              {runConsistencyCheck.isError && (
                <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800" role="alert">
                  <div className="font-semibold">{text('ReviewX 运行失败', 'ReviewX failed')}</div>
                  <div className="mt-1 break-words text-xs leading-5">
                    {runConsistencyCheck.error instanceof Error ? runConsistencyCheck.error.message : text('未返回具体原因。', 'No detailed error was returned.')}
                  </div>
                  <div className="mt-1 text-xs leading-5">
                    {text('请先确认论文正文已生成、Settings 中模型可用；若仅视觉审计失败，可关闭该选项后重试并保留本地审计结果。', 'Confirm that the paper body exists and the model in Settings is available. If only visual audit fails, disable it and retry; local audit results remain available.')}
                  </div>
                </div>
              )}
              {historyLoadError && (
                <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-950" role="alert">
                  <div className="font-semibold">{text('保存的审计记录已不可用', 'Saved audit record unavailable')}</div>
                  <div className="mt-1 text-xs leading-5">
                    {text(
                      '当前论文选择已保留。请刷新历史记录，或点击“加载最新结果”继续。',
                      'The current paper selection was kept. Refresh history or choose Load Latest to continue.',
                    )}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

          {selectedPaperId && (
            <Card className="order-2 min-w-0 shadow-md xl:order-none">
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
                              {formatDateTime(item.updatedAt || item.createdAt, locale)}
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
                          <Badge variant="outline">{severityText(item.severityCounts, locale)}</Badge>
                          <Badge variant="outline">
                            {item.claimCount || 0} {text('主张', 'claims')} · {item.evidenceCount || 0} {text('证据', 'evidence')}
                          </Badge>
                          {item.llmCallCount !== undefined && (
                            <Badge variant="outline">LLM {text('调用', 'calls')}: {item.llmCallCount}</Badge>
                          )}
                          {item.visualAuditEnabled && (
                            <Badge variant="outline">
                              {text('视觉审计', 'Visual')}: {item.visualAuditStatus || text('已启用', 'enabled')}
                            </Badge>
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
            <Card className="order-4 min-w-0 overflow-hidden shadow-md xl:order-none">
              <CardHeader className="bg-gradient-to-r from-slate-50 to-white border-b">
                <CardTitle className="text-xl flex items-center gap-2">
                  <GitBranch className="h-5 w-5 text-teal-600" />
                  {text('证据失配图', 'Evidence Mismatch Graph')}
                  <span className="text-sm font-medium text-slate-500">CEM</span>
                  <Badge variant="outline" className="ml-auto text-xs">{text('实验 Metric', 'Experiment Metrics')}</Badge>
                </CardTitle>
                <CardDescription>{text('主张与证据的失配风险、来源节点与可追溯连接', 'Claim-evidence risk, source nodes, and traceable links')}</CardDescription>
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
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                      <div className="rounded-md border border-slate-200 bg-white p-3">
                        <div className="text-lg font-bold text-slate-900">
                          {formatMetricValue(mismatchAggregate.meanMismatch)}
                        </div>
                        <div className="text-xs text-muted-foreground">{text('失配均值', 'Mean mismatch')}</div>
                      </div>
                      <div className="rounded-md border border-slate-200 bg-white p-3">
                        <div className="text-lg font-bold text-slate-900">
                          {formatMetricValue(mismatchAggregate.maxMismatch)}
                        </div>
                        <div className="text-xs text-muted-foreground">{text('最大失配', 'Max mismatch')}</div>
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
                    </div>

                    {mismatchDimensions.length > 0 && (
                      <div className="space-y-2">
                        <div className="text-xs font-semibold text-slate-600">
                          {text('主要风险维度', 'Highest-risk dimensions')}
                        </div>
                        <div className="flex flex-wrap gap-2 text-xs">
                          {mismatchDimensions.slice(0, 5).map(([name, value]) => {
                            const label = mismatchDimensionLabels[name]
                            return (
                              <Badge key={name} variant="outline" title={name}>
                                {label ? text(label[0], label[1]) : name}: {formatMetricValue(value)}
                              </Badge>
                            )
                          })}
                        </div>
                        {mismatchDimensions.length > 5 && (
                          <details className="text-xs text-slate-600">
                            <summary className="w-fit cursor-pointer font-medium text-teal-700 hover:text-teal-800">
                              {text(`查看其余 ${mismatchDimensions.length - 5} 个维度`, `Show ${mismatchDimensions.length - 5} more dimensions`)}
                            </summary>
                            <div className="mt-2 flex flex-wrap gap-2">
                              {mismatchDimensions.slice(5).map(([name, value]) => {
                                const label = mismatchDimensionLabels[name]
                                return (
                                  <Badge key={name} variant="outline" title={name}>
                                    {label ? text(label[0], label[1]) : name}: {formatMetricValue(value)}
                                  </Badge>
                                )
                              })}
                            </div>
                          </details>
                        )}
                      </div>
                    )}

                    {runDetail.mismatchReport?.method?.formula && (
                      <details className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-700">
                        <summary className="cursor-pointer font-semibold text-slate-700">
                          {text('CEM 计算定义', 'CEM calculation')}
                        </summary>
                        <code className="mt-2 block whitespace-pre-wrap break-words font-mono leading-relaxed [overflow-wrap:anywhere]">
                          {runDetail.mismatchReport.method.formula.replace(/,/g, ',\u200b')}
                        </code>
                      </details>
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
            <Card className="order-4 min-w-0 overflow-hidden shadow-md xl:order-none">
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
                      <div className="grid grid-cols-[minmax(0,1fr)_44px_44px_52px] gap-1.5 text-[11px] font-semibold text-slate-600 sm:grid-cols-[minmax(0,1fr)_64px_64px_64px] sm:gap-2 sm:text-xs">
                        <span className="min-w-0">Metric</span>
                        <span className="text-right">{text('修订前', 'Before')}</span>
                        <span className="text-right">{text('修订后', 'After')}</span>
                        <span className="text-right">Δ</span>
                      </div>
                      <div className="mt-2 space-y-2">
                        {comparisonRows.map((row) => (
                          <div key={row.key} className="grid grid-cols-[minmax(0,1fr)_44px_44px_52px] items-center gap-1.5 text-[11px] sm:grid-cols-[minmax(0,1fr)_64px_64px_64px] sm:gap-2 sm:text-sm">
                            <span className="min-w-0 break-words text-slate-700">{row.label}</span>
                            <span className="text-right text-muted-foreground">
                              {formatMetricValue(comparison.before[row.key as keyof ReviewXComparisonMetrics] as number | undefined, row.percent)}
                            </span>
                            <span className="text-right font-medium text-slate-900">
                              {formatMetricValue(comparison.after[row.key as keyof ReviewXComparisonMetrics] as number | undefined, row.percent)}
                            </span>
                            <span className="text-right">
                              <Badge className="max-w-full px-1 text-[10px] sm:px-2 sm:text-xs" variant={deltaTone(comparison.delta[row.key], row.lowerIsBetter)}>
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
            <Card className="order-4 min-w-0 overflow-hidden shadow-md xl:order-none">
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
                  <div className="max-h-[720px] overflow-x-hidden overflow-y-auto pr-1">
                    {rootRiskNodes.map((node) => renderRiskNode(node))}
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          {selectedPaperId && (
            <Card className="order-4 min-w-0 overflow-hidden shadow-md xl:order-none">
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
            <Card className="order-4 min-w-0 overflow-hidden shadow-md xl:order-none">
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
                        <Badge variant="secondary" className="max-w-full whitespace-normal break-all">Policy: {runDetail.modelTrace.llmRouting.budgetPolicy}</Badge>
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
                      <div className="min-w-0 break-words rounded-md border border-slate-200 bg-slate-50 p-3 text-xs leading-relaxed text-slate-700 [overflow-wrap:anywhere]">
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
                          <div key={`${call.task || 'call'}-${index}`} className="min-w-0 rounded-md border border-slate-200 bg-white p-3">
                            <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
                              <div className="min-w-0 break-all text-sm font-semibold text-slate-900">{call.task || `LLM call ${index + 1}`}</div>
                              <Badge variant="outline" className="max-w-full whitespace-normal break-all">{call.model || 'model'}</Badge>
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

        <div className="contents xl:block xl:min-w-0 xl:space-y-6">
          {selectedPaperId && findings && (
            <section className="order-3 border-y border-slate-200 bg-slate-50 px-4 py-4 sm:px-5 xl:order-none" aria-labelledby="reviewx-audit-summary">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h2 id="reviewx-audit-summary" className="text-sm font-bold text-slate-950">
                    {text('本次审计概览', 'Audit summary')}
                  </h2>
                  <p className="mt-0.5 text-xs text-slate-600">
                    {text('评分表示论文当前可接受性，不是 ReviewX 的结果置信度。', 'The score rates the manuscript, not confidence in the ReviewX audit.')}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Badge variant={auditTraceAvailable ? 'secondary' : 'outline'}>
                    {auditTraceAvailable ? text('审计轨迹可查', 'Audit trace available') : text('轨迹不完整', 'Partial trace')}
                  </Badge>
                  <Badge variant={hasLlmCalls ? 'secondary' : 'outline'}>
                    {hasLlmCalls ? text('Qwen 增强', 'Qwen refined') : text('本地规则', 'Local rules')}
                  </Badge>
                  {visualTrace?.enabled && (
                    <Badge variant={visualTrace.status === 'completed' ? 'secondary' : 'outline'}>
                      {text('视觉审计', 'Visual audit')}: {visualTrace.status || 'unknown'}
                    </Badge>
                  )}
                </div>
              </div>
              <div className="mt-4 grid grid-cols-2 divide-x divide-y divide-slate-200 border border-slate-200 bg-white sm:grid-cols-3 sm:divide-y-0 xl:grid-cols-6">
                <div className="min-w-0 px-3 py-3">
                  <div className="text-xl font-bold text-slate-950">{auditScore ?? '-'}<span className="ml-1 text-xs font-medium text-slate-500">/10</span></div>
                  <div className="text-xs text-slate-600">{text('论文建议评分', 'Paper score')}</div>
                </div>
                <div className="min-w-0 px-3 py-3">
                  <div className="text-xl font-bold text-slate-950">{findings.length}</div>
                  <div className="text-xs text-slate-600">Findings</div>
                </div>
                <div className="min-w-0 px-3 py-3">
                  <div className="text-xl font-bold text-red-600">{severityCounts.blocker || 0}</div>
                  <div className="text-xs text-slate-600">{text('阻断问题', 'Blockers')}</div>
                </div>
                <div className="min-w-0 px-3 py-3">
                  <div className="text-xl font-bold text-orange-600">{severityCounts.major || 0}</div>
                  <div className="text-xs text-slate-600">{text('主要问题', 'Major issues')}</div>
                </div>
                <div className="min-w-0 px-3 py-3">
                  <div className="text-xl font-bold text-slate-950">{auditClaimCount}</div>
                  <div className="text-xs text-slate-600">{text('已审计主张', 'Audited claims')}</div>
                </div>
                <div className="min-w-0 px-3 py-3">
                  <div className="text-xl font-bold text-slate-950">{auditEvidenceCount}<span className="mx-1 text-sm text-slate-400">/</span>{auditVerificationCount}</div>
                  <div className="text-xs text-slate-600">{text('证据 / 核验', 'Evidence / checks')}</div>
                </div>
              </div>
              <p className="mt-3 text-xs leading-5 text-slate-600">
                {text(
                  `可信依据：逐条关联 ${auditClaimCount} 条主张、${auditEvidenceCount} 条证据与 ${auditVerificationCount} 项核验；可在下方 finding 中查看原文定位、证据来源和修改建议。`,
                  `Trust basis: ${auditClaimCount} claims are linked to ${auditEvidenceCount} evidence items and ${auditVerificationCount} checks; inspect each finding below for source location, provenance, and a suggested fix.`,
                )}
              </p>
            </section>
          )}

          {selectedPaperId ? (
            <Card className="order-3 min-w-0 overflow-hidden shadow-md xl:order-none">
          <CardHeader className="bg-gradient-to-r from-slate-50 to-white border-b">
            <div className="flex flex-col items-start gap-3 sm:flex-row sm:items-center sm:justify-between">
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
            <div className="flex min-w-0 flex-col gap-3 sm:flex-row">
              <select
                className="rounded-md border-2 border-slate-200 bg-white px-4 py-2 text-sm font-medium hover:border-teal-500 focus:border-teal-500 focus:ring-2 focus:ring-teal-500/20 transition-colors"
                value={severityFilter}
                onChange={(e) => setSeverityFilter(e.target.value)}
              >
                <option value="all">{text('全部级别', 'All Severities')}</option>
                <option value="blocker">{text('阻断问题 (Blocker)', 'Blocker')}</option>
                <option value="major">{text('主要问题 (Major)', 'Major')}</option>
                <option value="minor">{text('次要问题 (Minor)', 'Minor')}</option>
                <option value="info">{text('提示 (Info)', 'Info')}</option>
              </select>
              <input
                type="search"
                placeholder={text('搜索 finding...', 'Search findings...')}
                className="min-w-0 flex-1 rounded-md border-2 border-slate-200 bg-white px-4 py-2 text-sm hover:border-teal-500 focus:border-teal-500 focus:ring-2 focus:ring-teal-500/20 transition-colors"
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
                {(findings?.length || 0) > 0
                  ? text('当前筛选条件下没有 finding。', 'No findings match the current filters.')
                  : (runDetail?.jsonReport?.summary?.claimCount ?? runDetail?.claims?.length ?? 0) === 0
                    ? text(
                        '未提取到可审计主张，不能据此判断论文通过。请确认论文正文完整，并用完整句明确陈述方法与结果后重新审计。',
                        'No auditable claims were extracted, so this is not a pass. Check that the full manuscript is present, state method and result claims explicitly, then rerun the audit.',
                      )
                    : text(
                        `已审计 ${runDetail?.jsonReport?.summary?.claimCount ?? runDetail?.claims?.length ?? 0} 条主张，未发现证据矛盾。`,
                        `No evidence conflicts were found across ${runDetail?.jsonReport?.summary?.claimCount ?? runDetail?.claims?.length ?? 0} audited claims.`,
                      )}
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
                        <h3 className="text-lg font-bold text-slate-900">
                          {text(
                            severityLabels[severity as keyof typeof severityLabels][0],
                            severityLabels[severity as keyof typeof severityLabels][1],
                          )}
                        </h3>
                        <Badge variant="outline" className="ml-auto">{items.length} {text('个问题', 'issues')}</Badge>
                      </div>
                      <div className="space-y-3">
                        {items.map((finding) => (
                          <article key={finding.id} className="min-w-0 overflow-hidden border border-slate-200 border-l-4 bg-white px-4 py-4" style={{
                            borderLeftColor: severity === 'blocker' ? '#ef4444' :
                              severity === 'major' ? '#f97316' :
                                severity === 'minor' ? '#3b82f6' : '#14b8a6'
                          }}>
                            <div className="flex min-w-0 flex-col items-start gap-2 sm:flex-row sm:justify-between">
                              <h4 className="min-w-0 flex-1 break-words text-base font-semibold leading-6 text-slate-950 [overflow-wrap:anywhere]">{finding.title}</h4>
                              <div className="flex shrink-0 flex-wrap justify-start gap-2 sm:justify-end">
                                <Badge variant={severityVariants[severity as keyof typeof severityVariants]} className="capitalize text-xs">
                                  {text(
                                    severityLabels[severity as keyof typeof severityLabels][0],
                                    severityLabels[severity as keyof typeof severityLabels][1],
                                  )}
                                </Badge>
                                <Badge variant={findingHasLlmRefinement(finding) ? 'secondary' : 'outline'} className="text-xs">
                                  {findingHasLlmRefinement(finding) ? text('LLM 增强', 'LLM Refined') : text('本地规则', 'Local Rule')}
                                </Badge>
                              </div>
                            </div>
                            <p className="mt-2 break-words text-sm leading-6 text-slate-700 [overflow-wrap:anywhere]">{finding.description}</p>

                            <details className="mt-3 text-xs text-slate-600">
                              <summary className="w-fit cursor-pointer font-semibold text-teal-700 hover:text-teal-800">
                                {text('查看证据链与技术元数据', 'Show evidence trace and metadata')}
                              </summary>
                              <div className="mt-2 flex flex-wrap gap-2">
                                {finding.riskType && (
                                  <Badge variant="outline">Risk: {finding.riskType}</Badge>
                                )}
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
                                {finding.reviewerDecision && (
                                  <Badge variant="outline">Decision: {finding.reviewerDecision}</Badge>
                                )}
                                {finding.revisionStatus && (
                                  <Badge variant="secondary">Revision: {finding.revisionStatus}</Badge>
                                )}
                              </div>
                            </details>

                            <div className="mt-4 space-y-3">
                              {finding.evidence && (
                                <div className="min-w-0 overflow-hidden bg-slate-50 border-l-2 border-slate-300 rounded-r-md">
                                  <div className="px-4 py-2 bg-slate-100 border-b border-slate-200">
                                    <span className="text-xs font-semibold text-slate-700 uppercase tracking-wide">{text('证据', 'Evidence')}</span>
                                  </div>
                                  <div className="min-w-0 p-4">
                                    <pre className="max-w-full whitespace-pre-wrap break-words text-xs font-mono leading-relaxed text-slate-700 [overflow-wrap:anywhere]">
                                      {finding.evidence}
                                    </pre>
                                  </div>
                                </div>
                              )}

                              {finding.suggestedFix && (
                                <div className="min-w-0 overflow-hidden bg-teal-50 border-l-2 border-teal-400 rounded-r-md">
                                  <div className="px-4 py-2 bg-teal-100 border-b border-teal-200">
                                    <span className="text-xs font-semibold text-teal-800 uppercase tracking-wide">{text('建议修复', 'Suggested Fix')}</span>
                                  </div>
                                  <div className="p-4">
                                    <p className="break-words text-sm leading-relaxed text-teal-900 [overflow-wrap:anywhere]">{finding.suggestedFix}</p>
                                  </div>
                                </div>
                              )}

                              {finding.relatedRunId && (
                                <div className="flex items-center gap-2 border-t border-slate-200 pt-3">
                                  <Link to={`/runs/${finding.relatedRunId}`}>
                                    <Button variant="outline" size="sm" className="hover:bg-teal-50 hover:border-teal-500">
                                      <ExternalLink className="h-3 w-3 mr-2" />
                                      {text('查看运行', 'View Run')}
                                    </Button>
                                  </Link>
                                </div>
                              )}
                            </div>
                          </article>
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
            <Card className="order-3 min-w-0 shadow-md xl:order-none">
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
