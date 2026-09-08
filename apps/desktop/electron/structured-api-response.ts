export type StructuredApiResponse = { ok: true; value: unknown } | { body: unknown; ok: false; status: number }

interface StructuredResponseStream {
  headers: Record<string, string | string[] | undefined>
  statusCode?: number
  on(event: 'data', listener: (chunk: string | Uint8Array) => void): unknown
  on(event: 'end', listener: () => void): unknown
  on(event: 'error', listener: (error: Error) => void): unknown
}

interface StructuredResponseOptions {
  isTimedOut?: () => boolean
  onSettled?: () => void
  url: string
}

// Backend snapshot entries total at most 16 MiB; reserve bounded space for the
// outer JSON/cursor envelope. Domain result decoding retains its 2 MiB limit.
const LIFECYCLE_RESPONSE_BYTES = 16 * 1024 * 1024 + 64 * 1024
const lifecycleError = () => new Error('Invalid workflow lifecycle response.')

function isLifecycleUrl(url: string): boolean {
  try {
    return /^\/api\/plugins\/workflow\/marketplace\/lifecycle\/v2(?:\/|$)/.test(new URL(url).pathname)
  } catch {
    return false
  }
}

/** Validate duplicate keys while their original spelling is still available. */
function rejectDuplicateLifecycleKeys(text: string): void {
  const stack: Array<{ keys: Set<string>; keyExpected: boolean } | null> = []

  for (let index = 0; index < text.length; index++) {
    const character = text[index]

    if (character === '"') {
      const start = index++

      for (; index < text.length; index++) {
        if (text[index] === '\\') {
          index++
        } else if (text[index] === '"') {
          break
        }
      }

      const object = stack.at(-1)

      if (object?.keyExpected) {
        const key: unknown = JSON.parse(text.slice(start, index + 1))

        if (typeof key !== 'string' || object.keys.has(key)) {
          throw lifecycleError()
        }

        object.keys.add(key)
        object.keyExpected = false
      }
    } else if (character === '{' || character === '[') {
      stack.push(character === '{' ? { keys: new Set(), keyExpected: true } : null)

      if (stack.length > 64) {
        throw lifecycleError()
      }
    } else if (character === '}' || character === ']') {
      stack.pop()
    } else if (character === ',') {
      const object = stack.at(-1)

      if (object) {
        object.keyExpected = true
      }
    }
  }
}

export function collectStructuredJsonResponse(
  response: StructuredResponseStream,
  options: StructuredResponseOptions,
  resolve: (value: StructuredApiResponse) => void,
  reject: (error: Error) => void
): void {
  const chunks: Buffer[] = []
  const lifecycle = isLifecycleUrl(options.url)
  let byteLength = 0
  let settled = false
  const isTimedOut = options.isTimedOut ?? (() => false)

  const settle = (callback: () => void) => {
    if (settled || isTimedOut()) {
      return
    }

    settled = true
    options.onSettled?.()

    try {
      callback()
    } finally {
      if (lifecycle) {
        chunks.length = 0
      }
    }
  }

  response.on('data', chunk => {
    if (!settled && !isTimedOut()) {
      const bytes = Buffer.from(chunk)
      byteLength += bytes.length

      if (lifecycle && byteLength > LIFECYCLE_RESPONSE_BYTES) {
        settle(() => reject(lifecycleError()))

        return
      }

      chunks.push(bytes)
    }
  })
  response.on('end', () => {
    settle(() => {
      let text: string

      try {
        const bytes = Buffer.concat(chunks)
        text = lifecycle ? new TextDecoder('utf-8', { fatal: true }).decode(bytes) : bytes.toString('utf8')
      } catch {
        reject(lifecycleError())

        return
      }

      const statusCode = response.statusCode || 500

      if (!text && statusCode < 400) {
        if (lifecycle) {
          reject(lifecycleError())

          return
        }

        resolve({ ok: true, value: null })

        return
      }

      const contentType = String(response.headers['content-type'] || response.headers['Content-Type'] || '')
      const looksHtml = /^\s*<(?:!doctype|html)/i.test(text)

      if (statusCode < 400 && (looksHtml || contentType.includes('text/html'))) {
        reject(
          lifecycle
            ? lifecycleError()
            : new Error(`Expected JSON from ${options.url} but got HTML (status ${statusCode}).`)
        )

        return
      }

      try {
        if (lifecycle) {
          rejectDuplicateLifecycleKeys(text)
        }

        const value: unknown = JSON.parse(text)
        resolve(statusCode >= 400 ? { body: value, ok: false, status: statusCode } : { ok: true, value })
      } catch {
        reject(
          lifecycle
            ? lifecycleError()
            : new Error(`Invalid JSON from ${options.url} (status ${statusCode}): ${text.slice(0, 200)}`)
        )
      }
    })
  })
  response.on('error', error => {
    settle(() => reject(lifecycle ? lifecycleError() : error))
  })
}
