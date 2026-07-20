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

function collect(response: FakeResponse, isTimedOut = () => false) {
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
      url: 'https://hermes.example/api/plugins/workflow/catalog'
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
