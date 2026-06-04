import { useNavigate } from 'react-router-dom'
import { AppPageLayout } from '@/components/layout/AppPageLayout'
import { Badge } from '@/components/ui/badge'
import { GitBranch, CheckCircle2, XCircle, Loader2, Circle } from 'lucide-react'
import { BlueprintGraph } from '@/components/code/BlueprintGraph'
import { mockBlueprint } from './blueprintMockData'

export function CodeBlueprint() {
  const navigate = useNavigate()

  const counts = {
    total: mockBlueprint.nodes.length,
    success: mockBlueprint.nodes.filter(n => n.status === 'success').length,
    running: mockBlueprint.nodes.filter(n => n.status === 'running').length,
    failed: mockBlueprint.nodes.filter(n => n.status === 'failed').length,
    pending: mockBlueprint.nodes.filter(n => n.status === 'pending').length,
  }

  return (
    <AppPageLayout
      title="Experiment Blueprint"
      subtitle={mockBlueprint.title}
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
        <BlueprintGraph
          height="100%"
          onNodeClick={(nodeId) => navigate(`/code/blueprint/step/${nodeId}`)}
        />
      </div>
    </AppPageLayout>
  )
}
