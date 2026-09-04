import { StrictMode, type ReactNode } from 'react'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ConsistencyChecker } from './ConsistencyChecker'

vi.mock('@/components/layout/AppPageLayout', () => ({
  AppPageLayout: ({ children }: { children: ReactNode }) => <>{children}</>,
}))

vi.mock('@/components/review/ExperimentFeedbackPanel', () => ({
  ExperimentFeedbackPanel: () => <div>Feedback panel content</div>,
}))

vi.mock('@/lib/hooks/useApi', () => ({
  usePapers: () => ({
    data: [{ id: 'paper-1', title: 'ReviewX fixture paper' }],
    isLoading: false,
  }),
  useReviewFindings: () => ({ data: [], isLoading: false }),
  useRunConsistencyCheck: () => ({
    mutate: vi.fn(),
    isPending: false,
    isError: false,
    error: null,
  }),
}))

const response = (payload: unknown, ok = true) => ({
  ok,
  json: async () => payload,
}) as Response

describe('ConsistencyChecker', () => {
  beforeEach(() => {
    window.localStorage.setItem('faros.review.locale', 'zh-CN')
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/history?paperId=paper-1')) {
        return response({ reviews: [{
          id: 'review-1',
          paperId: 'paper-1',
          status: 'completed',
          budgetMode: 'balanced',
          findingCount: 0,
          claimCount: 3,
          evidenceCount: 8,
          verificationCount: 4,
        }] })
      }
      if (url.endsWith('/review-1/findings')) return response([])
      if (url.endsWith('/reviewx/review-1')) {
        return response({
          id: 'review-1',
          paperId: 'paper-1',
          scoreSuggestion: 8,
          claims: [{ id: 'claim-1' }, { id: 'claim-2' }, { id: 'claim-3' }],
          jsonReport: { summary: { claimCount: 3, evidenceCount: 8, verificationCount: 4 } },
          actionItems: [],
          riskTree: [],
          mismatchReport: {
            aggregate: { meanMismatch: 0.2, maxMismatch: 0.2, highMismatchClaimCount: 0, dimensionMax: {} },
            method: { formula: 'M(c,E)=max(coverage_gap,numeric_contradiction)' },
            claimScores: [],
          },
          evidenceGraph: { nodes: [], edges: [], nodeCount: 0, edgeCount: 0 },
          modelTrace: { routingMode: 'balanced', llmCalls: [] },
        })
      }
      if (url.includes('/reviews/requests?reviewId=review-1')) return response({ requests: [] })
      if (url.includes('/reviewx/compare?')) return response({}, false)
      return response({})
    }))
  })

  it('keeps a deep-linked saved review loaded under React strict effects', async () => {
    render(
      <StrictMode>
        <MemoryRouter initialEntries={['/review/consistency?paperId=paper-1&reviewId=review-1']}>
          <ConsistencyChecker />
        </MemoryRouter>
      </StrictMode>,
    )

    expect(await screen.findByText('本次审计概览')).toBeInTheDocument()
    expect(screen.getByText('已审计 3 条主张，未发现证据矛盾。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /实验反馈闭环与人工签核/ })).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('Feedback panel content')).not.toBeInTheDocument()
  })
})
