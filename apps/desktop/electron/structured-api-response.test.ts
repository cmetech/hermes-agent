import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import test from 'node:test'

import { collectStructuredJsonResponse } from './structured-api-response'

class FakeResponse extends EventEmitter {
  headers: Record<string, string>
  statusCode: number

  constructor(statusCode: number, headers: Record<string, string> = { 'content-type': 'application/json' }) {
    super()
    this.headers = headers
    this.statusCode = statusCode
  }
}

function collect(
  response: FakeResponse,
  isTimedOut = () => false,
  url = 'https://hermes.example/api/plugins/workflow/catalog'
) {
  const rejected: unknown[] = []
  const resolved: unknown[] = []
  let settled = 0

  collectStructuredJsonResponse(
    response,
    {
      isTimedOut,
      onSettled: () => {
        settled += 1
      },
      url
    },
    value => resolved.push(value),
    error => rejected.push(error)
  )

  return { rejected, resolved, settled: () => settled }
}

test('collects a synthetic JSON success for both structured adapters', () => {
  const response = new FakeResponse(200)
  const result = collect(response)

  response.emit('data', Buffer.from('{"workflows":'))
  response.emit('data', Buffer.from('[]}'))
  response.emit('end')

  assert.deepEqual(result.resolved, [{ ok: true, value: { workflows: [] } }])
  assert.deepEqual(result.rejected, [])
  assert.equal(result.settled(), 1)
})

test('preserves a JSON HTTP failure as structured status and body', () => {
  const response = new FakeResponse(409)
  const result = collect(response)

  response.emit('data', Buffer.from('{"detail":{"code":"stale"}}'))
  response.emit('end')

  assert.deepEqual(result.resolved, [{ body: { detail: { code: 'stale' } }, ok: false, status: 409 }])
  assert.deepEqual(result.rejected, [])
  assert.equal(result.settled(), 1)
})

test('rejects non-JSON success and failure bodies instead of disguising transport failures', () => {
  for (const statusCode of [200, 503]) {
    const response = new FakeResponse(statusCode, { 'content-type': 'text/plain' })
    const result = collect(response)

    response.emit('data', Buffer.from('upstream disconnected'))
    response.emit('end')

    assert.equal(result.resolved.length, 0)
    assert.equal(result.rejected.length, 1)
    assert.match(String(result.rejected[0]), /Invalid JSON/)
    assert.equal(result.settled(), 1)
  }
})

test('rejects a response-stream error without converting it to an HTTP result', () => {
  const response = new FakeResponse(200)
  const result = collect(response)
  const disconnect = new Error('socket disconnected mid-body')

  response.emit('data', Buffer.from('{"partial":'))
  response.emit('error', disconnect)

  assert.deepEqual(result.resolved, [])
  assert.deepEqual(result.rejected, [disconnect])
  assert.equal(result.settled(), 1)
})

test('ignores late response events after the owning adapter aborts on timeout', () => {
  const response = new FakeResponse(200)
  const result = collect(response, () => true)

  response.emit('data', Buffer.from('{"late":true}'))
  response.emit('end')
  response.emit('error', new Error('abort emitted after timeout rejection'))

  assert.deepEqual(result.resolved, [])
  assert.deepEqual(result.rejected, [])
  assert.equal(result.settled(), 0)
})

const lifecycleUrl = 'https://hermes.example/api/plugins/workflow/marketplace/lifecycle/v2/operations'
const lifecycleCap = 16 * 1024 * 1024 + 64 * 1024

test('lifecycle rejects nested and escaped-equivalent duplicate keys before parsing loses evidence', () => {
  // Break caught: JSON.parse silently choosing a later correlated identity.
  for (const body of ['{"id":"a","id":"b"}', '{"nested":{"id":"a","\\u0069d":"b"}}']) {
    const response = new FakeResponse(200)
    const result = collect(response, () => false, lifecycleUrl)
    response.emit('data', Buffer.from(body))
    response.emit('end')
    assert.equal(result.resolved.length, 0)
    assert.equal(result.rejected.length, 1)
  }
})

test('lifecycle accepts the byte boundary and rejects boundary plus one during chunk collection', () => {
  // Break caught: unbounded buffering or an off-by-one that rejects legal bytes.
  for (const extra of [0, 1]) {
    const response = new FakeResponse(200)
    const result = collect(response, () => false, lifecycleUrl)
    const bytes = Buffer.from('"' + 'a'.repeat(lifecycleCap - 2 + extra) + '"')
    response.emit('data', bytes.subarray(0, lifecycleCap - 1))
    response.emit('data', bytes.subarray(lifecycleCap - 1))

    if (extra) {
      assert.equal(result.rejected.length, 1, 'overflow rejects before end')
    }

    response.emit('end')
    assert.equal(result.resolved.length, extra ? 0 : 1)
    assert.equal(result.rejected.length, extra ? 1 : 0)
    assert.equal(result.settled(), 1)
    response.emit('data', Buffer.from('ignored'))
    response.emit('end')
    assert.equal(result.settled(), 1)
  }
})

test('lifecycle errors never contain URL, token body fragments or stream error material', () => {
  const secret = 'ephemeral-review-token-for-endpoint-test'

  for (const event of ['end', 'error']) {
    const response = new FakeResponse(200)
    const result = collect(response, () => false, lifecycleUrl + '/review-token')
    response.emit('data', Buffer.from('{"confirmation_token":"' + secret + '"'))
    response.emit(event, new Error(secret))
    assert.equal(result.rejected.length, 1)
    assert.equal(String(result.rejected[0]), 'Error: Invalid workflow lifecycle response.')
    assert.ok(!JSON.stringify(result.rejected).includes(secret))
  }
})

test('legacy and lookalike paths retain their duplicate-key and size semantics', () => {
  for (const path of [
    '/api/plugins/workflow/marketplace/operations',
    '/api/plugins/workflow/marketplace/lifecycle/v20/operations',
    '/api/plugins/workflow/catalog'
  ]) {
    const response = new FakeResponse(200)
    const result = collect(response, () => false, 'https://hermes.example' + path)
    response.emit('data', Buffer.from('{"id":"first","id":"last","padding":"' + 'a'.repeat(lifecycleCap) + '"}'))
    response.emit('end')
    assert.equal(result.rejected.length, 0)
    assert.equal(result.resolved.length, 1)
    assert.equal((result.resolved[0] as { value: { id: string } }).value.id, 'last')
  }
})
