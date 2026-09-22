/**
 * backend-dial-claim.ts
 *
 * Single-owner reconnect/dial claim for backend spawns, keyed by the pool
 * scope key from backendScopeKey(connectionId, profile) (#90812).
 *
 * Why this exists: reconnectGateway()'s in-flight lock lives at renderer
 * module scope, so it only dedupes reconnects INSIDE one window. Two windows
 * (main + a session pop-out) racing the same wake both invoke the main-process
 * dial IPC, and for a pooled SSH connection the loser of the pool-entry race
 * could bootstrap a duplicate remote backend. Electron main is the single
 * owner of backend lifecycles, so the claim belongs here: the first dial for a
 * (connectionId, profile) key runs; every concurrent caller for the same key
 * awaits and receives that first dial's result.
 *
 * Settled dials release their claim. Lifecycle timeouts can also retire a
 * hung claim, but replacement work waits for its owner's teardown barrier.
 */
export class BackendDialClaims {
  readonly #inflightByKey = new Map<string, Promise<unknown>>()
  readonly #retiringByKey = new Map<string, Promise<void>>()

  /** Retire only after the lifecycle owner has fenced and drained its resources. */
  retire(key: string, teardown: () => Promise<void>): Promise<void> {
    const existing = this.#retiringByKey.get(key)

    if (existing) {
      return existing
    }

    this.#inflightByKey.delete(key)
    const retirement = Promise.resolve().then(teardown)
    this.#retiringByKey.set(key, retirement)
    void retirement.then(
      () => {
        if (this.#retiringByKey.get(key) === retirement) {
          this.#retiringByKey.delete(key)
        }
      },
      () => {
        /* A failed teardown must not permit an untracked replacement. */
      }
    )

    return retirement
  }

  /** Whether a dial for this key is currently in flight (test/diagnostic seam). */
  inFlight(key: string): boolean {
    return this.#inflightByKey.has(key)
  }

  run<T>(key: string, dial: () => Promise<T> | T): Promise<T> {
    const retirement = this.#retiringByKey.get(key)

    if (retirement) {
      return retirement.then(() => this.run(key, dial))
    }

    const existing = this.#inflightByKey.get(key) as Promise<T> | undefined

    if (existing) {
      return existing
    }

    // Start the dial eagerly so the first caller's spawn is already in flight
    // when a concurrent caller arrives; a synchronously-throwing dial is
    // converted into a rejection of THIS claim so it cannot bypass the seam.
    let pending: Promise<T>

    try {
      pending = Promise.resolve(dial())
    } catch (error) {
      pending = Promise.reject(error)
    }

    const release = () => {
      if (this.#inflightByKey.get(key) === pending) {
        this.#inflightByKey.delete(key)
      }
    }

    this.#inflightByKey.set(key, pending)
    // Release on both outcomes without creating an unhandled rejected branch.
    void pending.then(release, release)

    return pending
  }
}
