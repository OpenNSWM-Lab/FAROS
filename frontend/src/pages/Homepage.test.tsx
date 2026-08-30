import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'

import { REVIEW_LOCALE_STORAGE_KEY } from '@/lib/reviewLocale'
import { Homepage } from './Homepage'

vi.mock('@/lib/hooks/useApi', () => ({
  useRuns: () => ({ data: [], isLoading: false, isError: false }),
  useCompetitionSnapshot: () => ({ data: undefined, isLoading: false, isError: true }),
  useCompetitionWorkspace: () => ({ data: undefined, isLoading: false, isError: true }),
}))

describe('Homepage', () => {
  beforeEach(() => {
    window.localStorage.clear()
    window.localStorage.setItem(REVIEW_LOCALE_STORAGE_KEY, 'zh-CN')
  })

  it('shows the operational workflow without fabricated example results', () => {
    render(
      <MemoryRouter>
        <Homepage />
      </MemoryRouter>,
    )

    expect(screen.getByRole('heading', { name: 'FAROS' })).toBeInTheDocument()
    expect(screen.getByText('科研主流程')).toBeInTheDocument()
    expect(screen.getByText('暂无运行记录。')).toBeInTheDocument()
    expect(screen.queryByText('Paper Draft')).not.toBeInTheDocument()
    expect(screen.queryByText('Live Metrics')).not.toBeInTheDocument()
    expect(screen.queryByText('$0.42')).not.toBeInTheDocument()
  })
})
