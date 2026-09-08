import type { Translations } from '@/i18n/types'

type Copy = Translations['operations']

type KnownMarketplaceDiagnosticCode =
  | 'legacy_idle_timeout_seconds'
  | 'legacy_language_profile'
  | 'missing_provider'
  | 'missing_runtime'
  | 'missing_secret'
  | 'missing_service'
  | 'missing_tool'

const diagnosticMeaningKeys = {
  legacy_idle_timeout_seconds: 'workflowMarketplaceDiagnosticLegacyIdleTimeout',
  legacy_language_profile: 'workflowMarketplaceDiagnosticLegacyLanguage',
  missing_provider: 'workflowMarketplaceDiagnosticMissingProvider',
  missing_runtime: 'workflowMarketplaceDiagnosticMissingRuntime',
  missing_secret: 'workflowMarketplaceDiagnosticMissingSecret',
  missing_service: 'workflowMarketplaceDiagnosticMissingService',
  missing_tool: 'workflowMarketplaceDiagnosticMissingTool'
} as const satisfies Record<KnownMarketplaceDiagnosticCode, keyof Copy>

export function marketplaceDiagnosticMeaning(copy: Copy, code: string): string {
  return Object.hasOwn(diagnosticMeaningKeys, code)
    ? copy[diagnosticMeaningKeys[code as KnownMarketplaceDiagnosticCode]]
    : copy.workflowMarketplaceDiagnosticUnknown
}
