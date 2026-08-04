import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { IdeaGenerationPanel } from './IdeaGenerationPanel'

const jsonResponse = (body: unknown) =>
  Promise.resolve({
    ok: true,
    json: () => Promise.resolve(body),
  } as Response)

describe('IdeaGenerationPanel', () => {
  beforeEach(() => {
    localStorage.setItem('idea_active_session_id', 'idea_done')
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) => {
        if (url.endsWith('/api/v1/providers')) {
          return jsonResponse({ activeProvider: 'qwen', providers: [{ providerName: 'qwen', model: 'qwen-max' }] })
        }
        if (url.endsWith('/api/v1/ideas/sessions')) {
          return jsonResponse({ sessions: [] })
        }
        if (url.endsWith('/api/v1/ideas/sessions/idea_done')) {
          return jsonResponse({
            id: 'idea_done',
            status: 'completed',
            config: {
              seedQuery: 'high-risk RAG citation fidelity',
              providerName: 'qwen',
              model: 'qwen-max',
              paperType: 'algorithm',
              maxCandidates: 3,
              maxReviewIterations: 2,
            },
            candidateIds: ['cand_final', 'cand_rejected'],
            finalCandidateIds: ['cand_final'],
            hiddenCandidateIds: [],
            rejectedCandidateIds: ['cand_rejected'],
          })
        }
        if (url.endsWith('/api/v1/ideas/sessions/idea_done/trace')) {
          return jsonResponse({ steps: [], totalSteps: 0, successfulSteps: 0, failedSteps: 0 })
        }
        if (url.endsWith('/api/v1/ideas/sessions/idea_done/literature')) {
          return jsonResponse({ items: [] })
        }
        if (url.endsWith('/api/v1/ideas/sessions/idea_done/candidates')) {
          return jsonResponse({ candidates: [] })
        }
        return jsonResponse({})
      }),
    )
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  it('does not show the internal rejected-candidate badge in the session summary', async () => {
    render(
      <MemoryRouter>
        <IdeaGenerationPanel />
      </MemoryRouter>,
    )

    await screen.findByText('Session: idea_done')

    expect(screen.getByText('Final ideas: 1')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.queryByText(/Repaired\/rejected/i)).not.toBeInTheDocument()
    })
  })
})
