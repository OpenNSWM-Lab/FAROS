import { useEffect, useMemo, useState } from 'react'
import { Download, ExternalLink, FileCheck2, Loader2, Printer, ShieldCheck } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { buttonVariants } from '@/components/ui/button'
import { API_BASE_URL } from '@/lib/api'
import { useReviewLocale } from '@/lib/reviewLocale'

export interface DossierMetric {
  name: string
  direction: string
  baseline: number | null
  current: number | null
  delta: number | null
  ciLower: number | null
  ciUpper: number | null
  decision: string
  interpretation?: string
  role: string
  split: string
  sourceArtifactId?: string | null
  source: string
}

export interface SignoffDossierData {
  schemaVersion: string
  release: 'draft' | 'official'
  watermark?: string | null
  generatedAt: string
  contentHash: string
  subject: {
    feedbackId: string
    runId: string
    researchSeriesId: string
    scientificQuestion: string
    planPackageId: string
    iterationNumber: number
    artifactHash: string
  }
  executiveDecision: {
    iterationDecision: string
    qualityGate: string
    publicationReady: boolean
    blockingReasons: Array<{ code: string; message: string; nextStep: string }>
  }
  plan: {
    hypothesis: string
    baseline: string
    intervention: string
    primaryMetric: string
    guardrails: unknown[]
    stopConditions: unknown[]
    delta: {
      changedSections: string[]
      parameterChanges: Array<{
        field: string
        oldValue: unknown
        newValue: unknown
        rationale: string
        targetNode: string
      }>
      evidenceReferences: string[]
    }
  }
  evidence: {
    dataSource: unknown[]
    dataSplitPolicy: string
    metrics: DossierMetric[]
  }
  review: {
    findingCounts: Record<string, number>
    findings: Array<Record<string, unknown>>
    humanFeedback: Record<string, unknown>
    acceptanceConditions: Record<string, unknown>
  }
  limitations: string[]
  provenance: {
    sourceArtifacts: Record<string, string>
    benchmarkFingerprint: string
    qwenCalls: Array<Record<string, unknown>>
    auditIntegrity: { valid: boolean; eventCount?: number }
  }
  signoffs: Record<string, {
    status: string
    reviewerName?: string | null
    reviewerId?: string | null
    actorAccountId?: string | null
    authAssurance?: string | null
    decidedAt?: string | null
    artifactHash?: string | null
    stale?: boolean
  }>
}

function valueText(value: unknown) {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(4)
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

export function SignoffDossier({ feedbackId, refreshKey = 0 }: { feedbackId: string; refreshKey?: number }) {
  const { text } = useReviewLocale()
  const [dossier, setDossier] = useState<SignoffDossierData | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')
    fetch(`${API_BASE_URL}/api/v1/reviews/reviewx/experiment-feedback/${encodeURIComponent(feedbackId)}/signoff-dossier`)
      .then(async (response) => {
        const payload = await response.json().catch(() => ({}))
        if (!response.ok) throw new Error(String(payload.detail || `Dossier unavailable (${response.status})`))
        return payload as SignoffDossierData
      })
      .then((payload) => {
        if (!cancelled) setDossier(payload)
      })
      .catch((loadError) => {
        if (!cancelled) setError(loadError instanceof Error ? loadError.message : 'Dossier unavailable')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [feedbackId, refreshKey])

  const ids = useMemo(() => ({
    overview: `dossier-${feedbackId}-overview`,
    plan: `dossier-${feedbackId}-plan`,
    evidence: `dossier-${feedbackId}-evidence`,
    audit: `dossier-${feedbackId}-audit`,
  }), [feedbackId])

  if (loading) {
    return (
      <div aria-live="polite" className="flex min-h-40 items-center justify-center border-y border-slate-200 dark:border-slate-700">
        <Loader2 className="mr-2 h-5 w-5 animate-spin" />
        {text('正在构建签核摘要…', 'Building signoff summary…')}
      </div>
    )
  }
  if (error || !dossier) {
    return <div role="alert" className="border-y border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200">{error}</div>
  }

  const primary = dossier.evidence.metrics.find((metric) => metric.role === 'primary') || dossier.evidence.metrics[0]
  const guardrails = dossier.evidence.metrics.filter((metric) => metric.role === 'guardrail')
  const base = `${API_BASE_URL}/api/v1/reviews/reviewx/experiment-feedback/${encodeURIComponent(feedbackId)}`

  return (
    <div className="border-y border-slate-200 bg-white py-4 text-slate-900 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100" data-testid="signoff-dossier">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3 px-1">
        <div>
          <div className="flex items-center gap-2 text-base font-semibold"><FileCheck2 className="h-5 w-5 text-emerald-700 dark:text-emerald-400" />{text('人类可读签核摘要', 'Human-readable signoff summary')}</div>
          <div className="mt-1 font-mono text-[11px] text-slate-500 dark:text-slate-400">{dossier.contentHash.slice(0, 28)}</div>
        </div>
        <div className="flex flex-wrap gap-2">
          <a href={`#${ids.overview}`} className={buttonVariants({ size: 'sm' })}><ShieldCheck className="mr-2 h-4 w-4" />{text('查看签核摘要', 'Review signoff summary')}</a>
          <a href={`${base}/signoff-dossier.html?release=draft`} target="_blank" rel="noreferrer" className={buttonVariants({ variant: 'outline', size: 'sm' })}><Printer className="mr-2 h-4 w-4" />{text('打印/导出档案', 'Print / export dossier')}</a>
          <a href={`${base}/evidence-bundle?release=draft`} download className={buttonVariants({ variant: 'outline', size: 'sm' })}><Download className="mr-2 h-4 w-4" />{text('下载 JSON 原始证据', 'Download raw JSON evidence')}</a>
          {dossier.executiveDecision.publicationReady ? (
            <a href={`${base}/signoff-dossier.html?release=official`} target="_blank" rel="noreferrer" className={buttonVariants({ variant: 'outline', size: 'sm' })}><ExternalLink className="mr-2 h-4 w-4" />{text('正式签核档案', 'Official signed dossier')}</a>
          ) : (
            <span aria-disabled="true" className={`${buttonVariants({ variant: 'outline', size: 'sm' })} cursor-not-allowed opacity-50`}><ExternalLink className="mr-2 h-4 w-4" />{text('正式签核档案', 'Official signed dossier')}</span>
          )}
        </div>
      </div>

      <nav aria-label={text('档案分段导航', 'Dossier sections')} className="mb-4 flex gap-1 overflow-x-auto border-b border-slate-200 pb-2 text-sm dark:border-slate-700">
        <a className="whitespace-nowrap px-3 py-1.5 text-emerald-700 dark:text-emerald-400" href={`#${ids.overview}`}>{text('概览', 'Overview')}</a>
        <a className="whitespace-nowrap px-3 py-1.5" href={`#${ids.plan}`}>{text('计划与变更', 'Plan & changes')}</a>
        <a className="whitespace-nowrap px-3 py-1.5" href={`#${ids.evidence}`}>{text('证据与边界', 'Evidence & boundaries')}</a>
        <a className="whitespace-nowrap px-3 py-1.5" href={`#${ids.audit}`}>{text('审计记录', 'Audit trail')}</a>
      </nav>

      <section id={ids.overview} className="scroll-mt-24 px-1">
        <h3 className="text-lg font-semibold">{dossier.subject.scientificQuestion}</h3>
        <div className="mt-3 grid grid-cols-2 gap-px overflow-hidden border border-slate-200 bg-slate-200 sm:grid-cols-5 dark:border-slate-700 dark:bg-slate-700">
          {[
            [text('轮次', 'Round'), `V${dossier.subject.iterationNumber}`],
            [text('主指标', 'Primary metric'), primary ? `${primary.name}: ${valueText(primary.current)}` : '—'],
            [text('统计门控', 'Statistical gate'), primary?.decision || '—'],
            [text('质量门', 'Quality gate'), dossier.executiveDecision.qualityGate],
            [text('可发布', 'Release'), dossier.executiveDecision.publicationReady ? text('是', 'Yes') : text('否', 'No')],
          ].map(([label, value]) => <div key={label} className="min-w-0 bg-white p-3 dark:bg-slate-950"><div className="text-[11px] uppercase text-slate-500 dark:text-slate-400">{label}</div><div className="mt-1 break-words text-sm font-semibold">{value}</div></div>)}
        </div>
        {primary?.interpretation && primary.interpretation !== '未提供 / Not provided' && <p className="mt-2 text-sm font-medium text-amber-700 dark:text-amber-300">{primary.interpretation}</p>}
        {guardrails.length > 0 && <div className="mt-3 text-sm"><b>Guardrails：</b>{guardrails.map((metric) => `${metric.name} ${valueText(metric.current)} (${metric.decision})`).join(' · ')}</div>}
        {dossier.executiveDecision.blockingReasons.length > 0 && (
          <div className="mt-4 border-l-4 border-red-600 bg-red-50 px-4 py-3 dark:bg-red-950/30">
            <div className="font-semibold text-red-800 dark:text-red-200">{text('正式发布阻断项', 'Official release blockers')}</div>
            <ul className="mt-2 space-y-2 text-sm text-red-900 dark:text-red-100">{dossier.executiveDecision.blockingReasons.map((reason) => <li key={reason.code}><b>{reason.message}</b><span className="block text-xs">{text('下一步：', 'Next: ')}{reason.nextStep}</span></li>)}</ul>
          </div>
        )}
      </section>

      <section id={ids.plan} className="scroll-mt-24 px-1 pt-6">
        <h3 className="text-base font-semibold">{text('计划与 Plan Delta', 'Plan and Plan Delta')}</h3>
        <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-[9rem_1fr]"><dt className="text-slate-500">{text('假设', 'Hypothesis')}</dt><dd>{dossier.plan.hypothesis}</dd><dt className="text-slate-500">{text('基线', 'Baseline')}</dt><dd>{dossier.plan.baseline}</dd><dt className="text-slate-500">{text('干预', 'Intervention')}</dt><dd>{dossier.plan.intervention}</dd></dl>
        <div className="mt-3 overflow-x-auto"><table className="w-full min-w-[680px] border-collapse text-left text-sm"><thead><tr className="border-y border-slate-200 dark:border-slate-700"><th className="p-2">{text('字段', 'Field')}</th><th className="p-2">{text('旧值', 'Old')}</th><th className="p-2">{text('新值', 'New')}</th><th className="p-2">{text('依据', 'Evidence')}</th><th className="p-2">{text('影响节点', 'Target node')}</th></tr></thead><tbody>{dossier.plan.delta.parameterChanges.length ? dossier.plan.delta.parameterChanges.map((change, index) => <tr key={`${change.field}-${index}`} className="border-b border-slate-100 dark:border-slate-800"><td className="p-2 font-medium">{change.field}</td><td className="p-2">{valueText(change.oldValue)}</td><td className="p-2">{valueText(change.newValue)}</td><td className="p-2">{change.rationale}</td><td className="p-2">{change.targetNode}</td></tr>) : <tr><td colSpan={5} className="p-3 text-slate-500">{text('未提供结构化 Plan Delta。', 'No structured Plan Delta provided.')}</td></tr>}</tbody></table></div>
      </section>

      <section id={ids.evidence} className="scroll-mt-24 px-1 pt-6">
        <h3 className="text-base font-semibold">{text('证据与结论边界', 'Evidence and claim boundaries')}</h3>
        <p className="mt-2 text-sm"><b>{text('数据划分：', 'Data split: ')}</b>{dossier.evidence.dataSplitPolicy}</p>
        <div className="mt-3 overflow-x-auto"><table className="w-full min-w-[820px] border-collapse text-left text-sm"><thead><tr className="border-y border-slate-200 dark:border-slate-700"><th className="p-2">{text('指标', 'Metric')}</th><th className="p-2">{text('方向', 'Direction')}</th><th className="p-2">{text('基线', 'Baseline')}</th><th className="p-2">{text('当前', 'Current')}</th><th className="p-2">Δ</th><th className="p-2">95% CI</th><th className="p-2">{text('门控', 'Gate')}</th><th className="p-2">{text('来源', 'Source')}</th></tr></thead><tbody>{dossier.evidence.metrics.length ? dossier.evidence.metrics.map((metric) => <tr key={`${metric.name}-${metric.split}`} className="border-b border-slate-100 dark:border-slate-800"><td className="p-2 font-medium">{metric.name}<span className="block text-[11px] text-slate-500">{metric.role}</span></td><td className="p-2">{metric.direction}</td><td className="p-2 font-mono">{valueText(metric.baseline)}</td><td className="p-2 font-mono">{valueText(metric.current)}</td><td className="p-2 font-mono">{valueText(metric.delta)}</td><td className="p-2 font-mono">[{valueText(metric.ciLower)}, {valueText(metric.ciUpper)}]</td><td className="p-2"><Badge variant={metric.decision === 'BOUNDARY' || metric.decision === 'KEEP' ? 'outline' : 'secondary'}>{metric.decision}</Badge></td><td className="max-w-44 break-all p-2 text-xs">{metric.sourceArtifactId || metric.source}</td></tr>) : <tr><td colSpan={8} className="p-3 text-slate-500">{text('未提供结构化指标。', 'No structured metrics provided.')}</td></tr>}</tbody></table></div>
        <div className="mt-3 border-l-4 border-amber-500 px-4 py-2 text-sm"><b>{text('限制：', 'Limitations: ')}</b>{dossier.limitations.join(' · ')}</div>
      </section>

      <section id={ids.audit} className="scroll-mt-24 px-1 pt-6">
        <div className="flex items-center justify-between gap-2"><h3 className="text-base font-semibold">{text('审计记录', 'Audit trail')}</h3><Badge variant={dossier.provenance.auditIntegrity.valid ? 'secondary' : 'destructive'}>{dossier.provenance.auditIntegrity.valid ? text('哈希链有效', 'Hash chain valid') : text('哈希链异常', 'Hash chain invalid')}</Badge></div>
        <div className="mt-3 overflow-x-auto"><table className="w-full min-w-[760px] border-collapse text-left text-sm"><thead><tr className="border-y border-slate-200 dark:border-slate-700"><th className="p-2">{text('阶段', 'Stage')}</th><th className="p-2">{text('状态', 'Status')}</th><th className="p-2">{text('签核人', 'Reviewer')}</th><th className="p-2">{text('登录账号', 'Account')}</th><th className="p-2">{text('认证强度', 'Assurance')}</th><th className="p-2">{text('时间与哈希', 'Time & hash')}</th></tr></thead><tbody>{Object.entries(dossier.signoffs).map(([stage, signoff]) => <tr key={stage} className="border-b border-slate-100 dark:border-slate-800"><td className="p-2 font-medium">{stage}</td><td className="p-2">{signoff.status}{signoff.stale ? ` · ${text('已过期', 'stale')}` : ''}</td><td className="p-2">{signoff.reviewerName || signoff.reviewerId || '—'}</td><td className="p-2">{signoff.actorAccountId || '—'}</td><td className="p-2">{signoff.authAssurance || '—'}</td><td className="p-2 text-xs">{signoff.decidedAt || '—'}<span className="block font-mono">{signoff.artifactHash?.slice(0, 20)}</span></td></tr>)}</tbody></table></div>
      </section>
    </div>
  )
}
