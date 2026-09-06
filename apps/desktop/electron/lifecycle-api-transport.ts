import {
  type ConnectionGenerationRegistry,
  type ConnectionRouteLease,
  GENERATION_CHANGED
} from './connection-generation'

const ROOT = '/api/plugins/workflow/marketplace/lifecycle/v2'
const PRINCIPAL_HEADER = 'X-Hermes-Marketplace-Principal-Binding'

interface LifecycleRequest {
  path: string
  method?: string
  body?: unknown
  timeoutMs?: number
  profile?: string | null
  connectionId?: string | null
  expectedConnectionGeneration?: number
  expectedMarketplacePrincipalBinding?: string
}

interface Descriptor {
  baseUrl: string
  connectionGeneration: number
  authMode?: string
  token?: string | null
  headers?: Record<string, string>
}

interface TransportOptions {
  nativeLifecycle: true
  method?: string
  body?: unknown
  timeoutMs?: number
  structured: boolean
  headers: Record<string, string>
  bearer?: string
}

interface LifecycleTransportDeps {
  authority: ConnectionGenerationRegistry
  routeKey: (request: LifecycleRequest) => string
  resolve: (
    request: LifecycleRequest,
    captured: ConnectionRouteLease
  ) => Promise<{ descriptor: Descriptor; path: string; routeKey: string }>
  accessToken: (url: string) => Promise<string | null>
  fetchToken: (url: string, token: string | null, options: TransportOptions) => Promise<unknown>
  fetchCookie: (url: string, options: TransportOptions) => Promise<unknown>
}

function canonicalRequestPath(target: unknown): string {
  if (
    typeof target !== 'string' ||
    !target.startsWith('/') ||
    target.startsWith('//') ||
    [...target].some(
      character =>
        character.charCodeAt(0) <= 32 || character.charCodeAt(0) === 127 || character === '\\' || character === '#'
    )
  ) {
    throw new Error('Invalid workflow lifecycle request.')
  }

  const pathname = target.split('?')[0]
  const normalized = new URL(target, 'https://native-request.invalid')

  if (normalized.pathname !== pathname) {
    throw new Error('Invalid workflow lifecycle request.')
  }

  // ASGI decodes path escapes after the native WHATWG boundary. No encoded spelling may enter V2 through V1.
  let decoded: string

  try {
    decoded = decodeURIComponent(pathname)
  } catch {
    throw new Error('Invalid workflow lifecycle request.')
  }

  if (decoded !== pathname && (decoded === ROOT || decoded.startsWith(`${ROOT}/`))) {
    throw new Error('Invalid workflow lifecycle request.')
  }

  return pathname
}

function lifecycleDestination(baseUrl: string, path: string): string {
  const pathname = canonicalRequestPath(path)
  const target = new URL(`${baseUrl}${path}`)

  if (
    !pathname.startsWith(`${ROOT}/`) ||
    target.pathname !== pathname ||
    target.hash ||
    !['http:', 'https:'].includes(target.protocol)
  ) {
    throw new Error('Invalid workflow lifecycle request.')
  }

  return target.href
}

/** Final transport boundary: the header must not survive URL normalization or leave the exact V2 namespace. */
export function assertNativeLifecycleUrl(url: string): void {
  let target: URL

  try {
    target = new URL(url)
  } catch {
    throw new Error('Invalid workflow lifecycle request.')
  }

  if (
    target.href !== url ||
    target.hash ||
    !['http:', 'https:'].includes(target.protocol) ||
    !canonicalRequestPath(`${target.pathname}${target.search}`).startsWith(`${ROOT}/`)
  ) {
    throw new Error('Invalid workflow lifecycle request.')
  }
}

export function validateLifecycleRequest(request: unknown): boolean {
  if (!request || typeof request !== 'object') {
    return false
  }

  const value = request as Record<string, unknown>
  const pathname = canonicalRequestPath(value.path)
  const lifecycle = pathname.startsWith(`${ROOT}/`)

  if (!lifecycle) {
    if ('expectedConnectionGeneration' in value || 'expectedMarketplacePrincipalBinding' in value) {
      throw new Error('Invalid workflow lifecycle request.')
    }

    return false
  }

  const generation = value.expectedConnectionGeneration

  if (typeof generation !== 'number' || !Number.isSafeInteger(generation) || generation <= 0) {
    throw new Error(GENERATION_CHANGED)
  }

  const allowed = [
    'path',
    'method',
    'body',
    'timeoutMs',
    'profile',
    'connectionId',
    'expectedConnectionGeneration',
    'expectedMarketplacePrincipalBinding'
  ]

  if (Object.keys(value).some(key => !allowed.includes(key))) {
    throw new Error('Invalid workflow lifecycle request.')
  }

  // No URL normalization may retarget the native-owned header outside V2.
  if (
    pathname.includes('%') ||
    pathname.includes('\\') ||
    pathname.includes('#') ||
    pathname.split('/').some(part => part === '.' || part === '..')
  ) {
    throw new Error('Invalid workflow lifecycle request.')
  }

  if (pathname === `${ROOT}/capabilities`) {
    if ('expectedMarketplacePrincipalBinding' in value) {
      throw new Error('Invalid workflow lifecycle request.')
    }
  } else if (
    typeof value.expectedMarketplacePrincipalBinding !== 'string' ||
    !/^[0-9a-f]{64}$/.test(value.expectedMarketplacePrincipalBinding)
  ) {
    throw new Error('Invalid workflow lifecycle request.')
  }

  return true
}

/** Main-owned header merge. Do not call connection normalization here: old saved collisions must be stripped too. */
function requestHeaders(descriptor: Descriptor, principal?: string): Record<string, string> {
  const headers: Record<string, string> = {}

  for (const [name, value] of Object.entries(descriptor.headers || {})) {
    if (name.trim().toLowerCase() !== PRINCIPAL_HEADER.toLowerCase()) {
      headers[name] = value
    }
  }

  if (principal !== undefined) {
    headers[PRINCIPAL_HEADER] = principal
  }

  return headers
}

export function mergeNativeRequestHeaders(
  implicit: Record<string, string>,
  options: { headers?: Record<string, string>; nativeLifecycle?: boolean }
): Record<string, string> {
  const headers: Record<string, string> = {}

  for (const [name, value] of Object.entries({ ...implicit, ...options.headers })) {
    if (name.trim().toLowerCase() !== PRINCIPAL_HEADER.toLowerCase()) {
      headers[name] = value
    }
  }

  const binding = options.nativeLifecycle ? options.headers?.[PRINCIPAL_HEADER] : undefined

  if (binding !== undefined) {
    if (!/^[0-9a-f]{64}$/.test(binding)) {
      throw new Error('Invalid workflow lifecycle request.')
    }

    headers[PRINCIPAL_HEADER] = binding
  }

  return headers
}

/** Shared by both IPC channels; native route and backend actor preconditions remain independent. */
export async function dispatchLifecycleRequest(
  request: LifecycleRequest,
  structured: boolean,
  deps: LifecycleTransportDeps
): Promise<unknown> {
  deps.authority.assertAvailable()

  if (!validateLifecycleRequest(request)) {
    throw new Error('Invalid workflow lifecycle request.')
  }

  const expected = request.expectedConnectionGeneration!
  // Reject already-obsolete or queued requests without initiating a new dial.
  deps.authority.assertCurrent(expected)

  const routeKey = deps.routeKey(request)
  const captured = Object.freeze({ routeKey, lease: deps.authority.captureRoute(routeKey) })

  const assertRequestCurrent = () => {
    deps.authority.assertCurrent(expected)
    deps.authority.assertRouteLease(captured.routeKey, captured.lease)
  }

  let route: Awaited<ReturnType<LifecycleTransportDeps['resolve']>>

  try {
    route = await deps.resolve(request, captured)
  } catch (error) {
    assertRequestCurrent()
    throw error
  }

  const assertCurrent = () => {
    assertRequestCurrent()
    deps.authority.assertRouteCurrent(captured.routeKey, expected, captured.lease)

    if (route.routeKey !== captured.routeKey || route.descriptor.connectionGeneration !== expected) {
      throw new Error(GENERATION_CHANGED)
    }
  }

  assertCurrent()
  const { descriptor } = route
  const url = lifecycleDestination(descriptor.baseUrl, route.path)

  try {
    const bearer = descriptor.authMode === 'oauth' ? await deps.accessToken(descriptor.baseUrl).catch(() => null) : null
    assertCurrent()

    const options: TransportOptions = {
      nativeLifecycle: true,
      method: request.method,
      body: request.body,
      timeoutMs: request.timeoutMs,
      structured,
      headers: requestHeaders(descriptor, request.expectedMarketplacePrincipalBinding)
    }

    return await (descriptor.authMode === 'oauth' && !bearer
      ? deps.fetchCookie(url, options)
      : deps.fetchToken(url, bearer ? null : (descriptor.token ?? null), { ...options, ...(bearer ? { bearer } : {}) }))
  } finally {
    // Also suppress an obsolete backend error, which may carry its body.
    assertCurrent()
  }
}
