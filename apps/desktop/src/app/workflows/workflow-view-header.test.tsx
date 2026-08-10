// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { createRef } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'

import { WorkflowViewHeader } from './workflow-view-header'

afterEach(cleanup)

describe('WorkflowViewHeader', () => {
  it('keeps catalog navigation without run controls', () => {
    render(
      <I18nProvider configClient={null} initialLocale="en">
        <WorkflowViewHeader
          headingRef={createRef<HTMLHeadingElement>()}
          loadedRunCount={0}
          onViewChange={vi.fn()}
          view="workflows"
        />
      </I18nProvider>
    )

    expect(screen.getAllByRole('tab').map(tab => tab.textContent)).toEqual([
      'Workflows',
      'Active board',
      'History',
      'Archive'
    ])
    expect(screen.queryByRole('textbox')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Run filters coming soon' })).toBeNull()
  })

  it('shows an honest disabled run toolbar and dispatches view changes', () => {
    const onViewChange = vi.fn()

    render(
      <I18nProvider configClient={null} initialLocale="en">
        <WorkflowViewHeader
          headingRef={createRef<HTMLHeadingElement>()}
          loadedRunCount={3}
          onViewChange={onViewChange}
          view="board"
        />
      </I18nProvider>
    )

    expect(screen.getByLabelText('3 loaded workflow runs')).toBeTruthy()
    expect((screen.getByRole('button', { name: 'Run filters coming soon' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('textbox', { name: 'Search runs — coming soon' }) as HTMLInputElement).disabled).toBe(true)
    fireEvent.click(screen.getByRole('tab', { name: 'History' }))
    expect(onViewChange).toHaveBeenCalledWith('history')
  })

  it('uses logical inline spacing for the Arabic run toolbar', () => {
    const { container } = render(
      <I18nProvider configClient={null} initialLocale="ar">
        <WorkflowViewHeader
          headingRef={createRef<HTMLHeadingElement>()}
          loadedRunCount={3}
          onViewChange={vi.fn()}
          view="board"
        />
      </I18nProvider>
    )

    const toolbar = container.querySelector('[data-workflow-run-toolbar]')
    expect(toolbar?.className).toContain('ms-auto')
    expect(toolbar?.className).not.toContain('ml-auto')
  })
})
