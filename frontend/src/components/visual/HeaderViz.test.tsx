import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'

import { REVIEW_LOCALE_STORAGE_KEY } from '@/lib/reviewLocale'
import { HeaderViz } from './HeaderViz'

describe('HeaderViz', () => {
  beforeEach(() => {
    window.localStorage.clear()
    window.localStorage.setItem(REVIEW_LOCALE_STORAGE_KEY, 'zh-CN')
  })

  it('does not present placeholder metrics as real data', () => {
    render(<HeaderViz variant="metricCapsules" />)

    expect(screen.queryByTestId('header-viz')).not.toBeInTheDocument()
    expect(screen.queryByText('68%')).not.toBeInTheDocument()
  })

  it('renders localized labels when real metrics are supplied', () => {
    render(<HeaderViz variant="metricCapsules" data={[10, 20, 30]} />)

    expect(screen.getByText('活跃')).toBeInTheDocument()
    expect(screen.getByText('10%')).toBeInTheDocument()
  })
})
