import { connectionDialFieldsChanged, type RegistryConnection } from './connection-registry'

type Registry = { primary: string; connections: RegistryConnection[] }
type Legacy = { mode?: string; remote?: unknown; profiles?: Record<string, unknown> }

export function invalidateSshGeneration(
  state: { managedScope?: string; poolKey?: string },
  scope: string,
  replacingRetiredTunnel: boolean,
  invalidate: (scope: string) => void
): void {
  // Descriptor construction already began a new claim before replacing this old tunnel.
  if (replacingRetiredTunnel) {
    return
  }

  if (state.managedScope === 'primary') {
    invalidate('primary')
  } else if (state.managedScope === 'pool') {
    invalidate(`pool:${state.poolKey || scope}`)
  }
}

/** Retire authority before callers publish the replacement configuration or begin asynchronous teardown. */
export function invalidateRegistryConfiguration(
  previous: Registry | null,
  next: Registry,
  ports: { primary: () => void; registry: (id: string) => void }
): void {
  if (!previous) {
    return
  }

  if (previous.primary !== next.primary) {
    ports.primary()
  }

  for (const before of previous.connections) {
    const after = next.connections.find(connection => connection.id === before.id)

    if (!after || connectionDialFieldsChanged(before, after)) {
      ports.registry(before.id)
    }
  }
}

export function invalidateLegacyConfiguration(
  previous: Legacy | null,
  next: Legacy,
  ports: { primary: () => void; legacy: (profile: string) => void; primaryProfile: string; poolKeys: Iterable<string> }
): void {
  if (!previous) {
    return
  }

  if (JSON.stringify([previous.mode, previous.remote]) !== JSON.stringify([next.mode, next.remote])) {
    ports.primary()

    for (const key of ports.poolKeys) {
      if (!key.startsWith('conn:')) {
        ports.legacy(key)
      }
    }
  }

  for (const profile of new Set([...Object.keys(previous.profiles || {}), ...Object.keys(next.profiles || {})])) {
    if (JSON.stringify(previous.profiles?.[profile]) !== JSON.stringify(next.profiles?.[profile])) {
      if (profile === ports.primaryProfile) {
        ports.primary()
      } else {
        ports.legacy(profile)
      }
    }
  }
}
