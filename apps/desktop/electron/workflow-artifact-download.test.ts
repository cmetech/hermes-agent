import { EventEmitter } from 'node:events'
import fs from 'node:fs'
import http from 'node:http'
import os from 'node:os'
import path from 'node:path'

import { describe, expect, it, vi } from 'vitest'

import { pathWithGlobalRemoteProfile } from './connection-config'
import {
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

const request = {
  path: '/api/plugins/workflow/runs/run%20%2F%20one/artifacts/publication%20%2F%20opaque/download',
  profile: 'remote-profile'
}

describe('downloadWorkflowArtifactWithDeps', () => {
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
    const sender = { name: 'peer-web-contents' }
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
      ipcMain: { handle: (channel, handler) => handlers.set(channel, handler) },
      writeFile: fs.promises.writeFile
    })

    try {
      const handler = handlers.get('hermes:workflow-artifact:download')!
      await expect(handler({ sender }, request)).resolves.toMatchObject({ status: 'saved' })
      expect(showSaveDialog).toHaveBeenCalledWith(peerWindow, { defaultPath: 'peer-report.json' })
      expect(showSaveDialog).not.toHaveBeenCalledWith(primaryWindow, expect.anything())
      await expect(fs.promises.readFile(filePath, 'utf8')).resolves.toBe('{"ok":true}')
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
      ipcMain: { handle: (channel, handler) => handlers.set(channel, handler) },
      writeFile: fs.promises.writeFile
    })

    try {
      const handler = handlers.get('hermes:workflow-artifact:download')!
      await expect(handler({ sender: {} }, request)).resolves.toEqual({ status: 'cancelled' })
      await expect(handler({ sender: {} }, request)).rejects.toThrow()
      expect(showSaveDialog).toHaveBeenNthCalledWith(1, { defaultPath: 'artifact.json' })
      expect(showSaveDialog).toHaveBeenNthCalledWith(2, { defaultPath: 'artifact.json' })
    } finally {
      await fs.promises.rm(tempDir, { recursive: true, force: true })
    }
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
      downloadWorkflowArtifactWithDeps({ path: 'https://attacker.example/artifact', profile: 'default' }, invalidDeps)
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
