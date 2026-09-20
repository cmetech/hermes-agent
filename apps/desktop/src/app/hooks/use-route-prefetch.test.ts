import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SETTINGS_ROUTE, SKILLS_ROUTE } from '../routes'

import {
  createRouteIntentPrefetch,
  type RoutePrefetchScheduler,
  useIdleDesktopRoutePrefetch
} from './use-route-prefetch'

describe('useIdleDesktopRoutePrefetch', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('does not schedule before open, then idles Settings and Capabilities exactly once', () => {
    let idleCallback: (() => void) | null = null
    const prefetch = vi.fn()

    const scheduler: RoutePrefetchScheduler = {
      cancelIdle: vi.fn(),
      clearTimer: vi.fn(),
      requestIdle: vi.fn(callback => {
        idleCallback = callback

        return 7
      }),
      setTimer: vi.fn()
    }

    const { rerender } = renderHook(
      ({ enabled }) => useIdleDesktopRoutePrefetch(enabled, { prefetch, scheduler }),
      { initialProps: { enabled: false } }
    )

    expect(scheduler.requestIdle).not.toHaveBeenCalled()
    rerender({ enabled: true })
    expect(scheduler.requestIdle).toHaveBeenCalledTimes(1)

    act(() => idleCallback?.())
    expect(prefetch.mock.calls.map(call => call[0])).toEqual([SETTINGS_ROUTE, SKILLS_ROUTE])

    rerender({ enabled: false })
    rerender({ enabled: true })
    expect(scheduler.requestIdle).toHaveBeenCalledTimes(1)
  })

  it('cancels pending idle work during cleanup', () => {
    const scheduler: RoutePrefetchScheduler = {
      cancelIdle: vi.fn(),
      clearTimer: vi.fn(),
      requestIdle: vi.fn(() => 12),
      setTimer: vi.fn()
    }

    const { unmount } = renderHook(() =>
      useIdleDesktopRoutePrefetch(true, { prefetch: vi.fn(), scheduler })
    )

    unmount()
    expect(scheduler.cancelIdle).toHaveBeenCalledWith(12)
  })

  it('uses a cancellable timer when requestIdleCallback is unavailable', () => {
    const scheduler: RoutePrefetchScheduler = {
      cancelIdle: vi.fn(),
      clearTimer: vi.fn(),
      setTimer: vi.fn(() => 23)
    }

    const { unmount } = renderHook(() =>
      useIdleDesktopRoutePrefetch(true, { prefetch: vi.fn(), scheduler })
    )

    expect(scheduler.setTimer).toHaveBeenCalledTimes(1)
    unmount()
    expect(scheduler.clearTimer).toHaveBeenCalledWith(23)
  })

  it('gates repeated pointer/focus intent behind connection readiness', () => {
    const prefetch = vi.fn(() => Promise.resolve())
    const disabled = createRouteIntentPrefetch(false, prefetch)
    const enabled = createRouteIntentPrefetch(true, prefetch)

    disabled(SKILLS_ROUTE)
    enabled(SKILLS_ROUTE)
    enabled(SKILLS_ROUTE)
    enabled(null)

    expect(prefetch).toHaveBeenCalledTimes(1)
    expect(prefetch).toHaveBeenCalledWith(SKILLS_ROUTE)
  })
})
