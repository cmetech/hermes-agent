import { useStore } from '@nanostores/react'
import { useEffect, useRef } from 'react'

import { $gatewayState } from '@/store/session'

import { prefetchDesktopRoute } from '../lazy-pages'
import { SETTINGS_ROUTE, SKILLS_ROUTE } from '../routes'

export interface RoutePrefetchScheduler {
  cancelIdle?(handle: number): void
  clearTimer(handle: number): void
  requestIdle?(callback: () => void): number
  setTimer(callback: () => void): number
}

interface IdleRoutePrefetchOptions {
  prefetch?: typeof prefetchDesktopRoute
  scheduler?: RoutePrefetchScheduler
}

export function createRouteIntentPrefetch(
  enabled: boolean,
  prefetch: typeof prefetchDesktopRoute = prefetchDesktopRoute,
  onIntent?: (route: string) => void
): (route: null | string | undefined) => void {
  const seen = new Set<string>()

  return route => {
    if (!enabled || !route || seen.has(route)) {
      return
    }

    seen.add(route)
    onIntent?.(route)
    void prefetch(route)?.catch(() => undefined)
  }
}

function browserScheduler(): RoutePrefetchScheduler {
  return {
    cancelIdle:
      typeof window.cancelIdleCallback === 'function' ? handle => window.cancelIdleCallback(handle) : undefined,
    clearTimer: handle => window.clearTimeout(handle),
    requestIdle:
      typeof window.requestIdleCallback === 'function'
        ? callback => window.requestIdleCallback(() => callback(), { timeout: 2_000 })
        : undefined,
    setTimer: callback => window.setTimeout(callback, 250)
  }
}

export function useRoutePrefetchEnabled(): boolean {
  return useStore($gatewayState) === 'open'
}

export function useIdleDesktopRoutePrefetch(
  enabled: boolean,
  { prefetch = prefetchDesktopRoute, scheduler }: IdleRoutePrefetchOptions = {}
): void {
  const warmed = useRef(false)
  const schedulerRef = useRef<RoutePrefetchScheduler | null>(null)

  schedulerRef.current ??= scheduler ?? browserScheduler()
  const activeScheduler = schedulerRef.current

  // eslint-disable-next-line no-restricted-syntax -- one-shot idle-work sentinel, not reactive atom state
  useEffect(() => {
    if (!enabled || warmed.current) {
      return
    }

    const warm = () => {
      warmed.current = true

      for (const route of [SETTINGS_ROUTE, SKILLS_ROUTE]) {
        void prefetch(route)?.catch(() => undefined)
      }
    }

    if (activeScheduler.requestIdle) {
      const handle = activeScheduler.requestIdle(warm)

      return () => activeScheduler.cancelIdle?.(handle)
    }

    const handle = activeScheduler.setTimer(warm)

    return () => activeScheduler.clearTimer(handle)
  }, [activeScheduler, enabled, prefetch])
}
