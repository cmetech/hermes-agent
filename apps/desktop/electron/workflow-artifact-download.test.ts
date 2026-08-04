import { EventEmitter } from 'node:events'
import fs from 'node:fs'
import http from 'node:http'
import os from 'node:os'
import path from 'node:path'

import { describe, expect, it, vi } from 'vitest'

import { pathWithGlobalRemoteProfile } from './connection-config'
import {
  atomicReplaceWorkflowArtifact,
  collectWorkflowArtifactResponse,
  downloadWorkflowArtifactWithDeps,
  fetchWorkflowArtifactWithOauthCookie,
  fetchWorkflowArtifactWithToken,
  registerWorkflowArtifactDownloadIpc,
  WORKFLOW_ARTIFACT_MAX_BYTES,
  workflowArtifactAuthHeaders,
  type WorkflowArtifactDownloadDeps
} from './workflow-artifact-download'

async function listen(handler: http.RequestListener): Promise<{ baseUrl: string; close: () => Promise<void> }> {
  const server = http.createServer(handler)

  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()

  if (!address || typeof address === 'string') {
    throw new Error('Loopback server did not expose a TCP address.')
  }

  return {
    baseUrl: `http://127.0.0.1:${address.port}`,
    close: () => new Promise<void>((resolve, reject) => server.close(error => (error ? reject(error) : resolve())))
  }
}

function electronNetRequestFactory(calls: Array<Record<string, unknown>>) {
  return (options: Record<string, any>) => {
    calls.push(options)

    const request = new EventEmitter() as EventEmitter & {
      abort: () => void
      end: () => void
    }

    let active: http.ClientRequest | null = null
    let aborted = false

    const issue = (url: string) => {
      active = http.get(
        url,
        {
          headers: options.useSessionCookies && options.session?.cookie ? { Cookie: options.session.cookie } : {}
        },
        response => {
          const location = response.headers.location

          if (
            options.redirect === 'follow' &&
            location &&
            response.statusCode &&
            response.statusCode >= 300 &&
            response.statusCode < 400
          ) {
            response.resume()
            issue(new URL(location, url).toString())

            return
          }

          request.emit('response', response)
        }
      )
      active.on('error', error => {
        if (!aborted) {
          request.emit('error', error)
        }
      })
    }

    request.abort = () => {
      aborted = true
      active?.destroy()
    }

    request.end = () => issue(String(options.url))

    return request
  }
}

function deps(overrides: Partial<WorkflowArtifactDownloadDeps> = {}): WorkflowArtifactDownloadDeps {
  return {
    chooseSavePath: vi.fn().mockResolvedValue('/tmp/diagnostic.json'),
    ensureBackend: vi.fn().mockResolvedValue({
      authMode: 'token',
      baseUrl: 'http://127.0.0.1:8899',
      token: 'local-session-token'
    }),
    fetchResource: vi.fn().mockResolvedValue({
      bytes: new TextEncoder().encode('{"ok":true}'),
      headers: {
        'content-disposition': 'attachment; filename="diagnostic.json"',
        'content-type': 'application/json'
      }
    }),
    resolveOauthAuth: vi.fn(),
    routePath: vi.fn(path => path),
    writeFile: vi.fn().mockResolvedValue(undefined),
    ...overrides
  }
}

interface TestWebContents extends EventEmitter {
  destroy: () => void
  isDestroyed: () => boolean
  name: string
}

function webContents(name: string): TestWebContents {
  const sender = new EventEmitter() as TestWebContents
  let destroyed = false

  sender.name = name
  sender.isDestroyed = () => destroyed

  sender.destroy = () => {
    if (destroyed) {
      return
    }

    destroyed = true
    sender.emit('destroyed')
  }

  return sender
}

const request = {
  path: '/api/plugins/workflow/runs/run%20%2F%20one/artifacts/publication%20%2F%20opaque/download',
  profile: 'remote-profile',
  requestId: 'request-1'
}

describe('downloadWorkflowArtifactWithDeps', () => {
  it('atomically overwrites an approved destination and replaces a symlink without following it', async () => {
    const tempDir = await fs.promises.mkdtemp(path.join(os.tmpdir(), 'hermes-workflow-atomic-'))
    const destination = path.join(tempDir, 'artifact.json')
    const symlinkTarget = path.join(tempDir, 'target.json')

    try {
      await fs.promises.writeFile(destination, 'old-content')
      await atomicReplaceWorkflowArtifact(destination, new TextEncoder().encode('new-content'))
      await expect(fs.promises.readFile(destination, 'utf8')).resolves.toBe('new-content')

      await fs.promises.writeFile(symlinkTarget, 'target-must-survive')
      await fs.promises.unlink(destination)
      await fs.promises.symlink(symlinkTarget, destination)
      await atomicReplaceWorkflowArtifact(destination, new TextEncoder().encode('replacement'))

      await expect(fs.promises.readFile(symlinkTarget, 'utf8')).resolves.toBe('target-must-survive')
      await expect(fs.promises.readFile(destination, 'utf8')).resolves.toBe('replacement')
      expect((await fs.promises.lstat(destination)).isSymbolicLink()).toBe(false)
    } finally {
      await fs.promises.rm(tempDir, { recursive: true, force: true })
    }
  })

  it('preserves an existing destination and removes same-directory temp residue after a partial write failure', async () => {
    const tempDir = await fs.promises.mkdtemp(path.join(os.tmpdir(), 'hermes-workflow-atomic-failure-'))
    const destination = path.join(tempDir, 'artifact.json')
    await fs.promises.writeFile(destination, 'verified-original')

    try {
      await expect(
        atomicReplaceWorkflowArtifact(destination, new TextEncoder().encode('replacement'), {
          open: async (...args) => {
            const handle = await fs.promises.open(...args)

            return {
              close: () => handle.close(),
              sync: () => handle.sync(),
              writeFile: async bytes => {
                await handle.write(bytes.subarray(0, 3))
                throw new Error('injected partial write failure')
              }
            }
          },
          rename: fs.promises.rename,
          unlink: fs.promises.unlink
        })
      ).rejects.toThrow('injected partial write failure')

      await expect(fs.promises.readFile(destination, 'utf8')).resolves.toBe('verified-original')
      expect((await fs.promises.readdir(tempDir)).filter(name => name.includes('.tmp'))).toEqual([])
    } finally {
      await fs.promises.rm(tempDir, { recursive: true, force: true })
    }
  })

  it('maps static and native OAuth credentials to the established request headers', () => {
    expect(workflowArtifactAuthHeaders({ kind: 'token', token: 'local-session-token' })).toEqual({
      'X-Hermes-Session-Token': 'local-session-token'
    })
    expect(workflowArtifactAuthHeaders({ kind: 'bearer', token: 'native-access-token' })).toEqual({
      Authorization: 'Bearer native-access-token'
    })
  })

  it('sends static-token and native-bearer headers over real loopback HTTP and collects binary metadata', async () => {
    const seen: Array<{ authorization?: string; token?: string }> = []

    const server = await listen((request, response) => {
      seen.push({
        authorization: request.headers.authorization,
        token: request.headers['x-hermes-session-token'] as string | undefined
      })
      response.writeHead(200, {
        'Content-Disposition': 'attachment; filename="report.bin"',
        'Content-Length': '4',
        'Content-Type': 'application/octet-stream'
      })
      response.end(Buffer.from([0, 1, 2, 3]))
    })

    try {
      const staticResult = await fetchWorkflowArtifactWithToken(
        `${server.baseUrl}/static`,
        { kind: 'token', token: 'static-secret' },
        WORKFLOW_ARTIFACT_MAX_BYTES,
        1_000
      )

      const bearerResult = await fetchWorkflowArtifactWithToken(
        `${server.baseUrl}/bearer`,
        { kind: 'bearer', token: 'oauth-secret' },
        WORKFLOW_ARTIFACT_MAX_BYTES,
        1_000
      )

      expect(seen).toEqual([
        { authorization: undefined, token: 'static-secret' },
        { authorization: 'Bearer oauth-secret', token: undefined }
      ])
      expect([...staticResult.bytes]).toEqual([0, 1, 2, 3])
      expect([...bearerResult.bytes]).toEqual([0, 1, 2, 3])
      expect(staticResult.headers).toEqual({
        'content-disposition': 'attachment; filename="report.bin"',
        'content-type': 'application/octet-stream'
      })
    } finally {
      await server.close()
    }
  })

  it('enforces streamed loopback bounds without relying on declared content length', async () => {
    const server = await listen((_request, response) => {
      response.writeHead(200, { 'Content-Type': 'application/octet-stream' })
      response.write(Buffer.alloc(300_000))
      response.end(Buffer.alloc(300_001))
    })

    try {
      await expect(
        fetchWorkflowArtifactWithToken(
          `${server.baseUrl}/oversized`,
          { kind: 'token', token: 'static-secret' },
          WORKFLOW_ARTIFACT_MAX_BYTES,
          1_000
        )
      ).rejects.toThrow('Workflow artifact exceeds the download limit')
    } finally {
      await server.close()
    }
  })

  it('settles a synchronous token request-construction failure once and clears its deadline', async () => {
    vi.useFakeTimers()

    try {
      const rejected = vi.fn()

      const pending = fetchWorkflowArtifactWithToken(
        'http://127.0.0.1:1/invalid-header',
        { kind: 'token', token: 'invalid\nheader' },
        WORKFLOW_ARTIFACT_MAX_BYTES,
        20
      ).catch(error => {
        rejected(error)
        throw error
      })

      await expect(pending).rejects.toThrow('Invalid character in header content')
      expect(rejected).toHaveBeenCalledOnce()
      await vi.advanceTimersByTimeAsync(25)
      expect(rejected).toHaveBeenCalledOnce()
      expect(vi.getTimerCount()).toBe(0)
    } finally {
      vi.useRealTimers()
    }
  })

  it('enforces an absolute token deadline through body completion and closes the response', async () => {
    let responseClosed = false
    let responseStarted!: () => void

    const responseStart = new Promise<void>(resolve => {
      responseStarted = resolve
    })

    const server = await listen((_request, response) => {
      response.writeHead(200, {
        'Content-Disposition': 'attachment; filename="slow.bin"',
        'Content-Type': 'application/octet-stream'
      })
      response.flushHeaders()
      responseStarted()
      const interval = setInterval(() => response.write('x'), 250)
      response.on('close', () => {
        responseClosed = true
        clearInterval(interval)
      })
    })

    try {
      const pending = fetchWorkflowArtifactWithToken(
        `${server.baseUrl}/trickle`,
        { kind: 'token', token: 'static-secret' },
        WORKFLOW_ARTIFACT_MAX_BYTES,
        2_000
      )

      await responseStart
      await expect(pending).rejects.toThrow('deadline')
      await vi.waitFor(() => expect(responseClosed).toBe(true))
    } finally {
      await server.close()
    }
  })

  it('rejects same-origin, cross-origin, and looping redirects without forwarding credentials', async () => {
    let sourceRequests = 0
    let targetRequests = 0

    const target = await listen((_request, response) => {
      targetRequests += 1
      response.end('must not reach target')
    })

    const source = await listen((request, response) => {
      sourceRequests += 1

      const location =
        request.url === '/cross' ? `${target.baseUrl}/artifact` : request.url === '/loop' ? '/loop' : '/artifact'

      response.writeHead(302, { Location: location })
      response.end()
    })

    try {
      for (const route of ['same', 'cross', 'loop']) {
        await expect(
          fetchWorkflowArtifactWithToken(
            `${source.baseUrl}/${route}`,
            { kind: 'bearer', token: 'must-not-forward' },
            WORKFLOW_ARTIFACT_MAX_BYTES,
            1_000
          )
        ).rejects.toThrow('redirect')
      }

      expect(sourceRequests).toBe(3)
      expect(targetRequests).toBe(0)
    } finally {
      await source.close()
      await target.close()
    }
  })

  it('uses the supplied OAuth partition cookies across redirects and keeps collection bounded', async () => {
    const requests: Array<Record<string, any>> = []
    const seenCookies: Array<string | undefined> = []

    const server = await listen((request, response) => {
      if (request.url === '/redirect') {
        response.writeHead(302, { Location: '/artifact' })
        response.end()

        return
      }

      if (request.url === '/oversized') {
        response.writeHead(200, {
          'Content-Length': String(WORKFLOW_ARTIFACT_MAX_BYTES + 1),
          'Content-Type': 'application/octet-stream'
        })
        response.end(Buffer.alloc(WORKFLOW_ARTIFACT_MAX_BYTES + 1))

        return
      }

      seenCookies.push(request.headers.cookie)
      response.writeHead(200, {
        'Content-Disposition': 'attachment; filename="cookie.json"',
        'Content-Length': '2',
        'Content-Type': 'application/json'
      })
      response.end('{}')
    })

    const oauthSession = { cookie: 'oauth_session=partition-secret', partition: 'persist:hermes-oauth' }

    try {
      const result = await fetchWorkflowArtifactWithOauthCookie(
        `${server.baseUrl}/redirect`,
        WORKFLOW_ARTIFACT_MAX_BYTES,
        1_000,
        {
          getSession: () => oauthSession,
          request: electronNetRequestFactory(requests)
        }
      )

      expect(seenCookies).toEqual(['oauth_session=partition-secret'])
      expect(requests[0]).toMatchObject({
        redirect: 'follow',
        session: oauthSession,
        useSessionCookies: true
      })
      expect(new TextDecoder().decode(result.bytes)).toBe('{}')
      await expect(
        fetchWorkflowArtifactWithOauthCookie(`${server.baseUrl}/oversized`, WORKFLOW_ARTIFACT_MAX_BYTES, 1_000, {
          getSession: () => oauthSession,
          request: electronNetRequestFactory(requests)
        })
      ).rejects.toThrow('Workflow artifact exceeds the download limit')
    } finally {
      await server.close()
    }
  })

  it('aborts a stalled OAuth partition request on timeout', async () => {
    let aborted = false
    const request = new EventEmitter() as EventEmitter & { abort: () => void; end: () => void }

    request.abort = () => {
      aborted = true
    }

    request.end = () => undefined

    await expect(
      fetchWorkflowArtifactWithOauthCookie('http://127.0.0.1:1/stalled', WORKFLOW_ARTIFACT_MAX_BYTES, 10, {
        getSession: () => ({ partition: 'persist:hermes-oauth' }),
        request: () => request
      })
    ).rejects.toThrow('Timed out connecting to Hermes backend')
    expect(aborted).toBe(true)
  })

  it('parents the native dialog to the live invoking peer and writes the downloaded file', async () => {
    const tempDir = await fs.promises.mkdtemp(path.join(os.tmpdir(), 'hermes-workflow-artifact-'))
    const filePath = path.join(tempDir, 'peer-report.json')
    const peerWindow = { isDestroyed: () => false, name: 'peer-window' }
    const primaryWindow = { isDestroyed: () => false, name: 'primary-window' }
    const sender = webContents('peer-web-contents')
    const handlers = new Map<string, (...args: any[]) => Promise<unknown>>()
    const showSaveDialog = vi.fn().mockResolvedValue({ canceled: false, filePath })

    const resourceServer = await listen((_request, response) => {
      response.writeHead(200, {
        'Content-Disposition': 'attachment; filename="peer-report.json"',
        'Content-Length': '11',
        'Content-Type': 'application/json'
      })
      response.end('{"ok":true}')
    })

    registerWorkflowArtifactDownloadIpc({
      browserWindow: { fromWebContents: value => (value === sender ? peerWindow : primaryWindow) },
      dialog: { showSaveDialog },
      download: {
        ensureBackend: async () => ({
          authMode: 'token',
          baseUrl: resourceServer.baseUrl,
          token: 'local-token'
        }),
        fetchResource: (url, auth, maxBytes) =>
          fetchWorkflowArtifactWithToken(url, auth as { kind: 'token'; token: string }, maxBytes, 1_000),
        resolveOauthAuth: vi.fn(),
        routePath: (requestPath, profile) =>
          pathWithGlobalRemoteProfile(requestPath, profile, {
            globalRemote: false,
            profileRemoteOverride: false
          })
      },
      ipcMain: { handle: (channel, handler) => handlers.set(channel, handler) }
    })

    try {
      const handler = handlers.get('hermes:workflow-artifact:download')!
      await expect(handler({ sender }, request)).resolves.toMatchObject({ status: 'saved' })
      expect(showSaveDialog).toHaveBeenCalledWith(peerWindow, { defaultPath: 'peer-report.json' })
      expect(showSaveDialog).not.toHaveBeenCalledWith(primaryWindow, expect.anything())
      await expect(fs.promises.readFile(filePath, 'utf8')).resolves.toBe('{"ok":true}')
      expect(sender.listenerCount('destroyed')).toBe(0)
      await expect(
        handlers.get('hermes:workflow-artifact:cancel')!({ sender }, { requestId: request.requestId })
      ).resolves.toEqual({ cancelled: false })
      sender.destroy()
      expect(showSaveDialog).toHaveBeenCalledOnce()
    } finally {
      await resourceServer.close()
      await fs.promises.rm(tempDir, { recursive: true, force: true })
    }
  })

  it('uses an unparented dialog for a gone sender, preserves cancellation, and surfaces write failures', async () => {
    const tempDir = await fs.promises.mkdtemp(path.join(os.tmpdir(), 'hermes-workflow-artifact-write-failure-'))
    const handlers = new Map<string, (...args: any[]) => Promise<unknown>>()

    const showSaveDialog = vi
      .fn()
      .mockResolvedValueOnce({ canceled: true })
      .mockResolvedValueOnce({ canceled: false, filePath: tempDir })

    registerWorkflowArtifactDownloadIpc({
      browserWindow: { fromWebContents: () => ({ isDestroyed: () => true }) },
      dialog: { showSaveDialog },
      download: {
        ensureBackend: async () => ({ authMode: 'token', baseUrl: 'http://127.0.0.1:8899', token: 'token' }),
        fetchResource: vi.fn().mockResolvedValue({
          bytes: new TextEncoder().encode('{}'),
          headers: {
            'content-disposition': 'attachment; filename="artifact.json"',
            'content-type': 'application/json'
          }
        }),
        resolveOauthAuth: vi.fn(),
        routePath: value => value
      },
      ipcMain: { handle: (channel, handler) => handlers.set(channel, handler) }
    })

    try {
      const handler = handlers.get('hermes:workflow-artifact:download')!
      await expect(handler({ sender: webContents('gone-sender-one') }, request)).resolves.toEqual({
        status: 'cancelled'
      })
      await expect(handler({ sender: webContents('gone-sender-two') }, request)).rejects.toThrow()
      expect(showSaveDialog).toHaveBeenNthCalledWith(1, { defaultPath: 'artifact.json' })
      expect(showSaveDialog).toHaveBeenNthCalledWith(2, { defaultPath: 'artifact.json' })
    } finally {
      await fs.promises.rm(tempDir, { recursive: true, force: true })
    }
  })

  it('cancels an in-flight request by request id and never opens a late dialog', async () => {
    const handlers = new Map<string, (...args: any[]) => Promise<unknown>>()
    const showSaveDialog = vi.fn()
    const sender = webContents('cancel-sender')
    let transportSignal: AbortSignal | undefined

    registerWorkflowArtifactDownloadIpc({
      browserWindow: { fromWebContents: () => ({ isDestroyed: () => false }) },
      dialog: { showSaveDialog },
      download: {
        ensureBackend: async () => ({ authMode: 'token', baseUrl: 'http://127.0.0.1:8899', token: 'token' }),
        fetchResource: (_url, _auth, _maxBytes, signal) => {
          transportSignal = signal

          return new Promise((_resolve, reject) => {
            signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
          })
        },
        resolveOauthAuth: vi.fn(),
        routePath: value => value
      },
      ipcMain: { handle: (channel, handler) => handlers.set(channel, handler) }
    })

    const pending = handlers.get('hermes:workflow-artifact:download')!({ sender }, request)
    await vi.waitFor(() => expect(transportSignal).toBeDefined())
    await expect(
      handlers.get('hermes:workflow-artifact:cancel')!({ sender }, { requestId: request.requestId })
    ).resolves.toEqual({ cancelled: true })
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' })
    expect(transportSignal?.aborted).toBe(true)
    expect(showSaveDialog).not.toHaveBeenCalled()
  })

  it('isolates identical request ids by invoking sender and removes both entries after settlement', async () => {
    const handlers = new Map<string, (...args: any[]) => Promise<unknown>>()
    const showSaveDialog = vi.fn()
    const firstSender = webContents('first-sender')
    const secondSender = webContents('second-sender')
    const signals: AbortSignal[] = []

    registerWorkflowArtifactDownloadIpc({
      browserWindow: { fromWebContents: () => ({ isDestroyed: () => false }) },
      dialog: { showSaveDialog },
      download: {
        ensureBackend: async () => ({ authMode: 'token', baseUrl: 'http://127.0.0.1:8899', token: 'token' }),
        fetchResource: (_url, _auth, _maxBytes, signal) => {
          if (!signal) {
            throw new Error('Expected the IPC download transport to receive an abort signal.')
          }

          signals.push(signal)

          return new Promise((_resolve, reject) => {
            signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
          })
        },
        resolveOauthAuth: vi.fn(),
        routePath: value => value
      },
      ipcMain: { handle: (channel, handler) => handlers.set(channel, handler) }
    })

    const download = handlers.get('hermes:workflow-artifact:download')!
    const cancel = handlers.get('hermes:workflow-artifact:cancel')!

    const firstOutcome = download({ sender: firstSender }, request).then(
      value => value,
      error => error
    )

    await vi.waitFor(() => expect(signals).toHaveLength(1))

    const secondOutcome = download({ sender: secondSender }, request).then(
      value => value,
      error => error
    )

    await vi.waitFor(() => expect(signals).toHaveLength(2))
    expect(signals.map(signal => signal.aborted)).toEqual([false, false])

    await expect(cancel({ sender: firstSender }, { requestId: request.requestId })).resolves.toEqual({
      cancelled: true
    })
    await expect(firstOutcome).resolves.toMatchObject({ name: 'AbortError' })
    expect(signals.map(signal => signal.aborted)).toEqual([true, false])
    await expect(cancel({ sender: firstSender }, { requestId: request.requestId })).resolves.toEqual({
      cancelled: false
    })

    await expect(cancel({ sender: secondSender }, { requestId: request.requestId })).resolves.toEqual({
      cancelled: true
    })
    await expect(secondOutcome).resolves.toMatchObject({ name: 'AbortError' })
    await expect(cancel({ sender: secondSender }, { requestId: request.requestId })).resolves.toEqual({
      cancelled: false
    })
    expect(showSaveDialog).not.toHaveBeenCalled()
  })

  it('aborts and evicts a destroyed sender without affecting its peer or opening a late dialog', async () => {
    const tempDir = await fs.promises.mkdtemp(path.join(os.tmpdir(), 'hermes-workflow-destroyed-sender-'))
    const abandonedPath = path.join(tempDir, 'abandoned.json')
    const handlers = new Map<string, (...args: any[]) => Promise<unknown>>()
    const firstSender = webContents('destroyed-sender')
    const secondSender = webContents('live-sender')

    const transports: Array<{
      resolve: (resource: { bytes: Uint8Array; headers: Record<string, string> }) => void
      signal: AbortSignal
    }> = []

    const resource = {
      bytes: new TextEncoder().encode('{}'),
      headers: {
        'content-disposition': 'attachment; filename="artifact.json"',
        'content-type': 'application/json'
      }
    }

    const showSaveDialog = vi.fn(async (...args: unknown[]) =>
      args.length === 1 ? { canceled: false, filePath: abandonedPath } : { canceled: true }
    )

    registerWorkflowArtifactDownloadIpc({
      browserWindow: {
        fromWebContents: value => ({ isDestroyed: () => (value as TestWebContents).isDestroyed() })
      },
      dialog: { showSaveDialog },
      download: {
        ensureBackend: async () => ({ authMode: 'token', baseUrl: 'http://127.0.0.1:8899', token: 'token' }),
        fetchResource: (_url, _auth, _maxBytes, signal) => {
          if (!signal) {
            throw new Error('Expected the IPC download transport to receive an abort signal.')
          }

          return new Promise((resolve, reject) => {
            transports.push({ resolve, signal })
            signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
          })
        },
        resolveOauthAuth: vi.fn(),
        routePath: value => value
      },
      ipcMain: { handle: (channel, handler) => handlers.set(channel, handler) }
    })

    const download = handlers.get('hermes:workflow-artifact:download')!
    const cancel = handlers.get('hermes:workflow-artifact:cancel')!

    const firstOutcome = download({ sender: firstSender }, request).then(
      value => value,
      error => error
    )

    const secondOutcome = download({ sender: secondSender }, request).then(
      value => value,
      error => error
    )

    try {
      await vi.waitFor(() => expect(transports).toHaveLength(2))
      expect(firstSender.listenerCount('destroyed')).toBe(1)
      expect(secondSender.listenerCount('destroyed')).toBe(1)

      firstSender.destroy()

      await expect(firstOutcome).resolves.toMatchObject({ name: 'AbortError' })
      expect(transports.map(transport => transport.signal.aborted)).toEqual([true, false])
      await expect(cancel({ sender: firstSender }, { requestId: request.requestId })).resolves.toEqual({
        cancelled: false
      })

      transports[0]!.resolve(resource)
      await Promise.resolve()
      expect(showSaveDialog).not.toHaveBeenCalled()
      await expect(fs.promises.stat(abandonedPath)).rejects.toMatchObject({ code: 'ENOENT' })

      await expect(cancel({ sender: secondSender }, { requestId: request.requestId })).resolves.toEqual({
        cancelled: true
      })
      await expect(secondOutcome).resolves.toMatchObject({ name: 'AbortError' })
      expect(secondSender.listenerCount('destroyed')).toBe(0)
      await expect(cancel({ sender: secondSender }, { requestId: request.requestId })).resolves.toEqual({
        cancelled: false
      })

      const normalRequest = { ...request, requestId: 'normal-peer-request' }
      const normalOutcome = download({ sender: secondSender }, normalRequest)

      await vi.waitFor(() => expect(transports).toHaveLength(3))
      transports[2]!.resolve(resource)
      await expect(normalOutcome).resolves.toEqual({ status: 'cancelled' })
      expect(secondSender.listenerCount('destroyed')).toBe(0)
      await expect(cancel({ sender: secondSender }, { requestId: normalRequest.requestId })).resolves.toEqual({
        cancelled: false
      })
      secondSender.destroy()
      expect(showSaveDialog).toHaveBeenCalledOnce()
      await expect(fs.promises.stat(abandonedPath)).rejects.toMatchObject({ code: 'ENOENT' })
    } finally {
      await fs.promises.rm(tempDir, { force: true, recursive: true })
    }
  })

  it('rejects a download whose sender was already destroyed before handler setup', async () => {
    const handlers = new Map<string, (...args: any[]) => Promise<unknown>>()
    const sender = webContents('already-destroyed-sender')
    const fetchResource = vi.fn()
    const showSaveDialog = vi.fn()

    sender.destroy()
    registerWorkflowArtifactDownloadIpc({
      browserWindow: { fromWebContents: () => ({ isDestroyed: () => true }) },
      dialog: { showSaveDialog },
      download: {
        ensureBackend: async () => ({ authMode: 'token', baseUrl: 'http://127.0.0.1:8899', token: 'token' }),
        fetchResource,
        resolveOauthAuth: vi.fn(),
        routePath: value => value
      },
      ipcMain: { handle: (channel, handler) => handlers.set(channel, handler) }
    })

    await expect(handlers.get('hermes:workflow-artifact:download')!({ sender }, request)).rejects.toMatchObject({
      name: 'AbortError'
    })
    expect(fetchResource).not.toHaveBeenCalled()
    expect(showSaveDialog).not.toHaveBeenCalled()
    expect(sender.listenerCount('destroyed')).toBe(0)
  })

  it('aborts before buffering a response whose recorded size exceeds the producer limit', async () => {
    const response = new EventEmitter() as EventEmitter & {
      headers: Record<string, string>
      statusCode: number
    }

    response.headers = { 'content-length': String(WORKFLOW_ARTIFACT_MAX_BYTES + 1) }
    response.statusCode = 200
    const abort = vi.fn()

    await expect(collectWorkflowArtifactResponse(response, WORKFLOW_ARTIFACT_MAX_BYTES, abort)).rejects.toThrow(
      'Workflow artifact exceeds the download limit'
    )
    expect(abort).toHaveBeenCalledOnce()
  })

  it('downloads through the resolved backend instead of the renderer file:// origin and saves recorded metadata', async () => {
    const testDeps = deps()

    await expect(downloadWorkflowArtifactWithDeps(request, testDeps)).resolves.toEqual({
      filename: 'diagnostic.json',
      mediaType: 'application/json',
      sizeBytes: 11,
      status: 'saved'
    })

    expect(testDeps.ensureBackend).toHaveBeenCalledWith('remote-profile')
    expect(testDeps.routePath).toHaveBeenCalledWith(request.path, 'remote-profile')
    expect(testDeps.fetchResource).toHaveBeenCalledWith(
      'http://127.0.0.1:8899/api/plugins/workflow/runs/run%20%2F%20one/artifacts/publication%20%2F%20opaque/download',
      { kind: 'token', token: 'local-session-token' },
      WORKFLOW_ARTIFACT_MAX_BYTES
    )
    expect(testDeps.chooseSavePath).toHaveBeenCalledWith({
      filename: 'diagnostic.json',
      mediaType: 'application/json'
    })
    expect(testDeps.writeFile).toHaveBeenCalledWith('/tmp/diagnostic.json', expect.any(Uint8Array))
  })

  it('keeps the captured profile while applying global-remote/profile-aware routing', async () => {
    const testDeps = deps({
      ensureBackend: vi.fn().mockResolvedValue({
        authMode: 'token',
        baseUrl: 'https://gateway.example',
        token: 'remote-static-token'
      }),
      routePath: vi.fn((path, profile) => `${path}?profile=${encodeURIComponent(profile ?? '')}`)
    })

    await downloadWorkflowArtifactWithDeps(request, testDeps)

    expect(testDeps.fetchResource).toHaveBeenCalledWith(
      'https://gateway.example/api/plugins/workflow/runs/run%20%2F%20one/artifacts/publication%20%2F%20opaque/download?profile=remote-profile',
      { kind: 'token', token: 'remote-static-token' },
      WORKFLOW_ARTIFACT_MAX_BYTES
    )
  })

  it('uses a refreshed native OAuth bearer when available', async () => {
    const testDeps = deps({
      ensureBackend: vi.fn().mockResolvedValue({ authMode: 'oauth', baseUrl: 'https://gateway.example', token: '' }),
      resolveOauthAuth: vi.fn().mockResolvedValue({ kind: 'bearer', token: 'native-access-token' })
    })

    await downloadWorkflowArtifactWithDeps(request, testDeps)

    expect(testDeps.resolveOauthAuth).toHaveBeenCalledWith('https://gateway.example')
    expect(testDeps.fetchResource).toHaveBeenCalledWith(
      expect.stringContaining('https://gateway.example/api/plugins/workflow/'),
      { kind: 'bearer', token: 'native-access-token' },
      WORKFLOW_ARTIFACT_MAX_BYTES
    )
  })

  it('uses the OAuth session-cookie transport when no native bearer exists', async () => {
    const testDeps = deps({
      ensureBackend: vi.fn().mockResolvedValue({ authMode: 'oauth', baseUrl: 'https://gateway.example', token: '' }),
      resolveOauthAuth: vi.fn().mockResolvedValue({ kind: 'cookie' })
    })

    await downloadWorkflowArtifactWithDeps(request, testDeps)

    expect(testDeps.fetchResource).toHaveBeenCalledWith(
      expect.stringContaining('https://gateway.example/api/plugins/workflow/'),
      { kind: 'cookie' },
      WORKFLOW_ARTIFACT_MAX_BYTES
    )
  })

  it('returns typed cancellation without writing a file', async () => {
    const testDeps = deps({ chooseSavePath: vi.fn().mockResolvedValue(null) })

    await expect(downloadWorkflowArtifactWithDeps(request, testDeps)).resolves.toEqual({ status: 'cancelled' })
    expect(testDeps.writeFile).not.toHaveBeenCalled()
  })

  it('rejects unscoped paths before transport and bounds responses at the producer limit', async () => {
    const invalidDeps = deps()

    await expect(
      downloadWorkflowArtifactWithDeps(
        { path: 'https://attacker.example/artifact', profile: 'default', requestId: 'invalid-request' },
        invalidDeps
      )
    ).rejects.toThrow('Invalid workflow artifact download path')
    expect(invalidDeps.ensureBackend).not.toHaveBeenCalled()

    const oversizedDeps = deps({
      fetchResource: vi.fn().mockResolvedValue({
        bytes: new Uint8Array(WORKFLOW_ARTIFACT_MAX_BYTES + 1),
        headers: {
          'content-disposition': 'attachment; filename="too-large.json"',
          'content-type': 'application/json'
        }
      })
    })

    await expect(downloadWorkflowArtifactWithDeps(request, oversizedDeps)).rejects.toThrow(
      'Workflow artifact exceeds the download limit'
    )
    expect(oversizedDeps.chooseSavePath).not.toHaveBeenCalled()
    expect(oversizedDeps.writeFile).not.toHaveBeenCalled()
  })

  it('leaves transport failures retryable and never opens the save dialog after failure', async () => {
    const testDeps = deps({ fetchResource: vi.fn().mockRejectedValue(new Error('gateway unavailable')) })

    await expect(downloadWorkflowArtifactWithDeps(request, testDeps)).rejects.toThrow('gateway unavailable')
    expect(testDeps.chooseSavePath).not.toHaveBeenCalled()
    expect(testDeps.writeFile).not.toHaveBeenCalled()
  })
})
