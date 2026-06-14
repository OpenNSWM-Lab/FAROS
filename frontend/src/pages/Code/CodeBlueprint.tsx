import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { AppPageLayout } from '@/components/layout/AppPageLayout'
import { Badge } from '@/components/ui/badge'
import { GitBranch, CheckCircle2, XCircle, Loader2, Circle, AlertTriangle } from 'lucide-react'
import { BlueprintGraph } from '@/components/code/BlueprintGraph'
import { mockBlueprint, type ExperimentBlueprint } from './blueprintMockData'

const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

export function CodeBlueprint() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const packageId = searchParams.get('packageId')
  const [blueprint, setBlueprint] = useState<ExperimentBlueprint | null>(null)
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
        .then(data => setBlueprint(data))
        .catch(err => setError(err.message))
        .finally(() => setLoading(false))
    }
  }, [packageId])

  const data = blueprint || mockBlueprint

  const counts = {
    total: data.nodes.length,
    success: data.nodes.filter(n => n.status === 'success').length,
    running: data.nodes.filter(n => n.status === 'running').length,
    failed: data.nodes.filter(n => n.status === 'failed').length,
    pending: data.nodes.filter(n => n.status === 'pending').length,
  }

  return (
    <AppPageLayout
      title="Experiment Blueprint"
      subtitle={loading ? 'Loading...' : data.title}
      icon={GitBranch}
      iconColor="violet"
      accentColor="violet"
    >
      <div className="flex items-center gap-4 mb-4 flex-wrap">
        <Badge variant="outline" className="text-slate-600 text-xs">Total: {counts.total} steps</Badge>
        <Badge variant="outline" className="text-green-600 border-green-300 flex items-center gap-1 text-xs">
          <CheckCircle2 className="h-3 w-3" /> {counts.success} Success
        </Badge>
        <Badge variant="outline" className="text-blue-600 border-blue-300 flex items-center gap-1 text-xs">
          <Loader2 className="h-3 w-3 animate-spin" /> {counts.running} Running
        </Badge>
        <Badge variant="outline" className="text-red-600 border-red-300 flex items-center gap-1 text-xs">
          <XCircle className="h-3 w-3" /> {counts.failed} Failed
        </Badge>
        <Badge variant="outline" className="text-slate-400 border-slate-300 flex items-center gap-1 text-xs">
          <Circle className="h-3 w-3" /> {counts.pending} Pending
        </Badge>
      </div>

      <div
        className="relative bg-white border rounded-xl overflow-hidden shadow-sm"
        style={{ height: 'calc(100vh - 210px)' }}
      >
        <div className="absolute top-3 left-3 z-10 bg-white/90 backdrop-blur rounded-lg border px-3 py-2 text-xs flex items-center gap-3">
          <span className="flex items-center gap-1"><span className="inline-block w-2.5 h-2.5 rounded-full bg-emerald-500" /> 已完成</span>
          <span className="flex items-center gap-1"><span className="inline-block w-2.5 h-2.5 rounded-full bg-blue-500 animate-pulse" /> 进行中</span>
          <span className="flex items-center gap-1"><span className="inline-block w-2.5 h-2.5 rounded-full bg-red-500" /> 失败</span>
          <span className="flex items-center gap-1"><span className="inline-block w-2.5 h-2.5 rounded-full bg-slate-300" /> 待执行</span>
          <span className="border-l pl-3 text-muted-foreground">滚轮缩放 · 拖拽平移 · 悬停查看 · 点击详情</span>
        </div>
        {loading && (
          <div className="absolute inset-0 z-20 flex items-center justify-center bg-white/80">
            <div className="text-center">
              <Loader2 className="h-8 w-8 animate-spin text-violet-500 mx-auto mb-2" />
              <p className="text-sm text-slate-500">Loading blueprint...</p>
            </div>
          </div>
        )}
        {error && (
          <div className="absolute top-3 right-3 z-20 bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-sm text-red-700 flex items-center gap-2">
            <AlertTriangle className="h-4 w-4" />
            Failed to load: {error}
          </div>
        )}
        {!loading && !error && (
          <BlueprintGraph
            blueprint={data}
            height="100%"
            packageId={packageId || data.id}
            onNodeClick={(nodeId) => navigate(`/code/blueprint/step/${nodeId}?packageId=${packageId || data.id}`)}
          />
        )}
      </div>
    </AppPageLayout>
  )
}
