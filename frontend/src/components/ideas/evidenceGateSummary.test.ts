import { describe, expect, it } from 'vitest'
import { summarizeEvidenceGate } from './evidenceGateSummary'

describe('summarizeEvidenceGate', () => {
  it('returns null when the trace has no evidence gate step', () => {
    expect(summarizeEvidenceGate([{ name: 'ideaBrainstorm', status: 'ok', outputs: {} }])).toBeNull()
  })

  it('summarizes a passed evidence gate as ready', () => {
    const summary = summarizeEvidenceGate([
      {
        name: 'evidenceGate',
        status: 'ok',
        outputs: {
          allowedToBrainstorm: true,
          repairAttempted: false,
          evidenceGate: {
            passed: true,
            reviewMode: 'rule+llm',
            paperCount: 8,
            externalPaperCount: 5,
            alignedPaperCount: 4,
            gapSignalCount: 2,
            warnings: ['coverage is adequate'],
            llmReviewer: { score: 0.84 },
          },
        },
      },
    ])

    expect(summary?.tone).toBe('success')
    expect(summary?.title).toBe('Evidence ready')
    expect(summary?.stats).toEqual([
      { label: 'External papers', value: '5' },
      { label: 'Topic aligned', value: '4' },
      { label: 'Gap signals', value: '2' },
      { label: 'Reviewer', value: 'rule+llm' },
    ])
    expect(summary?.reviewerScore).toBe('84%')
  })

  it('summarizes repaired evidence as repaired', () => {
    const summary = summarizeEvidenceGate([
      {
        name: 'evidenceGate',
        status: 'ok',
        outputs: {
          allowedToBrainstorm: true,
          repairAttempted: true,
          repairReport: { queries: ['citation faithfulness limitations'] },
          evidenceGate: {
            passed: true,
            paperCount: 7,
            externalPaperCount: 3,
            alignedPaperCount: 4,
            gapSignalCount: 1,
          },
        },
      },
    ])

    expect(summary?.tone).toBe('warning')
    expect(summary?.title).toBe('Evidence repaired')
    expect(summary?.repairQueries).toEqual(['citation faithfulness limitations'])
  })

  it('summarizes a failed evidence gate with concise issues', () => {
    const summary = summarizeEvidenceGate([
      {
        name: 'evidenceGate',
        status: 'failed',
        error: 'Evidence Gate 2.0 failed before idea generation',
        outputs: {
          allowedToBrainstorm: false,
          repairAttempted: true,
          repairReport: { queries: ['better RAG citation search'] },
          evidenceGate: {
            passed: false,
            hardBlocked: true,
            externalPaperCount: 0,
            alignedPaperCount: 1,
            gapSignalCount: 0,
            errors: [
              'ideaBrainstorm.preflight: insufficient external evidence papers (0 < 2)',
              'ideaBrainstorm.preflight: no explicit gap or limitation signal found in structured evidence',
            ],
            llmReviewer: {
              blockingIssues: ['selected papers are generic language model papers'],
            },
          },
        },
      },
    ])

    expect(summary?.tone).toBe('danger')
    expect(summary?.title).toBe('Evidence not ready')
    expect(summary?.issues).toEqual([
      'insufficient external evidence papers (0 < 2)',
      'no explicit gap or limitation signal found in structured evidence',
      'selected papers are generic language model papers',
    ])
    expect(summary?.repairQueries).toEqual(['better RAG citation search'])
  })

  it('falls back to the step error when failed trace outputs are unavailable', () => {
    const summary = summarizeEvidenceGate([
      {
        name: 'evidenceGate',
        status: 'failed',
        error: 'Evidence Gate 2.0 failed before idea generation: insufficient external evidence papers',
        outputs: {},
      },
    ])

    expect(summary?.tone).toBe('danger')
    expect(summary?.issues).toEqual([
      'Evidence Gate 2.0 failed before idea generation: insufficient external evidence papers',
    ])
  })
})
