import { QueryClient } from '@tanstack/react-query'
import { cleanup, render } from '@testing-library/react'
import { atom } from 'nanostores'
import { createElement, StrictMode } from 'react'
import { afterEach, beforeEach, describe, expect, expectTypeOf, it, vi } from 'vitest'

import {
  LifecycleApiError,
  type LifecycleConnectionBinding,
  type LifecycleStart
} from '@/api/workflow-marketplace-lifecycle'
import { decodeLifecycleOperation } from '@/lib/workflow-marketplace-lifecycle-codec'
import type { _ConfirmBody, _InstallBody, _TrustBody, LifecycleOperation } from '@/types/workflow-marketplace-lifecycle'

import corpus from '../../../../tests/fixtures/workflow-marketplace-lifecycle-v2.json'

import type { MarketplaceIntent, SupervisionApi } from './workflow-marketplace-supervisor'

const module = await import('./workflow-marketplace-supervisor').catch(() => null)
const scope = { connectionId: 'remote-a', profile: 'support' }
const identity = { source_key: 'company', package_id: 'laptop-support' }
const subject = { type: 'package' as const, identity }

// Compile-time RED: route discrimination must narrow the body without a cast or permissive empty-body escape.
expectTypeOf<Extract<MarketplaceIntent, { kind: 'install_confirm' }>['body']>().toEqualTypeOf<_ConfirmBody>()
expectTypeOf<Extract<MarketplaceIntent, { kind: 'install_prepare' }>['body']>().toEqualTypeOf<_InstallBody>()
expectTypeOf<Extract<MarketplaceIntent, { kind: 'trust_revoke' }>['body']>().toEqualTypeOf<_TrustBody>()
expectTypeOf<Extract<MarketplaceIntent, { kind: 'refresh' }>['body']>().toEqualTypeOf<Record<string, never>>()

function fixture(name: string): LifecycleOperation {
  const value = decodeLifecycleOperation(corpus.operationCases.find(item => item.name === name)?.value)

  if (!value) {
    throw new Error(`Invalid test fixture ${name}`)
  }

  return value
}

function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (error: unknown) => void

  const promise = new Promise<T>((yes, no) => {
    resolve = yes
    reject = no
  })

  return { promise, resolve, reject }
}

function harness() {
  expect(module?.createMarketplaceSupervisor).toBeTypeOf('function')
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
  const visibility = atom(true)

  let generation = 1,
    principal = corpus.capabilities.principal_binding,
    epoch = corpus.capabilities.registry_epoch

  let connected = true,
    workers = 0,
    probes = 0,
    posts = 0,
    reads = 0,
    states = 0

  let fail: LifecycleApiError | null = null

  let lose = false,
    deferPost: ReturnType<typeof deferred<unknown>> | null = null

  let deferGet: ReturnType<typeof deferred<unknown>> | null = null
  let deferCapabilities: ReturnType<typeof deferred<unknown>> | null = null
  let descriptorWait: ReturnType<typeof deferred<{ connectionId: string; connectionGeneration: number }>> | null = null
  const listeners = new Set<(scope: { connectionId: string | null; profile: string } | null, reason: string) => void>()
  const receipts = new Map<string, LifecycleOperation>()

  let terminal = false,
    evicted = false,
    listItems: LifecycleOperation[] | null = null

  const calls: Array<{ type: string; requestId?: string; binding: LifecycleConnectionBinding }> = []

  const key = (binding: LifecycleConnectionBinding, requestId: string) =>
    JSON.stringify([binding.connectionId, binding.profile, binding.principalBinding, binding.registryEpoch, requestId])

  const read = (binding: LifecycleConnectionBinding, requestId: string) => {
    const operation = receipts.get(key(binding, requestId))

    if (!operation) {
      throw new LifecycleApiError('marketplace_admission_not_found', 404)
    }

    if (!terminal) {
      return operation
    }

    const name =
      operation.kind === 'update_check'
        ? 'service update check'
        : operation.kind === 'inspect'
          ? 'service inspect'
          : 'service install confirm'

    const result = decodeLifecycleOperation({ ...fixture(name), id: operation.id, request_id: operation.request_id })

    if (!result) {
      throw new Error('Invalid terminal fixture')
    }

    return result
  }

  const api: SupervisionApi = {
    capabilities: async () => {
      probes++

      return deferCapabilities
        ? deferCapabilities.promise
        : {
            ...corpus.capabilities,
            server_time: new Date()
              .toISOString()
              .replace('.000Z', 'Z')
              .replace(/(\.\d{3})Z$/, '$1000Z'),
            principal_binding: principal,
            registry_epoch: epoch
          }
    },
    start: async (intent: LifecycleStart, binding: LifecycleConnectionBinding) => {
      posts++
      calls.push({ type: 'post', requestId: intent.requestId, binding })
      const id = key(binding, intent.requestId)

      if (!receipts.has(id)) {
        workers++

        const template = fixture(
          intent.kind === 'update_check'
            ? 'service update check pending'
            : intent.kind === 'inspect'
              ? 'service inspect pending'
              : 'service install confirm pending'
        )

        const result = decodeLifecycleOperation({
          ...template,
          request_id: intent.requestId,
          id: `wmop_aaaaaaaaaaaa_${workers.toString(16).padStart(32, '0')}`
        })

        if (!result) {
          throw new Error('Invalid admitted fixture')
        }

        receipts.set(id, result)
      }

      if (deferPost) {
        return deferPost.promise
      }

      if (lose) {
        throw new LifecycleApiError('marketplace_network_error', 0)
      }

      return read(binding, intent.requestId)
    },
    get: async (id: string, binding: LifecycleConnectionBinding) => {
      reads++
      calls.push({ type: 'get', binding })

      if (deferGet) {
        return deferGet.promise
      }

      if (fail) {
        const error = fail
        fail = null
        throw error
      }

      const operation = [...receipts.values()].find(item => item.id === id)

      if (!operation) {
        throw new LifecycleApiError('marketplace_operation_not_found', 404)
      }

      return read(binding, operation.request_id)
    },
    lookup: async (requestId: string, binding: LifecycleConnectionBinding) => {
      calls.push({ type: 'lookup', requestId, binding })

      if (fail) {
        const error = fail
        fail = null
        throw error
      }

      const operation = read(binding, requestId)

      return evicted
        ? {
            state: 'evicted',
            operation_id: operation.id,
            request_id: operation.request_id,
            registry_epoch: operation.registry_epoch,
            profile: operation.profile,
            kind: operation.kind,
            subject: operation.subject,
            selection: operation.selection
          }
        : { state: 'found', operation }
    },
    list: async (binding: LifecycleConnectionBinding) => {
      calls.push({ type: 'list', binding })

      return {
        items:
          listItems ??
          [...receipts.values()]
            .filter(item => receipts.has(key(binding, item.request_id)))
            .map(item => read(binding, item.request_id)),
        complete: true,
        next_cursor: null
      }
    },
    cancel: async (id: string, binding: LifecycleConnectionBinding) => {
      terminal = true

      return api.get(id, binding)
    },
    packageState: async () => {
      states++

      return {
        profile: 'support',
        identity,
        observed_at: corpus.capabilities.server_time,
        state: 'absent',
        installed: null,
        trust: null,
        recovery: 'clear',
        busy: false
      }
    }
  }

  const supervisor = module!.createMarketplaceSupervisor({
    api,
    queryClient,
    visibility,
    clock: () => ({ wallNowMs: Date.now(), monotonicNowMs: performance.now() }),
    connections: {
      resolveConnection: async (input: typeof scope) =>
        descriptorWait
          ? descriptorWait.promise
          : {
              connectionId: input.connectionId,
              connectionGeneration: generation
            },
      isConnected: () => connected,
      subscribe: (
        listener: (scope: { connectionId: string | null; profile: string } | null, reason: string) => void
      ) => {
        listeners.add(listener)

        return () => {
          listeners.delete(listener)
        }
      }
    }
  })

  const intent = {
    kind: 'install_confirm' as const,
    subject,
    selection: null,
    body: {
      confirmation_token: 'fixed-private-review-secret-long-enough',
      subject,
      selection: null,
      review_digest: 'a'.repeat(64),
      prepare_operation_id: fixture('service install prepare').id
    }
  }

  const notify = (reason: string) => {
    for (const listener of listeners) {
      listener(reason === 'marketplace_connection_generation_exhausted' ? null : scope, reason)
    }
  }

  return {
    supervisor,
    queryClient,
    visibility,
    api,
    intent,
    receipts,
    calls,
    bind: () => supervisor.reconcileScope(scope),
    record: () => supervisor.$records.get()[0],
    workers: () => workers,
    probes: () => probes,
    posts: () => posts,
    reads: () => reads,
    states: () => states,
    lose: () => {
      lose = true
    },
    deferPost: () => (deferPost = deferred<unknown>()),
    deferGet: () => (deferGet = deferred<unknown>()),
    deferCapabilities: () => (deferCapabilities = deferred<unknown>()),
    resumeCapabilities: () => {
      deferCapabilities = null
    },
    holdDescriptor: (pending: NonNullable<typeof descriptorWait>) => {
      descriptorWait = pending
    },
    fail: (error: LifecycleApiError) => {
      fail = error
    },
    terminal: () => {
      terminal = true
    },
    evict: () => {
      evicted = true
    },
    list: (items: LifecycleOperation[]) => {
      listItems = items
    },
    changeGeneration: () => {
      generation++
      notify('reconfigured')
    },
    changePrincipal: () => {
      principal = 'c'.repeat(64)
      generation++
      notify('reconfigured')
    },
    changeEpoch: () => {
      epoch = 'd'.repeat(32)
      generation++
      notify('reconfigured')
    },
    disconnect: () => {
      connected = false
      notify('disconnect')
    },
    reconnect: () => {
      connected = true
    },
    notify,
    dispose: () => {
      supervisor.dispose()
      queryClient.clear()
    },
    listeners
  }
}

let cleanups: Array<() => void> = []

function setup() {
  const h = harness()
  cleanups.push(h.dispose)

  return h
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date', 'performance'] })
  vi.setSystemTime(Date.parse(corpus.capabilities.server_time))
})
afterEach(() => {
  cleanup()
  cleanups.forEach(clean => clean())
  cleanups = []
  vi.useRealTimers()
})

describe('application marketplace operation supervision', () => {
  // Break caught: a detached view owns the record, or recovery mints a new request/worker.
  it('retains admission correlation after every observer detaches and recovers the one admitted worker', async () => {
    const h = setup()
    const binding = await h.bind()
    h.lose()
    const off = h.supervisor.$records.subscribe(() => undefined)
    await h.supervisor.start(h.intent, binding!)
    off()
    expect(h.record()).toMatchObject({ status: 'admission_unknown', callPending: false, operationId: null })
    await h.bind()
    expect(h.record()).toMatchObject({ status: 'watching', callPending: false, operationId: expect.any(String) })
    expect(h.workers()).toBe(1)
    expect(h.supervisor.getPackageGate(binding!, identity).state).toBe('busy')
    expect(JSON.stringify(h.supervisor.$records.get())).not.toContain('fixed-private-review-secret')
  })

  // Break caught: Retry after lost response re-admits under a fresh request or retains secret beyond admission lifetime.
  it('replays exactly the same bounded request and clears replay capability after fifteen seconds', async () => {
    const h = setup()
    const binding = await h.bind()
    h.lose()
    await h.supervisor.start(h.intent, binding!)
    await h.supervisor.retry(h.record().key)
    expect(h.posts()).toBe(2)
    expect(h.workers()).toBe(1)
    expect(h.calls.filter(call => call.type === 'post').map(call => call.requestId)).toEqual([
      h.record().requestId,
      h.record().requestId
    ])
    await vi.advanceTimersByTimeAsync(15000)
    await h.supervisor.retry(h.record().key)
    expect(h.posts()).toBe(2)
    expect(h.record().operationId).not.toBeNull()
  })

  // Break caught: same-tick activation launches two preparations/commits despite an existing intent guard/barrier.
  it('reserves before awaiting and blocks duplicate activation without stranding the call guard', async () => {
    const h = setup()
    const binding = await h.bind()
    const post = h.deferPost()
    const first = h.supervisor.start(h.intent, binding!)
    await expect(h.supervisor.start(h.intent, binding!)).rejects.toThrow()
    expect(h.workers()).toBe(1)
    post.reject(new Error('private-secret-transport-detail'))
    await first
    expect(h.record().callPending).toBe(false)
    expect(JSON.stringify(h.supervisor.$records.get())).not.toContain('private-secret')
  })

  // Break caught: native generation rebinding trusts list absence instead of exact owned admission evidence.
  it('rebinds same authority only after exact request lookup under the new native generation', async () => {
    const h = setup()
    const binding = await h.bind()
    await h.supervisor.start(h.intent, binding!)
    const request = h.record().requestId
    h.changeGeneration()
    h.list([])
    expect(h.record().status).toBe('suspended')
    const next = await h.bind()
    expect(
      h.calls.some(
        call => call.type === 'lookup' && call.requestId === request && call.binding.connectionGeneration === 2
      )
    ).toBe(true)
    expect(h.record().binding).toEqual(next)
    expect(h.workers()).toBe(1)
  })

  // Break caught: changed backend authority adopts old history or late old settlement erases newly scoped data.
  it.each(['changePrincipal', 'changeEpoch'] as const)(
    '%s never adopts an old record or purges new data on late settlement',
    async change => {
      const h = setup()
      const binding = await h.bind()
      const post = h.deferPost()
      const pending = h.supervisor.start(h.intent, binding!)
      const old = [...h.receipts.values()][0]
      h[change]()
      const fresh = await h.bind()
      const root = ['workflow-marketplace', 'remote-a::support', 'installed']
      h.queryClient.setQueryData(root, ['new authority'])
      post.resolve(old)
      await pending
      expect(h.record().binding).toEqual(binding)
      expect(h.record().status).toBe('suspended')
      expect(h.queryClient.getQueryData(root)).toEqual(['new authority'])
      expect(h.supervisor.getPackageGate(fresh!, identity).state).toBe('unknown')
    }
  )

  // Break caught: transient errors are interpreted as terminal loss before authenticated capability proof.
  it.each([
    [401, 'marketplace_request_failed'],
    [403, 'marketplace_request_failed'],
    [409, 'marketplace_principal_changed'],
    [404, 'marketplace_operation_not_found'],
    [404, 'marketplace_admission_not_found'],
    [0, 'marketplace_connection_generation_changed']
  ] as const)('reprobes for %s/%s with no outcome claim', async (status, code) => {
    const h = setup()
    const binding = await h.bind()
    await h.supervisor.start(h.intent, binding!)
    h.fail(new LifecycleApiError(code, status))
    const before = h.probes()
    const cap = h.deferCapabilities()
    const retry = h.supervisor.retry(h.record().key)
    await vi.advanceTimersByTimeAsync(0)
    expect(h.probes()).toBe(before + 1)
    expect(h.supervisor.bindings.presentation(scope, 'old')).toBeUndefined()
    expect(h.record().operation?.outcome ?? null).toBeNull()
    cap.resolve(corpus.capabilities)
    await retry
    expect(h.record().callPending).toBe(false)
  })

  // Break caught: exhaustion runs a capabilities loop, drops unresolved barriers, or waits forever for IPC guards.
  it('exhaustion globally quarantines and releases guards without inventing an outcome or retrying', async () => {
    const h = setup()
    const binding = await h.bind()
    const post = h.deferPost()
    const pending = h.supervisor.start(h.intent, binding!)
    h.notify('marketplace_connection_generation_exhausted')
    await pending
    const before = h.probes()
    expect(h.record()).toMatchObject({ status: 'suspended', callPending: false, operation: null })
    expect(h.supervisor.$status.get()).toBe('restart_required')
    expect(h.supervisor.getPackageGate(binding!, identity).state).toBe('unknown')
    await h.bind()
    await h.supervisor.retry(h.record().key)
    expect(h.probes()).toBe(before)
    post.resolve([...h.receipts.values()][0])
  })

  // Break caught: hidden/disconnected observers poll, reconnect skips capability proof, or settlement leaves cadence alive.
  it('pauses hidden/disconnected polling and disposes all timers and subscriptions', async () => {
    const h = setup()
    const binding = await h.bind()
    await h.supervisor.start(h.intent, binding!)
    await vi.advanceTimersByTimeAsync(499)
    expect(h.reads()).toBe(0)
    await vi.advanceTimersByTimeAsync(1)
    expect(h.reads()).toBe(1)
    h.visibility.set(false)
    await vi.advanceTimersByTimeAsync(2000)
    expect(h.reads()).toBe(1)
    h.disconnect()
    h.visibility.set(true)
    await vi.advanceTimersByTimeAsync(2000)
    expect(h.reads()).toBe(1)
    h.reconnect()
    h.terminal()
    await h.bind()
    expect(h.record()).toMatchObject({ status: 'terminal', callPending: false })
    expect(h.supervisor.getPackageGate(binding!, identity).state).toBe('reconciling')
    h.dispose()
    expect(vi.getTimerCount()).toBe(0)
    expect(h.listeners.size).toBe(0)
  })

  it('detaches an aborted exact record waiter without cancelling or disposing supervised work', async () => {
    const h = setup()
    const binding = await h.bind()
    await h.supervisor.start(h.intent, binding!)
    const controller = new AbortController()
    const add = vi.spyOn(controller.signal, 'addEventListener')
    const remove = vi.spyOn(controller.signal, 'removeEventListener')
    const waiting = h.supervisor.waitForRecord(h.record().key, controller.signal)

    controller.abort()
    await expect(waiting).rejects.toMatchObject({ name: 'AbortError' })
    expect(remove).toHaveBeenCalledWith(add.mock.calls[0][0], add.mock.calls[0][1])
    expect(h.calls.some(call => call.type === 'cancel')).toBe(false)

    h.terminal()
    await vi.advanceTimersByTimeAsync(500)
    expect(h.record()).toMatchObject({ status: 'terminal', callPending: false })
    controller.abort()
    expect(h.calls.some(call => call.type === 'cancel')).toBe(false)
  })

  it.each(['terminal', 'suspended', 'disposed'] as const)(
    'settles an exact record waiter and removes its abort listener on %s',
    async finish => {
      const h = setup()
      const binding = await h.bind()
      await h.supervisor.start(h.intent, binding!)
      const controller = new AbortController()
      const add = vi.spyOn(controller.signal, 'addEventListener')
      const remove = vi.spyOn(controller.signal, 'removeEventListener')
      const waiting = h.supervisor.waitForRecord(h.record().key, controller.signal)

      if (finish === 'terminal') {
        h.terminal()
        await vi.advanceTimersByTimeAsync(500)
      } else if (finish === 'suspended') {
        h.disconnect()
      } else {
        h.dispose()
      }

      await expect(waiting).resolves.toMatchObject({ status: finish === 'disposed' ? 'suspended' : finish })
      expect(remove).toHaveBeenCalledWith(add.mock.calls[0][0], add.mock.calls[0][1])

      if (finish === 'disposed') {
        expect(vi.getTimerCount()).toBe(0)
        expect(h.listeners.size).toBe(0)
      }
    }
  )

  // Break caught: status error strands update-check guard or automatic polling continues after a recoverable failure.
  it('releases an update-check guard on status loss and allows explicit status retry', async () => {
    const h = setup()
    const binding = await h.bind()
    await h.supervisor.start({ kind: 'update_check', subject, selection: null, body: { identity } }, binding!)
    h.fail(new LifecycleApiError('marketplace_network_error', 0))
    await vi.advanceTimersByTimeAsync(500)
    expect(h.record()).toMatchObject({ status: 'status_unknown', callPending: false })
    const reads = h.reads()
    await vi.advanceTimersByTimeAsync(1500)
    expect(h.reads()).toBe(reads)
    h.terminal()
    await h.supervisor.retry(h.record().key)
    expect(h.record()).toMatchObject({ status: 'terminal', callPending: false })
  })

  // Break caught: cancellation request overrides a commit that already won backend serialization.
  it('accepts a correlated commit that wins cancellation without converting it to cancelled', async () => {
    const h = setup()
    const binding = await h.bind()
    await h.supervisor.start(h.intent, binding!)
    await h.supervisor.cancel(h.record().key)
    expect(h.record().operation?.state).toBe('succeeded')
    expect(h.record().operation?.outcome?.type).toBe('committed')
    expect(h.record().callPending).toBe(false)
  })

  // Break caught: a stale snapshot regresses the fresher exact lookup that completed during the scan.
  it('keeps an exact terminal lookup when an earlier snapshot still reports pending', async () => {
    const h = setup()
    const binding = await h.bind()
    await h.supervisor.start(h.intent, binding!)
    h.list([...h.receipts.values()])
    h.terminal()
    await h.bind()
    expect(h.record()).toMatchObject({ status: 'terminal', operation: { state: 'succeeded' } })
    expect(h.supervisor.bindings.isCurrent(binding!)).toBe(true)
  })

  it.each(['id', 'request', 'kind', 'subject', 'profile', 'epoch'] as const)(
    'does not overwrite a known admission from a list record with the wrong %s correlation',
    async change => {
      const h = setup()
      const binding = await h.bind()
      await h.supervisor.start(h.intent, binding!)
      const original = h.record().operation!
      const otherEpoch = 'd'.repeat(32)

      const value = {
        ...(change === 'kind' ? fixture('service inspect pending') : original),
        id: change === 'id' ? corpus.validOperationB.id : original.id,
        request_id:
          change === 'request'
            ? corpus.validOperationB.request_id
            : change === 'epoch'
              ? original.request_id.replace(original.registry_epoch, otherEpoch)
              : original.request_id,
        ...(change === 'subject'
          ? { subject: { type: 'package', identity: { ...identity, package_id: 'other-package' } } }
          : {}),
        ...(change === 'profile' ? { profile: 'other' } : {}),
        ...(change === 'epoch' ? { registry_epoch: otherEpoch } : {})
      }

      const changed = decodeLifecycleOperation(value)

      expect(changed).not.toBeNull()
      h.list([changed!])

      expect(await h.bind()).toBeNull()
      expect(h.record().operation).toEqual(original)
    }
  )

  it.each(['watching', 'status_unknown', 'terminal', 'evicted'] as const)(
    'does not overwrite exact %s truth from a later list snapshot',
    async state => {
      const h = setup()
      const binding = await h.bind()
      await h.supervisor.start(h.intent, binding!)
      const pending = h.record().operation!

      const terminal = decodeLifecycleOperation({
        ...fixture('service install confirm'),
        id: pending.id,
        request_id: pending.request_id
      })!

      if (state === 'terminal') {
        h.terminal()
        await h.supervisor.retry(h.record().key)
        h.list([pending])
      } else {
        h.list([terminal])

        if (state === 'evicted') {
          h.evict()
        } else if (state === 'status_unknown') {
          h.fail(new LifecycleApiError('marketplace_network_error', 0))
        }
      }

      expect(await h.bind()).toEqual(binding)
      expect(h.record()).toMatchObject(
        state === 'terminal'
          ? { status: 'terminal', operation: terminal }
          : state === 'evicted'
            ? { status: 'evicted', operation: pending, operationId: pending.id }
            : state === 'status_unknown'
              ? { status: 'status_unknown', operation: pending }
              : { status: 'watching', operation: pending }
      )
    }
  )

  it.each(['recovered-first', 'conflict-first'] as const)(
    'publishes no known recovery when a later snapshot owner conflicts (%s)',
    async order => {
      const h = setup()
      const binding = await h.bind()
      const inspect = { kind: 'inspect' as const, subject, selection: null, body: {} }
      h.lose()
      await h.supervisor.start(inspect, binding!)
      await h.supervisor.start(inspect, binding!)
      const [first, second] = [...h.receipts.values()]

      const recovered = decodeLifecycleOperation({
        ...fixture('service inspect'),
        id: first.id,
        request_id: first.request_id
      })!

      const conflicting = decodeLifecycleOperation({
        ...fixture('service install confirm pending'),
        id: second.id,
        request_id: second.request_id
      })!

      h.receipts.clear()
      h.list(order === 'recovered-first' ? [recovered, conflicting] : [conflicting, recovered])

      expect(await h.bind()).toBeNull()
      expect(
        h.supervisor.$records
          .get()
          .filter(record => [first.request_id, second.request_id].includes(record.requestId))
          .map(record => record.operation)
      ).toEqual([null, null])
    }
  )

  it('publishes every known recovery after the complete snapshot validates', async () => {
    const h = setup()
    const binding = await h.bind()
    const inspect = { kind: 'inspect' as const, subject, selection: null, body: {} }
    h.lose()
    await h.supervisor.start(inspect, binding!)
    await h.supervisor.start(inspect, binding!)
    const admitted = [...h.receipts.values()]

    const recovered = admitted.map(operation =>
      decodeLifecycleOperation({
        ...fixture('service inspect'),
        id: operation.id,
        request_id: operation.request_id
      })!
    )

    h.receipts.clear()
    h.list(recovered)

    expect(await h.bind()).toEqual(binding)
    expect(
      h.supervisor.$records
        .get()
        .filter(record => admitted.some(operation => operation.request_id === record.requestId))
        .map(record => record.operation)
    ).toEqual(recovered)
  })

  // Break caught: not-found plus a present-time state read prematurely fences a POST still admissible on the server.
  it('keeps a missing admission blocked until the server admission window closes and current state is read', async () => {
    const h = setup()
    const binding = await h.bind()
    h.lose()
    await h.supervisor.start(h.intent, binding!)
    h.receipts.clear()
    await vi.advanceTimersByTimeAsync(15000)
    await h.supervisor.retry(h.record().key)
    expect(h.record().admissionWindowClosed).toBe(false)
    expect(h.states()).toBe(0)
    await vi.advanceTimersByTimeAsync(285001)
    await h.bind()
    expect(h.record().admissionWindowClosed).toBe(true)
    expect(h.states()).toBe(1)
    expect(h.record().operation).toBeNull()
    expect(h.supervisor.getPackageGate(binding!, identity).state).toBe('unknown')
  })

  // Break caught: replay tombstone is treated as failure/rollback, or accepted under a wrong exact operation ID.
  it('retains evicted receipt identity without inventing a terminal result', async () => {
    const h = setup()
    const binding = await h.bind()
    h.lose()
    await h.supervisor.start(h.intent, binding!)
    h.evict()
    await h.bind()
    expect(h.record()).toMatchObject({
      status: 'evicted',
      operationId: expect.any(String),
      operation: null,
      barrier: true
    })
  })

  // Break caught: a hanging admission can hold a dialog guard beyond the required transport deadline.
  it('releases a hanging admission at fifteen seconds while preserving exact uncertainty', async () => {
    const h = setup()
    const binding = await h.bind()
    const post = h.deferPost()
    const pending = h.supervisor.start(h.intent, binding!)
    await vi.advanceTimersByTimeAsync(15000)
    await pending
    expect(h.record()).toMatchObject({
      status: 'admission_unknown',
      operationId: null,
      callPending: false,
      barrier: true
    })
    post.resolve([...h.receipts.values()][0])
    await Promise.resolve()
    expect(h.record().operationId).toBeNull()
  })

  // Break caught: active backend work from a fresh actor scan is dropped when there was no renderer intent.
  it('observes exact unsolicited work without creating a new intent or worker', async () => {
    const h = setup()
    h.list([fixture('service direct prepare pending'), fixture('service install confirm pending')])
    const binding = await h.bind()
    expect(h.supervisor.$records.get().map(record => record.operationId)).toEqual([
      fixture('service direct prepare pending').id,
      fixture('service install confirm pending').id
    ])
    expect(h.supervisor.getPackageGate(binding!, identity).state).toBe('busy')
    expect(h.workers()).toBe(0)
  })

  it.each(['service install prepare pending', 'service update check pending', 'service review all pending'])(
    'keeps an active package review/check barrier for %s',
    async name => {
      const h = setup()
      h.list([fixture(name)])
      const binding = await h.bind()

      expect(h.supervisor.getPackageGate(binding!, identity).state).toBe('busy')
    }
  )

  it('keeps a supervised read-only inspection out of the base package lifecycle barrier', async () => {
    const h = setup()
    h.list([fixture('service inspect pending')])
    const binding = await h.bind()
    await h.supervisor.reconcilePackage(binding!, identity)

    expect(h.supervisor.getPackageGate(binding!, identity).state).toBe('ready')
  })

  it('blocks inspection behind active package work', async () => {
    const h = setup()
    const binding = await h.bind()
    await h.supervisor.start(h.intent, binding!)

    await expect(
      h.supervisor.start({ kind: 'inspect', subject, selection: null, body: {} }, binding!)
    ).rejects.toMatchObject({ code: 'marketplace_request_conflict' })
  })

  it('admits a fresh exact inspection while an earlier detached inspection remains active', async () => {
    const h = setup()
    const binding = await h.bind()
    const inspect = { kind: 'inspect' as const, subject, selection: null, body: {} }
    const first = await h.supervisor.start(inspect, binding!)
    const second = await h.supervisor.start(inspect, binding!)
    const requests = h.calls.filter(call => call.type === 'post').map(call => call.requestId)

    expect(second).not.toBe(first)
    expect(requests).toHaveLength(2)
    expect(requests[1]).not.toBe(requests[0])
    expect(h.calls.some(call => call.type === 'cancel')).toBe(false)
  })

  it('allows only inspection through a terminal mutation barrier awaiting PackageState', async () => {
    const h = setup()
    const binding = await h.bind()
    await h.supervisor.start(h.intent, binding!)
    h.terminal()
    await vi.advanceTimersByTimeAsync(500)
    expect(h.record()).toMatchObject({ status: 'terminal', barrier: true })

    await expect(h.supervisor.start(h.intent, binding!)).rejects.toMatchObject({
      code: 'marketplace_request_conflict'
    })
    await expect(
      h.supervisor.start(
        {
          kind: 'install_prepare',
          subject,
          selection: null,
          body: { identifier: 'company/laptop-support', package_path: null, ref: null }
        },
        binding!
      )
    ).rejects.toMatchObject({ code: 'marketplace_request_conflict' })

    await expect(
      h.supervisor.start({ kind: 'inspect', subject, selection: null, body: {} }, binding!)
    ).resolves.toEqual(expect.any(String))
  })

  // Break caught: duplicate operation identities across snapshot pages are silently joined into a complete scan.
  it('rejects a duplicate snapshot across pages and leaves scope unavailable', async () => {
    const h = setup()
    const operation = fixture('service install confirm pending')
    h.api.list = async (_binding, options) => ({
      items: [operation],
      complete: Boolean(options?.cursor),
      next_cursor: options?.cursor ? null : 'a'.repeat(32)
    })
    expect(await h.bind()).toBeNull()
    expect(h.supervisor.bindings.presentation(scope, 'old')).toBeUndefined()
  })

  // Break caught: navigating to B destroys A or settling A uses ambient B cache authority.
  it('retains independent A and B bindings through A to B to A navigation', async () => {
    const h = setup()
    const a = await h.bind()
    await h.supervisor.start(h.intent, a!)
    const b = await h.supervisor.reconcileScope({ ...scope, connectionId: 'remote-b' })
    h.queryClient.setQueryData(['workflow-marketplace', 'remote-b::support', 'installed'], 'B current')
    h.terminal()
    await h.bind()
    expect(h.record()).toMatchObject({ binding: a, status: 'terminal' })
    expect(h.supervisor.bindings.isCurrent(b!)).toBe(true)
    expect(h.queryClient.getQueryData(['workflow-marketplace', 'remote-b::support', 'installed'])).toBe('B current')
  })

  // Break caught: StrictMode's mount rehearsal creates/disposes an operation owner or unmount cancels backend work.
  it('shares the application owner across StrictMode and route provider detach/reattach', async () => {
    const provider = await import('@/app/workflows/marketplace/supervisor-provider').catch(() => null)
    expect(provider?.MarketplaceSupervisorProvider).toBeTypeOf('function')
    const h = setup()
    const binding = await h.bind()
    await h.supervisor.start(h.intent, binding!)
    const observed = new Set<unknown>()

    function Consumer() {
      observed.add(provider!.useMarketplaceSupervisor())

      return null
    }

    const tree = () =>
      createElement(
        StrictMode,
        null,
        createElement(provider!.MarketplaceSupervisorProvider, { supervisor: h.supervisor }, createElement(Consumer))
      )

    const view = render(tree())
    view.unmount()
    render(tree())
    await vi.advanceTimersByTimeAsync(500)
    expect([...observed]).toEqual([h.supervisor])
    expect(h.workers()).toBe(1)
    expect(h.reads()).toBe(1)
  })

  // Break caught: an unbounded result list grows renderer history, or unresolved barriers are evicted to make room.
  it('retains at most 128 resolved histories while preserving every unresolved barrier', async () => {
    const h = setup()

    const operations = Array.from({ length: 130 }, (_, index) => {
      const suffix = (index + 1).toString(16).padStart(32, '0')

      return decodeLifecycleOperation({
        ...fixture('service inspect'),
        id: `wmop_aaaaaaaaaaaa_${suffix}`,
        request_id: `wmreq_${corpus.capabilities.registry_epoch}_1788609600000_${suffix}`
      })!
    })

    h.api.list = async (_binding, options) => ({
      items: options?.cursor ? operations.slice(100) : operations.slice(0, 100),
      complete: Boolean(options?.cursor),
      next_cursor: options?.cursor ? null : 'a'.repeat(32)
    })
    await h.bind()
    expect(h.supervisor.$records.get()).toHaveLength(128)
    expect(h.supervisor.$records.get().every(record => record.status === 'terminal')).toBe(true)
  })

  // Break caught: application setup repeats native subscriptions, ignores exhaustion, or disposes only a route provider.
  it('installs one main-window owner and tears down native and visibility subscriptions on pagehide', async () => {
    const provider = await import('@/app/workflows/marketplace/supervisor-provider')
    const listeners = new Set<(event: { kind: 'exhausted' }) => void>()
    const previous = window.hermesDesktop
    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: {
        onConnectionGenerationChanged: (listener: (event: { kind: 'exhausted' }) => void) => {
          listeners.add(listener)

          return () => listeners.delete(listener)
        }
      }
    })
    const queries = new QueryClient()

    try {
      const first = provider.startMainWindowMarketplaceSupervision(queries)
      const second = provider.startMainWindowMarketplaceSupervision(queries)
      expect(first).toBeDefined()
      expect(first).toBe(second)
      expect(listeners.size).toBe(1)

      for (const listener of listeners) {
        listener({ kind: 'exhausted' })
      }

      expect(first!.$status.get()).toBe('restart_required')
      window.dispatchEvent(new Event('pagehide'))
      expect(first!.$status.get()).toBe('disposed')
      expect(listeners.size).toBe(0)
      expect(vi.getTimerCount()).toBe(0)
    } finally {
      Object.defineProperty(window, 'hermesDesktop', { configurable: true, value: previous })
      queries.clear()
    }
  })

  // Break caught: unresolved observations are dropped for history retention, or capacity admits more work.
  it('preserves more than 128 unresolved observations and refuses further admission', async () => {
    const h = setup()

    const operations = Array.from({ length: 129 }, (_, index) => {
      const suffix = (index + 1).toString(16).padStart(32, '0')

      return decodeLifecycleOperation({
        ...fixture('service inspect pending'),
        id: `wmop_aaaaaaaaaaaa_${suffix}`,
        request_id: `wmreq_${corpus.capabilities.registry_epoch}_1788609600000_${suffix}`
      })!
    })

    h.api.list = async (_binding, options) => ({
      items: options?.cursor ? operations.slice(100) : operations.slice(0, 100),
      complete: Boolean(options?.cursor),
      next_cursor: options?.cursor ? null : 'a'.repeat(32)
    })
    const binding = await h.bind()
    expect(h.supervisor.$records.get()).toHaveLength(129)
    await expect(
      h.supervisor.start(
        { kind: 'refresh', subject: { type: 'source', source_name: 'other' }, selection: null, body: {} },
        binding!
      )
    ).rejects.toMatchObject({ code: 'marketplace_admission_capacity' })
    expect(h.workers()).toBe(0)
  })

  // Break caught: an invalidated in-flight capability scan prevents a new native generation from being reconciled.
  it('restarts a superseded capability scan immediately and rejects its delayed old settlement', async () => {
    const h = setup()
    await h.bind()
    const cap = h.deferCapabilities()
    const old = h.bind()
    await vi.advanceTimersByTimeAsync(0)
    h.changeGeneration()
    h.resumeCapabilities()
    const freshPromise = h.bind()
    await vi.advanceTimersByTimeAsync(0)
    expect(h.probes()).toBe(3)
    const fresh = await freshPromise
    expect(fresh?.connectionGeneration).toBe(2)
    cap.resolve(corpus.capabilities)
    expect(await old).toBeNull()
    expect(h.supervisor.bindings.isCurrent(fresh!)).toBe(true)
  })

  it('projects only declared capabilities for the exact current unsuspended binding', async () => {
    const h = setup()
    const capabilities = h.api.capabilities
    h.api.capabilities = async input => ({
      ...((await capabilities(input)) as object),
      capabilities: ['operations', 'package_state']
    })
    const binding = (await h.bind())!
    expect(h.supervisor.supports(binding, ['operations', 'package_state'])).toBe(true)
    expect(h.supervisor.supports(binding, ['operations', 'transactions'])).toBe(false)
    expect(h.supervisor.supports({ ...binding, connectionGeneration: 2 }, ['operations'])).toBe(false)
    h.visibility.set(false)
    expect(h.supervisor.supports(binding, ['operations'])).toBe(false)
  })

  it('renews idle authority before expiry through one exact-scope scan', async () => {
    const h = setup()
    const binding = (await h.bind())!
    await vi.advanceTimersByTimeAsync(239999)
    expect(h.probes()).toBe(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(h.probes()).toBe(2)
    expect(h.calls.filter(call => call.type === 'list').map(call => call.binding)).toEqual([binding, binding])
    await vi.advanceTimersByTimeAsync(60001)
    expect(h.supervisor.supports(binding, ['operations', 'transactions'])).toBe(true)
    expect(h.posts()).toBe(0)
  })

  it('replaces the renewal deadline after explicit reconciliation and accounts for slow scans', async () => {
    const h = setup()
    await h.bind()
    await vi.advanceTimersByTimeAsync(239000)
    const page = deferred<unknown>()
    const list = h.api.list
    h.api.list = () => page.promise
    const pending = h.bind()
    await vi.advanceTimersByTimeAsync(120000)
    expect(h.probes()).toBe(2)
    expect(vi.getTimerCount()).toBe(0)
    h.api.list = list
    page.resolve({ items: [], complete: true, next_cursor: null })
    const binding = (await pending)!
    await vi.advanceTimersByTimeAsync(119999)
    expect(h.probes()).toBe(2)
    await vi.advanceTimersByTimeAsync(1)
    expect(h.probes()).toBe(3)
    expect(h.supervisor.supports(binding, ['operations'])).toBe(true)
  })

  it.each(['hidden', 'disconnected', 'suspended', 'reconfigured', 'exhausted', 'disposed'])(
    'cancels idle renewal when %s',
    async reason => {
      const h = setup()
      const binding = (await h.bind())!
      expect(vi.getTimerCount()).toBe(1)

      if (reason === 'hidden') {
        h.visibility.set(false)
      } else if (reason === 'disconnected') {
        h.disconnect()
      } else if (reason === 'reconfigured') {
        h.changeGeneration()
      } else if (reason === 'exhausted') {
        h.notify('marketplace_connection_generation_exhausted')
      } else if (reason === 'disposed') {
        h.supervisor.dispose()
      } else {
        h.notify('suspended')
      }

      expect(vi.getTimerCount()).toBe(0)
      await vi.advanceTimersByTimeAsync(300001)
      expect(h.probes()).toBe(1)
      expect(h.supervisor.supports(binding, ['operations'])).toBe(false)
    }
  )

  it('deduplicates renewal with explicit reconciliation and rejects late old-binding capabilities', async () => {
    const h = setup()
    const oldBinding = (await h.bind())!
    const oldSample = h.deferCapabilities()
    await vi.advanceTimersByTimeAsync(240000)
    expect(h.probes()).toBe(2)
    const first = h.bind()
    expect(h.bind()).toBe(first)
    expect(h.supervisor.supports(oldBinding, ['operations'])).toBe(false)
    await expect(h.supervisor.start(h.intent, oldBinding)).rejects.toMatchObject({
      code: 'marketplace_clock_revalidation_required'
    })
    expect(h.posts()).toBe(0)
    await vi.advanceTimersByTimeAsync(300001)
    expect(h.probes()).toBe(2)
    h.changeGeneration()
    h.resumeCapabilities()
    const capabilities = h.api.capabilities
    h.api.capabilities = async input => ({ ...((await capabilities(input)) as object), capabilities: ['operations'] })
    const current = (await h.bind())!
    oldSample.resolve(corpus.capabilities)
    expect(await first).toBeNull()
    expect(h.supervisor.supports(oldBinding, ['transactions'])).toBe(false)
    expect(h.supervisor.supports(current, ['operations'])).toBe(true)
    expect(h.supervisor.supports(current, ['transactions'])).toBe(false)
    expect(vi.getTimerCount()).toBe(1)
    await vi.advanceTimersByTimeAsync(240000)
    expect(h.probes()).toBe(4)
    expect(h.supervisor.supports(current, ['operations'])).toBe(true)
    expect(h.supervisor.supports(current, ['transactions'])).toBe(false)
  })

  it('does not publish capabilities from a late old-binding sample and expires stale authority', async () => {
    const h = setup()
    const oldBinding = (await h.bind())!
    const oldSample = h.deferCapabilities()
    const old = h.bind()
    await vi.advanceTimersByTimeAsync(0)
    h.changeGeneration()
    h.resumeCapabilities()
    const capabilities = h.api.capabilities
    h.api.capabilities = async input => ({ ...((await capabilities(input)) as object), capabilities: ['operations'] })
    const current = (await h.bind())!
    oldSample.resolve(corpus.capabilities)
    await old
    expect(h.supervisor.supports(oldBinding, ['transactions'])).toBe(false)
    expect(h.supervisor.supports(current, ['transactions'])).toBe(false)
    expect(h.supervisor.supports(current, ['operations'])).toBe(true)

    // A throttled event loop may not run the renewal timer before both clocks age out.
    const wall = Date.now(),
      mono = performance.now()

    const monotonic = vi.spyOn(performance, 'now')

    try {
      vi.setSystemTime(wall + 300000)
      monotonic.mockReturnValue(mono + 300000)
      expect(h.supervisor.supports(current, ['operations'])).toBe(true)
      vi.setSystemTime(wall + 300001)
      monotonic.mockReturnValue(mono + 300001)
      expect(h.supervisor.supports(current, ['operations'])).toBe(false)
    } finally {
      monotonic.mockRestore()
    }

    h.notify('marketplace_connection_generation_exhausted')
    expect(h.supervisor.supports(current, [])).toBe(false)
  })

  // Break caught: the application supervisor bypasses the physical limiter for polling or queued polls survive hiding.
  it('bounds actual supervisor polls to three and drops queued observations when hidden', async () => {
    const h = setup()
    h.list(
      [
        'service inspect pending',
        'service direct prepare pending',
        'service install confirm pending',
        'service update check pending'
      ].map(fixture)
    )
    const get = h.deferGet()
    await h.bind()
    await vi.advanceTimersByTimeAsync(500)
    expect(h.reads()).toBe(3)
    h.visibility.set(false)
    await vi.advanceTimersByTimeAsync(0)
    expect(h.supervisor.$records.get().every(record => !record.callPending)).toBe(true)
    get.resolve(fixture('service inspect'))
    await vi.advanceTimersByTimeAsync(0)
    expect(h.reads()).toBe(3)
    expect(vi.getTimerCount()).toBe(0)
  })

  // Break caught: a known request in the snapshot can point to a different operation and the scan is still called complete.
  it('rejects a valid snapshot that changes a known request operation identity', async () => {
    const h = setup()
    const binding = await h.bind()
    await h.supervisor.start(h.intent, binding!)
    const changed = decodeLifecycleOperation({ ...[...h.receipts.values()][0], id: corpus.validOperationB.id })!
    h.list([changed])
    expect(await h.bind()).toBeNull()
    expect(h.supervisor.bindings.presentation(scope, 'old')).toBeUndefined()
  })

  // Break caught: the auxiliary local-state path swallows global exhaustion and leaves other work usable.
  it('propagates local-state exhaustion into global quarantine without clearing barriers', async () => {
    const h = setup()
    const binding = await h.bind()
    await h.supervisor.start(h.intent, binding!)

    h.api.packageState = async () => {
      throw new LifecycleApiError('marketplace_connection_generation_exhausted', 0)
    }

    expect(await h.supervisor.reconcilePackage(binding!, identity)).toBeNull()
    expect(h.supervisor.$status.get()).toBe('restart_required')
    expect(h.record().barrier).toBe(true)
  })

  // Break caught: a malformed/discontinuous clock failure from start does not initiate the required fresh probe.
  it('quarantines and reprobes on stale admission clock before any POST', async () => {
    const h = setup()
    const binding = await h.bind()
    const before = h.probes()
    vi.setSystemTime(Date.now() + 31000)
    await expect(h.supervisor.start(h.intent, binding!)).rejects.toMatchObject({
      code: 'marketplace_clock_revalidation_required'
    })
    await vi.advanceTimersByTimeAsync(0)
    expect(h.posts()).toBe(0)
    expect(h.probes()).toBe(before + 1)
  })

  // Break caught: repeated expired snapshots hot-loop or omit known exact lookups silently.
  it('restarts an expired list once and leaves retry explicit after the second expiration', async () => {
    const h = setup()
    let lists = 0

    h.api.list = async () => {
      lists++
      throw new LifecycleApiError('marketplace_list_expired', 410)
    }

    expect(await h.bind()).toBeNull()
    expect(lists).toBe(2)
    await vi.advanceTimersByTimeAsync(2000)
    expect(lists).toBe(2)
    expect(h.supervisor.bindings.presentation(scope, 'old')).toBeUndefined()
  })

  // Scope cancellation and its coordinator callback must not publish the same guard cleanup twice.
  it('publishes one guard cleanup when hiding also invalidates the coordinator attempt', async () => {
    const h = setup()
    const binding = await h.bind()
    await h.supervisor.start(h.intent, binding!)
    const states: string[] = []
    const detach = h.supervisor.$records.listen(records => states.push(records[0].status))
    h.visibility.set(false)
    expect(states).toEqual(['suspended'])
    expect(h.record()).toMatchObject({ barrier: true, callPending: false })
    detach()
  })

  // A disconnected scope must not be repeatedly cancelled or silently resumed by another scope's visibility return.
  it('disconnects only the exact origin and resumes it once only when explicitly reconnected', async () => {
    const h = setup()
    await h.bind()
    const other = await h.supervisor.reconcileScope({ ...scope, connectionId: 'remote-b' })
    const cancellations = vi.spyOn(h.queryClient, 'cancelQueries')
    h.disconnect()
    expect(h.supervisor.bindings.isCurrent(other!)).toBe(true)
    expect(h.supervisor.bindings.presentation(other!, 'other-current')).toBe('other-current')
    h.visibility.set(false)
    expect(cancellations.mock.calls.map(call => call[0]?.queryKey)).toEqual([
      ['workflow-marketplace', 'remote-a::support'],
      ['workflow-marketplace', 'remote-b::support']
    ])
    const dispatched: Array<string | null> = []
    const capabilities = h.api.capabilities

    h.api.capabilities = input => {
      dispatched.push(input.connectionId)

      return capabilities(input)
    }

    h.reconnect()
    h.visibility.set(true)
    await vi.advanceTimersByTimeAsync(0)
    expect(dispatched).toEqual(['remote-b'])

    const first = h.bind(),
      second = h.bind()

    expect(first).toBe(second)
    expect(await first).not.toBeNull()
    expect(dispatched).toEqual(['remote-b', 'remote-a'])
    cancellations.mockRestore()
  })

  // Late completion of an invalidated final await must not purge a newer valid binding's data.
  it('preserves the resumed binding and new-scope data when an old final cancellation settles late', async () => {
    const h = setup()
    const binding = await h.bind()
    const capabilities = h.api.capabilities
    h.api.capabilities = async input => ({
      ...((await capabilities(input)) as object),
      principal_binding: 'c'.repeat(64)
    })

    h.api.packageState = async () => {
      throw new LifecycleApiError('marketplace_network_error', 401)
    }

    const finalCancellation = deferred<void>()
    const cancelQueries = h.queryClient.cancelQueries.bind(h.queryClient)
    let cancellations = 0

    const cancellation = vi.spyOn(h.queryClient, 'cancelQueries').mockImplementation(async (...args) => {
      await cancelQueries(...args)

      if (++cancellations === 3) {
        await finalCancellation.promise
      }
    })

    const pending = h.supervisor.reconcilePackage(binding!, identity)
    await vi.advanceTimersByTimeAsync(0)
    expect(cancellations).toBe(3)
    h.visibility.set(false)
    h.visibility.set(true)
    const resumed = await h.bind()
    expect(resumed?.principalBinding).toBe('c'.repeat(64))
    const key = ['workflow-marketplace', 'remote-a::support', 'installed']
    h.queryClient.setQueryData(key, 'fresh-current-data')
    finalCancellation.resolve()
    expect(await pending).toBeNull()
    expect(h.supervisor.bindings.isCurrent(resumed!)).toBe(true)
    expect(h.queryClient.getQueryData(key)).toBe('fresh-current-data')
    expect(h.probes()).toBe(3)
    cancellation.mockRestore()
  })

  // Review R2 final-await race: aborting transport ownership alone cannot cancel a coordinator publication attempt.
  it.each([
    ['hidden', true],
    ['hidden', false],
    ['disconnect', true],
    ['disconnect', false],
    ['dispose', true],
    ['dispose', false]
  ] as const)(
    'invalidates changed-principal publication waiting on final query cancellation on %s (operation record: %s)',
    async (boundary, withOperation) => {
      const h = setup()
      const binding = await h.bind()

      if (withOperation) {
        await h.supervisor.start(h.intent, binding!)
      }

      const capabilities = h.api.capabilities
      h.api.capabilities = async input => ({
        ...((await capabilities(input)) as object),
        principal_binding: 'c'.repeat(64)
      })

      h.api.packageState = async () => {
        throw new LifecycleApiError('marketplace_network_error', 401)
      }

      const finalCancellation = deferred<void>()
      const cancelQueries = h.queryClient.cancelQueries.bind(h.queryClient)
      let cancellations = 0

      const cancellation = vi.spyOn(h.queryClient, 'cancelQueries').mockImplementation(async (...args) => {
        await cancelQueries(...args)
        cancellations++

        if (cancellations === 3) {
          await finalCancellation.promise
        }
      })

      const pending = h.supervisor.reconcilePackage(binding!, identity)
      await vi.advanceTimersByTimeAsync(0)
      expect(cancellations).toBe(3)
      expect(h.supervisor.bindings.state(scope).kind).toBe('probing')
      const before = h.probes()

      if (boundary === 'hidden') {
        h.visibility.set(false)
      } else if (boundary === 'disconnect') {
        h.disconnect()
      } else {
        h.supervisor.dispose()
      }

      finalCancellation.resolve()
      expect(await pending).toBeNull()
      expect(h.supervisor.bindings.state(scope).kind).toBe('unavailable')
      expect(h.supervisor.bindings.presentation(scope, 'private')).toBeUndefined()

      if (withOperation) {
        expect(h.record()).toMatchObject({ barrier: true, callPending: false, binding })
      } else {
        expect(h.supervisor.$records.get()).toEqual([])
      }

      expect(h.probes()).toBe(before)
      expect(await h.bind()).toBeNull()

      if (boundary !== 'dispose') {
        if (boundary === 'hidden') {
          h.visibility.set(true)
        } else {
          h.reconnect()
        }

        const first = h.bind(),
          second = h.bind()

        expect(first).toBe(second)
        const current = await first
        expect(current?.principalBinding).toBe('c'.repeat(64))
        expect(h.supervisor.bindings.isCurrent(current!)).toBe(true)
        expect(h.probes()).toBe(before + 1)

        if (withOperation) {
          expect(h.record().barrier).toBe(true)
        }
      }

      h.supervisor.dispose()
      h.supervisor.dispose()
      cancellation.mockRestore()
      expect(h.listeners.size).toBe(0)
      expect(vi.getTimerCount()).toBe(0)
    }
  )

  // Recovery policy checks: a replacement scan has one policy attempt, and a nested package read cannot await itself.
  it('bounds repeated snapshot authentication recovery to one owned replacement scan', async () => {
    const h = setup()
    await h.bind()
    const before = h.probes()
    let lists = 0

    h.api.list = async () => {
      lists++
      throw new LifecycleApiError('marketplace_network_error', 401)
    }

    expect(await h.bind()).toBeNull()
    expect(h.probes()).toBe(before + 2)
    expect(lists).toBe(2)
    await vi.advanceTimersByTimeAsync(1000)
    expect(h.probes()).toBe(before + 2)
    expect(h.supervisor.bindings.presentation(scope, 'private')).toBeUndefined()
  })

  it('does not recursively reprobe or retain a guard when a nested expired-admission state read loses authorization', async () => {
    const h = setup()
    const binding = await h.bind()
    h.lose()
    await h.supervisor.start(h.intent, binding!)
    h.receipts.clear()
    await vi.advanceTimersByTimeAsync(300001)

    h.api.packageState = async () => {
      throw new LifecycleApiError('marketplace_network_error', 401)
    }

    const before = h.probes()
    expect(await h.bind()).toBeNull()
    expect(h.probes()).toBe(before + 1)
    expect(h.record()).toMatchObject({
      callPending: false,
      barrier: true,
      operation: null,
      admissionWindowClosed: false
    })
    expect(h.supervisor.bindings.presentation(scope, 'private')).toBeUndefined()
    expect(vi.getTimerCount()).toBe(0)
  })

  // Review R3: request and operation identifiers are a bijection, including retained terminal histories.
  it.each([
    ['pending', 'request'],
    ['pending', 'kind'],
    ['pending', 'subject'],
    ['pending', 'selection'],
    ['terminal', 'request'],
    ['terminal', 'kind'],
    ['terminal', 'subject'],
    ['terminal', 'selection']
  ] as const)(
    'rejects an inverse operation-ID collision with a valid changed %s/%s snapshot',
    async (state, change) => {
      const h = setup()
      const original = fixture(state === 'terminal' ? 'service grant one A' : 'service grant one A pending')
      h.list([original])
      await h.bind()
      h.api.lookup = async () => ({ state: 'found', operation: original })

      const value = {
        ...fixture(change === 'kind' ? 'service review one A pending' : 'service grant one A pending'),
        id: original.id,
        request_id: corpus.validOperationB.request_id,
        ...(change === 'subject'
          ? { subject: { type: 'package', identity: { ...identity, package_id: 'other-package' } } }
          : {}),
        ...(change === 'selection' ? { selection: { type: 'all' } } : {})
      }

      const changed = decodeLifecycleOperation(value)
      expect(changed).not.toBeNull()
      expect(changed!.request_id).not.toBe(original.request_id)
      h.list([changed!])
      expect(await h.bind()).toBeNull()
      expect(h.supervisor.$records.get()).toHaveLength(1)
      expect(h.record()).toMatchObject({
        operationId: original.id,
        requestId: original.request_id,
        operation: original,
        barrier: true,
        callPending: false
      })
      expect(h.supervisor.bindings.presentation(scope, 'private')).toBeUndefined()
      expect(vi.getTimerCount()).toBe(0)
    }
  )

  // Review R2: recovery must remain a cancellable scope-owned operation at both asynchronous boundaries.
  it.each([
    ['package', 'hidden'],
    ['package', 'disconnect'],
    ['snapshot', 'hidden'],
    ['snapshot', 'disconnect']
  ] as const)('cancels %s recovery hidden in descriptor resolution on %s', async (source, boundary) => {
    const h = setup()
    const binding = await h.bind()
    const descriptor = deferred<{ connectionId: string; connectionGeneration: number }>()

    const fail = async () => {
      h.holdDescriptor(descriptor)
      throw new LifecycleApiError('marketplace_network_error', 401)
    }

    if (source === 'package') {
      h.api.packageState = fail
    } else {
      h.api.list = fail
    }

    const pending = source === 'package' ? h.supervisor.reconcilePackage(binding!, identity) : h.bind()
    await vi.advanceTimersByTimeAsync(0)
    const before = h.probes()

    if (boundary === 'hidden') {
      h.visibility.set(false)
    } else {
      h.disconnect()
    }

    descriptor.resolve({ connectionId: scope.connectionId, connectionGeneration: 1 })
    expect(await pending).toBeNull()
    expect(h.probes()).toBe(before)
    expect(h.supervisor.bindings.presentation(scope, 'private')).toBeUndefined()
    expect(vi.getTimerCount()).toBe(0)
  })

  it.each([
    ['package', 'hidden'],
    ['package', 'disconnect'],
    ['snapshot', 'hidden'],
    ['snapshot', 'disconnect']
  ] as const)('drops %s recovery queued behind physical capacity on %s', async (source, boundary) => {
    const h = setup()
    const binding = await h.bind()
    const failedRead = deferred<unknown>()

    if (source === 'package') {
      h.api.packageState = () => failedRead.promise
    } else {
      h.api.list = input =>
        input.connectionId === scope.connectionId
          ? failedRead.promise
          : Promise.resolve({ items: [], complete: true, next_cursor: null })
    }

    const recovery = source === 'package' ? h.supervisor.reconcilePackage(binding!, identity) : h.bind()
    await vi.advanceTimersByTimeAsync(0)
    const before = h.probes()
    const blockers = deferred<unknown>()
    let blockingCalls = 0
    const capabilities = h.api.capabilities

    h.api.capabilities = input => {
      if (input.connectionId === scope.connectionId) {
        return capabilities(input)
      }

      blockingCalls++

      return blockers.promise
    }

    const other = Array.from({ length: 3 }, (_, index) =>
      h.supervisor.reconcileScope({ ...scope, connectionId: `block-${index}` })
    )

    await vi.advanceTimersByTimeAsync(0)
    expect(blockingCalls).toBe(2)
    failedRead.reject(new LifecycleApiError('marketplace_network_error', 401))
    await vi.advanceTimersByTimeAsync(0)
    expect(blockingCalls).toBe(3)
    expect(h.probes()).toBe(before)

    if (boundary === 'hidden') {
      h.visibility.set(false)
    } else {
      h.disconnect()
    }

    blockers.resolve(corpus.capabilities)
    expect(await recovery).toBeNull()
    await Promise.all(other)
    expect(h.probes()).toBe(before)
    expect(h.supervisor.bindings.presentation(scope, 'private')).toBeUndefined()
    expect(vi.getTimerCount()).toBe(0)
  })

  // Review R1: capability exhaustion must cancel every queued scope, not just operation records.
  it('drops the fifth queued capability observer after global exhaustion without releasing physical slots early', async () => {
    const h = setup()
    const binding = await h.bind()
    await h.supervisor.start(h.intent, binding!)
    const replies = Array.from({ length: 5 }, () => deferred<unknown>())
    const dispatched: string[] = []

    h.api.capabilities = async input => {
      dispatched.push(input.connectionId!)

      return replies[Number(input.connectionId!.slice(-1))].promise
    }

    const pending = replies.map((_, index) => h.supervisor.reconcileScope({ ...scope, connectionId: `scope-${index}` }))
    await vi.advanceTimersByTimeAsync(0)
    expect(dispatched).toHaveLength(3)
    replies[0].reject(new LifecycleApiError('marketplace_connection_generation_exhausted', 0))
    await vi.advanceTimersByTimeAsync(0)
    expect(h.supervisor.$status.get()).toBe('restart_required')
    const atExhaustion = [...dispatched]
    expect(atExhaustion).not.toContain('scope-4')
    expect(h.record().barrier).toBe(true)
    expect(h.record().callPending).toBe(false)
    replies[1].resolve(corpus.capabilities)
    await vi.advanceTimersByTimeAsync(0)
    expect(dispatched).toEqual(atExhaustion)

    for (const reply of replies.slice(2)) {
      reply.resolve(corpus.capabilities)
    }

    expect(await Promise.all(pending)).toEqual(replies.map(() => null))
    expect(await h.bind()).toBeNull()
    h.visibility.set(false)
    h.visibility.set(true)
    await vi.advanceTimersByTimeAsync(1000)
    expect(dispatched).toEqual(atExhaustion)
    expect(vi.getTimerCount()).toBe(0)
  })

  // Break caught: an unavailable actor snapshot prevents exact recovery of an already-known request.
  it('looks up a known admission even when snapshot listing remains expired', async () => {
    const h = setup()
    const binding = await h.bind()
    h.lose()
    await h.supervisor.start(h.intent, binding!)

    h.api.list = async () => {
      throw new LifecycleApiError('marketplace_list_expired', 410)
    }

    expect(await h.bind()).toBeNull()
    expect(h.calls.filter(call => call.type === 'lookup').map(call => call.requestId)).toEqual([h.record().requestId])
    expect(h.record().operationId).toBe([...h.receipts.values()][0].id)
    expect(h.record().barrier).toBe(true)
    expect(h.record().callPending).toBe(false)
    expect(h.workers()).toBe(1)
  })

  // Break caught: a queued local-state request starts after the origin has been hidden or disconnected.
  it.each(['hidden', 'disconnect'] as const)('drops queued package reconciliation on %s', async boundary => {
    const h = setup()
    h.list(
      ['service inspect pending', 'service direct prepare pending', 'service install confirm pending'].map(fixture)
    )
    const waiting = h.deferGet()
    const binding = await h.bind()
    await vi.advanceTimersByTimeAsync(500)
    expect(h.reads()).toBe(3)
    const state = h.supervisor.reconcilePackage(binding!, identity)

    if (boundary === 'hidden') {
      h.visibility.set(false)
    } else {
      h.disconnect()
    }

    waiting.resolve(fixture('service inspect'))
    expect(await state).toBeNull()
    expect(h.states()).toBe(0)
    expect(vi.getTimerCount()).toBe(0)
  })

  // Break caught: terminal history with an unresolved package barrier cannot recover under a new exact native lease.
  it('rebinds immutable terminal history only after exact lookup and releases an interrupted terminal retry guard', async () => {
    const h = setup()
    const binding = await h.bind()
    await h.supervisor.start(h.intent, binding!)
    h.terminal()
    await h.supervisor.retry(h.record().key)
    const terminal = h.record().operation
    const waiting = h.deferGet()
    const retry = h.supervisor.retry(h.record().key)
    expect(h.record().callPending).toBe(true)
    h.changeGeneration()
    expect(h.record().callPending).toBe(false)
    await retry
    h.list([])
    const before = h.calls.filter(call => call.type === 'lookup').length
    const fresh = await h.bind()
    expect(h.calls.filter(call => call.type === 'lookup')).toHaveLength(before + 1)
    expect(h.record().binding).toEqual(fresh)
    expect(h.record().operation).toEqual(terminal)
    expect(h.record().status).toBe('terminal')
    expect(h.record().barrier).toBe(true)
    waiting.resolve(terminal)
    await vi.advanceTimersByTimeAsync(0)
    expect(h.record().binding).toEqual(fresh)
  })

  // Break caught: auxiliary full-app renderers create their own operation supervisor and background observers.
  it('does not attach application supervision in auxiliary windows', async () => {
    const windows = await import('@/store/windows')
    const provider = await import('@/app/workflows/marketplace/supervisor-provider')
    const auxiliary = vi.spyOn(windows, 'isAuxiliaryWindow').mockReturnValue(true)
    const queries = new QueryClient()
    const supervisor = provider.startMainWindowMarketplaceSupervision(queries)!

    try {
      expect(supervisor).toBeNull()
      expect(vi.getTimerCount()).toBe(0)
    } finally {
      supervisor?.dispose()
      auxiliary.mockRestore()
      queries.clear()
    }
  })

  // Break caught: main-window startup uses ambient routing for subsequent native requests, or hidden startup never resumes.
  it('binds native generation and backend actor through the real application API on visibility return', async () => {
    const provider = await import('@/app/workflows/marketplace/supervisor-provider')
    const client = await import('@/api/client')
    const gatewayStore = await import('@/store/gateway')

    const oldDesktop = window.hermesDesktop,
      oldGateway = gatewayStore.$gateway.get()

    const oldConnection = client.getApiRequestConnection(),
      oldProfile = client.getApiRequestProfile()

    const gateway = new client.HermesGateway()
    vi.spyOn(gateway, 'connectionState', 'get').mockReturnValue('open')
    vi.spyOn(gateway, 'onState').mockImplementation(listener => {
      listener('open')

      return () => undefined
    })
    const requests: Array<Record<string, unknown>> = []
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' })
    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: {
        getConnectionFor: async () => ({ connectionId: 'remote-a', connectionGeneration: 7 }),
        apiStructured: async (request: Record<string, unknown>) => {
          requests.push(request)

          return {
            ok: true,
            value: String(request.path).endsWith('/capabilities')
              ? corpus.capabilities
              : { items: [], complete: true, next_cursor: null }
          }
        }
      }
    })
    client.setApiRequestConnection('remote-a')
    client.setApiRequestProfile('support')
    gatewayStore.$gateway.set(gateway)
    const queries = new QueryClient()
    const supervisor = provider.startMainWindowMarketplaceSupervision(queries)!

    try {
      expect(requests).toEqual([])
      Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
      document.dispatchEvent(new Event('visibilitychange'))
      await vi.advanceTimersByTimeAsync(0)
      expect(requests).toHaveLength(2)
      expect(requests[0]).toMatchObject({
        connectionId: 'remote-a',
        profile: 'support',
        expectedConnectionGeneration: 7
      })
      expect(requests[0]).not.toHaveProperty('expectedMarketplacePrincipalBinding')
      expect(requests[1]).toMatchObject({
        connectionId: 'remote-a',
        profile: 'support',
        expectedConnectionGeneration: 7,
        expectedMarketplacePrincipalBinding: corpus.capabilities.principal_binding
      })
      expect(supervisor.bindings.state(scope).kind).toBe('bound')
    } finally {
      supervisor.dispose()
      queries.clear()
      gatewayStore.$gateway.set(oldGateway)
      client.setApiRequestConnection(oldConnection)
      client.setApiRequestProfile(oldProfile)
      Object.defineProperty(window, 'hermesDesktop', { configurable: true, value: oldDesktop })
      Reflect.deleteProperty(document, 'visibilityState')
      vi.restoreAllMocks()
    }
  })
})
