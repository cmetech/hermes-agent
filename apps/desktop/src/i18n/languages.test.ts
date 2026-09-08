import { describe, expect, it } from 'vitest'

import { TRANSLATIONS } from './catalog'
import { DEFAULT_LOCALE, isLocale, isSupportedLocaleValue, localeConfigValue, normalizeLocale } from './languages'

describe('desktop i18n languages', () => {
  for (const [locale, translation] of Object.entries(TRANSLATIONS)) {
    it(`renders marketplace copy and preserves interpolation in ${locale}`, () => {
      const nativeScript = {
        ar: /[\u0600-\u06ff]/,
        ja: /[\u3040-\u30ff\u3400-\u9fff]/,
        zh: /[\u3400-\u9fff]/,
        'zh-hant': /[\u3400-\u9fff]/
      }[locale]

      for (const [key, value] of Object.entries(translation.operations)) {
        if (!key.startsWith('workflowMarketplace')) {
          continue
        }
        const args = typeof value === 'function' ? Array.from({ length: value.length }, (_, i) => `sample-${i}`) : []
        const rendered = typeof value === 'function' ? Reflect.apply(value, undefined, args) : value
        expect(rendered, key).toBeTypeOf('string')
        expect(rendered.trim(), key).not.toBe('')

        for (const arg of args) {
          expect(rendered, key).toContain(arg)
        }

        if (nativeScript) {
          expect(rendered, key).toMatch(nativeScript)
        }
      }
    })
  }

  it('normalizes supported locale aliases', () => {
    expect(normalizeLocale('en')).toBe('en')
    expect(normalizeLocale('EN-US')).toBe('en')
    expect(normalizeLocale('zh')).toBe('zh')
    expect(normalizeLocale('zh-CN')).toBe('zh')
    expect(normalizeLocale('zh-Hans')).toBe('zh')
    expect(normalizeLocale(' zh_hans_cn ')).toBe('zh')
    expect(normalizeLocale('zh-Hant')).toBe('zh-hant')
    expect(normalizeLocale('zh-TW')).toBe('zh-hant')
    expect(normalizeLocale('zh_HK')).toBe('zh-hant')
    expect(normalizeLocale('ja')).toBe('ja')
    expect(normalizeLocale('ja-JP')).toBe('ja')
    expect(normalizeLocale('ar')).toBe('ar')
    expect(normalizeLocale('AR-SA')).toBe('ar')
    expect(normalizeLocale(' ar_eg ')).toBe('ar')
  })

  it('falls back to English for empty or unsupported values', () => {
    expect(normalizeLocale(null)).toBe(DEFAULT_LOCALE)
    expect(normalizeLocale('')).toBe(DEFAULT_LOCALE)
    expect(normalizeLocale('de')).toBe(DEFAULT_LOCALE)
  })

  it('distinguishes exact locale ids from supported config aliases', () => {
    expect(isSupportedLocaleValue('zh-CN')).toBe(true)
    expect(isSupportedLocaleValue('zh-TW')).toBe(true)
    expect(isSupportedLocaleValue('ja-JP')).toBe(true)
    expect(isSupportedLocaleValue('de')).toBe(false)
    expect(isLocale('zh-CN')).toBe(false)
    expect(isLocale('zh')).toBe(true)
    expect(isLocale('zh-hant')).toBe(true)
    expect(isLocale('ja')).toBe(true)
    expect(isLocale('ar')).toBe(true)
  })

  it('returns the persisted config value for supported locales', () => {
    expect(localeConfigValue('en')).toBe('en')
    expect(localeConfigValue('zh')).toBe('zh')
    expect(localeConfigValue('zh-hant')).toBe('zh-hant')
    expect(localeConfigValue('ja')).toBe('ja')
    expect(localeConfigValue('ar')).toBe('ar')
  })

  it('describes one-shot workflow scheduling in every locale without claiming Cron creation', () => {
    for (const translation of Object.values(TRANSLATIONS)) {
      const copy = [
        translation.operations.workflowRunLater,
        translation.operations.workflowRunLaterDescription,
        translation.operations.workflowScheduled
      ].join(' ')

      expect(copy).not.toMatch(/cron/i)
      expect(translation.operations.workflowRunLater).toBeTruthy()
      expect(translation.operations.workflowScheduled).toBeTruthy()
    }
  })
})
