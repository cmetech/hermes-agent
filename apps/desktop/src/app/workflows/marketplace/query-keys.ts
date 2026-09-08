export interface MarketplaceSearchKeyInput {
  limit: number
  offset: number
  query: string
  source: null | string
}

const root = (scopeKey: string) => ['workflow-marketplace', scopeKey] as const

export const marketplaceKeys = {
  root,
  capabilities: (scopeKey: string) => [...root(scopeKey), 'capabilities'] as const,
  catalog: (scopeKey: string) => [...root(scopeKey), 'catalog'] as const,
  trust: (scopeKey: string, sourceKey: string, packageId: string) =>
    [...root(scopeKey), 'trust', sourceKey, packageId] as const,
  packageState: (scopeKey: string, sourceKey: string, packageId: string) =>
    [...root(scopeKey), 'state', sourceKey, packageId] as const,
  detail: (scopeKey: string, sourceName: string, packageId: string) =>
    [...root(scopeKey), 'detail', sourceName, packageId] as const,
  installed: (scopeKey: string) => [...root(scopeKey), 'installed'] as const,
  operation: (scopeKey: string, operationId: string) => [...root(scopeKey), 'operation', operationId] as const,
  operations: (scopeKey: string) => [...root(scopeKey), 'operations'] as const,
  searchRoot: (scopeKey: string) => [...root(scopeKey), 'search'] as const,
  search: (scopeKey: string, input: MarketplaceSearchKeyInput) =>
    [...root(scopeKey), 'search', input.query, input.source, input.offset, input.limit] as const,
  sources: (scopeKey: string) => [...root(scopeKey), 'sources'] as const
}
