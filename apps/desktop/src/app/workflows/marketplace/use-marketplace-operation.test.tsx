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
    expect(api.list).toHaveBeenCalledWith(scopeA, { limit: 100, offset: 0 })
    expect(result.current.operationForSource('team')?.state).toBe('failed')
  })

  it('selects the newest eligible source operation instead of Map insertion order', async () => {
    const older = operation('failed', {
      created_at: '2026-09-03T00:00:00Z',
      updated_at: '2026-09-03T00:01:00Z'
    })
    const newer = operation('succeeded', {
      id: `wmop_${'a'.repeat(12)}_${'c'.repeat(32)}`,
      created_at: '2026-09-04T00:00:00Z',
      updated_at: '2026-09-04T00:01:00Z'
    })
    api.list.mockResolvedValue({ limit: 100, offset: 0, operations: [older, newer], profile: 'support' })

    const { result } = renderHook(() => useMarketplaceOperation(scopeA), { wrapper: wrapper(client) })

    await waitFor(() => expect(result.current.operations).toHaveLength(2))
    expect(result.current.operationForSource('company')?.id).toBe(newer.id)
  })

  it('pages the bounded registry projection and reconciles an active refresh beyond the first 100 records', async () => {
    const terminal = Array.from({ length: 100 }, (_, index) =>
      operation('succeeded', {
        id: `wmop_${'a'.repeat(12)}_${index.toString(16).padStart(32, '0')}`,
        source_name: `source-${index.toString().padStart(3, '0')}`
      })
    )
    const olderActive = operation('running', {
      id: `wmop_${'a'.repeat(12)}_${'f'.repeat(32)}`,
      source_name: 'older-active'
    })
    api.list.mockImplementation((_scope, options) =>
      Promise.resolve({
        limit: 100,
        offset: options.offset ?? 0,
        operations: options.offset === 100 ? [olderActive] : terminal,
        profile: 'support'
      })
    )
    const { result } = renderHook(() => useMarketplaceOperation(scopeA), { wrapper: wrapper(client) })

    await waitFor(() => expect(result.current.operations).toHaveLength(101))
    expect(api.list).toHaveBeenNthCalledWith(1, scopeA, { limit: 100, offset: 0 })
    expect(api.list).toHaveBeenNthCalledWith(2, scopeA, { limit: 100, offset: 100 })
    expect(api.list).toHaveBeenCalledTimes(2)
    expect(result.current.operationForSource('older-active')?.state).toBe('running')
  })

  it('invalidates stale source truth once when reconciliation discovers terminal operations', async () => {
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    api.list.mockResolvedValue({
      limit: 100,
      offset: 0,
      operations: [operation('failed'), operation('succeeded', { id: `wmop_${'a'.repeat(12)}_${'c'.repeat(32)}` })],
      profile: 'support'
    })
    renderHook(() => useMarketplaceOperation(scopeA), { wrapper: wrapper(client) })

    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith({ queryKey: marketplaceKeys.sources('remote-a::support') })
    )
    expect(
      invalidate.mock.calls.filter(
        ([input]) =>
          JSON.stringify(input?.queryKey).includes('remote-a::support') &&
          JSON.stringify(input?.queryKey).includes('sources')
      )
    ).toHaveLength(1)
    expect(
      invalidate.mock.calls.filter(
        ([input]) =>
          JSON.stringify(input?.queryKey).includes('remote-a::support') &&
          JSON.stringify(input?.queryKey).includes('search')
      )
    ).toHaveLength(1)
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

  it('removes every hidden cadence listener across repeated scope changes and unmount', async () => {
    vi.useFakeTimers()
    vi.spyOn(globalThis.document, 'visibilityState', 'get').mockReturnValue('hidden')
    const add = vi.spyOn(globalThis.document, 'addEventListener')
    const remove = vi.spyOn(globalThis.document, 'removeEventListener')
    const { result, rerender, unmount } = renderHook(({ scope }) => useMarketplaceOperation(scope), {
      initialProps: { scope: scopeA },
      wrapper: wrapper(client)
    })

    await act(async () => {
      void result.current.start('company', () => Promise.resolve(operation('pending')))
      await Promise.resolve()
    })
    const firstRegistration = add.mock.calls.find(([event]) => event === 'visibilitychange')

    rerender({ scope: scopeB })
    await act(async () => {
      void result.current.start('company', () => Promise.resolve(operation('pending')))
      await Promise.resolve()
    })
    rerender({ scope: scopeA })
    unmount()

    const registrations = add.mock.calls.filter(([event]) => event === 'visibilitychange')
    expect(firstRegistration).toBeDefined()
    expect(registrations).toHaveLength(2)
    for (const registration of registrations) {
      expect(remove).toHaveBeenCalledWith('visibilitychange', registration[1])
    }
    await vi.runAllTimersAsync()
    globalThis.document.dispatchEvent(new Event('visibilitychange'))
    expect(api.get).not.toHaveBeenCalled()
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
    expect(result.current.operationForSource('company')).toBeUndefined()

    await act(async () => {
      expect(await result.current.cancel(ID)).toBeNull()
    })
    expect(api.cancel).not.toHaveBeenCalled()

    const replacement = operation('succeeded', { id: `wmop_${'a'.repeat(12)}_${'e'.repeat(32)}` })
    await act(async () => {
      await result.current.start('company', () => Promise.resolve(replacement))
    })
    expect(result.current.errors.company).toBeUndefined()
  })

  it('shares one source identity guard across status Retry, Cancel, and a new start', async () => {
    const pendingStatus = deferred<WorkflowMarketplaceOperation>()
    const current = operation('running')
    api.list.mockResolvedValue({ limit: 100, offset: 0, operations: [current], profile: 'support' })
    api.get.mockReturnValue(pendingStatus.promise)
    const request = vi.fn().mockResolvedValue(operation('pending'))
    const { result } = renderHook(() => useMarketplaceOperation(scopeA), { wrapper: wrapper(client) })

    await waitFor(() => expect(result.current.operationForSource('company')?.id).toBe(ID))

    let retry!: Promise<WorkflowMarketplaceOperation | null>
    await act(async () => {
      retry = result.current.retry(ID)
      expect(await result.current.cancel(ID)).toBeNull()
      expect(await result.current.start('company', request)).toBeNull()
    })
    expect(api.get).toHaveBeenCalledTimes(1)
    expect(api.cancel).not.toHaveBeenCalled()
    expect(request).not.toHaveBeenCalled()

    await act(async () => pendingStatus.resolve(operation('succeeded')))
    await retry
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
