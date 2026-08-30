// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ComponentProps } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { PaneVisibleContext } from '@/components/pane-shell/pane-visibility'
import { I18nProvider } from '@/i18n'
import { ESCAPE_PRIORITY, pushEscapeLayer } from '@/lib/escape-layers'
import type { WorkflowRunSnapshot } from '@/types/hermes'

import { WorkflowRunDrawer } from './workflow-run-drawer'

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

function renderDrawer(props: Partial<ComponentProps<typeof WorkflowRunDrawer>> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  return render(
    <QueryClientProvider client={client}>
      <I18nProvider configClient={null} initialLocale="en">
        <WorkflowRunDrawer {...base} {...props} />
      </I18nProvider>
    </QueryClientProvider>
  )
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('WorkflowRunDrawer', () => {
  it('renders distinct run-details and run-inspector complementary regions', () => {
    renderDrawer()

    const drawer = screen.getByRole('complementary', { name: 'Laptop diagnostic run details' })
    const classTokens = (element: Element) => element.className.split(/\s+/)
    const frame = drawer.querySelector('[data-workflow-run-drawer-frame]')!

    expect(drawer.className).toContain('absolute')
    expect(drawer.className).toContain('right-0')
    expect(classTokens(drawer)).toContain('w-full')
    expect(classTokens(drawer)).toContain('sm:w-[min(45rem,calc(100%-2rem))]')
    expect(classTokens(drawer)).not.toContain('sm:w-[min(40rem,calc(100%-2rem))]')
    expect(classTokens(drawer)).not.toContain('border-l')
    expect(classTokens(frame)).toContain('border-l')
    expect(classTokens(frame)).toContain('h-full')
    expect(screen.getByRole('complementary', { name: 'Laptop diagnostic run inspector' })).toBeTruthy()
    expect(screen.getAllByRole('complementary')).toHaveLength(2)
  })

  it('renders bounded loading and error states', () => {
    const view = renderDrawer({ loading: true, run: null })

    expect(screen.getByLabelText('Loading run details')).toBeTruthy()
    view.unmount()
    renderDrawer({ error: new Error('detail failed'), run: null })
    expect(screen.getByText('Could not load run details')).toBeTruthy()
  })

  it('closes once from the close button or an unhandled Escape', () => {
    const onClose = vi.fn()

    const view = renderDrawer({ onClose })

    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(onClose).toHaveBeenCalledTimes(1)
    view.unmount()

    renderDrawer({ onClose })
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(2)
  })

  it('yields Escape to a higher application layer', () => {
    const onClose = vi.fn()
    const releaseOverlay = pushEscapeLayer(ESCAPE_PRIORITY.overlay)

    try {
      renderDrawer({ onClose })
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
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <I18nProvider configClient={null} initialLocale="en">
            <WorkflowRunDrawer {...base} onClose={onClose} />
          </I18nProvider>
        </QueryClientProvider>
      </PaneVisibleContext.Provider>
    )
    fireEvent.keyDown(window, { key: 'Escape' })

    expect(onClose).not.toHaveBeenCalled()
  })

  it('renders a bounded parent loop summary without private execution values', () => {
    renderDrawer({
      run: {
        ...run,
        current_nodes: ['ordinary', 'group'],
        nodes: {
          ordinary: {
            attempt_count: 1,
            attempts: [],
            depends_on: [],
            id: 'ordinary',
            state: 'running'
          },
          group: {
            attempt_count: 0,
            attempts: [],
            depends_on: [],
            id: 'group',
            loop_group: {
              body: [
                { attempt_count: 1, duration_ms: 125, id: 'fetch', node_type: 'bash', state: 'succeeded' },
                { attempt_count: 2, failure_code: 'provider_failed', id: 'publish', node_type: 'tool', state: 'failed' }
              ],
              completed_iterations: 6,
              iteration: 7,
              iterations: [],
              max_iterations: 25,
              primary_sink: 'publish'
            },
            state: 'running'
          }
        }
      }
    })

    const table = screen.getByRole('table', { name: 'Current node' })

    expect(table).toBeTruthy()
    expect(screen.getByRole('columnheader', { name: 'Output type' }).getAttribute('scope')).toBe('col')
    expect(screen.getByRole('columnheader', { name: 'Completion estimate' }).getAttribute('scope')).toBe('col')
    expect(screen.getByText('fetch')).toBeTruthy()
    expect(screen.getByText('provider_failed')).toBeTruthy()
    expect(globalThis.document.body.textContent).not.toContain('prompt')
    expect(globalThis.document.body.textContent).not.toContain('command')
    expect(globalThis.document.body.textContent).not.toContain('output')
  })
})
