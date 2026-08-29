import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { AppPageLayout } from '@/components/layout/AppPageLayout'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { PlayCircle, ExternalLink, Code2, BarChart3, Clock, CheckCircle2, XCircle, Loader2, RefreshCw } from 'lucide-react'
import { useReviewLocale } from '@/lib/reviewLocale'

const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

interface RunRecord {
  id: string
  status: string
  createdAt: string
  startedAt?: string
  completedAt?: string
  duration?: number
  planRef?: { linkId?: string; candidateNumber?: number; planSessionId?: string }
  projectId?: string
  experimentId?: string
  entrypoint?: string
  notes?: string
  type?: string
  config?: Record<string, unknown>
}

const statusColors: Record<string, string> = {
  completed: 'bg-green-100 text-green-800',
  running: 'bg-blue-100 text-blue-800',
  failed: 'bg-red-100 text-red-800',
  pending: 'bg-gray-100 text-gray-800',
  cancelled: 'bg-yellow-100 text-yellow-800',
}

export function RunsList() {
  const navigate = useNavigate()
  const { text } = useReviewLocale()
  const [runs, setRuns] = useState<RunRecord[]>([])
  const [sessions, setSessions] = useState<RunRecord[]>([])
  const [loading, setLoading] = useState(true)

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const [runsResp, sessionsResp] = await Promise.all([
        fetch(`${API_BASE}/api/v1/runs`).then(r => r.ok ? r.json() : { runs: [] }),
        fetch(`${API_BASE}/api/v1/codegen/sessions`).then(r => r.ok ? r.json() : { sessions: [] }),
      ])
      setRuns(runsResp.runs || [])
      const sess = (sessionsResp.sessions || []).map((s: Record<string, unknown>) => ({
        id: s.id as string,
        status: s.status as string,
        createdAt: s.createdAt as string,
        startedAt: s.startedAt as string | undefined,
        completedAt: s.completedAt as string | undefined,
        projectId: s.projectId as string | undefined,
        planRef: s.planLinkId ? { linkId: s.planLinkId as string } : undefined,
        entrypoint: 'codegen',
        notes: `${(s.steps as unknown[])?.length || 0} steps, ${(s.memory as Record<string, unknown>)?.generatedFileCount || 0} files`,
      }))
      setSessions(sess)
    } catch (err) {
      console.error('Failed to fetch runs:', err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchData() }, [fetchData])

  const allRuns: RunRecord[] = [...sessions, ...runs].sort(
    (a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime()
  )
  const latestRun = allRuns[0]
  const completedCount = allRuns.filter(r => r.status === 'completed').length
  const failedCount = allRuns.filter(r => r.status === 'failed').length
  const runningCount = allRuns.filter(r => r.status === 'running').length

  return (
    <AppPageLayout
      title="Runs"
      subtitle={text('科研执行与实验工程运行记录', 'Research executions and experiment project runs')}
      icon={PlayCircle}
      iconColor="teal"
      accentColor="teal"
      headerViz="sparkline"
      actions={
        <Button variant="outline" size="sm" onClick={fetchData} disabled={loading}>
          <RefreshCw className={`h-4 w-4 mr-2 ${loading ? 'animate-spin' : ''}`} /> {text('刷新', 'Refresh')}
        </Button>
      }
    >
      {/* Metrics strip */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <Card><CardContent className="pt-4 pb-3 text-center">
          <div className="text-2xl font-bold">{allRuns.length}</div>
          <div className="text-xs text-muted-foreground">{text('运行总数', 'Total runs')}</div>
        </CardContent></Card>
        <Card><CardContent className="pt-4 pb-3 text-center">
          <div className="flex items-center justify-center gap-1 text-2xl font-bold text-blue-600"><Loader2 className="h-4 w-4" />{runningCount}</div>
          <div className="text-xs text-muted-foreground">{text('运行中', 'Running')}</div>
        </CardContent></Card>
        <Card><CardContent className="pt-4 pb-3 text-center">
          <div className="flex items-center justify-center gap-1 text-2xl font-bold text-green-600"><CheckCircle2 className="h-4 w-4" />{completedCount}</div>
          <div className="text-xs text-muted-foreground">{text('已完成', 'Completed')}</div>
        </CardContent></Card>
        <Card><CardContent className="pt-4 pb-3 text-center">
          <div className="flex items-center justify-center gap-1 text-2xl font-bold text-red-600"><XCircle className="h-4 w-4" />{failedCount}</div>
          <div className="text-xs text-muted-foreground">{text('失败', 'Failed')}</div>
        </CardContent></Card>
      </div>

      {/* Latest Run card */}
      {latestRun && (
        <Card className="mb-6 border-teal-200">
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-base">
              <Clock className="h-4 w-4 text-teal-500" /> {text('最近运行', 'Latest run')}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-center justify-between">
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-sm">{latestRun.id}</span>
                  <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${statusColors[latestRun.status] || 'bg-gray-100'}`}>{latestRun.status}</span>
                </div>
                <div className="text-xs text-muted-foreground">
                  {text('创建于', 'Created')}: {new Date(latestRun.createdAt).toLocaleString()}
                  {latestRun.notes && <span className="ml-2">| {latestRun.notes}</span>}
                </div>
              </div>
              <div className="flex items-center gap-2">
                {latestRun.projectId && (
                  <Button variant="outline" size="sm" onClick={() => navigate(`/code/projects/${latestRun.projectId}`)}>
                    <Code2 className="h-3 w-3 mr-1" /> {text('项目', 'Project')}
                  </Button>
                )}
                {latestRun.experimentId && (
                  <Button variant="outline" size="sm" onClick={() => navigate(`/experiments/${latestRun.experimentId}`)}>
                    <BarChart3 className="h-3 w-3 mr-1" /> {text('实验', 'Experiment')}
                  </Button>
                )}
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* All runs table */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">{text('全部 Runs 与执行会话', 'All runs and execution sessions')} ({allRuns.length})</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="flex items-center justify-center py-12 text-muted-foreground">
              <Loader2 className="h-5 w-5 animate-spin mr-2" /> {text('正在加载运行记录...', 'Loading runs...')}
            </div>
          ) : allRuns.length === 0 ? (
            <div className="text-center py-12 text-muted-foreground">
              <p className="mb-2">{text('暂无运行记录。', 'No runs yet.')}</p>
              <Button variant="outline" size="sm" onClick={() => navigate('/research/pipeline')}>
                {text('开始科研流程', 'Start research pipeline')}
              </Button>
            </div>
          ) : (
            <div className="rounded-md border overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-muted/50 border-b">
                  <tr>
                    <th className="px-3 py-2 text-left font-medium">Run ID</th>
                    <th className="px-3 py-2 text-left font-medium">Status</th>
                    <th className="px-3 py-2 text-left font-medium">Created</th>
                    <th className="px-3 py-2 text-left font-medium">Plan Ref</th>
                    <th className="px-3 py-2 text-left font-medium">Project</th>
                    <th className="px-3 py-2 text-left font-medium">Info</th>
                    <th className="px-3 py-2 text-left font-medium">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {allRuns.map(run => (
                    <tr key={run.id} className="border-b hover:bg-muted/30 transition-colors">
                      <td className="px-3 py-2 font-mono text-xs">{run.id}</td>
                      <td className="px-3 py-2">
                        <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${statusColors[run.status] || 'bg-gray-100'}`}>{run.status}</span>
                      </td>
                      <td className="px-3 py-2 text-xs text-muted-foreground">{new Date(run.createdAt).toLocaleString()}</td>
                      <td className="px-3 py-2 text-xs">{run.planRef?.linkId ? <Badge variant="outline" className="text-xs">{run.planRef.linkId.slice(0, 12)}</Badge> : '—'}</td>
                      <td className="px-3 py-2 text-xs">
                        {run.projectId ? (
                          <Button variant="ghost" size="sm" className="h-6 px-1 text-xs" onClick={() => navigate(`/code/projects/${run.projectId}`)}>
                            <Code2 className="h-3 w-3 mr-1" />{run.projectId.slice(0, 12)}
                          </Button>
                        ) : '—'}
                      </td>
                      <td className="px-3 py-2 text-xs text-muted-foreground">{run.notes || run.entrypoint || '—'}</td>
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-1">
                          {run.projectId && (
                            <Button variant="ghost" size="sm" className="h-6 px-1" onClick={() => navigate(`/code/projects/${run.projectId}`)}>
                              <ExternalLink className="h-3 w-3" />
                            </Button>
                          )}
                          {run.experimentId && (
                            <Button variant="ghost" size="sm" className="h-6 px-1" onClick={() => navigate(`/experiments/${run.experimentId}`)}>
                              <BarChart3 className="h-3 w-3" />
                            </Button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </AppPageLayout>
  )
}
