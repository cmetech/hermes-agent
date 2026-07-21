import type { WorkflowTrustState } from '@/types/hermes'

export function workflowTrustAllowsRun(state: WorkflowTrustState): boolean {
  return state === 'trusted' || state === 'verified_bundled'
}
