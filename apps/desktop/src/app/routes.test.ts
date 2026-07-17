import { describe, expect, it } from 'vitest'

import { appViewForPath, isOverlayView, KANBAN_ROUTE, WORKFLOWS_ROUTE } from './routes'

describe('operations routes', () => {
  it('keeps Workflow and Kanban as independent durable pages', () => {
    expect(appViewForPath(WORKFLOWS_ROUTE)).toBe('workflows')
    expect(appViewForPath(KANBAN_ROUTE)).toBe('kanban')
    expect(isOverlayView('workflows')).toBe(false)
    expect(isOverlayView('kanban')).toBe(false)
  })
})
