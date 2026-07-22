import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { fieldCopyForSchemaKey } from '@/app/settings/field-copy'

import { TRANSLATIONS } from './catalog'
import { setRuntimeI18nLocale, translateNow } from './runtime'
import { zh } from './zh'

describe('desktop i18n runtime translator', () => {
  beforeEach(() => {
    setRuntimeI18nLocale('en')
  })

  afterEach(() => {
    setRuntimeI18nLocale('en')
  })

  it('translates string paths for the active runtime locale', () => {
    setRuntimeI18nLocale('zh')

    expect(translateNow('boot.ready')).toBe('Hermes 桌面版已就绪')
    expect(translateNow('notifications.voice.noSpeechDetected')).toBe('没有检测到语音')
    expect(translateNow('composer.lookupNoMatches')).toBe('没有匹配项。')
    expect(translateNow('assistant.tool.statusRecovered')).toBe('已恢复')
  })

  it('passes arguments to function translations', () => {
    expect(translateNow('notifications.updateReadyMessage', 2)).toBe('2 new changes available.')
  })

  it('translates migrated overlap keys for newly supported locales', () => {
    setRuntimeI18nLocale('ja')
    expect(translateNow('common.save')).toBe('保存')

    setRuntimeI18nLocale('zh-hant')
    expect(translateNow('cron.promptPlaceholder')).toBe('代理每次執行時應做什麼？')
  })

  it('keeps workflow text bounds and bundled fixture copy localized in all four locales', () => {
    for (const locale of ['en', 'ja', 'zh', 'zh-hant'] as const) {
      const operations = TRANSLATIONS[locale].operations as Record<string, unknown>
      expect(operations.workflowRunInputBytes).toBeTypeOf('function')
      expect(operations.workflowRunBundledFixture).toBeTypeOf('string')
      expect(operations.workflowRunInputTooLarge).toBeTypeOf('function')
    }

    expect(TRANSLATIONS.en.operations.workflowRunInputBytes(3, 5)).toBe('3 / 5 bytes')
    expect(TRANSLATIONS.en.operations.workflowRunBundledFixture).toBe('Bundled fixture')
    expect(TRANSLATIONS.en.operations.workflowRunInputTooLarge('symptom', 5)).toBe('symptom exceeds the 5-byte limit.')

    expect(TRANSLATIONS.ja.operations.workflowRunInputBytes(3, 5)).toBe('3 / 5 バイト')
    expect(TRANSLATIONS.ja.operations.workflowRunBundledFixture).toBe('同梱フィクスチャ')
    expect(TRANSLATIONS.ja.operations.workflowRunInputTooLarge('symptom', 5)).toBe(
      'symptom は 5 バイト以内にしてください。'
    )

    expect(TRANSLATIONS.zh.operations.workflowRunInputBytes(3, 5)).toBe('3 / 5 字节')
    expect(TRANSLATIONS.zh.operations.workflowRunBundledFixture).toBe('内置测试文件')
    expect(TRANSLATIONS.zh.operations.workflowRunInputTooLarge('symptom', 5)).toBe('symptom 不能超过 5 字节。')

    expect(TRANSLATIONS['zh-hant'].operations.workflowRunInputBytes(3, 5)).toBe('3 / 5 位元組')
    expect(TRANSLATIONS['zh-hant'].operations.workflowRunBundledFixture).toBe('內建測試檔案')
    expect(TRANSLATIONS['zh-hant'].operations.workflowRunInputTooLarge('symptom', 5)).toBe(
      'symptom 不能超過 5 位元組。'
    )
  })

  it('keeps the workflow AI information localized in all four locales', () => {
    for (const locale of ['en', 'ja', 'zh', 'zh-hant'] as const) {
      expect(TRANSLATIONS[locale].operations.workflowRequiresAi).toBeTypeOf('string')
    }

    expect(TRANSLATIONS.en.operations.workflowRequiresAi).toBe(
      'Runs AI inference through your configured model provider'
    )
    expect(TRANSLATIONS.ja.operations.workflowRequiresAi).toBe(
      '設定済みのモデルプロバイダーを通じて AI 推論を実行します'
    )
    expect(TRANSLATIONS.zh.operations.workflowRequiresAi).toBe('通过已配置的模型提供商运行 AI 推理')
    expect(TRANSLATIONS['zh-hant'].operations.workflowRequiresAi).toBe('透過已設定的模型供應商執行 AI 推論')
  })

  it('translates settings copy for newly supported locales', () => {
    setRuntimeI18nLocale('ja')
    expect(translateNow('settings.appearance.title')).toBe('外観')
    expect(translateNow('settings.nav.providers')).toBe('プロバイダー')

    setRuntimeI18nLocale('zh-hant')
    expect(translateNow('settings.appearance.title')).toBe('外觀')
    expect(translateNow('settings.nav.providerApiKeys')).toBe('API 金鑰')
  })

  it('keeps translated settings field copy addressable from schema keys', () => {
    const field = ['display', 'show_reasoning'].join('.')

    expect(fieldCopyForSchemaKey(zh.settings.fieldLabels, field)).toBe('推理过程块')
    expect(fieldCopyForSchemaKey(zh.settings.fieldDescriptions, field)).toBe('当后端提供推理内容时予以显示。')
  })

  it('falls back to English when the active locale cannot resolve a key', () => {
    const boot = TRANSLATIONS.ja.boot as { ready?: string }
    const originalReady = boot.ready

    try {
      boot.ready = undefined
      setRuntimeI18nLocale('ja')

      expect(translateNow('boot.ready')).toBe('Hermes Desktop is ready')
    } finally {
      boot.ready = originalReady
    }
  })

  it('returns the key when no locale can resolve a path', () => {
    setRuntimeI18nLocale('zh')

    expect(translateNow('missing.path')).toBe('missing.path')
  })
})
