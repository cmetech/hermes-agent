import { describe, expect, it } from 'vitest'

import {
  workflowSupportsImmediateRun,
  workflowSupportsScheduledRun,
  workflowTrustAllowsRun
} from './catalog-run-policy'

describe('workflow catalog run trust', () => {
  it.each([
    ['trusted', true],
    ['verified_bundled', true],
    ['untrusted', false]
  ] as const)('%s -> %s', (state, expected) => {
    expect(workflowTrustAllowsRun(state)).toBe(expected)
  })
})

describe('workflow catalog schedule support', () => {
  it.each([
    [{ reason: 'supported', supported: true }, true, true],
    [{ reason: 'schedule_required', supported: false }, false, true],
    [{ reason: 'unsupported_inputs', supported: false }, false, false],
    [{ reason: 'showcase_cli_required', supported: false }, false, false],
    [undefined, false, false]
  ] as const)('derives support %o as immediate=%s and scheduled=%s', (support, immediate, scheduled) => {
    expect(workflowSupportsImmediateRun(support)).toBe(immediate)
    expect(workflowSupportsScheduledRun(support)).toBe(scheduled)
  })
})
