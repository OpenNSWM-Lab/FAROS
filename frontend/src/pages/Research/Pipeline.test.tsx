import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { MemoryRouter, useNavigate } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { ResearchPipeline } from './Pipeline'

vi.mock('@/components/layout/AppPageLayout', () => ({
  AppPageLayout: ({ children }: { children: ReactNode }) => <>{children}</>,
}))

vi.mock('@/components/ideas/IdeaGenerationPanel', () => ({
  IdeaGenerationPanel: () => <div>Idea panel</div>,
}))

vi.mock('@/components/plans/PlanGenerationPanel', () => ({
  PlanGenerationPanel: ({
    ideaSessionId,
    ideaCandidateId,
    ideaCandidateTitle,
  }: {
    ideaSessionId?: string
    ideaCandidateId?: string
    ideaCandidateTitle?: string
  }) => (
    <div>
      Plan restored: {ideaSessionId} / {ideaCandidateId} / {ideaCandidateTitle}
    </div>
  ),
}))

vi.mock('@/components/research/VerifiedResearchHistories', () => ({
  VerifiedResearchHistories: () => null,
}))

function PipelineWithHistoryNavigation() {
  const navigate = useNavigate()
  return (
    <>
      <button
        type="button"
        onClick={() => navigate('/research/pipeline?ideaSessionId=idea_002&ideaCandidateId=cand_002&ideaCandidateTitle=Climate+Evidence&phase=plan')}
      >
        Open verified plan
      </button>
      <ResearchPipeline />
    </>
  )
}

describe('ResearchPipeline', () => {
  it('restores the selected candidate and Plan stage from the URL after refresh', async () => {
    render(
      <MemoryRouter initialEntries={[
        '/research/pipeline?ideaSessionId=idea_001&ideaCandidateId=cand_001&ideaCandidateTitle=Reliable+RAG',
      ]}>
        <ResearchPipeline />
      </MemoryRouter>,
    )

    expect(screen.getByText('Idea panel')).toBeInTheDocument()
    expect(await screen.findByText('Plan restored: idea_001 / cand_001 / Reliable RAG')).toBeInTheDocument()
  })

  it('updates the visible workflow when a history link changes URL parameters in place', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={[
        '/research/pipeline?ideaSessionId=idea_001&ideaCandidateId=cand_001&ideaCandidateTitle=Reliable+RAG',
      ]}>
        <PipelineWithHistoryNavigation />
      </MemoryRouter>,
    )

    expect(await screen.findByText('Plan restored: idea_001 / cand_001 / Reliable RAG')).toBeInTheDocument()
    await act(async () => {
      await user.click(screen.getByRole('button', { name: 'Open verified plan' }))
    })
    expect(await screen.findByText('Plan restored: idea_002 / cand_002 / Climate Evidence')).toBeInTheDocument()
  })
})
