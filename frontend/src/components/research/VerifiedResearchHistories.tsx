import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowRight,
  Beaker,
  CheckCircle2,
  Code2,
  Download,
  FileText,
  FlaskConical,
  History,
  Lightbulb,
  RefreshCw,
  Route,
  ShieldCheck,
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button, buttonVariants } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { API_BASE_URL } from '@/lib/api'
import { useReviewLocale } from '@/lib/reviewLocale'
import { cn } from '@/lib/utils'

interface VerifiedStage {
  id: 'idea' | 'plan' | 'code' | 'experiment' | 'paper' | 'reviewx'
  labelZh: string
  labelEn: string
  entityId: string
  url: string
  status: 'passed' | 'missing'
}

interface VerifiedArtifact {
  id: string
  label: string
  kind: string
  sha256: string
  verified: boolean
  url: string
}

interface ReviewRoundSummary {
  score?: number
  findingCount: number
  severityCounts: Record<string, number>
  llmCallCount: number
}

interface VerifiedHistory {
  id: string
  titleZh: string
  titleEn: string
  domainZh: string
  domainEn: string
  summaryZh: string
  summaryEn: string
  provenance: {
    dataset: string
    testPairs: number
    testLabelsUsedForSelection: boolean
  }
  decision: {
    code: 'apply_revision' | 'keep_round_one' | string
    labelZh: string
    labelEn: string
    validationCI95: [number, number]
  }
  primaryMetric: {
    name: string
    before: number
    after: number
    delta: number
  }
  stages: VerifiedStage[]
  reviewTrail: {
    initial: ReviewRoundSummary
    final: ReviewRoundSummary
    loopStatus: string
  }
  artifacts: VerifiedArtifact[]
  integrity: {
    status: 'verified' | 'incomplete'
  }
}

const stageIcons = {
  idea: Lightbulb,
  plan: Route,
  code: Code2,
  experiment: Beaker,
  paper: FileText,
  reviewx: ShieldCheck,
}

const formatSigned = (value: number) => `${value >= 0 ? '+' : ''}${(value * 100).toFixed(2)} pp`

export function VerifiedResearchHistories() {
  const { text } = useReviewLocale()
  const [histories, setHistories] = useState<VerifiedHistory[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const loadHistories = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/workspace/verified-histories`)
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const payload = await response.json()
      setHistories(payload.histories || [])
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Load failed')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadHistories()
  }, [loadHistories])

  if (!loading && histories.length === 0 && !error) return null

  return (
    <section aria-labelledby="verified-history-title" className="border-y border-slate-200 bg-slate-50/70 px-4 py-5 sm:px-6">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <History className="h-5 w-5 text-indigo-700" />
            <h2 id="verified-history-title" className="text-lg font-bold text-slate-950">
              {text('已验证的真实全流程历史', 'Verified real-data workflow histories')}
            </h2>
            {!loading && <Badge variant="outline">{histories.length}</Badge>}
          </div>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-600">
            {text(
              '可直接检查真实数据、六阶段产物和 ReviewX 反馈闭环。打开历史不会调用 API 或创建新任务。',
              'Inspect real data, six-stage artifacts, and the ReviewX feedback loop. Opening a history does not call a model or create a run.',
            )}
          </p>
        </div>
        <Button type="button" size="sm" variant="outline" onClick={() => void loadHistories()} disabled={loading}>
          <RefreshCw className={`mr-2 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
          {text('刷新', 'Refresh')}
        </Button>
      </div>

      {loading ? (
        <div className="grid gap-4 lg:grid-cols-2">
          <Skeleton className="h-80 w-full" />
          <Skeleton className="h-80 w-full" />
        </div>
      ) : error ? (
        <div className="flex items-center justify-between gap-3 border border-red-200 bg-red-50 p-4 text-sm text-red-800">
          <span>{text('无法加载已验证历史：', 'Unable to load verified histories: ')}{error}</span>
          <Button type="button" size="sm" variant="outline" onClick={() => void loadHistories()}>
            {text('重试', 'Retry')}
          </Button>
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {[...histories]
            .sort((left, right) => right.primaryMetric.delta - left.primaryMetric.delta)
            .map((history) => {
            const isUpdate = history.decision.code === 'apply_revision'
            const initialBlockers = history.reviewTrail.initial.severityCounts?.blocker || 0
            const finalBlockers = history.reviewTrail.final.severityCounts?.blocker || 0
            const highlightedArtifacts = ['final-pdf', 'research-dossier', 'evaluation-records']
              .map((id) => history.artifacts.find((artifact) => artifact.id === id))
              .filter((artifact): artifact is VerifiedArtifact => Boolean(artifact))

            return (
              <Card key={history.id} className="border-slate-200 shadow-sm">
                <CardHeader className="space-y-3 p-4 pb-3 sm:p-6 sm:pb-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2 text-xs font-semibold uppercase text-slate-500">
                      <FlaskConical className="h-4 w-4" />
                      {text(history.domainZh, history.domainEn)}
                    </div>
                    <Badge
                      variant="outline"
                      className={history.integrity.status === 'verified'
                        ? 'border-emerald-300 bg-emerald-50 text-emerald-800'
                        : 'border-red-300 bg-red-50 text-red-800'}
                    >
                      <CheckCircle2 className="mr-1 h-3.5 w-3.5" />
                      {history.integrity.status === 'verified' ? text('完整性已验证', 'Integrity verified') : text('产物不完整', 'Incomplete')}
                    </Badge>
                  </div>
                  <CardTitle className="text-base leading-6 text-slate-950">
                    {text(history.titleZh, history.titleEn)}
                  </CardTitle>
                  <p className="text-sm leading-6 text-slate-600">{text(history.summaryZh, history.summaryEn)}</p>
                </CardHeader>
                <CardContent className="space-y-4 p-4 pt-0 sm:p-6 sm:pt-0">
                  <div className="grid grid-cols-2 border-y border-slate-200 py-3 text-center sm:grid-cols-3">
                    <div className="border-r border-slate-200 px-2">
                      <div className="whitespace-nowrap text-base font-bold text-slate-950 sm:text-lg">{history.provenance.testPairs.toLocaleString()}</div>
                      <div className="text-[11px] leading-4 text-slate-500 sm:text-xs">{text('留出测试对', 'held-out pairs')}</div>
                    </div>
                    <div className="px-2">
                      <div className={`whitespace-nowrap text-base font-bold sm:text-lg ${history.primaryMetric.delta > 0 ? 'text-emerald-700' : 'text-slate-700'}`}>
                        {formatSigned(history.primaryMetric.delta)}
                      </div>
                      <div className="text-[11px] leading-4 text-slate-500 sm:text-xs">{history.primaryMetric.name}</div>
                    </div>
                    <div className="col-span-2 mt-3 border-t border-slate-200 px-2 pt-3 sm:col-span-1 sm:mt-0 sm:border-l sm:border-t-0 sm:pt-0">
                      <div className="whitespace-nowrap text-base font-bold text-slate-950 sm:text-lg">{initialBlockers} → {finalBlockers}</div>
                      <div className="text-[11px] leading-4 text-slate-500 sm:text-xs">Blockers</div>
                    </div>
                  </div>

                  <div className="flex flex-col items-start justify-between gap-2 border-l-4 border-slate-800 bg-white px-3 py-2 sm:flex-row sm:items-center sm:gap-3">
                    <div className="min-w-0">
                      <div className="text-xs font-semibold text-slate-500">{text('独立证据门禁', 'Independent evidence gate')}</div>
                      <div className="mt-0.5 text-sm font-semibold text-slate-900">
                        {text(history.decision.labelZh, history.decision.labelEn)}
                      </div>
                    </div>
                    <Badge className={isUpdate ? 'bg-amber-600 text-white' : 'bg-blue-700 text-white'}>
                      {isUpdate ? 'UPDATE' : 'KEEP'}
                    </Badge>
                  </div>

                  <div>
                    <div className="mb-2 text-xs font-semibold text-slate-500">{text('逐阶段打开证据', 'Open evidence by stage')}</div>
                    <div className="grid grid-cols-3 gap-2 sm:grid-cols-6 lg:grid-cols-3 xl:grid-cols-6">
                      {history.stages.map((stage) => {
                        const Icon = stageIcons[stage.id]
                        return (
                          <Link
                            key={stage.id}
                            to={stage.url}
                            title={text(stage.labelZh, stage.labelEn)}
                            className={cn(buttonVariants({ size: 'sm', variant: 'outline' }), 'h-auto min-h-14 flex-col gap-1 px-1 py-2 text-[11px]')}
                          >
                            <Icon className="h-4 w-4" />
                            <span className="max-w-full text-center leading-3">{text(stage.labelZh, stage.labelEn)}</span>
                          </Link>
                        )
                      })}
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center gap-2 border-t border-slate-200 pt-3">
                    {highlightedArtifacts.map((artifact) => (
                      <a
                        key={artifact.id}
                        href={`${API_BASE_URL}${artifact.url}`}
                        download
                        className={cn(buttonVariants({ size: 'sm', variant: 'ghost' }), 'px-2 text-xs')}
                      >
                        <Download className="mr-1.5 h-3.5 w-3.5" />
                        {artifact.id === 'final-pdf'
                          ? text('论文 PDF', 'Paper PDF')
                          : artifact.id === 'research-dossier'
                            ? text('研究档案', 'Dossier')
                            : text('逐条结果', 'Records')}
                      </a>
                    ))}
                    <Link
                      to={history.stages.find((stage) => stage.id === 'reviewx')?.url || '/review/consistency'}
                      className={cn(buttonVariants({ size: 'sm' }), 'ml-auto')}
                    >
                      {text('查看闭环', 'Open loop')}
                      <ArrowRight className="ml-2 h-4 w-4" />
                    </Link>
                  </div>
                </CardContent>
              </Card>
            )
          })}
        </div>
      )}
    </section>
  )
}
