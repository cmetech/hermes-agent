import { EventEmitter } from 'node:events'

// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import corpus from '../../../../tests/fixtures/workflow-marketplace-lifecycle-v2.json'
import { collectStructuredJsonResponse } from '../../electron/structured-api-response'

const api = await import('./workflow-marketplace-lifecycle').catch(() => null)

const scope = {
  connectionId: 'remote-a',
  connectionGeneration: 'generation-a',
  principal: 'alice',
  profile: 'support',
  registryEpoch: corpus.capabilities.registry_epoch
}

const received = { wallNowMs: 1000, monotonicNowMs: 50 }

const expected = {
  requestId: corpus.validOperationA.request_id,
  kind: corpus.validOperationA.kind,
  subject: corpus.validOperationA.subject,
  selection: null
}

describe('exact scoped V2 lifecycle helpers', () => {
  const transport = vi.fn()

  beforeEach(() => {
    transport.mockReset().mockResolvedValue({ ok: true, value: corpus.validOperationA })
    Object.defineProperty(window, 'hermesDesktop', { configurable: true, value: { apiStructured: transport } })
  })
  afterEach(() => {
    Reflect.deleteProperty(window, 'hermesDesktop')
    vi.restoreAllMocks()
  })

  it('rejects wall-clock adjustment instead of minting a future admission', () => {
    const observation = api!.observeLifecycleClock(corpus.capabilities, scope, received)
    expect(() =>
      api!.createLifecycleRequestId(observation, { wallNowMs: 32000, monotonicNowMs: 1050 }, scope)
    ).toThrowError(expect.objectContaining({ code: 'marketplace_clock_revalidation_required' }))
  })

  it.each([
    [-1, 0],
    [0, -1],
    [NaN, 0],
    [0, Infinity],
    [300001, 300000],
    [300000, 300001],
    [2000, 999],
    [999, 2000]
  ])('invalidates observations for elapsed wall=%s monotonic=%s and refuses reuse', (wall, monotonic) => {
    const observation = api!.observeLifecycleClock(corpus.capabilities, scope, received)
    expect(() =>
      api!.createLifecycleRequestId(
        observation,
        { wallNowMs: received.wallNowMs + wall, monotonicNowMs: received.monotonicNowMs + monotonic },
        scope
      )
    ).toThrowError(expect.objectContaining({ code: 'marketplace_clock_revalidation_required' }))
    expect(() => api!.createLifecycleRequestId(observation, received, scope)).toThrowError(
      expect.objectContaining({ code: 'marketplace_clock_revalidation_required' })
    )
  })

  it.each(['connectionId', 'connectionGeneration', 'profile', 'principal', 'registryEpoch'] as const)(
    'refuses exact clock scope change in %s',
    field => {
      const observation = api!.observeLifecycleClock(corpus.capabilities, scope, received)
      expect(() => api!.createLifecycleRequestId(observation, received, { ...scope, [field]: 'other' })).toThrowError(
        expect.objectContaining({ code: 'marketplace_clock_revalidation_required' })
      )
      expect(() => api!.createLifecycleRequestId(observation, received, scope)).toThrow()
    }
  )

  it('rejects copied or deserialized observations across application restart', () => {
    const observation = api!.observeLifecycleClock(corpus.capabilities, scope, received)

    for (const copy of [{ ...observation }, JSON.parse(JSON.stringify(observation))]) {
      expect(() => api!.createLifecycleRequestId(copy, received, scope)).toThrowError(
        expect.objectContaining({ code: 'marketplace_clock_revalidation_required' })
      )
    }
  })

  it('refuses admission before POST when its observation requires reprobe', async () => {
    const observation = api!.observeLifecycleClock(corpus.capabilities, scope, received)
    const intent = { ...expected, body: {} }
    await expect(
      api!.startLifecycleOperation(intent, scope, observation, { wallNowMs: 32000, monotonicNowMs: 1050 })
    ).rejects.toMatchObject({ code: 'marketplace_clock_revalidation_required' })
    expect(transport.mock.calls).toEqual([])
  })
  it('discards the observation on disconnect and on cryptographic failure', () => {
    const disconnected = api!.observeLifecycleClock(corpus.capabilities, scope, received)
    api!.discardLifecycleClockObservation(disconnected)
    expect(() => api!.createLifecycleRequestId(disconnected, received, scope)).toThrowError(
      expect.objectContaining({ code: 'marketplace_clock_revalidation_required' })
    )
    const observation = api!.observeLifecycleClock(corpus.capabilities, scope, received)
    vi.spyOn(crypto, 'getRandomValues').mockImplementation(() => {
      throw new Error('private-crypto-failure')
    })
    expect(() => api!.createLifecycleRequestId(observation, received, scope)).toThrowError(
      expect.objectContaining({ code: 'marketplace_clock_revalidation_required' })
    )
    vi.restoreAllMocks()
    expect(() => api!.createLifecycleRequestId(observation, received, scope)).toThrow()
  })

  it.each([
    [300000, 300000],
    [1000, 0],
    [0, 1000]
  ])('accepts exact clock boundaries wall=%s monotonic=%s', (wall, monotonic) => {
    const observation = api!.observeLifecycleClock(corpus.capabilities, scope, received)

    const id = api!.createLifecycleRequestId(
      observation,
      { wallNowMs: received.wallNowMs + wall, monotonicNowMs: received.monotonicNowMs + monotonic },
      scope
    )

    expect(id.split('_')[2]).toBe(String(1788609600000 + monotonic))
  })

  it.each(corpus.httpErrorCodes)('retains only approved backend code %s with a bounded HTTP status', async code => {
    for (const status of [400, 401, 403, 404, 409, 410, 422, 429, 500, 503]) {
      transport.mockResolvedValue({ ok: false, status, body: { detail: { code } } })
      await expect(api!.getLifecycleOperation(corpus.operationAId, scope)).rejects.toMatchObject({ code, status })
    }
  })

  it('normalizes malformed raw HTTP errors without retaining secret material', async () => {
    const secret = 'ephemeral_secret_error_only'

    for (const body of [
      JSON.stringify({ detail: { code: secret } }),
      JSON.stringify({ detail: { code: { nested: secret } } }),
      JSON.stringify({ detail: { code: 'marketplace_operation_not_found', extra: secret } }),
      JSON.stringify({ detail: { code: 'marketplace_network_error' } }),
      '{"detail":{"code":"marketplace_operation_not_found","code":"' + secret + '"}}',
      '<html>' + secret + '</html>',
      secret + ' '.repeat(16 * 1024 * 1024 + 64 * 1024)
    ]) {
      transport.mockImplementation(
        () =>
          new Promise((resolve, reject) => {
            const stream = Object.assign(new EventEmitter(), {
              statusCode: 409,
              headers: { 'content-type': 'application/json' }
            })

            collectStructuredJsonResponse(
              stream,
              { url: 'https://backend.example/api/plugins/workflow/marketplace/lifecycle/v2/operations' },
              resolve,
              reject
            )
            stream.emit('data', Buffer.from(body))
            stream.emit('end')
          })
      )
      const error = await api!.getLifecycleOperation(corpus.operationAId, scope).catch(value => value)
      expect(error).toMatchObject({ code: 'marketplace_request_failed' })
      expect(JSON.stringify(error)).not.toContain(secret)
      expect(String(error)).not.toContain(secret)
      expect(Object.hasOwn(error, 'cause')).toBe(false)
    }
  })

  it.each(['marketplace_unknown_backend_value', 'ephemeral_token_shaped_backend_value'])(
    'never retains unapproved backend code %s',
    async secret => {
      transport.mockResolvedValue({ ok: false, status: 409, body: { detail: { code: secret } } })
      const error = await api!.getLifecycleOperation(corpus.operationAId, scope).catch(value => value)
      expect(JSON.stringify(error)).not.toContain(secret)
      expect(error).toMatchObject({ code: 'marketplace_request_failed', status: 409 })
    }
  )

  // Break caught: a valid neighbor response being adopted as the requested watch.
  it.each(['getLifecycleOperation', 'cancelLifecycleOperation'] as const)(
    '%s rejects another operation',
    async method => {
      expect(api?.[method]).toBeTypeOf('function')
      transport.mockResolvedValue({ ok: true, value: corpus.validOperationB })
      await expect(api![method](corpus.operationAId, scope, expected)).rejects.toThrow()
    }
  )

  it.each([
    ['profile', { ...corpus.validOperationA, profile: 'other' }],
    ['request', { ...corpus.validOperationA, request_id: corpus.validOperationB.request_id }],
    ['subject', { ...corpus.validOperationA, subject: { type: 'source', source_name: 'other' } }]
  ])('rejects mismatched %s correlation', async (_name, value) => {
    expect(api?.getLifecycleOperation).toBeTypeOf('function')
    transport.mockResolvedValue({ ok: true, value })
    await expect(api!.getLifecycleOperation(corpus.operationAId, scope, expected)).rejects.toThrow()
  })

  it('routes the exact operation through the captured scope', async () => {
    expect(api?.getLifecycleOperation).toBeTypeOf('function')
    expect(await api!.getLifecycleOperation(corpus.operationAId, scope, expected)).toEqual(corpus.validOperationA)
    expect(transport.mock.calls[0][0]).toEqual({
      connectionId: 'remote-a',
      profile: 'support',
      path: `/api/plugins/workflow/marketplace/lifecycle/v2/operations/${corpus.operationAId}`
    })
  })

  it('distinguishes a missing capability route from authentication and network failure', async () => {
    expect(api?.getLifecycleCapabilities).toBeTypeOf('function')
    transport.mockResolvedValue({ ok: false, status: 404, body: { detail: 'Not Found' } })
    await expect(api!.getLifecycleCapabilities(scope)).rejects.toMatchObject({
      code: 'marketplace_lifecycle_unsupported',
      status: 404
    })
    transport.mockResolvedValue({ ok: false, status: 401, body: { detail: 'Unauthorized' } })
    await expect(api!.getLifecycleCapabilities(scope)).rejects.toMatchObject({ status: 401 })
    transport.mockRejectedValue(new Error('private transport details'))
    await expect(api!.getLifecycleCapabilities(scope)).rejects.toMatchObject({
      code: 'marketplace_network_error',
      status: 0
    })
  })

  it('generates server-adjusted request identities using cryptographic randomness', () => {
    expect(api?.createLifecycleRequestId).toBeTypeOf('function')
    const observation = api!.observeLifecycleClock(corpus.capabilities, scope, received)
    const now = { wallNowMs: 1500, monotonicNowMs: 550.75 }
    const first = api!.createLifecycleRequestId(observation, now, scope)
    const second = api!.createLifecycleRequestId(observation, now, scope)
    expect(first).toMatch(/^wmreq_e{32}_1788609600500_[0-9a-f]{32}$/)
    expect(first).not.toBe(second)
    expect(first).toHaveLength(85)
  })

  it('floors only after adding monotonic elapsed to sub-millisecond server time', () => {
    const observation = api!.observeLifecycleClock(
      { ...corpus.capabilities, server_time: '2026-09-05T12:00:00.000999Z' },
      scope,
      received
    )

    const id = api!.createLifecycleRequestId(observation, { wallNowMs: 1000.5, monotonicNowMs: 50.5 }, scope)
    expect(id.split('_')[2]).toBe('1788609600001')
  })

  it.each(['getLifecycleOperation', 'cancelLifecycleOperation'] as const)(
    '%s preserves an authoritative U+FEFF repository projection',
    async method => {
      const value = corpus.operationCases.find(
        item => item.name === 'repository FEFF https://fixtures.example/public.git'
      )!.value

      transport.mockResolvedValue({ ok: true, value })
      expect(await api![method](value.id, scope)).toEqual(value)
    }
  )

  it.each(
    corpus.operationCases.filter(
      testCase =>
        testCase.accepted &&
        testCase.name.startsWith('service ') &&
        !testCase.name.match(
          /pending|running|cancelled|wrong|boolean|unknown|noncanonical|reversed|failed committed/
        ) &&
        testCase.value.state === 'succeeded' &&
        !testCase.value.kind.endsWith('_confirm')
    )
  )('starts $name using the accepted route and envelope', async testCase => {
    expect(api?.startLifecycleOperation).toBeTypeOf('function')
    const value = testCase.value

    const bodies: Record<string, unknown> = {
      refresh: {},
      inspect: {},
      update_check: {
        identity: value.subject.type === 'all_packages' ? null : { source_key: 'company', package_id: 'laptop-support' }
      },
      install_prepare:
        value.subject.type === 'direct_install'
          ? { identifier: 'https://fixtures.example/workflows.git', ref: null, package_path: 'packages/laptop-support' }
          : { identifier: 'company/laptop-support', ref: null, package_path: null },
      update_prepare: { identity: { source_key: 'company', package_id: 'laptop-support' } },
      remove_prepare: { identity: { source_key: 'company', package_id: 'laptop-support' } },
      trust_prepare: {
        identity: { source_key: 'company', package_id: 'laptop-support' },
        workflow_name: value.selection?.type === 'one' ? value.selection.workflow_name : null
      },
      trust_revoke: {
        identity: { source_key: 'company', package_id: 'laptop-support' },
        workflow_name: value.selection?.type === 'one' ? value.selection.workflow_name : null
      }
    }

    const paths: Record<string, string> = {
      refresh: '/sources/company/refresh',
      inspect: '/packages/company/laptop-support',
      update_check: '/updates/check',
      install_prepare: '/install/prepare',
      update_prepare: '/update/prepare',
      remove_prepare: '/remove/prepare',
      trust_prepare: '/trust/review',
      trust_revoke: '/trust/revoke'
    }

    transport.mockResolvedValue({ ok: true, value })

    const intent = {
      requestId: value.request_id,
      kind: value.kind,
      subject: value.subject,
      selection: value.selection,
      body: bodies[value.kind]
    }

    expect(
      await api!.startLifecycleOperation(
        intent,
        scope,
        api!.observeLifecycleClock(corpus.capabilities, scope, received),
        received
      )
    ).toEqual(value)
    expect(transport.mock.calls[0][0]).toEqual({
      connectionId: 'remote-a',
      profile: 'support',
      method: 'POST',
      timeoutMs: 15000,
      path: `/api/plugins/workflow/marketplace/lifecycle/v2${paths[value.kind]}`,
      body: { request_id: value.request_id, body: bodies[value.kind] }
    })
  })

  it('rejects wrong epoch and mutable captured scope after dispatch', async () => {
    expect(api?.getLifecycleOperation).toBeTypeOf('function')
    await expect(
      api!.getLifecycleOperation(corpus.operationAId, { ...scope, registryEpoch: 'd'.repeat(32) }, expected)
    ).rejects.toThrow()
    let reply: (value: unknown) => void = () => undefined
    transport.mockReturnValue(
      new Promise(resolve => {
        reply = resolve
      })
    )
    const captured = { ...scope }
    const pending = api!.getLifecycleOperation(corpus.operationAId, captured, expected)
    captured.profile = 'other'
    reply({ ok: true, value: { ...corpus.validOperationA, profile: 'other' } })
    await expect(pending).rejects.toThrow()
  })

  it('validates admission replay and exact requested state identity', async () => {
    expect(api?.lookupLifecycleAdmission).toBeTypeOf('function')
    transport.mockResolvedValue({ ok: true, value: { state: 'found', operation: corpus.validOperationB } })
    await expect(api!.lookupLifecycleAdmission(corpus.validOperationA.request_id, scope, expected)).rejects.toThrow()
    const state = corpus.packageStateCases.find(testCase => testCase.name === 'service installed A trusted')!.value
    transport.mockResolvedValue({ ok: true, value: state })
    expect(await api!.getLifecyclePackageState(state.identity, scope)).toEqual(state)
    await expect(api!.getLifecyclePackageState({ ...state.identity, package_id: 'other' }, scope)).rejects.toThrow()
  })

  it('consumes actual registry replay, eviction and mixed V1/V2 snapshot envelopes', async () => {
    transport.mockResolvedValue({ ok: true, value: corpus.admissionFound })
    expect(await api!.lookupLifecycleAdmission(corpus.admissionFound.operation.request_id!, scope)).toEqual(
      corpus.admissionFound
    )
    transport.mockResolvedValue({ ok: true, value: corpus.admissionEvicted })
    expect(await api!.lookupLifecycleAdmission(corpus.admissionEvicted.request_id, scope)).toEqual(
      corpus.admissionEvicted
    )
    await expect(api!.lookupLifecycleAdmission(corpus.validOperationB.request_id, scope)).rejects.toThrow()
    transport.mockResolvedValue({ ok: true, value: corpus.operationPage })
    expect(await api!.listLifecycleOperations(scope, { limit: 1 })).toEqual(corpus.operationPage)
    transport.mockResolvedValue({ ok: true, value: corpus.operationPageFinal })
    expect(await api!.listLifecycleOperations(scope, { cursor: corpus.operationPage.next_cursor!, limit: 1 })).toEqual(
      corpus.operationPageFinal
    )
  })

  it('retrieves a token only for its exact operation, request, subject, selection, digest and expiry', async () => {
    expect(api?.getLifecycleReviewToken).toBeTypeOf('function')
    const prepared = corpus.operationCases.find(testCase => testCase.name === 'service review one A')!.value
    const review = prepared.result!.value as { review_digest: string; expires_at: string }
    // Fixed token lives exclusively in this explicit endpoint test, never corpus.
    const secret = 'ephemeral-review-token-for-endpoint-test'

    const response = {
      operation_id: prepared.id,
      request_id: prepared.request_id,
      subject: prepared.subject,
      selection: prepared.selection,
      review_digest: review.review_digest,
      confirmation_token: secret,
      expires_at: review.expires_at
    }

    transport.mockResolvedValue({ ok: true, value: response })
    expect(await api!.getLifecycleReviewToken(prepared, scope)).toEqual(response)

    for (const [key, replacement] of Object.entries({
      operation_id: corpus.operationAId,
      request_id: corpus.validOperationA.request_id,
      subject: corpus.validOperationA.subject,
      selection: { type: 'one', workflow_name: 'B' },
      review_digest: 'a'.repeat(64),
      expires_at: '2026-09-05T13:00:00Z',
      confirmation_token: 'x'.repeat(31)
    })) {
      transport.mockResolvedValue({ ok: true, value: { ...response, [key]: replacement } })
      await expect(api!.getLifecycleReviewToken(prepared, scope)).rejects.toThrow()
    }

    transport.mockRejectedValue(new Error(secret))

    try {
      await api!.getLifecycleReviewToken(prepared, scope)
    } catch (error) {
      expect(String(error)).not.toContain(secret)
      expect(JSON.stringify(error)).not.toContain(secret)
    }
  })

  it.each(corpus.tokenEndpointCases)('$name follows Python only inside the explicit token endpoint', async testCase => {
    const prepared = corpus.operationCases.find(item => item.name === 'service review one A')!.value
    transport.mockResolvedValue({
      ok: true,
      value: { ...testCase.value, confirmation_token: testCase.character.repeat(testCase.length) }
    })

    if (testCase.accepted) {
      expect(await api!.getLifecycleReviewToken(prepared, scope)).toEqual({
        ...testCase.value,
        confirmation_token: testCase.character.repeat(testCase.length)
      })
    } else {
      await expect(api!.getLifecycleReviewToken(prepared, scope)).rejects.toMatchObject({
        code: 'marketplace_invalid_response'
      })
    }
  })

  it.each(['service install confirm', 'service update confirm', 'service remove confirm', 'service grant one A'])(
    'confirms %s with exact preparation metadata beside the ephemeral token',
    async name => {
      const value = corpus.operationCases.find(testCase => testCase.name === name)!.value

      const prepareName: Record<string, string> = {
        install_confirm: 'service install prepare',
        update_confirm: 'service update prepare',
        remove_confirm: 'service remove prepare',
        trust_confirm: 'service review one A'
      }

      const prepare = corpus.operationCases.find(testCase => testCase.name === prepareName[value.kind])!.value

      const body = {
        confirmation_token: 'ephemeral-confirm-token-for-endpoint-test',
        prepare_operation_id: prepare.id,
        subject: value.subject,
        selection: value.selection,
        review_digest: (prepare.result!.value as { review_digest: string }).review_digest
      }

      transport.mockResolvedValue({ ok: true, value })
      expect(
        await api!.startLifecycleOperation(
          { requestId: value.request_id, kind: value.kind, subject: value.subject, selection: value.selection, body },
          scope,
          api!.observeLifecycleClock(corpus.capabilities, scope, received),
          received
        )
      ).toEqual(value)
      expect(transport.mock.calls[0][0].body).toEqual({ request_id: value.request_id, body })
      expect(transport.mock.calls[0][0].path).toBe(
        '/api/plugins/workflow/marketplace/lifecycle/v2/' +
          (
            {
              install_confirm: 'install/confirm',
              update_confirm: 'update/confirm',
              remove_confirm: 'remove/confirm',
              trust_confirm: 'trust/grant'
            } as Record<string, string>
          )[value.kind]
      )
    }
  )

  it('rejects wrong selected workflow responses even when the backend operation is otherwise valid', async () => {
    const prepared = corpus.operationCases.find(testCase => testCase.name === 'service review one A')!.value
    transport.mockResolvedValue({ ok: true, value: prepared })
    await expect(
      api!.getLifecycleOperation(prepared.id, scope, {
        requestId: prepared.request_id,
        kind: prepared.kind,
        subject: prepared.subject,
        selection: { type: 'one', workflow_name: 'B' }
      })
    ).rejects.toThrow()
  })

  it('validates complete snapshot scope and outgoing pagination', async () => {
    expect(api?.listLifecycleOperations).toBeTypeOf('function')
    transport.mockResolvedValue({
      ok: true,
      value: { items: [corpus.validOperationA], complete: true, next_cursor: null }
    })
    expect((await api!.listLifecycleOperations(scope, { cursor: 'a'.repeat(32), limit: 10 })).items).toEqual([
      corpus.validOperationA
    ])
    expect(transport.mock.calls[0][0].path).toBe(
      `/api/plugins/workflow/marketplace/lifecycle/v2/operations?cursor=${'a'.repeat(32)}&limit=10`
    )
    await expect(api!.listLifecycleOperations({ ...scope, profile: 'other' })).rejects.toThrow()
  })

  it('rejects newline-suffixed request identities before issuing a transport request', async () => {
    transport.mockResolvedValue({ ok: true, value: corpus.validOperationA })
    await expect(api!.getLifecycleOperation(corpus.operationAId + '\n', scope)).rejects.toThrow(
      'Invalid workflow lifecycle request'
    )
    await expect(api!.lookupLifecycleAdmission(corpus.validOperationA.request_id + '\n', scope)).rejects.toThrow(
      'Invalid workflow lifecycle request'
    )
  })

  it('preserves a Python-valid U+FEFF profile identity in the exact captured scope', async () => {
    const profile = '\ufeffsupport'
    transport.mockResolvedValue({ ok: true, value: { ...corpus.validOperationA, profile } })
    expect((await api!.getLifecycleOperation(corpus.operationAId, { ...scope, profile }, expected)).profile).toBe(
      profile
    )
  })

  it('feeds actual collected JSON bytes to the real decoder/helper and rejects duplicate-key substitutions', async () => {
    const reply = (body: string) =>
      transport.mockImplementation(
        () =>
          new Promise((resolve, reject) => {
            const response = Object.assign(new EventEmitter(), {
              statusCode: 200,
              headers: { 'content-type': 'application/json' }
            })

            collectStructuredJsonResponse(
              response,
              {
                url:
                  'https://backend.example/api/plugins/workflow/marketplace/lifecycle/v2/operations/' +
                  corpus.operationAId
              },
              resolve,
              reject
            )
            response.emit('data', Buffer.from(body))
            response.emit('end')
          })
      )

    reply(JSON.stringify(corpus.validOperationA))
    expect(await api!.getLifecycleOperation(corpus.operationAId, scope, expected)).toEqual(corpus.validOperationA)
    reply('{"\\u0069d":"neighbor",' + JSON.stringify(corpus.validOperationA).slice(1))
    await expect(api!.getLifecycleOperation(corpus.operationAId, scope, expected)).rejects.toThrow()
    reply(' '.repeat(16 * 1024 * 1024 + 64 * 1024) + JSON.stringify(corpus.validOperationA))
    await expect(api!.getLifecycleOperation(corpus.operationAId, scope, expected)).rejects.toThrow()
  })
})
