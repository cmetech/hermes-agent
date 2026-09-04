// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { WorkflowMarketplaceApiError } from '@/api/workflow-marketplace'
import type { WorkflowMarketplaceOperation } from '@/types/hermes'

import { marketplaceKeys } from './query-keys'
import { useMarketplaceOperation } from './use-marketplace-operation'

const api = vi.hoisted(() => ({ cancel: vi.fn(), get: vi.fn(), list: vi.fn() }))

vi.mock('@/hermes', () => ({
  cancelWorkflowMarketplaceOperation: (...args: unknown[]) => api.cancel(...args),
  getWorkflowMarketplaceOperation: (...args: unknown[]) => api.get(...args),
  listWorkflowMarketplaceOperations: (...args: unknown[]) => api.list(...args)
}))

const scopeA = { connectionId: 'remote-a', profile: 'support' }
const scopeB = { connectionId: 'remote-b', profile: 'support' }
const NOW = '2026-09-04T00:00:00Z'
const ID = `wmop_${'a'.repeat(12)}_${'b'.repeat(32)}`

function operation(
  state: 'cancelled' | 'failed' | 'pending' | 'running' | 'succeeded',
  overrides: Partial<WorkflowMarketplaceOperation> = {}
): WorkflowMarketplaceOperation {
  const common = {
    created_at: NOW,
    id: ID,
    kind: 'refresh' as const,
    profile: 'support',
    schema_version: 1 as const,
    source_name: 'company',
    updated_at: NOW
  }

  if (state === 'pending') {
    return {
      ...common,
      error: null,
      finished_at: null,
      phase: 'queued',
      progress: 0,
      result: null,
      started_at: null,
      state,
      ...overrides
    } as WorkflowMarketplaceOperation
  }

  if (state === 'running') {
    return {
      ...common,
      error: null,
      finished_at: null,
      phase: 'fetching',
      progress: 40,
      result: null,
      started_at: NOW,
      state,
      ...overrides
    } as WorkflowMarketplaceOperation
  }

  if (state === 'succeeded') {
    return {
      ...common,
      error: null,
      finished_at: NOW,
      phase: 'completed',
      progress: 100,
      result: {
        type: 'source_refresh',
        value: {
          diagnostic_code: null,
          message: null,
          package_count: 1,
          repository_url: 'https://example.test/company/workflows.git',
          resolved_commit: 'c'.repeat(40),
          source_name: 'company',
          state: 'fresh',
          verified_at: NOW
        }
      },
      started_at: NOW,
      state,
      ...overrides
    } as WorkflowMarketplaceOperation
  }

  if (state === 'failed') {
    return {
      ...common,
      error: { code: 'source_unavailable', message: 'Workflow marketplace operation failed.' },
      finished_at: NOW,
      phase: 'failed',
      progress: 40,
      result: null,
      started_at: NOW,
      state,
      ...overrides
    } as WorkflowMarketplaceOperation
  }

  return {
    ...common,
    error: null,
    finished_at: NOW,
    phase: 'cancelled',
    progress: 40,
    result: null,
    started_at: NOW,
    state,
    ...overrides
  } as WorkflowMarketplaceOperation
}

function wrapper(client: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
}

function deferred<T>() {
  let resolve!: (value: T) => void

  const promise = new Promise<T>(done => {
    resolve = done
  })

  return { promise, resolve }
}

describe('useMarketplaceOperation', () => {
  let client: QueryClient

  beforeEach(() => {
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    api.list.mockReset().mockResolvedValue({ limit: 100, offset: 0, operations: [], profile: 'support' })
    api.get.mockReset()
    api.cancel.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
    client.clear()
  })

  it('reconciles every same-scope refresh operation by its backend source identity', async () => {
    const company = operation('running')
    const team = operation('failed', { id: `wmop_${'a'.repeat(12)}_${'d'.repeat(32)}`, source_name: 'team' })
    api.list.mockResolvedValue({ limit: 100, offset: 0, operations: [team, company], profile: 'support' })
    api.get.mockResolvedValue(operation('succeeded'))

    const { result } = renderHook(() => useMarketplaceOperation(scopeA), { wrapper: wrapper(client) })

    await waitFor(() => expect(result.current.operations.map(item => item.source_name)).toEqual(['team', 'company']))
    expect(api.list).toHaveBeenCalledWith(scopeA, { limit: 100 })
    expect(result.current.operationForSource('team')?.state).toBe('failed')
  })

  it('accepts an initial terminal result without polling and invalidates only its source scope', async () => {
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    const { result } = renderHook(() => useMarketplaceOperation(scopeA), { wrapper: wrapper(client) })

    await act(async () => {
      await result.current.start('company', () => Promise.resolve(operation('succeeded')))
    })

    expect(api.get).not.toHaveBeenCalled()
    expect(invalidate).toHaveBeenCalledWith({ queryKey: marketplaceKeys.sources('remote-a::support') })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: marketplaceKeys.searchRoot('remote-a::support') })
  })

  it('polls pending work to backend terminal truth and guards duplicate starts and cancels synchronously', async () => {
    vi.useFakeTimers()
    api.get.mockResolvedValueOnce(operation('running')).mockResolvedValueOnce(operation('cancelled'))
    api.cancel.mockResolvedValue(operation('running'))
    const request = vi.fn().mockResolvedValue(operation('pending'))
    const { result } = renderHook(() => useMarketplaceOperation(scopeA), { wrapper: wrapper(client) })

    let first!: Promise<WorkflowMarketplaceOperation | null>
    await act(async () => {
      first = result.current.start('company', request)
      const duplicate = result.current.start('company', request)
      expect(await duplicate).toBeNull()
      await vi.advanceTimersByTimeAsync(500)
    })
    expect(result.current.operationForSource('company')?.state).toBe('running')

    await act(async () => {
      const one = result.current.cancel(ID)
      const two = result.current.cancel(ID)
      await Promise.all([one, two])
      await vi.advanceTimersByTimeAsync(500)
    })
    await first

    expect(request).toHaveBeenCalledTimes(1)
    expect(api.cancel).toHaveBeenCalledTimes(1)
    expect(result.current.operationForSource('company')?.state).toBe('cancelled')
  })

  it.each(['failed', 'succeeded'] as const)('stops polling when backend reports %s', async state => {
    vi.useFakeTimers()
    api.get.mockResolvedValue(operation(state))
    const { result } = renderHook(() => useMarketplaceOperation(scopeA), { wrapper: wrapper(client) })

    let completed!: Promise<WorkflowMarketplaceOperation | null>

    await act(async () => {
      completed = result.current.start('company', () => Promise.resolve(operation('pending')))
      await vi.advanceTimersByTimeAsync(500)
    })

    expect((await completed)?.state).toBe(state)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000)
    })
    expect(api.get).toHaveBeenCalledTimes(1)
  })

  it('pauses the foreground cadence while hidden and resumes once visible', async () => {
    vi.useFakeTimers()
    let hidden = true
    vi.spyOn(globalThis.document, 'visibilityState', 'get').mockImplementation(() => (hidden ? 'hidden' : 'visible'))
    api.get.mockResolvedValue(operation('succeeded'))
    const { result } = renderHook(() => useMarketplaceOperation(scopeA), { wrapper: wrapper(client) })

    act(() => {
      void result.current.start('company', () => Promise.resolve(operation('pending')))
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000)
    })
    expect(api.get).not.toHaveBeenCalled()

    hidden = false
    act(() => globalThis.document.dispatchEvent(new Event('visibilitychange')))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500)
    })
    expect(api.get).toHaveBeenCalledTimes(1)
  })

  it('ignores late status from a previous scope and treats eviction as recoverable terminal state', async () => {
    const late = deferred<WorkflowMarketplaceOperation>()
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    api.get.mockReturnValueOnce(late.promise)

    const { result, rerender } = renderHook(({ scope }) => useMarketplaceOperation(scope), {
      initialProps: { scope: scopeA },
      wrapper: wrapper(client)
    })

    act(() => {
      void result.current.start('company', () => Promise.resolve(operation('pending')))
    })
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(1))
    rerender({ scope: scopeB })
    await act(async () => late.resolve(operation('succeeded')))

    expect(invalidate).not.toHaveBeenCalledWith({ queryKey: marketplaceKeys.sources('remote-b::support') })

    api.get.mockRejectedValueOnce(new WorkflowMarketplaceApiError('marketplace_operation_not_found', 404, 'safe'))
    await act(async () => {
      await result.current.start('company', () => Promise.resolve(operation('pending')))
    })
    await waitFor(() => expect(result.current.errors.company).toBe('evicted'))

    api.get.mockResolvedValueOnce(operation('succeeded'))
    await act(async () => {
      await result.current.retry(ID)
    })
    expect(result.current.errors.company).toBeUndefined()
  })

  it('contains admission failures and unmount never implies backend cancellation', async () => {
    const { result, unmount } = renderHook(() => useMarketplaceOperation(scopeA), { wrapper: wrapper(client) })

    await act(async () => {
      expect(await result.current.start('company', () => Promise.reject(new Error('/private/tmp/secret')))).toBeNull()
    })
    expect(result.current.errors.company).toBe('status')
    unmount()
    expect(api.cancel).not.toHaveBeenCalled()
  })

  it('surfaces operation-list reconciliation failure and guards exact Retry', async () => {
    api.list.mockRejectedValueOnce(new Error('network')).mockResolvedValueOnce({
      limit: 100,
      offset: 0,
      operations: [],
      profile: 'support'
    })
    const { result } = renderHook(() => useMarketplaceOperation(scopeA), { wrapper: wrapper(client) })

    await waitFor(() => expect(result.current.errors.__reconcile__).toBe('status'))
    act(() => {
      result.current.reconcile()
      result.current.reconcile()
    })
    await waitFor(() => expect(api.list).toHaveBeenCalledTimes(2))
    expect(result.current.errors.__reconcile__).toBeUndefined()
  })
})
