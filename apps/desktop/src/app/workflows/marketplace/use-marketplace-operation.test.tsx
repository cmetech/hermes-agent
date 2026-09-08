// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { profileScopeKey } from '@/api/client'

import { createLifecycleHarness, lifecycleScope } from './lifecycle-test-harness'
import { marketplaceKeys } from './query-keys'
import { useMarketplaceOperation } from './use-marketplace-operation'

const disposals: Array<() => void> = []

function deferred() {
  let resolve!: () => void

  const promise = new Promise<void>(onResolve => {
    resolve = onResolve
  })

  return { promise, resolve }
}

afterEach(() => {
  cleanup()

  for (const dispose of disposals.splice(0)) {
    dispose()
  }
})

describe('V2 supervised source refresh adapter', () => {
  it('admits a strict backend-generated refresh fixture with the exact public source subject', async () => {
    const h = createLifecycleHarness()
    disposals.push(h.dispose)
    const binding = await h.bind()
    h.route('refresh', 'service refresh')

    const view = renderHook(() => useMarketplaceOperation(lifecycleScope, binding, h.supervisor), {
      wrapper: h.Providers
    })

    let operation = null
    await act(async () => {
      operation = await view.result.current.start('company')
    })

    expect(operation).toMatchObject({ kind: 'refresh', state: 'succeeded' })
    expect(h.calls.filter(call => call.type === 'start')).toHaveLength(1)
    expect(h.calls.find(call => call.type === 'start')?.input).toMatchObject({
      kind: 'refresh',
      subject: { source_name: 'company', type: 'source' },
      selection: null,
      body: {}
    })
  })

  it('serializes rapid activation to one exact admission', async () => {
    const h = createLifecycleHarness()
    disposals.push(h.dispose)
    const binding = await h.bind()
    const admission = h.holdAdmission('refresh')
    h.route('refresh', 'service refresh')

    const view = renderHook(() => useMarketplaceOperation(lifecycleScope, binding, h.supervisor), {
      wrapper: h.Providers
    })

    let first!: Promise<unknown>
    let second!: Promise<unknown>
    act(() => {
      first = view.result.current.start('company')
      second = view.result.current.start('company')
    })
    await waitFor(() => expect(h.calls.filter(call => call.type === 'start')).toHaveLength(1))
    admission.resolve()
    await act(async () => Promise.all([first, second]))
    expect(h.calls.filter(call => call.type === 'start')).toHaveLength(1)
  })

  it('detaches a held foreground waiter on navigation without cancelling supervisor work or retaining its key', async () => {
    const h = createLifecycleHarness()
    disposals.push(h.dispose)
    const binding = await h.bind()
    const admission = h.holdAdmission('refresh')
    h.route('refresh', 'service refresh')

    const view = renderHook(() => useMarketplaceOperation(lifecycleScope, binding, h.supervisor), {
      wrapper: h.Providers
    })

    let pending!: Promise<unknown>
    act(() => {
      pending = view.result.current.start('company')
    })
    await waitFor(() => expect(h.calls.filter(call => call.type === 'start')).toHaveLength(1))
    view.unmount()
    admission.resolve()
    await expect(pending).resolves.toBeNull()
    expect(h.calls.filter(call => call.type === 'cancel')).toHaveLength(0)
    expect(h.supervisor.$records.get()).toHaveLength(1)
    expect(h.supervisor.$records.get()[0]).toMatchObject({ kind: 'refresh', status: 'terminal' })
  })

  it('uses exact operation identity for explicit cancellation and waits for terminal truth', async () => {
    const h = createLifecycleHarness()
    disposals.push(h.dispose)
    const binding = await h.bind()
    h.route('refresh', 'service refresh pending')

    const view = renderHook(() => useMarketplaceOperation(lifecycleScope, binding, h.supervisor), {
      wrapper: h.Providers
    })

    act(() => {
      void view.result.current.start('company')
    })
    await waitFor(() => expect(view.result.current.operationForSource('company')?.state).toBe('pending'))
    const operationId = view.result.current.operationForSource('company')!.id

    let terminal = null
    await act(async () => {
      terminal = await view.result.current.cancel(operationId)
    })
    expect(terminal).toMatchObject({ id: operationId, state: 'cancelled' })
    expect(h.calls.filter(call => call.type === 'cancel')).toEqual([{ id: operationId, type: 'cancel' }])
  })

  it.each(
    (['cancel', 'retry'] as const).flatMap(operation =>
      (['unmount', 'connection', 'generation', 'profile', 'principal', 'epoch'] as const).map(transition => ({
        operation,
        transition
      }))
    )
  )(
    'does not create a foreground waiter when $operation settles after $transition cleanup',
    async ({ operation, transition }) => {
      const h = createLifecycleHarness()
      disposals.push(h.dispose)
      const binding = await h.bind()
      h.route('refresh', 'service refresh pending')

      const view = renderHook(
        ({ currentBinding, currentScope }) => useMarketplaceOperation(currentScope, currentBinding, h.supervisor),
        {
          initialProps: { currentBinding: binding, currentScope: lifecycleScope },
          wrapper: h.Providers
        }
      )

      act(() => {
        void view.result.current.start('company')
      })
      await waitFor(() => expect(view.result.current.operationForSource('company')?.state).toBe('pending'))
      const operationId = view.result.current.operationForSource('company')!.id
      const held = deferred()
      const entered = deferred()
      const retained = h.supervisor.$records.get()[0]
      const wait = vi.spyOn(h.supervisor, 'waitForRecord').mockResolvedValue(retained)

      const deferOperation = async () => {
        entered.resolve()
        await held.promise
      }

      const operationCall =
        operation === 'cancel'
          ? vi.spyOn(h.supervisor, 'cancel').mockImplementation(deferOperation)
          : vi.spyOn(h.supervisor, 'retry').mockImplementation(deferOperation)

      let foreground!: Promise<unknown>
      act(() => {
        foreground = view.result.current[operation](operationId)
      })
      await entered.promise
      const waitCalls = wait.mock.calls.length

      if (transition === 'unmount') {
        view.unmount()
      } else {
        const currentBinding = {
          ...binding,
          ...(transition === 'connection' ? { connectionId: 'remote-b' } : {}),
          ...(transition === 'generation' ? { connectionGeneration: binding.connectionGeneration + 1 } : {}),
          ...(transition === 'profile' ? { profile: 'other' } : {}),
          ...(transition === 'principal' ? { principalBinding: 'c'.repeat(64) } : {}),
          ...(transition === 'epoch' ? { registryEpoch: 'd'.repeat(32) } : {})
        }

        const currentScope = {
          connectionId: currentBinding.connectionId!,
          profile: currentBinding.profile
        }

        act(() => view.rerender({ currentBinding, currentScope }))
      }

      await act(async () => held.resolve())

      await expect(foreground).resolves.toBeNull()
      expect(wait).toHaveBeenCalledTimes(waitCalls)
      expect(operationCall).toHaveBeenCalledOnce()
      expect(h.calls.filter(call => call.type === 'cancel')).toHaveLength(0)
    }
  )

  it('invalidates only the exact originating source and search scope on terminal truth', async () => {
    const h = createLifecycleHarness()
    disposals.push(h.dispose)
    const binding = await h.bind()
    h.route('refresh', 'service refresh')
    const origin = profileScopeKey(lifecycleScope)
    const other = 'remote-b::support'
    h.queryClient.setQueryData(marketplaceKeys.sources(origin), { marker: 'origin-source' })
    h.queryClient.setQueryData(marketplaceKeys.searchRoot(origin), { marker: 'origin-search' })
    h.queryClient.setQueryData(marketplaceKeys.sources(other), { marker: 'other-source' })

    const view = renderHook(() => useMarketplaceOperation(lifecycleScope, binding, h.supervisor), {
      wrapper: h.Providers
    })

    await act(async () => {
      await view.result.current.start('company')
    })

    expect(h.queryClient.getQueryState(marketplaceKeys.sources(origin))?.isInvalidated).toBe(true)
    expect(h.queryClient.getQueryState(marketplaceKeys.searchRoot(origin))?.isInvalidated).toBe(true)
    expect(h.queryClient.getQueryState(marketplaceKeys.sources(other))?.isInvalidated).toBe(false)
  })
})
