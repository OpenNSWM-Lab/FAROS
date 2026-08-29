import { render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
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
})
