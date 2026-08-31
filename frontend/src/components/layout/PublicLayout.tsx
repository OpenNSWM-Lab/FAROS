import { Link, Outlet } from 'react-router-dom'
import { Activity, FlaskConical } from 'lucide-react'

import { AppLogo } from '@/components/branding/AppLogo'
import { buttonVariants } from '@/components/ui/button'
import { useReviewLocale } from '@/lib/reviewLocale'
import { cn } from '@/lib/utils'
import { LanguageToggle } from './LanguageToggle'
import { ThemeToggle } from './ThemeToggle'

export function PublicLayout() {
  const { text } = useReviewLocale()

  return (
    <div className="min-h-screen bg-background text-foreground" data-public-shell>
      <header className="sticky top-0 z-50 border-b border-border bg-background/90 backdrop-blur-md">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <Link to="/" className="transition-opacity hover:opacity-80">
            <AppLogo size="md" variant="full" />
          </Link>

          <nav className="hidden items-center gap-6 md:flex" aria-label={text('首页导航', 'Homepage navigation')}>
            <Link to="/research/pipeline" className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-teal-600 dark:hover:text-teal-400">
              <FlaskConical className="h-4 w-4" />
              {text('科研流程', 'Pipeline')}
            </Link>
            <Link to="/system/health" className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-teal-600 dark:hover:text-teal-400">
              <Activity className="h-4 w-4" />
              {text('系统状态', 'System health')}
            </Link>
          </nav>

          <div className="flex items-center gap-2">
            <LanguageToggle />
            <ThemeToggle />
            <Link
              to="/research/pipeline"
              className={cn(buttonVariants(), 'hidden bg-teal-600 text-white hover:bg-teal-700 sm:inline-flex dark:bg-teal-500 dark:text-neutral-950 dark:hover:bg-teal-400')}
            >
              {text('开始使用', 'Get started')}
            </Link>
          </div>
        </div>
      </header>

      <main>
        <Outlet />
      </main>

      <footer className="border-t border-border bg-card">
        <div className="mx-auto flex max-w-7xl flex-col gap-4 px-6 py-8 text-sm text-muted-foreground sm:flex-row sm:items-center sm:justify-between">
          <AppLogo size="sm" variant="full" />
          <nav className="flex flex-wrap gap-x-5 gap-y-2" aria-label={text('页脚导航', 'Footer navigation')}>
            <Link to="/runs" className="hover:text-teal-600 dark:hover:text-teal-400">Runs</Link>
            <Link to="/experiments" className="hover:text-teal-600 dark:hover:text-teal-400">{text('实验', 'Experiments')}</Link>
            <Link to="/papers" className="hover:text-teal-600 dark:hover:text-teal-400">{text('论文', 'Papers')}</Link>
            <Link to="/review/consistency" className="hover:text-teal-600 dark:hover:text-teal-400">ReviewX</Link>
          </nav>
          <span>{text('可追踪、可复核的 AI 科研流程', 'Traceable and auditable AI research')}</span>
        </div>
      </footer>
    </div>
  )
}
