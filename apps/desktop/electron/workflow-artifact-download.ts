import crypto from 'node:crypto'
import fs from 'node:fs'
import http from 'node:http'
import https from 'node:https'
import path from 'node:path'

export const WORKFLOW_ARTIFACT_MAX_BYTES = 500_000

export interface WorkflowArtifactDownloadRequest {
  path: string
  profile?: null | string
  requestId: string
}

export type WorkflowArtifactDownloadResult =
  | { status: 'cancelled' }
  | { filename: string; mediaType: string; sizeBytes: number; status: 'saved' }

export type WorkflowArtifactDownloadAuth =
  | { kind: 'bearer'; token: string }
  | { kind: 'cookie' }
  | { kind: 'token'; token: string }

export interface WorkflowArtifactBackendConnection {
  authMode?: null | string
  baseUrl: string
  token: string
}

export interface WorkflowArtifactResource {
  bytes: Uint8Array
  headers: Record<string, string | undefined>
}

export interface WorkflowArtifactDownloadDeps {
  chooseSavePath: (metadata: { filename: string; mediaType: string }) => Promise<null | string>
  ensureBackend: (profile?: null | string) => Promise<WorkflowArtifactBackendConnection>
  fetchResource: (
    url: string,
    auth: WorkflowArtifactDownloadAuth,
    maxBytes: number,
    signal?: AbortSignal
  ) => Promise<WorkflowArtifactResource>
  resolveOauthAuth: (baseUrl: string) => Promise<Extract<WorkflowArtifactDownloadAuth, { kind: 'bearer' | 'cookie' }>>
  routePath: (path: string, profile?: null | string) => string
  writeFile: (filePath: string, bytes: Uint8Array) => Promise<void>
}

interface OauthCookieRequest {
  abort: () => void
  end: () => void
  on: (event: string, listener: (...args: any[]) => void) => OauthCookieRequest
}

export interface WorkflowArtifactOauthCookieDeps {
  getSession: () => unknown
  request: (options: Record<string, unknown>) => OauthCookieRequest
}

interface WorkflowArtifactNativeWindow {
  isDestroyed: () => boolean
}

interface WorkflowArtifactIpcEvent {
  sender: unknown
}

export interface WorkflowArtifactDownloadIpcDeps {
  browserWindow: {
    fromWebContents: (sender: unknown) => null | WorkflowArtifactNativeWindow
  }
  dialog: {
    showSaveDialog: (...args: any[]) => Promise<{ canceled: boolean; filePath?: string }>
  }
  download: Omit<WorkflowArtifactDownloadDeps, 'chooseSavePath' | 'writeFile'>
  ipcMain: {
    handle: (channel: string, handler: (event: WorkflowArtifactIpcEvent, request: any) => Promise<unknown>) => void
  }
}

interface AtomicArtifactFileHandle {
  close: () => Promise<void>
  sync: () => Promise<void>
  writeFile: (bytes: Uint8Array) => Promise<void>
}

export interface AtomicArtifactWriteDeps {
  open: (filePath: string, flags: string, mode: number) => Promise<AtomicArtifactFileHandle>
  rename: (oldPath: string, newPath: string) => Promise<void>
  unlink: (filePath: string) => Promise<void>
}

const DOWNLOAD_PATH_PATTERN = /^\/api\/plugins\/workflow\/runs\/([^/?#]+)\/artifacts\/([^/?#]+)\/download$/

export function workflowArtifactAuthHeaders(
  auth: Exclude<WorkflowArtifactDownloadAuth, { kind: 'cookie' }>
): Record<string, string> {
  return auth.kind === 'bearer' ? { Authorization: `Bearer ${auth.token}` } : { 'X-Hermes-Session-Token': auth.token }
}

function responseHeader(headers: unknown, name: string): string {
  const value = Object.entries(headers ?? {}).find(([key]) => key.toLowerCase() === name.toLowerCase())?.[1]

  return Array.isArray(value) ? String(value[0] ?? '') : String(value ?? '')
}

function responseStatusError(statusCode: number): Error | null {
  if (statusCode >= 200 && statusCode < 300) {
    return null
  }

  if (statusCode >= 300 && statusCode < 400) {
    return new Error(`Authenticated workflow artifact download rejected redirect response ${statusCode}.`)
  }

  return new Error(`Workflow artifact download failed with HTTP ${statusCode}.`)
}

function abortError(): DOMException {
  return new DOMException('Workflow artifact download was cancelled.', 'AbortError')
}

export async function atomicReplaceWorkflowArtifact(
  destination: string,
  bytes: Uint8Array,
  deps: AtomicArtifactWriteDeps = {
    open: (filePath, flags, mode) => fs.promises.open(filePath, flags, mode),
    rename: fs.promises.rename,
    unlink: fs.promises.unlink
  }
): Promise<void> {
  if (bytes.byteLength > WORKFLOW_ARTIFACT_MAX_BYTES) {
    throw new Error(`Workflow artifact exceeds the download limit of ${WORKFLOW_ARTIFACT_MAX_BYTES} bytes.`)
  }

  const directory = path.dirname(destination)
  const basename = path.basename(destination)
  let handle: AtomicArtifactFileHandle | null = null
  let temporaryPath = ''

  for (let attempt = 0; attempt < 8; attempt += 1) {
    temporaryPath = path.join(directory, `.${basename}.${crypto.randomBytes(12).toString('hex')}.tmp`)

    try {
      handle = await deps.open(temporaryPath, 'wx', 0o600)

      break
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'EEXIST' || attempt === 7) {
        throw error
      }
    }
  }

  if (!handle || !temporaryPath) {
    throw new Error('Could not create a temporary workflow artifact file.')
  }

  try {
    await handle.writeFile(bytes)
    await handle.sync()
    await handle.close()
    handle = null
    await deps.rename(temporaryPath, destination)
    temporaryPath = ''
  } finally {
    if (handle) {
      await handle.close().catch(() => undefined)
    }

    if (temporaryPath) {
      await deps.unlink(temporaryPath).catch(() => undefined)
    }
  }
}

export function collectWorkflowArtifactResponse(
  response: any,
  maxBytes: number,
  abort: () => void
): Promise<WorkflowArtifactResource> {
  return new Promise((resolve, reject) => {
    const contentLengthHeader = responseHeader(response.headers, 'content-length')
    const contentLength = contentLengthHeader ? Number(contentLengthHeader) : null
    const statusError = responseStatusError(Number(response.statusCode || 500))

    if (statusError) {
      abort()
      reject(statusError)

      return
    }

    if (contentLength !== null && Number.isFinite(contentLength) && contentLength > maxBytes) {
      abort()
      reject(new Error(`Workflow artifact exceeds the download limit of ${maxBytes} bytes.`))

      return
    }

    const chunks: Buffer[] = []
    let byteLength = 0
    let settled = false

    const fail = (error: Error) => {
      if (settled) {
        return
      }

      settled = true
      abort()
      reject(error)
    }

    response.on('error', error => fail(error instanceof Error ? error : new Error(String(error))))
    response.on('data', chunk => {
      if (settled) {
        return
      }

      const bytes = Buffer.from(chunk)
      byteLength += bytes.byteLength

      if (byteLength > maxBytes) {
        fail(new Error(`Workflow artifact exceeds the download limit of ${maxBytes} bytes.`))

        return
      }

      chunks.push(bytes)
    })
    response.on('end', () => {
      if (settled) {
        return
      }

      settled = true
      const bytes = Buffer.concat(chunks, byteLength)

      if (contentLength !== null && Number.isFinite(contentLength) && contentLength !== byteLength) {
        reject(new Error(`Workflow artifact response ended after ${byteLength} of ${contentLength} bytes.`))

        return
      }

      resolve({
        bytes,
        headers: {
          'content-disposition': responseHeader(response.headers, 'content-disposition'),
          'content-type': responseHeader(response.headers, 'content-type')
        }
      })
    })
  })
}

export function fetchWorkflowArtifactWithToken(
  url: string,
  auth: Exclude<WorkflowArtifactDownloadAuth, { kind: 'cookie' }>,
  maxBytes: number,
  timeoutMs: number,
  signal?: AbortSignal
): Promise<WorkflowArtifactResource> {
  return new Promise((resolve, reject) => {
    const parsed = new URL(url)
    const client = parsed.protocol === 'https:' ? https : http

    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      reject(new Error(`Unsupported Hermes backend URL protocol: ${parsed.protocol}`))

      return
    }

    let deadline: ReturnType<typeof setTimeout> | null = null
    let request: http.ClientRequest | null = null
    let response: http.IncomingMessage | null = null
    let settled = false

    const cleanup = () => {
      if (deadline) {
        clearTimeout(deadline)
        deadline = null
      }

      signal?.removeEventListener('abort', onAbort)
    }

    const fail = (error: unknown) => {
      if (settled) {
        return
      }

      settled = true
      cleanup()
      response?.destroy()
      request?.destroy()
      reject(error instanceof Error ? error : new Error(String(error)))
    }

    const succeed = (resource: WorkflowArtifactResource) => {
      if (settled) {
        return
      }

      settled = true
      cleanup()
      resolve(resource)
    }

    const onAbort = () => fail(abortError())

    deadline = setTimeout(
      () => fail(new Error(`Workflow artifact download exceeded its ${timeoutMs}ms deadline.`)),
      timeoutMs
    )

    try {
      request = client.request(parsed, { headers: workflowArtifactAuthHeaders(auth), method: 'GET' }, incoming => {
        response = incoming
        void collectWorkflowArtifactResponse(incoming, maxBytes, () => {
          incoming.destroy()
          request?.destroy()
        }).then(succeed, fail)
      })
    } catch (error) {
      fail(error)

      return
    }

    request.on('error', fail)
    signal?.addEventListener('abort', onAbort, { once: true })

    if (signal?.aborted) {
      onAbort()

      return
    }

    request.end()
  })
}

export function fetchWorkflowArtifactWithOauthCookie(
  url: string,
  maxBytes: number,
  timeoutMs: number,
  deps: WorkflowArtifactOauthCookieDeps,
  signal?: AbortSignal
): Promise<WorkflowArtifactResource> {
  return new Promise((resolve, reject) => {
    const oauthSession = deps.getSession()

    if (!oauthSession) {
      reject(new Error('OAuth session partition is unavailable.'))

      return
    }

    const parsed = new URL(url)

    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      reject(new Error(`Unsupported Hermes backend URL protocol: ${parsed.protocol}`))

      return
    }

    const request = deps.request({
      method: 'GET',
      redirect: 'follow',
      session: oauthSession,
      url,
      useSessionCookies: true
    })

    let settled = false

    const fail = (error: unknown) => {
      if (settled) {
        return
      }

      settled = true
      clearTimeout(timer)
      signal?.removeEventListener('abort', onAbort)
      reject(error instanceof Error ? error : new Error(String(error)))
    }

    const onAbort = () => {
      fail(abortError())
      request.abort()
    }

    const timer = setTimeout(() => {
      fail(new Error(`Timed out connecting to Hermes backend after ${timeoutMs}ms`))
      request.abort()
    }, timeoutMs)

    request.on('response', response => {
      void collectWorkflowArtifactResponse(response, maxBytes, () => request.abort()).then(value => {
        if (settled) {
          return
        }

        settled = true
        clearTimeout(timer)
        signal?.removeEventListener('abort', onAbort)
        resolve(value)
      }, fail)
    })
    request.on('error', fail)
    signal?.addEventListener('abort', onAbort, { once: true })

    if (signal?.aborted) {
      onAbort()

      return
    }

    request.end()
  })
}

export function registerWorkflowArtifactDownloadIpc(deps: WorkflowArtifactDownloadIpcDeps): void {
  const activeBySender = new Map<unknown, Map<string, AbortController>>()

  deps.ipcMain.handle('hermes:workflow-artifact:download', async (event, request) => {
    if (typeof request?.requestId !== 'string' || request.requestId.trim().length === 0) {
      throw new Error('Workflow artifact download request id is required.')
    }

    const sender = event.sender
    const active = activeBySender.get(sender) ?? new Map<string, AbortController>()

    activeBySender.set(sender, active)
    active.get(request.requestId)?.abort()
    const controller = new AbortController()
    active.set(request.requestId, controller)

    try {
      return await downloadWorkflowArtifactWithDeps(
        request,
        {
          ...deps.download,
          chooseSavePath: async ({ filename }) => {
            controller.signal.throwIfAborted()
            let owner: null | WorkflowArtifactNativeWindow = null

            try {
              owner = deps.browserWindow.fromWebContents(event.sender)
            } catch {
              // The invoking WebContents may have closed between click and save.
            }

            const options = { defaultPath: filename }

            const result =
              owner && !owner.isDestroyed()
                ? await deps.dialog.showSaveDialog(owner, options)
                : await deps.dialog.showSaveDialog(options)

            controller.signal.throwIfAborted()

            return result.canceled || !result.filePath ? null : result.filePath
          },
          writeFile: atomicReplaceWorkflowArtifact
        },
        controller.signal
      )
    } finally {
      if (active.get(request.requestId) === controller) {
        active.delete(request.requestId)

        if (active.size === 0 && activeBySender.get(sender) === active) {
          activeBySender.delete(sender)
        }
      }
    }
  })

  deps.ipcMain.handle('hermes:workflow-artifact:cancel', async (event, payload) => {
    const controller = activeBySender.get(event.sender)?.get(payload?.requestId)
    controller?.abort()

    return { cancelled: Boolean(controller) }
  })
}

function isCanonicalEncodedIdentity(value: string): boolean {
  try {
    const decoded = decodeURIComponent(value)

    return decoded.trim().length > 0 && encodeURIComponent(decoded) === value
  } catch {
    return false
  }
}

function assertWorkflowArtifactDownloadPath(requestPath: unknown): asserts requestPath is string {
  if (typeof requestPath !== 'string') {
    throw new Error('Invalid workflow artifact download path.')
  }

  const match = DOWNLOAD_PATH_PATTERN.exec(requestPath)

  if (!match || !isCanonicalEncodedIdentity(match[1]!) || !isCanonicalEncodedIdentity(match[2]!)) {
    throw new Error('Invalid workflow artifact download path.')
  }
}

function header(headers: Record<string, string | undefined>, name: string): string {
  const direct = headers[name]

  if (direct) {
    return direct
  }

  const entry = Object.entries(headers).find(([key]) => key.toLowerCase() === name)

  return entry?.[1] ?? ''
}

function filenameFromContentDisposition(value: string): string {
  const filenameMatch = /(?:^|;)\s*filename="([^"]*)"/i.exec(value)
  const raw = filenameMatch?.[1]?.trim() ?? ''

  const filename = Array.from(raw, character => {
    const codePoint = character.codePointAt(0) ?? 0

    return character === '/' || character === '\\' || codePoint <= 31 || codePoint === 127 ? '_' : character
  }).join('')

  if (!filename || filename === '.' || filename === '..') {
    throw new Error('Workflow artifact response did not include a valid filename.')
  }

  return filename
}

function artifactUrl(baseUrl: string, requestPath: string): string {
  const base = new URL(baseUrl)

  if (base.protocol !== 'http:' && base.protocol !== 'https:') {
    throw new Error(`Unsupported Hermes backend URL protocol: ${base.protocol}`)
  }

  if (!requestPath.startsWith('/')) {
    throw new Error('Invalid routed workflow artifact download path.')
  }

  return `${base.toString().replace(/\/$/, '')}${requestPath}`
}

export async function downloadWorkflowArtifactWithDeps(
  request: WorkflowArtifactDownloadRequest,
  deps: WorkflowArtifactDownloadDeps,
  signal?: AbortSignal
): Promise<WorkflowArtifactDownloadResult> {
  assertWorkflowArtifactDownloadPath(request?.path)
  signal?.throwIfAborted()

  const profile = request.profile
  const connection = await deps.ensureBackend(profile)
  const requestPath = deps.routePath(request.path, profile)

  const auth: WorkflowArtifactDownloadAuth =
    connection.authMode === 'oauth'
      ? await deps.resolveOauthAuth(connection.baseUrl)
      : { kind: 'token', token: connection.token }

  const resourceUrl = artifactUrl(connection.baseUrl, requestPath)

  const resource = signal
    ? await deps.fetchResource(resourceUrl, auth, WORKFLOW_ARTIFACT_MAX_BYTES, signal)
    : await deps.fetchResource(resourceUrl, auth, WORKFLOW_ARTIFACT_MAX_BYTES)

  signal?.throwIfAborted()

  if (resource.bytes.byteLength > WORKFLOW_ARTIFACT_MAX_BYTES) {
    throw new Error(`Workflow artifact exceeds the download limit of ${WORKFLOW_ARTIFACT_MAX_BYTES} bytes.`)
  }

  const filename = filenameFromContentDisposition(header(resource.headers, 'content-disposition'))
  const mediaType = header(resource.headers, 'content-type') || 'application/octet-stream'
  const filePath = await deps.chooseSavePath({ filename, mediaType })
  signal?.throwIfAborted()

  if (!filePath) {
    return { status: 'cancelled' }
  }

  await deps.writeFile(filePath, resource.bytes)

  return { filename, mediaType, sizeBytes: resource.bytes.byteLength, status: 'saved' }
}
