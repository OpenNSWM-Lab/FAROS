import { describe, expect, it } from 'vitest'

import { formatBytes } from './utils'

describe('formatBytes', () => {
  it('formats byte values across supported units', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(512)).toBe('512.0 B')
    expect(formatBytes(1024)).toBe('1.0 KB')
    expect(formatBytes(1024 ** 2)).toBe('1.0 MB')
    expect(formatBytes(1024 ** 5)).toBe('1.0 PB')
    expect(formatBytes(1024 ** 6)).toBe('1.0 EB')
  })

  it('handles invalid or out-of-range values without undefined units', () => {
    expect(formatBytes(-1)).toBe('–')
    expect(formatBytes(Number.NaN)).toBe('–')
    expect(formatBytes(Number.POSITIVE_INFINITY)).toBe('–')
    expect(formatBytes(0.5)).toBe('0.5 B')
    expect(formatBytes(1024 ** 7)).toBe('1024.0 EB')
  })
})
