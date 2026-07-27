import { describe, expect, it } from 'vitest'

import type { WorkflowDefinition, WorkflowDetail } from '@/types/hermes'

import {
  desktopWorkflowLanguageLabel,
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

function catalogWorkflow(overrides: Partial<WorkflowDefinition> = {}): WorkflowDefinition {
  return {
    compatibility: { level: 'supported', runnable: true },
    description: 'Workflow',
    inputs: [],
    name: 'workflow',
    precedence: 1,
    run_support: { reason: 'supported', supported: true },
    source: 'profile',
    supported_inputs: { reason: 'parameterless', supported: true },
    trust_state: 'trusted',
    version: '1.0.0',
    ...overrides
  }
}

function withoutProjection(
  value: WorkflowDetail,
  projection: 'compatibility' | 'coordinator',
  representation: 'absent' | 'null' | 'undefined'
): WorkflowDetail {
  const payload = value as unknown as Record<string, unknown>

  if (representation === 'absent') {
    Reflect.deleteProperty(payload, projection)
  } else {
    payload[projection] = representation === 'null' ? null : undefined
  }

  return payload as unknown as WorkflowDetail
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
        copy,
        'detail'
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
        copy,
        'detail'
      )
    ).toBe('incompatible')
  })

  it('fails closed when a present compatibility projection omits its runnable verdict', () => {
    expect(
      desktopWorkflowRunDisabledReason(
        workflow({ compatibility: { findings: [], level: 'supported' } }),
        copy,
        'detail'
      )
    ).toBe('incompatible')
  })

  it.each(['absent', 'undefined', 'null'] as const)(
    'fails catalog compatibility closed when its projection is %s without requiring a coordinator',
    representation => {
      const item = catalogWorkflow()

      if (representation === 'absent') {
        Reflect.deleteProperty(item, 'compatibility')
      } else {
        ;(item as unknown as Record<string, unknown>).compatibility = representation === 'null' ? null : undefined
      }

      expect(desktopWorkflowRunDisabledReason(item, copy, 'catalog')).toBe('incompatible')
    }
  )

  it('allows a compatible catalog row without a coordinator projection', () => {
    expect(desktopWorkflowRunDisabledReason(catalogWorkflow(), copy, 'catalog')).toBeNull()
  })

  it.each(['absent', 'undefined', 'null'] as const)(
    'fails detail compatibility closed when its projection is %s',
    representation => {
      expect(
        desktopWorkflowRunDisabledReason(withoutProjection(workflow(), 'compatibility', representation), copy, 'detail')
      ).toBe('incompatible')
    }
  )

  it.each(['absent', 'undefined', 'null'] as const)(
    'fails detail coordinator health closed when its projection is %s',
    representation => {
      expect(
        desktopWorkflowRunDisabledReason(withoutProjection(workflow(), 'coordinator', representation), copy, 'detail')
      ).toBe('coordinator unavailable')
    }
  )

  it('keeps compatibility precedence when trust is missing too', () => {
    const item = withoutProjection(workflow({ trust_state: undefined as never }), 'compatibility', 'absent')

    expect(desktopWorkflowRunDisabledReason(item, copy, 'detail')).toBe('incompatible')
  })
})

describe('desktop workflow language label', () => {
  it('preserves a nonblank future server profile verbatim', () => {
    expect(
      desktopWorkflowLanguageLabel(
        { effective_profile: '  future-workflow-language  ' as never, legacy: false },
        {
          workflowLanguage: 'Workflow language',
          workflowLanguageArchon: 'Archon 2026-07',
          workflowLanguageLegacy: 'Legacy semantics'
        }
      )
    ).toBe('  future-workflow-language  ')
  })
})
