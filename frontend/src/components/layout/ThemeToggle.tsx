import { Moon, Sun } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { useTheme } from '@/lib/hooks/use-theme'
import { useReviewLocale } from '@/lib/reviewLocale'

export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme()
  const { text } = useReviewLocale()

  const themeLabel = resolvedTheme === 'dark'
    ? text('切换到浅色模式', 'Switch to light mode')
    : text('切换到深色模式', 'Switch to dark mode')

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={() => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')}
      aria-label={themeLabel}
      title={themeLabel}
      className="shrink-0 text-foreground"
    >
      {resolvedTheme === 'dark' ? (
        <Sun className="h-5 w-5" />
      ) : (
        <Moon className="h-5 w-5" />
      )}
    </Button>
  )
}
