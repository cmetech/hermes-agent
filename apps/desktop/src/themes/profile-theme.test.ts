import { beforeEach, describe, expect, it } from 'vitest'

import { modePref, skinPref } from './context'
import { DEFAULT_SKIN_NAME } from './presets'

// Skin and mode share one per-profile contract, so assert it once over both.
interface Pref {
  resolve: (profile: string) => string
  assign: (profile: string, value: string) => void
}

const cases = [
  {
    name: 'skin',
    pref: skinPref as unknown as Pref,
    fallback: DEFAULT_SKIN_NAME,
    a: 'ember',
    b: 'midnight',
    junk: 'nope'
  },
  // fallback is 'dark', not upstream's 'light': normalizeMode in ./context
  // defaults to dark on first run (this fork's default) while still honoring a
  // user's saved 'light'/'system'. Registered in the workspace surface table.
  { name: 'mode', pref: modePref as unknown as Pref, fallback: 'dark', a: 'light', b: 'system', junk: 'dusk' }
]

describe.each(cases)('per-profile $name', ({ pref, fallback, a, b, junk }) => {
  beforeEach(() => window.localStorage.clear())

  it('falls back to the default when unassigned', () => {
    expect(pref.resolve('default')).toBe(fallback)
    expect(pref.resolve('work')).toBe(fallback)
  })

  it('keeps each profile on its own value', () => {
    pref.assign('work', a)
    pref.assign('default', b)
    expect(pref.resolve('work')).toBe(a)
    expect(pref.resolve('default')).toBe(b)
  })

  it('lets unassigned profiles inherit the default profile as the global fallback', () => {
    pref.assign('default', a)
    expect(pref.resolve('never-themed')).toBe(a)
  })

  it('normalizes an unknown stored value back to the default', () => {
    pref.assign('work', junk)
    expect(pref.resolve('work')).toBe(fallback)
  })
})
