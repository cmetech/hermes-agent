// scripts/brand/emitters/auth-noauth.mjs
export const authNoauthEmitter = {
  id: 'auth-noauth',
  check: () => ({ ok: true, detail: 'stub' }),
  write: () => ({ changed: false, detail: 'stub' })
}
