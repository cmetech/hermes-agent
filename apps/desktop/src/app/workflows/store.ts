import { Codecs, persistentAtom } from '@/lib/persisted'

export const $workflowSelectedRunId = persistentAtom<null | string>(
  'hermes.desktop.workflow.selectedRun', null, Codecs.nullableText
)
export const $workflowAttentionFirst = persistentAtom(
  'hermes.desktop.workflow.attentionFirst', true, Codecs.bool
)

export function selectWorkflowRun(runId: null | string): void {
  $workflowSelectedRunId.set(runId)
}
