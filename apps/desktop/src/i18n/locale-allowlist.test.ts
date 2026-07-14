import { describe, expect, it } from 'vitest'

import { LOCALE_META } from './languages'
import { filterAllowedLocales, isLocaleAllowed, shouldShowLanguagePicker, visibleLocaleCount } from './locale-allowlist'
import type { Locale } from './types'

const allEntries = Object.entries(LOCALE_META) as Array<[Locale, (typeof LOCALE_META)[Locale]]>

describe('locale allowlist (English-only)', () => {
  it('allows English and rejects the non-allowlisted locales', () => {
    expect(isLocaleAllowed('en')).toBe(true)
    expect(isLocaleAllowed('zh')).toBe(false)
    expect(isLocaleAllowed('zh-hant')).toBe(false)
    expect(isLocaleAllowed('ja')).toBe(false)
  })

  it('filters the picker down to English when English is active', () => {
    expect(filterAllowedLocales(allEntries, 'en').map(([id]) => id)).toEqual(['en'])
  })

  it('keeps a non-allowlisted active locale visible so the user can switch away', () => {
    const ids = filterAllowedLocales(allEntries, 'ja').map(([id]) => id)
    expect(ids).toContain('en')
    expect(ids).toContain('ja')
    expect(ids).not.toContain('zh')
  })

  it('hides the language picker when English is the only option', () => {
    expect(visibleLocaleCount('en')).toBe(1)
    expect(shouldShowLanguagePicker('en')).toBe(false)
  })

  it('shows the language picker when a second locale is in play (off-list active)', () => {
    expect(visibleLocaleCount('ja')).toBe(2)
    expect(shouldShowLanguagePicker('ja')).toBe(true)
  })
})
