import { useI18n } from '@/i18n'
import type { WorkflowRunSnapshot } from '@/types/hermes'

interface RunInspectorProps {
  run: WorkflowRunSnapshot
}

export function RunInspector({ run }: RunInspectorProps) {
  const { t } = useI18n()
  const copy = t.operations

  return (
    <aside aria-label={`${run.workflow} run inspector`} className="min-w-0 py-4">
      <h2 className="text-base font-medium">{run.workflow}</h2>
      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
        <dt>{copy.status}</dt>
        <dd>{run.status}</dd>
        <dt>{copy.health}</dt>
        <dd>{run.health}</dd>
        <dt>{copy.graphProgress}</dt>
        <dd>
          {run.progress.completed_nodes}/{run.progress.total_nodes}
        </dd>
        <dt>{copy.completionEstimate}</dt>
        <dd>{copy.estimateUnavailable}</dd>
        <dt>{copy.nextActions}</dt>
        <dd>{run.next_actions.join(', ')}</dd>
      </dl>
    </aside>
  )
}
