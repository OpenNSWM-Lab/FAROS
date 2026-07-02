/**
 * Code Blueprint — Project-associated experiment DAG visualization.
 *
 * Reads ?projectId= from URL, fetches blueprint from backend,
 * renders the DAG via BlueprintGraph with real data.
 */

import { useEffect, useState, useCallback } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { AppPageLayout } from '@/components/layout/AppPageLayout'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import {
  GitBranch, CheckCircle2, XCircle, Loader2, Circle, RefreshCw,
  FolderOpen, FlaskConical, AlertTriangle, ExternalLink,
} from 'lucide-react'
import { BlueprintGraph } from '@/components/code/BlueprintGraph'
import {
  getProjectBlueprint, listProjectBlueprints,
  getProject, listProjects,
  CodeProjectV2, BlueprintResponse,
} from '@/lib/api/codeProjects'

export function CodeBlueprint() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const projectId = searchParams.get('projectId') || ''

  // Project list (for selector)
  const [projects, setProjects] = useState<CodeProjectV2[]>([])
  const [selectedProject, setSelectedProject] = useState<CodeProjectV2 | null>(null)

  // Blueprint data
  const [blueprint, setBlueprint] = useState<BlueprintResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Blueprint sessions
  const [sessions, setSessions] = useState<{ id: string; title: string; source: string; nodeCount: number }[]>([])

  // Load project list
  useEffect(() => {
    listProjects({ limit: 100 }).then(resp => setProjects(resp.projects)).catch(() => {})
  }, [])

  // Load/refresh blueprint
  const loadBlueprint = useCallback(() => {
    if (!projectId) return
    setLoading(true)
    setError(null)
    getProjectBlueprint(projectId)
      .then(bp => {
        setBlueprint(bp)
        listProjectBlueprints(projectId).then(s => setSessions(s)).catch(() => {})
      })
      .catch(err => setError(err.message))
      .finally(() => setLoading(false))
  }, [projectId])

  // Load selected project and blueprint
  useEffect(() => {
    if (!projectId) { setBlueprint(null); return }
    loadBlueprint()
    getProject(projectId).then(p => setSelectedProject(p)).catch(() => {})
  }, [projectId, loadBlueprint])

  // Count node statuses
  const counts = blueprint ? {
    total: blueprint.nodes.length,
    success: blueprint.nodes.filter(n => n.status === 'success').length,
    running: blueprint.nodes.filter(n => n.status === 'running').length,
    failed: blueprint.nodes.filter(n => n.status === 'failed').length,
    pending: blueprint.nodes.filter(n => n.status === 'pending').length,
  } : null

  return (
    <AppPageLayout
      title="Experiment Blueprint"
      subtitle={selectedProject ? selectedProject.title : 'Select a project to view its experiment DAG'}
      icon={GitBranch}
      iconColor="violet"
      accentColor="violet"
    >
      {/* Code sub-navigation */}
      <div className="flex items-center gap-1 mb-4 border-b pb-2">
        {[
          { label: 'Projects', href: '/code/projects', icon: FolderOpen },
          { label: 'Workspace', href: '/code/workspace', icon: FlaskConical },
          { label: 'Blueprint', href: '/code/blueprint', icon: GitBranch },
        ].map((tab) => (
          <Button
            key={tab.href}
            variant={location.pathname === tab.href ? 'default' : 'ghost'}
            size="sm"
            onClick={() => navigate(tab.href)}
            className={cn(
              'text-sm',
              location.pathname === tab.href
                ? 'bg-violet-600 text-white hover:bg-violet-700'
                : 'text-muted-foreground hover:text-foreground'
            )}
          >
            <tab.icon className="h-4 w-4 mr-1.5" />
            {tab.label}
          </Button>
        ))}
      </div>

      {/* Project selector */}
      <div className="flex items-center gap-3 mb-4 flex-wrap">
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted-foreground">Project:</span>
          <select
            className="border rounded-lg px-3 py-1.5 text-sm min-w-[200px]"
            value={projectId}
            onChange={e => {
              const pid = e.target.value
              if (pid) {
                setSearchParams({ projectId: pid })
              } else {
                setSearchParams({})
              }
            }}
          >
            <option value="">-- Select a project --</option>
            {projects.map(p => (
              <option key={p.id} value={p.id}>{p.title}</option>
            ))}
          </select>
        </div>

        {/* Blueprint sessions */}
        {sessions.length > 0 && (
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground">Session:</span>
            <select className="border rounded-lg px-3 py-1.5 text-sm" onChange={e => {
              if (e.target.value !== 'current') {
                // TODO: load specific session
              }
            }}>
              <option value="current">Current</option>
              {sessions.map(s => (
                <option key={s.id} value={s.id}>{s.title} ({s.nodeCount} nodes)</option>
              ))}
            </select>
          </div>
        )}

        {/* Source badge */}
        {blueprint && (
          <Badge variant="outline" className="text-xs">
            Source: {blueprint.source === 'plan_package' ? 'Idea Plan' : blueprint.source === 'project_structure' ? 'Project Structure' : blueprint.source}
          </Badge>
        )}

        {/* Refresh */}
        <Button variant="ghost" size="sm" onClick={loadBlueprint} disabled={loading}>
          <RefreshCw className={`h-3.5 w-3.5 mr-1 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
      </div>

      {/* Status badges */}
      {counts && counts.total > 0 && (
        <div className="flex items-center gap-4 mb-4 flex-wrap">
          <Badge variant="outline" className="text-slate-600 text-xs">Total: {counts.total} nodes</Badge>
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
      )}

      {/* Main content */}
      {!projectId ? (
        <Card>
          <CardContent className="py-12 text-center">
            <GitBranch className="h-12 w-12 text-muted-foreground mx-auto mb-4" />
            <p className="text-muted-foreground mb-2">Select a project above to view its experiment blueprint</p>
            <p className="text-sm text-muted-foreground/60">
              The blueprint shows the experiment design as a DAG — stages, steps, and their dependencies.
            </p>
          </CardContent>
        </Card>
      ) : loading ? (
        <div className="flex items-center justify-center h-64">
          <Loader2 className="h-8 w-8 animate-spin text-violet-500" />
        </div>
      ) : error ? (
        <Card>
          <CardContent className="py-8 text-center">
            <AlertTriangle className="h-12 w-12 text-red-500 mx-auto mb-4" />
            <p className="text-muted-foreground mb-2">{error}</p>
            <Button variant="outline" onClick={() => setSearchParams({ projectId })}>Retry</Button>
          </CardContent>
        </Card>
      ) : blueprint && blueprint.nodes.length > 0 ? (
        <div
          className="relative bg-white border rounded-xl overflow-hidden shadow-sm"
          style={{ height: 'calc(100vh - 280px)' }}
        >
          <div className="absolute top-3 left-3 z-10 bg-white/90 backdrop-blur rounded-lg border px-3 py-2 text-xs flex items-center gap-3">
            <span className="flex items-center gap-1"><span className="inline-block w-2.5 h-2.5 rounded-full bg-emerald-500" /> Completed</span>
            <span className="flex items-center gap-1"><span className="inline-block w-2.5 h-2.5 rounded-full bg-blue-500 animate-pulse" /> Running</span>
            <span className="flex items-center gap-1"><span className="inline-block w-2.5 h-2.5 rounded-full bg-red-500" /> Failed</span>
            <span className="flex items-center gap-1"><span className="inline-block w-2.5 h-2.5 rounded-full bg-slate-300" /> Pending</span>
            <span className="border-l pl-3 text-muted-foreground">Scroll zoom · Drag pan · Hover details · Click step</span>
          </div>
          <BlueprintGraph
            blueprint={{
              id: blueprint.id,
              title: blueprint.projectTitle,
              description: blueprint.description,
              nodes: blueprint.nodes.map(n => ({
                id: n.id,
                label: n.label,
                stage: n.stage,
                status: n.status as 'pending' | 'running' | 'success' | 'failed',
                description: n.description,
                method: n.method,
                inputs: n.inputs,
                outputs: n.outputs,
                result: n.result ? {
                  summary: String(n.result.summary || ''),
                  metrics: (n.result.metrics || {}) as Record<string, string | number>,
                  error: n.result.error as string | undefined,
                  logs: n.result.logs as string[] | undefined,
                } : null,
                startedAt: n.startedAt,
                finishedAt: n.finishedAt,
                duration: n.duration,
              })),
              edges: blueprint.edges.map(e => ({
                id: e.id,
                source: e.source,
                target: e.target,
              })),
            }}
            height="100%"
            onNodeClick={(nodeId) => navigate(`/code/blueprint/step/${nodeId}?projectId=${projectId}`)}
            packageId={blueprint.id}
          />
        </div>
      ) : (
        <Card>
          <CardContent className="py-12 text-center">
            <GitBranch className="h-12 w-12 text-muted-foreground mx-auto mb-4" />
            <p className="text-muted-foreground mb-2">No blueprint data available for this project</p>
            <p className="text-sm text-muted-foreground/60 mb-4">
              Generate the project code first, or create a Plan Package from an Idea session.
            </p>
            <Button variant="outline" onClick={() => navigate(`/code/projects/${projectId}`)}>
              <ExternalLink className="h-4 w-4 mr-1.5" /> Open Project
            </Button>
          </CardContent>
        </Card>
      )}
    </AppPageLayout>
  )
}
