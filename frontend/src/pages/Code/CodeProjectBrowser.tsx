/**
 * Code Project Browser — file tree + viewer + search + export + run.
 */

import { useState, useEffect, useRef, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { AppPageLayout } from '@/components/layout/AppPageLayout'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import {
  Code2, ArrowLeft, FolderOpen, FileCode, File, Download,
  Search, ExternalLink, Copy, Loader2, AlertTriangle,
  ChevronRight, FolderClosed, Archive, Play, Square, Terminal,
  CheckCircle2, XCircle, Clock, RefreshCw, ChevronDown, ChevronUp,
  SkipForward, Circle, Trash2, Brain, GitBranch
} from 'lucide-react'
import {
  getProject, getTree, getFileContent, searchProject,
  exportProject, getVSCodeLink, getFileDownloadUrl, getExportDownloadUrl,
  runProjectPipeline, getPipelineResults, deleteJob,
  CodeProjectV2, TreeEntry, SearchResult, PipelineStepResult,
} from '@/lib/api/codeProjects'
import {
  streamClaudeAgent, streamCartRun,
  ClaudeStreamEvent, CartProgressEvent,
} from '@/lib/api/codeAgent'

// Language to simple syntax highlight class
const LANG_COLORS: Record<string, string> = {
  python: 'text-blue-600',
  javascript: 'text-yellow-600',
  typescript: 'text-blue-500',
  json: 'text-green-600',
  markdown: 'text-gray-700',
  yaml: 'text-purple-600',
  html: 'text-orange-600',
  css: 'text-pink-600',
  bash: 'text-green-700',
  dockerfile: 'text-cyan-600',
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function CodeProjectBrowser() {
  const { projectId } = useParams<{ projectId: string }>()
  const navigate = useNavigate()

  // Project data
  const [project, setProject] = useState<CodeProjectV2 | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Tree state
  const [currentPath, setCurrentPath] = useState('')
  const [treeEntries, setTreeEntries] = useState<TreeEntry[]>([])
  const [treeLoading, setTreeLoading] = useState(false)

  // File viewer state
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [fileContent, setFileContent] = useState<string | null>(null)
  const [fileLanguage, setFileLanguage] = useState<string | null>(null)
  const [fileLoading, setFileLoading] = useState(false)

  // Search state
  const [searchQuery, setSearchQuery] = useState('')
  const [searchMode, setSearchMode] = useState<'path' | 'content'>('path')
  const [searchResults, setSearchResults] = useState<SearchResult[] | null>(null)
  const [searching, setSearching] = useState(false)

  // Export state
  const [exporting, setExporting] = useState(false)

  // ---- Pipeline Run state ----
  const [pipelineRunId, setPipelineRunId] = useState<string | null>(null)
  const [pipelineStatus, setPipelineStatus] = useState<'idle' | 'running' | 'succeeded' | 'failed' | 'partial'>('idle')
  const [pipelineSteps, setPipelineSteps] = useState<PipelineStepResult[]>([])
  const [pipelineSummary, setPipelineSummary] = useState('')
  const [expandedSteps, setExpandedSteps] = useState<Record<number, boolean>>({})
  const [lastRun, setLastRun] = useState<{ status: string; totalDurationMs: number; steps: PipelineStepResult[] } | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // ---- Claude Agent state ----
  const [claudeModalOpen, setClaudeModalOpen] = useState(false)
  const [claudeRunning, setClaudeRunning] = useState(false)
  const [claudeEvents, setClaudeEvents] = useState<ClaudeStreamEvent[]>([])
  const [claudeTask, setClaudeTask] = useState({ goal: '', template: 'run_experiment' as string, systemPrompt: '' })
  const [claudeAbortRef] = useState<{ current: AbortController | null }>({ current: null })
  const claudePanelRef = useRef<HTMLDivElement>(null)

  // ---- Cart Runner state ----
  const [cartRunning, setCartRunning] = useState(false)
  const [cartEvents, setCartEvents] = useState<CartProgressEvent[]>([])
  const [cartAbortRef] = useState<{ current: AbortController | null }>({ current: null })
  const cartPanelRef = useRef<HTMLDivElement>(null)
  const [expandedCartNodes, setExpandedCartNodes] = useState<Record<number, boolean>>({})

  useEffect(() => { return () => { stopPolling() } }, [])

  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
  }, [])

  // Load last pipeline results on mount
  useEffect(() => {
    if (!projectId) return
    getPipelineResults(projectId).then(resp => {
      if (resp.status !== 'idle' && resp.steps.length > 0) {
        setLastRun({ status: resp.status, totalDurationMs: resp.totalDurationMs, steps: resp.steps })
        setPipelineSteps(resp.steps)
        if (resp.jobId) setPipelineRunId(resp.jobId)
        if (resp.status === 'running') setPipelineStatus('running')
      }
    }).catch(() => {})
  }, [projectId])

  // Handle delete of current pipeline run
  const handleDeleteRun = async () => {
    if (!pipelineRunId) return
    if (!confirm('Delete this pipeline run record and all associated files?')) return
    try {
      await deleteJob(pipelineRunId)
      setLastRun(null)
      setPipelineSteps([])
      setPipelineRunId(null)
      setPipelineStatus('idle')
    } catch (e) {
      console.error('Delete failed:', e)
    }
  }

  // Run pipeline
  const handleRun = async () => {
    if (!projectId) return
    try {
      setPipelineStatus('running')
      setPipelineSteps([])
      setPipelineSummary('')
      setLastRun(null)

      const resp = await runProjectPipeline(projectId)
      setPipelineRunId(resp.jobId)
      setPipelineSteps(resp.steps)
      setPipelineSummary(resp.summary)

      // Poll for results
      stopPolling()
      pollRef.current = setInterval(async () => {
        try {
          const results = await getPipelineResults(projectId, resp.jobId)
          setPipelineSteps(results.steps)
          setPipelineSummary(results.summary)

          if (results.status === 'succeeded') {
            setPipelineStatus('succeeded')
            setLastRun({ status: 'succeeded', totalDurationMs: results.totalDurationMs, steps: results.steps })
            stopPolling()
          } else if (results.status === 'failed') {
            setPipelineStatus('failed')
            setLastRun({ status: 'failed', totalDurationMs: results.totalDurationMs, steps: results.steps })
            stopPolling()
          } else if (results.status === 'partial') {
            setPipelineStatus('partial')
            setLastRun({ status: 'partial', totalDurationMs: results.totalDurationMs, steps: results.steps })
            stopPolling()
          }
        } catch {
          // keep polling
        }
      }, 1500)
    } catch (err) {
      setPipelineStatus('failed')
      setPipelineSummary(err instanceof Error ? err.message : 'Pipeline start failed')
    }
  }

  // ---- (AI Agent handlers removed — use Claude Agent instead) ----

  // ---- Claude Agent handlers ----
  const handleClaudeStart = () => {
    if (!projectId || claudeRunning) return
    setClaudeRunning(true)
    setClaudeEvents([])

    const ctrl = streamClaudeAgent(
      {
        projectId,
        goal: claudeTask.goal || `Execute the research experiment in this project`,
        template: claudeTask.template as 'run_experiment' | 'fix_and_verify' | 'analyze_and_plot' | 'custom',
        systemPrompt: claudeTask.systemPrompt || undefined,
        timeout: 900,
        maxBudget: 10,
      },
      (event) => {
        setClaudeEvents(prev => [...prev, event])
        // Auto-scroll
        if (claudePanelRef.current) {
          claudePanelRef.current.scrollTop = claudePanelRef.current.scrollHeight
        }
      },
      (error) => {
        setClaudeRunning(false)
        if (error && error !== 'Cancelled') {
          setClaudeEvents(prev => [...prev, {
            event_type: 'error', content: error, tool_name: '', tool_input: '', tool_output: '', step: 'complete', timestamp: new Date().toLocaleTimeString()
          }])
        }
      }
    )
    claudeAbortRef.current = ctrl
  }

  const handleClaudeStop = () => {
    claudeAbortRef.current?.abort()
    setClaudeRunning(false)
  }

  const openClaudeModal = () => {
    setClaudeTask({
      goal: `Execute the research experiment: "${project?.title || 'project'}". Run all steps, collect results, generate figures, and produce a summary report.`,
      template: 'run_experiment',
      systemPrompt: '',
    })
    setClaudeModalOpen(true)
  }

  // ---- Cart Runner handler ----
  const handleCartRun = () => {
    if (!projectId || cartRunning) return
    setCartRunning(true)
    setCartEvents([])
    const ctrl = streamCartRun(
      { projectId, packageId: 'demo_ppkg_math', timeout: 900 },
      (event) => {
        setCartEvents(prev => [...prev, event])
        if (cartPanelRef.current) cartPanelRef.current.scrollTop = cartPanelRef.current.scrollHeight
      },
      (error) => {
        setCartRunning(false)
        if (error && error !== 'Cancelled') {
          setCartEvents(prev => [...prev, {
            event_type: 'cart_complete', node_id: '', status: 'failed', message: error, timestamp: new Date().toLocaleTimeString()
          }])
        }
      }
    )
    cartAbortRef.current = ctrl
  }

  const handleCartStop = () => {
    cartAbortRef.current?.abort()
    setCartRunning(false)
  }

  // ---- step helpers ----

  const toggleStepExpand = (idx: number) => {
    setExpandedSteps(prev => ({ ...prev, [idx]: !prev[idx] }))
  }

  const stepIcon = (status: string) => {
    switch (status) {
      case 'running': return <RefreshCw className="h-4 w-4 animate-spin text-blue-500" />
      case 'succeeded': return <CheckCircle2 className="h-4 w-4 text-emerald-500" />
      case 'failed': return <XCircle className="h-4 w-4 text-red-500" />
      case 'skipped': return <SkipForward className="h-4 w-4 text-slate-400" />
      default: return <Circle className="h-4 w-4 text-slate-300" />
    }
  }

  // Load project
  useEffect(() => {
    if (!projectId) return
    const load = async () => {
      try {
        setLoading(true)
        const p = await getProject(projectId)
        setProject(p)
        setError(null)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load project')
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [projectId])

  // Load tree when path changes
  useEffect(() => {
    if (!projectId) return
    const loadTree = async () => {
      try {
        setTreeLoading(true)
        const resp = await getTree(projectId, currentPath)
        setTreeEntries(resp.entries)
      } catch (err) {
        console.error('Tree load error:', err)
      } finally {
        setTreeLoading(false)
      }
    }
    loadTree()
  }, [projectId, currentPath])

  // Navigate into directory
  const handleTreeClick = async (entry: TreeEntry) => {
    if (entry.isDir) {
      setCurrentPath(entry.path)
      setSelectedFile(null)
      setFileContent(null)
      setSearchResults(null)
    } else {
      // Load file content
      if (!projectId) return
      try {
        setFileLoading(true)
        setSelectedFile(entry.path)
        const resp = await getFileContent(projectId, entry.path)
        setFileContent(resp.content)
        setFileLanguage(resp.language || null)
        setSearchResults(null)
      } catch (err) {
        setFileContent(`Error loading file: ${err instanceof Error ? err.message : 'unknown'}`)
      } finally {
        setFileLoading(false)
      }
    }
  }

  // Navigate up
  const handleNavigateUp = () => {
    if (!currentPath) return
    const parts = currentPath.split('/')
    parts.pop()
    setCurrentPath(parts.join('/'))
    setSelectedFile(null)
    setFileContent(null)
  }

  // Breadcrumb
  const breadcrumbs = currentPath ? currentPath.split('/') : []

  // Search
  const handleSearch = async () => {
    if (!projectId || !searchQuery.trim()) return
    try {
      setSearching(true)
      const resp = await searchProject(projectId, searchQuery, searchMode)
      setSearchResults(resp.results)
      setSelectedFile(null)
      setFileContent(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Search failed')
    } finally {
      setSearching(false)
    }
  }

  // Export
  const handleExport = async () => {
    if (!projectId) return
    try {
      setExporting(true)
      const resp = await exportProject(projectId)
      window.open(getExportDownloadUrl(resp.id), '_blank')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Export failed')
    } finally {
      setExporting(false)
    }
  }

  // VSCode
  const handleVSCode = async () => {
    if (!projectId) return
    try {
      const resp = await getVSCodeLink(projectId)
      window.open(resp.uri, '_blank')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'VSCode link failed')
    }
  }

  // Copy path
  const copyPath = () => {
    if (selectedFile) navigator.clipboard.writeText(selectedFile)
  }

  if (loading) {
    return (
      <AppPageLayout title="Loading..." icon={Code2} iconColor="violet" accentColor="violet">
        <div className="flex items-center justify-center h-64">
          <Loader2 className="h-8 w-8 animate-spin text-violet-500" />
        </div>
      </AppPageLayout>
    )
  }

  if (!project) {
    return (
      <AppPageLayout title="Not Found" icon={Code2} iconColor="violet" accentColor="violet">
        <Card><CardContent className="py-8 text-center">
          <AlertTriangle className="h-12 w-12 text-red-500 mx-auto mb-4" />
          <p className="text-muted-foreground mb-4">Project not found: {projectId}</p>
          <Button onClick={() => navigate('/code/projects')}><ArrowLeft className="h-4 w-4 mr-2" /> Back</Button>
        </CardContent></Card>
      </AppPageLayout>
    )
  }

  return (
    <AppPageLayout
      title={project.title}
      subtitle={project.description || undefined}
      icon={Code2}
      iconColor="violet"
      accentColor="violet"
    >
      {/* Header actions */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={() => navigate('/code/projects')}>
            <ArrowLeft className="h-4 w-4 mr-1" /> Projects
          </Button>
          {project.language && <Badge variant="secondary">{project.language}</Badge>}
          {project.framework && <Badge variant="outline">{project.framework}</Badge>}
          <span className="text-sm text-muted-foreground">{project.fileCount} files · {formatBytes(project.totalSizeBytes)}</span>
          {project.sourceIdeaSessionId && <Badge variant="outline" className="text-xs">From Idea #{project.sourceIdeaSessionId.slice(-6)}</Badge>}
        </div>
        <div className="flex items-center gap-2">
          {/* Run Cart Button */}
          {!cartRunning ? (
            <Button
              variant="outline"
              size="sm"
              onClick={handleCartRun}
              className="border-emerald-400 text-emerald-700 hover:bg-emerald-50"
              title="Run full experiment pipeline from PlanPackage"
            >
              <Play className="h-4 w-4 mr-1" /> {cartEvents.length > 0 ? 'Re-run Cart' : 'Run Cart'}
            </Button>
          ) : (
            <Button variant="outline" size="sm" onClick={handleCartStop} className="border-emerald-400 bg-emerald-50 text-emerald-700">
              <Loader2 className="h-4 w-4 mr-1 animate-spin" /> Stop Cart
            </Button>
          )}

          {/* Claude Code Agent Button */}
          {!claudeRunning ? (
            <Button
              variant="outline"
              size="sm"
              onClick={openClaudeModal}
              className="border-amber-400 text-amber-800 hover:bg-amber-50"
              title="Claude Code: autonomous research agent"
            >
              <Brain className="h-4 w-4 mr-1" /> {claudeEvents.length > 0 ? 'New Claude Task' : 'Claude Agent'}
            </Button>
          ) : (
            <Button
              variant="outline"
              size="sm"
              onClick={handleClaudeStop}
              className="border-amber-400 bg-amber-50 text-amber-800"
            >
              <Loader2 className="h-4 w-4 mr-1 animate-spin" /> Stop Claude
            </Button>
          )}

          {/* Run Pipeline Button */}
          {pipelineStatus === 'idle' ? (
            <Button variant="outline" size="sm" onClick={handleRun} className="border-emerald-300 text-emerald-700 hover:bg-emerald-50">
              <Play className="h-4 w-4 mr-1" /> {lastRun ? 'Re-run Pipeline' : 'Run Pipeline'}
            </Button>
          ) : pipelineStatus === 'running' ? (
            <Button variant="outline" size="sm" disabled className="border-yellow-300 text-yellow-700">
              <RefreshCw className="h-4 w-4 mr-1 animate-spin" /> Pipeline Running...
            </Button>
          ) : pipelineStatus === 'succeeded' ? (
            <Button variant="outline" size="sm" onClick={handleRun} className="border-emerald-300 text-emerald-700 hover:bg-emerald-50">
              <Play className="h-4 w-4 mr-1" /> Re-run Pipeline
            </Button>
          ) : pipelineStatus === 'failed' || pipelineStatus === 'partial' ? (
            <Button variant="outline" size="sm" onClick={handleRun} className="border-red-300 text-red-700 hover:bg-red-50">
              <Play className="h-4 w-4 mr-1" /> Retry Pipeline
            </Button>
          ) : null}
          <Button variant="outline" size="sm" onClick={() => navigate(`/code/blueprint?projectId=${projectId}`)}>
            <GitBranch className="h-4 w-4 mr-1" /> Blueprint
          </Button>
          <Button variant="outline" size="sm" onClick={handleVSCode}>
            <ExternalLink className="h-4 w-4 mr-1" /> Open in VSCode
          </Button>
          <Button variant="outline" size="sm" onClick={handleExport} disabled={exporting}>
            {exporting ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Archive className="h-4 w-4 mr-1" />}
            Download ZIP
          </Button>
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 mb-4 flex items-center gap-2">
          <AlertTriangle className="h-4 w-4 text-red-600" />
          <span className="text-sm text-red-900">{error}</span>
          <Button variant="ghost" size="sm" onClick={() => setError(null)} className="ml-auto">Dismiss</Button>
        </div>
      )}

      {/* Search bar */}
      <div className="flex items-center gap-2 mb-4">
        <Input
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
          placeholder="Search files..."
          className="max-w-md"
        />
        <select
          className="border rounded-md px-2 py-2 text-sm"
          value={searchMode}
          onChange={(e) => setSearchMode(e.target.value as 'path' | 'content')}
        >
          <option value="path">File name</option>
          <option value="content">Content</option>
        </select>
        <Button variant="outline" size="sm" onClick={handleSearch} disabled={searching}>
          {searching ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
        </Button>
        {searchResults !== null && (
          <Button variant="ghost" size="sm" onClick={() => setSearchResults(null)}>
            Clear results
          </Button>
        )}
      </div>

      {/* ---- Cart Pipeline Execution Panel ---- */}
      {(cartEvents.length > 0 || cartRunning) && (
        <Card className={`mb-4 border-2 ${
          cartRunning ? 'border-emerald-300' :
          cartEvents.some(e => e.status === 'failed') ? 'border-red-300' :
          'border-emerald-300'
        }`}>
          <CardHeader className="py-2 px-4 flex-row items-center justify-between">
            <div className="flex items-center gap-2">
              <Play className={`h-4 w-4 ${cartRunning ? 'text-emerald-500' : 'text-emerald-600'}`} />
              <span className="font-medium text-sm">Cart Pipeline</span>
              <Badge variant="outline" className={`text-xs ${cartRunning ? 'border-emerald-300 text-emerald-700' : ''}`}>
                {cartRunning ? <Loader2 className="h-3 w-3 mr-1 inline animate-spin" /> :
                 <CheckCircle2 className="h-3 w-3 mr-1 inline text-emerald-500" />}
                {cartRunning ? 'Running...' : 'Complete'}
              </Badge>
              {!cartRunning && cartEvents.length > 0 && (
                <span className="text-xs text-muted-foreground">
                  {cartEvents.filter(e => (e.status === 'success' || e.status === 'succeeded')).length} succeeded
                  · {cartEvents.filter(e => e.status === 'failed').length} failed
                </span>
              )}
            </div>
            <Button variant="ghost" size="sm" onClick={() => { setCartEvents([]); setCartRunning(false) }}>
              <Square className="h-3 w-3 mr-1" /> Clear
            </Button>
          </CardHeader>
          <CardContent className="p-0">
            <div ref={cartPanelRef} className="divide-y max-h-80 overflow-auto">
              {cartEvents.map((event, idx) => {
                const isStart = event.event_type === 'node_start' || event.event_type === 'cart_start'
                const isComplete = event.event_type === 'node_complete' || event.event_type === 'cart_complete'
                const isOk = event.status === 'succeeded'
                const isFail = event.status === 'failed' || event.status === 'skipped'
                return (
                  <div key={idx}>
                    <button
                      className={`w-full text-left px-4 py-2 border-l-2 flex items-start gap-3 hover:bg-muted/20 transition-colors ${
                        isStart ? 'border-l-blue-400 bg-blue-50/30' :
                        isComplete && isOk ? 'border-l-emerald-400 bg-emerald-50/30' :
                        isComplete && isFail ? 'border-l-red-400 bg-red-50/30' :
                        ''
                      }`}
                      onClick={() => isComplete && setExpandedCartNodes(prev => ({ ...prev, [idx]: !prev[idx] }))}
                    >
                      <div className="mt-0.5 flex-shrink-0">
                        {isStart ? <Play className="h-3.5 w-3.5 text-blue-500" /> :
                         isOk ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" /> :
                         isFail ? <XCircle className="h-3.5 w-3.5 text-red-500" /> :
                         <Circle className="h-3.5 w-3.5 text-slate-400" />}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-0.5">
                          <span className="text-[10px] text-muted-foreground">{event.timestamp}</span>
                          {event.node_id && (
                            <Badge variant="secondary" className="text-[10px] py-0 px-1 font-mono">{event.node_id}</Badge>
                          )}
                          <Badge variant={isFail ? 'destructive' : 'outline'} className="text-[10px] py-0 px-1">{event.status}</Badge>
                          {event.result && typeof event.result === 'object' && 'duration_ms' in event.result && (
                            <span className="text-[10px] text-muted-foreground">{((event.result as Record<string,number>).duration_ms / 1000).toFixed(1)}s</span>
                          )}
                          {isComplete && (
                            <ChevronDown className={`h-3 w-3 text-muted-foreground ml-auto transition-transform ${expandedCartNodes[idx] ? 'rotate-180' : ''}`} />
                          )}
                        </div>
                        <p className={`text-xs ${expandedCartNodes[idx] ? 'whitespace-pre-wrap' : 'truncate'} ${isFail ? 'text-red-700' : 'text-muted-foreground'}`}>
                          {event.message}
                        </p>
                        {event.result && 'artifacts' in event.result && Array.isArray((event.result as Record<string,unknown>).artifacts) && ((event.result as Record<string,unknown>).artifacts as Array<{name: string}>).length > 0 && (
                          <div className="flex items-center gap-1 mt-1 flex-wrap">
                            {((event.result as Record<string,unknown>).artifacts as Array<{name: string}>).map((a, i) => (
                              <Badge key={i} variant="secondary" className="text-[10px] py-0">{a.name}</Badge>
                            ))}
                          </div>
                        )}
                        {/* Expanded detail for completed nodes */}
                        {expandedCartNodes[idx] && isComplete && (
                          <div className="mt-2 p-2 bg-muted/50 rounded text-xs text-muted-foreground whitespace-pre-wrap max-h-60 overflow-auto">
                            {event.message || 'No details available'}
                          </div>
                        )}
                      </div>
                    </button>
                  </div>
                )
              })}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Pipeline Execution Panel */}
      {(pipelineSteps.length > 0 || lastRun) && (
        <Card className={`mb-4 border-2 ${
          pipelineStatus === 'running' ? 'border-yellow-300' :
          pipelineStatus === 'succeeded' || lastRun?.status === 'succeeded' ? 'border-emerald-300' :
          pipelineStatus === 'failed' || pipelineStatus === 'partial' || lastRun?.status === 'failed' ? 'border-red-300' :
          'border-muted'
        }`}>
          <CardHeader className="py-2 px-4 flex-row items-center justify-between">
            <div className="flex items-center gap-2">
              <Terminal className="h-4 w-4" />
              <span className="font-medium text-sm">Pipeline Execution</span>
              {(pipelineStatus !== 'idle' || lastRun) && (
                <Badge variant={
                  pipelineStatus === 'succeeded' || (lastRun?.status === 'succeeded' && pipelineStatus === 'idle') ? 'default' :
                  pipelineStatus === 'failed' || pipelineStatus === 'partial' ? 'destructive' :
                  'outline'
                } className="text-xs">
                  {pipelineStatus === 'running' ? <RefreshCw className="h-3 w-3 mr-1 inline animate-spin" /> :
                   pipelineStatus === 'succeeded' || lastRun?.status === 'succeeded' ? <CheckCircle2 className="h-3 w-3 mr-1 inline" /> :
                   pipelineStatus === 'failed' || pipelineStatus === 'partial' ? <XCircle className="h-3 w-3 mr-1 inline" /> :
                   <Clock className="h-3 w-3 mr-1 inline" />}
                  {pipelineStatus === 'running' ? 'Running' :
                   pipelineStatus === 'succeeded' ? 'All Passed' :
                   pipelineStatus === 'failed' ? 'Failed' :
                   pipelineStatus === 'partial' ? 'Partial' :
                   lastRun?.status === 'succeeded' ? 'Last: All Passed' :
                   lastRun?.status === 'failed' ? 'Last: Failed' : 'Idle'}
                </Badge>
              )}
              {lastRun && pipelineStatus === 'idle' && (
                <span className="text-xs text-muted-foreground">
                  {(lastRun.totalDurationMs / 1000).toFixed(1)}s · {lastRun.steps.length} steps
                </span>
              )}
              {pipelineStatus === 'running' && (
                <span className="text-xs text-muted-foreground">{pipelineSummary}</span>
              )}
            </div>
            <div className="flex items-center gap-1 ml-auto">
              {(lastRun || pipelineSteps.length > 0) && (
                <Button variant="ghost" size="sm" className="text-red-500 hover:text-red-700 hover:bg-red-50" onClick={handleDeleteRun}>
                  <Trash2 className="h-3 w-3 mr-1" /> Delete
                </Button>
              )}
              {pipelineStatus !== 'idle' && (
                <Button variant="ghost" size="sm" onClick={() => {
                  setPipelineStatus('idle')
                  setPipelineSteps([])
                }}>
                  <Square className="h-3 w-3 mr-1" /> Clear
                </Button>
              )}
            </div>
          </CardHeader>
          <CardContent className="p-0">
            {/* Step list */}
            <div className="divide-y">
              {(pipelineSteps.length > 0 ? pipelineSteps : lastRun?.steps || []).map((step, idx) => (
                <div key={idx} className={`${expandedSteps[idx] ? 'bg-muted/20' : ''}`}>
                  {/* Step header — always visible */}
                  <button
                    className="w-full text-left px-4 py-2.5 flex items-center gap-3 hover:bg-muted/30 transition-colors"
                    onClick={() => toggleStepExpand(idx)}
                  >
                    {stepIcon(step.status)}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className={`text-sm font-medium ${
                          step.status === 'running' ? 'text-blue-700' :
                          step.status === 'succeeded' ? 'text-emerald-700' :
                          step.status === 'failed' ? 'text-red-700' :
                          step.status === 'skipped' ? 'text-slate-400' : 'text-muted-foreground'
                        }`}>
                          <span className="text-xs text-muted-foreground mr-1">{(idx + 1).toString().padStart(2, '0')}</span>
                          {step.name}
                        </span>
                        {step.durationMs > 0 && (
                          <span className="text-xs text-muted-foreground">{(step.durationMs / 1000).toFixed(1)}s</span>
                        )}
                        {step.exitCode != null && step.exitCode !== 0 && (
                          <Badge variant="destructive" className="text-xs py-0">exit {step.exitCode}</Badge>
                        )}
                      </div>
                      <p className="text-xs text-muted-foreground mt-0.5 truncate">{step.purpose}</p>
                    </div>
                    {expandedSteps[idx] ? <ChevronUp className="h-4 w-4 text-muted-foreground" /> : <ChevronDown className="h-4 w-4 text-muted-foreground" />}
                  </button>

                  {/* Expanded detail */}
                  {expandedSteps[idx] && (
                    <div className="px-4 pb-3 space-y-2">
                      {step.error && (
                        <div className="p-2 bg-red-50 border border-red-200 rounded text-sm text-red-800">
                          <strong>Error:</strong> {step.error}
                        </div>
                      )}
                      {step.stdout && (
                        <div>
                          <div className="text-xs text-muted-foreground mb-1 font-medium">stdout</div>
                          <pre className="bg-black text-green-400 text-xs font-mono p-2 rounded max-h-40 overflow-auto whitespace-pre-wrap">
                            {step.stdout}
                          </pre>
                        </div>
                      )}
                      {step.stderr && (
                        <div>
                          <div className="text-xs text-muted-foreground mb-1 font-medium">stderr</div>
                          <pre className="bg-slate-900 text-orange-300 text-xs font-mono p-2 rounded max-h-32 overflow-auto whitespace-pre-wrap">
                            {step.stderr}
                          </pre>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>

            {/* Summary footer */}
            {lastRun && pipelineStatus === 'idle' && (
              <div className="px-4 py-2 border-t bg-muted/30 text-xs text-muted-foreground flex items-center gap-4 flex-wrap">
                <span>Steps: {lastRun.steps.filter(s => s.status === 'succeeded').length}/{lastRun.steps.length} passed</span>
                <span>Total: {(lastRun.totalDurationMs / 1000).toFixed(1)}s</span>
                {lastRun.steps.filter(s => s.status === 'failed').length > 0 && (
                  <span className="text-red-600">Failed: {lastRun.steps.filter(s => s.status === 'failed').map(s => s.name).join(', ')}</span>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* ---- Claude Agent: Task Modal ---- */}
      {claudeModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => !claudeRunning && setClaudeModalOpen(false)}>
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[90vh] overflow-auto" onClick={e => e.stopPropagation()}>
            <div className="p-6">
              <div className="flex items-center gap-3 mb-6">
                <div className="h-10 w-10 rounded-lg bg-amber-100 flex items-center justify-center">
                  <Brain className="h-5 w-5 text-amber-600" />
                </div>
                <div>
                  <h2 className="text-lg font-semibold">Claude Code Research Agent</h2>
                  <p className="text-sm text-muted-foreground">Configure and launch an autonomous research task</p>
                </div>
              </div>

              {/* Task Template */}
              <div className="mb-4">
                <label className="text-sm font-medium mb-1.5 block">Task Template</label>
                <div className="grid grid-cols-3 gap-2">
                  {[
                    { key: 'run_experiment', label: 'Run Experiment', desc: 'Execute code, collect results, generate report' },
                    { key: 'fix_and_verify', label: 'Fix & Verify', desc: 'Find and fix all bugs, verify with tests' },
                    { key: 'analyze_and_plot', label: 'Analyze & Plot', desc: 'Analyze data, generate figures' },
                  ].map(t => (
                    <button
                      key={t.key}
                      onClick={() => setClaudeTask(prev => ({ ...prev, template: t.key }))}
                      className={`p-3 rounded-lg border-2 text-left transition-colors ${
                        claudeTask.template === t.key
                          ? 'border-amber-400 bg-amber-50'
                          : 'border-muted hover:border-amber-200'
                      }`}
                    >
                      <div className="text-sm font-medium">{t.label}</div>
                      <div className="text-xs text-muted-foreground mt-0.5">{t.desc}</div>
                    </button>
                  ))}
                </div>
              </div>

              {/* Goal */}
              <div className="mb-4">
                <label className="text-sm font-medium mb-1.5 block">Research Goal</label>
                <textarea
                  className="w-full border rounded-lg p-3 text-sm min-h-[80px] resize-y"
                  value={claudeTask.goal}
                  onChange={e => setClaudeTask(prev => ({ ...prev, goal: e.target.value }))}
                  placeholder="Describe what you want Claude to accomplish..."
                />
              </div>

              {/* System Prompt (collapsible) */}
              <details className="mb-4">
                <summary className="text-sm font-medium cursor-pointer text-muted-foreground hover:text-foreground">
                  System Prompt (advanced)
                </summary>
                <textarea
                  className="w-full border rounded-lg p-3 text-xs font-mono min-h-[100px] mt-2"
                  value={claudeTask.systemPrompt}
                  onChange={e => setClaudeTask(prev => ({ ...prev, systemPrompt: e.target.value }))}
                  placeholder="Custom system prompt (leave empty to use template default)..."
                />
              </details>

              {/* Actions */}
              <div className="flex items-center gap-3 justify-end">
                <Button variant="ghost" onClick={() => setClaudeModalOpen(false)} disabled={claudeRunning}>
                  Cancel
                </Button>
                <Button
                  onClick={() => { setClaudeModalOpen(false); handleClaudeStart() }}
                  disabled={!claudeTask.goal.trim() || claudeRunning}
                  className="bg-amber-600 hover:bg-amber-700 text-white"
                >
                  <Brain className="h-4 w-4 mr-1.5" /> Launch Claude Agent
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ---- Claude Agent: Live Streaming Panel ---- */}
      {(claudeEvents.length > 0 || claudeRunning) && (
        <Card className={`mb-4 border-2 ${
          claudeRunning ? 'border-amber-300' :
          claudeEvents.some(e => e.event_type === 'error') ? 'border-red-300' :
          'border-emerald-300'
        }`}>
          <CardHeader className="py-2 px-4 flex-row items-center justify-between">
            <div className="flex items-center gap-2">
              <Brain className={`h-4 w-4 ${claudeRunning ? 'text-amber-500' : 'text-emerald-500'}`} />
              <span className="font-medium text-sm">Claude Code Agent</span>
              <Badge variant="outline" className={`text-xs ${claudeRunning ? 'border-amber-300 text-amber-700' : ''}`}>
                {claudeRunning ? <Loader2 className="h-3 w-3 mr-1 inline animate-spin" /> :
                 <CheckCircle2 className="h-3 w-3 mr-1 inline text-emerald-500" />}
                {claudeRunning ? 'Working...' : 'Complete'}
              </Badge>
              <span className="text-xs text-muted-foreground">{claudeEvents.length} events</span>
            </div>
            <Button variant="ghost" size="sm" onClick={() => { setClaudeEvents([]); setClaudeRunning(false) }}>
              <Square className="h-3 w-3 mr-1" /> Clear
            </Button>
          </CardHeader>
          <CardContent className="p-0">
            <div ref={claudePanelRef} className="divide-y max-h-96 overflow-auto">
              {claudeEvents.map((event, idx) => {
                const isThinking = event.event_type === 'thinking'
                const isTool = event.event_type === 'tool_use'
                const isResult = event.event_type === 'tool_result'
                const isError = event.event_type === 'error'
                const isDone = event.event_type === 'done'

                return (
                  <div key={idx} className={`px-4 py-2.5 border-l-2 flex items-start gap-3 ${
                    isThinking ? 'border-l-blue-400 bg-blue-50/30' :
                    isTool ? 'border-l-amber-400 bg-amber-50/30' :
                    isResult ? 'border-l-emerald-400 bg-emerald-50/30' :
                    isError ? 'border-l-red-400 bg-red-50/30' :
                    isDone ? 'border-l-emerald-400 bg-emerald-50/50' :
                    ''
                  }`}>
                    <div className="mt-0.5 flex-shrink-0">
                      {isThinking ? <FileCode className="h-3.5 w-3.5 text-blue-500" /> :
                       isTool ? <Play className="h-3.5 w-3.5 text-amber-500" /> :
                       isResult ? <Terminal className="h-3.5 w-3.5 text-emerald-500" /> :
                       isError ? <AlertTriangle className="h-3.5 w-3.5 text-red-500" /> :
                       isDone ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" /> :
                       <Circle className="h-3.5 w-3.5 text-slate-400" />}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-0.5">
                        <span className="text-[10px] text-muted-foreground">{event.timestamp}</span>
                        <Badge variant="outline" className="text-[10px] py-0 px-1 capitalize">{
                          event.event_type.replace('_', ' ')
                        }</Badge>
                        {event.tool_name && (
                          <Badge variant="secondary" className="text-[10px] py-0 px-1 font-mono">{event.tool_name}</Badge>
                        )}
                      </div>
                      {event.content && (
                        <p className={`text-xs whitespace-pre-wrap ${isError ? 'text-red-700' : 'text-muted-foreground'}`}>
                          {event.content}
                        </p>
                      )}
                      {event.tool_input && (
                        <details className="mt-0.5">
                          <summary className="text-[10px] text-muted-foreground cursor-pointer">Input</summary>
                          <pre className="text-xs bg-muted p-1.5 rounded mt-0.5 max-h-20 overflow-auto whitespace-pre-wrap font-mono">{event.tool_input}</pre>
                        </details>
                      )}
                      {event.tool_output && (
                        <pre className="text-xs bg-slate-900 text-green-400 p-1.5 rounded mt-0.5 max-h-24 overflow-auto whitespace-pre-wrap font-mono">{event.tool_output}</pre>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Main layout: tree + viewer */}
      <div className="grid grid-cols-12 gap-4" style={{ minHeight: '500px' }}>
        {/* Left: Tree / Search Results */}
        <div className="col-span-4 lg:col-span-3">
          <Card className="h-full">
            <CardHeader className="py-2 px-3">
              <CardTitle className="text-xs font-medium text-muted-foreground">
                {searchResults !== null ? `Search: ${searchResults.length} results` : 'Files'}
              </CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              {searchResults !== null ? (
                /* Search results */
                <div className="max-h-[500px] overflow-auto">
                  {searchResults.length === 0 ? (
                    <p className="text-sm text-muted-foreground p-4 text-center">No results</p>
                  ) : (
                    searchResults.map((r, i) => (
                      <button
                        key={i}
                        className="w-full text-left px-3 py-1.5 hover:bg-accent text-sm flex items-center gap-2 border-b border-b-muted/30"
                        onClick={() => {
                          if (!r.isDir && projectId) {
                            setFileLoading(true)
                            setSelectedFile(r.path)
                            getFileContent(projectId, r.path).then(resp => {
                              setFileContent(resp.content)
                              setFileLanguage(resp.language || null)
                            }).catch(() => setFileContent('Error loading file')).finally(() => setFileLoading(false))
                          }
                        }}
                      >
                        {r.isDir ? <FolderClosed className="h-3 w-3 text-blue-500 flex-shrink-0" /> : <File className="h-3 w-3 text-gray-400 flex-shrink-0" />}
                        <div className="truncate">
                          <div className="font-mono text-xs truncate">{r.path}</div>
                          {r.line && <div className="text-xs text-muted-foreground">Line {r.line}: {r.content}</div>}
                        </div>
                      </button>
                    ))
                  )}
                </div>
              ) : (
                /* Tree view */
                <div className="max-h-[500px] overflow-auto">
                  {/* Breadcrumb */}
                  {currentPath && (
                    <div className="flex items-center gap-1 px-3 py-2 border-b bg-muted/30 text-xs flex-wrap">
                      <button className="hover:underline text-blue-600" onClick={() => { setCurrentPath(''); setSelectedFile(null); setFileContent(null) }}>root</button>
                      {breadcrumbs.map((part, i) => (
                        <span key={i} className="flex items-center gap-1">
                          <ChevronRight className="h-3 w-3 text-muted-foreground" />
                          <button
                            className="hover:underline text-blue-600"
                            onClick={() => {
                              setCurrentPath(breadcrumbs.slice(0, i + 1).join('/'))
                              setSelectedFile(null)
                              setFileContent(null)
                            }}
                          >
                            {part}
                          </button>
                        </span>
                      ))}
                    </div>
                  )}

                  {/* Up button */}
                  {currentPath && (
                    <button
                      className="w-full text-left px-3 py-1.5 hover:bg-accent text-sm flex items-center gap-2 border-b"
                      onClick={handleNavigateUp}
                    >
                      <ArrowLeft className="h-3 w-3" />
                      <span className="text-muted-foreground">..</span>
                    </button>
                  )}

                  {treeLoading ? (
                    <div className="p-4 text-center"><Loader2 className="h-5 w-5 animate-spin mx-auto text-violet-500" /></div>
                  ) : treeEntries.length === 0 ? (
                    <p className="text-sm text-muted-foreground p-4 text-center">Empty directory</p>
                  ) : (
                    treeEntries.map((entry) => (
                      <button
                        key={entry.path}
                        className={`w-full text-left px-3 py-1.5 hover:bg-accent text-sm flex items-center gap-2 border-b border-b-muted/30 ${
                          selectedFile === entry.path ? 'bg-accent' : ''
                        }`}
                        onClick={() => handleTreeClick(entry)}
                      >
                        {entry.isDir ? (
                          <FolderClosed className="h-4 w-4 text-blue-500 flex-shrink-0" />
                        ) : (
                          <FileCode className="h-4 w-4 text-gray-400 flex-shrink-0" />
                        )}
                        <span className="truncate font-mono text-xs">{entry.name}</span>
                        {!entry.isDir && (
                          <span className="ml-auto text-xs text-muted-foreground flex-shrink-0">{formatBytes(entry.size)}</span>
                        )}
                      </button>
                    ))
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Right: File viewer */}
        <div className="col-span-8 lg:col-span-9">
          <Card className="h-full flex flex-col">
            {selectedFile ? (
              <>
                <CardHeader className="py-2 px-4 border-b flex-row items-center justify-between">
                  <div className="flex items-center gap-2">
                    <FileCode className="h-4 w-4 text-violet-500" />
                    <span className="font-mono text-sm">{selectedFile}</span>
                    {fileLanguage && <Badge variant="secondary" className="text-xs">{fileLanguage}</Badge>}
                  </div>
                  <div className="flex items-center gap-1">
                    <Button variant="ghost" size="sm" onClick={copyPath} title="Copy path">
                      <Copy className="h-3 w-3" />
                    </Button>
                    <Button
                      variant="ghost" size="sm"
                      onClick={() => projectId && window.open(getFileDownloadUrl(projectId, selectedFile), '_blank')}
                      title="Download file"
                    >
                      <Download className="h-3 w-3" />
                    </Button>
                  </div>
                </CardHeader>
                <CardContent className="flex-1 p-0 overflow-auto">
                  {fileLoading ? (
                    <div className="flex items-center justify-center h-48">
                      <Loader2 className="h-6 w-6 animate-spin text-violet-500" />
                    </div>
                  ) : (
                    <pre className={`p-4 text-xs font-mono whitespace-pre-wrap break-all ${LANG_COLORS[fileLanguage || ''] || 'text-gray-800'}`}
                         style={{ minHeight: '400px', background: '#fafafa' }}>
                      {fileContent}
                    </pre>
                  )}
                </CardContent>
              </>
            ) : (
              <CardContent className="flex-1 flex items-center justify-center text-center py-16">
                <div>
                  <FolderOpen className="h-16 w-16 text-muted-foreground mx-auto mb-4 opacity-50" />
                  <p className="text-muted-foreground">Select a file from the tree to view its contents</p>
                </div>
              </CardContent>
            )}
          </Card>
        </div>
      </div>
    </AppPageLayout>
  )
}
