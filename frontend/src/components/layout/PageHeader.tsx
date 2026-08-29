import { ReactNode } from 'react'
import { LucideIcon } from 'lucide-react'
import { HeaderViz } from '@/components/visual/HeaderViz'

type HeaderVizVariant = 'sparkline' | 'miniBars' | 'donut' | 'metricCapsules'

interface PageHeaderProps {
  title: string
  subtitle?: string
  icon?: LucideIcon
  iconColor?: string
  accentColor?: string
  actions?: ReactNode
  breadcrumb?: ReactNode
  headerViz?: HeaderVizVariant
  headerVizData?: number[]
}

export function PageHeader({
  title,
  subtitle,
  icon: Icon,
  iconColor = 'teal',
  accentColor = 'teal',
  actions,
  breadcrumb,
  headerViz,
  headerVizData,
}: PageHeaderProps) {
  // Use iconColor for color selection (same as accentColor in most cases)
  const selectedColor = iconColor || accentColor
  const colorMap = {
    teal: {
      iconBg: 'bg-teal-50 dark:bg-teal-950/60',
      iconText: 'text-teal-600 dark:text-teal-300',
      accent: 'from-teal-400 to-cyan-400',
      border: 'border-l-teal-500',
    },
    cyan: {
      iconBg: 'bg-cyan-50 dark:bg-cyan-950/60',
      iconText: 'text-cyan-600 dark:text-cyan-300',
      accent: 'from-cyan-400 to-blue-400',
      border: 'border-l-cyan-500',
    },
    indigo: {
      iconBg: 'bg-indigo-50 dark:bg-indigo-950/60',
      iconText: 'text-indigo-600 dark:text-indigo-300',
      accent: 'from-indigo-400 to-purple-400',
      border: 'border-l-indigo-500',
    },
    slate: {
      iconBg: 'bg-slate-100 dark:bg-slate-800',
      iconText: 'text-slate-600 dark:text-slate-300',
      accent: 'from-slate-400 to-slate-500',
      border: 'border-l-slate-500',
    },
    orange: {
      iconBg: 'bg-orange-50 dark:bg-orange-950/60',
      iconText: 'text-orange-600 dark:text-orange-300',
      accent: 'from-orange-400 to-amber-400',
      border: 'border-l-orange-500',
    },
  }

  const colors = colorMap[selectedColor as keyof typeof colorMap] || colorMap.teal

  return (
    <div className="mb-8">
      {breadcrumb && (
        <div className="mb-4 text-sm text-muted-foreground">
          {breadcrumb}
        </div>
      )}

      <div className={`relative bg-gradient-to-r ${colors.accent} p-[2px] rounded-lg mb-6`}>
        <div className="rounded-lg bg-card p-4 text-card-foreground sm:p-6">
          <div className="flex flex-col items-start gap-4 md:flex-row md:justify-between">
            <div className="flex items-start gap-4 flex-1 min-w-0">
              {Icon && (
                <div className={`h-12 w-12 rounded-xl ${colors.iconBg} flex items-center justify-center flex-shrink-0`}>
                  <Icon className={`h-6 w-6 ${colors.iconText}`} />
                </div>
              )}
              <div className="flex-1 min-w-0">
                <h1 className="mb-2 text-2xl font-bold leading-tight text-card-foreground sm:text-4xl">
                  {title}
                </h1>
                {subtitle && (
                  <p className="text-base leading-relaxed text-muted-foreground sm:text-lg">
                    {subtitle}
                  </p>
                )}
              </div>
            </div>

            <div className="flex max-w-full shrink-0 flex-wrap items-center gap-4 md:ml-6">
              {headerViz && (
                <HeaderViz variant={headerViz} data={headerVizData} />
              )}
              {actions && (
                <div className="flex items-center gap-3">
                  {actions}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      <div className="h-px w-full bg-border" data-testid="header-divider" />
    </div>
  )
}
