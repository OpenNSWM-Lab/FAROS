/**
 * Code Step Detail — Shows details for a specific node in the Blueprint DAG.
 *
 * Reads ?projectId= from URL, fetches real blueprint, finds the step by stepId.
 */

import { useEffect, useState } from 'react'
import { useParams, useNavigate, useSearchParams } from 'react-router-dom'
import { AppPageLayout } from '@/components/layout/AppPageLayout'
import { SectionCard } from '@/components/detail/SectionCard'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import {
  ArrowLeft, CheckCircle2, XCircle, Loader2, Circle,
  GitBranch, FileText, BarChart3, Terminal, FolderOpen, FlaskConical, AlertTriangle,
} from 'lucide-react'
import {
  getProjectBlueprint, BlueprintResponse,
} from '@/lib/api/codeProjects'

const statusConfig: Record<string, { label: string; icon: React.ReactNode; badgeClass: string }> = {
  pending:  { label: 'Pending', icon: <Circle className="h-4 w-4" />,         badgeClass: 'bg-slate-100 text-slate-700 border-slate-300' },
  running:  { label: 'Running', icon: <Loader2 className="h-4 w-4 animate-spin" />, badgeClass: 'bg-blue-50 text-blue-700 border-blue-300' },
  success:  { label: 'Success', icon: <CheckCircle2 className="h-4 w-4" />,  badgeClass: 'bg-emerald-50 text-emerald-700 border-emerald-300' },
  failed:   { label: 'Failed', icon: <XCircle className="h-4 w-4" />,        badgeClass: 'bg-red-50 text-red-700 border-red-300' },
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
            <th className="text-left px-3 py-2 font-medium text-muted-foreground">Metric</th>
            <th className="text-right px-3 py-2 font-medium text-muted-foreground">Value</th>
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
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const projectId = searchParams.get('projectId') || ''

  const [activeTab, setActiveTab] = useState('overview')
  const [blueprint, setBlueprint] = useState<BlueprintResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!projectId) return
    setLoading(true)
    setError(null)
    getProjectBlueprint(projectId)
      .then(bp => setBlueprint(bp))
      .catch(err => setError(err.message))
      .finally(() => setLoading(false))
  }, [projectId])

  const node = blueprint?.nodes.find(n => n.id === stepId) || null
  // Extract result fields with proper types
  const result = node?.result
  const resultSummary = result && typeof result === 'object' && 'summary' in result ? String((result as Record<string, unknown>).summary || '') : ''
  const resultError = result && typeof result === 'object' && 'error' in result ? String((result as Record<string, unknown>).error || '') : ''
  const resultMetrics = (result && typeof result === 'object' && 'metrics' in result && typeof (result as Record<string, unknown>).metrics === 'object') ? (result as Record<string, unknown>).metrics as Record<string, string | number> : null
  const resultLogs = (result && typeof result === 'object' && 'logs' in result && Array.isArray((result as Record<string, unknown>).logs)) ? (result as Record<string, unknown>).logs as string[] : null

  if (loading) {
    return (
      <AppPageLayout title="Loading..." icon={GitBranch} iconColor="violet" accentColor="violet">
        <div className="flex items-center justify-center h-64">
          <Loader2 className="h-8 w-8 animate-spin text-violet-500" />
        </div>
      </AppPageLayout>
    )
  }

  if (error) {
    return (
      <AppPageLayout title="Error" icon={GitBranch} iconColor="violet" accentColor="violet">
        <div className="flex flex-col items-center justify-center py-20">
          <AlertTriangle className="h-12 w-12 text-red-500 mb-4" />
          <p className="text-muted-foreground text-sm mb-4">{error}</p>
          <Button variant="outline" onClick={() => navigate('/code/blueprint')}>
            <ArrowLeft className="h-4 w-4 mr-2" /> Back to Blueprint
          </Button>
        </div>
      </AppPageLayout>
    )
  }

  if (!node) {
    return (
      <AppPageLayout title="Step Not Found" icon={GitBranch} iconColor="violet" accentColor="violet">
        <div className="flex flex-col items-center justify-center py-20">
          <XCircle className="h-12 w-12 text-muted-foreground mb-4" />
          <h2 className="text-lg font-medium mb-2">Step does not exist</h2>
          <p className="text-muted-foreground text-sm mb-1">ID: {stepId}</p>
          {projectId && <p className="text-muted-foreground text-sm mb-4">Project: {projectId}</p>}
          <Button variant="outline" onClick={() => navigate(`/code/blueprint?projectId=${projectId || ''}`)}>
            <ArrowLeft className="h-4 w-4 mr-2" /> Back to Blueprint
          </Button>
        </div>
      </AppPageLayout>
    )
  }

  const cfg = statusConfig[node.status] ?? statusConfig.pending

  return (
    <AppPageLayout
      title={node.label}
      subtitle={`Stage: ${node.stage || 'N/A'} · ${node.description || ''}`}
      icon={GitBranch}
      iconColor="violet"
      accentColor="violet"
      actions={
        <Button
          variant="ghost"
          size="sm"
          onClick={() => navigate(`/code/blueprint?projectId=${projectId || ''}`)}
          className="text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4 mr-1" /> Back to Blueprint
        </Button>
      }
    >
      {/* Code sub-navigation */}
      <div className="flex items-center gap-1 mb-4 border-b pb-2">
        {[
          { label: 'Projects', href: '/code/projects', icon: FolderOpen },
          { label: 'Workspace', href: '/code/workspace', icon: FlaskConical },
          { label: 'Blueprint', href: `/code/blueprint?projectId=${projectId || ''}`, icon: GitBranch },
        ].map((tab) => (
          <Button
            key={tab.href}
            variant={location.pathname.startsWith(tab.href.split('?')[0]) ? 'default' : 'ghost'}
            size="sm"
            onClick={() => navigate(tab.href)}
            className={cn(
              'text-sm',
              location.pathname.startsWith(tab.href.split('?')[0])
                ? 'bg-violet-600 text-white hover:bg-violet-700'
                : 'text-muted-foreground hover:text-foreground'
            )}
          >
            <tab.icon className="h-4 w-4 mr-1.5" />
            {tab.label}
          </Button>
        ))}
      </div>

      {/* Status banner */}
      <div className="flex items-center gap-3 mb-6">
        <Badge variant="outline" className={`flex items-center gap-1 ${cfg.badgeClass}`}>
          {cfg.icon} {cfg.label}
        </Badge>
        {node.startedAt && node.finishedAt && (
          <span className="text-sm text-muted-foreground">
            {node.startedAt} → {node.finishedAt}
            {node.duration != null && ` (${(node.duration / 1000).toFixed(1)}s)`}
          </span>
        )}
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="mb-4">
          <TabsTrigger value="overview" className="flex items-center gap-1.5">
            <FileText className="h-3.5 w-3.5" /> Overview
          </TabsTrigger>
          <TabsTrigger value="results" className="flex items-center gap-1.5">
            <BarChart3 className="h-3.5 w-3.5" /> Results
          </TabsTrigger>
          <TabsTrigger value="logs" className="flex items-center gap-1.5">
            <Terminal className="h-3.5 w-3.5" /> Logs
          </TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="space-y-4">
          <SectionCard title="Basic Info" icon={FileText}>
            <InfoRow label="Node ID" value={<code className="text-xs bg-muted px-1.5 py-0.5 rounded">{node.id}</code>} />
            <InfoRow label="Stage" value={node.stage || 'N/A'} />
            <InfoRow label="Label" value={node.label} />
            <InfoRow label="Status" value={<Badge variant="outline" className={`${cfg.badgeClass}`}>{cfg.icon}<span className="ml-1">{cfg.label}</span></Badge>} />
            {node.description && <InfoRow label="Description" value={node.description} />}
            {node.method && <InfoRow label="Method" value={node.method} />}
          </SectionCard>

          {(node.inputs.length > 0 || node.outputs.length > 0) && (
            <SectionCard title="Inputs & Outputs" icon={GitBranch}>
              {node.inputs.length > 0 && <InfoRow label="Inputs" value={node.inputs.join(', ')} />}
              {node.outputs.length > 0 && <InfoRow label="Outputs" value={node.outputs.join(', ')} />}
            </SectionCard>
          )}

          {result && (
            <SectionCard title="Result Summary" icon={CheckCircle2}>
              {resultSummary && <InfoRow label="Summary" value={resultSummary} />}
              {resultError && (
                <div className="p-3 bg-red-50 border border-red-200 rounded-md mt-2">
                  <p className="text-sm font-medium text-red-800 mb-1">Error</p>
                  <pre className="text-xs text-red-700 whitespace-pre-wrap">{resultError}</pre>
                </div>
              )}
            </SectionCard>
          )}
        </TabsContent>

        <TabsContent value="results">
          {resultMetrics && Object.keys(resultMetrics).length > 0 ? (
            <SectionCard title="Metrics" icon={BarChart3}>
              <MetricsTable metrics={resultMetrics as Record<string, string | number>} />
            </SectionCard>
          ) : (
            <div className="text-center py-12 text-sm text-muted-foreground">
              No metrics available yet — run the experiment to see results.
            </div>
          )}
        </TabsContent>

        <TabsContent value="logs">
          {resultLogs && resultLogs.length > 0 ? (
            <SectionCard title="Execution Logs" icon={Terminal}>
              <pre className="bg-slate-900 text-green-400 text-xs font-mono p-3 rounded-md max-h-96 overflow-auto whitespace-pre-wrap">
                {resultLogs.join('\n')}
              </pre>
            </SectionCard>
          ) : (
            <div className="text-center py-12 text-sm text-muted-foreground">
              No logs yet — execution logs will appear here after the experiment runs.
            </div>
          )}
        </TabsContent>
      </Tabs>
    </AppPageLayout>
  )
}
