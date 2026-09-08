export type ConnectionGenerationEvent =
  { kind: 'scope_invalidated'; connectionId: string | null; profile: string } | { kind: 'exhausted' }

const CHANNEL = 'hermes:connection-generation'

export function decodeConnectionGenerationEvent(value: unknown): ConnectionGenerationEvent | null {
  if (!value || typeof value !== 'object' || Object.getPrototypeOf(value) !== Object.prototype) {
    return null
  }

  const fields = Object.getOwnPropertyDescriptors(value)

  if (
    Reflect.ownKeys(value).some(key => typeof key !== 'string') ||
    Object.values(fields).some(field => !field.enumerable || !('value' in field))
  ) {
    return null
  }

  if (fields.kind?.value === 'exhausted' && Object.keys(fields).length === 1) {
    return { kind: 'exhausted' }
  }

  const { connectionId, profile } = value as { connectionId?: unknown; profile?: unknown }

  if (
    fields.kind?.value !== 'scope_invalidated' ||
    Object.keys(fields).length !== 3 ||
    !(
      connectionId === null ||
      (typeof connectionId === 'string' && connectionId.length > 0 && connectionId.length <= 256)
    ) ||
    typeof profile !== 'string' ||
    !profile ||
    profile.length > 256 ||
    profile.includes('\0')
  ) {
    return null
  }

  return { kind: 'scope_invalidated', connectionId: connectionId as string | null, profile }
}

export function subscribeConnectionGeneration(
  ipc: {
    on: (channel: string, listener: (event: unknown, payload: unknown) => void) => unknown
    removeListener: (channel: string, listener: (event: unknown, payload: unknown) => void) => unknown
  },
  callback: (event: ConnectionGenerationEvent) => void
) {
  const listener = (_event: unknown, payload: unknown) => {
    const event = decodeConnectionGenerationEvent(payload)

    if (event) {
      callback(event)
    }
  }

  ipc.on(CHANNEL, listener)

  return () => {
    ipc.removeListener(CHANNEL, listener)
  }
}

export function broadcastConnectionGeneration(
  windows: readonly {
    webContents: { isDestroyed: () => boolean; send: (channel: string, payload: ConnectionGenerationEvent) => void }
  }[],
  event: ConnectionGenerationEvent
) {
  const payload = decodeConnectionGenerationEvent(event)

  if (!payload) {
    return
  }

  for (const { webContents } of windows) {
    if (!webContents.isDestroyed()) {
      webContents.send(CHANNEL, payload)
    }
  }
}
