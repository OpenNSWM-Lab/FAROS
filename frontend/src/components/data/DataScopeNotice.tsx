import { Database, Sparkles } from 'lucide-react'

import { useReviewLocale } from '@/lib/reviewLocale'

export function DataScopeNotice() {
  const { text } = useReviewLocale()

  return (
    <div className="mb-6 flex flex-col gap-3 border-y border-border bg-muted/35 px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between">
      <div className="flex min-w-0 items-center gap-2 font-medium text-foreground">
        <Database className="h-4 w-4 shrink-0 text-teal-600 dark:text-teal-400" />
        <span>{text('团队演示工作区中的已保存记录', 'Saved records in the team demo workspace')}</span>
      </div>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-muted-foreground">
        <span>{text('打开页面：不调用模型', 'Page load: no model call')}</span>
        <span className="inline-flex items-center gap-1.5">
          <Sparkles className="h-3.5 w-3.5 text-amber-500" />
          {text('生成或运行：使用当前账号配置', 'Generate or run: uses current account settings')}
        </span>
      </div>
    </div>
  )
}
