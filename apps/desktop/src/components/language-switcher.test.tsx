import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { HermesConfigRecord } from '@/hermes'
import { type I18nConfigClient, I18nProvider } from '@/i18n'

import { LanguageSwitcher } from './language-switcher'

// cmdk (the searchable list) wires a ResizeObserver and scrolls the active
// item into view — neither exists in jsdom. Stub them, matching the polyfill
// idiom in tool-approval-group.test.tsx.
class TestResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

vi.stubGlobal('ResizeObserver', TestResizeObserver)

Element.prototype.scrollIntoView = function scrollIntoView() {}

describe('LanguageSwitcher', () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  // OTTO: the picker is restricted to the locale allowlist (English-only today,
  // see i18n/locale-allowlist.ts). With English active, opening the picker shows
  // only English — the non-allowlisted Chinese + Japanese locales are hidden.
  it('restricts the picker to the allowlisted locales', async () => {
    const saveConfig = vi.fn().mockResolvedValue({ ok: true })
    const latestConfig: HermesConfigRecord = { display: { language: 'en', skin: 'slate' } }

    const configClient: I18nConfigClient = {
      getConfig: vi.fn().mockResolvedValue(latestConfig),
      saveConfig
    }

    render(
      <I18nProvider configClient={configClient}>
        <LanguageSwitcher />
      </I18nProvider>
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Switch language' }).hasAttribute('disabled')).toBe(false)
    })

    fireEvent.click(screen.getByRole('button', { name: 'Switch language' }))

    // English (allowlisted) renders...
    expect(screen.getByRole('option', { name: /English/i })).toBeTruthy()
    // ...but the non-allowlisted locales do not.
    expect(screen.queryByRole('option', { name: /简体中文/ })).toBeNull()
    expect(screen.queryByRole('option', { name: /繁體中文/ })).toBeNull()
    expect(screen.queryByRole('option', { name: /日本語/i })).toBeNull()
  })
})
