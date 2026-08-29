import { AlertTriangle, PencilLine, RefreshCw, Sparkles } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useReviewLocale } from '@/lib/reviewLocale'
import type { LiteratureFailureCode, LiteratureFailureSummary } from './literatureFailureSummary'

const ACTION_TEXT: Record<string, [string, string]> = {
  use_english_academic_terms: ['优先使用英文论文术语重新检索', 'Prefer English academic terms for the retry'],
  wait_for_search_cooldown: ['等待约 5 分钟，避免外部文献接口限流', 'Wait about five minutes for literature API cooldown'],
  resume_after_retry: ['确认检索服务恢复后再继续当前会话', 'Resume this session after search services recover'],
  add_task_method_evaluation: ['在主题中同时写明具体任务、方法和评估目标', 'Name a concrete task, method, and evaluation target'],
  use_multiple_discriminative_terms: ['加入至少三个有区分度的学术概念，避免只写领域名称', 'Use at least three discriminative academic concepts instead of a field name'],
  create_new_session: ['改写后点击“生成研究创意”创建新会话，不要继续旧会话', 'After rewriting, create a new session instead of resuming the old one'],
  broaden_niche_terms: ['适度泛化产品名、缩写或过窄的数据集名称', 'Broaden product names, acronyms, or overly narrow dataset terms'],
  keep_core_task_and_method: ['泛化时保留核心任务和方法，避免变成宽泛领域词', 'Keep the core task and method while broadening'],
  add_domain_and_task_anchors: ['补充研究领域与目标任务，使论文能够明确对齐', 'Add domain and target-task anchors'],
  name_method_or_evaluation_target: ['写明方法名称、数据集、指标或实验约束', 'Name a method, dataset, metric, or experimental constraint'],
  cover_missing_evidence_roles: ['根据缺失项补充任务、方法或评估关键词', 'Add keywords for the missing task, method, or evaluation role'],
  inspect_repair_queries: ['检查系统尝试过的补充检索词，避免重复同类泛化表达', 'Inspect attempted repair queries and avoid equivalent generic wording'],
}

const REASON_TEXT: Record<LiteratureFailureCode, [string, string]> = {
  no_search_results: [
    '外部文献源没有返回结果，可能是检索词、网络超时或接口限流造成的。',
    'No literature source returned results. The query, a timeout, or API rate limiting may be responsible.',
  ],
  seed_too_broad: [
    '检索本身有结果，但当前主题过短或过宽，论文只命中了泛化词，没有同时覆盖具体任务、方法和评估。',
    'Search returned results, but the seed is too short or broad. Papers only matched generic terms instead of a concrete task, method, and evaluation.',
  ],
  eligible_pool_too_small: [
    '部分论文相关，但通过相关性过滤的数量不足。主题可能包含过窄的产品名、缩写或限定条件。',
    'Some papers were relevant, but too few passed filtering. The topic may contain overly narrow names, acronyms, or constraints.',
  ],
  weak_topic_alignment: [
    '有效论文数量尚可，但它们与当前研究问题的语义对齐不足，需要明确领域、任务或方法。',
    'The pool is large enough, but semantic alignment is weak. Clarify the domain, task, or method.',
  ],
  missing_evidence_roles: [
    '论文池缺少流程要求的任务、方法或评估证据，当前证据结构不足以支撑后续创意。',
    'The pool is missing task, method, or evaluation evidence required downstream.',
  ],
  evidence_quality_failed: [
    '文献池没有通过综合质量门禁，请根据统计和补充检索词进一步修改主题。',
    'The literature pool failed the combined quality gate. Use the diagnostics and repair queries to refine the topic.',
  ],
}

function rejectionReasonText(reason: string | undefined, text: (zh: string, en: string) => string) {
  if (reason === 'generic_overlap_only') {
    return text('仅有泛化词重叠', 'generic terms only')
  }
  if (reason === 'missing_text') {
    return text('论文缺少标题或摘要文本', 'missing title or abstract text')
  }
  return reason || text('未分类', 'unclassified')
}

export function LiteratureFailureNotice({
  summary,
  isBusy,
  isSuggesting,
  onEditSeed,
  onAskQwen,
  onResume,
}: {
  summary: LiteratureFailureSummary
  isBusy: boolean
  isSuggesting: boolean
  onEditSeed: () => void
  onAskQwen: () => void
  onResume: () => void
}) {
  const { text } = useReviewLocale()
  const reason = REASON_TEXT[summary.code]
  const queryTemplate = text(
    '“[具体方法]用于[具体任务]，面向[研究领域]，使用[数据集或指标]评估”',
    `“${summary.queryTemplate}”`,
  )

  return (
    <div className="mt-4 overflow-hidden rounded-md border border-amber-300 border-l-4 border-l-amber-600 bg-amber-50/70 dark:border-amber-800 dark:border-l-amber-400 dark:bg-amber-950/25">
      <div className="flex items-start gap-3 border-b border-amber-200 px-4 py-4 dark:border-amber-900">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-700 dark:text-amber-300" />
        <div className="min-w-0">
          <h4 className="text-sm font-semibold text-foreground">
            {text('深读前的文献相关性门禁未通过', 'Literature relevance gate stopped before deep reading')}
          </h4>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">{text(reason[0], reason[1])}</p>
          {summary.seedQuery && (
            <p className="mt-2 break-words font-mono text-xs text-foreground">
              {text('当前主题', 'Current seed')}: {summary.seedQuery}
            </p>
          )}
        </div>
      </div>

      <div className="space-y-3 px-4 py-4">
        <div className="flex flex-wrap gap-2">
          <Button type="button" onClick={onAskQwen} disabled={isBusy || isSuggesting} className="bg-cyan-700 text-white hover:bg-cyan-800 dark:bg-cyan-400 dark:text-neutral-950 dark:hover:bg-cyan-300">
            {isSuggesting ? <RefreshCw className="mr-2 h-4 w-4 animate-spin" /> : <Sparkles className="mr-2 h-4 w-4" />}
            {text('让千问推荐 3 个可用主题', 'Ask Qwen for 3 usable topics')}
          </Button>
          <Button type="button" variant="outline" onClick={onEditSeed}>
            <PencilLine className="mr-2 h-4 w-4" />
            {text('我自己修改', 'Edit manually')}
          </Button>
          {summary.resumeRecommended && (
            <Button type="button" variant="outline" onClick={onResume} disabled={isBusy}>
              <RefreshCw className={`mr-2 h-4 w-4 ${isBusy ? 'animate-spin' : ''}`} />
              {text('检索服务恢复后重试', 'Retry after search recovers')}
            </Button>
          )}
        </div>

        <details className="rounded-md border border-amber-200 bg-background/70 px-3 py-2 dark:border-amber-900">
          <summary className="cursor-pointer text-xs font-medium text-muted-foreground">
            {text('查看技术诊断', 'View technical diagnostics')}
          </summary>
          <div className="mt-3 space-y-4 border-t border-border pt-3">
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {[
                [text('原始结果', 'Raw results'), summary.rawResultCount],
                [text('去重结果', 'Unique results'), summary.uniqueResultCount],
                [text('有效论文', 'Eligible papers'), `${summary.eligiblePaperCount} / ${summary.minPaperCount}`],
                [text('主题对齐', 'Topic aligned'), `${summary.alignedPaperCount} / ${summary.minAlignedPaperCount}`],
              ].map(([label, value]) => (
                <div key={label} className="border-l-2 border-amber-400 px-2 py-1">
                  <p className="text-[11px] text-muted-foreground">{label}</p>
                  <p className="mt-0.5 font-mono text-sm font-semibold text-foreground">{value}</p>
                </div>
              ))}
            </div>

            <div className="flex flex-wrap gap-2 text-xs">
              {summary.rejectedPaperCount > 0 && <Badge variant="outline">{text('已过滤', 'Filtered')} {summary.rejectedPaperCount}</Badge>}
              {summary.dominantRejectionReason && (
                <Badge variant="outline">
                  {text('主要原因', 'Main reason')}: {rejectionReasonText(summary.dominantRejectionReason, text)} ({summary.dominantRejectionCount})
                </Badge>
              )}
              <Badge variant="outline">{text('对齐阈值', 'Alignment threshold')} ≥ {Math.round(summary.minAlignmentScore * 100)}%</Badge>
            </div>

            {summary.seedAnchors.length > 0 && (
              <p className="text-xs leading-5 text-muted-foreground">
                {text('系统识别到的有效主题锚点', 'Recognized topic anchors')}: <span className="font-mono text-foreground">{summary.seedAnchors.join(', ')}</span>
              </p>
            )}

            <div>
              <p className="text-xs font-semibold text-foreground">{text('建议改写格式', 'Recommended query structure')}</p>
              <p className="mt-1 border-l-2 border-teal-500 px-3 py-2 font-mono text-xs leading-5 text-foreground">{queryTemplate}</p>
            </div>

            <ol className="space-y-1.5 text-xs leading-5 text-muted-foreground">
              {summary.actionCodes.map((code, index) => {
                const action = ACTION_TEXT[code] || [code, code]
                return <li key={code}>{index + 1}. {text(action[0], action[1])}</li>
              })}
            </ol>

            {summary.repairQueries.length > 0 && (
              <div>
                <p className="text-xs font-semibold text-foreground">{text('系统已经尝试的补充检索', 'Repair searches already attempted')}</p>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {summary.repairQueries.slice(0, 4).map((query) => (
                    <Badge key={query} variant="outline" className="max-w-full text-xs text-muted-foreground">
                      <span className="truncate">{query}</span>
                    </Badge>
                  ))}
                </div>
              </div>
            )}
          </div>
        </details>
      </div>
    </div>
  )
}
