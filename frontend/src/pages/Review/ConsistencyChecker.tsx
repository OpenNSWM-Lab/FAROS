import { useEffect, useState, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { AppPageLayout } from '@/components/layout/AppPageLayout'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { AlertCircle, AlertTriangle, Info, CheckCircle, ExternalLink, Shield, History } from 'lucide-react'
import { usePapers, useReviewFindings, useRunConsistencyCheck } from '@/lib/hooks/useApi'
import { API_BASE_URL } from '@/lib/api'
import type { ReviewFinding } from '@/lib/types'

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
  findingCount?: number
  severityCounts?: Record<string, number>
  llmCallCount?: number
  llmSkipped?: boolean
  llmSkipReason?: string
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

export function ConsistencyChecker() {
  const { data: papers, isLoading: papersLoading } = usePapers()
  const [selectedPaperId, setSelectedPaperId] = useState<string>('')
  const [budgetMode, setBudgetMode] = useState<string>('balanced')
  const [severityFilter, setSeverityFilter] = useState<string>('all')
  const [searchQuery, setSearchQuery] = useState('')
  const [history, setHistory] = useState<ReviewXHistoryItem[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [selectedHistoryId, setSelectedHistoryId] = useState<string>('')
  const [historyFindings, setHistoryFindings] = useState<ReviewFinding[] | null>(null)
  const [historyFindingsLoading, setHistoryFindingsLoading] = useState(false)

  const { data: latestFindings, isLoading: latestFindingsLoading } = useReviewFindings(selectedPaperId)
  const runConsistencyCheck = useRunConsistencyCheck()
  const findings = selectedHistoryId ? historyFindings : latestFindings
  const findingsLoading = selectedHistoryId ? historyFindingsLoading : latestFindingsLoading

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
    setHistoryFindingsLoading(true)
    try {
      const resp = await fetch(`${API_BASE_URL}/api/v1/reviews/reviewx/${reviewId}/findings`)
      const data = await resp.json()
      setHistoryFindings(data || [])
    } finally {
      setHistoryFindingsLoading(false)
    }
  }

  useEffect(() => {
    setSelectedHistoryId('')
    setHistoryFindings(null)
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

  return (
    <AppPageLayout
      title="Consistency Checker"
      subtitle="Validate research outputs for consistency and quality"
      icon={Shield}
      iconColor="orange"
      accentColor="orange"
      headerViz="metricCapsules"
    >
      <div className="grid grid-cols-1 xl:grid-cols-[420px_minmax(0,1fr)] gap-6 items-start">
        <div className="space-y-6">
          <Card className="shadow-md">
            <CardHeader className="bg-gradient-to-r from-slate-50 to-white border-b">
              <CardTitle className="text-xl">Run Consistency Check</CardTitle>
              <CardDescription>Check citations, references, and formatting</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4 pt-6">
              <div className="space-y-2">
                <label className="text-sm font-semibold text-slate-700">Select Paper</label>
                {papersLoading ? (
                  <Skeleton className="h-10 w-full" />
                ) : (
                  <select
                    className="w-full rounded-md border-2 border-slate-200 bg-white px-4 py-2.5 text-sm font-medium hover:border-teal-500 focus:border-teal-500 focus:ring-2 focus:ring-teal-500/20 transition-colors"
                    value={selectedPaperId}
                    onChange={(e) => setSelectedPaperId(e.target.value)}
                  >
                    <option value="">Select a paper...</option>
                    {papers?.map((paper) => (
                      <option key={paper.id} value={paper.id}>
                        {paper.title}
                      </option>
                    ))}
                  </select>
                )}
              </div>
              <div className="space-y-2">
                <label className="text-sm font-semibold text-slate-700">Review Mode</label>
                <select
                  className="w-full rounded-md border-2 border-slate-200 bg-white px-4 py-2.5 text-sm font-medium hover:border-teal-500 focus:border-teal-500 focus:ring-2 focus:ring-teal-500/20 transition-colors"
                  value={budgetMode}
                  onChange={(e) => setBudgetMode(e.target.value)}
                >
                  <option value="local_only">Local Only</option>
                  <option value="balanced">Balanced</option>
                  <option value="deep">Deep</option>
                </select>
              </div>
              <Button
                disabled={!selectedPaperId || runConsistencyCheck.isPending}
                onClick={() => {
                  if (selectedPaperId) {
                    runConsistencyCheck.mutate(
                      { paperId: selectedPaperId, budgetMode },
                      {
                        onSuccess: () => {
                          setSelectedHistoryId('')
                          setHistoryFindings(null)
                          void refreshHistory(selectedPaperId)
                        },
                      }
                    )
                  }
                }}
                className="w-full bg-teal-600 hover:bg-teal-700"
                size="lg"
              >
                {runConsistencyCheck.isPending ? 'Running ReviewX...' : 'Run Consistency Check'}
              </Button>
              {runConsistencyCheck.isError && (
                <p className="text-sm text-destructive">
                  ReviewX failed to run. Check backend logs for details.
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
                      ReviewX History
                    </CardTitle>
                    <CardDescription>Saved ReviewX runs for this paper</CardDescription>
                  </div>
                  <Button variant="outline" size="sm" onClick={() => void refreshHistory(selectedPaperId)}>
                    Refresh
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
                    }}
                  >
                    Show Latest
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
                    No ReviewX history yet. Run a consistency check to create one.
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
                          <Badge variant="outline">{item.claimCount || 0} claims</Badge>
                          <Badge variant="outline">{item.evidenceCount || 0} evidence</Badge>
                          {item.llmCallCount !== undefined && (
                            <Badge variant="outline">LLM calls: {item.llmCallCount}</Badge>
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
        </div>

        <div className="space-y-6 min-w-0">
          {selectedPaperId && findings && (
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
              <Card className="border-l-4 border-l-red-500 bg-gradient-to-br from-red-50/50 to-white">
                <CardContent className="pt-6">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-sm font-medium text-slate-600">Blockers</p>
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
                      <p className="text-sm font-medium text-slate-600">Major Issues</p>
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
                      <p className="text-sm font-medium text-slate-600">Minor Issues</p>
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
                      <p className="text-sm font-medium text-slate-600">Info</p>
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
                <CardTitle className="text-xl">Audit Results ({filteredFindings.length})</CardTitle>
                <CardDescription>Consistency check results grouped by severity</CardDescription>
              </div>
              <Badge variant="outline" className="text-sm px-3 py-1">
                {filteredFindings.length} findings
              </Badge>
            </div>
          </CardHeader>
          <CardContent className="space-y-4 pt-6">
            <div className="flex gap-3">
              <select
                className="rounded-md border-2 border-slate-200 bg-white px-4 py-2 text-sm font-medium hover:border-teal-500 focus:border-teal-500 focus:ring-2 focus:ring-teal-500/20 transition-colors"
                value={severityFilter}
                onChange={(e) => setSeverityFilter(e.target.value)}
              >
                <option value="all">All Severities</option>
                <option value="blocker">🔴 Blocker</option>
                <option value="major">🟠 Major</option>
                <option value="minor">🔵 Minor</option>
                <option value="info">✓ Info</option>
              </select>
              <input
                type="search"
                placeholder="Search findings..."
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
                No findings. Paper looks good!
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
                        <Badge variant="outline" className="ml-auto">{items.length} issues</Badge>
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
                                {finding.claimId && (
                                  <Badge variant="outline">Claim: {finding.claimId}</Badge>
                                )}
                                {finding.evidenceIds && finding.evidenceIds.length > 0 && (
                                  <Badge variant="outline">Evidence: {finding.evidenceIds.join(', ')}</Badge>
                                )}
                              </div>

                              {finding.evidence && (
                                <div className="bg-slate-50 border-l-2 border-slate-300 rounded-r-md">
                                  <div className="px-4 py-2 bg-slate-100 border-b border-slate-200">
                                    <span className="text-xs font-semibold text-slate-700 uppercase tracking-wide">Evidence</span>
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
                                    <span className="text-xs font-semibold text-teal-800 uppercase tracking-wide">Suggested Fix</span>
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
                                      View Run
                                    </Button>
                                  </Link>
                                )}
                                {finding.relatedArtifactId && (
                                  <Link to="/artifacts">
                                    <Button variant="outline" size="sm" className="hover:bg-teal-50 hover:border-teal-500">
                                      <ExternalLink className="h-3 w-3 mr-2" />
                                      View Artifact
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
              </CardContent>
            </Card>
          ) : (
            <Card className="shadow-md">
              <CardContent className="py-16 text-center text-sm text-muted-foreground">
                Select a paper to view ReviewX results and history.
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </AppPageLayout>
  )
}
