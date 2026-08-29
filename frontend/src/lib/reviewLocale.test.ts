import { describe, expect, it } from 'vitest'

import { normalizeReviewLocale, resolveReviewLocale, reviewText } from './reviewLocale'

describe('reviewLocale', () => {
  it('normalizes supported locale variants', () => {
    expect(normalizeReviewLocale('zh-Hans-CN')).toBe('zh-CN')
    expect(normalizeReviewLocale('en-GB')).toBe('en-US')
    expect(normalizeReviewLocale('fr-FR')).toBeNull()
  })

  it('prefers a persisted locale and otherwise follows the browser', () => {
    expect(resolveReviewLocale('en-US', ['zh-CN'])).toBe('en-US')
    expect(resolveReviewLocale(null, ['ja-JP', 'zh-CN'])).toBe('zh-CN')
    expect(resolveReviewLocale(null, ['fr-FR'])).toBe('zh-CN')
  })

  it('selects bilingual copy without rewriting domain terms', () => {
    expect(reviewText('zh-CN', 'Qwen 复验', 'Qwen rerun')).toBe('Qwen 复验')
    expect(reviewText('en-US', 'SHA-256 证据', 'SHA-256 evidence')).toBe('SHA-256 evidence')
  })
})
