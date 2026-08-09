import type { Translations } from '@/i18n/types'
import type {
  WorkflowDefinition,
  WorkflowDetail,
  WorkflowLanguageStatus,
  WorkflowProviderCapabilityProjection,
  WorkflowRunSupport,
  WorkflowTrustState
} from '@/types/hermes'

type DesktopWorkflowRunCopy = Pick<
  Translations['operations'],
  | 'workflowRunCoordinatorUnavailable'
  | 'workflowRunIncompatible'
  | 'workflowRunProviderAuthorityUnavailable'
  | 'workflowRunShowcaseFromCli'
  | 'workflowRunSupportUnavailable'
  | 'workflowRunUnsupportedInputs'
  | 'workflowRunUntrusted'
>

const CAPABILITY_LEVELS = new Set(['degraded', 'portable', 'unsupported'])
const CAPABILITY_DISPOSITIONS = new Set([
  'degraded_with_explicit_semantics',
  'hermes_adapter',
  'native',
  'unsupported'
])
const CAPABILITY_FEATURES = new Set([
  'cost_budgets',
  'effort_thinking',
  'fallback_models',
  'hooks',
  'mcp',
  'provider_native_sandbox',
  'session_resumption',
  'skills_inline_agents',
  'structured_output',
  'tool_restrictions',
  'web_execution'
])
const ROUTE_ROLES = new Set(['fallback', 'inline_agent', 'primary'])
const REFERENCE_KINDS = new Set(['configured_alias', 'literal', 'tier'])
const SHA256 = /^[0-9a-f]{64}$/

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function boundedString(value: unknown, max = 128): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= max
}

function boundedInteger(value: unknown, max: number): value is number {
  return Number.isInteger(value) && Number(value) >= 0 && Number(value) <= max
}

export function isDesktopProviderCapabilityProjection(
  value: unknown,
  shape: 'catalog' | 'detail'
): value is WorkflowProviderCapabilityProjection {
  if (!isRecord(value)) {
    return false
  }
  const summaryValid =
    value.schema_version === 1 &&
    CAPABILITY_LEVELS.has(String(value.level)) &&
    typeof value.mixed_provider === 'boolean' &&
    boundedInteger(value.resolved_route_count, 512) &&
    boundedInteger(value.unsupported_count, 4096) &&
    boundedInteger(value.degraded_count, 4096) &&
    typeof value.authority_digest === 'string' &&
    SHA256.test(value.authority_digest) &&
    Array.isArray(value.warning_codes) &&
    value.warning_codes.length <= 512 &&
    value.warning_codes.every(code => boundedString(code))

  if (!summaryValid || value.level === 'unsupported' || Number(value.unsupported_count) !== 0) {
    return false
  }
  if (shape === 'catalog') {
    return value.routes === undefined && value.decisions === undefined
  }
  if (
    !Array.isArray(value.routes) ||
    !Array.isArray(value.decisions) ||
    value.routes.length !== value.resolved_route_count ||
    value.routes.length > 512 ||
    value.decisions.length > 4096
  ) {
    return false
  }
  const routesValid = value.routes.every(
    route =>
      isRecord(route) &&
      boundedString(route.node_id) &&
      ROUTE_ROLES.has(String(route.role)) &&
      (route.inline_agent_id === null || boundedString(route.inline_agent_id)) &&
      REFERENCE_KINDS.has(String(route.reference_kind)) &&
      boundedString(route.provider) &&
      boundedString(route.model)
  )
  const decisionsValid = value.decisions.every(
    decision =>
      isRecord(decision) &&
      boundedString(decision.path, 256) &&
      CAPABILITY_FEATURES.has(String(decision.feature)) &&
      CAPABILITY_DISPOSITIONS.has(String(decision.disposition)) &&
      decision.disposition !== 'unsupported' &&
      boundedString(decision.provider) &&
      boundedString(decision.model) &&
      (decision.option === null || boundedString(decision.option)) &&
      isRecord(decision.effective_semantics) &&
      boundedString(decision.code)
  )
  return routesValid && decisionsValid
}

type DesktopWorkflowLanguageCopy = Pick<
  Translations['operations'],
  'workflowLanguage' | 'workflowLanguageArchon' | 'workflowLanguageLegacy'
>

export function workflowTrustAllowsRun(state: WorkflowTrustState): boolean {
  return state === 'trusted' || state === 'verified_bundled'
}

export function workflowSupportsImmediateRun(support?: WorkflowRunSupport): boolean {
  return support?.supported === true
}

export function workflowSupportsScheduledRun(support?: WorkflowRunSupport): boolean {
  return support?.supported === true || support?.reason === 'schedule_required'
}

function workflowRunSupportDisabledReason(
  support: WorkflowRunSupport | undefined,
  copy: DesktopWorkflowRunCopy
): string | null {
  if (support?.reason === 'supported' && support.supported === true) {
    return null
  }

  if (support?.reason === 'schedule_required' && support.supported === false) {
    return null
  }

  if (support?.reason === 'unsupported_inputs' && support.supported === false) {
    return copy.workflowRunUnsupportedInputs
  }

  if (support?.reason === 'showcase_cli_required' && support.supported === false) {
    return copy.workflowRunShowcaseFromCli
  }

  return copy.workflowRunSupportUnavailable
}

export function desktopWorkflowRunDisabledReason(
  workflow: WorkflowDefinition | WorkflowDetail,
  copy: DesktopWorkflowRunCopy,
  shape: 'catalog' | 'detail'
): string | null {
  const supportReason = workflowRunSupportDisabledReason(workflow.run_support, copy)

  if (supportReason) {
    return supportReason
  }

  if (workflow.compatibility?.runnable !== true) {
    return copy.workflowRunIncompatible
  }

  if (
    Number(workflow.language?.normalizer_version) >= 5 &&
    !isDesktopProviderCapabilityProjection(workflow.provider_capability, shape)
  ) {
    return copy.workflowRunProviderAuthorityUnavailable
  }

  if (!workflowTrustAllowsRun(workflow.trust_state)) {
    return copy.workflowRunUntrusted
  }

  if (shape === 'detail' && (workflow as WorkflowDetail).coordinator?.healthy !== true) {
    return copy.workflowRunCoordinatorUnavailable
  }

  return null
}

export function desktopWorkflowLanguageLabel(
  language: WorkflowLanguageStatus,
  copy: DesktopWorkflowLanguageCopy
): string {
  if (language.effective_profile === 'archon-2026-07') {
    return copy.workflowLanguageArchon
  }

  if (language.effective_profile === 'hermes-legacy') {
    return copy.workflowLanguageLegacy
  }

  const effectiveProfile: unknown = language.effective_profile
  const serverProfile = typeof effectiveProfile === 'string' && effectiveProfile.trim() ? effectiveProfile : ''

  return serverProfile || copy.workflowLanguage
}
