import { useEffect, useRef, useCallback, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Graph } from '@antv/g6'
import { mockBlueprint } from '@/pages/Code/blueprintMockData'

const STATUS_COLORS: Record<string, { fill: string; stroke: string; badge: string; text: string }> = {
  pending:  { fill: '#f1f5f9', stroke: '#94a3b8', badge: '#94a3b8', text: '#475569' },
  running:  { fill: '#eff6ff', stroke: '#3b82f6', badge: '#3b82f6', text: '#1e40af' },
  success:  { fill: '#ecfdf5', stroke: '#10b981', badge: '#059669', text: '#065f46' },
  failed:   { fill: '#fef2f2', stroke: '#ef4444', badge: '#dc2626', text: '#991b1b' },
}

const STATUS_LABEL: Record<string, string> = {
  pending: '待执行', running: '进行中', success: '成功', failed: '失败',
}

function InfoPanel({ data }: { data: Record<string, unknown> }) {
  const status = (data.status as string) ?? 'pending'
  const c = STATUS_COLORS[status] ?? STATUS_COLORS.pending
  const label = STATUS_LABEL[status] ?? status
  const desc = (data.description as string) ?? ''
  const method = (data.method as string) ?? ''
  const result = data.result as Record<string, unknown> | null
  const error = result?.error as string | undefined
  const metrics = result?.metrics as Record<string, string | number> | undefined

  return (
    <div className="bg-white/95 backdrop-blur rounded-xl border shadow-lg p-4 text-sm" style={{ minWidth: 280, maxWidth: 360 }}>
      <div className="flex items-center gap-2 mb-2">
        <span className="inline-block w-2.5 h-2.5 rounded-full shrink-0" style={{ background: c.badge }} />
        <span className="font-semibold text-slate-800 truncate">{data.label as string ?? ''}</span>
        <span
          className="ml-auto text-xs px-2 py-0.5 rounded-full font-medium border shrink-0"
          style={{ background: c.fill, color: c.text, borderColor: c.stroke }}
        >
          {label}
        </span>
      </div>
      <p className="text-slate-500 text-xs leading-relaxed mb-2">
        {desc.length > 120 ? desc.slice(0, 120) + '...' : desc}
      </p>
      {method && (
        <p className="text-xs text-slate-400 mb-2">
          <span className="font-medium">方法:</span> {method.length > 80 ? method.slice(0, 80) + '...' : method}
        </p>
      )}
      {metrics && Object.keys(metrics).length > 0 && (
        <div className="text-xs text-slate-500 mt-2 pt-2 border-t border-slate-100">
          {Object.entries(metrics).slice(0, 4).map(([k, v]) => (
            <div key={k} className="flex justify-between mb-1">
              <span className="text-slate-400">{k}</span>
              <span className="font-medium">{v}</span>
            </div>
          ))}
          {Object.keys(metrics).length > 4 && (
            <p className="text-slate-400 text-xs text-right">+{Object.keys(metrics).length - 4} more...</p>
          )}
        </div>
      )}
      {status === 'failed' && error && (
        <div className="mt-2 p-2 bg-red-50 rounded-md text-xs text-red-700 leading-relaxed">
          {error.slice(0, 150)}
        </div>
      )}
      <p className="mt-2 text-xs text-slate-400 text-right border-t border-slate-50 pt-2">点击节点查看详情 →</p>
    </div>
  )
}

interface BlueprintGraphProps {
  height?: number | string
  onNodeClick?: (nodeId: string) => void
}

export function BlueprintGraph({ height = '100%', onNodeClick }: BlueprintGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const graphRef = useRef<Graph | null>(null)
  const navigate = useNavigate()
  const [hoveredData, setHoveredData] = useState<Record<string, unknown> | null>(null)

  const initGraph = useCallback(() => {
    if (!containerRef.current || graphRef.current) return

    const container = containerRef.current

    const nodes = mockBlueprint.nodes.map((n) => {
      const isHeader = n.id.startsWith('stage-')
      const status = n.status ?? 'pending'
      const colors = STATUS_COLORS[status] ?? STATUS_COLORS.pending
      const displayLabel = isHeader
        ? n.label
        : n.label.length > 18 ? n.label.slice(0, 18) + '…' : n.label

      return {
        id: n.id,
        data: {
          label: n.label,
          status,
          description: n.description,
          method: n.method,
          result: n.result,
          isHeader,
        },
        style: {
          labelText: displayLabel,
          labelFill: isHeader ? '#1e293b' : '#334155',
          labelFontSize: isHeader ? 13 : 11,
          labelFontWeight: isHeader ? 600 : 500,
          labelPlacement: 'center' as const,
          labelOffsetY: 0,
          labelMaxWidth: isHeader ? 240 : 200,
          size: isHeader ? [250, 40] as [number, number] : [200, 50] as [number, number],
          radius: isHeader ? 6 : 8,
          fill: colors.fill,
          stroke: colors.stroke,
          lineWidth: isHeader ? 2.5 : status === 'running' ? 2 : 1.5,
          strokeDasharray: status === 'pending' ? [5, 3] : undefined,
          shadowBlur: status === 'running' ? 8 : undefined,
          shadowColor: status === 'running' ? '#3b82f6' : undefined,
        },
      }
    })

    const edges = mockBlueprint.edges.map((e) => {
      const targetNode = mockBlueprint.nodes.find(n => n.id === e.target)
      const targetStatus = targetNode?.status ?? 'pending'

      return {
        id: e.id,
        source: e.source,
        target: e.target,
        style: {
          stroke: targetStatus === 'running' ? '#3b82f6'
                : targetStatus === 'success' ? '#10b981'
                : '#cbd5e1',
          lineWidth: targetStatus === 'running' ? 2 : 1.2,
          endArrow: true,
          strokeDasharray: targetStatus === 'pending' ? [5, 4] : undefined,
        },
      }
    })

    const graph = new Graph({
      container,
      data: { nodes, edges },
      layout: {
        type: 'dagre',
        rankdir: 'TB',
        nodesep: 25,
        ranksep: 50,
      },
      node: {
        type: 'rect',
        style: {
          labelPlacement: 'center',
          labelFill: '#334155',
          labelFontSize: 11,
        },
      },
      edge: {
        type: 'polyline',
        style: {
          stroke: '#cbd5e1',
          lineWidth: 1.2,
          endArrow: true,
          radius: 6,
        },
      },
      animation: true,
      autoFit: 'view',
      padding: [20, 20],
      behaviors: ['drag-canvas', 'zoom-canvas'],
    })

    graph.on('node:click', (evt: any) => {
      const nodeId = evt?.target?.id
      if (nodeId) {
        if (onNodeClick) {
          onNodeClick(nodeId)
        } else {
          navigate(`/code/blueprint/step/${nodeId}`)
        }
      }
    })

    graph.on('node:pointerenter', (evt: any) => {
      const nodeId = evt?.target?.id
      if (nodeId) {
        const node = mockBlueprint.nodes.find(n => n.id === nodeId)
        if (node) {
          setHoveredData({
            label: node.label,
            status: node.status ?? 'pending',
            description: node.description ?? '',
            method: node.method ?? '',
            result: node.result ?? null,
          })
        }
      }
    })

    graph.on('node:pointerleave', () => {
      setHoveredData(null)
    })

    graph.render()
    graphRef.current = graph
  }, [navigate, onNodeClick])

  useEffect(() => {
    const timer = setTimeout(initGraph, 50)
    return () => {
      clearTimeout(timer)
      if (graphRef.current) {
        graphRef.current.destroy()
        graphRef.current = null
      }
    }
  }, [initGraph])

  return (
    <div
      ref={containerRef}
      style={{ width: '100%', height, position: 'relative' }}
      className="bg-white"
    >
      {hoveredData && (
        <div className="absolute top-3 right-3 z-20 animate-in fade-in slide-in-from-top-2 duration-150">
          <InfoPanel data={hoveredData} />
        </div>
      )}
    </div>
  )
}
