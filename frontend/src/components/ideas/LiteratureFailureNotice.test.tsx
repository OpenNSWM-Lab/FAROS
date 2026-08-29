import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { REVIEW_LOCALE_STORAGE_KEY } from '@/lib/reviewLocale'
import { LiteratureFailureNotice } from './LiteratureFailureNotice'
import type { LiteratureFailureSummary } from './literatureFailureSummary'

const summary: LiteratureFailureSummary = {
  code: 'seed_too_broad',
  seedQuery: 'AI scientist',
  rawResultCount: 360,
  uniqueResultCount: 182,
  eligiblePaperCount: 0,
  alignedPaperCount: 0,
  rejectedPaperCount: 182,
  minPaperCount: 4,
  minAlignedPaperCount: 3,
  minAlignmentScore: 0.32,
  dominantRejectionReason: 'generic_overlap_only',
  dominantRejectionCount: 182,
  seedAnchors: ['scientist'],
  roleIssues: [],
  repairQueries: ['AI scientist evaluation'],
  actionCodes: ['add_task_method_evaluation', 'create_new_session'],
  queryTemplate: '[specific method] for [specific task]',
  resumeRecommended: false,
}

describe('LiteratureFailureNotice', () => {
  beforeEach(() => {
    window.localStorage.clear()
    window.localStorage.setItem(REVIEW_LOCALE_STORAGE_KEY, 'zh-CN')
  })

  it('shows filtering details and directs broad topics back to editing', () => {
    const onEditSeed = vi.fn()
    const onAskQwen = vi.fn()
    render(
      <LiteratureFailureNotice
        summary={summary}
        isBusy={false}
        isSuggesting={false}
        onEditSeed={onEditSeed}
        onAskQwen={onAskQwen}
        onResume={vi.fn()}
      />,
    )

    expect(screen.getByText('原始结果')).toBeInTheDocument()
    expect(screen.getByText('360')).toBeInTheDocument()
    expect(screen.getByText(/当前主题过短或过宽/)).toBeInTheDocument()
    expect(screen.getByText(/创建新会话，不要继续旧会话/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '检索服务恢复后重试' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '让千问推荐 3 个可用主题' }))
    expect(onAskQwen).toHaveBeenCalledOnce()

    fireEvent.click(screen.getByRole('button', { name: '我自己修改' }))
    expect(onEditSeed).toHaveBeenCalledOnce()
  })
})
