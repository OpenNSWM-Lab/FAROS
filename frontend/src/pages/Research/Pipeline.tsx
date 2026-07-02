import { useState, useCallback } from 'react'
import { AppPageLayout } from '@/components/layout/AppPageLayout'
import { IdeaGenerationPanel } from '@/components/ideas/IdeaGenerationPanel'
import { PlanGenerationPanel } from '@/components/plans/PlanGenerationPanel'
import { FlaskConical, ArrowDown } from 'lucide-react'

interface CandidateSelection {
  ideaSessionId: string
  ideaCandidateId: string
  ideaCandidateTitle: string
  ideaSeedQuery: string
}

export function ResearchPipeline() {
  const [selectedCandidate, setSelectedCandidate] = useState<CandidateSelection | null>(null)

  const handleCandidateSelected = useCallback((data: CandidateSelection) => {
    setSelectedCandidate(data)
    // scroll to plan section
    setTimeout(() => {
      document.getElementById('pipeline-phase-2')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }, 100)
  }, [])

  return (
    <AppPageLayout
      title="Research Pipeline"
      subtitle="End-to-end research ideation and planning"
      icon={FlaskConical}
      iconColor="indigo"
      accentColor="indigo"
      headerViz="metricCapsules"
    >
      <div className="space-y-6">
        {/* Phase 1: Idea Generation */}
        <div id="pipeline-phase-1">
          <div className="mb-4 flex items-center gap-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-indigo-700 text-sm font-bold text-white">
              1
            </div>
            <h2 className="text-lg font-semibold text-slate-900">Idea Generation</h2>
            <span className="rounded-full bg-indigo-100 px-2.5 py-0.5 text-xs font-medium text-indigo-800">
              Phase 1
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
                <span className="text-xs">Candidate selected, scroll down for planning</span>
              </div>
            </div>

            <div id="pipeline-phase-2">
              <div className="mb-4 flex items-center gap-3">
                <div className="flex h-8 w-8 items-center justify-center rounded-full bg-emerald-700 text-sm font-bold text-white">
                  2
                </div>
                <h2 className="text-lg font-semibold text-slate-900">PlanPackage Generation</h2>
                <span className="rounded-full bg-emerald-100 px-2.5 py-0.5 text-xs font-medium text-emerald-800">
                  Phase 2
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
