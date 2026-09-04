import { useState, useCallback, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { AppPageLayout } from '@/components/layout/AppPageLayout'
import { IdeaGenerationPanel } from '@/components/ideas/IdeaGenerationPanel'
import { PlanGenerationPanel } from '@/components/plans/PlanGenerationPanel'
import { VerifiedResearchHistories } from '@/components/research/VerifiedResearchHistories'
import { FlaskConical, ArrowDown } from 'lucide-react'
import { useReviewLocale } from '@/lib/reviewLocale'

interface CandidateSelection {
  ideaSessionId: string
  ideaCandidateId: string
  ideaCandidateTitle: string
  ideaSeedQuery: string
}

const candidateFromParams = (searchParams: URLSearchParams): CandidateSelection | null => {
  const ideaSessionId = searchParams.get('ideaSessionId')?.trim() || ''
  if (!ideaSessionId) return null
  return {
    ideaSessionId,
    ideaCandidateId: searchParams.get('ideaCandidateId')?.trim() || '',
    ideaCandidateTitle: searchParams.get('ideaCandidateTitle')?.trim() || '',
    ideaSeedQuery: searchParams.get('ideaSeedQuery')?.trim() || '',
  }
}

export function ResearchPipeline() {
  const { text } = useReviewLocale()
  const [searchParams, setSearchParams] = useSearchParams()
  const [selectedCandidate, setSelectedCandidate] = useState<CandidateSelection | null>(() => candidateFromParams(searchParams))
  const requestedPhase = searchParams.get('phase') === 'idea' ? 'idea' : searchParams.get('phase') === 'plan' ? 'plan' : ''
  const selectedIdeaSessionId = selectedCandidate?.ideaSessionId || ''

  useEffect(() => {
    const nextCandidate = candidateFromParams(searchParams)
    setSelectedCandidate((current) => {
      if (!current || !nextCandidate) return nextCandidate
      const unchanged = current.ideaSessionId === nextCandidate.ideaSessionId
        && current.ideaCandidateId === nextCandidate.ideaCandidateId
        && current.ideaCandidateTitle === nextCandidate.ideaCandidateTitle
        && current.ideaSeedQuery === nextCandidate.ideaSeedQuery
      return unchanged ? current : nextCandidate
    })
  }, [searchParams])

  useEffect(() => {
    if (!requestedPhase || (requestedPhase === 'plan' && !selectedIdeaSessionId)) return
    const timer = window.setTimeout(() => {
      document.getElementById(`pipeline-phase-${requestedPhase === 'idea' ? '1' : '2'}`)
        ?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
    }, 80)
    return () => window.clearTimeout(timer)
  }, [requestedPhase, selectedIdeaSessionId])

  const handleCandidateSelected = useCallback((data: CandidateSelection) => {
    setSelectedCandidate(data)
    const next = new URLSearchParams(searchParams)
    next.set('ideaSessionId', data.ideaSessionId)
    next.set('ideaCandidateId', data.ideaCandidateId)
    next.set('ideaCandidateTitle', data.ideaCandidateTitle)
    if (data.ideaSeedQuery) next.set('ideaSeedQuery', data.ideaSeedQuery)
    next.set('phase', 'plan')
    setSearchParams(next, { replace: true })
  }, [searchParams, setSearchParams])

  return (
    <AppPageLayout
      title={text('科研流程', 'Research Pipeline')}
      subtitle={text('从研究选题、文献证据到可执行实验计划的完整流程', 'End-to-end research ideation and planning')}
      icon={FlaskConical}
      iconColor="indigo"
      accentColor="indigo"
      headerViz="metricCapsules"
    >
      <div className="space-y-6">
        <VerifiedResearchHistories />

        {/* Phase 1: Idea Generation */}
        <div id="pipeline-phase-1">
          <div className="mb-4 flex items-center gap-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-indigo-700 text-sm font-bold text-white">
              1
            </div>
            <h2 className="text-lg font-semibold text-slate-900">{text('研究创意生成', 'Idea Generation')}</h2>
            <span className="rounded-full bg-indigo-100 px-2.5 py-0.5 text-xs font-medium text-indigo-800">
              {text('阶段 1', 'Phase 1')}
            </span>
          </div>
          <IdeaGenerationPanel onCandidateSelected={handleCandidateSelected} />
        </div>

        {/* Phase 2: PlanPackage */}
        {selectedCandidate && (
          <>
            <div className="flex justify-center py-2">
              <div className="flex flex-col items-center gap-1 text-slate-400">
                <ArrowDown className="h-6 w-6 animate-bounce" />
                <span className="text-xs">{text('已选择候选创意，正在进入计划阶段', 'Candidate selected, scroll down for planning')}</span>
              </div>
            </div>

            <div id="pipeline-phase-2">
              <div className="mb-4 flex items-center gap-3">
                <div className="flex h-8 w-8 items-center justify-center rounded-full bg-emerald-700 text-sm font-bold text-white">
                  2
                </div>
                <h2 className="text-lg font-semibold text-slate-900">{text('PlanPackage 生成', 'PlanPackage Generation')}</h2>
                <span className="rounded-full bg-emerald-100 px-2.5 py-0.5 text-xs font-medium text-emerald-800">
                  {text('阶段 2', 'Phase 2')}
                </span>
              </div>
              <PlanGenerationPanel
                ideaSessionId={selectedCandidate.ideaSessionId}
                ideaCandidateId={selectedCandidate.ideaCandidateId}
                ideaCandidateTitle={selectedCandidate.ideaCandidateTitle}
                ideaSeedQuery={selectedCandidate.ideaSeedQuery}
              />
            </div>
          </>
        )}
      </div>
    </AppPageLayout>
  )
}
