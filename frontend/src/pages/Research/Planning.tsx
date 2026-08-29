import { AppPageLayout } from '@/components/layout/AppPageLayout'
import { FileText } from 'lucide-react'
import { PlanGenerationPanel } from '@/components/plans/PlanGenerationPanel'
import { useReviewLocale } from '@/lib/reviewLocale'

export function ResearchPlanning() {
  const { text } = useReviewLocale()
  return (
    <AppPageLayout
      title={text('研究计划', 'Plan')}
      subtitle={text('基于研究创意生成可审查、可执行的候选计划', 'Generate candidate research plans using AI-powered analysis')}
      icon={FileText}
      iconColor="teal"
      accentColor="teal"
      headerViz="metricCapsules"
    >
      <PlanGenerationPanel />
    </AppPageLayout>
  )
}
