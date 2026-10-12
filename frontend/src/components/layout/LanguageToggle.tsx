import { Languages } from 'lucide-react'
import { useRef } from 'react'

import { useReviewLocale, type ReviewLocale } from '@/lib/reviewLocale'
import { cn } from '@/lib/utils'

const localeOptions: Array<{ locale: ReviewLocale; label: string; title: string }> = [
  { locale: 'zh-CN', label: '中', title: '切换为中文' },
  { locale: 'en-US', label: 'EN', title: 'Switch to English' },
]

export function LanguageToggle() {
  const { locale, setLocale, text } = useReviewLocale()
  const buttonRefs = useRef<(HTMLButtonElement | null)[]>([])

  const handleKeyDown = (e: React.KeyboardEvent, index: number) => {
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      e.preventDefault()
      const nextIndex = e.key === 'ArrowLeft'
        ? (index - 1 + localeOptions.length) % localeOptions.length
        : (index + 1) % localeOptions.length
      buttonRefs.current[nextIndex]?.focus()
    }
  }

  return (
    <div
      className="flex h-9 shrink-0 items-center gap-1 rounded-md border border-input bg-background px-1 shadow-sm"
      role="group"
      aria-label={text('界面语言', 'Interface language')}
    >
      <Languages className="ml-1 h-4 w-4 text-muted-foreground" aria-hidden="true" />
      {localeOptions.map((option, index) => {
        const active = locale === option.locale
        return (
          <button
            key={option.locale}
            ref={(el) => { buttonRefs.current[index] = el }}
            type="button"
            title={option.title}
            aria-pressed={active}
            onClick={() => setLocale(option.locale)}
            onKeyDown={(e) => handleKeyDown(e, index)}
            className={cn(
              'flex h-7 min-w-8 items-center justify-center rounded px-2 text-xs font-semibold transition-colors',
              active
                ? 'bg-foreground text-background'
                : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
            )}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}
