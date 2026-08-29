import { ArrowRight, Sparkles } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useReviewLocale } from '@/lib/reviewLocale'

export interface SeedSuggestion {
  titleZh: string
  titleEn: string
  query: string
  rationaleZh?: string
  rationaleEn?: string
}

export function SeedSuggestionList({
  suggestions,
  model,
  onSelect,
}: {
  suggestions: SeedSuggestion[]
  model?: string
  onSelect: (query: string) => void
}) {
  const { locale, text } = useReviewLocale()
  if (!suggestions.length) return null

  return (
    <div className="overflow-hidden rounded-md border border-cyan-200 bg-cyan-50/50 dark:border-cyan-900 dark:bg-cyan-950/20">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-cyan-200 px-3 py-2.5 dark:border-cyan-900">
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-cyan-700 dark:text-cyan-300" />
          <p className="text-sm font-semibold text-foreground">
            {text('千问推荐的可用研究主题', 'Search-ready topics from Qwen')}
          </p>
        </div>
        {model && <Badge variant="outline" className="bg-background text-xs">Qwen / {model}</Badge>}
      </div>

      <div className="divide-y divide-cyan-200 dark:divide-cyan-900">
        {suggestions.map((suggestion, index) => {
          const title = locale === 'zh-CN' ? suggestion.titleZh : suggestion.titleEn
          const rationale = locale === 'zh-CN' ? suggestion.rationaleZh : suggestion.rationaleEn
          return (
            <div key={`${suggestion.query}-${index}`} className="grid gap-3 px-3 py-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
              <div className="min-w-0">
                <p className="text-sm font-semibold text-foreground">{index + 1}. {title}</p>
                {rationale && <p className="mt-1 text-xs leading-5 text-muted-foreground">{rationale}</p>}
                <p className="mt-1.5 break-words text-xs leading-5 text-foreground">{suggestion.query}</p>
              </div>
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="shrink-0 border-cyan-500 bg-background"
                onClick={() => onSelect(suggestion.query)}
                aria-label={text(`采用主题 ${index + 1}`, `Use topic ${index + 1}`)}
              >
                {text('采用', 'Use')}
                <ArrowRight className="ml-1.5 h-3.5 w-3.5" />
              </Button>
            </div>
          )
        })}
      </div>
    </div>
  )
}
