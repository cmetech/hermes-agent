/**
 * Helpers for Electron net.request calls that ride the OAuth session partition.
 *
 * Electron's ClientRequest forbids app-set restricted headers such as
 * Content-Length. Let Chromium frame the body itself; only set the JSON content
 * type here.
 */

import { assertNativeLifecycleUrl, mergeNativeRequestHeaders } from './lifecycle-api-transport'

function serializeJsonBody(body) {
  return body === undefined ? undefined : Buffer.from(JSON.stringify(body))
}

function setJsonRequestHeaders(request) {
  request.setHeader('Content-Type', 'application/json')
}

export { serializeJsonBody, setJsonRequestHeaders }

export function createOauthJsonRequest<Session, Request extends { setHeader: (name: string, value: string) => void }>(
  url: string,
  options: { method?: string; headers?: Record<string, string>; nativeLifecycle?: boolean },
  deps: {
    sessionForUrl: (url: string) => Session | null
    request: (options: {
      method: string
      url: string
      session: Session
      useSessionCookies: true
      redirect: 'follow' | 'error'
    }) => Request
    headersForUrl: (url: string) => Record<string, string>
  }
): Request {
  if (options.nativeLifecycle) {
    assertNativeLifecycleUrl(url)
  }

  const session = deps.sessionForUrl(url)

  if (!session) {
    throw new Error('OAuth session partition is unavailable.')
  }

  let parsed: URL

  try {
    parsed = new URL(url)
  } catch (error) {
    throw new Error(`Invalid URL: ${error instanceof Error ? error.message : String(error)}`)
  }

  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    throw new Error(`Unsupported Hermes backend URL protocol: ${parsed.protocol}`)
  }

  const request = deps.request({
    method: options.method || 'GET',
    url,
    session,
    useSessionCookies: true,
    redirect: options.nativeLifecycle ? 'error' : 'follow'
  })

  setJsonRequestHeaders(request)

  for (const [name, value] of Object.entries(
    mergeNativeRequestHeaders(options.nativeLifecycle ? {} : deps.headersForUrl(url), options)
  )) {
    request.setHeader(name, String(value))
  }

  return request
}
