/**
 * The banner must actually reach both boards.
 *
 * `KanbanRouteContent` being correct proves nothing on its own: the SDK kanban
 * plugin's board is a prebuilt bundle we cannot edit, so the ONLY thing that
 * puts a banner on it is the `route.path === KANBAN_ROUTE` branch inside the
 * contributed-routes map in `surfaces.tsx`. Deleting that branch is a silent
 * regression -- no conflict, no build error, no type error -- and until this
 * file rendered the route table, no test failed either.
 *
 * So there are two levels here: the wrapper in isolation, and the wrapper as
 * the route table actually wires it.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import { atom } from 'nanostores'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { registry } from '@/contrib/registry'

import { KANBAN_ROUTE, ROUTES_AREA } from '../routes'

// Same sibling-stubbing idiom as kanban-yield.test.tsx: the surface pulls the
// full chat/shell trees and none of them are under test. The dispatcher banner
// is deliberately NOT mocked here -- it is the subject.
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
vi.mock('@/store/profile', () => ({ $activeGatewayProfile: atom(null) }))
vi.mock('@/store/session', () => ({ $freshDraftReady: atom(false), $gatewayState: atom('closed') }))

// Only the two calls the banner makes; everything else stays real so the
// surface's own imports resolve normally.
vi.mock('@/hermes', async importOriginal => {
  const actual = (await importOriginal()) as Record<string, unknown>

  return {
    ...actual,
    getApiRequestProfile: () => 'default',
    getStatus: () => Promise.resolve({ gateway_running: false })
  }
})

import { ChatRoutesSurface, KanbanRouteContent } from './surfaces'

// The wiring controller's actions bag — every handler is incidental here.
const actionsStub = new Proxy({}, { get: () => () => null }) as never

function withQuery(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>)
}

afterEach(() => {
  cleanup()
})

describe('kanban route content', () => {
  it('puts the dispatcher banner above the board', async () => {
    withQuery(<KanbanRouteContent>{<div data-testid="board" />}</KanbanRouteContent>)

    expect(await screen.findByRole('status')).toBeTruthy()
    expect(screen.getByTestId('board')).toBeTruthy()
  })
})

describe('the route table wires the banner onto both boards', () => {
  function renderAt(path: string) {
    return withQuery(
      <MemoryRouter initialEntries={[path]}>
        <ChatRoutesSurface actions={actionsStub} />
      </MemoryRouter>
    )
  }

  it('shows the banner on the contributed SDK plugin board', async () => {
    // THE load-bearing assertion: this is the only coverage of the
    // `route.path === KANBAN_ROUTE` branch in the contributed-routes map.
    // Delete that branch and this test -- and only this test -- fails.
    const dispose = registry.register({
      id: 'page',
      area: ROUTES_AREA,
      source: 'plugin:kanban',
      data: { path: KANBAN_ROUTE },
      render: () => <div data-testid="plugin-kanban-page" />
    })

    try {
      renderAt(KANBAN_ROUTE)

      const board = await screen.findByTestId('plugin-kanban-page')

      expect(await screen.findByRole('status')).toBeTruthy()
      expect(board).toBeTruthy()
    } finally {
      dispose()
    }
  })

  it('shows the banner on the built-in fallback board', async () => {
    renderAt(KANBAN_ROUTE)

    expect(await screen.findByTestId('builtin-kanban')).toBeTruthy()
    expect(await screen.findByRole('status')).toBeTruthy()
  })

  it('does not show the banner on a non-kanban contributed page', async () => {
    const dispose = registry.register({
      id: 'other-page',
      area: ROUTES_AREA,
      source: 'plugin:other',
      data: { path: '/some-plugin-page' },
      render: () => <div data-testid="other-plugin-page" />
    })

    try {
      renderAt('/some-plugin-page')

      expect(await screen.findByTestId('other-plugin-page')).toBeTruthy()
      expect(screen.queryByRole('status')).toBeNull()
    } finally {
      dispose()
    }
  })
})
