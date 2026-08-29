import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ThemeProvider } from '@/lib/theme-context'
import { REVIEW_LOCALE_STORAGE_KEY } from '@/lib/reviewLocale'
import { Header } from './Header'

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

describe('Header theme controls', () => {
  beforeEach(() => {
    window.localStorage.clear()
    window.localStorage.setItem(REVIEW_LOCALE_STORAGE_KEY, 'en-US')
    document.documentElement.className = ''
    document.documentElement.style.colorScheme = ''
    installMatchMedia(false)
  })

  afterEach(() => {
    document.documentElement.className = ''
    document.documentElement.style.colorScheme = ''
  })

  it('persists an explicit mode and does not render a placeholder account action', async () => {
    window.localStorage.setItem('theme', 'dark')

    render(
      <ThemeProvider>
        <Header />
      </ThemeProvider>,
    )

    await waitFor(() => expect(document.documentElement).toHaveClass('dark'))
    expect(screen.queryByRole('button', { name: 'User menu' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Switch to light mode' }))

    await waitFor(() => expect(document.documentElement).toHaveClass('light'))
    expect(window.localStorage.getItem('theme')).toBe('light')
    expect(screen.getByRole('button', { name: 'Switch to dark mode' })).toBeInTheDocument()
  })
})
