import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'

import { REVIEW_LOCALE_STORAGE_KEY, useReviewLocale } from '@/lib/reviewLocale'
import { LanguageToggle } from './LanguageToggle'

function LocaleProbe() {
  const { text } = useReviewLocale()
  return <span>{text('中文界面', 'English interface')}</span>
}

describe('LanguageToggle', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it('persists the selected language and updates other app surfaces', () => {
    render(
      <>
        <LanguageToggle />
        <LocaleProbe />
      </>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'EN' }))

    expect(screen.getByText('English interface')).toBeInTheDocument()
    expect(window.localStorage.getItem(REVIEW_LOCALE_STORAGE_KEY)).toBe('en-US')
  })
})
