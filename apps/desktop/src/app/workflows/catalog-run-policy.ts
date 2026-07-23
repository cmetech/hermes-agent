import type { WorkflowRunSupport, WorkflowTrustState } from '@/types/hermes'

export function workflowTrustAllowsRun(state: WorkflowTrustState): boolean {
  return state === 'trusted' || state === 'verified_bundled'
}

export function workflowSupportsImmediateRun(support?: WorkflowRunSupport): boolean {
  return support?.supported === true
}

export function workflowSupportsScheduledRun(support?: WorkflowRunSupport): boolean {
  return support?.supported === true || support?.reason === 'schedule_required'
}
