import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it, vi } from 'vitest'

import { BackendDialClaims } from './backend-dial-claim'
import { parseBackendScopeKey } from './connection-registry'

const here = path.dirname(fileURLToPath(import.meta.url))
const mainSource = fs.readFileSync(path.join(here, 'main.ts'), 'utf8').replace(/\r\n/g, '\n')

describe('BackendDialClaims (#90812)', () => {
  it('coalesces two concurrent dials for the same (connectionId, profile) onto ONE backend spawn', async () => {
    const claims = new BackendDialClaims()
    let spawns = 0
    let resolveSpawn: ((value: { baseUrl: string }) => void) | undefined

    const dial = vi.fn(() => {
      spawns += 1

      return new Promise<{ baseUrl: string }>(resolve => {
        resolveSpawn = resolve
      })
    })

    // Two renderer windows race the same reconnect: reconnectGateway()'s
    // in-flight lock is per-renderer, so BOTH invoke the main-process dial.
    const first = claims.run('conn:office-ssh::default', dial)
    const second = claims.run('conn:office-ssh::default', dial)

    expect(spawns).toBe(1)

    resolveSpawn?.({ baseUrl: 'http://127.0.0.1:53150' })

    const [firstResult, secondResult] = await Promise.all([first, second])

    // The second caller receives the FIRST dial's result, not its own spawn.
    expect(firstResult).toBe(secondResult)
    expect(firstResult).toEqual({ baseUrl: 'http://127.0.0.1:53150' })
    expect(dial).toHaveBeenCalledTimes(1)
  })

  it('scopes claims by key: different (connectionId, profile) pairs dial independently', async () => {
    const claims = new BackendDialClaims()
    const dialA = vi.fn(async () => 'a')
    const dialB = vi.fn(async () => 'b')

    const [a, b] = await Promise.all([
      claims.run('conn:office-ssh::default', dialA),
      claims.run('conn:office-ssh::work', dialB)
    ])

    expect(a).toBe('a')
    expect(b).toBe('b')
    expect(dialA).toHaveBeenCalledTimes(1)
    expect(dialB).toHaveBeenCalledTimes(1)
  })

  it('releases the claim once the dial settles so a later reconnect can dial again (bounded, not latched)', async () => {
    const claims = new BackendDialClaims()
    const dial = vi.fn(async () => 'fresh')

    await claims.run('default', dial)
    expect(claims.inFlight('default')).toBe(false)

    await claims.run('default', dial)
    expect(dial).toHaveBeenCalledTimes(2)
  })

  it('propagates a failed dial to every coalesced waiter and never caches the rejection', async () => {
    const claims = new BackendDialClaims()
    let rejectSpawn: ((error: Error) => void) | undefined

    const failingDial = vi.fn(
      () =>
        new Promise<never>((_resolve, reject) => {
          rejectSpawn = reject
        })
    )

    const first = claims.run('conn:office-ssh::default', failingDial)
    const second = claims.run('conn:office-ssh::default', failingDial)
    expect(failingDial).toHaveBeenCalledTimes(1)

    rejectSpawn?.(new Error('ssh dial failed'))

    await expect(first).rejects.toThrow('ssh dial failed')
    await expect(second).rejects.toThrow('ssh dial failed')

    // Fail closed but not latched: the NEXT dial attempt runs fresh.
    const recovered = vi.fn(async () => 'recovered')
    await expect(claims.run('conn:office-ssh::default', recovered)).resolves.toBe('recovered')
    expect(recovered).toHaveBeenCalledTimes(1)
  })

  it('a synchronously-throwing dial rejects the claim instead of escaping the coalescing seam', async () => {
    const claims = new BackendDialClaims()

    await expect(
      claims.run('default', () => {
        throw new Error('spawn refused')
      })
    ).rejects.toThrow('spawn refused')

    expect(claims.inFlight('default')).toBe(false)
  })
})

describe('parseBackendScopeKey (#90812/#93910)', () => {
  it('round-trips the composite pool key back to (connectionId, profile)', () => {
    expect(parseBackendScopeKey('conn:office-ssh::default')).toEqual({
      connectionId: 'office-ssh',
      profile: 'default'
    })
    expect(parseBackendScopeKey('conn:office-ssh::work')).toEqual({ connectionId: 'office-ssh', profile: 'work' })
  })

  it('treats a bare profile key as the local/primary scope', () => {
    expect(parseBackendScopeKey('default')).toEqual({ connectionId: null, profile: 'default' })
    expect(parseBackendScopeKey('work')).toEqual({ connectionId: null, profile: 'work' })
  })
})

describe('main.ts connection authority wiring for #90812', () => {
  it('keeps BackendDialClaims inside the one lifecycle dial adapter', () => {
    const adapterStart = mainSource.indexOf('async function dialDesktopConnection(')
    expect(adapterStart).toBeGreaterThan(-1)
    const body = mainSource.slice(adapterStart, adapterStart + 2_000)

    expect(body).toContain('backendDialClaims.run(backendScopeKey(scope.connectionId, scope.profile)')
    expect(body).toContain("ensureRegistryBackend(scope.connectionId, scope.profile, '', undefined, reportPhase)")
    expect(body).toContain('backendDialClaims.run(backendScopeKey(null, scope.profile)')
    expect(body).toContain('ensureBackend(scope.profile, undefined, reportPhase)')
  })

  it('routes the profile-scoped dial IPC through the lifecycle authority', () => {
    const handlerStart = mainSource.indexOf("ipcMain.handle('hermes:connection', ")
    expect(handlerStart).toBeGreaterThan(-1)
    const body = mainSource.slice(handlerStart, handlerStart + 900)

    expect(body).toContain('ensureDesktopConnection({ connectionId: null, profile })')
  })

  it('routes the registry-scoped dial IPC through the lifecycle authority', () => {
    const handlerStart = mainSource.indexOf("ipcMain.handle('hermes:connection:for', ")
    expect(handlerStart).toBeGreaterThan(-1)
    const body = mainSource.slice(handlerStart, handlerStart + 1_200)

    expect(body).toContain('ensureDesktopConnection({ connectionId: id, profile })')
  })

  // Native consumers join the same coordinator. BackendDialClaims remains the
  // final raw-dial guard inside its adapter, so old and new callers cannot race.

  it('routes a media-stream connection resolve through the lifecycle authority', () => {
    const handlerStart = mainSource.indexOf('resolveRemoteConnection: ({ connectionId, profile }) =>')
    expect(handlerStart).toBeGreaterThan(-1)
    const body = mainSource.slice(handlerStart, handlerStart + 300)

    expect(body).toContain('ensureDesktopConnection({ connectionId, profile })')
  })

  it('routes terminal-pane registry and local resolves through the lifecycle authority', () => {
    const handlerStart = mainSource.indexOf('async function ensureTerminalBackend(webContentsId: number) {')
    expect(handlerStart).toBeGreaterThan(-1)
    const body = mainSource.slice(handlerStart, handlerStart + 900)

    expect(body).toContain('connectionId: windowRoute.connectionId')
    expect(body).toContain('return ensureDesktopConnection({ connectionId: null, profile })')
  })

  it('routes the roster-enumeration probe through the lifecycle authority', () => {
    const handlerStart = mainSource.indexOf('async function enumerateRegistryAgentSources')
    expect(handlerStart).toBeGreaterThan(-1)
    const body = mainSource.slice(handlerStart, handlerStart + 3_700)

    expect(body).toContain('ensureDesktopConnection({ connectionId: connection.id, profile: null })')
    expect(body).toContain("getJsonForBackend(descriptor, '/api/profiles'")
  })

  it('routes the connections update-all dispatch through the lifecycle authority', () => {
    const handlerStart = mainSource.indexOf("ipcMain.handle('hermes:connections:update-all',")
    expect(handlerStart).toBeGreaterThan(-1)
    // The handler grew on main (renderer-side exclusions + the managed-SSH
    // dispatch branch) — keep the scan window comfortably past the dial.
    const body = mainSource.slice(handlerStart, handlerStart + 3_000)

    expect(body).toContain('ensureDesktopConnection({ connectionId: connection.id, profile: null })')
    expect(body).toContain("postJsonForBackend(descriptor, '/api/hermes/update'")
  })

  it('routes every ordinary registry-scoped REST dispatch through the lifecycle authority', () => {
    const handlerStart = mainSource.indexOf('async function dispatchRegistryApiRequest(')
    expect(handlerStart).toBeGreaterThan(-1)
    const body = mainSource.slice(handlerStart, handlerStart + 900)

    expect(body).toContain('ensureDesktopConnection({ connectionId: registryConnectionId, profile: routeProfile })')
  })
})
