import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  _resetDesktopRoutePerformanceForTests,
  createDesktopRoutePerformance,
  noteDesktopRouteIntent,
  noteDesktopRouteSettled
} from './desktop-performance'

describe('desktop route performance', () => {
  afterEach(() => {
    _resetDesktopRoutePerformanceForTests()
  })

  it('records normalized intent, visible, and settled timing and logs only slow routes', () => {
    let now = 0
    const log = vi.fn()
    const timelineCalls: string[] = []

    const tracker = createDesktopRoutePerformance({
      now: () => now,
      log,
      timeline: {
        clearMarks: name => timelineCalls.push(`clear:${name}`),
        mark: name => timelineCalls.push(`mark:${name}`),
        measure: (name, start, end) => timelineCalls.push(`measure:${name}:${start}:${end}`)
      }
    })

    tracker.noteIntent('/skills?tab=mcp#server')
    now = 120
    tracker.noteVisible('/skills?tab=toolsets')
    now = 620
    tracker.noteSettled('/skills#ready')

    expect(log).toHaveBeenCalledOnce()
    expect(log).toHaveBeenCalledWith({
      event: 'desktop.route.slow',
      route: '/skills',
      settledMs: 620,
      visibleMs: 120
    })
    expect(timelineCalls.some(call => call.startsWith('measure:hermes.desktop.route.visible:/skills:'))).toBe(true)
    expect(timelineCalls.some(call => call.startsWith('measure:hermes.desktop.route.settled:/skills:'))).toBe(true)
    expect(timelineCalls.join(' ')).not.toMatch(/tab=|server|profile|connectionId|https?:|token/)

    tracker.noteIntent('/settings?tab=appearance')
    now = 800
    tracker.noteSettled('/settings')
    expect(log).toHaveBeenCalledOnce()
  })

  it('does not let a superseded route close the newer measurement', () => {
    let now = 0
    const log = vi.fn()
    const tracker = createDesktopRoutePerformance({ now: () => now, log, timeline: null })

    tracker.noteIntent('/skills')
    now = 50
    tracker.noteIntent('/settings')
    now = 600
    tracker.noteSettled('/skills')
    tracker.noteVisible('/settings')
    now = 700
    tracker.noteSettled('/settings')

    expect(log).toHaveBeenCalledOnce()
    expect(log.mock.calls[0][0]).toMatchObject({ route: '/settings', settledMs: 650 })
  })

  it('exports no-op-safe singleton helpers', () => {
    expect(() => {
      noteDesktopRouteIntent('/skills')
      noteDesktopRouteSettled('/settings')
    }).not.toThrow()
  })
})
