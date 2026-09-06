import { type ConnectionGenerationRegistry, GENERATION_CHANGED } from './connection-generation'

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
  resolve: (request: LifecycleRequest) => Promise<{ descriptor: Descriptor; path: string; routeKey: string }>
  accessToken: (url: string) => Promise<string | null>
  fetchToken: (url: string, token: string | null, options: TransportOptions) => Promise<unknown>
  fetchCookie: (url: string, options: TransportOptions) => Promise<unknown>
}

export function validateLifecycleRequest(request: unknown): boolean {
  if (!request || typeof request !== 'object') {
    return false
  }

  const value = request as Record<string, unknown>
  const lifecycle = typeof value.path === 'string' && value.path.startsWith(`${ROOT}/`)

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

  const pathname = (value.path as string).split('?')[0]

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

  const route = await deps.resolve(request).catch(error => {
    deps.authority.assertCurrent(expected)
    throw error
  })

  const assertCurrent = () => {
    deps.authority.assertCurrent(expected)

    if (route.descriptor.connectionGeneration !== expected || deps.authority.current(route.routeKey) !== expected) {
      throw new Error(GENERATION_CHANGED)
    }
  }

  assertCurrent()
  const { descriptor } = route
  const url = `${descriptor.baseUrl}${route.path}`

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
