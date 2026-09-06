export const GENERATION_CHANGED = 'marketplace_connection_generation_changed'
export const GENERATION_EXHAUSTED = 'marketplace_connection_generation_exhausted'

interface GenerationClaim {
  readonly scope: string
  readonly generation: number
}

/** Main-process authority. No credentials, persistence, or renderer-controlled counter. */
export function createConnectionGenerationRegistry(
  onInvalidate: (reason: string, routes: readonly string[]) => void = () => {},
  initialLastIssued = 0
) {
  let lastIssued = initialLastIssued
  let exhausted = false
  const claims = new Map<string, GenerationClaim>()
  const routes = new Map<string, number>()

  function assertAvailable() {
    if (exhausted) {
      throw new Error(GENERATION_EXHAUSTED)
    }
  }

  function isCurrent(generation: number) {
    return !exhausted && [...claims.values()].some(claim => claim.generation === generation)
  }

  function assertCurrent(generation: number) {
    assertAvailable()

    if (!Number.isSafeInteger(generation) || generation <= 0 || !isCurrent(generation)) {
      throw new Error(GENERATION_CHANGED)
    }
  }

  function invalidate(scope?: string) {
    const generations = new Set(
      [...claims.values()].filter(claim => scope === undefined || claim.scope === scope).map(claim => claim.generation)
    )

    const affectedRoutes = [...routes].filter(([, generation]) => generations.has(generation)).map(([key]) => key)

    for (const [key, claim] of claims) {
      if (generations.has(claim.generation)) {
        claims.delete(key)
      }
    }

    for (const [key, generation] of routes) {
      if (generations.has(generation)) {
        routes.delete(key)
      }
    }

    if (generations.size) {
      onInvalidate(GENERATION_CHANGED, affectedRoutes)
    }
  }

  return {
    assertAvailable,
    assertCurrent,
    isCurrent,
    invalidate,
    invalidateRoutes(predicate: (route: string) => boolean) {
      const generations = new Set([...routes].filter(([route]) => predicate(route)).map(([, generation]) => generation))

      for (const claim of [...claims.values()]) {
        if (generations.has(claim.generation)) {
          invalidate(claim.scope)
        }
      }
    },
    current: (route: string) => routes.get(route),
    associate(route: string, generation: number) {
      assertCurrent(generation)
      routes.set(route, generation)
    },
    begin(scope: string): GenerationClaim {
      assertAvailable()

      if (lastIssued === Number.MAX_SAFE_INTEGER) {
        exhausted = true
        claims.clear()
        routes.clear()
        onInvalidate(GENERATION_EXHAUSTED, [])
        throw new Error(GENERATION_EXHAUSTED)
      }

      invalidate(scope)
      const claim = Object.freeze({ scope, generation: ++lastIssued })
      claims.set(scope, claim)

      return claim
    },
    publish<T extends object>(claim: GenerationClaim, descriptor: T): T & { connectionGeneration: number } {
      assertAvailable()

      if (claims.get(claim.scope) !== claim) {
        throw new Error(GENERATION_CHANGED)
      }

      return { ...descriptor, connectionGeneration: claim.generation }
    }
  }
}

export type ConnectionGenerationRegistry = ReturnType<typeof createConnectionGenerationRegistry>
