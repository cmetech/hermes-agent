/**
 * Unit + live-transport tests for the Electron main process's Hermes REST
 * retry policy (#92976 / PR #92977 salvage).
 *
 * The live tests run REAL node http servers that misbehave the way the
 * reported backend does (closing sockets under burst keep-alive traffic) and
 * prove two things end to end:
 *
 *  - idempotent GETs that die with ECONNRESET are retried and succeed, where
 *    a single bare attempt (pre-PR behavior) surfaces the raw reset;
 *  - a POST whose socket is reset AFTER the server processed it is NOT
 *    retried: the server-side hit counter stays at 1 and the error surfaces.
 */
import http from 'node:http'
import type { AddressInfo } from 'node:net'

import { afterAll, describe, expect, it } from 'vitest'

import {
  destroyKeepaliveAgents,
  downloadAgentFor,
  isIdempotentMethod,
  isTransientTransportError,
  jsonAgentFor,
  shouldRetryRequest,
  withRetry
} from './api-transport'
import { createConnectionGenerationRegistry } from './connection-generation'

const lifecycleTransport = await import('./lifecycle-api-transport').catch(() => null)
const lifecycleRoot = '/api/plugins/workflow/marketplace/lifecycle/v2'
const principalHeader = 'X-Hermes-Marketplace-Principal-Binding'
const principal = 'a'.repeat(64)

function pendingValue<T>() {
  let resolve!: (value: T) => void

  const promise = new Promise<T>(done => {
    resolve = done
  })

  return { promise, resolve }
}

describe('native lifecycle IPC authority boundary', () => {
  // Break caught: low-level config merging reintroduces a differently-cased actor header after descriptor sanitation.
  it('strips implicit/config collisions before reinjecting the native singleton at the final transport merge', () => {
    expect(lifecycleTransport!.mergeNativeRequestHeaders).toBeTypeOf('function')

    const headers = lifecycleTransport!.mergeNativeRequestHeaders(
      { [principalHeader]: 'bad', 'x-hermes-marketplace-principal-binding': 'bad' },
      {
        headers: { 'x-HeRmEs-MaRkEtPlAcE-PrInCiPaL-BiNdInG': 'bad', [principalHeader]: principal, 'X-Fixture': 'kept' },
        nativeLifecycle: true
      }
    )

    expect(headers).toEqual({ 'X-Fixture': 'kept', [principalHeader]: principal })
    expect(lifecycleTransport!.mergeNativeRequestHeaders({ [principalHeader]: 'bad' }, {})).toEqual({})
  })

  for (const structured of [false, true]) {
    // Break caught: WHATWG normalization routes a raw non-V2 alias into V2 without native generation.
    it.each([
      '/api/plugins/workflow/marketplace/lifecycle/v\t2/capabilities',
      '/api/plugins/workflow/marketplace/lifecycle/v\n2/capabilities',
      '/api/plugins/workflow/marketplace/lifecycle/v\r2/capabilities',
      `\u0000${lifecycleRoot}/capabilities`,
      `${lifecycleRoot}/capabilities\u007f`,
      `${lifecycleRoot}/.\t./operations`,
      `${lifecycleRoot}/../operations`,
      `${lifecycleRoot}/%2e%2e/operations`,
      `${lifecycleRoot}/%2E./operations`,
      `${lifecycleRoot}\\..\\operations`,
      `${lifecycleRoot}/operations#fragment`,
      `//other.example${lifecycleRoot}/capabilities`,
      `https://other.example${lifecycleRoot}/capabilities`
    ])(`rejects a noncanonical IPC target before routing (structured=${structured}): %j`, async path => {
      let resolved = 0
      const authority = createConnectionGenerationRegistry()
      authority.begin('primary')
      const input = { path }
      // Both main IPC handlers use this classifier before their legacy/V2 split.
      expect(() => lifecycleTransport!.validateLifecycleRequest(input)).toThrow()
      await expect(
        lifecycleTransport!.dispatchLifecycleRequest(
          { ...input, expectedConnectionGeneration: 1, expectedMarketplacePrincipalBinding: principal },
          structured,
          {
            authority,
            resolve: async () => {
              resolved++
              throw new Error('must not route')
            },
            accessToken: async () => {
              throw new Error('must not authenticate')
            },
            fetchToken: async () => {
              throw new Error('must not fetch')
            },
            fetchCookie: async () => {
              throw new Error('must not fetch')
            }
          }
        )
      ).rejects.toThrow()
      expect(resolved).toBe(0)
    })

    // Break caught: native route resolution changes the final path/base before the principal header is injected.
    it.each([
      { baseUrl: 'https://fixture.example', path: `${lifecycleRoot}/.\t./operations` },
      { baseUrl: 'https://fixture.example', path: '/api/status' },
      { baseUrl: 'https://fixture.example/prefix', path: `${lifecycleRoot}/operations` },
      { baseUrl: 'https://fixture.example#fragment', path: `${lifecycleRoot}/operations` }
    ])(`refuses a resolved destination outside exact V2 (structured=${structured}): %j`, async target => {
      const authority = createConnectionGenerationRegistry()
      const descriptor = authority.publish(authority.begin('primary'), { baseUrl: target.baseUrl, authMode: 'oauth' })
      authority.associate('route', 1)
      const effects: string[] = []
      await expect(
        lifecycleTransport!.dispatchLifecycleRequest(
          {
            path: `${lifecycleRoot}/operations`,
            expectedConnectionGeneration: 1,
            expectedMarketplacePrincipalBinding: principal
          },
          structured,
          {
            authority,
            resolve: async () => ({ descriptor, path: target.path, routeKey: 'route' }),
            accessToken: async () => {
              effects.push('auth')

              return null
            },
            fetchToken: async () => {
              effects.push('token')

              return { private: 'escaped' }
            },
            fetchCookie: async () => {
              effects.push('cookie')

              return { private: 'escaped' }
            }
          }
        )
      ).rejects.toThrow('Invalid workflow lifecycle request.')
      expect(effects).toEqual([])
    })
    // Break caught: a rejected old route resolution leaks its error after native authority is retired.
    it(`suppresses obsolete route-resolution errors (structured=${structured})`, async () => {
      const authority = createConnectionGenerationRegistry()
      authority.begin('primary')
      const gate = pendingValue<void>()

      const task = lifecycleTransport!.dispatchLifecycleRequest(
        { path: `${lifecycleRoot}/capabilities`, expectedConnectionGeneration: 1 },
        structured,
        {
          authority,
          resolve: async () => {
            await gate.promise
            throw new Error('private old endpoint error')
          },
          accessToken: async () => null,
          fetchToken: async () => {
            throw new Error('must not fetch')
          },
          fetchCookie: async () => {
            throw new Error('must not fetch')
          }
        }
      )

      authority.invalidate('primary')
      gate.resolve()
      await expect(task).rejects.toThrow('marketplace_connection_generation_changed')
    })
    // Break caught: either channel fetches under the wrong route or returns a stale secret-bearing response.
    it.each(['resolve', 'auth', 'fetch'])(
      `rejects generation race during %s (structured=${structured})`,
      async stage => {
        expect(lifecycleTransport).not.toBeNull()
        const authority = createConnectionGenerationRegistry()
        const claim = authority.begin('primary')

        const descriptor = authority.publish(claim, {
          baseUrl: 'https://fixture.example',
          authMode: 'oauth',
          token: null
        })

        authority.associate('route', 1)
        const gate = pendingValue<void>()
        const entered = pendingValue<void>()
        const received: unknown[] = []
        let dispatched = 0

        const pause = async (at: string) => {
          if (stage === at) {
            entered.resolve()
            await gate.promise
          }
        }

        const task = lifecycleTransport!
          .dispatchLifecycleRequest(
            {
              path: `${lifecycleRoot}/operations`,
              expectedConnectionGeneration: 1,
              expectedMarketplacePrincipalBinding: principal
            },
            structured,
            {
              authority,
              resolve: async () => {
                await pause('resolve')

                return { descriptor, path: `${lifecycleRoot}/operations`, routeKey: 'route' }
              },
              accessToken: async () => {
                await pause('auth')

                return 'fixture-bearer'
              },
              fetchToken: async () => {
                dispatched++
                await pause('fetch')

                return { secret: 'fixture-private-response' }
              },
              fetchCookie: async () => {
                throw new Error('wrong transport')
              }
            }
          )
          .then(value => received.push(value))

        await entered.promise
        authority.begin('primary')
        gate.resolve()
        await expect(task).rejects.toThrow('marketplace_connection_generation_changed')
        expect(dispatched).toBe(stage === 'fetch' ? 1 : 0)
        expect(received).toEqual([])
      }
    )

    // Break caught: numeric exhaustion can still resolve a route or dispatch a queued generation 1.
    it(`refuses exhausted requests before resolving (structured=${structured})`, async () => {
      expect(lifecycleTransport).not.toBeNull()
      const authority = createConnectionGenerationRegistry(undefined, Number.MAX_SAFE_INTEGER)
      expect(() => authority.begin('primary')).toThrow('marketplace_connection_generation_exhausted')
      let resolutions = 0
      await expect(
        lifecycleTransport!.dispatchLifecycleRequest(
          { path: `${lifecycleRoot}/capabilities`, expectedConnectionGeneration: 1 },
          structured,
          {
            authority,
            resolve: async () => {
              resolutions++
              throw new Error('must not resolve')
            },
            accessToken: async () => null,
            fetchToken: async () => {
              throw new Error('must not fetch')
            },
            fetchCookie: async () => {
              throw new Error('must not fetch')
            }
          }
        )
      ).rejects.toThrow('marketplace_connection_generation_exhausted')
      expect(resolutions).toBe(0)
    })
  }

  // Break caught: renderer routing metadata leaks onto V1 or malformed principals become native headers.
  it('validates the dedicated lifecycle-only fields without accepting arbitrary headers', () => {
    expect(lifecycleTransport).not.toBeNull()
    const validate = lifecycleTransport!.validateLifecycleRequest
    expect(validate({ path: '/api/plugins/workflow/marketplace/sources' })).toBe(false)

    for (const path of ['/api/status', `${lifecycleRoot}-other/capabilities`]) {
      for (const field of ['expectedConnectionGeneration', 'expectedMarketplacePrincipalBinding']) {
        expect(() => validate({ path, [field]: 1 })).toThrow()
      }
    }

    for (const generation of [undefined, null, '1', 0, -1, 1.5, Infinity, Number.MAX_SAFE_INTEGER + 1]) {
      expect(() =>
        validate({ path: `${lifecycleRoot}/capabilities`, expectedConnectionGeneration: generation })
      ).toThrow()
    }

    for (const binding of [
      undefined,
      null,
      '',
      'A'.repeat(64),
      'a'.repeat(63),
      'a'.repeat(65),
      `${principal}\n`,
      [principal]
    ]) {
      expect(() =>
        validate({
          path: `${lifecycleRoot}/operations`,
          expectedConnectionGeneration: 1,
          expectedMarketplacePrincipalBinding: binding
        })
      ).toThrow()
    }

    expect(() =>
      validate({
        path: `${lifecycleRoot}/operations`,
        expectedConnectionGeneration: 1,
        expectedMarketplacePrincipalBinding: principal,
        headers: {}
      })
    ).toThrow()
    expect(validate({ path: `${lifecycleRoot}/capabilities`, expectedConnectionGeneration: 1 })).toBe(true)
  })

  // Break caught: merge order preserves a descriptor collision or serializes expected generation into HTTP.
  it.each(['token', 'bearer', 'cookie'])(
    `sends one native-owned principal header through %s transport`,
    async transport => {
      expect(lifecycleTransport).not.toBeNull()
      const wireRequests: Array<{ headers: string[]; url: string; body: string }> = []

      const server = http.createServer((request, response) => {
        let body = ''
        request.on('data', chunk => {
          body += chunk
        })
        request.on('end', () => {
          wireRequests.push({ headers: request.rawHeaders, url: request.url!, body })
          response.end('{"accepted":true}')
        })
      })

      await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
      const authority = createConnectionGenerationRegistry()

      const descriptor = authority.publish(authority.begin('primary'), {
        baseUrl: `http://127.0.0.1:${(server.address() as AddressInfo).port}`,
        authMode: transport === 'token' ? 'token' : 'oauth',
        token: 'fixture-token',
        headers: {
          [principalHeader]: 'wrong',
          'x-hermes-marketplace-principal-binding': 'wrong-lower',
          'x-HeRmEs-MaRkEtPlAcE-PrInCiPaL-BiNdInG': 'wrong-mixed',
          'X-Fixture': 'kept'
        }
      })

      authority.associate('route', 1)

      const send = (url: string, headers: Record<string, string>, body: unknown) =>
        new Promise<unknown>((resolve, reject) => {
          const req = http.request(url, { method: 'POST', headers }, response => {
            let text = ''
            response.on('data', chunk => {
              text += chunk
            })
            response.on('end', () => resolve(JSON.parse(text)))
          })

          req.on('error', reject)
          req.end(JSON.stringify(body))
        })

      try {
        await lifecycleTransport!.dispatchLifecycleRequest(
          {
            path: `${lifecycleRoot}/install/prepare`,
            method: 'POST',
            body: { request_id: 'fixture' },
            expectedConnectionGeneration: 1,
            expectedMarketplacePrincipalBinding: principal
          },
          true,
          {
            authority,
            resolve: async () => ({ descriptor, path: `${lifecycleRoot}/install/prepare`, routeKey: 'route' }),
            accessToken: async () => (transport === 'bearer' ? 'fixture-bearer' : null),
            fetchToken: (url, token, options) =>
              send(
                url,
                {
                  ...options.headers,
                  ...(token ? { 'X-Hermes-Session-Token': token } : { Authorization: `Bearer ${options.bearer}` })
                },
                options.body
              ),
            fetchCookie: (url, options) => send(url, { ...options.headers, Cookie: 'fixture=session' }, options.body)
          }
        )
        expect(wireRequests).toHaveLength(1)
        const observed = wireRequests[0]!
        expect(
          observed.headers.filter(
            (name, index) => index % 2 === 0 && name.toLowerCase() === principalHeader.toLowerCase()
          )
        ).toEqual([principalHeader])
        expect(observed.headers[observed.headers.indexOf(principalHeader) + 1]).toBe(principal)
        expect(observed.url).toBe(`${lifecycleRoot}/install/prepare`)
        expect(observed.body).toBe('{"request_id":"fixture"}')
        expect(JSON.stringify(observed)).not.toContain('expectedConnectionGeneration')
        expect(JSON.stringify(observed)).not.toContain('wrong')
      } finally {
        await new Promise<void>(resolve => server.close(() => resolve()))
      }
    }
  )
})

function errWithCode(code: string, message = code): NodeJS.ErrnoException {
  const e: NodeJS.ErrnoException = new Error(message)
  e.code = code

  return e
}

afterAll(() => {
  destroyKeepaliveAgents()
})

describe('isTransientTransportError', () => {
  it('accepts transient socket-level codes and messages', () => {
    for (const code of ['ECONNRESET', 'ECONNREFUSED', 'EPIPE', 'ETIMEDOUT', 'ENOTFOUND', 'EAI_AGAIN']) {
      expect(isTransientTransportError(errWithCode(code))).toBe(true)
    }

    expect(isTransientTransportError(new Error('socket hang up'))).toBe(true)
    expect(isTransientTransportError(new Error('read ECONNRESET'))).toBe(true)
  })

  it('rejects non-transport errors', () => {
    expect(isTransientTransportError(new Error('404: not found'))).toBe(false)
    expect(isTransientTransportError(new Error('Invalid JSON from http://x'))).toBe(false)
    expect(isTransientTransportError(null)).toBe(false)
    expect(isTransientTransportError(undefined)).toBe(false)
  })
})

describe('isIdempotentMethod', () => {
  it.each([
    ['GET', true],
    ['get', true],
    ['HEAD', true],
    ['OPTIONS', true],
    ['POST', false],
    ['PUT', false],
    ['PATCH', false],
    ['DELETE', false],
    [undefined, true] // node http defaults omitted method to GET
  ])('%s -> %s', (method, expected) => {
    expect(isIdempotentMethod(method)).toBe(expected)
  })
})

describe('shouldRetryRequest truth table', () => {
  const reset = () => errWithCode('ECONNRESET', 'read ECONNRESET')
  const refused = () => errWithCode('ECONNREFUSED', 'connect ECONNREFUSED 127.0.0.1:1')
  const hangUp = () => new Error('socket hang up')

  it('GET: retries any transient error regardless of body state', () => {
    expect(shouldRetryRequest(reset(), 'GET', { bodySent: true })).toBe(true)
    expect(shouldRetryRequest(reset(), 'GET', { bodySent: false })).toBe(true)
    expect(shouldRetryRequest(hangUp(), 'HEAD', { bodySent: true })).toBe(true)
  })

  it('GET: never retries non-transport errors (HTTP 4xx/5xx surfaced as Error)', () => {
    expect(shouldRetryRequest(new Error('500: boom'), 'GET', { bodySent: true })).toBe(false)
  })

  it('POST: retries when the connection provably never happened', () => {
    expect(shouldRetryRequest(refused(), 'POST', { bodySent: false })).toBe(true)
    expect(shouldRetryRequest(refused(), 'POST', { bodySent: true })).toBe(true) // refused == nothing sent
    expect(shouldRetryRequest(errWithCode('ENOTFOUND'), 'PUT', { bodySent: false })).toBe(true)
  })

  it('POST: retries transient errors thrown before the body was flushed', () => {
    expect(shouldRetryRequest(reset(), 'POST', { bodySent: false })).toBe(true)
    expect(shouldRetryRequest(hangUp(), 'DELETE', { bodySent: false })).toBe(true)
  })

  it('POST: does NOT retry ambiguous resets after the body went out', () => {
    expect(shouldRetryRequest(reset(), 'POST', { bodySent: true })).toBe(false)
    expect(shouldRetryRequest(hangUp(), 'POST', { bodySent: true })).toBe(false)
    expect(shouldRetryRequest(errWithCode('EPIPE'), 'PUT', { bodySent: true })).toBe(false)
    expect(shouldRetryRequest(errWithCode('ETIMEDOUT'), 'DELETE', { bodySent: true })).toBe(false)
  })

  it('POST: conservative when request state is unknown', () => {
    // No bodySent flag at all — treat as "may have been sent", don't retry.
    expect(shouldRetryRequest(reset(), 'POST', {})).toBe(false)
    expect(shouldRetryRequest(reset(), 'POST')).toBe(false)
  })
})

describe('withRetry', () => {
  const noDelay = { delayFn: () => Promise.resolve() }

  it('retries a GET through transient failures and resolves', async () => {
    let attempts = 0

    const result = await withRetry(
      () => {
        attempts += 1

        if (attempts < 3) {
          return Promise.reject(errWithCode('ECONNRESET'))
        }

        return Promise.resolve('ok')
      },
      { method: 'GET', ...noDelay }
    )

    expect(result).toBe('ok')
    expect(attempts).toBe(3)
  })

  it('gives each attempt a fresh requestState', async () => {
    const seen: boolean[] = []
    let attempts = 0
    await withRetry(
      (state: any) => {
        seen.push(state.bodySent)
        state.bodySent = true
        attempts += 1

        if (attempts < 2) {
          return Promise.reject(errWithCode('ECONNREFUSED'))
        }

        return Promise.resolve(null)
      },
      { method: 'POST', ...noDelay }
    )
    expect(seen).toEqual([false, false])
  })

  it('does not retry a POST that failed after the body was flushed', async () => {
    let attempts = 0
    await expect(
      withRetry(
        (state: any) => {
          attempts += 1
          state.bodySent = true

          return Promise.reject(errWithCode('ECONNRESET', 'read ECONNRESET'))
        },
        { method: 'POST', ...noDelay }
      )
    ).rejects.toThrow('read ECONNRESET')
    expect(attempts).toBe(1)
  })

  it('retries a POST on ECONNREFUSED (never reached the server)', async () => {
    let attempts = 0
    await expect(
      withRetry(
        () => {
          attempts += 1

          return Promise.reject(errWithCode('ECONNREFUSED'))
        },
        { method: 'POST', maxRetries: 2, ...noDelay }
      )
    ).rejects.toThrow('ECONNREFUSED')
    expect(attempts).toBe(3)
  })

  it('bounds retries at maxRetries even for GET', async () => {
    let attempts = 0
    await expect(
      withRetry(
        () => {
          attempts += 1

          return Promise.reject(errWithCode('ECONNRESET'))
        },
        { method: 'GET', maxRetries: 2, ...noDelay }
      )
    ).rejects.toThrow()
    expect(attempts).toBe(3)
  })

  it('never retries non-transient errors', async () => {
    let attempts = 0
    await expect(
      withRetry(
        () => {
          attempts += 1

          return Promise.reject(new Error('500: internal'))
        },
        { method: 'GET', ...noDelay }
      )
    ).rejects.toThrow('500')
    expect(attempts).toBe(1)
  })
})

describe('keep-alive agent pools', () => {
  it('separates JSON and download pools per protocol', () => {
    expect(jsonAgentFor('http:')).not.toBe(jsonAgentFor('https:'))
    expect(jsonAgentFor('http:')).not.toBe(downloadAgentFor('http:'))
    expect(jsonAgentFor('https:')).not.toBe(downloadAgentFor('https:'))
    // Stable across calls (a real pool, not a factory).
    expect(jsonAgentFor('http:')).toBe(jsonAgentFor('http:'))
  })
})

// ---------------------------------------------------------------------------
// LIVE transport tests against real misbehaving HTTP servers.
// ---------------------------------------------------------------------------

/** Minimal single-attempt GET mirroring the pre-PR fetchJson (no retry). */
function bareJsonGet(url: string): Promise<any> {
  return new Promise((resolve, reject) => {
    const req = http.request(new URL(url), { agent: jsonAgentFor('http:'), method: 'GET' }, res => {
      const chunks: Buffer[] = []
      res.on('error', reject)
      res.on('data', c => chunks.push(c))
      res.on('end', () => resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))))
    })

    req.on('error', reject)
    req.end()
  })
}

/** The head behavior: same request under the verb-gated retry policy. */
function retriedJsonGet(url: string): Promise<any> {
  return withRetry(() => bareJsonGet(url), { method: 'GET', delayFn: () => Promise.resolve() })
}

function listen(server: http.Server): Promise<string> {
  return new Promise(resolve => {
    server.listen(0, '127.0.0.1', () => {
      resolve(`http://127.0.0.1:${(server.address() as AddressInfo).port}`)
    })
  })
}

describe('live: GET burst against a server that resets keep-alive sockets', () => {
  it('bare attempts fail with ECONNRESET/hang-up; retried GETs all succeed', async () => {
    // Deterministic misbehavior: every other request gets its socket
    // destroyed instead of a response — the observable client-side effect of
    // a backend killing idle keep-alive sockets mid-burst.
    let hits = 0

    const server = http.createServer((req, res) => {
      hits += 1

      if (hits % 2 === 1) {
        req.socket.destroy()

        return
      }

      res.setHeader('content-type', 'application/json')
      res.end(JSON.stringify({ n: hits }))
    })

    const base = await listen(server)

    try {
      // BASE (pre-PR, single attempt): the burst surfaces raw transport errors.
      let baseFailures = 0

      for (let i = 0; i < 6; i++) {
        try {
          await bareJsonGet(`${base}/api/sessions`)
        } catch (error: any) {
          baseFailures += 1
          expect(isTransientTransportError(error)).toBe(true)
        }
      }

      expect(baseFailures).toBeGreaterThan(0)

      // HEAD (retry policy): the same burst fully succeeds. Sequential so the
      // server's alternating destroy/respond pattern is deterministic per
      // request (first attempt reset, retry served).
      for (let i = 0; i < 6; i++) {
        const r = await retriedJsonGet(`${base}/api/sessions`)
        expect(r).toHaveProperty('n')
      }
    } finally {
      server.close()
    }
  }, 20_000)
})

describe('live: POST reset after server-side processing', () => {
  it('does not double-submit: server hit count stays 1, error surfaces', async () => {
    // The server fully receives and "processes" the POST (counter increments),
    // then RSTs the socket before responding — the dangerous ambiguous case.
    let posts = 0

    const server = http.createServer((req, res) => {
      const chunks: Buffer[] = []
      req.on('data', c => chunks.push(c))
      req.on('end', () => {
        posts += 1 // processed: prompt submitted / session created
        req.socket.resetAndDestroy()
        void res
      })
    })

    const base = await listen(server)

    const postOnce = () =>
      withRetry(
        (state: any) =>
          new Promise((resolve, reject) => {
            const body = Buffer.from(JSON.stringify({ prompt: 'hello' }))

            const req = http.request(
              new URL(`${base}/api/prompt`),
              {
                agent: jsonAgentFor('http:'),
                method: 'POST',
                headers: { 'content-type': 'application/json', 'content-length': String(body.length) }
              },
              res => {
                res.resume()
                res.on('end', () => resolve(null))
              }
            )

            req.on('error', reject)
            state.bodySent = true
            req.write(body)
            req.end()
          }),
        { method: 'POST', delayFn: () => Promise.resolve() }
      )

    try {
      await expect(postOnce()).rejects.toSatisfy((error: any) => isTransientTransportError(error))
      expect(posts).toBe(1) // exactly one server-side submission — no retry
    } finally {
      server.close()
    }
  }, 20_000)

  it('sanity: an identical GET-shaped retry WOULD have re-hit the server', async () => {
    // Companion proof that the verb gate (not luck) is what kept posts === 1:
    // the same reset-after-processing server sees multiple hits under GET.
    let gets = 0

    const server = http.createServer(req => {
      gets += 1
      req.socket.resetAndDestroy()
    })

    const base = await listen(server)

    try {
      await expect(retriedJsonGet(`${base}/api/thing`)).rejects.toThrow()
      expect(gets).toBeGreaterThan(1) // retried — proves the machinery fires
    } finally {
      server.close()
    }
  }, 20_000)
})
