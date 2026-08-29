import { ReactNode } from 'react'
import { LucideIcon } from 'lucide-react'
import { PageHeader } from './PageHeader'

type HeaderVizVariant = 'sparkline' | 'miniBars' | 'donut' | 'metricCapsules'

interface AppPageLayoutProps {
  title: string
  subtitle?: string
  icon?: LucideIcon
  iconColor?: string
  accentColor?: string
  actions?: ReactNode
  breadcrumb?: ReactNode
  headerViz?: HeaderVizVariant
  headerVizData?: number[]
  children: ReactNode
}

export function AppPageLayout({
  title,
  subtitle,
  icon,
  iconColor,
  accentColor,
  actions,
  breadcrumb,
  headerViz,
  headerVizData,
  children
}: AppPageLayoutProps) {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <div className="mx-auto w-full max-w-[1680px] px-4 sm:px-6 lg:px-10 2xl:max-w-[1840px] py-8" data-testid="page-container">
        <PageHeader
          title={title}
          subtitle={subtitle}
          icon={icon}
          iconColor={iconColor}
          accentColor={accentColor}
          actions={actions}
          breadcrumb={breadcrumb}
          headerViz={headerViz}
          headerVizData={headerVizData}
        />

        {children}
      </div>
    </div>
  )
}
