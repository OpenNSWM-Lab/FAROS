import { useEffect, useState } from 'react'
import { useParams, useNavigate, useSearchParams } from 'react-router-dom'
import { AppPageLayout } from '@/components/layout/AppPageLayout'
import { SectionCard } from '@/components/detail/SectionCard'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  ArrowLeft, CheckCircle2, XCircle, Loader2, Clock, Circle,
  GitBranch, FileText, BarChart3, Terminal, ExternalLink,
} from 'lucide-react'
import { mockBlueprint, type ExperimentStep } from './blueprintMockData'

const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

const statusConfig: Record<string, { label: string; icon: React.ReactNode; badgeVariant: string; badgeClass: string }> = {
  pending:  { label: '待执行', icon: <Circle className="h-4 w-4" />,         badgeVariant: 'outline', badgeClass: 'text-slate-500 border-slate-300' },
  running:  { label: '进行中', icon: <Loader2 className="h-4 w-4 animate-spin" />, badgeVariant: 'outline', badgeClass: 'text-blue-600 border-blue-300' },
  success:  { label: '已完成', icon: <CheckCircle2 className="h-4 w-4" />,  badgeVariant: 'outline', badgeClass: 'text-green-600 border-green-300' },
  failed:   { label: '失败',   icon: <XCircle className="h-4 w-4" />,        badgeVariant: 'outline', badgeClass: 'text-red-600 border-red-300' },
}

function InfoRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start py-2 border-b border-border/50 last:border-0">
      <span className="w-32 shrink-0 text-sm text-muted-foreground">{label}</span>
      <span className="text-sm text-foreground flex-1">{value}</span>
    </div>
  )
}

function MetricsTable({ metrics }: { metrics: Record<string, string | number> }) {
  return (
    <div className="border rounded-md overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-muted/50">
          <tr>
            <th className="text-left px-3 py-2 font-medium text-muted-foreground">指标</th>
            <th className="text-right px-3 py-2 font-medium text-muted-foreground">值</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(metrics).map(([k, v]) => (
            <tr key={k} className="border-t border-border/50">
              <td className="px-3 py-2 text-foreground">{k}</td>
              <td className="px-3 py-2 text-right font-mono text-foreground">{v}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function CodeStepDetail() {
  const { stepId } = useParams<{ stepId: string }>()
  const [searchParams] = useSearchParams()
  const packageId = searchParams.get('packageId')
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState('overview')
  const [node, setNode] = useState<ExperimentStep | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (packageId) {
      setLoading(true)
      setError(null)
      fetch(`${API_BASE}/api/v1/plans/packages/${encodeURIComponent(packageId)}/blueprint`)
        .then(res => {
          if (!res.ok) throw new Error(`HTTP ${res.status}`)
          return res.json()
        })
        .then(data => {
          const found = (data.nodes as ExperimentStep[]).find(n => n.id === stepId)
          if (found) {
            setNode(found)
          } else {
            setError('Step not found in blueprint')
          }
        })
        .catch(err => setError(err.message))
        .finally(() => setLoading(false))
    } else {
      const found = mockBlueprint.nodes.find(n => n.id === stepId)
      if (found) setNode(found)
    }
  }, [packageId, stepId])

  if (loading) {
    return (
      <AppPageLayout title="加载中..." icon={GitBranch} iconColor="violet" accentColor="violet">
        <div className="flex flex-col items-center justify-center py-20">
          <Loader2 className="h-12 w-12 text-violet-500 animate-spin mb-4" />
          <p className="text-muted-foreground text-sm">正在加载步骤数据...</p>
        </div>
      </AppPageLayout>
    )
  }

  if (error) {
    return (
      <AppPageLayout title="加载失败" icon={GitBranch} iconColor="violet" accentColor="violet">
        <div className="flex flex-col items-center justify-center py-20">
          <XCircle className="h-12 w-12 text-muted-foreground mb-4" />
          <h2 className="text-lg font-medium mb-2">无法加载步骤</h2>
          <p className="text-muted-foreground text-sm mb-4">{error}</p>
          <Button variant="outline" onClick={() => navigate(`/code/blueprint?packageId=${packageId}`)}>
            <ArrowLeft className="h-4 w-4 mr-2" /> 返回蓝图
          </Button>
        </div>
      </AppPageLayout>
    )
  }

  if (!node) {
    return (
      <AppPageLayout title="未找到步骤" icon={GitBranch} iconColor="violet" accentColor="violet">
        <div className="flex flex-col items-center justify-center py-20">
          <XCircle className="h-12 w-12 text-muted-foreground mb-4" />
          <h2 className="text-lg font-medium mb-2">步骤不存在</h2>
          <p className="text-muted-foreground text-sm mb-4">ID: {stepId}</p>
          <Button variant="outline" onClick={() => navigate(packageId ? `/code/blueprint?packageId=${packageId}` : '/code/blueprint')}>
            <ArrowLeft className="h-4 w-4 mr-2" /> 返回蓝图
          </Button>
        </div>
      </AppPageLayout>
    )
  }

  const cfg = statusConfig[node.status] ?? statusConfig.pending

  return (
    <AppPageLayout
      title={node.label}
      subtitle={`Stage: ${node.stage} · Experiment Blueprint`}
      icon={GitBranch}
      iconColor="violet"
      accentColor="violet"
      actions={
        <Button variant="outline" size="sm" onClick={() => navigate(packageId ? `/code/blueprint?packageId=${packageId}` : '/code/blueprint')}>
          <ArrowLeft className="h-4 w-4 mr-2" /> 返回蓝图
        </Button>
      }
    >
      <div className="flex items-center gap-3 mb-6">
        <Badge className={cfg.badgeClass + ' flex items-center gap-1'}>
          {cfg.icon} {cfg.label}
        </Badge>
        {node.startedAt && (
          <span className="text-xs text-muted-foreground flex items-center gap-1">
            <Clock className="h-3 w-3" />
            {new Date(node.startedAt).toLocaleString('zh-CN')}
          </span>
        )}
        {node.duration != null && (
          <span className="text-xs text-muted-foreground">
            耗时: {node.duration} min
          </span>
        )}
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
        <TabsList>
          <TabsTrigger value="overview" className="flex items-center gap-1">
            <FileText className="h-3.5 w-3.5" /> 概览
          </TabsTrigger>
          <TabsTrigger value="results" className="flex items-center gap-1">
            <BarChart3 className="h-3.5 w-3.5" /> 结果数据
          </TabsTrigger>
          <TabsTrigger value="logs" className="flex items-center gap-1">
            <Terminal className="h-3.5 w-3.5" /> 日志
          </TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="mt-4">
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-2 space-y-6">
              <SectionCard title="基本信息">
                <InfoRow label="阶段" value={node.stage} />
                <InfoRow label="描述" value={node.description} />
                <InfoRow label="方法" value={node.method || '—'} />
              </SectionCard>

              <SectionCard title="输入 / 输出">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <h4 className="text-sm font-medium text-muted-foreground mb-2">输入</h4>
                    <ul className="space-y-1">
                      {node.inputs.map((inp, i) => (
                        <li key={i} className="text-sm flex items-center gap-2 text-foreground">
                          <span className="inline-block w-1 h-1 rounded-full bg-blue-400" />
                          {inp}
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <h4 className="text-sm font-medium text-muted-foreground mb-2">输出</h4>
                    <ul className="space-y-1">
                      {node.outputs.map((out, i) => (
                        <li key={i} className="text-sm flex items-center gap-2 text-foreground">
                          <span className="inline-block w-1 h-1 rounded-full bg-green-400" />
                          {out}
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              </SectionCard>

              {node.result?.summary && (
                <SectionCard title="结果摘要">
                  <p className="text-sm text-foreground leading-relaxed">{node.result.summary}</p>
                </SectionCard>
              )}

              {node.status === 'failed' && node.result?.error && (
                <Card className="border-red-200 bg-red-50/50">
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm font-medium text-red-800 flex items-center gap-2">
                      <XCircle className="h-4 w-4" /> 失败原因
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    <p className="text-sm text-red-700">{node.result.error}</p>
                  </CardContent>
                </Card>
              )}
            </div>

            <div className="space-y-4">
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">状态信息</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3 text-sm">
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">状态</span>
                    <Badge className={cfg.badgeClass + ' flex items-center gap-1'}>{cfg.icon} {cfg.label}</Badge>
                  </div>
                  {node.startedAt && (
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">开始时间</span>
                      <span>{new Date(node.startedAt).toLocaleString('zh-CN')}</span>
                    </div>
                  )}
                  {node.finishedAt && (
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">完成时间</span>
                      <span>{new Date(node.finishedAt).toLocaleString('zh-CN')}</span>
                    </div>
                  )}
                  {node.duration != null && (
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">耗时</span>
                      <span>{node.duration} min</span>
                    </div>
                  )}
                </CardContent>
              </Card>

              {node.result?.artifacts && node.result.artifacts.length > 0 && (
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">产出文件</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <ul className="space-y-1">
                      {node.result.artifacts.map((a, i) => (
                        <li key={i} className="text-xs text-foreground flex items-center gap-2 py-1">
                          <FileText className="h-3 w-3 text-muted-foreground" />
                          {a}
                        </li>
                      ))}
                    </ul>
                  </CardContent>
                </Card>
              )}

              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">流程导航</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="w-full justify-start text-sm"
                    onClick={() => navigate(packageId ? `/code/blueprint?packageId=${packageId}` : '/code/blueprint')}
                  >
                    <GitBranch className="h-4 w-4 mr-2" /> 返回完整蓝图
                  </Button>
                </CardContent>
              </Card>
            </div>
          </div>
        </TabsContent>

        <TabsContent value="results" className="mt-4">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {node.result ? (
              <>
                {node.result.metrics && Object.keys(node.result.metrics).length > 0 && (
                  <SectionCard title="关键指标">
                    <MetricsTable metrics={node.result.metrics} />
                  </SectionCard>
                )}
                {node.result.artifacts && node.result.artifacts.length > 0 && (
                  <SectionCard title="产出文件">
                    <ul className="space-y-2">
                      {node.result.artifacts.map((a, i) => (
                        <li key={i} className="text-sm flex items-center gap-2 p-2 bg-muted/30 rounded">
                          <FileText className="h-4 w-4 text-violet-500" />
                          <span className="flex-1">{a}</span>
                          <Button variant="ghost" size="sm" className="h-6 text-xs">
                            <ExternalLink className="h-3 w-3" />
                          </Button>
                        </li>
                      ))}
                    </ul>
                  </SectionCard>
                )}
              </>
            ) : (
              <div className="col-span-2 flex flex-col items-center justify-center py-16 text-muted-foreground">
                <BarChart3 className="h-10 w-10 mb-3 opacity-40" />
                <p className="text-sm">该步骤尚未执行，暂无结果数据</p>
              </div>
            )}
          </div>
        </TabsContent>

        <TabsContent value="logs" className="mt-4">
          {node.result?.logs && node.result.logs.length > 0 ? (
            <Card>
              <CardContent className="pt-4">
                <div className="bg-slate-950 rounded-lg p-4 font-mono text-xs text-green-400 overflow-x-auto max-h-96 overflow-y-auto">
                  {node.result.logs.map((line, i) => (
                    <div key={i} className="leading-relaxed">
                      <span className="text-slate-500 select-none mr-2">[{i + 1}]</span>
                      {line}
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          ) : node.status === 'pending' ? (
            <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
              <Terminal className="h-10 w-10 mb-3 opacity-40" />
              <p className="text-sm">该步骤尚未执行，无日志</p>
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
              <Terminal className="h-10 w-10 mb-3 opacity-40" />
              <p className="text-sm">该步骤无详细日志记录</p>
            </div>
          )}
        </TabsContent>
      </Tabs>
    </AppPageLayout>
  )
}
