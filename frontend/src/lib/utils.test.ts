import { describe, expect, it } from 'vitest'

import { formatBytes } from './utils'

describe('formatBytes', () => {
  it('formats zero and whole-byte values', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(512)).toBe('512.0 B')
  })

  it('keeps fractional bytes in the byte unit', () => {
    expect(formatBytes(0.5)).toBe('0.5 B')
  })

  it('handles invalid sizes without rendering undefined units', () => {
    expect(formatBytes(-1)).toBe('0 B')
    expect(formatBytes(Number.NaN)).toBe('0 B')
    expect(formatBytes(Number.POSITIVE_INFINITY)).toBe('0 B')
  })
})
