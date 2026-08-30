import { Monitor, Moon, Sun } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { useTheme } from '@/lib/hooks/use-theme'
import type { Theme } from '@/lib/hooks/use-theme'
import { useReviewLocale } from '@/lib/reviewLocale'

const THEME_CYCLE: Theme[] = ['light', 'dark', 'system']

function nextTheme(current: Theme): Theme {
  const idx = THEME_CYCLE.indexOf(current)
  return THEME_CYCLE[(idx + 1) % THEME_CYCLE.length]
}

export function ThemeToggle() {
  const { theme, resolvedTheme, setTheme } = useTheme()
  const { text } = useReviewLocale()

  const next = nextTheme(theme)
  const themeLabel = next === 'dark'
    ? text('切换到深色模式', 'Switch to dark mode')
    : next === 'system'
      ? text('切换到跟随系统', 'Switch to system theme')
      : text('切换到浅色模式', 'Switch to light mode')

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={() => setTheme(next)}
      aria-label={themeLabel}
      title={themeLabel}
      className="shrink-0 text-foreground"
    >
      {theme === 'system' ? (
        <Monitor className="h-5 w-5" />
      ) : resolvedTheme === 'dark' ? (
        <Sun className="h-5 w-5" />
      ) : (
        <Moon className="h-5 w-5" />
      )}
    </Button>
  )
}
