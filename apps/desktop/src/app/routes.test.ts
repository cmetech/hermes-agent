import { describe, expect, it } from 'vitest'

import { registry } from '@/contrib/registry'

import {
  appViewForPath,
  contributedRoutes,
  isOverlayView,
  KANBAN_ROUTE,
  NEW_CHAT_ROUTE,
  primaryRouteSelectedSessionId,
  ROUTES_AREA,
  sessionRoute,
  SETTINGS_ROUTE,
  WORKFLOWS_ROUTE
} from './routes'

describe('operations routes', () => {
  it('keeps Workflow and Kanban as independent durable pages', () => {
    expect(appViewForPath(WORKFLOWS_ROUTE)).toBe('workflows')
    expect(appViewForPath(KANBAN_ROUTE)).toBe('kanban')
    expect(isOverlayView('workflows')).toBe(false)
    expect(isOverlayView('kanban')).toBe(false)
  })
})

describe('contributedRoutes yield contract', () => {
  // The built-in kanban page yields to a contributed /kanban page (the SDK
  // kanban plugin), so that contribution must SURVIVE the reserved-path
  // filter — /kanban is in APP_ROUTES on this fork, and dropping the
  // contribution here makes the yield gate in contrib/surfaces.tsx
  // unreachable (the v5.2.1 field bug: the old board rendered forever).
  it('lets a contribution claim /kanban while still blocking other reserved paths', () => {
    const dispose = registry.registerMany([
      {
        id: 'kanban-page',
        area: ROUTES_AREA,
        source: 'plugin:kanban',
        data: { path: KANBAN_ROUTE },
        render: () => null
      },
      {
        id: 'rogue-settings',
        area: ROUTES_AREA,
        source: 'plugin:rogue',
        data: { path: SETTINGS_ROUTE },
        render: () => null
      }
    ])

    try {
      const paths = contributedRoutes().map(route => route.path)

      expect(paths).toContain(KANBAN_ROUTE)
      expect(paths).not.toContain(SETTINGS_ROUTE)
    } finally {
      dispose()
    }
  })

  it('classifies a claimed /kanban as a contributed page, never a session', () => {
    const dispose = registry.register({
      id: 'kanban-page',
      area: ROUTES_AREA,
      source: 'plugin:kanban',
      data: { path: KANBAN_ROUTE },
      render: () => null
    })

    try {
      expect(appViewForPath(KANBAN_ROUTE)).toBe('extension')
      expect(primaryRouteSelectedSessionId(KANBAN_ROUTE, 'sess-x')).toBe('sess-x')
    } finally {
      dispose()
    }
  })
})

const SESS_A = 'sess-a'
const SESS_B = 'sess-b'

describe('primaryRouteSelectedSessionId', () => {
  it('prefers the routed session id over a stale/different store selection (#59305)', () => {
    // The route already committed to B while the store selection hasn't
    // caught up yet (still reads A) — the route wins.
    expect(primaryRouteSelectedSessionId(sessionRoute(SESS_B), SESS_A)).toBe(SESS_B)
  })

  it('returns null on the new-chat route even with a leftover selection from the previous chat', () => {
    expect(primaryRouteSelectedSessionId(NEW_CHAT_ROUTE, SESS_A)).toBeNull()
  })

  it('falls back to the store selection on a non-chat route (settings, overlays)', () => {
    expect(primaryRouteSelectedSessionId(SETTINGS_ROUTE, SESS_A)).toBe(SESS_A)
  })

  it('falls back to the store selection when the route matches the same session', () => {
    expect(primaryRouteSelectedSessionId(sessionRoute(SESS_A), SESS_A)).toBe(SESS_A)
  })

  it('returns null on a non-chat route with no store selection', () => {
    expect(primaryRouteSelectedSessionId(SETTINGS_ROUTE, null)).toBeNull()
  })
})
