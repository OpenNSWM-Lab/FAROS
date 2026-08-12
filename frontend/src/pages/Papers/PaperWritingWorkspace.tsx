import { useCallback, useEffect, useState } from 'react'
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, ArrowRight, BookOpen, Clock, Code2, Download, Eye, FileText, Loader2, Network, RefreshCw, Save, ScrollText } from 'lucide-react'
import { AppPageLayout } from '@/components/layout/AppPageLayout'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { LLM_PROVIDERS, getModelsByProvider } from '@/lib/models/providers'
import { paperDisplayStatusClass, paperDisplayStatusLabel } from './paperStatus'

const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

type Stage = 'start' | 'brief' | 'writing' | 'result'

const STAGES: { id: Stage; label: string; description: string }[] = [
  { id: 'start', label: 'Start', description: 'Paper, template, links, evidence' },
  { id: 'brief', label: 'Brief', description: 'Paper and section brief' },
  { id: 'writing', label: 'Feedback Writing', description: 'Agent loop and revision requests' },
  { id: 'result', label: 'Results', description: 'Files and PDF preview' },
]

const PAPER_TYPE_OPTIONS = ['algorithm', 'application', 'survey', 'benchmark', 'system', 'security', 'position']
const TEMPLATE_FALLBACK_OPTIONS = ['icml', 'neurips', 'iclr', 'acl', 'generic', 'challenge_cup']

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
  rawSource?: string
  artifactPath: string
  artifactId?: string
  loopRound?: number
  iteration?: number
  issues: FeedbackIssue[]
  targets: FeedbackTarget[]
  rewrites: unknown[]
  summary: string
}

interface StepEvent {
  time: string
  owner: string
  step: string
  duration?: string
}

interface FeedbackLoopItem {
  label: string
  source: 'latex_compile' | 'simple_review'
  status?: 'success' | 'issues' | 'failed'
  feedback?: FeedbackRound
  detail?: string
}

interface FeedbackLoopRound {
  round: number
  items: FeedbackLoopItem[]
}

interface AgentTransfer {
  from: string
  to: string
  label: string
  kind: string
  content: unknown
  artifactPath?: string
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
  const [contextProjectId, setContextProjectId] = useState('')
  const [contextRunIds, setContextRunIds] = useState<string[]>([])
  const [contextExperimentIds, setContextExperimentIds] = useState<string[]>([])
  const [savingContext, setSavingContext] = useState(false)
  const [savingMetadata, setSavingMetadata] = useState(false)
  const [feedbackRounds, setFeedbackRounds] = useState<FeedbackRound[]>([])
  const [pdfTs, setPdfTs] = useState(Date.now())
  const [loading, setLoading] = useState(true)
  const [draftTitle, setDraftTitle] = useState('')
  const [draftPaperType, setDraftPaperType] = useState('algorithm')
  const [selectedTemplate, setSelectedTemplate] = useState('generic')
  const [draftProvider, setDraftProvider] = useState('')
  const [draftModel, setDraftModel] = useState('')

  const refreshPaper = useCallback(async () => {
    if (!id) return null
    const resp = await fetch(`${API_BASE}/api/v1/papers/${id}`)
    if (!resp.ok) return null
    const data = await resp.json()
    setPaper(data)
    setBriefUserEdits(data.briefUserEdits || '')
    setDraftTitle(data.title || '')
    setDraftPaperType(data.paperType || 'algorithm')
    const templateId = data.templateId || data.targetVenue || 'generic'
    setSelectedTemplate(templateId)
    setDraftProvider(data.providerName || '')
    setDraftModel(data.model || '')
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
        file.path.startsWith('artifacts/feedback/')
        || file.path.includes('latex_compile_agent')
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
      const artifactMeta = parsed._artifact as Record<string, unknown> | undefined
      const artifactId = typeof artifactMeta?.id === 'string' ? artifactMeta.id : ''
      const artifactRound = Number(artifact.path.match(/artifacts\/feedback\/round_(\d+)\//)?.[1]) || Number(parsed.round) || undefined
      const feedbackSource = artifactId.includes('latex_compile')
        || artifactId.includes('review_compile')
        || artifact.path.includes('/compile')
        || artifact.path.includes('latex_compile_agent')
        || artifact.path.includes('simple_review_compile_agent')
        ? 'latex_compile'
        : artifactId.includes('simple_review')
        || artifact.path.includes('/review')
        || artifact.path.includes('simple_review')
          ? 'simple_review'
          : 'latex_compile'
      const reviews = Array.isArray(parsed.reviews)
        ? parsed.reviews as Record<string, unknown>[]
        : (Array.isArray(parsed.issues) || Array.isArray(parsed.targets) || typeof parsed.passed === 'boolean')
          ? [parsed]
          : []
      if (reviews.length === 0 && (artifact.path.endsWith('/compile.json') || artifact.path.endsWith('/review.json'))) {
        rounds.push({
          source: feedbackSource,
          rawSource: typeof parsed.source === 'string' ? parsed.source : undefined,
          artifactPath: artifact.path,
          artifactId,
          loopRound: artifactRound,
          iteration: artifactRound,
          issues: [],
          targets: [],
          rewrites: [],
          summary: `${artifact.path} round ${artifactRound || ''}`,
        })
      }
      for (const review of reviews) {
        rounds.push({
          source: feedbackSource,
          rawSource: typeof review.source === 'string' ? review.source : undefined,
          artifactPath: artifact.path,
          artifactId,
          loopRound: artifactRound,
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
          artifactId,
          loopRound: artifactRound,
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

  const saveMetadata = async () => {
    if (!paper) return
    setSavingMetadata(true)
    try {
      const templateVenue = selectedTemplate || paper.templateId || paper.targetVenue || 'generic'
      const resp = await fetch(`${API_BASE}/api/v1/papers/${paper.id}/metadata`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: draftTitle,
          paperType: draftPaperType,
          targetVenue: templateVenue,
          providerName: draftProvider || undefined,
          model: draftModel || undefined,
          templateId: templateVenue,
        }),
      })
      if (resp.ok) await refreshPaper()
    } finally {
      setSavingMetadata(false)
    }
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
          templates={templates}
          draftTitle={draftTitle}
          setDraftTitle={setDraftTitle}
          draftPaperType={draftPaperType}
          setDraftPaperType={setDraftPaperType}
          selectedTemplate={selectedTemplate}
          setSelectedTemplate={(value) => {
            const templateId = value || 'generic'
            setSelectedTemplate(templateId)
          }}
          draftProvider={draftProvider}
          setDraftProvider={(value) => {
            setDraftProvider(value)
            setDraftModel(getModelsByProvider(value)[0]?.id || '')
          }}
          draftModel={draftModel}
          setDraftModel={setDraftModel}
          saveMetadata={saveMetadata}
          savingMetadata={savingMetadata}
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
          {paper && <Badge variant="outline" className={paperDisplayStatusClass(paper)}>{paperDisplayStatusLabel(paper)}</Badge>}
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
  templates: TemplateInfo[]
  draftTitle: string
  setDraftTitle: (value: string) => void
  draftPaperType: string
  setDraftPaperType: (value: string) => void
  selectedTemplate: string
  setSelectedTemplate: (value: string) => void
  draftProvider: string
  setDraftProvider: (value: string) => void
  draftModel: string
  setDraftModel: (value: string) => void
  saveMetadata: () => void
  savingMetadata: boolean
}) {
  const evidence = props.paper.evidenceJson
  const modelOptions = getModelsByProvider(props.draftProvider)
  const templateOptions = props.templates.length
    ? props.templates.map(template => ({ value: template.id, label: template.name || template.id }))
    : TEMPLATE_FALLBACK_OPTIONS.map(templateId => ({ value: templateId, label: templateId }))
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
      <Card className="xl:col-span-1">
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Paper & Template</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <div className="space-y-1">
            <label className="text-xs font-medium text-slate-600">Title</label>
            <input
              className="w-full rounded-md border bg-white px-2 py-2 text-sm"
              value={props.draftTitle}
              onChange={event => props.setDraftTitle(event.target.value)}
              placeholder="Paper title"
            />
          </div>
          <SelectBlock
            label="Type"
            value={props.draftPaperType}
            onChange={props.setDraftPaperType}
            options={PAPER_TYPE_OPTIONS.map(type => ({ value: type, label: type }))}
            emptyLabel="Select type"
          />
          <SelectBlock
            label="Template"
            value={props.selectedTemplate}
            onChange={props.setSelectedTemplate}
            options={templateOptions}
            emptyLabel="Select template"
          />
          <SelectBlock
            label="Provider"
            value={props.draftProvider}
            onChange={props.setDraftProvider}
            options={LLM_PROVIDERS.map(provider => ({ value: provider.id, label: provider.id }))}
            emptyLabel="Select provider"
          />
          <SelectBlock
            label="Model"
            value={props.draftModel}
            onChange={props.setDraftModel}
            options={modelOptions.map(model => ({ value: model.id, label: model.id }))}
            emptyLabel="Select model"
          />
          <Button size="sm" variant="outline" className="w-full" onClick={props.saveMetadata} disabled={props.savingMetadata || !props.draftTitle.trim()}>
            {props.savingMetadata ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Save className="mr-1 h-4 w-4" />}
            Save paper settings
          </Button>
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
  const stepEvents = parseStepEvents(props.logs)
  const loopRounds = buildFeedbackLoopRounds(props.feedbackRounds, props.logs)
  const transfers = buildAgentTransfers(props.feedbackRounds, props.paper, props.logs)

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-5">
      <Card className="xl:col-span-2">
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Agent Interaction Diagram</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
              <AgentNode icon={<FileText className="h-4 w-4" />} title="Writing Agent" desc="Drafts and performs all revisions" active />
              <AgentNode icon={<Code2 className="h-4 w-4" />} title="LaTeX Compile Agent" desc="Compiles and reports feedback only" active={props.paper.compileStatus !== 'latexmk'} />
              <AgentNode icon={<ScrollText className="h-4 w-4" />} title="Simple Review Agent" desc="Checks format and submission readiness" active={!props.paper.simpleReviewPassed} />
            </div>
            <div className="space-y-2 rounded-md border bg-slate-50 p-3">
              {transfers.length === 0 ? (
                <div className="rounded-md border border-dashed bg-white p-3 text-sm text-slate-500">No agent handoff has been recorded yet.</div>
              ) : (
                transfers.map((transfer, index) => (
                  <TransferRow key={`${transfer.from}-${transfer.to}-${index}`} transfer={transfer} index={index + 1} />
                ))
              )}
            </div>
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
          <CardTitle className="text-base">Writing Steps, Timing, and Feedback</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="rounded-md border bg-white p-3">
            <div className="mb-2 flex items-center gap-2 text-xs font-medium text-slate-600">
              <Clock className="h-4 w-4" /> Step timeline
            </div>
            {stepEvents.length === 0 ? (
              <div className="text-sm text-slate-500">No step timing has been recorded yet.</div>
            ) : (
              <div className="space-y-2">
                {stepEvents.map((event, index) => (
                  <div key={`${event.time}-${event.owner}-${event.step}-${index}`} className="flex items-center justify-between gap-3 rounded-md border bg-slate-50 px-3 py-2">
                    <div className="truncate text-sm font-medium text-slate-800">{formatStepEvent(event)}</div>
                    <Badge variant="outline" className="shrink-0">{event.duration || 'running'}</Badge>
                  </div>
                ))}
              </div>
            )}
          </div>

          {loopRounds.length === 0 ? (
            <div className="rounded-md border border-dashed p-4 text-sm text-slate-500">No feedback artifacts yet.</div>
          ) : (
            loopRounds.map(group => (
              <details key={`feedback-round-${group.round}`} className="rounded-md border bg-white p-3">
                <summary className="cursor-pointer">
                  <div className="inline-flex flex-wrap items-center gap-2">
                    <Badge variant="outline">round {group.round}</Badge>
                    <span className="text-sm font-medium text-slate-800">{summarizeFeedbackLoopRound(group)}</span>
                  </div>
                </summary>
                <div className="mt-3 space-y-2">
                  {group.items.map((item, index) => (
                    <FeedbackLoopItemView key={`${group.round}-${item.label}-${index}`} item={item} index={index + 1} />
                  ))}
                </div>
              </details>
            ))
          )}
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

function compactOwner(owner: string): string {
  const normalized = owner.replace(/_agent$/i, '')
  if (normalized === 'latex_compile') return 'compile'
  if (normalized === 'simple_review') return 'review'
  if (normalized === 'paper') return 'orchestrator'
  return normalized
}

function compactStep(step: string): string {
  return step
    .replace(/^run$/, 'run')
    .replace(/_agent$/i, '')
}

function formatStepEvent(event: StepEvent): string {
  return `${compactOwner(event.owner)}/${compactStep(event.step)}`
}

function parseStepEvents(logs: PaperLog[]): StepEvent[] {
  const starts = new Map<string, PaperLog>()
  const events: StepEvent[] = []
  logs.forEach(log => {
    const agentMatch = log.message.match(/^([^:]+): running skill ([A-Za-z0-9_]+)/)
    if (agentMatch) {
      starts.set(`${agentMatch[1]}/${agentMatch[2]}`, log)
      return
    }

    const completionMatch = log.message.match(/^([^/]+)\/([^:]+):\s*(.*?)(?:\s+\(([\d.]+s)\))?$/)
    if (completionMatch) {
      events.push({
        time: log.timestamp,
        owner: completionMatch[1],
        step: completionMatch[2],
        duration: completionMatch[4],
      })
      starts.delete(`${completionMatch[1]}/${completionMatch[2]}`)
      Array.from(starts.keys())
        .filter(key => key.startsWith(`${completionMatch[1]}/`))
        .forEach(key => starts.delete(key))
      return
    }

    const legacyMatch = log.message.match(/^([A-Za-z0-9_]+):\s*(.*?)(?:\s+\(([\d.]+s)\))?$/)
    if (legacyMatch && legacyMatch[3] && !log.message.startsWith('Artifacts:')) {
      events.push({
        time: log.timestamp,
        owner: 'pipeline',
        step: legacyMatch[1],
        duration: legacyMatch[3],
      })
    }
  })

  starts.forEach((log, key) => {
    const [owner, step] = key.split('/')
    events.push({ time: log.timestamp, owner, step })
  })

  return events
}

function sortedFeedbackRounds(feedbackRounds: FeedbackRound[]): FeedbackRound[] {
  return [...feedbackRounds].sort((a, b) => {
    const sourceOrder = (round: FeedbackRound) => {
      if (round.artifactId === '09_latex_compile_agent' || round.artifactPath.endsWith('/compile.json') || round.artifactPath.includes('09_latex_compile_agent')) return 0
      if (round.artifactId === '10_simple_review_compile_agent' || round.artifactPath.endsWith('/review_compile.json') || round.artifactPath.includes('10_simple_review_compile_agent')) return 1
      if (round.source === 'simple_review') return 2
      return 3
    }
    return sourceOrder(a) - sourceOrder(b) || (a.iteration || 0) - (b.iteration || 0) || a.artifactPath.localeCompare(b.artifactPath)
  })
}

function getLoopRound(map: Map<number, FeedbackLoopRound>, round: number): FeedbackLoopRound {
  const normalized = Math.max(1, round)
  const existing = map.get(normalized)
  if (existing) return existing
  const created = { round: normalized, items: [] }
  map.set(normalized, created)
  return created
}

function buildFeedbackLoopRounds(feedbackRounds: FeedbackRound[], logs: PaperLog[]): FeedbackLoopRound[] {
  const feedbackItems = sortedFeedbackRounds(feedbackRounds).filter(round => round.source !== 'writing_rewrite' && round.rawSource !== 'evidence_usage')
  const compileQueue = feedbackItems.filter(round => round.source === 'latex_compile')
  const reviewQueue = feedbackItems.filter(round => round.source === 'simple_review')
  const used = new Set<FeedbackRound>()
  const groups = new Map<number, FeedbackLoopRound>()
  let currentRound = 1
  let pendingCompileItems: FeedbackLoopItem[] = []

  logs.forEach(log => {
    const compileResult = log.message.match(/^latex_compile_agent\/latex_compile_once:\s*([^()]+?)(?:\s+\(([\d.]+s)\))?$/)
    if (compileResult) {
      const status = compileResult[1].trim()
      const duration = compileResult[2]
      const feedback = compileQueue.find(item => !used.has(item) && item.loopRound === currentRound)
        || compileQueue.find(item => !used.has(item) && !item.loopRound)
        || compileQueue.find(item => !used.has(item))
      if (feedback) used.add(feedback)
      pendingCompileItems.push({
        label: 'compile feedback',
        source: 'latex_compile',
        status: feedback ? feedbackStatus(feedback) : status.startsWith('latexmk') ? 'success' : status.startsWith('failed') ? 'failed' : undefined,
        feedback,
        detail: feedback
          ? feedbackEmptyMessage(feedback, 'latex_compile') || undefined
          : status.startsWith('latexmk')
          ? `No compile feedback; LaTeX passed${duration ? ` in ${duration}` : ''}.`
          : `compile ${status}${duration ? `, ${duration}` : ''}`,
      })
      return
    }

    const compileFeedback = log.message.match(/^latex_compile_agent: requesting feedback round/)
    if (compileFeedback) {
      const feedback = compileQueue.find(item => !used.has(item) && item.loopRound === currentRound)
        || compileQueue.find(item => !used.has(item))
      if (feedback) used.add(feedback)
      const lastCompile = pendingCompileItems[pendingCompileItems.length - 1]
      if (lastCompile && !lastCompile.feedback) {
        lastCompile.feedback = feedback
        lastCompile.status = feedback ? feedbackStatus(feedback) : lastCompile.status
        lastCompile.detail = feedback ? feedbackEmptyMessage(feedback, 'latex_compile') : lastCompile.detail
      } else {
        pendingCompileItems.push({
          label: 'compile feedback',
          source: 'latex_compile',
          status: feedback ? feedbackStatus(feedback) : undefined,
          feedback,
          detail: feedback ? feedbackEmptyMessage(feedback, 'latex_compile') : undefined,
        })
      }
      return
    }

    const reviewFeedback = log.message.match(/^simple_review_agent: requesting feedback round (\d+)/)
    if (reviewFeedback) {
      const round = Number(reviewFeedback[1]) || currentRound
      const feedback = reviewQueue.find(item => !used.has(item) && (!item.iteration || item.iteration === round))
        || reviewQueue.find(item => !used.has(item))
      if (feedback) used.add(feedback)
      const group = getLoopRound(groups, round)
      group.items.push(...pendingCompileItems)
      pendingCompileItems = []
      group.items.push({
        label: 'review feedback',
        source: 'simple_review',
        status: feedback ? feedbackStatus(feedback) : undefined,
        feedback,
        detail: feedback ? feedbackEmptyMessage(feedback, 'simple_review') : undefined,
      })
      currentRound = round + 1
    }
  })

  feedbackItems.forEach(item => {
    if (used.has(item)) return
    const round = item.source === 'simple_review'
      ? item.loopRound || item.iteration || currentRound
      : item.loopRound
        ? item.loopRound
        : item.artifactId === '09_latex_compile_agent' || item.artifactPath.endsWith('/compile.json') || item.artifactPath.includes('09_latex_compile_agent')
        ? 1
        : Math.max(1, currentRound)
    getLoopRound(groups, round).items.push({
      label: item.source === 'latex_compile' ? 'compile feedback' : 'review feedback',
      source: item.source === 'latex_compile' ? 'latex_compile' : 'simple_review',
      status: feedbackStatus(item),
      feedback: item,
    })
  })

  if (pendingCompileItems.length > 0) {
    getLoopRound(groups, Math.max(1, currentRound - 1)).items.push(...pendingCompileItems)
  }

  return [...groups.values()]
    .filter(group => group.items.length > 0)
    .map(group => ({ ...group, items: sortFeedbackLoopItems(group.items) }))
    .sort((a, b) => a.round - b.round)
}

function sortFeedbackLoopItems(items: FeedbackLoopItem[]): FeedbackLoopItem[] {
  const itemOrder = (item: FeedbackLoopItem) => {
    if (item.source === 'latex_compile') return 0
    if (item.source === 'simple_review') return 1
    return 2
  }
  return [...items].sort((a, b) => itemOrder(a) - itemOrder(b))
}

function summarizeFeedbackLoopRound(round: FeedbackLoopRound): string {
  return round.items.map(item => `${item.label}${item.status === 'success' ? ' success' : ''}`).join('; ')
}

function feedbackHasBlockingOrMajor(feedback: FeedbackRound): boolean {
  return feedback.issues.some(issue => ['blocking', 'major'].includes(String(issue.severity || '').toLowerCase()))
}

function feedbackStatus(feedback: FeedbackRound): 'success' | 'issues' {
  return feedbackHasBlockingOrMajor(feedback) ? 'issues' : 'success'
}

function feedbackEmptyMessage(feedback: FeedbackRound, source: 'latex_compile' | 'simple_review'): string {
  if (feedback.issues.length > 0 || feedback.targets.length > 0) return ''
  return source === 'latex_compile'
    ? 'No compile feedback; LaTeX passed or no writing-side repair target was needed.'
    : 'No review feedback; no presentation, format, figure, table, or artifact-usage revision was requested.'
}

function statusBadgeClass(status?: FeedbackLoopItem['status']) {
  if (status === 'success') return 'border-emerald-300 bg-emerald-50 text-emerald-700'
  if (status === 'failed' || status === 'issues') return 'border-amber-300 bg-amber-50 text-amber-700'
  return ''
}

function FeedbackLoopItemView({ item, index }: { item: FeedbackLoopItem; index: number }) {
  const feedback = item.feedback
  return (
    <details className="rounded-md border bg-slate-50 p-3">
      <summary className="cursor-pointer">
        <div className="inline-flex flex-wrap items-center gap-2">
          <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-white text-[11px] font-medium text-slate-600">{index}</span>
          <Badge variant="outline">{item.label}</Badge>
          {item.status && <Badge variant="outline" className={statusBadgeClass(item.status)}>{item.status === 'issues' ? 'needs revision' : item.status}</Badge>}
          {feedback?.iteration && <Badge variant="outline">feedback {feedback.iteration}</Badge>}
          {feedback?.artifactPath && <span className="font-mono text-xs text-slate-500">{feedback.artifactPath}</span>}
        </div>
      </summary>
      {feedback ? (
        <div className="mt-3">
          {item.detail && <div className="mb-2 text-xs text-slate-500">{item.detail}</div>}
          <FeedbackList title="Issues" items={feedback.issues.map(issue => `${issue.severity || 'issue'} ${issue.path || ''}: ${issue.message || ''}`)} />
          <FeedbackList title="Writing targets" items={feedback.targets.map(target => `${target.path || ''}: ${target.instruction || ''}`)} />
        </div>
      ) : item.detail ? (
        <div className="mt-2 text-xs text-slate-500">{item.detail}</div>
      ) : (
        <div className="mt-2 text-xs text-slate-500">
          {item.source === 'latex_compile'
            ? 'No compile feedback; LaTeX passed or no writing-side repair target was needed.'
            : 'No review feedback is available yet; no modification request was recorded for this step.'}
        </div>
      )}
    </details>
  )
}

function buildAgentTransfers(feedbackRounds: FeedbackRound[], paper: PaperRecord, logs: PaperLog[]): AgentTransfer[] {
  const transfers: AgentTransfer[] = []
  const compileStarted = logs.some(log =>
    log.message.includes('starting LaTeX compile agent')
    || log.message.startsWith('latex_compile_agent:')
    || log.message.startsWith('latex_compile_agent/')
  ) || feedbackRounds.some(round => round.source === 'latex_compile') || Boolean(paper.compileStatus)

  if (compileStarted) {
    transfers.push({
      from: 'Writing Agent',
      to: 'LaTeX Compile Agent',
      label: 'TeX source files after writing',
      kind: 'paper_ready_for_compile',
      content: {
        title: paper.title,
        status: paper.status,
        compileStatus: paper.compileStatus || 'not compiled',
        recentSteps: parseStepEvents(logs).slice(-6),
      },
    })
  }

  feedbackRounds.forEach(round => {
    if (round.source === 'latex_compile') {
      transfers.push({
        from: 'LaTeX Compile Agent',
        to: 'Writing Agent',
        label: `Compile feedback${round.iteration ? ` round ${round.iteration}` : ''}`,
        kind: 'compile_feedback',
        artifactPath: round.artifactPath,
        content: { issues: round.issues, targets: round.targets },
      })
    }
    if (round.source === 'writing_rewrite') {
      transfers.push({
        from: 'Writing Agent',
        to: 'LaTeX Compile Agent',
        label: round.artifactId === 'feedback_rewrite_simple_review' || round.artifactPath.includes('rewrite_review') || round.artifactPath.includes('simple_review')
          ? 'Review-driven rewrite; return to compile step'
          : 'Compile-driven rewrite; return to compile step',
        kind: 'rewrite_result',
        artifactPath: round.artifactPath,
        content: round.rewrites,
      })
    }
    if (round.source === 'simple_review') {
      transfers.push({
        from: 'Simple Review Agent',
        to: 'Writing Agent',
        label: `Simple review feedback${round.iteration ? ` round ${round.iteration}` : ''}`,
        kind: 'simple_review_feedback',
        artifactPath: round.artifactPath,
        content: { issues: round.issues, targets: round.targets },
      })
    }
  })

  if (paper.compileStatus === 'latexmk') {
    transfers.push({
      from: 'LaTeX Compile Agent',
      to: 'Simple Review Agent',
      label: 'Compiled PDF and TeX files',
      kind: 'paper_ready_for_review',
      content: {
        title: paper.title,
        simpleReviewPassed: Boolean(paper.simpleReviewPassed),
        pdfAvailable: Boolean(paper.pdfAvailable),
        compileStatus: paper.compileStatus,
      },
    })
  }

  return transfers
}

function TransferRow({ transfer, index }: { transfer: AgentTransfer; index: number }) {
  return (
    <details className="rounded-md border bg-white p-3">
      <summary className="cursor-pointer">
        <div className="grid grid-cols-1 gap-2 text-sm lg:grid-cols-[48px_1fr_28px_1fr] lg:items-center">
          <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-indigo-50 text-xs font-medium text-indigo-700">{index}</span>
          <div>
            <div className="text-[10px] uppercase text-slate-500">Source agent</div>
            <div className="font-medium text-slate-800">{transfer.from}</div>
          </div>
          <ArrowRight className="hidden h-4 w-4 text-slate-400 lg:block" />
          <div>
            <div className="text-[10px] uppercase text-slate-500">Target agent</div>
            <div className="font-medium text-slate-800">{transfer.to}</div>
          </div>
        </div>
      </summary>
      <div className="mt-2 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline">{transfer.kind}</Badge>
          <span className="text-sm font-medium text-slate-800">{transfer.label}</span>
        </div>
        <div className="text-xs text-slate-500">{summarizeTransfer(transfer)}</div>
        {transfer.artifactPath && <div className="font-mono text-xs text-slate-500">{transfer.artifactPath}</div>}
        <pre className="max-h-52 overflow-auto rounded bg-slate-50 p-2 text-[11px] text-slate-700">{jsonPreview(transfer.content, 1200)}</pre>
      </div>
    </details>
  )
}

function summarizeTransfer(transfer: AgentTransfer): string {
  if (transfer.kind.includes('feedback') && typeof transfer.content === 'object' && transfer.content) {
    const data = transfer.content as { issues?: unknown[]; targets?: unknown[] }
    return `${data.issues?.length || 0} issues, ${data.targets?.length || 0} writing targets`
  }
  if (Array.isArray(transfer.content)) {
    return `${transfer.content.length} writing rewrite records`
  }
  if (typeof transfer.content === 'object' && transfer.content) {
    const data = transfer.content as Record<string, unknown>
    return Object.entries(data).slice(0, 3).map(([key, value]) => `${key}: ${toText(value).split('\n')[0]}`).join(' · ')
  }
  return toText(transfer.content).split('\n')[0]
}
