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
      iconBg: 'bg-teal-50',
      iconText: 'text-teal-600',
      accent: 'from-teal-400 to-cyan-400',
      border: 'border-l-teal-500',
    },
    cyan: {
      iconBg: 'bg-cyan-50',
      iconText: 'text-cyan-600',
      accent: 'from-cyan-400 to-blue-400',
      border: 'border-l-cyan-500',
    },
    indigo: {
      iconBg: 'bg-indigo-50',
      iconText: 'text-indigo-600',
      accent: 'from-indigo-400 to-purple-400',
      border: 'border-l-indigo-500',
    },
    slate: {
      iconBg: 'bg-slate-50',
      iconText: 'text-slate-600',
      accent: 'from-slate-400 to-slate-500',
      border: 'border-l-slate-500',
    },
    orange: {
      iconBg: 'bg-orange-50',
      iconText: 'text-orange-600',
      accent: 'from-orange-400 to-amber-400',
      border: 'border-l-orange-500',
    },
  }

  const colors = colorMap[selectedColor as keyof typeof colorMap] || colorMap.teal

  return (
    <div className="mb-8">
      {breadcrumb && (
        <div className="mb-4 text-sm text-slate-600">
          {breadcrumb}
        </div>
      )}

      <div className={`relative bg-gradient-to-r ${colors.accent} p-[2px] rounded-lg mb-6`}>
        <div className="rounded-lg bg-white p-4 sm:p-6">
          <div className="flex flex-col items-start gap-4 md:flex-row md:justify-between">
            <div className="flex items-start gap-4 flex-1 min-w-0">
              {Icon && (
                <div className={`h-12 w-12 rounded-xl ${colors.iconBg} flex items-center justify-center flex-shrink-0`}>
                  <Icon className={`h-6 w-6 ${colors.iconText}`} />
                </div>
              )}
              <div className="flex-1 min-w-0">
                <h1 className="mb-2 text-2xl font-bold leading-tight text-slate-900 sm:text-4xl">
                  {title}
                </h1>
                {subtitle && (
                  <p className="text-base leading-relaxed text-slate-600 sm:text-lg">
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

      <div className="h-px w-full bg-gradient-to-r from-slate-200 via-slate-300 to-slate-200" data-testid="header-divider" />
    </div>
  )
}
