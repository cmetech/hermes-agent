import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { DispatcherBanner } from './dispatcher-banner'

const getStatus = vi.fn()

vi.mock('@/hermes', () => ({
  getApiRequestProfile: () => 'default',
  getStatus: () => getStatus()
}))

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
})
