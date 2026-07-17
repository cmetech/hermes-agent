import { describe, expect, it } from 'vitest'

import { evaluateModelEligibility, type ModelEligibilityReasonKey, type ModelUsageKind } from '@/lib/model-eligibility'
import type {
  ModelCapabilities,
  ModelCapabilityCatalogStatus,
  ModelCapabilityState,
  ModelOptionProvider
} from '@/types/hermes'

const ALL_SUPPORTED: Record<'completion' | 'reasoning' | 'tools' | 'vision', ModelCapabilityState> = {
  completion: 'supported',
  reasoning: 'supported',
  tools: 'supported',
  vision: 'supported'
}

function contractProvider({
  model = 'model-a',
  models = [model],
  status = 'ready',
  capabilities = {}
}: {
  model?: string
  models?: string[]
  status?: ModelCapabilityCatalogStatus
  capabilities?: Partial<ModelCapabilities>
} = {}): ModelOptionProvider {
  return {
    capability_status: status,
    capabilities: {
      [model]: {
        fast: false,
        reasoning: true,
        selection_mode: 'explicit',
        verified: ALL_SUPPORTED,
        ...capabilities
      }
    },
    models,
    name: 'Gateway',
    slug: 'gateway'
  }
}

function withVerified(
  capability: keyof typeof ALL_SUPPORTED,
  state: ModelCapabilityState
): Record<keyof typeof ALL_SUPPORTED, ModelCapabilityState> {
  return { ...ALL_SUPPORTED, [capability]: state }
}

describe('evaluateModelEligibility', () => {
  it.each<{
    usage: ModelUsageKind
    required: Array<'completion' | 'tools' | 'vision'>
  }>([
    { usage: 'main', required: ['completion', 'tools'] },
    { usage: 'fallback', required: ['completion', 'tools'] },
    { usage: 'auxiliary', required: ['completion'] },
    { usage: 'vision', required: ['completion', 'vision'] },
    { usage: 'moa-reference', required: ['completion'] },
    { usage: 'moa-aggregator', required: ['completion', 'tools'] }
  ])('accepts supported requirements for $usage', ({ required, usage }) => {
    const verified: Record<keyof typeof ALL_SUPPORTED, ModelCapabilityState> = {
      completion: 'unknown',
      reasoning: 'unknown',
      tools: 'unknown',
      vision: 'unknown'
    }

    for (const capability of required) {
      verified[capability] = 'supported'
    }

    expect(evaluateModelEligibility(contractProvider({ capabilities: { verified } }), 'model-a', usage)).toEqual({
      eligible: true,
      grandfathered: false,
      reasonKey: null,
      reasoningVerified: false
    })
  })

  it.each<{
    capability: 'completion' | 'tools' | 'vision'
    usage: ModelUsageKind
  }>([
    { usage: 'main', capability: 'completion' },
    { usage: 'main', capability: 'tools' },
    { usage: 'fallback', capability: 'completion' },
    { usage: 'fallback', capability: 'tools' },
    { usage: 'auxiliary', capability: 'completion' },
    { usage: 'vision', capability: 'completion' },
    { usage: 'vision', capability: 'vision' },
    { usage: 'moa-reference', capability: 'completion' },
    { usage: 'moa-aggregator', capability: 'completion' },
    { usage: 'moa-aggregator', capability: 'tools' }
  ])('distinguishes $capability states for $usage', ({ capability, usage }) => {
    for (const state of ['unsupported', 'unknown'] as const) {
      const reasonKey: ModelEligibilityReasonKey = `${capability}-${state}`

      expect(
        evaluateModelEligibility(
          contractProvider({ capabilities: { verified: withVerified(capability, state) } }),
          'model-a',
          usage
        )
      ).toMatchObject({
        eligible: capability === 'completion' && state === 'unknown',
        grandfathered: false,
        reasonKey: capability === 'completion' && state === 'unknown' ? null : reasonKey
      })
    }
  })

  it.each<ModelUsageKind>(['main', 'fallback', 'auxiliary', 'vision', 'moa-reference', 'moa-aggregator'])(
    'accepts a live explicit %s model with unverified completion when its other requirements are supported',
    usage => {
      expect(
        evaluateModelEligibility(
          contractProvider({ capabilities: { verified: withVerified('completion', 'unknown') } }),
          'model-a',
          usage
        )
      ).toMatchObject({
        eligible: true,
        grandfathered: false,
        reasonKey: null
      })
    }
  )

  it('allows main auto before catalog readiness or model metadata checks', () => {
    expect(
      evaluateModelEligibility(
        contractProvider({
          model: 'model-a',
          models: [],
          status: 'gateway-upgrade-required'
        }),
        'auto',
        'main'
      )
    ).toEqual({
      eligible: true,
      grandfathered: false,
      reasonKey: null,
      reasoningVerified: false
    })
  })

  it.each<ModelUsageKind>(['fallback', 'auxiliary', 'vision', 'moa-reference', 'moa-aggregator'])(
    'rejects auto by model id for non-main usage %s',
    usage => {
      expect(
        evaluateModelEligibility(
          contractProvider({
            model: 'auto',
            capabilities: {
              selection_mode: 'explicit',
              verified: ALL_SUPPORTED
            }
          }),
          'auto',
          usage
        )
      ).toMatchObject({
        eligible: false,
        reasonKey: 'automatic-not-allowed'
      })
    }
  )

  it('rejects other automatic-selection models outside the main auto exception', () => {
    expect(
      evaluateModelEligibility(contractProvider({ capabilities: { selection_mode: 'automatic' } }), 'model-a', 'main')
    ).toMatchObject({
      eligible: false,
      reasonKey: 'automatic-not-allowed'
    })
  })

  it.each<Exclude<ModelCapabilityCatalogStatus, 'ready'>>([
    'authentication-required',
    'capability-response-invalid',
    'catalog-empty',
    'gateway-unreachable',
    'gateway-upgrade-required',
    'unknown'
  ])('uses non-ready status %s before model and metadata failures', status => {
    expect(
      evaluateModelEligibility(
        contractProvider({
          models: [],
          status,
          capabilities: { selection_mode: 'automatic', verified: undefined }
        }),
        'model-a',
        'main'
      )
    ).toMatchObject({
      eligible: false,
      reasonKey: status
    })
  })

  it('rejects a model absent from the provider live model list before capability checks', () => {
    expect(
      evaluateModelEligibility(
        contractProvider({ models: [], capabilities: { verified: ALL_SUPPORTED } }),
        'model-a',
        'main'
      )
    ).toMatchObject({
      eligible: false,
      reasonKey: 'model-not-live'
    })
  })

  it('rejects an explicitly non-live saved model before its supported capability evidence', () => {
    expect(
      evaluateModelEligibility(
        contractProvider({ capabilities: { live: false, verified: ALL_SUPPORTED } }),
        'model-a',
        'fallback'
      )
    ).toEqual({
      eligible: false,
      grandfathered: false,
      reasonKey: 'model-not-live',
      reasoningVerified: true
    })
  })

  it('keeps an explicitly non-live exact current model grandfathered for rendering', () => {
    expect(
      evaluateModelEligibility(
        contractProvider({ capabilities: { live: false, verified: ALL_SUPPORTED } }),
        'model-a',
        'fallback',
        { isCurrent: true }
      )
    ).toEqual({
      eligible: false,
      grandfathered: true,
      reasonKey: 'model-not-live',
      reasoningVerified: true
    })
  })

  it('keeps a current invalid model ineligible but marks it grandfathered for rendering', () => {
    expect(
      evaluateModelEligibility(
        contractProvider({
          capabilities: { verified: withVerified('tools', 'unknown') }
        }),
        'model-a',
        'main',
        { isCurrent: true }
      )
    ).toEqual({
      eligible: false,
      grandfathered: true,
      reasonKey: 'tools-unknown',
      reasoningVerified: true
    })
  })

  it('treats providers without new contract fields as legacy-compatible', () => {
    const provider: ModelOptionProvider = {
      capabilities: {
        'model-a': {
          fast: false,
          reasoning: false
        }
      },
      models: [],
      name: 'Legacy',
      slug: 'legacy'
    }

    expect(evaluateModelEligibility(provider, 'model-a', 'main')).toEqual({
      eligible: true,
      grandfathered: false,
      reasonKey: null,
      reasoningVerified: false
    })
  })

  it('detects a contract from verified fields even when provider status is absent', () => {
    const provider = contractProvider()
    delete provider.capability_status

    expect(
      evaluateModelEligibility(
        {
          ...provider,
          capabilities: {
            'model-a': {
              ...provider.capabilities?.['model-a'],
              fast: false,
              reasoning: true,
              verified: withVerified('completion', 'unknown')
            }
          }
        },
        'model-a',
        'main'
      )
    ).toMatchObject({ eligible: true, reasonKey: null })
  })

  it.each([
    ['supported', true],
    ['unsupported', false],
    ['unknown', false]
  ] as const)('reports contract reasoning %s as verified=%s', (reasoning, expected) => {
    expect(
      evaluateModelEligibility(
        contractProvider({
          capabilities: { verified: withVerified('reasoning', reasoning) }
        }),
        'model-a',
        'main'
      ).reasoningVerified
    ).toBe(expected)
  })

  it('preserves legacy reasoning defaults when no contract is present', () => {
    const legacy = (reasoning?: boolean): ModelOptionProvider => ({
      capabilities: reasoning === undefined ? undefined : { 'model-a': { fast: false, reasoning } },
      models: ['model-a'],
      name: 'Legacy',
      slug: 'legacy'
    })

    expect(evaluateModelEligibility(legacy(true), 'model-a', 'main').reasoningVerified).toBe(true)
    expect(evaluateModelEligibility(legacy(false), 'model-a', 'main').reasoningVerified).toBe(false)
    expect(evaluateModelEligibility(legacy(), 'model-a', 'main').reasoningVerified).toBe(true)
  })
})
