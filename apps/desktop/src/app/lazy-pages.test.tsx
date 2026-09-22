import { render, screen } from '@testing-library/react'
import { type ComponentType, Suspense } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { createDesktopLazyPageRegistry, DESKTOP_LAZY_ROUTES } from './lazy-pages'
import {
  AGENTS_ROUTE,
  ARTIFACTS_ROUTE,
  COMMAND_CENTER_ROUTE,
  CRON_ROUTE,
  KANBAN_ROUTE,
  MESSAGING_ROUTE,
  PROFILES_ROUTE,
  SETTINGS_ROUTE,
  SKILLS_ROUTE,
  STARMAP_ROUTE,
  WEBHOOKS_ROUTE,
  WORKFLOWS_ROUTE
} from './routes'

function deferred<T>() {
  let resolve!: (value: T) => void

  const promise = new Promise<T>(onResolve => {
    resolve = onResolve
  })

  return { promise, resolve }
}

describe('desktop lazy page registry', () => {
  it('prefetches once and React.lazy reuses the exact pending module promise', async () => {
    const module = deferred<{ default: ComponentType }>()
    const load = vi.fn(() => module.promise)
    const registry = createDesktopLazyPageRegistry([{ load, route: SKILLS_ROUTE }])
    const Skills = registry.component(SKILLS_ROUTE)!

    expect(registry.prefetch('/skills?tab=mcp#installed')).not.toBeNull()
    expect(load).toHaveBeenCalledTimes(1)

    render(
      <Suspense fallback={<div>loading</div>}>
        <Skills />
      </Suspense>
    )

    expect(screen.getByText('loading')).toBeTruthy()
    expect(load).toHaveBeenCalledTimes(1)

    module.resolve({ default: () => <div>capabilities</div> })
    expect(await screen.findByText('capabilities')).toBeTruthy()
    expect(load).toHaveBeenCalledTimes(1)
  })

  it('defines every built-in lazy page from route constants, including Settings', () => {
    expect(new Set(DESKTOP_LAZY_ROUTES)).toEqual(
      new Set([
        AGENTS_ROUTE,
        ARTIFACTS_ROUTE,
        COMMAND_CENTER_ROUTE,
        CRON_ROUTE,
        KANBAN_ROUTE,
        MESSAGING_ROUTE,
        PROFILES_ROUTE,
        SETTINGS_ROUTE,
        SKILLS_ROUTE,
        STARMAP_ROUTE,
        WEBHOOKS_ROUTE,
        WORKFLOWS_ROUTE
      ])
    )
  })

  it('does not guess unknown or contributed routes', () => {
    const registry = createDesktopLazyPageRegistry([])

    expect(registry.prefetch('/plugin-page?tab=one')).toBeNull()
    expect(registry.component('/plugin-page')).toBeNull()
  })
})
