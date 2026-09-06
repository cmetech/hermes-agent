import { normalizeRemoteBaseUrl } from './connection-config'
import type { ConnectionGenerationRegistry } from './connection-generation'
import { backendScopePrefix, connectionDialFieldsChanged, type RegistryConnection } from './connection-registry'

type Registry = { primary: string; connections: RegistryConnection[] }
type Legacy = { mode?: string; remote?: unknown; profiles?: Record<string, unknown> }
type OauthLegacy = {
  remote?: { authMode?: string; url?: string }
  profiles?: Record<string, { authMode?: string; url?: string }>
}

/** Native route ownership shared by main's publication/invalidation seams. */
export function createConnectionGenerationRouting(
  authority: ConnectionGenerationRegistry,
  options: { primaryProfile: () => string; poolKeys: () => Iterable<string> }
) {
  const routeKey = (connectionId: string | null, profile?: string) =>
    JSON.stringify([connectionId || null, String(profile || '').trim() || options.primaryProfile()])

  const invalidateRegistry = (id: string, registry: Registry | null) => {
    authority.invalidateRoutes(route => JSON.parse(route)[0] === id)

    if (registry?.primary === id) {
      authority.invalidate('primary')
    }

    for (const key of options.poolKeys()) {
      if (key.startsWith(backendScopePrefix(id))) {
        authority.invalidate(`pool:${key}`)
      }
    }
  }

  return {
    routeKey,
    invalidateRegistry,
    invalidateOauth(
      baseUrl: string,
      registry: Registry,
      config: OauthLegacy,
      partitionForUrl: (url: string) => string,
      cookiePartition = false
    ) {
      const partition = cookiePartition ? partitionForUrl(baseUrl) : null

      const matches = (url?: string) =>
        url &&
        (cookiePartition
          ? partitionForUrl(url) === partition
          : normalizeRemoteBaseUrl(url) === normalizeRemoteBaseUrl(baseUrl))

      for (const source of registry.connections) {
        if ((source.kind === 'cloud' || source.authMode === 'oauth') && matches(source.url)) {
          invalidateRegistry(source.id, registry)
        }
      }

      if (config.remote?.authMode === 'oauth' && matches(config.remote.url)) {
        authority.invalidate('primary')
      }

      for (const [profile, remote] of Object.entries(config.profiles || {})) {
        if (remote.authMode === 'oauth' && matches(remote.url)) {
          authority.invalidate(profile === options.primaryProfile() ? 'primary' : `pool:${profile}`)
        }
      }
    },
    invalidateRegistryConfiguration: (previous: Registry | null, next: Registry) =>
      invalidateRegistryConfiguration(previous, next, {
        primary: () => authority.invalidate('primary'),
        registry: id => invalidateRegistry(id, previous)
      }),
    invalidateLegacyConfiguration: (previous: Legacy | null, next: Legacy) =>
      invalidateLegacyConfiguration(previous, next, {
        primary: () => authority.invalidate('primary'),
        legacy: profile => {
          authority.retireRoute(routeKey(null, profile))
          authority.invalidate(`pool:${profile}`)
        },
        primaryProfile: options.primaryProfile(),
        poolKeys: options.poolKeys()
      })
  }
}

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
