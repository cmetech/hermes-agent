// scripts/brand/emitters/provider.mjs
export const providerEmitter = {
  id: 'provider',
  check: () => ({ ok: true, detail: 'stub' }),
  write: () => ({ changed: false, detail: 'stub' })
}
