import { describe, expect, it } from 'vitest'

import { summarizeLiteratureFailure } from './literatureFailureSummary'

describe('summarizeLiteratureFailure', () => {
  it('derives actionable broad-seed guidance from an existing failed trace', () => {
    const summary = summarizeLiteratureFailure([
      {
        name: 'literatureSearch',
        status: 'failed',
        inputs: { seedQuery: 'AI scientist' },
        outputs: {
          resultCountBeforeDedup: 360,
          uniqueResultCount: 182,
          filteredOutCount: 182,
          evidenceTierCounts: { direct: 0, transferable: 0, rejected: 182 },
          rejectionReasonCounts: { generic_overlap_only: 182 },
          repairQueries: ['AI scientist evaluation', 'AI scientist method'],
          paperQualityGate: {
            paperCount: 0,
            alignedPaperCount: 0,
          },
        },
      },
    ])

    expect(summary).toMatchObject({
      code: 'seed_too_broad',
      seedQuery: 'AI scientist',
      rawResultCount: 360,
      uniqueResultCount: 182,
      eligiblePaperCount: 0,
      alignedPaperCount: 0,
      rejectedPaperCount: 182,
      minPaperCount: 4,
      minAlignedPaperCount: 3,
      resumeRecommended: false,
    })
    expect(summary?.actionCodes).toContain('create_new_session')
    expect(summary?.repairQueries).toHaveLength(2)
  })

  it('uses structured backend guidance when available', () => {
    const summary = summarizeLiteratureFailure([
      {
        name: 'literatureSearch',
        status: 'failed',
        outputs: {
          failureDiagnosis: {
            code: 'no_search_results',
            seedQuery: 'specialized topic',
            rawResultCount: 0,
            uniqueResultCount: 0,
            eligiblePaperCount: 0,
            alignedPaperCount: 0,
            rejectedPaperCount: 0,
            requirements: { minPaperCount: 5, minAlignedPaperCount: 3, minAlignmentScore: 0.4 },
            resumeRecommended: true,
            actionCodes: ['wait_for_search_cooldown', 'resume_after_retry'],
          },
        },
      },
    ])

    expect(summary).toMatchObject({
      code: 'no_search_results',
      minPaperCount: 5,
      minAlignmentScore: 0.4,
      resumeRecommended: true,
    })
  })

  it('returns null when literature search did not fail', () => {
    expect(summarizeLiteratureFailure([
      { name: 'literatureSearch', status: 'ok', outputs: {} },
    ])).toBeNull()
  })
})
