import { useNavigate } from 'react-router-dom'
import {
  Activity,
  ArrowRight,
  ClipboardCheck,
  Code2,
  FileText,
  FlaskConical,
  Lightbulb,
  ListChecks,
  PlayCircle,
  Settings,
} from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useRuns } from '@/lib/hooks/useApi'
import { useReviewLocale } from '@/lib/reviewLocale'
import { cn, formatRelativeTime } from '@/lib/utils'

type FlowStep = {
  id: string
  number: string
  title: string
  detail: string
  path: string
  icon: typeof Lightbulb
  edgeClass: string
  iconClass: string
  hoverClass: string
}

export function Homepage() {
  const navigate = useNavigate()
  const { data: runs, isLoading: runsLoading, isError: runsError } = useRuns()
  const { text } = useReviewLocale()
  const recentRuns = runs?.slice(0, 5) || []

  const flowSteps: FlowStep[] = [
    {
      id: 'idea',
      number: '01',
      title: text('研究选题', 'Research idea'),
      detail: text('检索证据并收敛候选方向', 'Retrieve evidence and refine candidates'),
      path: '/research/pipeline',
      icon: Lightbulb,
      edgeClass: 'border-t-teal-500',
      iconClass: 'bg-teal-50 text-teal-700 dark:bg-teal-950 dark:text-teal-300',
      hoverClass: 'hover:bg-teal-50/70 dark:hover:bg-teal-950/30',
    },
    {
      id: 'plan',
      number: '02',
      title: 'PlanPackage',
      detail: text('冻结假设、变量与验收条件', 'Freeze hypotheses, variables, and gates'),
      path: '/research/pipeline',
      icon: ListChecks,
      edgeClass: 'border-t-amber-400',
      iconClass: 'bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300',
      hoverClass: 'hover:bg-amber-50/70 dark:hover:bg-amber-950/30',
    },
    {
      id: 'code',
      number: '03',
      title: 'Code',
      detail: text('从已批准计划生成实验工程', 'Generate an experiment project from an approved plan'),
      path: '/code/workspace',
      icon: Code2,
      edgeClass: 'border-t-sky-500',
      iconClass: 'bg-sky-50 text-sky-700 dark:bg-sky-950 dark:text-sky-300',
      hoverClass: 'hover:bg-sky-50/70 dark:hover:bg-sky-950/30',
    },
    {
      id: 'experiment',
      number: '04',
      title: text('实验', 'Experiment'),
      detail: text('执行、记录并对比实验指标', 'Run, record, and compare experiment metrics'),
      path: '/experiments',
      icon: FlaskConical,
      edgeClass: 'border-t-emerald-500',
      iconClass: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300',
      hoverClass: 'hover:bg-emerald-50/70 dark:hover:bg-emerald-950/30',
    },
    {
      id: 'paper',
      number: '05',
      title: text('论文', 'Paper'),
      detail: text('基于证据包组织论文内容', 'Compose from the research evidence package'),
      path: '/papers',
      icon: FileText,
      edgeClass: 'border-t-indigo-500',
      iconClass: 'bg-indigo-50 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300',
      hoverClass: 'hover:bg-indigo-50/70 dark:hover:bg-indigo-950/30',
    },
    {
      id: 'review',
      number: '06',
      title: 'ReviewX',
      detail: text('核验主张、证据与实验一致性', 'Verify claims, evidence, and experiment consistency'),
      path: '/review/consistency',
      icon: ClipboardCheck,
      edgeClass: 'border-t-rose-500',
      iconClass: 'bg-rose-50 text-rose-700 dark:bg-rose-950 dark:text-rose-300',
      hoverClass: 'hover:bg-rose-50/70 dark:hover:bg-rose-950/30',
    },
  ]

  const supportingViews = [
    {
      title: 'Runs',
      detail: text('查看真实执行记录', 'Inspect recorded executions'),
      path: '/runs',
      icon: PlayCircle,
    },
    {
      title: text('模型设置', 'Model settings'),
      detail: text('配置当前账号的 Provider 与 API Key', 'Configure this account\'s provider and API key'),
      path: '/settings/providers',
      icon: Settings,
    },
    {
      title: text('系统状态', 'System health'),
      detail: text('检查后端服务状态', 'Check backend service status'),
      path: '/system/health',
      icon: Activity,
    },
  ]

  const workspaceState = runsLoading
    ? text('同步中', 'Syncing')
    : runsError
      ? text('暂不可用', 'Unavailable')
      : text('已连接', 'Connected')

  return (
    <div className="min-h-screen bg-background">
      <section className="relative overflow-hidden border-b border-border bg-card">
        <div className="absolute right-0 top-0 h-1 w-1/3 bg-amber-400" aria-hidden="true" />
        <div className="mx-auto max-w-7xl px-6 py-12 lg:py-16">
          <div className="max-w-3xl">
            <div className="mb-5 flex items-center gap-3 text-sm font-semibold text-teal-700 dark:text-teal-300">
              <span className="h-px w-8 bg-teal-500" aria-hidden="true" />
              {text('协同式 AI Scientist 科研系统', 'Collaborative AI Scientist research system')}
            </div>
            <h1 className="font-display text-5xl font-semibold text-foreground sm:text-6xl">FAROS</h1>
            <p className="mt-5 max-w-2xl text-base leading-7 text-muted-foreground sm:text-lg">
              {text(
                '把研究问题、证据、计划、代码、实验、论文与审核连接成一条可追踪的科研链路。',
                'Connect questions, evidence, plans, code, experiments, papers, and review in one traceable research chain.',
              )}
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Button
                size="lg"
                onClick={() => navigate('/research/pipeline')}
                className="bg-teal-600 text-white hover:bg-teal-700 dark:bg-teal-500 dark:text-neutral-950 dark:hover:bg-teal-400"
              >
                <Lightbulb className="mr-2 h-5 w-5" />
                {text('开始科研流程', 'Start research pipeline')}
              </Button>
            </div>

            <div className="mt-8 flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-muted-foreground">
              <span className="flex items-center gap-2">
                <span className={cn(
                  'h-2 w-2 rounded-full',
                  runsError ? 'bg-red-500' : runsLoading ? 'bg-amber-400' : 'bg-emerald-500',
                )} />
                {text('当前账号', 'Current account')} · {workspaceState}
              </span>
            </div>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-7xl px-6 py-12 lg:py-14">
        <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-2xl font-semibold text-foreground">{text('科研主流程', 'Research workflow')}</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              {text('六个阶段，共用同一份研究上下文与证据记录', 'Six stages sharing one research context and evidence record')}
            </p>
          </div>
          <Button variant="ghost" onClick={() => navigate('/research/pipeline')}>
            {text('打开当前流程', 'Open current workflow')}
            <ArrowRight className="ml-2 h-4 w-4" />
          </Button>
        </div>

        <div className="grid gap-px overflow-hidden rounded-md border border-border bg-border sm:grid-cols-2 xl:grid-cols-6">
          {flowSteps.map((step) => {
            const Icon = step.icon
            return (
              <button
                key={step.id}
                type="button"
                onClick={() => navigate(step.path)}
                className={cn(
                  'group min-h-48 border-t-2 bg-card p-5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring',
                  step.edgeClass,
                  step.hoverClass,
                )}
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono text-xs font-semibold text-muted-foreground">{step.number}</span>
                  <div className="flex items-center gap-2">
                    <span className={cn('flex h-9 w-9 items-center justify-center rounded-md', step.iconClass)}>
                      <Icon className="h-5 w-5" />
                    </span>
                  </div>
                </div>
                <h3 className="mt-7 text-base font-semibold text-foreground">{step.title}</h3>
                <p className="mt-2 text-sm leading-5 text-muted-foreground">{step.detail}</p>
                <ArrowRight className="mt-4 h-4 w-4 text-muted-foreground transition-transform group-hover:translate-x-1 group-hover:text-foreground" />
              </button>
            )
          })}
        </div>
      </section>

      <section className="border-y border-border bg-muted/40">
        <div className="mx-auto grid max-w-7xl gap-10 px-6 py-12 lg:grid-cols-[minmax(0,1.35fr)_minmax(300px,0.65fr)]">
          <div>
            <div className="mb-5 flex items-center justify-between gap-3">
              <div>
                <h2 className="text-xl font-semibold text-foreground">{text('最近运行', 'Recent runs')}</h2>
                <p className="mt-1 text-sm text-muted-foreground">{text('工作区中主动创建的真实执行记录', 'Persisted executions explicitly created in this workspace')}</p>
              </div>
              <Button variant="outline" size="sm" onClick={() => navigate('/runs')}>
                {text('全部 Runs', 'All runs')}
              </Button>
            </div>

            {runsLoading ? (
              <div className="flex min-h-40 items-center justify-center rounded-md border border-border bg-card text-sm text-muted-foreground">
                {text('正在读取运行记录...', 'Loading run records...')}
              </div>
            ) : runsError ? (
              <div className="min-h-40 rounded-md border-l-4 border-destructive bg-destructive/10 px-5 py-6 text-sm text-destructive">
                {text('运行记录暂时不可用，请检查系统状态。', 'Run records are unavailable. Check system health.')}
              </div>
            ) : recentRuns.length > 0 ? (
              <div className="divide-y divide-border overflow-hidden rounded-md border border-border">
                {recentRuns.map((run) => (
                  <button
                    key={run.id}
                    type="button"
                    onClick={() => navigate(`/runs/${run.id}`)}
                    className="flex w-full items-center gap-4 bg-card px-4 py-3 text-left hover:bg-accent"
                  >
                    <PlayCircle className="h-5 w-5 shrink-0 text-teal-600 dark:text-teal-400" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-mono text-sm font-medium text-foreground">{run.id}</div>
                      <div className="mt-1 text-xs text-muted-foreground">
                        {run.type} · {formatRelativeTime(run.startedAt)}
                      </div>
                    </div>
                    <Badge variant={run.status === 'completed' ? 'default' : 'outline'}>{run.status}</Badge>
                    <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                  </button>
                ))}
              </div>
            ) : (
              <div className="flex min-h-40 flex-col items-center justify-center rounded-md border border-border bg-card px-6 text-center">
                <p className="text-sm text-muted-foreground">{text('暂无运行记录。', 'No run records yet.')}</p>
                <Button
                  className="mt-4 bg-teal-600 text-white hover:bg-teal-700 dark:bg-teal-500 dark:text-neutral-950 dark:hover:bg-teal-400"
                  size="sm"
                  onClick={() => navigate('/research/pipeline')}
                >
                  {text('创建研究任务', 'Create research task')}
                </Button>
              </div>
            )}
          </div>

          <div className="lg:border-l lg:border-border lg:pl-10">
            <h2 className="text-xl font-semibold text-foreground">{text('辅助入口', 'Supporting views')}</h2>
            <p className="mt-1 text-sm text-muted-foreground">{text('复核、配置与诊断', 'Evidence, configuration, and diagnostics')}</p>
            <div className="mt-5 divide-y divide-border overflow-hidden rounded-md border border-border">
              {supportingViews.map((view) => {
                const Icon = view.icon
                return (
                  <button
                    key={view.path}
                    type="button"
                    onClick={() => navigate(view.path)}
                    className="flex w-full items-center gap-3 bg-card px-4 py-4 text-left hover:bg-accent"
                  >
                    <Icon className="h-5 w-5 shrink-0 text-muted-foreground" />
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-semibold text-foreground">{view.title}</div>
                      <div className="mt-0.5 text-xs leading-5 text-muted-foreground">{view.detail}</div>
                    </div>
                    <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                  </button>
                )
              })}
            </div>
          </div>
        </div>
      </section>
    </div>
  )
}
