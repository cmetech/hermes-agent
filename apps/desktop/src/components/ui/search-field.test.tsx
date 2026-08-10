// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'

import { SearchField } from './search-field'

afterEach(cleanup)

describe('SearchField', () => {
  it('uses a native-disabled input and suppresses trailing actions', () => {
    render(
      <I18nProvider configClient={null} initialLocale="en">
        <SearchField
          aria-label="Search runs — coming soon"
          disabled
          onChange={vi.fn()}
          placeholder="Search runs — coming soon"
          trailingAction={<button type="button">Trailing action</button>}
          value=""
        />
      </I18nProvider>
    )

    const input = screen.getByRole('textbox', { name: 'Search runs — coming soon' }) as HTMLInputElement

    expect(input.disabled).toBe(true)
    expect(input.closest('[aria-disabled="true"]')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Trailing action' })).toBeNull()
  })

  it('preserves enabled input behavior', () => {
    const onChange = vi.fn()

    render(
      <I18nProvider configClient={null} initialLocale="en">
        <SearchField onChange={onChange} placeholder="Search" value="" />
      </I18nProvider>
    )

    fireEvent.change(screen.getByRole('textbox', { name: 'Search' }), { target: { value: 'run' } })
    expect(onChange).toHaveBeenCalledWith('run')
  })
})
