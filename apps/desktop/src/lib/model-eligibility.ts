import type {
  ModelCapabilities,
  ModelCapabilityCatalogStatus,
  ModelCapabilityState,
  ModelOptionProvider
} from '@/types/hermes'

export type ModelUsageKind = 'auxiliary' | 'fallback' | 'main' | 'moa-aggregator' | 'moa-reference' | 'vision'

type RequiredCapability = 'completion' | 'tools' | 'vision'
type UnverifiedCapabilityState = Exclude<ModelCapabilityState, 'supported'>

export type ModelEligibilityReasonKey =
  | Exclude<ModelCapabilityCatalogStatus, 'ready'>
  | 'automatic-not-allowed'
  | 'model-not-live'
  | `${RequiredCapability}-${UnverifiedCapabilityState}`

export interface ModelEligibilityView {
  eligible: boolean
  grandfathered: boolean
  reasonKey: ModelEligibilityReasonKey | null
  reasoningVerified: boolean
}

const REQUIRED_CAPABILITIES: Record<ModelUsageKind, readonly RequiredCapability[]> = {
  auxiliary: ['completion'],
  fallback: ['completion', 'tools'],
  main: ['completion', 'tools'],
  'moa-aggregator': ['completion', 'tools'],
  'moa-reference': ['completion'],
  vision: ['completion', 'vision']
}

function hasCapabilityContract(
  provider: ModelOptionProvider,
  modelCapabilities: ModelCapabilities | undefined
): boolean {
  if (provider.capability_status !== undefined || modelCapabilities?.verified !== undefined) {
    return true
  }

  return Object.values(provider.capabilities ?? {}).some(capabilities => capabilities.verified !== undefined)
}

function ineligible(
  reasonKey: ModelEligibilityReasonKey,
  reasoningVerified: boolean,
  isCurrent: boolean
): ModelEligibilityView {
  return {
    eligible: false,
    grandfathered: isCurrent,
    reasonKey,
    reasoningVerified
  }
}

export function evaluateModelEligibility(
  provider: ModelOptionProvider,
  model: string,
  usage: ModelUsageKind,
  options?: { isCurrent?: boolean }
): ModelEligibilityView {
  const modelCapabilities = provider.capabilities?.[model]
  const capabilityContract = hasCapabilityContract(provider, modelCapabilities)

  const reasoningVerified = capabilityContract
    ? modelCapabilities?.verified?.reasoning === 'supported'
    : (modelCapabilities?.reasoning ?? true)

  const isCurrent = options?.isCurrent === true

  if (!capabilityContract) {
    return {
      eligible: true,
      grandfathered: false,
      reasonKey: null,
      reasoningVerified
    }
  }

  if (model === 'auto' && usage === 'main') {
    return {
      eligible: true,
      grandfathered: false,
      reasonKey: null,
      reasoningVerified
    }
  }

  const catalogStatus = provider.capability_status ?? 'ready'

  if (catalogStatus !== 'ready') {
    return ineligible(catalogStatus, reasoningVerified, isCurrent)
  }

  if (model === 'auto' || modelCapabilities?.selection_mode === 'automatic') {
    return ineligible('automatic-not-allowed', reasoningVerified, isCurrent)
  }

  if (modelCapabilities?.live === false || !provider.models?.includes(model)) {
    return ineligible('model-not-live', reasoningVerified, isCurrent)
  }

  for (const capability of REQUIRED_CAPABILITIES[usage]) {
    const state = modelCapabilities?.verified?.[capability] ?? 'unknown'

    if (state === 'supported') {
      continue
    }

    // The checks above already prove this is an explicit model in the live
    // provider catalog. That is sufficient completion evidence when the
    // additive capability record has not caught up; richer capabilities stay
    // fail-closed.
    if (capability === 'completion' && state === 'unknown') {
      continue
    }

    const normalizedState: UnverifiedCapabilityState = state === 'unsupported' ? 'unsupported' : 'unknown'

    return ineligible(`${capability}-${normalizedState}`, reasoningVerified, isCurrent)
  }

  return {
    eligible: true,
    grandfathered: false,
    reasonKey: null,
    reasoningVerified
  }
}
