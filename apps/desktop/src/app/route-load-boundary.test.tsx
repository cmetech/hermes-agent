import { render, screen, waitFor } from '@testing-library/react'
import { lazy } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { RouteLoadBoundary } from './route-load-boundary'

vi.mock('@/i18n', () => ({ useI18n: () => ({ t: { common: { loading: 'Loading Hermes' } } }) }))

function deferred<T>() {
  let resolve!: (value: T) => void

  const promise = new Promise<T>(onResolve => {
    resolve = onResolve
  })

  return { promise, resolve }
}

describe('RouteLoadBoundary', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it.each([
    ['workspace', 'route-load-workspace'],
    ['overlay', 'route-load-overlay'],
    ['tile', 'route-load-tile']
  ] as const)('shows a visible non-focusable %s fallback and reports settlement', async (variant, className) => {
    const module = deferred<{ default: () => React.JSX.Element }>()
    const View = lazy(() => module.promise)
    const onVisible = vi.fn()
    const onSettled = vi.fn()

    render(
      <RouteLoadBoundary
        onSettled={onSettled}
        onVisible={onVisible}
        route="/skills"
        variant={variant}
      >
        <View />
      </RouteLoadBoundary>
    )

    const status = screen.getByRole('status', { name: 'Loading Hermes' })

    expect(status.className).toContain(className)
    expect(globalThis.document.activeElement).not.toBe(status)
    await waitFor(() => expect(onVisible).toHaveBeenCalledWith('/skills'))
    expect(onSettled).not.toHaveBeenCalled()

    module.resolve({ default: () => <div>Capabilities ready</div> })
    expect(await screen.findByText('Capabilities ready')).toBeTruthy()
    await waitFor(() => expect(onSettled).toHaveBeenCalledWith('/skills'))
    expect(screen.queryByRole('status')).toBeNull()
  })
})
