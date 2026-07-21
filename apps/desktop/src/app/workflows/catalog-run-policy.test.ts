import { describe, expect, it } from 'vitest'

import { workflowTrustAllowsRun } from './catalog-run-policy'

describe('workflow catalog run trust', () => {
  it.each([
    ['trusted', true],
    ['verified_bundled', true],
    ['untrusted', false]
  ] as const)('%s -> %s', (state, expected) => {
    expect(workflowTrustAllowsRun(state)).toBe(expected)
  })
})
