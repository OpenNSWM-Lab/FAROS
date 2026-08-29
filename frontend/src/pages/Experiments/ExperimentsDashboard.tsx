/**
 * ExperimentsDashboard — Project-level experiment dashboard with metrics,
 * figure generation, and artifact management.
 *
 * Replaces the old table-only view with:
 * - Create experiment form
 * - Experiments list with linked projects/plans
 * - Metrics panel per experiment (ingest + view)
 * - "Generate Paper-Ready Figure" button
 * - Figure preview + download
 */

import { useState, useEffect, useCallback } from 'react'
import { AppPageLayout } from '@/components/layout/AppPageLayout'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import {
  BarChart3, Plus, Loader2, RefreshCw,
  TrendingUp, Image, Download, Sparkles, AlertTriangle, Code2,
} from 'lucide-react'
import { LLM_PROVIDERS, getModelsByProvider } from '@/lib/models/providers'
import { useReviewLocale } from '@/lib/reviewLocale'

const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

interface Experiment {
  id: string
  name: string
  projectId?: string
  planSessionId?: string
  planLinkId?: string
  status: string
  tags: string[]
  description: string
  createdAt: string
  updatedAt: string
  metricsCount?: number
  figuresCount?: number
}

interface Metric {
  id: string
  experimentId: string
  key: string
  value: number
  step?: number
  timestamp: string
}

interface FigureArtifact {
  id: string
  experimentId: string
  figureType: string
  spec: Record<string, unknown>
  caption: string
  pathPng?: string
  pathPdf?: string
  sizePng?: number
  sizePdf?: number
  createdAt: string
}

interface Dataset {
  id: string
  experimentId: string
  name: string
  format: string
  rowCount: number
  columns: string[]
  createdAt: string
}

const ALL_FIGURE_TYPES = [
  { value: '', label: 'Auto-detect' },
  { value: 'line', label: 'Line Chart' },
  { value: 'bar', label: 'Bar Chart' },
  { value: 'groupedBar', label: 'Grouped Bar' },
  { value: 'stackedBar', label: 'Stacked Bar' },
  { value: 'scatter', label: 'Scatter Plot' },
  { value: 'bubble', label: 'Bubble Chart' },
  { value: 'histogram', label: 'Histogram' },
  { value: 'boxplot', label: 'Box Plot' },
  { value: 'violin', label: 'Violin Plot' },
  { value: 'heatmap', label: 'Heatmap' },
  { value: 'radar', label: 'Radar Chart' },
  { value: 'roc', label: 'ROC Curve' },
  { value: 'pr', label: 'PR Curve' },
  { value: 'pie', label: 'Pie Chart' },
]

interface FigTypeCheck { enabled: boolean; reason: string }

function checkFigureTypeCompatibility(
  figType: string,
  metricKeys: string[],
  metricCount: number,
  hasStep: boolean,
  datasetCols: string[],
  datasetRows: number,
  hasDataset: boolean,
): FigTypeCheck {
  const numCols = metricKeys.length
  const totalCols = hasDataset ? datasetCols.length : numCols
  const totalRows = hasDataset ? datasetRows : metricCount

  if (figType === '') return { enabled: true, reason: 'Auto-detect best chart type' }

  if (totalRows === 0 && metricCount === 0) return { enabled: false, reason: 'No data available. Add metrics or upload a dataset.' }

  switch (figType) {
    case 'line':
      if (!hasStep && !hasDataset) return { enabled: false, reason: 'Line chart needs sequential data (step values or dataset with ordered column)' }
      return { enabled: true, reason: 'Plot metric values over steps' }
    case 'bar':
      return { enabled: numCols >= 1 || totalCols >= 1, reason: numCols >= 1 ? 'Bar chart of metric values' : 'Need at least 1 metric or column' }
    case 'groupedBar':
      if (numCols < 2 && totalCols < 2) return { enabled: false, reason: 'Grouped bar needs at least 2 numeric columns or metric keys' }
      return { enabled: true, reason: 'Compare multiple metrics side-by-side' }
    case 'stackedBar':
      if (numCols < 2 && totalCols < 2) return { enabled: false, reason: 'Stacked bar needs at least 2 numeric columns or metric keys' }
      return { enabled: true, reason: 'Show composition across categories' }
    case 'scatter':
      if (numCols < 2 && totalCols < 2) return { enabled: false, reason: 'Scatter plot needs at least 2 numeric columns' }
      return { enabled: true, reason: 'Plot relationship between two variables' }
    case 'bubble':
      if (numCols < 3 && totalCols < 3) return { enabled: false, reason: 'Bubble chart needs at least 3 numeric columns (x, y, size)' }
      return { enabled: true, reason: 'Plot 3 dimensions: x, y, and bubble size' }
    case 'histogram':
      if (totalRows < 2) return { enabled: false, reason: 'Histogram needs at least 2 data points' }
      return { enabled: true, reason: 'Distribution of values' }
    case 'boxplot':
      if (totalRows < 3) return { enabled: false, reason: 'Boxplot needs at least 3 data points for quartile computation' }
      return { enabled: true, reason: 'Statistical distribution summary' }
    case 'violin':
      if (totalRows < 5) return { enabled: false, reason: 'Violin plot needs at least 5 data points for density estimation' }
      return { enabled: true, reason: 'Density + distribution visualization' }
    case 'heatmap':
      if (numCols < 2 && totalCols < 2) return { enabled: false, reason: 'Heatmap needs at least 2 numeric columns for a matrix' }
      if (totalRows < 2) return { enabled: false, reason: 'Heatmap needs at least 2 rows' }
      return { enabled: true, reason: 'Correlation or value matrix' }
    case 'radar':
      if (numCols < 3 && totalCols < 3) return { enabled: false, reason: 'Radar chart needs at least 3 metrics/columns for axes' }
      return { enabled: true, reason: 'Multi-metric comparison on radial axes' }
    case 'roc':
      if (!hasDataset && numCols < 2) return { enabled: false, reason: 'ROC curve needs true labels and predicted scores columns' }
      return { enabled: true, reason: 'Receiver Operating Characteristic curve' }
    case 'pr':
      if (!hasDataset && numCols < 2) return { enabled: false, reason: 'PR curve needs true labels and predicted scores columns' }
      return { enabled: true, reason: 'Precision-Recall curve' }
    case 'pie':
      if (numCols < 1 && totalCols < 1) return { enabled: false, reason: 'Pie chart needs at least 1 categorical column with values' }
      return { enabled: true, reason: 'Proportional breakdown' }
    default:
      return { enabled: true, reason: '' }
  }
}

export function ExperimentsDashboard() {
  const { text } = useReviewLocale()
  const [experiments, setExperiments] = useState<Experiment[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Create form
  const [showCreate, setShowCreate] = useState(false)
  const [newName, setNewName] = useState('')
  const [newProjectId, setNewProjectId] = useState('')
  const [newDescription, setNewDescription] = useState('')
  const [creating, setCreating] = useState(false)

  // Selected experiment
  const [selectedExp, setSelectedExp] = useState<Experiment | null>(null)
  const [metrics, setMetrics] = useState<Metric[]>([])
  const [figures, setFigures] = useState<FigureArtifact[]>([])
  const [loadingMetrics, setLoadingMetrics] = useState(false)
  const [loadingFigures, setLoadingFigures] = useState(false)

  // Metric ingest
  const [metricKey, setMetricKey] = useState('')
  const [metricValue, setMetricValue] = useState('')
  const [metricStep, setMetricStep] = useState('')

  // Figure generation
  const [figProvider, setFigProvider] = useState('')
  const [figModel, setFigModel] = useState('')
  const [preferredFigType, setPreferredFigType] = useState('')
  const [generatingFig, setGeneratingFig] = useState(false)
  const [figError, setFigError] = useState<string | null>(null)
  const [figDatasetId, setFigDatasetId] = useState('')

  // Datasets
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [uploadingDs, setUploadingDs] = useState(false)
  const [dsName, setDsName] = useState('uploaded_data')

  // Bulk metric ingest
  const [bulkMetrics, setBulkMetrics] = useState('')

  // Figure code viewer
  const [figCodeMap, setFigCodeMap] = useState<Record<string, string>>({})
  const [figShowCode, setFigShowCode] = useState<Record<string, boolean>>({})

  const loadExperiments = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await fetch(`${API_BASE}/api/v1/experiments`)
      if (resp.ok) {
        const data = await resp.json()
        setExperiments(data.experiments || [])
      }
    } catch (e) {
      setError(text('实验列表加载失败，请检查网络后重试。', 'Failed to load experiments. Check the network and retry.'))
    }
    setLoading(false)
  }, [text])

  useEffect(() => { void loadExperiments() }, [loadExperiments])
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
          setFigProvider(providerName)
          setFigModel(providerInfo?.model || getModelsByProvider(providerName)[0]?.id || '')
        }
      } catch {
        if (!cancelled) {
          setFigProvider('')
          setFigModel('')
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])


  const createExperiment = async () => {
    if (!newName.trim()) return
    setCreating(true)
    try {
      const resp = await fetch(`${API_BASE}/api/v1/experiments`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: newName,
          projectId: newProjectId || undefined,
          description: newDescription,
        }),
      })
      if (resp.ok) {
        setNewName(''); setNewProjectId(''); setNewDescription('')
        setShowCreate(false)
        loadExperiments()
      }
    } catch { void 0 }
    setCreating(false)
  }

  const selectExperiment = async (exp: Experiment) => {
    setSelectedExp(exp)
    setFigError(null)
    setFigDatasetId('')

    // Load metrics
    setLoadingMetrics(true)
    try {
      const resp = await fetch(`${API_BASE}/api/v1/experiments/${exp.id}/metrics`)
      if (resp.ok) {
        const data = await resp.json()
        setMetrics(data.metrics || [])
      }
    } catch { void 0 }
    setLoadingMetrics(false)

    // Load figures
    setLoadingFigures(true)
    try {
      const resp = await fetch(`${API_BASE}/api/v1/experiments/${exp.id}/figures`)
      if (resp.ok) {
        const data = await resp.json()
        setFigures(data.figures || [])
      }
    } catch { void 0 }
    setLoadingFigures(false)

    // Load datasets
    try {
      const resp = await fetch(`${API_BASE}/api/v1/experiments/${exp.id}/datasets`)
      if (resp.ok) {
        const data = await resp.json()
        setDatasets(data.datasets || [])
      }
    } catch { void 0 }
  }

  const uploadDataset = async (file: File) => {
    if (!selectedExp) return
    setUploadingDs(true)
    try {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('name', dsName || file.name)
      const resp = await fetch(`${API_BASE}/api/v1/experiments/${selectedExp.id}/datasets/upload`, {
        method: 'POST',
        body: formData,
      })
      if (resp.ok) {
        selectExperiment(selectedExp)
      } else {
        const err = await resp.json().catch(() => ({}))
        setFigError(err.detail || 'Upload failed')
      }
    } catch (e) {
      setFigError(e instanceof Error ? e.message : 'Upload failed')
    }
    setUploadingDs(false)
  }

  const ingestMetric = async () => {
    if (!selectedExp || !metricKey.trim()) return
    const val = parseFloat(metricValue)
    if (isNaN(val)) return
    try {
      await fetch(`${API_BASE}/api/v1/experiments/${selectedExp.id}/metrics`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          metrics: [{ key: metricKey, value: val, step: metricStep ? parseInt(metricStep) : undefined }]
        }),
      })
      setMetricKey(''); setMetricValue(''); setMetricStep('')
      selectExperiment(selectedExp)
    } catch { void 0 }
  }

  const ingestBulkMetrics = async () => {
    if (!selectedExp || !bulkMetrics.trim()) return
    try {
      const parsed = JSON.parse(bulkMetrics)
      const metricsArr = Array.isArray(parsed) ? parsed : [parsed]
      await fetch(`${API_BASE}/api/v1/experiments/${selectedExp.id}/metrics`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ metrics: metricsArr }),
      })
      setBulkMetrics('')
      selectExperiment(selectedExp)
    } catch {
      setFigError('Invalid JSON for bulk metrics')
    }
  }

  const generateFigure = async () => {
    if (!selectedExp) return
    setGeneratingFig(true)
    setFigError(null)
    try {
      const resp = await fetch(`${API_BASE}/api/v1/experiments/${selectedExp.id}/figures/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          providerName: figProvider,
          model: figModel,
          preferredFigureType: preferredFigType || undefined,
          datasetId: figDatasetId || undefined,
        }),
      })
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}))
        throw new Error(err.detail || `Error ${resp.status}`)
      }
      selectExperiment(selectedExp)
    } catch (e) {
      setFigError(e instanceof Error ? e.message : 'Figure generation failed')
    }
    setGeneratingFig(false)
  }

  const loadFigureCode = async (figId: string) => {
    if (figCodeMap[figId]) {
      setFigShowCode(prev => ({ ...prev, [figId]: !prev[figId] }))
      return
    }
    try {
      const resp = await fetch(`${API_BASE}/api/v1/experiments/figures/${figId}/code`)
      if (resp.ok) {
        const data = await resp.json()
        setFigCodeMap(prev => ({ ...prev, [figId]: data.code || '' }))
        setFigShowCode(prev => ({ ...prev, [figId]: true }))
      }
    } catch { void 0 }
  }

  // Unique metric keys for summary
  const metricSummary = metrics.reduce<Record<string, { count: number; sum: number; min: number; max: number }>>((acc, m) => {
    if (!acc[m.key]) acc[m.key] = { count: 0, sum: 0, min: Infinity, max: -Infinity }
    acc[m.key].count++
    acc[m.key].sum += m.value
    acc[m.key].min = Math.min(acc[m.key].min, m.value)
    acc[m.key].max = Math.max(acc[m.key].max, m.value)
    return acc
  }, {})

  return (
    <AppPageLayout
      title={text('实验', 'Experiments')}
      subtitle={text('记录实验指标、上传数据并生成论文级图表', 'Record metrics, upload datasets, and generate paper-ready figures')}
      icon={BarChart3}
      iconColor="indigo"
      accentColor="indigo"
      actions={
        <Button onClick={() => setShowCreate(!showCreate)} className="bg-indigo-500 hover:bg-indigo-600">
          <Plus className="h-4 w-4 mr-2" /> {text('新建实验', 'New Experiment')}
        </Button>
      }
    >
      {error && (
        <div className="p-3 rounded bg-red-50 border border-red-200 text-sm text-red-800 mb-4">
          {error}
        </div>
      )}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* LEFT: Experiments List */}
        <div className="space-y-4">
          {/* Create form */}
          {showCreate && (
            <Card className="border-indigo-200">
              <CardContent className="pt-4 space-y-3">
                <Input placeholder={text('实验名称', 'Experiment name')} value={newName} onChange={e => setNewName(e.target.value)} />
                <Input placeholder={text('关联 Project ID（可选）', 'Project ID (optional)')} value={newProjectId} onChange={e => setNewProjectId(e.target.value)} />
                <Input placeholder={text('实验说明（可选）', 'Description (optional)')} value={newDescription} onChange={e => setNewDescription(e.target.value)} />
                <div className="flex gap-2">
                  <Button size="sm" onClick={createExperiment} disabled={creating || !newName.trim()} className="bg-indigo-500 hover:bg-indigo-600">
                    {creating ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Plus className="h-4 w-4 mr-1" />} {text('创建', 'Create')}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setShowCreate(false)}>{text('取消', 'Cancel')}</Button>
                </div>
              </CardContent>
            </Card>
          )}

          {/* List */}
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-medium text-muted-foreground">{text(`${experiments.length} 个实验`, `${experiments.length} experiments`)}</h3>
            <Button variant="ghost" size="sm" onClick={loadExperiments}><RefreshCw className="h-3 w-3" /></Button>
          </div>

          {loading ? (
            <div className="flex items-center justify-center py-8"><Loader2 className="h-6 w-6 animate-spin text-indigo-500" /></div>
          ) : experiments.length === 0 ? (
            <Card><CardContent className="py-8 text-center text-sm text-muted-foreground">{text('暂无实验。新建实验后可记录指标和生成图表。', 'No experiments yet. Create one to record metrics and generate figures.')}</CardContent></Card>
          ) : (
            <div className="space-y-2 max-h-[600px] overflow-y-auto">
              {experiments.map(exp => (
                <Card
                  key={exp.id}
                  className={`cursor-pointer hover:border-indigo-300 transition-colors ${selectedExp?.id === exp.id ? 'border-indigo-400 bg-indigo-50' : ''}`}
                  onClick={() => selectExperiment(exp)}
                >
                  <CardContent className="py-3 space-y-1">
                    <div className="flex items-center justify-between">
                      <p className="text-sm font-medium truncate">{exp.name}</p>
                      <Badge variant="outline" className="text-xs">{exp.status}</Badge>
                    </div>
                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                      {exp.projectId && <span>Project: {exp.projectId.slice(0, 12)}</span>}
                      <span>{new Date(exp.createdAt).toLocaleDateString()}</span>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </div>

        {/* CENTER + RIGHT: Experiment Detail */}
        <div className="lg:col-span-2 space-y-4">
          {!selectedExp ? (
            <Card><CardContent className="py-12 text-center"><BarChart3 className="h-10 w-10 mx-auto mb-3 text-muted-foreground opacity-50" /><p className="text-sm text-muted-foreground">{text('选择一个实验查看指标、数据集和图表', 'Select an experiment to view metrics, datasets, and figures')}</p></CardContent></Card>
          ) : (
            <>
              {/* Header */}
              <Card>
                <CardHeader className="pb-2">
                  <div className="flex items-center justify-between">
                    <CardTitle className="text-lg">{selectedExp.name}</CardTitle>
                    <Badge variant="outline">{selectedExp.status}</Badge>
                  </div>
                </CardHeader>
                <CardContent>
                  <div className="flex items-center gap-4 text-xs text-muted-foreground">
                    <span>ID: {selectedExp.id}</span>
                    {selectedExp.projectId && <span>Project: {selectedExp.projectId}</span>}
                    {selectedExp.planSessionId && <span>Plan: {selectedExp.planSessionId}</span>}
                    <span>Created: {new Date(selectedExp.createdAt).toLocaleString()}</span>
                  </div>
                  {selectedExp.description && <p className="text-sm mt-2 text-muted-foreground">{selectedExp.description}</p>}
                </CardContent>
              </Card>

              {/* Metrics */}
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-base flex items-center gap-2">
                    <TrendingUp className="h-4 w-4 text-indigo-500" />
                    {text('指标', 'Metrics')} ({metrics.length})
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  {/* Summary */}
                  {Object.keys(metricSummary).length > 0 && (
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                      {Object.entries(metricSummary).map(([key, s]) => (
                        <div key={key} className="p-3 rounded bg-slate-50 border">
                          <p className="text-xs font-medium text-muted-foreground">{key}</p>
                          <p className="text-lg font-semibold">{(s.sum / s.count).toFixed(3)}</p>
                          <p className="text-xs text-muted-foreground">n={s.count} min={s.min.toFixed(3)} max={s.max.toFixed(3)}</p>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Ingest single */}
                  <div className="flex gap-2 items-end">
                    <div className="flex-1"><label className="text-xs font-medium">Key</label><Input value={metricKey} onChange={e => setMetricKey(e.target.value)} placeholder="accuracy" className="h-8 text-sm" /></div>
                    <div className="w-24"><label className="text-xs font-medium">Value</label><Input value={metricValue} onChange={e => setMetricValue(e.target.value)} placeholder="0.95" className="h-8 text-sm" /></div>
                    <div className="w-20"><label className="text-xs font-medium">Step</label><Input value={metricStep} onChange={e => setMetricStep(e.target.value)} placeholder="1" className="h-8 text-sm" /></div>
                    <Button size="sm" onClick={ingestMetric} disabled={!metricKey.trim() || !metricValue.trim()} className="h-8"><Plus className="h-3 w-3 mr-1" /> Add</Button>
                  </div>

                  {/* Bulk ingest */}
                  <div>
                    <label className="text-xs font-medium">Bulk JSON (array of {'{key, value, step?}'})</label>
                    <div className="flex gap-2 mt-1">
                      <textarea value={bulkMetrics} onChange={e => setBulkMetrics(e.target.value)} placeholder='[{"key":"accuracy","value":0.95},{"key":"loss","value":0.1}]' className="flex-1 rounded border px-2 py-1 text-xs font-mono min-h-[60px]" />
                      <Button size="sm" variant="outline" onClick={ingestBulkMetrics} disabled={!bulkMetrics.trim()} className="self-end">Ingest</Button>
                    </div>
                  </div>

                  {loadingMetrics && <div className="flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="h-3 w-3 animate-spin" /> Loading metrics...</div>}
                </CardContent>
              </Card>

              {/* Dataset Upload */}
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-base flex items-center gap-2">
                    <BarChart3 className="h-4 w-4 text-indigo-500" />
                    {text('数据集', 'Datasets')} ({datasets.length})
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  <div className="flex gap-2 items-end">
                    <div className="flex-1">
                      <label className="text-xs font-medium">Dataset Name</label>
                      <Input value={dsName} onChange={e => setDsName(e.target.value)} placeholder="my_results" className="h-8 text-sm" />
                    </div>
                    <div>
                      <label className="text-xs font-medium">CSV or JSON file</label>
                      <input
                        type="file"
                        accept=".csv,.json,.jsonl"
                        className="block w-full text-xs file:mr-2 file:py-1 file:px-2 file:rounded file:border-0 file:text-xs file:bg-indigo-50 file:text-indigo-600"
                        onChange={e => { if (e.target.files?.[0]) uploadDataset(e.target.files[0]) }}
                        disabled={uploadingDs}
                      />
                    </div>
                  </div>
                  {uploadingDs && <div className="flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="h-3 w-3 animate-spin" /> Uploading...</div>}
                  {datasets.length > 0 && (
                    <div className="space-y-1">
                      {datasets.map(ds => (
                        <div key={ds.id} className="flex items-center justify-between p-2 rounded bg-slate-50 border text-xs">
                          <div className="flex items-center gap-2">
                            <Badge variant="outline">{ds.format}</Badge>
                            <span className="font-medium">{ds.name}</span>
                            <span className="text-muted-foreground">{ds.rowCount} rows, {ds.columns.length} cols</span>
                          </div>
                          <span className="text-muted-foreground">{new Date(ds.createdAt).toLocaleDateString()}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>

              {/* Figure Generation */}
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-base flex items-center gap-2">
                    <Image className="h-4 w-4 text-indigo-500" />
                    {text('图表', 'Figures')} ({figures.length})
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  {/* Generate button */}
                  <div className="p-3 rounded bg-gradient-to-r from-indigo-50 to-purple-50 border space-y-3">
                    <p className="text-sm font-medium">{text('生成论文级图表', 'Generate Paper-Ready Figure')}</p>
                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <label className="text-xs font-medium">Provider</label>
                        <select value={figProvider} onChange={e => { setFigProvider(e.target.value); const ms = getModelsByProvider(e.target.value); if (ms.length) setFigModel(ms[0].id) }} className="w-full rounded border px-2 py-1 text-xs">
                          {LLM_PROVIDERS.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                        </select>
                      </div>
                      <div>
                        <label className="text-xs font-medium">Model</label>
                        <select value={figModel} onChange={e => setFigModel(e.target.value)} className="w-full rounded border px-2 py-1 text-xs">
                          {getModelsByProvider(figProvider).map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
                        </select>
                      </div>
                    </div>
                    {/* Figure Type Grid with data-aware enable/disable */}
                    <div>
                      <label className="text-xs font-medium mb-2 block">Figure Type</label>
                      <div className="grid grid-cols-3 sm:grid-cols-5 gap-1.5">
                        {ALL_FIGURE_TYPES.map(ft => {
                          const metricKeys = Object.keys(metricSummary)
                          const selectedDs = datasets.find(d => d.id === figDatasetId)
                          const check = checkFigureTypeCompatibility(
                            ft.value,
                            metricKeys,
                            metrics.length,
                            metrics.some(m => m.step !== undefined && m.step !== null),
                            selectedDs?.columns || [],
                            selectedDs?.rowCount || 0,
                            !!figDatasetId,
                          )
                          const isSelected = preferredFigType === ft.value
                          return (
                            <div key={ft.value} className="relative group">
                              <button
                                type="button"
                                disabled={!check.enabled}
                                onClick={() => setPreferredFigType(ft.value)}
                                className={`w-full px-2 py-1.5 rounded text-xs border transition-all ${!check.enabled
                                    ? 'bg-gray-100 text-gray-400 border-gray-200 cursor-not-allowed opacity-60'
                                    : isSelected
                                      ? 'bg-indigo-100 text-indigo-700 border-indigo-400 ring-1 ring-indigo-300 font-semibold'
                                      : 'bg-white text-gray-700 border-gray-300 hover:border-indigo-300 hover:bg-indigo-50 cursor-pointer'
                                  }`}
                              >
                                {ft.label}
                              </button>
                              {/* Tooltip */}
                              <div className="absolute z-20 bottom-full left-1/2 -translate-x-1/2 mb-1 px-2 py-1 rounded bg-gray-900 text-white text-[10px] whitespace-nowrap opacity-0 group-hover:opacity-100 pointer-events-none transition-opacity max-w-[200px] text-center">
                                {check.reason}
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    </div>
                    {/* Data source selector */}
                    <div className="flex items-center gap-2">
                      <label className="text-xs font-medium whitespace-nowrap">Data Source:</label>
                      <select value={figDatasetId} onChange={e => setFigDatasetId(e.target.value)} className="flex-1 rounded border px-2 py-1 text-xs">
                        <option value="">Experiment Metrics ({metrics.length} points)</option>
                        {datasets.map(ds => <option key={ds.id} value={ds.id}>{ds.name} ({ds.rowCount} rows, {ds.format})</option>)}
                      </select>
                    </div>
                    <Button onClick={generateFigure} disabled={generatingFig || (metrics.length === 0 && !figDatasetId)} className="bg-indigo-500 hover:bg-indigo-600">
                      {generatingFig ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : <Sparkles className="h-4 w-4 mr-2" />}
                      {text('生成论文级图表', 'Generate Paper-Ready Figure')}
                    </Button>
                    {metrics.length === 0 && !figDatasetId && <p className="text-xs text-amber-600">{text('请先添加指标或上传数据集。', 'Add metrics or upload a dataset first.')}</p>}
                    {figError && <p className="text-xs text-red-600"><AlertTriangle className="h-3 w-3 inline mr-1" />{figError}</p>}
                  </div>

                  {/* Figure gallery */}
                  {loadingFigures ? (
                    <div className="flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="h-3 w-3 animate-spin" /> Loading figures...</div>
                  ) : figures.length === 0 ? (
                    <p className="text-xs text-muted-foreground text-center py-4">{text('暂无图表，可基于上方指标生成。', 'No figures yet. Generate one from the metrics above.')}</p>
                  ) : (
                    <div className="space-y-4">
                      {figures.map(fig => (
                        <div key={fig.id} className="border rounded-lg overflow-hidden">
                          <div className="bg-slate-50 px-3 py-2 border-b flex items-center justify-between">
                            <div className="flex items-center gap-2">
                              <Image className="h-4 w-4 text-indigo-500" />
                              <span className="text-sm font-medium">{typeof fig.spec.title === 'string' ? fig.spec.title : fig.figureType}</span>
                              <Badge variant="outline" className="text-xs">{fig.figureType}</Badge>
                            </div>
                            <div className="flex gap-1">
                              <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => window.open(`${API_BASE}/api/v1/experiments/figures/${fig.id}/png`, '_blank')}>
                                <Download className="h-3 w-3 mr-1" /> PNG
                              </Button>
                              {fig.pathPdf && (
                                <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => window.open(`${API_BASE}/api/v1/experiments/figures/${fig.id}/pdf`, '_blank')}>
                                  <Download className="h-3 w-3 mr-1" /> PDF
                                </Button>
                              )}
                              <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => loadFigureCode(fig.id)}>
                                <Code2 className="h-3 w-3 mr-1" /> {figShowCode[fig.id] ? 'Preview' : 'Code'}
                              </Button>
                              <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => window.open(`${API_BASE}/api/v1/experiments/figures/${fig.id}/download/code.py`, '_blank')}>
                                <Download className="h-3 w-3 mr-1" /> .py
                              </Button>
                            </div>
                          </div>
                          {/* Preview or Code */}
                          {figShowCode[fig.id] && figCodeMap[fig.id] ? (
                            <div className="p-3 bg-slate-900 overflow-auto" style={{ maxHeight: '400px' }}>
                              <div className="flex items-center justify-between mb-2">
                                <span className="text-xs text-slate-400 font-mono">plot.py</span>
                                <Button size="sm" variant="ghost" className="h-6 text-[10px] text-slate-400 hover:text-white" onClick={() => navigator.clipboard.writeText(figCodeMap[fig.id])}>
                                  Copy
                                </Button>
                              </div>
                              <pre className="text-xs font-mono text-green-300 whitespace-pre-wrap">{figCodeMap[fig.id]}</pre>
                            </div>
                          ) : (
                            <div className="p-4 bg-white flex justify-center">
                              <img
                                src={`${API_BASE}/api/v1/experiments/figures/${fig.id}/png`}
                                alt={typeof fig.spec.title === 'string' ? fig.spec.title : 'Figure'}
                                className="max-w-full max-h-[400px] object-contain"
                              />
                            </div>
                          )}
                          {fig.caption && (
                            <div className="px-3 py-2 bg-slate-50 border-t text-xs text-muted-foreground italic">
                              {fig.caption}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>
            </>
          )}
        </div>
      </div>
    </AppPageLayout>
  )
}
