import { useState, useEffect, useCallback } from 'react'
import { AppPageLayout } from '@/components/layout/AppPageLayout'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { BookOpen, Plus, Download, Code2, Loader2, RefreshCw, Save, Eye, Copy, CheckCircle, ImagePlus, FileText, ListTree, Trash2 } from 'lucide-react'
import { LLM_PROVIDERS, getModelsByProvider } from '@/lib/models/providers'

const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

interface TemplateInfo {
  id: string
  name: string
  description: string
  sections: string[]
  bibStyle: string
}

const VENUES = ['icml', 'neurips', 'iclr', 'acl', 'generic']

interface PaperBrief {
  research_question?: string
  core_claim?: string
  paper_angle?: string
  target_audience?: string
  contributions?: unknown[]
  must_use_evidence?: unknown[]
  must_use_figures?: unknown[]
  avoid_claims?: unknown[]
  section_priorities?: Record<string, unknown>
  [key: string]: unknown
}

interface PaperOutlineSection {
  id: string
  title: string
  keyPoints: string[]
  minWords: number
  hasAlgorithm: boolean
  hasEquations: boolean
  numEquations: number
  hasTables: boolean
  hasFigures: boolean
  figureDescriptions: string[]
}

interface PaperOutline {
  title: string
  authors: string[]
  abstract: string
  sections: PaperOutlineSection[]
  references?: unknown[]
  algorithms?: unknown[]
  contributions: string[]
  [key: string]: unknown
}

interface PaperRecord {
  id: string
  title: string
  paperType: string
  targetVenue?: string
  status: string
  planLinkId?: string
  projectId?: string
  experimentIds: string[]
  figureIds: string[]
  runIds: string[]
  providerName: string
  model: string
  pdfAvailable?: boolean
  briefJson?: PaperBrief | null
  briefUserEdits?: string
  briefStatus?: string
  outlineJson?: PaperOutline | null
  outlineStatus?: string
  evidenceGates?: Record<string, unknown>
  sectionCount?: number
  referenceCount?: number
  figureCount?: number
  logs: { timestamp: string; message: string }[]
  fileCount?: number
  createdAt: string
  updatedAt: string
}

interface PaperFile {
  path: string
  name: string
  size: number
  isDir: boolean
}

interface Figure {
  id: string
  experimentId: string
  figureType: string
  title?: string
  caption: string
  fileNamePng?: string
  fileNamePdf?: string
  pathPng?: string
  pathPdf?: string
}

interface Experiment {
  id: string
  name: string
}

interface CodeProject {
  id: string
  title: string
  description?: string
  language?: string
  framework?: string
}

interface RunArtifact {
  id: string
  type: string
  filename?: string
  size?: number
}

interface RunRecord {
  id: string
  status: string
  type: string
  duration?: number
  errorMessage?: string
  artifacts?: RunArtifact[]
  config?: {
    model?: string
    workplaceName?: string
  }
}

const PAPER_TYPES = ['algorithm', 'application', 'survey', 'benchmark', 'system', 'security', 'position']
const statusColors: Record<string, string> = {
  created: 'bg-gray-100 text-gray-800',
  generating: 'bg-blue-100 text-blue-800',
  completed: 'bg-green-100 text-green-800',
  failed: 'bg-red-100 text-red-800',
}

const toStringList = (value: unknown): string[] => {
  if (!Array.isArray(value)) return []
  return value.map(item => String(item || '').trim()).filter(Boolean)
}

const listToText = (value: string[] = []) => value.join('\n')

const textToList = (value: string) => value.split('\n').map(item => item.trim()).filter(Boolean)

const cleanSectionId = (value: string, fallback: string) => {
  const cleaned = value.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
  return cleaned || fallback
}

const createBlankSection = (index: number): PaperOutlineSection => ({
  id: `section_${index}`,
  title: `Section ${index}`,
  keyPoints: [],
  minWords: 500,
  hasAlgorithm: false,
  hasEquations: false,
  numEquations: 0,
  hasTables: false,
  hasFigures: false,
  figureDescriptions: [],
})

const normalizeOutline = (outline: PaperOutline | null | undefined, fallbackTitle: string): PaperOutline | null => {
  if (!outline || typeof outline !== 'object') return null
  const raw = outline as Record<string, unknown>
  const rawSections = Array.isArray(raw.sections) ? raw.sections : []
  const sections = rawSections.map((section, idx) => {
    const item = (section || {}) as Record<string, unknown>
    const title = String(item.title || `Section ${idx + 1}`)
    const id = cleanSectionId(String(item.id || title), `section_${idx + 1}`)
    const minWords = Number(item.minWords)
    const numEquations = Number(item.numEquations)
    return {
      id,
      title,
      keyPoints: toStringList(item.keyPoints),
      minWords: Number.isFinite(minWords) ? Math.max(150, Math.round(minWords)) : 500,
      hasAlgorithm: Boolean(item.hasAlgorithm),
      hasEquations: Boolean(item.hasEquations),
      numEquations: Number.isFinite(numEquations) ? Math.max(0, Math.round(numEquations)) : 0,
      hasTables: Boolean(item.hasTables),
      hasFigures: Boolean(item.hasFigures),
      figureDescriptions: toStringList(item.figureDescriptions),
    }
  })

  return {
    ...outline,
    title: String(raw.title || fallbackTitle || 'Untitled Paper'),
    authors: toStringList(raw.authors).length > 0 ? toStringList(raw.authors) : ['Auto-LLM Draft'],
    abstract: String(raw.abstract || ''),
    sections,
    references: Array.isArray(raw.references) ? raw.references : [],
    algorithms: Array.isArray(raw.algorithms) ? raw.algorithms : [],
    contributions: toStringList(raw.contributions),
  }
}

export function PapersList() {
  const [papers, setPapers] = useState<PaperRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedPaper, setSelectedPaper] = useState<PaperRecord | null>(null)
  const [paperFiles, setPaperFiles] = useState<PaperFile[]>([])
  const [fileContent, setFileContent] = useState('')
  const [editedContent, setEditedContent] = useState('')
  const [selectedFile, setSelectedFile] = useState('')
  const [creating, setCreating] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [saving, setSaving] = useState(false)
  const [pdfAvailable, setPdfAvailable] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [pdfTs, setPdfTs] = useState(0)

  // Templates
  const [templates, setTemplates] = useState<TemplateInfo[]>([])
  const [applyingTemplate, setApplyingTemplate] = useState(false)

  // Figures management
  const [experiments, setExperiments] = useState<Experiment[]>([])
  const [projects, setProjects] = useState<CodeProject[]>([])
  const [runs, setRuns] = useState<RunRecord[]>([])
  const [selectedExperiment, setSelectedExperiment] = useState<string>('')
  const [experimentFigures, setExperimentFigures] = useState<Figure[]>([])
  const [paperFigures, setPaperFigures] = useState<Figure[]>([])
  const [loadingFigures, setLoadingFigures] = useState(false)
  const [addingFigure, setAddingFigure] = useState(false)
  const [copiedLatex, setCopiedLatex] = useState<string>('')
  const [activeTab, setActiveTab] = useState<'files' | 'figures'>('files')
  const [renderingPdf, setRenderingPdf] = useState(false)

  // Create form
  const [showCreate, setShowCreate] = useState(false)
  const [newTitle, setNewTitle] = useState('My Research Paper')
  const [newType, setNewType] = useState('algorithm')
  const [newProvider, setNewProvider] = useState('')
  const [newModel, setNewModel] = useState('')
  const [newTemplate, setNewTemplate] = useState('generic')
  const [newVenue, setNewVenue] = useState('generic')
  const [newProjectId, setNewProjectId] = useState('')
  const [newRunIds, setNewRunIds] = useState<string[]>([])
  const [newExperimentIds, setNewExperimentIds] = useState<string[]>([])
  const [newNotes, setNewNotes] = useState('')
  const [contextProjectId, setContextProjectId] = useState('')
  const [contextRunIds, setContextRunIds] = useState<string[]>([])
  const [contextExperimentIds, setContextExperimentIds] = useState<string[]>([])
  const [savingContext, setSavingContext] = useState(false)
  const [briefUserEdits, setBriefUserEdits] = useState('')
  const [generatingBrief, setGeneratingBrief] = useState(false)
  const [savingBrief, setSavingBrief] = useState(false)
  const [outlineDraft, setOutlineDraft] = useState<PaperOutline | null>(null)
  const [outlineDirty, setOutlineDirty] = useState(false)
  const [generatingOutline, setGeneratingOutline] = useState(false)
  const [savingOutline, setSavingOutline] = useState(false)

  const fetchPapers = useCallback(async () => {
    try {
      const resp = await fetch(`${API_BASE}/api/v1/papers`)
      if (resp.ok) {
        const data = await resp.json()
        setPapers(data.papers || [])
      }
    } catch (err) { console.error(err) }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { fetchPapers() }, [fetchPapers])
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const resp = await fetch(`${API_BASE}/api/v1/providers`)
        if (!resp.ok || cancelled) return
        const data = await resp.json()
        const providerName = data.activeProvider || ''
        const providerInfo = (data.providers || []).find((p: { providerName: string; model: string }) => p.providerName === providerName)
        if (!cancelled) {
          setNewProvider(providerName)
          setNewModel(providerInfo?.model || getModelsByProvider(providerName)[0]?.id || '')
        }
      } catch {
        if (!cancelled) {
          setNewProvider('')
          setNewModel('')
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])


  // Load templates
  useEffect(() => {
    fetch(`${API_BASE}/api/v1/templates`)
      .then(r => r.json())
      .then(data => setTemplates(data.templates || []))
      .catch(() => { void 0 })
  }, [])

  const toggleSelection = (current: string[], value: string) => {
    return current.includes(value) ? current.filter(item => item !== value) : [...current, value]
  }

  // Load experiments
  const fetchExperiments = async () => {
    try {
      const resp = await fetch(`${API_BASE}/api/v1/experiments`)
      if (resp.ok) {
        const data = await resp.json()
        setExperiments(data.experiments || [])
      }
    } catch (err) { console.error(err) }
  }

  const fetchProjects = async () => {
    try {
      const resp = await fetch(`${API_BASE}/api/v1/code/projects`)
      if (resp.ok) {
        const data = await resp.json()
        setProjects(data.projects || [])
      }
    } catch (err) { console.error(err) }
  }

  const fetchRuns = async () => {
    try {
      const resp = await fetch(`${API_BASE}/api/v1/runs`)
      if (resp.ok) {
        const data = await resp.json()
        setRuns(data.runs || [])
      }
    } catch (err) { console.error(err) }
  }

  // Load experiment figures
  const fetchExperimentFigures = async (expId: string) => {
    setLoadingFigures(true)
    try {
      if (expId) {
        const resp = await fetch(`${API_BASE}/api/v1/experiments/${expId}/figures`)
        if (resp.ok) {
          const data = await resp.json()
          setExperimentFigures(data.figures || [])
        }
      } else {
        setExperimentFigures([])
      }
    } catch (err) { console.error(err) }
    finally { setLoadingFigures(false) }
  }

  // Load paper figures
  const fetchPaperFigures = async (paperId: string) => {
    try {
      const resp = await fetch(`${API_BASE}/api/v1/papers/${paperId}/figures`)
      if (resp.ok) {
        const data = await resp.json()
        setPaperFigures(data.figures || [])
      }
    } catch (err) { console.error(err) }
  }

  // Add figure to paper
  const addFigureToPaper = async (figureId: string) => {
    if (!selectedPaper) return
    setAddingFigure(true)
    try {
      const resp = await fetch(`${API_BASE}/api/v1/papers/${selectedPaper.id}/figures`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ figureId }),
      })
      if (resp.ok) {
        await fetchPaperFigures(selectedPaper.id)
        await selectPaper(selectedPaper)
      }
    } catch (err) { console.error(err) }
    finally { setAddingFigure(false) }
  }

  // Copy LaTeX reference
  const copyLatexRef = async (figureId: string) => {
    try {
      const resp = await fetch(`${API_BASE}/api/v1/papers/figures/${figureId}/latex-ref`)
      if (resp.ok) {
        const data = await resp.json()
        await navigator.clipboard.writeText(data.latex)
        setCopiedLatex(figureId)
        setTimeout(() => setCopiedLatex(''), 2000)
      }
    } catch (err) { console.error(err) }
  }

  // Render PDF preview
  const renderPdfPreview = async () => {
    if (!selectedPaper) return
    setRenderingPdf(true)
    try {
      const resp = await fetch(`${API_BASE}/api/v1/papers/${selectedPaper.id}/render-pdf`, {
        method: 'POST',
      })
      if (resp.ok) {
        // First set a timeout to ensure the PDF starts generating
        // Then start polling
        let attempts = 0
        const pollInterval = setInterval(async () => {
          attempts++
          try {
            const checkResp = await fetch(`${API_BASE}/api/v1/papers/${selectedPaper.id}`)
            if (checkResp.ok) {
              const updatedPaper = await checkResp.json()
              // Update selectedPaper first
              await selectPaper(updatedPaper)
              
              if (updatedPaper.pdfAvailable) {
                clearInterval(pollInterval)
                // Update timestamp to force iframe reload
                setPdfTs(Date.now())
              }
            }
          } catch (err) {
            console.error(err)
          }
          
          // Stop polling after 45 seconds (45 attempts)
          if (attempts >= 45) {
            clearInterval(pollInterval)
            setPdfTs(Date.now()) // Try to refresh anyway
          }
        }, 1000)
        
        // Immediately update timestamp to trigger iframe refresh
        setTimeout(() => setPdfTs(Date.now()), 500)
      }
    } catch (err) { 
      console.error(err)
      // Even if there's an error, try to refresh the iframe
      setPdfTs(Date.now())
    }
    finally {
      setTimeout(() => setRenderingPdf(false), 2000)
    }
  }

  // Load experiments on mount
  useEffect(() => {
    fetchExperiments()
    fetchProjects()
    fetchRuns()
  }, [])

  useEffect(() => {
    setContextProjectId(selectedPaper?.projectId || '')
    setContextRunIds(selectedPaper?.runIds || [])
    setContextExperimentIds(selectedPaper?.experimentIds || [])
    setBriefUserEdits(selectedPaper?.briefUserEdits || '')
    setOutlineDraft(normalizeOutline(selectedPaper?.outlineJson, selectedPaper?.title || 'Untitled Paper'))
    setOutlineDirty(false)
  }, [selectedPaper])

  const selectPaper = async (p: PaperRecord) => {
    // Refresh paper metadata
    try {
      const mResp = await fetch(`${API_BASE}/api/v1/papers/${p.id}`)
      if (mResp.ok) { p = await mResp.json() }
    } catch { void 0 }
    setSelectedPaper(p)
    setSelectedFile('')
    setFileContent('')
    setEditedContent('')
    setDirty(false)
    setPdfAvailable(false)
    try {
      const resp = await fetch(`${API_BASE}/api/v1/papers/${p.id}/tree`)
      if (resp.ok) {
        const data = await resp.json()
        const entries: PaperFile[] = data.entries || []
        setPaperFiles(entries)
        // Check if PDF exists
        const hasPdf = entries.some((f: PaperFile) => f.name === 'main.pdf')
        setPdfAvailable(hasPdf)
        if (hasPdf) setPdfTs(Date.now())
        // Auto-load main.tex if it exists
        const hasMainTex = entries.some((f: PaperFile) => f.path === 'main.tex')
        if (hasMainTex) {
          setSelectedFile('main.tex')
          try {
            const fResp = await fetch(`${API_BASE}/api/v1/papers/${p.id}/files?path=main.tex`)
            if (fResp.ok) {
              const fData = await fResp.json()
              setFileContent(fData.content || '')
              setEditedContent(fData.content || '')
            }
          } catch { void 0 }
        }
      }
    } catch (err) { console.error(err) }
    await fetchPaperFigures(p.id)
  }

  const loadFile = async (path: string) => {
    if (!selectedPaper) return
    if (dirty && !confirm('Discard unsaved changes?')) return
    setSelectedFile(path)
    setDirty(false)
    try {
      const resp = await fetch(`${API_BASE}/api/v1/papers/${selectedPaper.id}/files?path=${encodeURIComponent(path)}`)
      if (resp.ok) {
        const data = await resp.json()
        setFileContent(data.content || '')
        setEditedContent(data.content || '')
      }
    } catch (err) { console.error(err) }
  }

  const saveFile = async () => {
    if (!selectedPaper || !selectedFile) return
    setSaving(true)
    try {
      const resp = await fetch(`${API_BASE}/api/v1/papers/${selectedPaper.id}/files`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: selectedFile, content: editedContent }),
      })
      if (resp.ok) {
        setFileContent(editedContent)
        setDirty(false)
      }
    } catch (err) { console.error(err) }
    finally { setSaving(false) }
  }

  const savePaperContext = async () => {
    if (!selectedPaper) return
    setSavingContext(true)
    try {
      const resp = await fetch(`${API_BASE}/api/v1/papers/${selectedPaper.id}/context`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          projectId: contextProjectId || undefined,
          runIds: contextRunIds,
          experimentIds: contextExperimentIds,
        }),
      })
      if (resp.ok) {
        const data = await resp.json()
        setSelectedPaper(data)
        await fetchPapers()
      }
    } catch (err) { console.error(err) }
    finally { setSavingContext(false) }
  }

  const generateBrief = async () => {
    if (!selectedPaper) return
    setGeneratingBrief(true)
    try {
      const resp = await fetch(`${API_BASE}/api/v1/papers/${selectedPaper.id}/brief/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ briefUserEdits, force: true }),
      })
      if (resp.ok) {
        const data = await resp.json()
        const updated = {
          ...selectedPaper,
          briefJson: data.brief,
          briefUserEdits: data.briefUserEdits || '',
          briefStatus: data.briefStatus,
        }
        setSelectedPaper(updated)
        setBriefUserEdits(data.briefUserEdits || '')
        await fetchPapers()
      }
    } catch (err) { console.error(err) }
    finally { setGeneratingBrief(false) }
  }

  const saveBrief = async () => {
    if (!selectedPaper) return
    setSavingBrief(true)
    try {
      const resp = await fetch(`${API_BASE}/api/v1/papers/${selectedPaper.id}/brief`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ briefUserEdits }),
      })
      if (resp.ok) {
        const data = await resp.json()
        setSelectedPaper({
          ...selectedPaper,
          briefJson: data.brief,
          briefUserEdits: data.briefUserEdits || '',
          briefStatus: data.briefStatus,
        })
        await fetchPapers()
      }
    } catch (err) { console.error(err) }
    finally { setSavingBrief(false) }
  }

  const updateOutline = (updates: Partial<PaperOutline>) => {
    setOutlineDraft(current => current ? { ...current, ...updates } : current)
    setOutlineDirty(true)
  }

  const updateOutlineSection = (index: number, updates: Partial<PaperOutlineSection>) => {
    setOutlineDraft(current => {
      if (!current) return current
      const sections = current.sections.map((section, idx) => (
        idx === index ? { ...section, ...updates } : section
      ))
      return { ...current, sections }
    })
    setOutlineDirty(true)
  }

  const addOutlineSection = () => {
    setOutlineDraft(current => {
      if (!current) return current
      return {
        ...current,
        sections: [...current.sections, createBlankSection(current.sections.length + 1)],
      }
    })
    setOutlineDirty(true)
  }

  const removeOutlineSection = (index: number) => {
    setOutlineDraft(current => {
      if (!current) return current
      return {
        ...current,
        sections: current.sections.filter((_, idx) => idx !== index),
      }
    })
    setOutlineDirty(true)
  }

  const persistOutlineDraft = async (skipIfClean = true) => {
    if (!selectedPaper || !outlineDraft) return true
    if (skipIfClean && !outlineDirty) return true
    setSavingOutline(true)
    try {
      const resp = await fetch(`${API_BASE}/api/v1/papers/${selectedPaper.id}/outline`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ outlineJson: outlineDraft }),
      })
      if (!resp.ok) return false
      const data = await resp.json()
      const normalized = normalizeOutline(data.outline, selectedPaper.title)
      const updated = {
        ...selectedPaper,
        outlineJson: normalized,
        outlineStatus: data.outlineStatus,
      }
      setSelectedPaper(updated)
      setOutlineDraft(normalized)
      setOutlineDirty(false)
      await fetchPapers()
      return true
    } catch (err) {
      console.error(err)
      return false
    } finally {
      setSavingOutline(false)
    }
  }

  const saveOutline = async () => {
    await persistOutlineDraft(false)
  }

  const generateOutline = async () => {
    if (!selectedPaper) return
    setGeneratingOutline(true)
    try {
      if (briefUserEdits !== (selectedPaper.briefUserEdits || '')) {
        await fetch(`${API_BASE}/api/v1/papers/${selectedPaper.id}/brief`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ briefUserEdits }),
        }).catch(() => { void 0 })
      }

      const resp = await fetch(`${API_BASE}/api/v1/papers/${selectedPaper.id}/outline/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ force: true }),
      })
      if (resp.ok) {
        const data = await resp.json()
        const normalized = normalizeOutline(data.outline, selectedPaper.title)
        const updated = {
          ...selectedPaper,
          briefUserEdits,
          outlineJson: normalized,
          outlineStatus: data.outlineStatus,
        }
        setSelectedPaper(updated)
        setOutlineDraft(normalized)
        setOutlineDirty(false)
        await fetchPapers()
      }
    } catch (err) { console.error(err) }
    finally { setGeneratingOutline(false) }
  }

  const createPaper = async () => {
    setCreating(true)
    try {
      const resp = await fetch(`${API_BASE}/api/v1/papers`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: newTitle,
          paperType: newType,
          targetVenue: newVenue,
          providerName: newProvider || undefined,
          model: newModel || undefined,
          projectId: newProjectId || undefined,
          runIds: newRunIds,
          experimentIds: newExperimentIds,
          notes: newNotes || undefined,
        }),
      })
      if (resp.ok) {
        const data = await resp.json()
        // Apply template if selected
        if (newTemplate) {
          await fetch(`${API_BASE}/api/v1/templates/apply`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ paperId: data.id, templateId: newTemplate, title: newTitle }),
          }).catch(() => { void 0 })
        }
        await fetchPapers()
        setShowCreate(false)
        selectPaper(data)
      }
    } catch (err) { console.error(err) }
    finally { setCreating(false) }
  }

  const applyTemplate = async (templateId: string) => {
    if (!selectedPaper) return
    setApplyingTemplate(true)
    try {
      const resp = await fetch(`${API_BASE}/api/v1/templates/apply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paperId: selectedPaper.id, templateId, title: selectedPaper.title }),
      })
      if (resp.ok) {
        await selectPaper(selectedPaper)
      }
    } catch (err) { console.error(err) }
    finally { setApplyingTemplate(false) }
  }

  const generatePaper = async () => {
    if (!selectedPaper) return
    setGenerating(true)
    try {
      if (outlineDraft) {
        const saved = await persistOutlineDraft(true)
        if (!saved) return
      }
      await fetch(`${API_BASE}/api/v1/papers/${selectedPaper.id}/generate`, { method: 'POST' })
      for (let i = 0; i < 60; i++) {
        await new Promise(r => setTimeout(r, 3000))
        const resp = await fetch(`${API_BASE}/api/v1/papers/${selectedPaper.id}`)
        if (resp.ok) {
          const data = await resp.json()
          setSelectedPaper(data)
          if (data.status === 'completed' || data.status === 'failed') {
            await selectPaper(data)
            break
          }
        }
      }
    } catch (err) { console.error(err) }
    finally { setGenerating(false); fetchPapers() }
  }

  const brief = selectedPaper?.briefJson || null
  const outline = outlineDraft
  const briefItems = (items: unknown): string[] => {
    if (!Array.isArray(items)) return []
    return items.map(item => {
      if (typeof item === 'string') return item
      if (item && typeof item === 'object') {
        const obj = item as Record<string, unknown>
        return [obj.label, obj.path, obj.caption, obj.target_section].filter(Boolean).join(' | ')
      }
      return String(item)
    }).filter(Boolean)
  }

  const allFiles = paperFiles.filter(f => !f.isDir)
  const isEditable = selectedFile.endsWith('.tex') || selectedFile.endsWith('.bib') || selectedFile.endsWith('.md')

  return (
    <AppPageLayout
      title="Papers"
      subtitle="Generate research-grade papers with PDF preview and LaTeX download"
      icon={BookOpen}
      iconColor="indigo"
      accentColor="indigo"
      actions={
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => setShowCreate(!showCreate)}>
            <Plus className="h-4 w-4 mr-1" /> New
          </Button>
          <Button variant="outline" size="sm" onClick={fetchPapers} disabled={loading}>
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
          </Button>
        </div>
      }
    >
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-4" style={{ minHeight: '70vh' }}>
        {/* Left Panel: Create + List */}
        <div className="space-y-3">
          {showCreate && (
            <Card className="border-indigo-200">
              <CardContent className="pt-3 space-y-2">
                <input className="w-full border rounded px-2 py-1.5 text-sm" placeholder="Title" value={newTitle} onChange={e => setNewTitle(e.target.value)} />
                <select className="w-full border rounded px-2 py-1.5 text-sm" value={newType} onChange={e => setNewType(e.target.value)}>
                  {PAPER_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                </select>
                <select className="w-full border rounded px-2 py-1.5 text-sm" value={newVenue} onChange={e => setNewVenue(e.target.value)}>
                  {VENUES.map(v => <option key={v} value={v}>{v.toUpperCase()}</option>)}
                </select>
                <select className="w-full border rounded px-2 py-1.5 text-sm" value={newTemplate} onChange={e => setNewTemplate(e.target.value)}>
                  <option value="">No template</option>
                  {templates.map(t => <option key={t.id} value={t.id}>{t.name} — {t.description}</option>)}
                </select>
                <div className="grid grid-cols-2 gap-1">
                  <select className="border rounded px-2 py-1 text-xs" value={newProvider} onChange={e => { const provider = e.target.value; setNewProvider(provider); setNewModel(getModelsByProvider(provider)[0]?.id || "") }}>
                    <option value="">Select provider</option>
                    {LLM_PROVIDERS.map(provider => <option key={provider.id} value={provider.id}>{provider.name}</option>)}
                  </select>
                  <select className="border rounded px-2 py-1 text-xs" value={newModel} onChange={e => setNewModel(e.target.value)}>
                    {getModelsByProvider(newProvider).map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
                  </select>
                </div>
                <select className="w-full border rounded px-2 py-1.5 text-sm" value={newProjectId} onChange={e => setNewProjectId(e.target.value)}>
                  <option value="">No linked project</option>
                  {projects.map(project => <option key={project.id} value={project.id}>{project.title} ({project.id})</option>)}
                </select>
                <textarea
                  className="w-full border rounded px-2 py-1.5 text-xs resize-none"
                  rows={3}
                  placeholder="Writing intent / notes"
                  value={newNotes}
                  onChange={e => setNewNotes(e.target.value)}
                />
                <div className="space-y-1">
                  <div className="text-[11px] font-medium text-muted-foreground">Link runs</div>
                  <div className="max-h-24 overflow-y-auto border rounded p-1 space-y-1">
                    {runs.length === 0 ? <div className="text-[11px] text-muted-foreground">No runs available</div> : runs.map(run => (
                      <label key={run.id} className="flex items-start gap-2 text-[11px] cursor-pointer">
                        <input type="checkbox" checked={newRunIds.includes(run.id)} onChange={() => setNewRunIds(current => toggleSelection(current, run.id))} />
                        <span className="truncate">{run.id} [{run.status}] {run.config?.model || run.type}</span>
                      </label>
                    ))}
                  </div>
                </div>
                <div className="space-y-1">
                  <div className="text-[11px] font-medium text-muted-foreground">Link experiments</div>
                  <div className="max-h-24 overflow-y-auto border rounded p-1 space-y-1">
                    {experiments.length === 0 ? <div className="text-[11px] text-muted-foreground">No experiments available</div> : experiments.map(exp => (
                      <label key={exp.id} className="flex items-start gap-2 text-[11px] cursor-pointer">
                        <input type="checkbox" checked={newExperimentIds.includes(exp.id)} onChange={() => setNewExperimentIds(current => toggleSelection(current, exp.id))} />
                        <span className="truncate">{exp.name} ({exp.id})</span>
                      </label>
                    ))}
                  </div>
                </div>
                <Button size="sm" className="w-full" onClick={createPaper} disabled={creating}>
                  {creating ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : <Plus className="h-3 w-3 mr-1" />} Create
                </Button>
              </CardContent>
            </Card>
          )}

          <div className="text-xs font-medium text-muted-foreground">{papers.length} papers</div>
          {loading ? (
            <div className="flex justify-center py-8"><Loader2 className="h-5 w-5 animate-spin text-indigo-500" /></div>
          ) : (
            <div className="space-y-1.5 max-h-[65vh] overflow-y-auto">
              {papers.map(p => (
                <div
                  key={p.id}
                  className={`p-2.5 rounded-lg border cursor-pointer transition-colors ${selectedPaper?.id === p.id ? 'border-indigo-400 bg-indigo-50' : 'hover:bg-muted/50'}`}
                  onClick={() => selectPaper(p)}
                >
                  <div className="flex items-start justify-between gap-1">
                    <span className="text-xs font-medium truncate">{p.title}</span>
                    <span className={`px-1 py-0.5 rounded text-[10px] font-medium shrink-0 ${statusColors[p.status] || 'bg-gray-100'}`}>{p.status}</span>
                  </div>
                  <div className="flex items-center gap-1.5 mt-1 text-[10px] text-muted-foreground">
                    <Badge variant="outline" className="text-[10px] py-0">{p.paperType}</Badge>
                    {p.targetVenue && <Badge variant="outline" className="text-[10px] py-0">{p.targetVenue.toUpperCase()}</Badge>}
                    <span>{new Date(p.createdAt).toLocaleDateString()}</span>
                    {p.pdfAvailable && <span className="text-green-600 font-medium">PDF</span>}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Center+Right: 3-panel Paper Viewer */}
        {!selectedPaper ? (
          <div className="lg:col-span-3 flex items-center justify-center">
            <div className="text-center text-muted-foreground">
              <BookOpen className="h-12 w-12 mx-auto mb-3 opacity-30" />
              <p className="text-sm">Select a paper to view</p>
            </div>
          </div>
        ) : (
          <div className="lg:col-span-3 flex flex-col gap-3">
            {/* Header bar */}
            <div className="flex items-center justify-between bg-white border rounded-lg px-4 py-2">
              <div className="flex items-center gap-3">
                <span className="font-medium text-sm truncate max-w-xs">{selectedPaper.title}</span>
                <Badge variant="outline" className="text-xs">{selectedPaper.paperType}</Badge>
                <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${statusColors[selectedPaper.status] || 'bg-gray-100'}`}>{selectedPaper.status}</span>
              </div>
              <div className="flex items-center gap-1.5">
                {templates.length > 0 && (selectedPaper.status === 'created' || selectedPaper.status === 'completed') && (
                  <select
                    className="border rounded px-1.5 py-0.5 text-xs h-7 bg-white"
                    value=""
                    onChange={e => { if (e.target.value) applyTemplate(e.target.value) }}
                    disabled={applyingTemplate}
                  >
                    <option value="">{applyingTemplate ? 'Applying...' : 'Apply Template'}</option>
                    {templates.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
                  </select>
                )}
                {selectedPaper.status !== 'generating' && (
                  <Button size="sm" variant="outline" onClick={generateBrief} disabled={generatingBrief} className="h-7 text-xs">
                    {generatingBrief ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : <FileText className="h-3 w-3 mr-1" />}
                    Brief
                  </Button>
                )}
                {selectedPaper.status !== 'generating' && (
                  <Button size="sm" variant="outline" onClick={generateOutline} disabled={generatingOutline} className="h-7 text-xs">
                    {generatingOutline ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : <ListTree className="h-3 w-3 mr-1" />}
                    Outline
                  </Button>
                )}
                {(selectedPaper.status === 'created' || selectedPaper.status === 'failed') && (
                  <Button size="sm" onClick={generatePaper} disabled={generating} className="h-7 text-xs">
                    {generating ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : <Code2 className="h-3 w-3 mr-1" />}
                    Generate
                  </Button>
                )}
                {selectedPaper.status === 'completed' && (
                  <>
                    {pdfAvailable && (
                      <a href={`${API_BASE}/api/v1/papers/${selectedPaper.id}/pdf`} target="_blank" rel="noopener noreferrer">
                        <Button size="sm" variant="outline" className="h-7 text-xs"><Eye className="h-3 w-3 mr-1" /> View PDF</Button>
                      </a>
                    )}
                    <a href={`${API_BASE}/api/v1/papers/${selectedPaper.id}/download/latex.zip`} target="_blank" rel="noopener noreferrer">
                      <Button size="sm" variant="outline" className="h-7 text-xs"><Download className="h-3 w-3 mr-1" /> LaTeX ZIP</Button>
                    </a>
                    {pdfAvailable && (
                      <a href={`${API_BASE}/api/v1/papers/${selectedPaper.id}/download/pdf`} target="_blank" rel="noopener noreferrer">
                        <Button size="sm" variant="outline" className="h-7 text-xs"><Download className="h-3 w-3 mr-1" /> Download PDF</Button>
                      </a>
                    )}
                  </>
                )}
              </div>
            </div>

            {generating && (
              <div className="flex items-center gap-2 p-3 bg-blue-50 rounded-lg text-sm text-blue-700">
                <Loader2 className="h-4 w-4 animate-spin" /> Generating paper... This may take a few minutes.
              </div>
            )}

            {/* Main 3-panel area */}
            <div className="grid grid-cols-12 gap-3 flex-1 min-h-0">
              {/* Left: File tree + Figures management */}
              <div className="col-span-2 border rounded-lg overflow-hidden bg-white flex flex-col" style={{ maxHeight: '60vh' }}>
                <div className="flex border-b">
                  <button
                    className={`flex-1 px-2 py-1.5 text-xs font-medium border-r ${activeTab === 'files' ? 'bg-indigo-50 text-indigo-700' : 'text-muted-foreground hover:bg-slate-50'}`}
                    onClick={() => setActiveTab('files')}
                  >
                    Files
                  </button>
                  <button
                    className={`flex-1 px-2 py-1.5 text-xs font-medium ${activeTab === 'figures' ? 'bg-indigo-50 text-indigo-700' : 'text-muted-foreground hover:bg-slate-50'}`}
                    onClick={() => setActiveTab('figures')}
                  >
                    Figures
                  </button>
                </div>
                <div className="flex-1 overflow-y-auto">
                  {activeTab === 'files' ? (
                    <>
                      {allFiles.length === 0 ? (
                        <p className="text-xs text-muted-foreground p-2">No files yet</p>
                      ) : (
                        <div className="p-1 space-y-0.5">
                          {allFiles.map(f => (
                            <button
                              key={f.path}
                              className={`w-full text-left px-1.5 py-1 rounded text-[11px] truncate block ${selectedFile === f.path ? 'bg-indigo-100 text-indigo-800 font-medium' : 'hover:bg-muted/50 text-muted-foreground'}`}
                              onClick={() => loadFile(f.path)}
                              title={f.path}
                            >
                              {f.path}
                            </button>
                          ))}
                        </div>
                      )}
                    </>
                  ) : (
                    <div className="p-2 space-y-3">
                      <div>
                        <div className="text-xs font-medium text-muted-foreground mb-1">Add Figure</div>
                        {experiments.length === 0 ? (
                          <p className="text-xs text-muted-foreground">No experiments available. Create an experiment first.</p>
                        ) : (
                          <>
                            <select
                              className="w-full border rounded px-2 py-1.5 text-xs mb-1"
                              value={selectedExperiment}
                              onChange={(e) => {
                                setSelectedExperiment(e.target.value)
                                if (e.target.value) fetchExperimentFigures(e.target.value)
                                else setExperimentFigures([])
                              }}
                            >
                              <option value="">Select experiment</option>
                              {experiments.map(exp => (
                                <option key={exp.id} value={exp.id}>{exp.name}</option>
                              ))}
                            </select>
                            {loadingFigures ? (
                              <div className="text-center py-2"><Loader2 className="h-4 w-4 animate-spin mx-auto" /></div>
                            ) : selectedExperiment && experimentFigures.length === 0 ? (
                              <p className="text-xs text-muted-foreground">No figures in this experiment</p>
                            ) : experimentFigures.length > 0 ? (
                              <div className="space-y-1 max-h-40 overflow-y-auto">
                                {experimentFigures.map(fig => (
                                  <button
                                    key={fig.id}
                                    className="w-full text-left px-2 py-1 text-xs rounded bg-slate-50 hover:bg-slate-100 flex items-center gap-1"
                                    onClick={() => addFigureToPaper(fig.id)}
                                    disabled={addingFigure}
                                  >
                                    <ImagePlus className="h-3 w-3" />
                                    <span className="truncate">{fig.title || fig.caption}</span>
                                  </button>
                                ))}
                              </div>
                            ) : null}
                          </>
                        )}
                      </div>
                      <div>
                        <div className="text-xs font-medium text-muted-foreground mb-1">Paper Figures ({paperFigures.length})</div>
                        {paperFigures.length === 0 ? (
                          <p className="text-xs text-muted-foreground">No figures added yet</p>
                        ) : (
                          <div className="space-y-2">
                            {paperFigures.map(fig => (
                              <div key={fig.id} className="border rounded p-1.5 bg-white">
                                <div className="text-xs font-medium truncate mb-1">{fig.title || fig.caption}</div>
                                <img
                                  src={`${API_BASE}/api/v1/experiments/figures/${fig.id}/png`}
                                  alt={fig.caption}
                                  className="w-full rounded mb-1 border"
                                  style={{ maxHeight: '80px', objectFit: 'contain' }}
                                  onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
                                />
                                <button
                                  className="w-full text-xs px-1 py-0.5 rounded bg-slate-50 hover:bg-slate-100 flex items-center justify-center gap-1"
                                  onClick={() => copyLatexRef(fig.id)}
                                >
                                  {copiedLatex === fig.id ? (
                                    <><CheckCircle className="h-3 w-3" /> Copied!</>
                                  ) : (
                                    <><Copy className="h-3 w-3" /> Copy LaTeX</>
                                  )}
                                </button>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              </div>

              {/* Center: Editor */}
              <div className="col-span-5 border rounded-lg overflow-hidden bg-white flex flex-col" style={{ maxHeight: '60vh' }}>
                <div className="px-3 py-1.5 border-b bg-slate-50 flex items-center justify-between">
                  <span className="text-xs font-mono text-muted-foreground truncate">{selectedFile || 'No file selected'}</span>
                  <div className="flex items-center gap-1">
                    {dirty && <span className="text-[10px] text-amber-600 font-medium">unsaved</span>}
                    {isEditable && dirty && (
                      <Button size="sm" variant="ghost" onClick={saveFile} disabled={saving} className="h-6 text-[10px] px-2">
                        {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3 mr-1" />} Save
                      </Button>
                    )}
                  </div>
                </div>
                <div className="flex-1 overflow-auto">
                  {!selectedFile ? (
                    <div className="flex items-center justify-center h-full text-sm text-muted-foreground">Select a file</div>
                  ) : isEditable ? (
                    <textarea
                      className="w-full h-full p-3 text-xs font-mono resize-none border-0 focus:outline-none"
                      style={{ minHeight: '50vh' }}
                      value={editedContent}
                      onChange={e => { setEditedContent(e.target.value); setDirty(e.target.value !== fileContent) }}
                      spellCheck={false}
                    />
                  ) : (
                    <pre className="p-3 text-xs font-mono whitespace-pre-wrap overflow-auto">{fileContent}</pre>
                  )}
                </div>
              </div>

              {/* Right: PDF preview + Metadata + Logs */}
              <div className="col-span-5 space-y-3" style={{ maxHeight: '60vh', overflowY: 'auto' }}>
                {/* PDF Preview */}
                <div className="border rounded-lg bg-white overflow-hidden">
                  <div className="px-3 py-1.5 border-b bg-slate-50 flex items-center justify-between">
                    <div className="text-xs font-medium text-muted-foreground flex items-center gap-1">
                      <Eye className="h-3 w-3" /> PDF Preview
                    </div>
                    {selectedPaper && (
                      <div className="flex items-center gap-1">
                        <Button
                          size="sm"
                          variant="secondary"
                          className="h-6 text-[10px] px-2 flex items-center gap-1"
                          onClick={renderPdfPreview}
                          disabled={renderingPdf}
                          title="Refresh Preview"
                        >
                          {renderingPdf ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            <RefreshCw className="h-3 w-3" />
                          )}
                          Refresh Preview
                        </Button>
                        {pdfAvailable && (
                          <>
                            <a
                              href={`${API_BASE}/api/v1/papers/${selectedPaper.id}/pdf?t=${pdfTs}`}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="inline-flex"
                            >
                              <Button size="sm" variant="ghost" className="h-6 text-[10px] px-1.5" title="Open in new window">
                                <Eye className="h-3 w-3" />
                              </Button>
                            </a>
                            <a
                              href={`${API_BASE}/api/v1/papers/${selectedPaper.id}/download/pdf`}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="inline-flex"
                            >
                              <Button size="sm" variant="ghost" className="h-6 text-[10px] px-1.5" title="Download PDF">
                                <Download className="h-3 w-3" />
                              </Button>
                            </a>
                          </>
                        )}
                      </div>
                    )}
                  </div>
                  {pdfAvailable ? (
                    <iframe
                      src={`${API_BASE}/api/v1/papers/${selectedPaper.id}/pdf?t=${pdfTs}`}
                      className="w-full border-0"
                      style={{ height: '35vh' }}
                      title="PDF Preview"
                    />
                  ) : (
                    <div className="p-4 text-center text-xs text-muted-foreground">
                      {selectedPaper.status === 'completed'
                        ? 'PDF is being generated...'
                        : selectedPaper.status === 'generating'
                          ? 'Paper generation in progress...'
                          : 'Generate the paper to see the PDF preview.'}
                    </div>
                  )}
                </div>

                {/* Evidence Sources */}
                <div className="border rounded-lg bg-white overflow-hidden">
                  <div className="px-3 py-1.5 border-b bg-slate-50 text-xs font-medium text-muted-foreground">Evidence Sources</div>
                  <div className="p-2 space-y-2 text-xs text-muted-foreground">
                    <div>
                      <div className="mb-1 font-medium">Project</div>
                      <select className="w-full border rounded px-2 py-1.5 text-xs" value={contextProjectId} onChange={e => setContextProjectId(e.target.value)}>
                        <option value="">No linked project</option>
                        {projects.map(project => <option key={project.id} value={project.id}>{project.title} ({project.id})</option>)}
                      </select>
                    </div>
                    <div>
                      <div className="mb-1 font-medium">Runs</div>
                      <div className="max-h-24 overflow-y-auto border rounded p-1 space-y-1">
                        {runs.length === 0 ? <div className="text-[11px] text-muted-foreground">No runs available</div> : runs.map(run => (
                          <label key={run.id} className="flex items-start gap-2 text-[11px] cursor-pointer">
                            <input type="checkbox" checked={contextRunIds.includes(run.id)} onChange={() => setContextRunIds(current => toggleSelection(current, run.id))} />
                            <span className="truncate">{run.id} [{run.status}] {run.config?.model || run.type}</span>
                          </label>
                        ))}
                      </div>
                    </div>
                    <div>
                      <div className="mb-1 font-medium">Experiments</div>
                      <div className="max-h-24 overflow-y-auto border rounded p-1 space-y-1">
                        {experiments.length === 0 ? <div className="text-[11px] text-muted-foreground">No experiments available</div> : experiments.map(exp => (
                          <label key={exp.id} className="flex items-start gap-2 text-[11px] cursor-pointer">
                            <input type="checkbox" checked={contextExperimentIds.includes(exp.id)} onChange={() => setContextExperimentIds(current => toggleSelection(current, exp.id))} />
                            <span className="truncate">{exp.name} ({exp.id})</span>
                          </label>
                        ))}
                      </div>
                    </div>
                    <Button size="sm" variant="outline" onClick={savePaperContext} disabled={savingContext} className="w-full h-7 text-xs">
                      {savingContext ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : <Save className="h-3 w-3 mr-1" />}
                      Save Evidence Links
                    </Button>
                    <div className="space-y-1 border-t pt-2">
                      <div><span className="font-medium">Linked project:</span> {selectedPaper.projectId || 'none'}</div>
                      <div><span className="font-medium">Linked runs:</span> {selectedPaper.runIds?.length || 0}</div>
                      <div><span className="font-medium">Linked experiments:</span> {selectedPaper.experimentIds?.length || 0}</div>
                    </div>
                    {selectedPaper.runIds && selectedPaper.runIds.length > 0 && (
                      <div className="space-y-1 border-t pt-2">
                        <div className="font-medium">Selected run details</div>
                        {runs.filter(run => selectedPaper.runIds.includes(run.id)).map(run => (
                          <div key={run.id} className="rounded border p-1 text-[11px]">
                            <div>{run.id} [{run.status}]</div>
                            <div>{run.config?.model || run.type}{run.duration != null ? ` · ${run.duration}s` : ''}</div>
                            <div>artifacts: {run.artifacts?.length || 0}{run.errorMessage ? ` · error: ${run.errorMessage}` : ''}</div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>

                {/* Writing Brief */}
                <div className="border rounded-lg bg-white overflow-hidden">
                  <div className="px-3 py-1.5 border-b bg-slate-50 flex items-center justify-between">
                    <div className="text-xs font-medium text-muted-foreground flex items-center gap-1">
                      <FileText className="h-3 w-3" /> Writing Brief
                    </div>
                    <Badge variant="outline" className="text-[10px]">{selectedPaper.briefStatus || 'missing'}</Badge>
                  </div>
                  <div className="p-2 space-y-2 text-xs text-muted-foreground">
                    {brief ? (
                      <div className="space-y-2">
                        <div>
                          <div className="font-medium text-slate-700">Research question</div>
                          <div className="mt-0.5 text-[11px]">{brief.research_question || 'N/A'}</div>
                        </div>
                        <div>
                          <div className="font-medium text-slate-700">Core claim</div>
                          <div className="mt-0.5 text-[11px]">{brief.core_claim || 'N/A'}</div>
                        </div>
                        {briefItems(brief.contributions).length > 0 && (
                          <div>
                            <div className="font-medium text-slate-700">Contributions</div>
                            <ul className="mt-0.5 list-disc pl-4 space-y-0.5 text-[11px]">
                              {briefItems(brief.contributions).slice(0, 4).map((item, idx) => <li key={idx}>{item}</li>)}
                            </ul>
                          </div>
                        )}
                        {briefItems(brief.must_use_evidence).length > 0 && (
                          <div>
                            <div className="font-medium text-slate-700">Evidence</div>
                            <ul className="mt-0.5 list-disc pl-4 space-y-0.5 text-[11px]">
                              {briefItems(brief.must_use_evidence).slice(0, 4).map((item, idx) => <li key={idx}>{item}</li>)}
                            </ul>
                          </div>
                        )}
                        {briefItems(brief.must_use_figures).length > 0 && (
                          <div>
                            <div className="font-medium text-slate-700">Figures</div>
                            <ul className="mt-0.5 list-disc pl-4 space-y-0.5 text-[11px]">
                              {briefItems(brief.must_use_figures).slice(0, 4).map((item, idx) => <li key={idx}>{item}</li>)}
                            </ul>
                          </div>
                        )}
                        {briefItems(brief.avoid_claims).length > 0 && (
                          <div>
                            <div className="font-medium text-slate-700">Avoid</div>
                            <ul className="mt-0.5 list-disc pl-4 space-y-0.5 text-[11px]">
                              {briefItems(brief.avoid_claims).slice(0, 3).map((item, idx) => <li key={idx}>{item}</li>)}
                            </ul>
                          </div>
                        )}
                      </div>
                    ) : (
                      <div className="rounded border border-dashed p-2 text-[11px]">No brief generated</div>
                    )}
                    <textarea
                      className="w-full border rounded px-2 py-1.5 text-xs resize-none"
                      rows={4}
                      placeholder="Additional writing instructions"
                      value={briefUserEdits}
                      onChange={e => setBriefUserEdits(e.target.value)}
                    />
                    <div className="grid grid-cols-2 gap-2">
                      <Button size="sm" variant="outline" onClick={saveBrief} disabled={savingBrief} className="h-7 text-xs">
                        {savingBrief ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : <Save className="h-3 w-3 mr-1" />}
                        Save
                      </Button>
                      <Button size="sm" variant="secondary" onClick={generateBrief} disabled={generatingBrief} className="h-7 text-xs">
                        {generatingBrief ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : <RefreshCw className="h-3 w-3 mr-1" />}
                        Generate
                      </Button>
                    </div>
                  </div>
                </div>

                {/* Editable Outline */}
                <div className="border rounded-lg bg-white overflow-hidden">
                  <div className="px-3 py-1.5 border-b bg-slate-50 flex items-center justify-between">
                    <div className="text-xs font-medium text-muted-foreground flex items-center gap-1">
                      <ListTree className="h-3 w-3" /> Outline
                    </div>
                    <div className="flex items-center gap-1">
                      {outlineDirty && <Badge variant="outline" className="text-[10px] text-amber-700 border-amber-300">unsaved</Badge>}
                      <Badge variant="outline" className="text-[10px]">{selectedPaper.outlineStatus || 'missing'}</Badge>
                    </div>
                  </div>
                  <div className="p-2 space-y-2 text-xs text-muted-foreground">
                    {outline ? (
                      <>
                        <div className="space-y-1">
                          <label className="text-[11px] font-medium text-slate-700">Title</label>
                          <input
                            className="w-full border rounded px-2 py-1.5 text-xs"
                            value={outline.title}
                            onChange={e => updateOutline({ title: e.target.value })}
                          />
                        </div>
                        <div className="space-y-1">
                          <label className="text-[11px] font-medium text-slate-700">Authors</label>
                          <textarea
                            className="w-full border rounded px-2 py-1.5 text-xs resize-none"
                            rows={2}
                            value={listToText(outline.authors)}
                            onChange={e => updateOutline({ authors: textToList(e.target.value) })}
                          />
                        </div>
                        <div className="space-y-1">
                          <label className="text-[11px] font-medium text-slate-700">Abstract</label>
                          <textarea
                            className="w-full border rounded px-2 py-1.5 text-xs resize-y"
                            rows={4}
                            value={outline.abstract}
                            onChange={e => updateOutline({ abstract: e.target.value })}
                          />
                        </div>
                        <div className="space-y-1">
                          <label className="text-[11px] font-medium text-slate-700">Contributions</label>
                          <textarea
                            className="w-full border rounded px-2 py-1.5 text-xs resize-none"
                            rows={3}
                            value={listToText(outline.contributions)}
                            onChange={e => updateOutline({ contributions: textToList(e.target.value) })}
                          />
                        </div>

                        <div className="space-y-2 border-t pt-2">
                          <div className="flex items-center justify-between">
                            <span className="text-[11px] font-medium text-slate-700">Sections ({outline.sections.length})</span>
                            <Button size="sm" variant="outline" onClick={addOutlineSection} className="h-6 text-[10px] px-2">
                              <Plus className="h-3 w-3 mr-1" /> Add
                            </Button>
                          </div>
                          <div className="space-y-2 max-h-96 overflow-y-auto pr-1">
                            {outline.sections.map((section, index) => (
                              <div key={`${section.id}-${index}`} className="rounded border p-2 bg-slate-50/60 space-y-2">
                                <div className="flex items-center gap-1">
                                  <span className="text-[10px] text-muted-foreground w-5">{index + 1}</span>
                                  <input
                                    className="flex-1 border rounded px-2 py-1 text-xs bg-white"
                                    value={section.title}
                                    onChange={e => {
                                      const title = e.target.value
                                      updateOutlineSection(index, {
                                        title,
                                        id: cleanSectionId(title, `section_${index + 1}`),
                                      })
                                    }}
                                  />
                                  <button
                                    className="h-6 w-6 rounded border bg-white text-slate-500 hover:text-red-600 hover:border-red-200 inline-flex items-center justify-center disabled:opacity-40"
                                    onClick={() => removeOutlineSection(index)}
                                    disabled={outline.sections.length <= 1}
                                    title="Remove section"
                                  >
                                    <Trash2 className="h-3 w-3" />
                                  </button>
                                </div>
                                <div className="grid grid-cols-2 gap-2">
                                  <div>
                                    <label className="text-[10px] font-medium text-slate-600">Min words</label>
                                    <input
                                      type="number"
                                      min={150}
                                      className="w-full border rounded px-2 py-1 text-xs bg-white"
                                      value={section.minWords}
                                      onChange={e => updateOutlineSection(index, { minWords: Number(e.target.value) || 150 })}
                                    />
                                  </div>
                                  <div>
                                    <label className="text-[10px] font-medium text-slate-600">Equations</label>
                                    <input
                                      type="number"
                                      min={0}
                                      className="w-full border rounded px-2 py-1 text-xs bg-white"
                                      value={section.numEquations}
                                      onChange={e => updateOutlineSection(index, { numEquations: Number(e.target.value) || 0, hasEquations: Number(e.target.value) > 0 })}
                                    />
                                  </div>
                                </div>
                                <textarea
                                  className="w-full border rounded px-2 py-1.5 text-xs resize-none bg-white"
                                  rows={3}
                                  placeholder="Key points, one per line"
                                  value={listToText(section.keyPoints)}
                                  onChange={e => updateOutlineSection(index, { keyPoints: textToList(e.target.value) })}
                                />
                                <div className="grid grid-cols-2 gap-1 text-[11px]">
                                  <label className="flex items-center gap-1">
                                    <input type="checkbox" checked={section.hasAlgorithm} onChange={e => updateOutlineSection(index, { hasAlgorithm: e.target.checked })} />
                                    Algorithm
                                  </label>
                                  <label className="flex items-center gap-1">
                                    <input type="checkbox" checked={section.hasTables} onChange={e => updateOutlineSection(index, { hasTables: e.target.checked })} />
                                    Tables
                                  </label>
                                  <label className="flex items-center gap-1">
                                    <input type="checkbox" checked={section.hasFigures} onChange={e => updateOutlineSection(index, { hasFigures: e.target.checked })} />
                                    Figures
                                  </label>
                                  <label className="flex items-center gap-1">
                                    <input type="checkbox" checked={section.hasEquations} onChange={e => updateOutlineSection(index, { hasEquations: e.target.checked, numEquations: e.target.checked ? Math.max(1, section.numEquations) : 0 })} />
                                    Equations
                                  </label>
                                </div>
                                {section.hasFigures && (
                                  <textarea
                                    className="w-full border rounded px-2 py-1.5 text-xs resize-none bg-white"
                                    rows={2}
                                    placeholder="Figure notes, one per line"
                                    value={listToText(section.figureDescriptions)}
                                    onChange={e => updateOutlineSection(index, { figureDescriptions: textToList(e.target.value) })}
                                  />
                                )}
                              </div>
                            ))}
                          </div>
                        </div>
                      </>
                    ) : (
                      <div className="rounded border border-dashed p-2 text-[11px]">No outline generated</div>
                    )}
                    <div className="grid grid-cols-2 gap-2">
                      <Button size="sm" variant="outline" onClick={saveOutline} disabled={savingOutline || !outline} className="h-7 text-xs">
                        {savingOutline ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : <Save className="h-3 w-3 mr-1" />}
                        Save
                      </Button>
                      <Button size="sm" variant="secondary" onClick={generateOutline} disabled={generatingOutline} className="h-7 text-xs">
                        {generatingOutline ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : <RefreshCw className="h-3 w-3 mr-1" />}
                        Generate
                      </Button>
                    </div>
                  </div>
                </div>

                {/* Metadata */}
                <div className="border rounded-lg bg-white overflow-hidden">
                  <div className="px-3 py-1.5 border-b bg-slate-50 text-xs font-medium text-muted-foreground">Metadata</div>
                  <div className="p-2 space-y-1 text-xs text-muted-foreground">
                    <div><span className="font-medium">ID:</span> {selectedPaper.id}</div>
                    <div><span className="font-medium">Type:</span> {selectedPaper.paperType}</div>
                    {selectedPaper.targetVenue && <div><span className="font-medium">Venue:</span> {selectedPaper.targetVenue.toUpperCase()}</div>}
                    <div><span className="font-medium">Provider:</span> {selectedPaper.providerName} / {selectedPaper.model}</div>
                    <div><span className="font-medium">Created:</span> {new Date(selectedPaper.createdAt).toLocaleString()}</div>
                    {selectedPaper.sectionCount != null && <div><span className="font-medium">Sections:</span> {selectedPaper.sectionCount}</div>}
                    {selectedPaper.referenceCount != null && <div><span className="font-medium">References:</span> {selectedPaper.referenceCount}</div>}
                    {selectedPaper.figureCount != null && <div><span className="font-medium">Figures:</span> {selectedPaper.figureCount}</div>}
                    {selectedPaper.pdfAvailable && <div><span className="font-medium text-green-600">PDF Available</span></div>}
                    {selectedPaper.projectId && <div><span className="font-medium">Project:</span> {selectedPaper.projectId}</div>}
                  </div>
                </div>

                {/* Logs */}
                <div className="border rounded-lg bg-white overflow-hidden">
                  <div className="px-3 py-1.5 border-b bg-slate-50 text-xs font-medium text-muted-foreground">
                    Generation Logs ({selectedPaper.logs?.length || 0})
                  </div>
                  <div className="max-h-32 overflow-y-auto">
                    {(!selectedPaper.logs || selectedPaper.logs.length === 0) ? (
                      <p className="text-xs text-muted-foreground p-2">No logs</p>
                    ) : (
                      <div className="p-1 space-y-0.5">
                        {selectedPaper.logs.map((log, i) => (
                          <div key={i} className="flex gap-1.5 text-[10px] px-1">
                            <span className="text-muted-foreground whitespace-nowrap">{new Date(log.timestamp).toLocaleTimeString()}</span>
                            <span>{log.message}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </AppPageLayout>
  )
}
