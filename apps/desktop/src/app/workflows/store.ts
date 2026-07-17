import { atom } from 'nanostores'

export const $workflowSelectedRunId = atom<null | string>(null)
export const $workflowAttentionFirst = atom(true)

export function selectWorkflowRun(runId: null | string): void {
  $workflowSelectedRunId.set(runId)
}
