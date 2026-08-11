import { useCallback, useEffect, useState } from 'react'
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, BookOpen, CheckCircle, Code2, Download, Eye, FileText, GitBranch, Loader2, Network, RefreshCw, Save, ScrollText } from 'lucide-react'
import { AppPageLayout } from '@/components/layout/AppPageLayout'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

type Stage = 'start' | 'brief' | 'writing' | 'result'

const STAGES: { id: Stage; label: string; description: string }[] = [
  { id: 'start', label: 'Start', description: 'Paper, template, links, evidence' },
  { id: 'brief', label: 'Brief', description: 'Paper and section brief' },
  { id: 'writing', label: 'Feedback Writing', description: 'Agent loop and revision requests' },
  { id: 'result', label: 'Results', description: 'Files and PDF preview' },
]

interface TemplateInfo {
  id: string
  name: string
  description?: string
}

interface PaperLog {
  timestamp: string
  message: string
}

interface PaperRecord {
  id: string
  title: string
  authors?: string[]
  paperType: string
  targetVenue?: string
  templateId?: string
  status: string
  planLinkId?: string
  projectId?: string
  experimentIds?: string[]
  runIds?: string[]
  providerName?: string
  model?: string
  pdfAvailable?: boolean
  briefJson?: Record<string, unknown> | null
  briefUserEdits?: string
  briefStatus?: string
  outlineJson?: PaperOutline | null
  outlineStatus?: string
  evidenceJson?: Record<string, unknown> | null
  evidenceStatus?: string
  compileStatus?: string
  compileErrors?: string | null
  simpleReviewPassed?: boolean
  logs?: PaperLog[]
  createdAt: string
  updatedAt: string
}

interface PaperOutlineSection {
  id: string
  title: string
  keyPoints?: string[]
  minWords?: number
}

interface PaperOutline {
  title?: string
  authors?: string[]
  abstract?: string
  contributions?: string[]
  sections?: PaperOutlineSection[]
}

interface PaperFile {
  path: string
  name: string
  size: number
  isDir: boolean
}

interface CodeProject {
  id: string
  title: string
  description?: string
  language?: string
  framework?: string
}

interface RunRecord {
  id: string
  status: string
  type: string
  duration?: number
  errorMessage?: string
  artifacts?: unknown[]
  config?: {
    model?: string
    workplaceName?: string
  }
}

interface Experiment {
  id: string
  name: string
  status?: string
}

interface FeedbackIssue {
  severity?: string
  path?: string
  message?: string
}

interface FeedbackTarget {
  path?: string
  instruction?: string
}

interface FeedbackRound {
  source: 'latex_compile' | 'simple_review' | 'writing_rewrite'
  artifactPath: string
  iteration?: number
  issues: FeedbackIssue[]
  targets: FeedbackTarget[]
  rewrites: unknown[]
  summary: string
}

const statusClass: Record<string, string> = {
  created: 'border-slate-300 bg-slate-50 text-slate-700',
  generating: 'border-blue-300 bg-blue-50 text-blue-700',
  completed: 'border-emerald-300 bg-emerald-50 text-emerald-700',
  failed: 'border-red-300 bg-red-50 text-red-700',
}

const validStage = (value?: string): value is Stage => STAGES.some(stage => stage.id === value)

const toText = (value: unknown): string => {
  if (value === undefined || value === null || value === '') return 'N/A'
  if (Array.isArray(value)) return value.map(toText).join('\n')
  if (typeof value === 'object') return JSON.stringify(value, null, 2)
  return String(value)
}

const listItems = (value: unknown): string[] => {
  if (!Array.isArray(value)) return []
  return value.map(item => toText(item)).filter(Boolean)
}

const jsonPreview = (value: unknown, max = 1400) => {
  const text = typeof value === 'string' ? value : JSON.stringify(value ?? {}, null, 2)
  return text.length > max ? `${text.slice(0, max)}\n...` : text
}

export function PaperWritingWorkspace() {
  const { id, stage } = useParams()
  const navigate = useNavigate()
  const currentStage: Stage = validStage(stage) ? stage : 'start'
  const [paper, setPaper] = useState<PaperRecord | null>(null)
  const [templates, setTemplates] = useState<TemplateInfo[]>([])
  const [projects, setProjects] = useState<CodeProject[]>([])
  const [runs, setRuns] = useState<RunRecord[]>([])
  const [experiments, setExperiments] = useState<Experiment[]>([])
  const [files, setFiles] = useState<PaperFile[]>([])
  const [selectedFile, setSelectedFile] = useState('main.tex')
  const [fileContent, setFileContent] = useState('')
  const [briefUserEdits, setBriefUserEdits] = useState('')
  const [savingBrief, setSavingBrief] = useState(false)
  const [generatingBrief, setGeneratingBrief] = useState(false)
  const [generatingPaper, setGeneratingPaper] = useState(false)
  const [selectedTemplate, setSelectedTemplate] = useState('')
  const [contextProjectId, setContextProjectId] = useState('')
  const [contextRunIds, setContextRunIds] = useState<string[]>([])
  const [contextExperimentIds, setContextExperimentIds] = useState<string[]>([])
  const [savingContext, setSavingContext] = useState(false)
  const [feedbackRounds, setFeedbackRounds] = useState<FeedbackRound[]>([])
  const [pdfTs, setPdfTs] = useState(Date.now())
  const [loading, setLoading] = useState(true)

  const refreshPaper = useCallback(async () => {
    if (!id) return null
    const resp = await fetch(`${API_BASE}/api/v1/papers/${id}`)
    if (!resp.ok) return null
    const data = await resp.json()
    setPaper(data)
    setBriefUserEdits(data.briefUserEdits || '')
    setSelectedTemplate(data.templateId || data.targetVenue || '')
    setContextProjectId(data.projectId || '')
    setContextRunIds(data.runIds || [])
    setContextExperimentIds(data.experimentIds || [])
    return data as PaperRecord
  }, [id])

  const refreshFiles = useCallback(async () => {
    if (!id) return []
    const resp = await fetch(`${API_BASE}/api/v1/papers/${id}/tree`)
    if (!resp.ok) return []
    const data = await resp.json()
    const entries = (data.entries || []) as PaperFile[]
    setFiles(entries)
    return entries
  }, [id])

  const loadFile = useCallback(async (path: string) => {
    if (!id || !path) return
    setSelectedFile(path)
    const resp = await fetch(`${API_BASE}/api/v1/papers/${id}/files?path=${encodeURIComponent(path)}`)
    if (resp.ok) {
      const data = await resp.json()
      setFileContent(data.content || '')
    }
  }, [id])

  const refreshFeedback = useCallback(async (entries: PaperFile[]) => {
    if (!id) return
    const artifactFiles = entries
      .filter(file => !file.isDir && file.path.startsWith('artifacts/') && file.path.endsWith('.json'))
      .filter(file => (
        file.path.includes('latex_compile_agent')
        || file.path.includes('simple_review')
        || file.path.includes('feedback_rewrite')
      ))

    const rounds: FeedbackRound[] = []
    for (const artifact of artifactFiles) {
      const resp = await fetch(`${API_BASE}/api/v1/papers/${id}/files?path=${encodeURIComponent(artifact.path)}`)
      if (!resp.ok) continue
      const data = await resp.json()
      let parsed: Record<string, unknown>
      try {
        parsed = JSON.parse(data.content || '{}')
      } catch {
        continue
      }
      const reviews = Array.isArray(parsed.reviews) ? parsed.reviews as Record<string, unknown>[] : []
      for (const review of reviews) {
        rounds.push({
          source: artifact.path.includes('simple_review') ? 'simple_review' : 'latex_compile',
          artifactPath: artifact.path,
          iteration: Number(review.iteration) || undefined,
          issues: Array.isArray(review.issues) ? review.issues as FeedbackIssue[] : [],
          targets: Array.isArray(review.targets) ? review.targets as FeedbackTarget[] : [],
          rewrites: [],
          summary: `${artifact.path}${review.iteration ? ` round ${review.iteration}` : ''}`,
        })
      }
      const rewrites = Array.isArray(parsed.rewrites) ? parsed.rewrites : Array.isArray(parsed.writingRewrites) ? parsed.writingRewrites : []
      if (rewrites.length > 0 || artifact.path.includes('feedback_rewrite')) {
        rounds.push({
          source: 'writing_rewrite',
          artifactPath: artifact.path,
          issues: [],
          targets: [],
          rewrites,
          summary: `${rewrites.length} targeted writing rewrite${rewrites.length === 1 ? '' : 's'}`,
        })
      }
    }
    setFeedbackRounds(rounds)
  }, [id])

  const loadAll = useCallback(async () => {
    setLoading(true)
    try {
      const [paperData, entries] = await Promise.all([
        refreshPaper(),
        refreshFiles(),
        fetch(`${API_BASE}/api/v1/templates`).then(r => r.ok ? r.json() : { templates: [] }).then(data => setTemplates(data.templates || [])),
        fetch(`${API_BASE}/api/v1/code/projects`).then(r => r.ok ? r.json() : { projects: [] }).then(data => setProjects(data.projects || [])),
        fetch(`${API_BASE}/api/v1/runs`).then(r => r.ok ? r.json() : { runs: [] }).then(data => setRuns(data.runs || [])),
        fetch(`${API_BASE}/api/v1/experiments`).then(r => r.ok ? r.json() : { experiments: [] }).then(data => setExperiments(data.experiments || [])),
      ])
      await refreshFeedback(entries as PaperFile[])
      const hasMain = (entries as PaperFile[]).some(file => file.path === 'main.tex')
      if (paperData && hasMain) await loadFile('main.tex')
    } finally {
      setLoading(false)
    }
  }, [loadFile, refreshFeedback, refreshFiles, refreshPaper])

  useEffect(() => {
    void loadAll()
  }, [loadAll])

  if (!id) return <Navigate to="/papers" replace />
  if (!validStage(stage)) return <Navigate to={`/papers/${id}/start`} replace />

  const setStage = (next: Stage) => navigate(`/papers/${id}/${next}`)
  const linkedRuns = runs.filter(run => contextRunIds.includes(run.id))
  const linkedExperiments = experiments.filter(exp => contextExperimentIds.includes(exp.id))
  const linkedProject = projects.find(project => project.id === contextProjectId)
  const outline = paper?.outlineJson
  const brief = paper?.briefJson
  const allFiles = files.filter(file => !file.isDir)
  const selectedFileRecord = allFiles.find(file => file.path === selectedFile)

  const saveContext = async () => {
    if (!paper) return
    setSavingContext(true)
    try {
      const resp = await fetch(`${API_BASE}/api/v1/papers/${paper.id}/context`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          projectId: contextProjectId || undefined,
          runIds: contextRunIds,
          experimentIds: contextExperimentIds,
        }),
      })
      if (resp.ok) await refreshPaper()
    } finally {
      setSavingContext(false)
    }
  }

  const applyTemplate = async () => {
    if (!paper || !selectedTemplate) return
    await fetch(`${API_BASE}/api/v1/templates/apply`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paperId: paper.id, templateId: selectedTemplate, title: paper.title }),
    })
    await loadAll()
  }

  const saveBrief = async () => {
    if (!paper) return
    setSavingBrief(true)
    try {
      const resp = await fetch(`${API_BASE}/api/v1/papers/${paper.id}/brief`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ briefUserEdits }),
      })
      if (resp.ok) await refreshPaper()
    } finally {
      setSavingBrief(false)
    }
  }

  const generateBrief = async () => {
    if (!paper) return
    setGeneratingBrief(true)
    try {
      await fetch(`${API_BASE}/api/v1/papers/${paper.id}/brief/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ briefUserEdits, force: true }),
      })
      await refreshPaper()
    } finally {
      setGeneratingBrief(false)
    }
  }

  const generatePaper = async () => {
    if (!paper) return
    setGeneratingPaper(true)
    try {
      await fetch(`${API_BASE}/api/v1/papers/${paper.id}/generate`, { method: 'POST' })
      setStage('writing')
      for (let i = 0; i < 60; i += 1) {
        await new Promise(resolve => setTimeout(resolve, 3000))
        const updated = await refreshPaper()
        const entries = await refreshFiles()
        await refreshFeedback(entries)
        if (updated?.status === 'completed' || updated?.status === 'failed') break
      }
      setPdfTs(Date.now())
    } finally {
      setGeneratingPaper(false)
    }
  }

  const renderStage = () => {
    if (!paper) {
      return (
        <div className="flex min-h-[52vh] items-center justify-center text-sm text-slate-500">
          {loading ? <Loader2 className="h-5 w-5 animate-spin" /> : 'Paper not found'}
        </div>
      )
    }

    if (currentStage === 'start') {
      return (
        <StartStage
          paper={paper}
          templates={templates}
          selectedTemplate={selectedTemplate}
          setSelectedTemplate={setSelectedTemplate}
          applyTemplate={applyTemplate}
          projects={projects}
          runs={runs}
          experiments={experiments}
          contextProjectId={contextProjectId}
          setContextProjectId={setContextProjectId}
          contextRunIds={contextRunIds}
          setContextRunIds={setContextRunIds}
          contextExperimentIds={contextExperimentIds}
          setContextExperimentIds={setContextExperimentIds}
          linkedProject={linkedProject}
          linkedRuns={linkedRuns}
          linkedExperiments={linkedExperiments}
          saveContext={saveContext}
          savingContext={savingContext}
          goBrief={() => setStage('brief')}
        />
      )
    }

    if (currentStage === 'brief') {
      return (
        <BriefStage
          paper={paper}
          brief={brief}
          outline={outline}
          briefUserEdits={briefUserEdits}
          setBriefUserEdits={setBriefUserEdits}
          saveBrief={saveBrief}
          generateBrief={generateBrief}
          savingBrief={savingBrief}
          generatingBrief={generatingBrief}
          goWriting={generatePaper}
          generatingPaper={generatingPaper}
        />
      )
    }

    if (currentStage === 'writing') {
      return (
        <WritingStage
          paper={paper}
          feedbackRounds={feedbackRounds}
          logs={paper.logs || []}
          refresh={loadAll}
          generatePaper={generatePaper}
          generatingPaper={generatingPaper}
          goResult={() => setStage('result')}
        />
      )
    }

    return (
      <ResultStage
        paper={paper}
        files={allFiles}
        selectedFile={selectedFile}
        selectedFileRecord={selectedFileRecord}
        fileContent={fileContent}
        loadFile={loadFile}
        pdfTs={pdfTs}
        refreshFiles={async () => {
          const entries = await refreshFiles()
          await refreshFeedback(entries)
          setPdfTs(Date.now())
        }}
      />
    )
  }

  return (
    <AppPageLayout
      title={paper?.title || 'Paper Writing'}
      subtitle="Paper writing workspace"
      icon={BookOpen}
      iconColor="indigo"
      accentColor="indigo"
      actions={
        <div className="flex items-center gap-2">
          {paper && <Badge variant="outline" className={statusClass[paper.status] || ''}>{paper.status}</Badge>}
          <Link to="/papers">
            <Button variant="outline" size="sm"><ArrowLeft className="mr-1 h-4 w-4" /> Papers</Button>
          </Link>
        </div>
      }
    >
      <div className="mb-4 grid grid-cols-1 gap-2 md:grid-cols-4">
        {STAGES.map((item, index) => (
          <button
            key={item.id}
            className={`rounded-md border px-3 py-2 text-left transition-colors ${currentStage === item.id ? 'border-indigo-400 bg-indigo-50 text-indigo-900' : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'}`}
            onClick={() => setStage(item.id)}
          >
            <div className="flex items-center gap-2 text-sm font-medium">
              <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-white text-[11px]">{index + 1}</span>
              {item.label}
            </div>
            <div className="mt-1 text-xs text-slate-500">{item.description}</div>
          </button>
        ))}
      </div>
      {renderStage()}
    </AppPageLayout>
  )
}

function toggleList(current: string[], value: string) {
  return current.includes(value) ? current.filter(item => item !== value) : [...current, value]
}

function StartStage(props: {
  paper: PaperRecord
  templates: TemplateInfo[]
  selectedTemplate: string
  setSelectedTemplate: (value: string) => void
  applyTemplate: () => void
  projects: CodeProject[]
  runs: RunRecord[]
  experiments: Experiment[]
  contextProjectId: string
  setContextProjectId: (value: string) => void
  contextRunIds: string[]
  setContextRunIds: (value: string[]) => void
  contextExperimentIds: string[]
  setContextExperimentIds: (value: string[]) => void
  linkedProject?: CodeProject
  linkedRuns: RunRecord[]
  linkedExperiments: Experiment[]
  saveContext: () => void
  savingContext: boolean
  goBrief: () => void
}) {
  const evidence = props.paper.evidenceJson
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
      <Card className="xl:col-span-1">
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Paper and Template</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <InfoRow label="Title" value={props.paper.title} />
          <InfoRow label="Type" value={props.paper.paperType} />
          <InfoRow label="Venue" value={props.paper.targetVenue || 'generic'} />
          <InfoRow label="Provider" value={[props.paper.providerName, props.paper.model].filter(Boolean).join(' / ') || 'N/A'} />
          <div className="space-y-1">
            <label className="text-xs font-medium text-slate-600">Template</label>
            <div className="flex gap-2">
              <select className="min-w-0 flex-1 rounded-md border bg-white px-2 py-2 text-sm" value={props.selectedTemplate} onChange={event => props.setSelectedTemplate(event.target.value)}>
                <option value="">No template</option>
                {props.templates.map(template => <option key={template.id} value={template.id}>{template.name}</option>)}
              </select>
              <Button size="sm" variant="outline" onClick={props.applyTemplate}>Apply</Button>
            </div>
          </div>
          <Button className="w-full" onClick={props.goBrief}>Continue to Brief</Button>
        </CardContent>
      </Card>

      <Card className="xl:col-span-1">
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Module Links</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <SelectBlock label="Code project" value={props.contextProjectId} onChange={props.setContextProjectId} options={props.projects.map(project => ({ value: project.id, label: `${project.title} (${project.id})` }))} emptyLabel="No linked project" />
          <ChecklistBlock label="Runs" values={props.contextRunIds} setValues={props.setContextRunIds} items={props.runs.map(run => ({ value: run.id, label: `${run.id} [${run.status}] ${run.config?.model || run.type}` }))} />
          <ChecklistBlock label="Experiments" values={props.contextExperimentIds} setValues={props.setContextExperimentIds} items={props.experiments.map(exp => ({ value: exp.id, label: `${exp.name} (${exp.id})` }))} />
          <Button size="sm" variant="outline" className="w-full" onClick={props.saveContext} disabled={props.savingContext}>
            {props.savingContext ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Save className="mr-1 h-4 w-4" />}
            Save links
          </Button>
        </CardContent>
      </Card>

      <Card className="xl:col-span-1">
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Idea / Code Evidence</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <LinkedEvidence title="Linked project" items={props.linkedProject ? [`${props.linkedProject.title}: ${props.linkedProject.description || props.linkedProject.language || props.linkedProject.id}`] : []} />
          <LinkedEvidence title="Linked runs" items={props.linkedRuns.map(run => `${run.id}: ${run.status}, artifacts ${run.artifacts?.length || 0}${run.errorMessage ? `, error ${run.errorMessage}` : ''}`)} />
          <LinkedEvidence title="Linked experiments" items={props.linkedExperiments.map(exp => `${exp.name}: ${exp.status || exp.id}`)} />
          <div className="rounded-md border bg-slate-50 p-3">
            <div className="mb-2 text-xs font-medium text-slate-600">Collected evidence JSON</div>
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap text-[11px] text-slate-700">{evidence ? jsonPreview(evidence) : 'No collected evidence yet.'}</pre>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

function BriefStage(props: {
  paper: PaperRecord
  brief?: Record<string, unknown> | null
  outline?: PaperOutline | null
  briefUserEdits: string
  setBriefUserEdits: (value: string) => void
  saveBrief: () => void
  generateBrief: () => void
  savingBrief: boolean
  generatingBrief: boolean
  goWriting: () => void
  generatingPaper: boolean
}) {
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-5">
      <Card className="xl:col-span-2">
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Paper Brief</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <Badge variant="outline">{props.paper.briefStatus || 'missing'}</Badge>
          <SummaryBlock title="Research question" value={props.brief?.research_question} />
          <SummaryBlock title="Core claim" value={props.brief?.core_claim} />
          <ListBlock title="Contributions" items={listItems(props.brief?.contributions)} />
          <ListBlock title="Must-use evidence" items={listItems(props.brief?.must_use_evidence)} />
          <ListBlock title="Must-use figures" items={listItems(props.brief?.must_use_figures)} />
          <ListBlock title="Avoid claims" items={listItems(props.brief?.avoid_claims)} />
        </CardContent>
      </Card>

      <Card className="xl:col-span-3">
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Manual Paper and Section Brief</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <textarea
            className="min-h-[180px] w-full rounded-md border bg-white px-3 py-2 text-sm"
            value={props.briefUserEdits}
            onChange={event => props.setBriefUserEdits(event.target.value)}
            placeholder="Add paper-level summary, required evidence, claims to avoid, and section-level writing requirements."
          />
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={props.saveBrief} disabled={props.savingBrief}>
              {props.savingBrief ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Save className="mr-1 h-4 w-4" />}
              Save brief
            </Button>
            <Button variant="secondary" onClick={props.generateBrief} disabled={props.generatingBrief}>
              {props.generatingBrief ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-1 h-4 w-4" />}
              Generate brief
            </Button>
            <Button onClick={props.goWriting} disabled={props.generatingPaper}>
              {props.generatingPaper ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Code2 className="mr-1 h-4 w-4" />}
              Start Feedback Writing
            </Button>
          </div>
          <div className="rounded-md border bg-slate-50 p-3">
            <div className="mb-2 text-xs font-medium text-slate-600">Section brief / outline</div>
            {props.outline?.sections?.length ? (
              <div className="space-y-2">
                {props.outline.sections.map(section => (
                  <div key={section.id} className="rounded-md border bg-white p-2">
                    <div className="flex items-center justify-between gap-2">
                      <div className="font-medium text-slate-800">{section.title}</div>
                      <Badge variant="outline">{section.minWords || 0} words</Badge>
                    </div>
                    <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-slate-600">
                      {(section.keyPoints || []).map(point => <li key={point}>{point}</li>)}
                    </ul>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-sm text-slate-500">No outline generated yet.</div>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

function WritingStage(props: {
  paper: PaperRecord
  feedbackRounds: FeedbackRound[]
  logs: PaperLog[]
  refresh: () => void
  generatePaper: () => void
  generatingPaper: boolean
  goResult: () => void
}) {
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-5">
      <Card className="xl:col-span-2">
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Agent Interaction Diagram</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            <AgentNode icon={<FileText className="h-4 w-4" />} title="Writing Agent" desc="Drafts the paper and performs local section rewrites from feedback" active />
            <FlowArrow label="LaTeX source" />
            <AgentNode icon={<Code2 className="h-4 w-4" />} title="Latex Compile Agent" desc="Compiles and reports feedback only; it never edits" active={props.paper.compileStatus !== 'latexmk'} />
            <FlowArrow label="compile feedback targets" reverse />
            <AgentNode icon={<ScrollText className="h-4 w-4" />} title="Simple Review Agent" desc="Reviews formatting, conventions, figures, and submission readiness only" active={!props.paper.simpleReviewPassed} />
            <FlowArrow label="review feedback targets" reverse />
            <AgentNode icon={<CheckCircle className="h-4 w-4" />} title="Writing Agent" desc="The only agent allowed to modify paper text" active />
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <Button onClick={props.generatePaper} disabled={props.generatingPaper}>
              {props.generatingPaper ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Network className="mr-1 h-4 w-4" />}
              Run writing loop
            </Button>
            <Button variant="outline" onClick={props.refresh}><RefreshCw className="mr-1 h-4 w-4" /> Refresh</Button>
            <Button variant="secondary" onClick={props.goResult}>Open Results</Button>
          </div>
        </CardContent>
      </Card>

      <Card className="xl:col-span-3">
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Revision Requests from Each Feedback Round</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {props.feedbackRounds.length === 0 ? (
            <div className="rounded-md border border-dashed p-4 text-sm text-slate-500">No feedback artifacts yet.</div>
          ) : (
            props.feedbackRounds.map((round, index) => (
              <div key={`${round.artifactPath}-${index}`} className="rounded-md border bg-white p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="outline">{round.source}</Badge>
                  {round.iteration && <Badge variant="outline">round {round.iteration}</Badge>}
                  <span className="font-mono text-xs text-slate-500">{round.artifactPath}</span>
                </div>
                <div className="mt-2 text-sm font-medium text-slate-800">{round.summary}</div>
                <FeedbackList title="Issues" items={round.issues.map(issue => `${issue.severity || 'issue'} ${issue.path || ''}: ${issue.message || ''}`)} />
                <FeedbackList title="Writing targets" items={round.targets.map(target => `${target.path || ''}: ${target.instruction || ''}`)} />
                {round.rewrites.length > 0 && (
                  <pre className="mt-2 max-h-36 overflow-auto rounded bg-slate-50 p-2 text-[11px] text-slate-700">{jsonPreview(round.rewrites, 900)}</pre>
                )}
              </div>
            ))
          )}
          <div className="rounded-md border bg-slate-50 p-3">
            <div className="mb-2 text-xs font-medium text-slate-600">Generation logs</div>
            <div className="max-h-56 space-y-1 overflow-auto text-xs">
              {props.logs.length === 0 ? <div className="text-slate-500">No logs</div> : props.logs.map((log, index) => (
                <div key={`${log.timestamp}-${index}`} className="grid grid-cols-[84px_1fr] gap-2">
                  <span className="text-slate-500">{new Date(log.timestamp).toLocaleTimeString()}</span>
                  <span>{log.message}</span>
                </div>
              ))}
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

function ResultStage(props: {
  paper: PaperRecord
  files: PaperFile[]
  selectedFile: string
  selectedFileRecord?: PaperFile
  fileContent: string
  loadFile: (path: string) => void
  pdfTs: number
  refreshFiles: () => void
}) {
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-12">
      <Card className="xl:col-span-3">
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Paper files</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <Button variant="outline" size="sm" onClick={props.refreshFiles}><RefreshCw className="mr-1 h-4 w-4" /> Refresh</Button>
          <div className="max-h-[58vh] overflow-auto rounded-md border">
            {props.files.length === 0 ? <div className="p-3 text-sm text-slate-500">No files yet.</div> : props.files.map(file => (
              <button
                key={file.path}
                className={`block w-full truncate px-3 py-2 text-left text-xs ${props.selectedFile === file.path ? 'bg-indigo-50 font-medium text-indigo-800' : 'hover:bg-slate-50'}`}
                onClick={() => props.loadFile(file.path)}
                title={file.path}
              >
                {file.path}
              </button>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card className="xl:col-span-4">
        <CardHeader className="pb-3">
          <CardTitle className="text-base">{props.selectedFile || 'File preview'}</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="mb-2 text-xs text-slate-500">{props.selectedFileRecord ? `${props.selectedFileRecord.size} bytes` : ''}</div>
          <pre className="max-h-[64vh] overflow-auto whitespace-pre-wrap rounded-md border bg-slate-950 p-3 text-xs text-slate-100">{props.fileContent || 'Select a text file to preview.'}</pre>
        </CardContent>
      </Card>

      <Card className="xl:col-span-5">
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Paper preview</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-2">
            {props.paper.pdfAvailable && (
              <>
                <a href={`${API_BASE}/api/v1/papers/${props.paper.id}/pdf?t=${props.pdfTs}`} target="_blank" rel="noopener noreferrer">
                  <Button variant="outline" size="sm"><Eye className="mr-1 h-4 w-4" /> Open PDF</Button>
                </a>
                <a href={`${API_BASE}/api/v1/papers/${props.paper.id}/download/pdf`} target="_blank" rel="noopener noreferrer">
                  <Button variant="outline" size="sm"><Download className="mr-1 h-4 w-4" /> PDF</Button>
                </a>
              </>
            )}
            <a href={`${API_BASE}/api/v1/papers/${props.paper.id}/download/latex.zip`} target="_blank" rel="noopener noreferrer">
              <Button variant="outline" size="sm"><Download className="mr-1 h-4 w-4" /> LaTeX ZIP</Button>
            </a>
          </div>
          {props.paper.pdfAvailable ? (
            <iframe src={`${API_BASE}/api/v1/papers/${props.paper.id}/pdf?t=${props.pdfTs}`} className="h-[62vh] w-full rounded-md border" title="PDF Preview" />
          ) : (
            <div className="flex h-[62vh] items-center justify-center rounded-md border border-dashed text-sm text-slate-500">PDF not available.</div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[96px_1fr] gap-2 text-sm">
      <span className="text-slate-500">{label}</span>
      <span className="font-medium text-slate-800">{value}</span>
    </div>
  )
}

function SelectBlock(props: { label: string; value: string; onChange: (value: string) => void; options: { value: string; label: string }[]; emptyLabel: string }) {
  return (
    <div className="space-y-1">
      <label className="text-xs font-medium text-slate-600">{props.label}</label>
      <select className="w-full rounded-md border bg-white px-2 py-2 text-sm" value={props.value} onChange={event => props.onChange(event.target.value)}>
        <option value="">{props.emptyLabel}</option>
        {props.options.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
    </div>
  )
}

function ChecklistBlock(props: { label: string; values: string[]; setValues: (values: string[]) => void; items: { value: string; label: string }[] }) {
  return (
    <div className="space-y-1">
      <div className="text-xs font-medium text-slate-600">{props.label}</div>
      <div className="max-h-36 space-y-1 overflow-auto rounded-md border p-2">
        {props.items.length === 0 ? <div className="text-xs text-slate-500">No items</div> : props.items.map(item => (
          <label key={item.value} className="flex items-start gap-2 text-xs">
            <input type="checkbox" checked={props.values.includes(item.value)} onChange={() => props.setValues(toggleList(props.values, item.value))} />
            <span>{item.label}</span>
          </label>
        ))}
      </div>
    </div>
  )
}

function LinkedEvidence({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="rounded-md border bg-white p-3">
      <div className="mb-2 text-xs font-medium text-slate-600">{title}</div>
      {items.length === 0 ? <div className="text-xs text-slate-500">None linked</div> : (
        <ul className="list-disc space-y-1 pl-4 text-xs text-slate-700">
          {items.map(item => <li key={item}>{item}</li>)}
        </ul>
      )}
    </div>
  )
}

function SummaryBlock({ title, value }: { title: string; value: unknown }) {
  return (
    <div>
      <div className="text-xs font-medium text-slate-600">{title}</div>
      <div className="mt-1 whitespace-pre-wrap text-sm text-slate-800">{toText(value)}</div>
    </div>
  )
}

function ListBlock({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null
  return (
    <div>
      <div className="text-xs font-medium text-slate-600">{title}</div>
      <ul className="mt-1 list-disc space-y-1 pl-5 text-sm text-slate-800">
        {items.map(item => <li key={item}>{item}</li>)}
      </ul>
    </div>
  )
}

function AgentNode({ icon, title, desc, active }: { icon: React.ReactNode; title: string; desc: string; active: boolean }) {
  return (
    <div className={`rounded-md border p-3 ${active ? 'border-indigo-300 bg-indigo-50' : 'bg-white'}`}>
      <div className="flex items-center gap-2 font-medium text-slate-900">{icon}{title}</div>
      <div className="mt-1 text-xs text-slate-600">{desc}</div>
    </div>
  )
}

function FlowArrow({ label, reverse = false }: { label: string; reverse?: boolean }) {
  return (
    <div className="flex items-center gap-2 px-3 text-xs text-slate-500">
      <GitBranch className={`h-4 w-4 ${reverse ? 'rotate-180' : ''}`} />
      <span>{label}</span>
    </div>
  )
}

function FeedbackList({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null
  return (
    <div className="mt-2">
      <div className="text-xs font-medium text-slate-600">{title}</div>
      <ul className="mt-1 list-disc space-y-1 pl-5 text-xs text-slate-700">
        {items.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}
      </ul>
    </div>
  )
}
