import { describe, expect, it } from 'vitest'

import type { WorkflowDetail } from '@/types/hermes'

import {
  desktopWorkflowRunDisabledReason,
  workflowSupportsImmediateRun,
  workflowSupportsScheduledRun,
  workflowTrustAllowsRun
} from './catalog-run-policy'

const copy = {
  workflowRunCoordinatorUnavailable: 'coordinator unavailable',
  workflowRunIncompatible: 'incompatible',
  workflowRunShowcaseFromCli: 'showcase CLI required',
  workflowRunSupportUnavailable: 'support unavailable',
  workflowRunUnsupportedInputs: 'unsupported inputs',
  workflowRunUntrusted: 'untrusted'
}

function workflow(overrides: Partial<WorkflowDetail> = {}): WorkflowDetail {
  return {
    compatibility: { findings: [], level: 'supported', runnable: true },
    coordinator: { healthy: true, reason: 'ready', status: 'healthy' },
    definition: {},
    description: 'Workflow',
    inputs: [],
    name: 'workflow',
    precedence: 1,
    risk_summary: {},
    run_support: { reason: 'supported', supported: true },
    source: 'profile',
    supported_inputs: { reason: 'parameterless', supported: true },
    topology: { mermaid: null, text: '', warnings: [] },
    trust_state: 'trusted',
    version: '1.0.0',
    ...overrides
  }
}

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

describe('desktop workflow Run disabled reason', () => {
  it('fails closed when an unsupported backend sends an unknown reason', () => {
    expect(
      desktopWorkflowRunDisabledReason(
        workflow({ run_support: { reason: 'future_backend_rule' as never, supported: false } }),
        copy
      )
    ).toBe('support unavailable')
  })

  it('applies compatibility, trust, and coordinator gates after support succeeds', () => {
    expect(
      desktopWorkflowRunDisabledReason(
        workflow({
          compatibility: { findings: [], level: 'unsupported', runnable: false },
          coordinator: { healthy: false, reason: 'offline', status: 'unhealthy' },
          trust_state: 'untrusted'
        }),
        copy
      )
    ).toBe('incompatible')
  })

  it('fails closed when a present compatibility projection omits its runnable verdict', () => {
    expect(
      desktopWorkflowRunDisabledReason(workflow({ compatibility: { findings: [], level: 'supported' } }), copy)
    ).toBe('incompatible')
  })
})
