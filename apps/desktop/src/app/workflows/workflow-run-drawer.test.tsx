// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { PaneVisibleContext } from '@/components/pane-shell/pane-visibility'
import { I18nProvider } from '@/i18n'
import { ESCAPE_PRIORITY, pushEscapeLayer } from '@/lib/escape-layers'
import type { WorkflowRunSnapshot } from '@/types/hermes'

import { WorkflowRunDrawer } from './workflow-run-drawer'

vi.mock('./run-inspector', () => ({
  RunInspector: ({ run }: { run: WorkflowRunSnapshot }) => (
    <aside aria-label={`${run.workflow} run inspector`}>Inspector {run.run_id}</aside>
  )
}))

const run: WorkflowRunSnapshot = {
  definition_digest: 'a'.repeat(64),
  health: 'healthy',
  next_actions: [],
  progress: { completed_nodes: 1, kind: 'graph', total_nodes: 2 },
  run_id: 'run-1',
  state_version: 1,
  status: 'running',
  updated_at: '2026-08-09T00:00:00Z',
  workflow: 'Laptop diagnostic'
}

const base = {
  actionsDisabled: false,
  error: null,
  events: [],
  loading: false,
  onAction: vi.fn(),
  onClose: vi.fn(),
  run,
  selectedRunId: 'run-1'
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('WorkflowRunDrawer', () => {
  it('renders distinct run-details and run-inspector complementary regions', () => {
    render(
      <I18nProvider configClient={null} initialLocale="en">
        <WorkflowRunDrawer {...base} />
      </I18nProvider>
    )

    const drawer = screen.getByRole('complementary', { name: 'Laptop diagnostic run details' })
    const classTokens = (element: Element) => element.className.split(/\s+/)
    const frame = drawer.querySelector('[data-workflow-run-drawer-frame]')!

    expect(drawer.className).toContain('absolute')
    expect(drawer.className).toContain('right-0')
    expect(classTokens(drawer)).toContain('w-full')
    expect(classTokens(drawer)).toContain('sm:w-[min(40rem,calc(100%-2rem))]')
    expect(classTokens(drawer)).not.toContain('sm:w-[min(32rem,calc(100%-2rem))]')
    expect(classTokens(drawer)).not.toContain('border-l')
    expect(classTokens(frame)).toContain('border-l')
    expect(classTokens(frame)).toContain('h-full')
    expect(screen.getByRole('complementary', { name: 'Laptop diagnostic run inspector' })).toBeTruthy()
    expect(screen.getAllByRole('complementary')).toHaveLength(2)
  })

  it('renders bounded loading and error states', () => {
    const view = render(
      <I18nProvider configClient={null} initialLocale="en">
        <WorkflowRunDrawer {...base} loading run={null} />
      </I18nProvider>
    )

    expect(screen.getByLabelText('Loading run details')).toBeTruthy()
    view.rerender(
      <I18nProvider configClient={null} initialLocale="en">
        <WorkflowRunDrawer {...base} error={new Error('detail failed')} run={null} />
      </I18nProvider>
    )
    expect(screen.getByText('Could not load run details')).toBeTruthy()
  })

  it('closes once from the close button or an unhandled Escape', () => {
    const onClose = vi.fn()

    const view = render(
      <I18nProvider configClient={null} initialLocale="en">
        <WorkflowRunDrawer {...base} onClose={onClose} />
      </I18nProvider>
    )

    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(onClose).toHaveBeenCalledTimes(1)
    view.unmount()

    render(
      <I18nProvider configClient={null} initialLocale="en">
        <WorkflowRunDrawer {...base} onClose={onClose} />
      </I18nProvider>
    )
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(2)
  })

  it('yields Escape to a higher application layer', () => {
    const onClose = vi.fn()
    const releaseOverlay = pushEscapeLayer(ESCAPE_PRIORITY.overlay)

    try {
      render(
        <I18nProvider configClient={null} initialLocale="en">
          <WorkflowRunDrawer {...base} onClose={onClose} />
        </I18nProvider>
      )
      fireEvent.keyDown(window, { key: 'Escape' })

      expect(onClose).not.toHaveBeenCalled()
    } finally {
      releaseOverlay()
    }
  })

  it('does not own Escape while its kept-alive pane is hidden', () => {
    const onClose = vi.fn()

    render(
      <PaneVisibleContext.Provider value={false}>
        <I18nProvider configClient={null} initialLocale="en">
          <WorkflowRunDrawer {...base} onClose={onClose} />
        </I18nProvider>
      </PaneVisibleContext.Provider>
    )
    fireEvent.keyDown(window, { key: 'Escape' })

    expect(onClose).not.toHaveBeenCalled()
  })
})
