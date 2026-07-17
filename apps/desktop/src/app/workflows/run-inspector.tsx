import type { WorkflowRunSnapshot } from '@/types/hermes'

interface RunInspectorProps {
  run: WorkflowRunSnapshot
}

export function RunInspector({ run }: RunInspectorProps) {
  return (
    <aside aria-label={`${run.workflow} run inspector`} className="min-w-0 py-4">
      <h2 className="text-base font-medium">{run.workflow}</h2>
      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
        <dt>Status</dt>
        <dd>{run.status}</dd>
        <dt>Health</dt>
        <dd>{run.health}</dd>
        <dt>Graph progress</dt>
        <dd>
          {run.progress.completed_nodes}/{run.progress.total_nodes}
        </dd>
        <dt>Completion estimate</dt>
        <dd>estimate_unavailable</dd>
        <dt>Next actions</dt>
        <dd>{run.next_actions.join(', ')}</dd>
      </dl>
    </aside>
  )
}
