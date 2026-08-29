import { AppPageLayout } from '@/components/layout/AppPageLayout'
import { IdeaGenerationPanel } from '@/components/ideas/IdeaGenerationPanel'
import { Lightbulb } from 'lucide-react'
import { useReviewLocale } from '@/lib/reviewLocale'

export function ResearchIdeas() {
  const { text } = useReviewLocale()
  return (
    <AppPageLayout
      title={text('研究创意生成', 'Idea Generation')}
      subtitle={text('基于文献证据与大模型分析生成研究创意', 'Generate novel research ideas using AI-powered analysis')}
      icon={Lightbulb}
      iconColor="amber"
      accentColor="amber"
      headerViz="metricCapsules"
    >
      <IdeaGenerationPanel />
    </AppPageLayout>
  )
}
