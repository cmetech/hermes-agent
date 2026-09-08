interface RegistryPublication<T> {
  current: () => T | null
  persist: (next: T) => number
  invalidate: (previous: T | null, next: T) => void
  assign: (next: T, mtime: number | null) => void
}

/** Owns both persisted and in-memory registry publication, including migration/drift fallbacks. */
export function publishConnectionRegistry<T>(
  next: T,
  deps: RegistryPublication<T>,
  options: { persist?: boolean; fallback?: boolean; internal?: boolean; mtime?: number | null } = {}
): T {
  let mtime = options.mtime ?? null

  if (options.persist) {
    try {
      mtime = deps.persist(next)
    } catch (error) {
      if (!options.fallback) {
        throw error
      }

      mtime = null
    }
  }

  if (!options.internal) {
    deps.invalidate(deps.current(), next)
  }

  deps.assign(next, mtime)

  return next
}
