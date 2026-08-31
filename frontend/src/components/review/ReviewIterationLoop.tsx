import { Link } from 'react-router-dom'
import {
  ArrowRight,
  CheckCircle2,
  Code2,
  ExternalLink,
  FileEdit,
  FlaskConical,
  GitBranch,
  Lightbulb,
  LockKeyhole,
  PlayCircle,
  RotateCcw,
  SearchCheck,
  ShieldCheck,
} from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { API_BASE_URL } from '@/lib/api'
import { useReviewLocale } from '@/lib/reviewLocale'

export interface ReviewLoopChange {
  fieldPath: string
  before?: unknown
  after?: unknown
  rationale?: string
  evidenceIds?: string[]
}

export interface ReviewLoopRound {
  runId: string
  iterationNumber: number
  value?: number | null
  delta?: number | null
  improved?: boolean | null
  gateStatus?: string | null
  decision?: string | null
  guardrailsSatisfied?: boolean
}

export interface ReviewLoopTrace {
  status: 'needs_iteration' | 'iteration_created' | 'accepted' | 'completed'
  fromRunId?: string | null
  toRunId?: string | null
  researchSeriesId?: string | null
  fromIteration: number
  toIteration?: number | null
  scientificDecision: string
  targetModules: string[]
  targetSections: string[]
  changes: ReviewLoopChange[]
  rounds: ReviewLoopRound[]
  primaryMetric?: string | null
  selectedCandidateId?: string | null
  benchmarkFingerprint?: string | null
  contractHash?: string | null
  finalHoldoutProtected?: boolean
}

interface MetricDelta {
  name: string
  previous: number
  current: number
  delta: number
}

interface ReviewIterationLoopProps {
  trace: ReviewLoopTrace
  gateStatus?: string
  findingCount: number
  metricDeltas: MetricDelta[]
  sourceArtifactUrls?: Record<string, string>
  nextRunId?: string
  nextRunStatus?: string
  iterationHumanReady: boolean
  actionLoading: 'revise' | 'next' | 'start' | ''
  showActions?: boolean
  onCreateIteration: () => void
  onStartIteration: () => void
  onAuditIteration: () => void
  onOpenSignoff: () => void
}

const modulePresentation = {
  idea: { zh: 'Idea / 研究计划', en: 'Idea / research plan', href: '/research/pipeline', icon: Lightbulb },
  plan: { zh: '研究计划', en: 'Research plan', href: '/research/pipeline', icon: GitBranch },
  code: { zh: 'Code', en: 'Code', href: '/code', icon: Code2 },
  experiments: { zh: '实验', en: 'Experiments', href: '/experiments', icon: FlaskConical },
  papers: { zh: 'Paper', en: 'Paper', href: '/papers', icon: FileEdit },
} as const

function compactNumber(value: number) {
  if (Number.isInteger(value)) return String(value)
  return value.toFixed(4).replace(/0+$/, '').replace(/\.$/, '')
}

export function summarizeLoopValue(value: unknown) {
  if (Array.isArray(value)) return `${value.length} items`
  if (typeof value === 'number') return compactNumber(value)
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (value === null || value === undefined || value === '') return '--'
  if (typeof value === 'object') return 'recorded'
  return String(value)
}

function changeSummary(change: ReviewLoopChange, isChinese: boolean) {
  const before = change.before
  const after = change.after
  if (Array.isArray(before) && Array.isArray(after)) {
    const removed = before.filter((item) => !after.includes(item))
    const added = after.filter((item) => !before.includes(item))
    if (removed.length === 1 && added.length === 0) {
      return isChinese ? `移除 ${String(removed[0])}` : `Remove ${String(removed[0])}`
    }
    if (added.length === 1 && removed.length === 0) {
      return isChinese ? `加入 ${String(added[0])}` : `Add ${String(added[0])}`
    }
  }
  return `${summarizeLoopValue(before)} → ${summarizeLoopValue(after)}`
}

function fieldLabel(fieldPath: string, isChinese: boolean) {
  const labels: Record<string, [string, string]> = {
    'model.selectedFeatures': ['模型特征集合', 'Model feature set'],
    decisionThreshold: ['决策阈值', 'Decision threshold'],
    stages: ['执行阶段', 'Execution stages'],
    constants: ['运行常量', 'Runtime constants'],
    expectedMetrics: ['预期指标', 'Expected metrics'],
  }
  const label = labels[fieldPath]
  return label ? label[isChinese ? 0 : 1] : fieldPath
}

function primaryMetricSummary(trace: ReviewLoopTrace) {
  const rounds = trace.rounds.filter((round) => typeof round.value === 'number')
  if (rounds.length < 2) return null
  const first = rounds[0]
  const last = rounds[rounds.length - 1]
  const difference = Number(last.value) - Number(first.value)
  return {
    first,
    last,
    difference,
    differenceLabel: Math.abs(difference) <= 1
      ? `${difference >= 0 ? '+' : ''}${(difference * 100).toFixed(2)} pp`
      : `${difference >= 0 ? '+' : ''}${compactNumber(difference)}`,
  }
}

export function ReviewIterationLoop({
  trace,
  gateStatus,
  findingCount,
  metricDeltas,
  sourceArtifactUrls = {},
  nextRunId,
  nextRunStatus,
  iterationHumanReady,
  actionLoading,
  showActions = true,
  onCreateIteration,
  onStartIteration,
  onAuditIteration,
  onOpenSignoff,
}: ReviewIterationLoopProps) {
  const { isChinese, text } = useReviewLocale()
  const completed = trace.status === 'completed' || trace.rounds.length >= 2
  const hasRoute = trace.scientificDecision !== 'accept_results' || trace.changes.length > 0 || completed
  const metricSummary = primaryMetricSummary(trace)
  const planDeltaUrl = sourceArtifactUrls['plan_delta_contract.json']
  const iterationNumber = trace.toIteration || trace.fromIteration + 1
  const nextRunCompleted = nextRunStatus === 'completed'
  const nextRunActive = nextRunStatus === 'running' || nextRunStatus === 'queued'

  const steps = [
    {
      id: 'execution',
      icon: FlaskConical,
      title: text(`V${trace.fromIteration} 本轮执行`, `V${trace.fromIteration} execution`),
      detail: text('实验产出进入证据合同', 'Outputs enter the evidence contract'),
      done: true,
    },
    {
      id: 'audit',
      icon: SearchCheck,
      title: text('ReviewX 审计', 'ReviewX audit'),
      detail: findingCount > 0
        ? `${findingCount} Finding · Gate ${gateStatus || '--'}`
        : hasRoute
          ? text('0 个硬性问题 · 发现优化机会', '0 hard findings · optimization found')
          : text(`0 个问题 · Gate ${gateStatus || '--'}`, `0 findings · Gate ${gateStatus || '--'}`),
      done: true,
    },
    {
      id: 'route-back',
      icon: GitBranch,
      title: hasRoute ? text('定向退回', 'Targeted route-back') : text('无需退回', 'No route-back'),
      detail: trace.targetModules.length > 0
        ? trace.targetModules.map((module) => modulePresentation[module as keyof typeof modulePresentation]?.[isChinese ? 'zh' : 'en'] || module).join(' + ')
        : text('结果满足当前约束', 'Current constraints satisfied'),
      done: hasRoute || trace.scientificDecision === 'accept_results',
    },
    {
      id: 'rerun',
      icon: RotateCcw,
      title: text(`V${iterationNumber} 重新执行`, `V${iterationNumber} re-execution`),
      detail: completed
        ? text('携带修正合同完成重跑', 'Rerun completed with the correction contract')
        : nextRunActive
          ? text('下一轮正在执行', 'Next iteration is running')
          : nextRunId
            ? text('下一轮已创建', 'Next iteration created')
            : text('等待签核与创建', 'Awaiting signoff and creation'),
      done: completed || nextRunCompleted,
    },
    {
      id: 're-audit',
      icon: ShieldCheck,
      title: text('ReviewX 回审', 'ReviewX re-audit'),
      detail: completed
        ? text('新证据通过同口径复验', 'New evidence passed the same audit')
        : text('比较变化并决定停止或继续', 'Compare evidence and stop or continue'),
      done: completed,
    },
  ]

  const routeModules = trace.targetModules
    .map((module) => ({ key: module, presentation: modulePresentation[module as keyof typeof modulePresentation] }))
    .filter((item): item is { key: string; presentation: typeof modulePresentation[keyof typeof modulePresentation] } => Boolean(item.presentation))

  const primaryMetricKey = String(trace.primaryMetric || '').toLowerCase().replace(/[^a-z0-9]+/g, '')
  const supportingDeltas = metricDeltas
    .filter((metric) => {
      const metricKey = metric.name.toLowerCase().replace(/[^a-z0-9]+/g, '')
      return /^method:/i.test(metric.name)
        && /f1|recall|ece|calibration/i.test(metric.name)
        && metricKey !== primaryMetricKey
    })
    .slice(0, 3)

  return (
    <section id="reviewx-closed-loop" className="border-b border-slate-200 pb-5" aria-label={text('ReviewX 受控闭环', 'ReviewX controlled loop')}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-xs font-semibold uppercase text-amber-700">
            <RotateCcw className="h-4 w-4" />
            Evidence → Finding → Action → New Evidence
          </div>
          <h3 className="mt-1 text-lg font-semibold text-slate-950">{text('ReviewX 受控闭环', 'ReviewX Controlled Loop')}</h3>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline">V{trace.fromIteration} → V{iterationNumber}</Badge>
          {completed && <Badge variant="secondary">{text('闭环已完成', 'Loop completed')}</Badge>}
          {trace.finalHoldoutProtected && (
            <Badge variant="outline" className="border-violet-200 bg-violet-50 text-violet-800">
              <LockKeyhole className="mr-1 h-3.5 w-3.5" />
              {text('留出集后加载', 'Holdout loaded after freeze')}
            </Badge>
          )}
        </div>
      </div>

      <div className="mt-4 grid min-h-24 grid-cols-1 gap-2 md:grid-cols-5">
        {steps.map((step, index) => (
          <div
            key={step.title}
            data-review-loop-step={step.id}
            className={`relative border-l-2 px-3 py-2 ${step.done ? 'border-emerald-600' : 'border-slate-300'}`}
          >
            <div className="flex items-center gap-2">
              <div className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md ${step.done ? 'bg-emerald-700 text-white' : 'bg-slate-100 text-slate-500'}`}>
                {step.done ? <CheckCircle2 className="h-4 w-4" /> : <step.icon className="h-4 w-4" />}
              </div>
              <span className="text-sm font-semibold text-slate-900">{step.title}</span>
            </div>
            <p className="mt-2 text-xs leading-5 text-slate-600">{step.detail}</p>
            {index < steps.length - 1 && (
              <ArrowRight className="absolute -right-2 top-4 hidden h-4 w-4 text-slate-300 md:block" />
            )}
          </div>
        ))}
      </div>

      {(trace.changes.length > 0 || metricSummary || routeModules.length > 0) && (
        <div className="mt-4 grid gap-4 border-t border-slate-200 pt-4 lg:grid-cols-[minmax(0,1.35fr)_minmax(260px,0.65fr)]">
          <div data-review-loop-panel="changes">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="text-xs font-semibold uppercase text-slate-600">{text('退回内容与实际修改', 'Route-back and applied changes')}</div>
              {planDeltaUrl && (
                <a
                  href={`${API_BASE_URL}${planDeltaUrl}`}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 text-xs font-medium text-emerald-700 hover:text-emerald-900"
                >
                  {text('查看变化合同', 'Open delta contract')} <ExternalLink className="h-3.5 w-3.5" />
                </a>
              )}
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              {routeModules.map(({ key, presentation }) => (
                <Link
                  key={key}
                  to={presentation.href}
                  className="inline-flex min-h-9 items-center gap-2 rounded-md border border-slate-300 bg-white px-3 text-xs font-semibold text-slate-800 hover:border-emerald-500 hover:text-emerald-800"
                >
                  <presentation.icon className="h-4 w-4" />
                  {text(presentation.zh, presentation.en)}
                </Link>
              ))}
              {trace.changes.slice(0, 4).map((change) => (
                <div key={change.fieldPath} className="min-h-9 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-950">
                  <span className="font-semibold">{fieldLabel(change.fieldPath, isChinese)}：</span>
                  {changeSummary(change, isChinese)}
                </div>
              ))}
            </div>
          </div>

          <div data-review-loop-panel="same-protocol" className="border-l-0 border-slate-200 lg:border-l lg:pl-4">
            <div className="text-xs font-semibold uppercase text-slate-600">{text('同口径复验', 'Same-protocol recheck')}</div>
            {metricSummary ? (
              <div className="mt-2 flex items-end justify-between gap-3">
                <div>
                  <div className="text-xs text-slate-500">{trace.primaryMetric?.replace(/^method:/i, '') || text('主指标', 'Primary metric')}</div>
                  <div className="mt-1 font-mono text-lg font-semibold text-slate-950">
                    {Number(metricSummary.first.value).toFixed(4)} → {Number(metricSummary.last.value).toFixed(4)}
                  </div>
                </div>
                <div className={`font-mono text-lg font-semibold ${metricSummary.difference >= 0 ? 'text-emerald-700' : 'text-red-700'}`}>
                  {metricSummary.differenceLabel}
                </div>
              </div>
            ) : (
              <div className="mt-2 text-sm text-slate-600">{text('等待下一轮产生可比较证据。', 'Awaiting comparable evidence from the next iteration.')}</div>
            )}
            {supportingDeltas.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-600">
                {supportingDeltas.map((metric) => (
                  <span key={metric.name}>
                    {metric.name.replace(/^method:/i, '')} {metric.delta >= 0 ? '+' : ''}{(metric.delta * 100).toFixed(2)} pp
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {showActions && !completed && hasRoute && (
        <div className="mt-4 flex flex-wrap items-center justify-end gap-2 border-t border-slate-200 pt-4">
          {!nextRunId && !iterationHumanReady ? (
            <Button variant="outline" onClick={onOpenSignoff}>
              <ShieldCheck className="mr-2 h-4 w-4" />
              {text('完成本轮签核', 'Complete iteration signoff')}
            </Button>
          ) : !nextRunId ? (
            <Button onClick={onCreateIteration} disabled={Boolean(actionLoading)}>
              <RotateCcw className="mr-2 h-4 w-4" />
              {text(`创建携带 ReviewX 反馈的 V${iterationNumber}`, `Create V${iterationNumber} with ReviewX feedback`)}
            </Button>
          ) : nextRunCompleted ? (
            <Button onClick={onAuditIteration} disabled={Boolean(actionLoading)}>
              <SearchCheck className="mr-2 h-4 w-4" />
              {text(`回审 V${iterationNumber}`, `Re-audit V${iterationNumber}`)}
            </Button>
          ) : (
            <Button onClick={onStartIteration} disabled={Boolean(actionLoading) || nextRunActive}>
              <PlayCircle className="mr-2 h-4 w-4" />
              {nextRunActive ? text(`V${iterationNumber} 正在执行`, `V${iterationNumber} is running`) : text(`启动 V${iterationNumber} 重新执行`, `Start V${iterationNumber} re-execution`)}
            </Button>
          )}
        </div>
      )}
    </section>
  )
}
