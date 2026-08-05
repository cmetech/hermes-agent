import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { DispatcherBanner } from './dispatcher-banner'

const getStatus = vi.fn()

vi.mock('@/hermes', () => ({
  getApiRequestProfile: () => 'default',
  getStatus: () => getStatus()
}))

// Capture the query options so the poll cadence is assertable. On a cold
// launch the backend autostarts the gateway, so the first response can say
// `false` while the gateway is coming up -- a slow cycle leaves the banner
// crying wolf for its whole duration.
let queryOptions: Record<string, unknown> | undefined

vi.mock('@tanstack/react-query', async importOriginal => {
  const actual = (await importOriginal()) as Record<string, unknown> & {
    useQuery: (options: Record<string, unknown>) => unknown
  }

  return {
    ...actual,
    useQuery: (options: Record<string, unknown>) => {
      queryOptions = options

      return actual.useQuery(options)
    }
  }
})

function renderBanner() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  return render(
    <QueryClientProvider client={client}>
      <DispatcherBanner />
    </QueryClientProvider>
  )
}

describe('DispatcherBanner', () => {
  it('warns when no dispatcher is running', async () => {
    getStatus.mockResolvedValue({ gateway_running: false })
    renderBanner()
    await waitFor(() => expect(screen.getByRole('status')).toBeTruthy())
  })

  it('stays silent when the dispatcher is running', async () => {
    getStatus.mockResolvedValue({ gateway_running: true })
    renderBanner()
    await waitFor(() => expect(getStatus).toHaveBeenCalled())
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('stays silent while status is still unknown', async () => {
    // A banner that flashes on every mount before the first response would
    // train users to ignore it.
    getStatus.mockReturnValue(new Promise(() => {}))
    renderBanner()
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('re-checks on the board cadence and on window focus', async () => {
    // The autostart window: a banner that keeps claiming the board is inert
    // for a full minute after the gateway came up is the same "cries wolf"
    // failure the explicit-false rule exists to prevent.
    getStatus.mockResolvedValue({ gateway_running: false })
    renderBanner()
    await waitFor(() => expect(screen.getByRole('status')).toBeTruthy())

    const interval = queryOptions?.refetchInterval as () => false | number

    expect(interval()).toBe(20_000)
    expect(queryOptions?.refetchOnWindowFocus).toBe(true)
  })
})
