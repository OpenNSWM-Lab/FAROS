import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { REVIEW_LOCALE_STORAGE_KEY } from '@/lib/reviewLocale'
import { ReviewIterationLoop, summarizeLoopValue, type ReviewLoopTrace } from './ReviewIterationLoop'

const trace: ReviewLoopTrace = {
  status: 'completed',
  fromRunId: 'run_v1',
  toRunId: 'run_v2',
  researchSeriesId: 'series_1',
  fromIteration: 1,
  toIteration: 2,
  scientificDecision: 'revise_plan',
  targetModules: ['code', 'experiments'],
  targetSections: ['model.selectedFeatures', 'decisionThreshold'],
  changes: [
    {
      fieldPath: 'model.selectedFeatures',
      before: ['coverage', 'numeric_alignment'],
      after: ['coverage'],
      evidenceIds: ['ev-round-one'],
    },
    {
      fieldPath: 'decisionThreshold',
      before: 0.5,
      after: 0.375,
      evidenceIds: ['ev-candidate-arena'],
    },
  ],
  rounds: [
    { runId: 'run_v1', iterationNumber: 1, value: 0.6991, gateStatus: 'pass' },
    { runId: 'run_v2', iterationNumber: 2, value: 0.7567, delta: 0.0576, improved: true, gateStatus: 'pass' },
  ],
  primaryMetric: 'method:F1-Score',
  finalHoldoutProtected: true,
}

describe('ReviewIterationLoop', () => {
  beforeEach(() => {
    localStorage.setItem(REVIEW_LOCALE_STORAGE_KEY, 'zh-CN')
  })

  it('shows the complete route-back, rerun, and re-audit chain', () => {
    render(
      <MemoryRouter>
        <ReviewIterationLoop
          trace={trace}
          gateStatus="pass"
          findingCount={2}
          metricDeltas={[]}
          iterationHumanReady
          actionLoading=""
          onCreateIteration={vi.fn()}
          onStartIteration={vi.fn()}
          onAuditIteration={vi.fn()}
          onOpenSignoff={vi.fn()}
        />
      </MemoryRouter>,
    )

    expect(screen.getByText('ReviewX 受控闭环')).toBeInTheDocument()
    expect(screen.getByText('定向退回')).toBeInTheDocument()
    expect(screen.getByText('V2 重新执行')).toBeInTheDocument()
    expect(screen.getByText('ReviewX 回审')).toBeInTheDocument()
    expect(screen.getByText('移除 numeric_alignment')).toBeInTheDocument()
    expect(screen.getByText('0.5 → 0.375')).toBeInTheDocument()
    expect(screen.getByText('+5.76 pp')).toBeInTheDocument()
    expect(screen.getByText('留出集后加载')).toBeInTheDocument()
  })

  it('keeps scalar and collection summaries compact', () => {
    expect(summarizeLoopValue(0.375)).toBe('0.375')
    expect(summarizeLoopValue(['a', 'b'])).toBe('2 items')
  })
})
