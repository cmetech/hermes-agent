import type { NativeTokenSet } from './native-oauth'

/** Main's existing native token/session mutations, ordered behind descriptor retirement. */
export function createNativeSessionGeneration(deps: {
  tokens: Map<string, NativeTokenSet>
  invalidate: (baseUrl: string) => void
  persist: (baseUrl: string, tokens: NativeTokenSet | null) => void
}) {
  return {
    store(baseUrl: string, tokens: NativeTokenSet, replacement = true) {
      if (replacement) {
        deps.invalidate(baseUrl)
      }

      deps.tokens.set(baseUrl, tokens)
      deps.persist(baseUrl, tokens)
    },
    clear(baseUrl: string) {
      deps.invalidate(baseUrl)
      deps.tokens.delete(baseUrl)
      deps.persist(baseUrl, null)
    }
  }
}
