import { useCallback, useEffect, useState } from 'react'

export type ReviewLocale = 'zh-CN' | 'en-US'

export const REVIEW_LOCALE_STORAGE_KEY = 'faros.review.locale'
export const REVIEW_LOCALE_EVENT = 'faros:review-locale-change'

export function normalizeReviewLocale(value?: string | null): ReviewLocale | null {
  if (!value) return null
  const normalized = value.trim().toLowerCase()
  if (normalized === 'zh' || normalized.startsWith('zh-')) return 'zh-CN'
  if (normalized === 'en' || normalized.startsWith('en-')) return 'en-US'
  return null
}

export function resolveReviewLocale(
  stored?: string | null,
  browserLanguages: readonly string[] = [],
): ReviewLocale {
  const storedLocale = normalizeReviewLocale(stored)
  if (storedLocale) return storedLocale
  for (const language of browserLanguages) {
    const locale = normalizeReviewLocale(language)
    if (locale) return locale
  }
  return 'zh-CN'
}

export function reviewText(locale: ReviewLocale, chinese: string, english: string): string {
  return locale === 'zh-CN' ? chinese : english
}

function readBrowserLocale(): ReviewLocale {
  if (typeof window === 'undefined') return 'zh-CN'
  return resolveReviewLocale(
    window.localStorage.getItem(REVIEW_LOCALE_STORAGE_KEY),
    window.navigator.languages || [window.navigator.language],
  )
}

export function useReviewLocale() {
  const [locale, setLocaleState] = useState<ReviewLocale>(readBrowserLocale)

  useEffect(() => {
    document.documentElement.lang = locale
  }, [locale])

  useEffect(() => {
    const handleLocaleChange = (event: Event) => {
      const nextLocale = (event as CustomEvent<ReviewLocale>).detail
      if (nextLocale) setLocaleState(nextLocale)
    }
    const handleStorage = (event: StorageEvent) => {
      if (event.key === REVIEW_LOCALE_STORAGE_KEY) {
        setLocaleState(resolveReviewLocale(event.newValue, window.navigator.languages))
      }
    }
    window.addEventListener(REVIEW_LOCALE_EVENT, handleLocaleChange)
    window.addEventListener('storage', handleStorage)
    return () => {
      window.removeEventListener(REVIEW_LOCALE_EVENT, handleLocaleChange)
      window.removeEventListener('storage', handleStorage)
    }
  }, [])

  const setLocale = useCallback((nextLocale: ReviewLocale) => {
    window.localStorage.setItem(REVIEW_LOCALE_STORAGE_KEY, nextLocale)
    document.documentElement.lang = nextLocale
    setLocaleState(nextLocale)
    window.dispatchEvent(new CustomEvent<ReviewLocale>(REVIEW_LOCALE_EVENT, { detail: nextLocale }))
  }, [])

  const text = useCallback(
    (chinese: string, english: string) => reviewText(locale, chinese, english),
    [locale],
  )

  return {
    locale,
    isChinese: locale === 'zh-CN',
    setLocale,
    text,
  }
}
