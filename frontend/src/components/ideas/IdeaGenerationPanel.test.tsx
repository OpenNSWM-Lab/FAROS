import { fireEvent, render, screen, waitFor } from '@testing-library/react'
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
          return jsonResponse({
            sessions: [{
              id: 'idea_done',
              status: 'completed',
              createdAt: '2026-08-29T08:00:00Z',
              config: { seedQuery: 'high-risk RAG citation fidelity', paperType: 'algorithm' },
            }],
          })
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
          return jsonResponse({
            candidates: [{
              id: 'cand_final',
              title: 'Attribution-aware scientific RAG',
              problem: 'Scientific claims need traceable evidence.',
              keyInsight: 'Optimize retrieval for sentence-level attribution.',
              novelty: 8.4,
              feasibility: 8.2,
              impact: 8.1,
              clarity: 8.0,
              risk: 3.0,
              alignment: 9.0,
              referenceSupport: 8.5,
              experimentSpecificity: 8.3,
              overallScore: 8.4,
            }],
          })
        }
        if (url.endsWith('/api/v1/ideas/seed-suggestion-jobs')) {
          return jsonResponse({ jobId: 'seedjob_001', status: 'pending' })
        }
        if (url.endsWith('/api/v1/ideas/seed-suggestion-jobs/seedjob_001')) {
          return jsonResponse({
            jobId: 'seedjob_001',
            status: 'completed',
            result: {
              model: 'qwen-max',
              suggestions: [{
                titleZh: '科学主张核验',
                titleEn: 'Scientific claim verification',
                query: 'Retrieval-augmented generation for scientific claim verification evaluated by claim-level F1 score',
                rationaleZh: '任务、方法和指标明确。',
                rationaleEn: 'The task, method, and metric are explicit.',
              }],
            },
          })
        }
        return jsonResponse({})
      }),
    )
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  it('starts clean and only restores history after an explicit user action', async () => {
    render(
      <MemoryRouter>
        <IdeaGenerationPanel />
      </MemoryRouter>,
    )

    await screen.findByRole('button', { name: /Research history \(1\)/ })
    expect(screen.queryByText('Research progress')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Research history \(1\)/ }))
    fireEvent.click(await screen.findByRole('button', { name: /high-risk RAG citation fidelity/ }))
    await screen.findByText('Research progress')

    expect(screen.getByText('Research ideas ready: 1')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Use In Planning' })).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.queryByText(/Repaired\/rejected/i)).not.toBeInTheDocument()
    })
  })

  it('lets a first-time user ask Qwen for usable topics', async () => {
    render(
      <MemoryRouter>
        <IdeaGenerationPanel />
      </MemoryRouter>,
    )

    fireEvent.click(await screen.findByRole('button', { name: 'Ask Qwen for 3 topics' }))
    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/v1/ideas/seed-suggestion-jobs'),
        expect.objectContaining({
          method: 'POST',
          body: expect.stringContaining('"count":3'),
        }),
      )
    })
    expect(await screen.findByText('Search-ready topics from Qwen')).toBeInTheDocument()
    expect(screen.getByText(/Scientific claim verification/)).toBeInTheDocument()
  })
})
