import type { Translations } from '@/i18n/types'
import type {
  WorkflowDefinition,
  WorkflowDetail,
  WorkflowLanguageStatus,
  WorkflowRunSupport,
  WorkflowTrustState
} from '@/types/hermes'

type DesktopWorkflowRunCopy = Pick<
  Translations['operations'],
  | 'workflowRunCoordinatorUnavailable'
  | 'workflowRunIncompatible'
  | 'workflowRunShowcaseFromCli'
  | 'workflowRunSupportUnavailable'
  | 'workflowRunUnsupportedInputs'
  | 'workflowRunUntrusted'
>

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
