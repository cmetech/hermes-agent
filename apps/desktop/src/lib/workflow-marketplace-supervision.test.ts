import { afterEach, describe, expect, it, vi } from 'vitest'

import corpus from '../../../../tests/fixtures/workflow-marketplace-lifecycle-v2.json'

import { decodeLifecycleOperation } from './workflow-marketplace-lifecycle-codec'

const supervision = await import('./workflow-marketplace-supervision').catch(() => null)

const binding = {
  connectionId: 'remote-a',
  connectionGeneration: 1,
  profile: 'support',
  principalBinding: corpus.capabilities.principal_binding,
  registryEpoch: corpus.capabilities.registry_epoch
}

const operation = decodeLifecycleOperation(corpus.validOperationA)!

afterEach(() => vi.useRealTimers())

describe('exact marketplace supervision', () => {
  // Break caught: a field omitted from correlation allows an unrelated operation to replace a watch.
  it.each(['request_id', 'id', 'kind', 'subject', 'selection', 'profile', 'registry_epoch'])(
    'rejects mismatched %s before accepting an observation',
    field => {
      expect(supervision?.acceptSupervisedOperation).toBeTypeOf('function')

      const expected = {
        requestId: operation.request_id,
        operationId: operation.id,
        kind: operation.kind,
        subject: operation.subject,
        selection: operation.selection
      }

      expect(supervision!.acceptSupervisedOperation(operation, binding, expected)).toEqual(operation)
      expect(() =>
        supervision!.acceptSupervisedOperation(
          { ...operation, [field]: field === 'id' ? corpus.validOperationB.id : 'different' },
          binding,
          expected
        )
      ).toThrow()
    }
  )

  // Break caught: decoding succeeds but a valid operation belonging to another intent is accepted.
  it.each([
    { requestId: corpus.validOperationB.request_id },
    { operationId: corpus.validOperationB.id },
    { kind: 'inspect' as const },
    { subject: { type: 'source' as const, source_name: 'other' } },
    { selection: { type: 'all' as const } }
  ])('requires intent equality even when the entire backend operation is valid: %j', difference => {
    const expected = {
      requestId: operation.request_id,
      operationId: operation.id,
      kind: operation.kind,
      subject: operation.subject,
      selection: operation.selection,
      ...difference
    }

    expect(() => supervision!.acceptSupervisedOperation(operation, binding, expected)).toThrow()
  })

  // Break caught: delimiting keys ambiguously or ignoring any native/actor field aliases different origins.
  it('keys records by all binding fields and exact request and operation identities', () => {
    expect(supervision?.supervisionKey).toBeTypeOf('function')
    const key = supervision!.supervisionKey(binding, operation.request_id, operation.id)

    for (const [field, value] of Object.entries({
      connectionId: null,
      connectionGeneration: 2,
      profile: 'other',
      principalBinding: 'c'.repeat(64),
      registryEpoch: 'd'.repeat(32)
    })) {
      expect(supervision!.supervisionKey({ ...binding, [field]: value }, operation.request_id, operation.id)).not.toBe(
        key
      )
    }

    expect(supervision!.supervisionKey(binding, corpus.validOperationB.request_id, operation.id)).not.toBe(key)
    expect(supervision!.supervisionKey(binding, operation.request_id, null)).not.toBe(key)
  })

  // Break caught: resolved cadence listeners accumulate or disposal leaves a timer alive.
  it.each(['elapsed', 'abort'])('removes the cadence timer and listener after %s', async finish => {
    vi.useFakeTimers()
    expect(supervision?.waitForSupervision).toBeTypeOf('function')
    const controller = new AbortController()
    const add = vi.spyOn(controller.signal, 'addEventListener')
    const remove = vi.spyOn(controller.signal, 'removeEventListener')
    const waiting = supervision!.waitForSupervision(500, controller.signal).catch(() => undefined)
    expect(vi.getTimerCount()).toBe(1)
    expect(add).toHaveBeenCalledTimes(1)

    if (finish === 'abort') {
      controller.abort()
    } else {
      await vi.advanceTimersByTimeAsync(500)
    }

    await waiting
    expect(vi.getTimerCount()).toBe(0)
    expect(remove.mock.calls[0]).toEqual(add.mock.calls[0])
  })

  // Break caught: abort frees a physical IPC slot and allows more than three real calls, or a queued abort dispatches later.
  it('limits physical calls to three even when observation is aborted', async () => {
    expect(supervision?.createSupervisionScheduler).toBeTypeOf('function')
    const scheduler = supervision!.createSupervisionScheduler()
    const controllers = Array.from({ length: 5 }, () => new AbortController())
    const completions: Array<() => void> = []

    let inflight = 0,
      maximum = 0

    const run = () =>
      new Promise<void>(resolve => {
        inflight++
        maximum = Math.max(maximum, inflight)
        completions.push(() => {
          inflight--
          resolve()
        })
      })

    const calls = controllers.map(controller => scheduler.run(run, controller.signal).catch(() => undefined))
    expect(inflight).toBe(3)
    controllers[0].abort()
    controllers[3].abort()
    await calls[0]
    expect(inflight).toBe(3)
    completions[0]()
    await calls[3]
    await Promise.resolve()
    expect(completions).toHaveLength(4)

    for (const finish of completions.slice(1)) {
      finish()
    }

    await Promise.all(calls)
    expect(maximum).toBe(3)
    expect(inflight).toBe(0)
  })
})
