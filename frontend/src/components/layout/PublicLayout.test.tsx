import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

import { ThemeProvider } from '@/lib/theme-context'
import { REVIEW_LOCALE_STORAGE_KEY } from '@/lib/reviewLocale'
import { PublicLayout } from './PublicLayout'

function installMatchMedia(matches: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: vi.fn().mockImplementation(() => ({
      matches,
      media: '(prefers-color-scheme: dark)',
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  })
}

describe('PublicLayout theme controls', () => {
  beforeEach(() => {
    window.localStorage.clear()
    window.localStorage.setItem(REVIEW_LOCALE_STORAGE_KEY, 'zh-CN')
    window.localStorage.setItem('theme', 'dark')
    document.documentElement.className = ''
    document.documentElement.style.colorScheme = ''
    installMatchMedia(false)
  })

  afterEach(() => {
    document.documentElement.className = ''
    document.documentElement.style.colorScheme = ''
  })

  it('keeps the homepage on the shared theme and exposes the theme toggle', async () => {
    render(
      <ThemeProvider>
        <MemoryRouter initialEntries={['/']}>
          <Routes>
            <Route element={<PublicLayout />}>
              <Route index element={<div>Homepage content</div>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </ThemeProvider>,
    )

    await waitFor(() => expect(document.documentElement).toHaveClass('dark'))
    expect(screen.getByText('Homepage content')).toBeInTheDocument()
    expect(document.querySelector('[data-public-shell]')).not.toHaveClass('theme-light-surface')

    fireEvent.click(screen.getByRole('button', { name: '切换到浅色模式' }))

    await waitFor(() => expect(document.documentElement).toHaveClass('light'))
    expect(window.localStorage.getItem('theme')).toBe('light')
  })
})
