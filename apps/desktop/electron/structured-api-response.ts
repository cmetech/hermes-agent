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

export function collectStructuredJsonResponse(
  response: StructuredResponseStream,
  options: StructuredResponseOptions,
  resolve: (value: StructuredApiResponse) => void,
  reject: (error: Error) => void
): void {
  const chunks: Buffer[] = []
  let settled = false
  const isTimedOut = options.isTimedOut ?? (() => false)

  const settle = (callback: () => void) => {
    if (settled || isTimedOut()) {
      return
    }

    settled = true
    options.onSettled?.()
    callback()
  }

  response.on('data', chunk => {
    if (!settled && !isTimedOut()) {
      chunks.push(Buffer.from(chunk))
    }
  })
  response.on('end', () => {
    settle(() => {
      const text = Buffer.concat(chunks).toString('utf8')
      const statusCode = response.statusCode || 500

      if (!text && statusCode < 400) {
        resolve({ ok: true, value: null })

        return
      }

      const contentType = String(response.headers['content-type'] || response.headers['Content-Type'] || '')
      const looksHtml = /^\s*<(?:!doctype|html)/i.test(text)

      if (statusCode < 400 && (looksHtml || contentType.includes('text/html'))) {
        reject(new Error(`Expected JSON from ${options.url} but got HTML (status ${statusCode}).`))

        return
      }

      try {
        const value: unknown = JSON.parse(text)
        resolve(statusCode >= 400 ? { body: value, ok: false, status: statusCode } : { ok: true, value })
      } catch {
        reject(new Error(`Invalid JSON from ${options.url} (status ${statusCode}): ${text.slice(0, 200)}`))
      }
    })
  })
  response.on('error', error => {
    settle(() => reject(error))
  })
}
