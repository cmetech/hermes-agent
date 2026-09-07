import type { Translations } from '@/i18n/types'
import type { WorkflowMarketplaceCompatibilityIdentity, WorkflowMarketplaceFileChange } from '@/types/hermes'
import type { LifecycleOperation } from '@/types/workflow-marketplace-lifecycle'

type Copy = Translations['operations']

const phaseKeys = {
  queued: 'workflowMarketplacePhaseQueued',
  running: 'workflowMarketplacePhaseRunning',
  fetching: 'workflowMarketplacePhaseFetching',
  reviewing: 'workflowMarketplacePhaseReviewing',
  validating: 'workflowMarketplacePhaseValidating',
  committing: 'workflowMarketplacePhaseCommitting',
  recovering: 'workflowMarketplacePhaseRecovering',
  completed: 'workflowMarketplacePhaseCompleted',
  failed: 'workflowMarketplacePhaseFailed',
  cancelled: 'workflowMarketplacePhaseCancelled',
  checking: 'workflowMarketplacePhaseChecking'
} as const satisfies Record<LifecycleOperation['phase'] | 'checking', keyof Copy>

const changeKeys = {
  added: 'workflowMarketplaceChangeAdded',
  modified: 'workflowMarketplaceChangeModified',
  removed: 'workflowMarketplaceChangeRemoved',
  renamed: 'workflowMarketplaceChangeRenamed'
} as const satisfies Record<WorkflowMarketplaceFileChange['kind'], keyof Copy>

const severityKeys = {
  blocker: 'workflowMarketplaceSeverityBlocker',
  advisory: 'workflowMarketplaceSeverityAdvisory'
} as const satisfies Record<WorkflowMarketplaceCompatibilityIdentity['severity'], keyof Copy>

// Legacy observation views have a string phase. Never expose an unknown wire
// label as translated copy; this fallback makes no lifecycle-state assertion.
export function marketplacePhaseLabel(copy: Copy, phase: string): string {
  return Object.hasOwn(phaseKeys, phase)
    ? copy[phaseKeys[phase as keyof typeof phaseKeys]]
    : copy.workflowMarketplacePhaseUnknown
}

export function marketplaceFileChangeLabel(copy: Copy, kind: WorkflowMarketplaceFileChange['kind']): string {
  return copy[changeKeys[kind]]
}

export function marketplaceSeverityLabel(
  copy: Copy,
  severity: WorkflowMarketplaceCompatibilityIdentity['severity']
): string {
  return copy[severityKeys[severity]]
}
