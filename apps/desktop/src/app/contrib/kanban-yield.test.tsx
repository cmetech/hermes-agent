/**
 * The kanban yield contract, end to end at the route table (the v5.2.1 field
 * bug): when the SDK kanban plugin contributes a `/kanban` page, the built-in
 * operations board must stand down AND the contributed page must actually
 * render at /kanban. The regression mode is silent — `contributedRoutes()`
 * dropping the contribution (reserved-path filter) leaves `kanbanContributed`
 * false with no build or type error, and the old board renders forever.
 */
import { cleanup, render, screen } from '@testing-library/react'
import { atom } from 'nanostores'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { registry } from '@/contrib/registry'

import { KANBAN_ROUTE, ROUTES_AREA } from '../routes'

// The surface's siblings pull the full chat/shell trees — none of them are
// under test. The built-in kanban view is replaced with a marker so "which
// board rendered" is a plain testid assertion.
vi.mock('../chat', () => ({ ChatView: () => <div data-testid="chat-view" /> }))
vi.mock('../chat/sidebar', () => ({ ChatSidebar: () => null }))
vi.mock('../right-sidebar/terminal/chrome', () => ({ TerminalPaneChrome: () => null }))
vi.mock('../shell/hooks/use-status-snapshot', () => ({
  useStatusSnapshot: () => ({ inferenceStatus: null, statusSnapshot: null })
}))
vi.mock('../shell/hooks/use-statusbar-items', () => ({
  useStatusbarItems: () => ({ leftStatusbarItems: [], statusbarItems: [] })
}))
vi.mock('../shell/model-menu-panel', () => ({ ModelMenuPanel: () => null }))
vi.mock('../shell/statusbar-fallback', () => ({
  StatusbarBoundary: ({ children }: { children?: unknown }) => children
}))
vi.mock('../shell/statusbar-controls', () => ({ StatusbarControls: () => null }))
vi.mock('./panes', () => ({
  setStatusbarItemGroup: () => undefined,
  useStatusbarContributions: () => []
}))
vi.mock('../kanban', () => ({ KanbanView: () => <div data-testid="builtin-kanban" /> }))
// The banner needs a QueryClientProvider this route table doesn't set up;
// the yield contract under test is which board renders, not the banner.
vi.mock('../kanban/dispatcher-banner', () => ({ DispatcherBanner: () => null }))
vi.mock('@/store/profile', () => ({ $activeGatewayProfile: atom(null) }))
vi.mock('@/store/session', () => ({ $freshDraftReady: atom(false), $gatewayState: atom('closed') }))

import { ChatRoutesSurface } from './surfaces'

// The wiring controller's actions bag — every handler is incidental here.
const actionsStub = new Proxy(
  {},
  { get: () => () => null }
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
) as any

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ChatRoutesSurface actions={actionsStub} />
    </MemoryRouter>
  )
}

afterEach(() => {
  cleanup()
})

describe('kanban yield contract at /kanban', () => {
  it('renders the contributed plugin board, not the built-in one, when the plugin claims the route', async () => {
    const dispose = registry.register({
      id: 'page',
      area: ROUTES_AREA,
      source: 'plugin:kanban',
      data: { path: KANBAN_ROUTE },
      render: () => <div data-testid="plugin-kanban-page" />
    })

    try {
      renderAt(KANBAN_ROUTE)

      expect(await screen.findByTestId('plugin-kanban-page')).toBeTruthy()
      expect(screen.queryByTestId('builtin-kanban')).toBeNull()
    } finally {
      dispose()
    }
  })

  it('falls back to the built-in operations board when no contribution claims /kanban', async () => {
    renderAt(KANBAN_ROUTE)

    expect(await screen.findByTestId('builtin-kanban')).toBeTruthy()
    expect(screen.queryByTestId('plugin-kanban-page')).toBeNull()
  })

  it('re-yields reactively when the plugin registers after first mount', async () => {
    renderAt(KANBAN_ROUTE)
    expect(await screen.findByTestId('builtin-kanban')).toBeTruthy()

    const dispose = registry.register({
      id: 'page',
      area: ROUTES_AREA,
      source: 'plugin:kanban',
      data: { path: KANBAN_ROUTE },
      render: () => <div data-testid="plugin-kanban-page" />
    })

    try {
      expect(await screen.findByTestId('plugin-kanban-page')).toBeTruthy()
      expect(screen.queryByTestId('builtin-kanban')).toBeNull()
    } finally {
      dispose()
    }
  })
})
