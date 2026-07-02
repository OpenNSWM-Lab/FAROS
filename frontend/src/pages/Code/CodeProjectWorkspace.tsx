/**
 * CodeProjectWorkspace — Main Code page with Plan Context + Generate + Debug UI.
 *
 * When navigated with ?linkId=..., loads the plan context and shows:
 * - LEFT: Project tree + file viewer (after generation)
 * - RIGHT: Tabs for "Plan Context" and "Generation" (agent steps + logs)
 *
 * When no linkId, shows a project-centric dashboard with past sessions.
 */

import { useState, useEffect, useCallback } from 'react'
import { useSearchParams, useNavigate, useLocation } from 'react-router-dom'
import { AppPageLayout } from '@/components/layout/AppPageLayout'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import {
  Code2, Loader2, CheckCircle2, XCircle, Clock, Play,
  FileText, FolderTree, Download,
  AlertTriangle, Sparkles,
  SkipForward, Wrench, GitBranch, FolderOpen, FlaskConical, Eye,
} from 'lucide-react'
const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

interface PlanContextSession {
  id: string
  status: string
  config?: Record<string, unknown> & {
    ideaSeedQuery?: string
    paperType?: string
  }
}

interface PlanContextCandidate {
  title: string
  planAbstract?: string
  method?: string
  gapAnalysis?: string
  experimentDesign?: Record<string, unknown> & {
    research_question?: string
  }
  evaluationProtocol?: Record<string, unknown>
  overallScore?: number
}

interface PlanContext {
  linkId: string
  planSessionId: string
  candidateId: string
  candidateIndex?: number
  session?: PlanContextSession
  candidate?: PlanContextCandidate
  createdAt: string
}

interface CodeGenStep {
  name: string
  status: string
  detail: string
  durationMs: number
  toolCalls: Array<{ skill: string; ok: boolean }>
}

interface CodeGenSessionConfig extends Record<string, unknown> {
  title?: string
}

interface CodeGenSessionData {
  id: string
  projectId: string
  planLinkId?: string
  providerName: string
  model: string
  status: string
  steps: CodeGenStep[]
  memory: {
    referenceCount: number
    githubRepoCount: number
    summaryCount: number
    hasDesignDoc: boolean
    fileTreePlanned: boolean
    generatedFileCount: number
    verificationCount: number
    patchesApplied: number
  }
  config: CodeGenSessionConfig
  createdAt: string
  startedAt?: string
  completedAt?: string
  errorMessage?: string
}

interface TreeEntry {
  name: string
  path: string
  isDir: boolean
  size: number
}

export function CodeProjectWorkspace() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const location = useLocation()
  const linkId = searchParams.get('linkId')

  // Plan context
  const [planContext, setPlanContext] = useState<PlanContext | null>(null)
  const [loadingContext, setLoadingContext] = useState(false)

  // Generation
  const [codeGenSession, setCodeGenSession] = useState<CodeGenSessionData | null>(null)
  const [isGenerating, setIsGenerating] = useState(false)
  const [isPolling, setIsPolling] = useState(false)
  const [genError, setGenError] = useState<string | null>(null)

  // Config (LLM 使用设置页的活跃 provider/model，不在此页单独配置)
  const [settingsLlmLabel, setSettingsLlmLabel] = useState<string | null>(null)
  const [language, setLanguage] = useState('python')
  const [framework, setFramework] = useState('FastAPI')
  const [enableWebSearch, setEnableWebSearch] = useState(true)
  const [enableGithub, setEnableGithub] = useState(true)

  // Project browser
  const [projectId, setProjectId] = useState<string | null>(null)
  const [treeEntries, setTreeEntries] = useState<TreeEntry[]>([])
  const [treePath, setTreePath] = useState('')
  const [fileContent, setFileContent] = useState<string | null>(null)
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [loadingTree, setLoadingTree] = useState(false)

  // Tabs
  const [activeTab, setActiveTab] = useState<'plan' | 'generation' | 'blueprint'>('plan')

  // Past sessions
  const [pastSessions, setPastSessions] = useState<CodeGenSessionData[]>([])

  // Load plan context from linkId
  useEffect(() => {
    if (!linkId) return
    setLoadingContext(true)
    fetch(`${API_BASE}/api/v1/code/plan-links/${linkId}`)
      .then(r => { if (!r.ok) throw new Error('Link not found'); return r.json() })
      .then(data => { setPlanContext(data); setActiveTab('plan') })
      .catch(() => setPlanContext(null))
      .finally(() => setLoadingContext(false))
  }, [linkId])

  // Load past sessions
  useEffect(() => {
    fetch(`${API_BASE}/api/v1/codegen/sessions`)
      .then(r => r.json())
      .then(data => setPastSessions(data.sessions || []))
      .catch(() => { void 0 })
  }, [])

  // Polling for generation status
  const pollSession = useCallback(async () => {
    if (!codeGenSession?.id || !isPolling) return
    try {
      const resp = await fetch(`${API_BASE}/api/v1/codegen/sessions/${codeGenSession.id}`)
      if (!resp.ok) return
      const data: CodeGenSessionData = await resp.json()
      setCodeGenSession(data)
      if (data.status === 'completed' || data.status === 'failed') {
        setIsPolling(false)
        setIsGenerating(false)
        if (data.status === 'completed' && data.projectId) {
          setProjectId(data.projectId)
          loadTree(data.projectId, '')
        }
      }
    } catch { void 0 }
  }, [codeGenSession?.id, isPolling])

  useEffect(() => {
    if (!isPolling) return
    const interval = setInterval(pollSession, 2000)
    return () => clearInterval(interval)
  }, [isPolling, pollSession])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const r = await fetch(`${API_BASE}/api/v1/providers`)
        if (!r.ok || cancelled) return
        const data = await r.json()
        const pname = data.activeProvider || ''
        const providerInfo = (data.providers || []).find(
          (p: { providerName: string; model: string }) => p.providerName === pname
        )
        const m = providerInfo?.model || ''
        if (!cancelled) setSettingsLlmLabel(`${pname} / ${m}`)
      } catch {
        if (!cancelled) setSettingsLlmLabel(null)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  // Start code generation
  const startGeneration = async () => {
    if (!planContext) return
    setIsGenerating(true)
    setGenError(null)
    setActiveTab('generation')
    try {
      // Create session
      const createResp = await fetch(`${API_BASE}/api/v1/codegen/sessions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          planLinkId: planContext.linkId,
          planSessionId: planContext.planSessionId,
          candidateId: planContext.candidateId,
          language,
          framework,
          enableWebSearch,
          enableGithub,
        }),
      })
      if (!createResp.ok) {
        const err = await createResp.json().catch(() => ({}))
        throw new Error(err.detail || `Error ${createResp.status}`)
      }
      const sessionData: CodeGenSessionData = await createResp.json()
      setCodeGenSession(sessionData)
      setProjectId(sessionData.projectId)

      // Start agent
      const startResp = await fetch(`${API_BASE}/api/v1/codegen/sessions/${sessionData.id}/start`, { method: 'POST' })
      if (!startResp.ok) throw new Error('Failed to start agent')

      setIsPolling(true)
    } catch (err) {
      setGenError(err instanceof Error ? err.message : 'Generation failed')
      setIsGenerating(false)
    }
  }

  // Load project tree
  const loadTree = async (pid: string, path: string) => {
    setLoadingTree(true)
    try {
      const qs = path ? `?path=${encodeURIComponent(path)}` : ''
      const resp = await fetch(`${API_BASE}/api/v1/code/projects/${pid}/tree${qs}`)
      if (resp.ok) {
        const data = await resp.json()
        setTreeEntries(data.entries || [])
        setTreePath(path)
      }
    } catch { void 0 }
    setLoadingTree(false)
  }

  // Load file content
  const loadFile = async (pid: string, path: string) => {
    try {
      const resp = await fetch(`${API_BASE}/api/v1/code/projects/${pid}/file?path=${encodeURIComponent(path)}`)
      if (resp.ok) {
        const data = await resp.json()
        setFileContent(data.content)
        setSelectedFile(path)
      }
    } catch { void 0 }
  }

  const getStepIcon = (status: string) => {
    switch (status) {
      case 'ok': return <CheckCircle2 className="h-4 w-4 text-green-500" />
      case 'failed': return <XCircle className="h-4 w-4 text-red-500" />
      case 'running': return <Loader2 className="h-4 w-4 text-blue-500 animate-spin" />
      case 'skipped': return <SkipForward className="h-4 w-4 text-gray-400" />
      default: return <Clock className="h-4 w-4 text-gray-300" />
    }
  }

  const getStepLabel = (name: string) => {
    const labels: Record<string, string> = {
      research_web_search: 'Web Search',
      research_github_search: 'GitHub Search',
      research_summarize: 'Summarize Context',
      design_document: 'Design Document',
      plan_file_tree: 'Plan File Tree',
      code_synthesis_batch: 'Batch Code Synthesis',
      code_synthesis_fill: 'Fill Missing Files',
      verify_structure: 'Verify Structure',
      repair_cycle_1: 'Repair Cycle 1',
      repair_cycle_2: 'Repair Cycle 2',
      re_verify_1: 'Re-verify 1',
      re_verify_2: 'Re-verify 2',
      persist_files: 'Persist Files',
    }
    return labels[name] || name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
  }

  // Render Plan Context Panel
  const renderPlanContext = () => {
    if (loadingContext) {
      return <div className="flex items-center gap-2 p-4"><Loader2 className="h-4 w-4 animate-spin" /> Loading plan context...</div>
    }
    if (!planContext) {
      return (
        <div className="p-4 text-center text-muted-foreground">
          <FileText className="h-8 w-8 mx-auto mb-2 opacity-50" />
          <p className="text-sm">No plan context loaded.</p>
          <p className="text-xs mt-1">Navigate from the Plan page to load a plan.</p>
        </div>
      )
    }
    const c = planContext.candidate
    const s = planContext.session
    return (
      <div className="space-y-4 p-4">
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="text-xs">Link: {planContext.linkId.slice(0, 16)}</Badge>
          {planContext.candidateIndex && <Badge className="bg-teal-100 text-teal-800">#{planContext.candidateIndex}</Badge>}
        </div>
        {s && (
          <div className="p-3 rounded bg-slate-50 border text-sm space-y-1">
            <p className="font-medium text-slate-700">Plan Session</p>
            <p className="text-xs text-muted-foreground">ID: {s.id}</p>
            <p className="text-xs">Status: <Badge variant="outline" className="text-xs ml-1">{s.status}</Badge></p>
            {s.config?.ideaSeedQuery && <p className="text-xs">Seed: {s.config.ideaSeedQuery}</p>}
            {s.config?.paperType && <p className="text-xs">Paper Type: {s.config.paperType}</p>}
          </div>
        )}
        {c && (
          <div className="p-3 rounded bg-gradient-to-r from-teal-50 to-emerald-50 border text-sm space-y-2">
            <p className="font-semibold text-teal-800">{c.title}</p>
            {c.planAbstract && <p className="text-xs text-muted-foreground">{c.planAbstract}</p>}
            {c.method && <p className="text-xs"><span className="font-medium">Method:</span> {c.method}</p>}
            {c.gapAnalysis && <p className="text-xs"><span className="font-medium">Gap:</span> {c.gapAnalysis}</p>}
            {c.experimentDesign?.research_question && (
              <p className="text-xs"><span className="font-medium">Research Q:</span> {c.experimentDesign.research_question}</p>
            )}
            {c.evaluationProtocol && Object.keys(c.evaluationProtocol).length > 0 && (
              <p className="text-xs"><span className="font-medium">Evaluation:</span> {JSON.stringify(c.evaluationProtocol).slice(0, 200)}</p>
            )}
            {c.overallScore != null && (
              <Badge className={`text-xs ${c.overallScore >= 7 ? 'bg-green-100 text-green-800' : 'bg-amber-100 text-amber-800'}`}>
                Score: {Number(c.overallScore).toFixed(1)}
              </Badge>
            )}
          </div>
        )}
        {!codeGenSession && (
          <div className="space-y-3 pt-2 border-t">
            <p className="text-sm font-medium">Generation Config</p>
            <p className="text-xs text-muted-foreground">
              LLM：{settingsLlmLabel ?? '加载设置中…'}（与{' '}
              <button type="button" className="text-violet-600 underline" onClick={() => navigate('/settings/providers')}>
                设置 → LLM 提供商
              </button>
              {' '}一致）
            </p>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-xs font-medium">Language</label>
                <select value={language} onChange={e => setLanguage(e.target.value)} className="w-full rounded border px-2 py-1 text-xs">
                  <option value="python">Python</option>
                  <option value="typescript">TypeScript</option>
                </select>
              </div>
              <div>
                <label className="text-xs font-medium">Framework</label>
                <input value={framework} onChange={e => setFramework(e.target.value)} className="w-full rounded border px-2 py-1 text-xs" />
              </div>
            </div>
            <div className="flex gap-3 text-xs">
              <label className="flex items-center gap-1"><input type="checkbox" checked={enableWebSearch} onChange={e => setEnableWebSearch(e.target.checked)} /> Web Search</label>
              <label className="flex items-center gap-1"><input type="checkbox" checked={enableGithub} onChange={e => setEnableGithub(e.target.checked)} /> GitHub</label>
            </div>
            <Button onClick={startGeneration} disabled={isGenerating} className="w-full bg-violet-500 hover:bg-violet-600">
              {isGenerating ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : <Play className="h-4 w-4 mr-2" />}
              Generate Project Code
            </Button>
          </div>
        )}
      </div>
    )
  }

  // Render Generation Panel (Debug UI)
  const renderGeneration = () => {
    if (!codeGenSession) {
      return (
        <div className="p-4 text-center text-muted-foreground">
          <Wrench className="h-8 w-8 mx-auto mb-2 opacity-50" />
          <p className="text-sm">No generation in progress.</p>
          <p className="text-xs mt-1">Start a generation from the Plan Context tab.</p>
        </div>
      )
    }
    const s = codeGenSession
    return (
      <div className="space-y-4 p-4">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium">Session: {s.id.slice(0, 16)}</p>
            <p className="text-xs text-muted-foreground">Project: {s.projectId}</p>
            <p className="text-xs text-muted-foreground mt-0.5">LLM: {s.providerName} / {s.model}</p>
          </div>
          <Badge className={`text-xs ${s.status === 'completed' ? 'bg-green-100 text-green-800' :
              s.status === 'failed' ? 'bg-red-100 text-red-800' :
                s.status === 'running' ? 'bg-blue-100 text-blue-800' :
                  'bg-yellow-100 text-yellow-800'
            }`}>{s.status}</Badge>
        </div>

        {/* Steps */}
        <div className="space-y-1">
          <p className="text-xs font-medium text-muted-foreground mb-2">Pipeline Steps</p>
          {s.steps.map((step, i) => (
            <div key={i} className="flex items-center gap-2 p-2 rounded bg-slate-50 text-xs">
              {getStepIcon(step.status)}
              <span className="font-medium flex-1">{getStepLabel(step.name)}</span>
              {step.durationMs > 0 && <span className="text-muted-foreground">{(step.durationMs / 1000).toFixed(1)}s</span>}
              {step.detail && <span className="text-muted-foreground truncate max-w-[150px]" title={step.detail}>{step.detail}</span>}
            </div>
          ))}
          {s.status === 'running' && (
            <div className="flex items-center gap-2 p-2 text-xs text-blue-600">
              <Loader2 className="h-3 w-3 animate-spin" /> Agent is working...
            </div>
          )}
        </div>

        {/* Memory Summary */}
        {s.memory && (
          <div className="p-3 rounded bg-slate-50 border">
            <p className="text-xs font-medium mb-2">Agent Memory</p>
            <div className="grid grid-cols-2 gap-1 text-xs">
              <span>References: {s.memory.referenceCount}</span>
              <span>GitHub Repos: {s.memory.githubRepoCount}</span>
              <span>Design Doc: {s.memory.hasDesignDoc ? '✓' : '—'}</span>
              <span>File Tree: {s.memory.fileTreePlanned ? '✓' : '—'}</span>
              <span>Files Generated: {s.memory.generatedFileCount}</span>
              <span>Patches: {s.memory.patchesApplied}</span>
            </div>
          </div>
        )}

        {/* Error */}
        {s.errorMessage && (
          <div className="p-3 rounded bg-red-50 border border-red-200 text-xs text-red-700">
            <AlertTriangle className="h-4 w-4 inline mr-1" />{s.errorMessage}
          </div>
        )}

        {/* Completion actions */}
        {s.status === 'completed' && s.projectId && (
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={() => navigate(`/code/projects/${s.projectId}`)}>
              <Eye className="h-4 w-4 mr-1" /> Browse Project
            </Button>
            <Button size="sm" variant="outline" onClick={async () => {
              try {
                const resp = await fetch(`${API_BASE}/api/v1/code/projects/${s.projectId}/export`, { method: 'POST' })
                if (resp.ok) {
                  const data = await resp.json()
                  window.open(`${API_BASE}/api/v1/code/projects/exports/${data.id}/download`, '_blank')
                }
              } catch { void 0 }
            }}>
              <Download className="h-4 w-4 mr-1" /> Download ZIP
            </Button>
          </div>
        )}
      </div>
    )
  }

  // Render file tree
  const renderFileTree = () => {
    if (!projectId) return null
    return (
      <div className="border rounded-lg overflow-hidden">
        <div className="bg-slate-50 px-3 py-2 border-b flex items-center gap-2">
          <FolderTree className="h-4 w-4 text-violet-500" />
          <span className="text-sm font-medium">Project Files</span>
          {treePath && (
            <Button variant="ghost" size="sm" className="text-xs h-6" onClick={() => {
              const parent = treePath.includes('/') ? treePath.split('/').slice(0, -1).join('/') : ''
              loadTree(projectId, parent)
            }}>← Back</Button>
          )}
          {treePath && <span className="text-xs text-muted-foreground">/{treePath}</span>}
        </div>
        {loadingTree ? (
          <div className="p-4 flex items-center gap-2 text-sm"><Loader2 className="h-4 w-4 animate-spin" /> Loading...</div>
        ) : treeEntries.length === 0 ? (
          <div className="p-4 text-sm text-muted-foreground text-center">No files yet. Generate code first.</div>
        ) : (
          <div className="max-h-96 overflow-y-auto">
            {treeEntries.map(entry => (
              <div
                key={entry.path}
                className={`flex items-center gap-2 px-3 py-1.5 hover:bg-slate-50 cursor-pointer text-sm ${selectedFile === entry.path ? 'bg-violet-50 border-l-2 border-violet-500' : ''}`}
                onClick={() => {
                  if (entry.isDir) {
                    loadTree(projectId, entry.path)
                  } else {
                    loadFile(projectId, entry.path)
                  }
                }}
              >
                {entry.isDir ? <FolderTree className="h-4 w-4 text-amber-500" /> : <FileText className="h-4 w-4 text-slate-400" />}
                <span className="flex-1 truncate">{entry.name}</span>
                {!entry.isDir && <span className="text-xs text-muted-foreground">{entry.size > 1024 ? `${(entry.size / 1024).toFixed(1)}K` : `${entry.size}B`}</span>}
              </div>
            ))}
          </div>
        )}
      </div>
    )
  }

  // Render past sessions list (when no linkId)
  const renderPastSessions = () => (
    <div className="space-y-3">
      <h3 className="text-sm font-medium">Past Generation Sessions</h3>
      {pastSessions.length === 0 ? (
        <p className="text-sm text-muted-foreground">No sessions yet. Navigate from Plan page to start.</p>
      ) : (
        <div className="space-y-2">
          {pastSessions.map(s => (
            <Card key={s.id} className="cursor-pointer hover:border-violet-300" onClick={() => {
              setCodeGenSession(s)
              setProjectId(s.projectId)
              setActiveTab('generation')
              if (s.status === 'completed' && s.projectId) loadTree(s.projectId, '')
            }}>
              <CardContent className="py-3 flex items-center gap-3">
                <Badge className={`text-xs ${s.status === 'completed' ? 'bg-green-100 text-green-800' :
                    s.status === 'failed' ? 'bg-red-100 text-red-800' :
                      'bg-yellow-100 text-yellow-800'
                  }`}>{s.status}</Badge>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">{s.config?.title || s.id}</p>
                  <p className="text-xs text-muted-foreground">Project: {s.projectId} • {s.steps?.length || 0} steps</p>
                </div>
                <span className="text-xs text-muted-foreground">{new Date(s.createdAt).toLocaleDateString()}</span>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  )

  return (
    <AppPageLayout
      title="Code Generation"
      subtitle="Agent-driven project-level code generation from research plans"
      icon={Code2}
      iconColor="violet"
      accentColor="violet"
    >
      {/* Unified Code sub-navigation — shared across all Code pages */}
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

      {/* Workspace internal tabs — Plan Context / Generation */}
      <div className="flex border-b mb-4">
        <button
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${activeTab === 'plan' ? 'border-teal-500 text-teal-700' : 'border-transparent text-muted-foreground hover:text-foreground'}`}
          onClick={() => setActiveTab('plan')}
        >
          <FileText className="h-4 w-4 inline mr-1" />Plan Context
        </button>
        <button
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${activeTab === 'generation' ? 'border-violet-500 text-violet-700' : 'border-transparent text-muted-foreground hover:text-foreground'}`}
          onClick={() => setActiveTab('generation')}
        >
          <Wrench className="h-4 w-4 inline mr-1" />Generation
          {codeGenSession?.status === 'running' && <Loader2 className="h-3 w-3 inline ml-1 animate-spin" />}
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* LEFT: Project Tree + File Viewer */}
          <div className="lg:col-span-2 space-y-4">
            {projectId && renderFileTree()}

            {/* File content viewer */}
            {selectedFile && fileContent !== null && (
              <div className="border rounded-lg overflow-hidden">
                <div className="bg-slate-50 px-3 py-2 border-b flex items-center gap-2">
                  <FileText className="h-4 w-4 text-violet-500" />
                  <span className="text-sm font-medium truncate">{selectedFile}</span>
                  <Button variant="ghost" size="sm" className="ml-auto h-6 text-xs" onClick={() => { setSelectedFile(null); setFileContent(null) }}>Close</Button>
                </div>
                <pre className="p-4 text-xs font-mono overflow-auto max-h-[500px] bg-slate-900 text-slate-100">
                  {fileContent}
                </pre>
              </div>
            )}

            {/* When no project yet and no linkId, show past sessions */}
            {!projectId && !linkId && renderPastSessions()}

            {/* When linkId but no project yet, show welcome */}
            {!projectId && linkId && planContext && !codeGenSession && (
              <Card>
                <CardContent className="py-8 text-center">
                  <Sparkles className="h-10 w-10 text-violet-400 mx-auto mb-3" />
                  <h3 className="text-lg font-medium mb-1">Ready to Generate</h3>
                  <p className="text-sm text-muted-foreground mb-4">
                    Plan loaded: <strong>{planContext.candidate?.title || 'Research Plan'}</strong>
                  </p>
                  <p className="text-xs text-muted-foreground">Configure options and click "Generate Project Code" in the right panel →</p>
                </CardContent>
              </Card>
            )}
          </div>

          {/* RIGHT: Plan / Generation content */}
          <div className="space-y-4">
            <Card>
              <CardContent className="p-0">
                {activeTab === 'plan' ? renderPlanContext() : renderGeneration()}
              </CardContent>
            </Card>

            {genError && (
              <div className="p-3 rounded bg-red-50 border border-red-200 text-sm text-red-700">
                <AlertTriangle className="h-4 w-4 inline mr-1" />{genError}
              </div>
            )}
          </div>
        </div>
    </AppPageLayout>
  )
}
